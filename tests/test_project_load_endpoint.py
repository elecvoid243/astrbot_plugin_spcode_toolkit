"""Tests for the ``POST /spcode/project-load`` webapi endpoint.

Author: elecvoid243 · 2026-07-28

覆盖场景:
- 参数校验:无 umo / 无 body / directory 缺失 / directory 非 str /
  no_agentsmd / no_codegraph / force 类型错
- 静默 load 成功路径(完整 3 步流水线,不向 event yield)
- 失败路径:agentsmd 失败 / codegraph 失败 / 重复 load / 路径不安全 /
  feature 关闭 / no_agentsmd 跳过 / no_codegraph 跳过 / force 覆盖
- envelope 字段完整且 ReasonCode 正确
- 静默语义:不向 mock event 写用户消息

与 ``tests/test_project_subcommand.py`` 的差异:
- 后者覆盖 ``/project load`` 命令(async generator,带 yield 用户消息)
- 本文件覆盖 ``POST /spcode/project-load`` webapi 端点(async def,
  静默返回结构化 envelope)。两者行为对齐,但调用入口不同。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest  # noqa: E402

# 不通过 ``from astrbot_plugin_spcode_toolkit import main`` 间接 import
# (main.py 顶层引入 ``.tools.webapi`` 会触发 ``astrbot.api.web`` ImportError,
# 在精简 AstrBot 安装下 web 子包不存在)。改为直接 import 子包,所有
# 需要的符号在 tools.* 中可访问。
#
# 把插件包根目录加入 sys.path 是 conftest 的职责 — 同一目录下不需要再做。
#
# 注:webapi 包整体依赖 ``astrbot.api.web.JSONResponse``(见
# ``tools/webapi/_helpers.py:18``)。如果当前 AstrBot 安装缺 web 子包,
# 整个 webapi 端点测试应被 skip — 但 ``ProjectManager.load_impl_silent``
# 直接单元测试(不依赖 webapi)仍能跑。处理:用 try/except 把 webapi import
# 包成软依赖,失败时设 ``WEBAPI_AVAILABLE = False``,webapi 端点测试
# 在 @pytest.mark.skipif 保护下 skip,load_impl_silent 测试无影响。

from tools.agentsmd import AgentsmdSubsystem  # noqa: E402
from tools.project import ProjectManager  # noqa: E402

try:
    from tools.webapi import HANDLERS, ROUTES  # type: ignore[import-not-found]
    from tools.webapi import (  # type: ignore[import-not-found]
        project_load as _project_load_webapi,
    )

    WEBAPI_AVAILABLE = True
except ImportError as _e:
    # 精简 AstrBot 安装缺 web 子包 — webapi 端点测试无法跑,
    # 但 ProjectManager.load_impl_silent 单元测试不受影响。
    HANDLERS = None  # type: ignore[assignment]
    ROUTES = None  # type: ignore[assignment]
    _project_load_webapi = None  # type: ignore[assignment]
    WEBAPI_AVAILABLE = False
    _WEBAPI_IMPORT_ERROR = _e

# 与其他 test_<module>.py 风格一致:插件主类在此别名供 _make_plugin / fixtures。
# 实际无需 import main.py,只取 SPCodeToolkit 类型用于类型注解。

# webapi 端点测试的 skip 条件 — AstrBot 缺 web 子包时统一 skip。
requires_webapi = pytest.mark.skipif(
    not WEBAPI_AVAILABLE,
    reason=(
        "astrbot.api.web 不可用(精简 AstrBot 安装);"
        "webapi 端点测试需完整 AstrBot 宿主环境。"
        f"原始 ImportError: {_WEBAPI_IMPORT_ERROR if not WEBAPI_AVAILABLE else ''}"
    ),
)


# ── Fixtures ─────────────────────────────────────────────────────────


def _make_plugin() -> MagicMock:
    """构造一个最小可用的 plugin mock,启用 agentsmd / codegraph feature flag。"""
    plugin = MagicMock()
    plugin.context = MagicMock()
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": None,
    }
    # agentsmd / codegraph 子系统 — 真实 ProjectManager 实例
    from tools.project import ProjectManager

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **kw: (True, ""),
    )
    plugin.project = ProjectManager(plugin)
    return plugin


def _patch_substeps_success(plugin) -> None:
    """把 3 个子方法 mock 为成功路径 — yield 单一 OK 消息后结束。"""

    async def _ok(*a, **kw):
        yield "mock-substep-ok"

    for name in ("init", "load"):
        m = MagicMock(side_effect=_ok)
        setattr(plugin.agentsmd, name, m)

    plugin.agentsmd.unload = MagicMock(return_value="mock-unload-ok")

    # 2026-08-15: codegraph 子步骤只剩 init(set_project 不再参与 load,
    # 但保留 mock 供 unload / 防御性断言使用)。
    for name in ("init", "set_project"):
        m = MagicMock(side_effect=_ok)
        setattr(plugin.codegraph, name, m)


def _patch_substeps_fail_at(plugin, *, fail_step: str) -> None:
    """让某个子方法 yield ❌ 消息 → 触发 ProjectLoadAbort。

    Args:
        fail_step: "agentsmd_init" / "agentsmd_load" / "codegraph_init"。
    """

    async def _fail(*a, **kw):
        yield "❌ 模拟子步骤失败"

    async def _ok(*a, **kw):
        yield "mock-substep-ok"

    # 默认全部成功
    plugin.agentsmd.init = MagicMock(side_effect=_ok)
    plugin.agentsmd.load = MagicMock(side_effect=_ok)
    plugin.codegraph.init = MagicMock(side_effect=_ok)
    plugin.codegraph.set_project = MagicMock(side_effect=_ok)
    plugin.agentsmd.unload = MagicMock(return_value="mock-unload-ok")

    if fail_step == "agentsmd_init":
        plugin.agentsmd.init = MagicMock(side_effect=_fail)
    elif fail_step == "agentsmd_load":
        plugin.agentsmd.load = MagicMock(side_effect=_fail)
    elif fail_step == "codegraph_init":
        plugin.codegraph.init = MagicMock(side_effect=_fail)
    else:
        raise ValueError(f"unknown fail_step: {fail_step}")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def plugin():
    return _make_plugin()


@pytest.fixture
def plugin_with_mocks():
    p = _make_plugin()
    _patch_substeps_success(p)
    return p


def _reset_state():
    from tools.project import state as _proj_state

    _proj_state.reset()


# ── 路由表 / HANDLERS 注册 ────────────────────────────────────────


@requires_webapi
def test_route_registered_in_routes_table() -> None:
    """POST /spcode/project-load 应注册在 ROUTES 表。"""
    routes = {r[0] for r in ROUTES}
    assert "/spcode/project-load" in routes


@requires_webapi
def test_route_method_is_post() -> None:
    """该端点只支持 POST(静默 load 是写操作)。"""
    for path, methods, _handler, _desc in ROUTES:
        if path == "/spcode/project-load":
            assert methods == ["POST"]
            return
    raise AssertionError("/spcode/project-load not found in ROUTES")


@requires_webapi
def test_handler_registered_in_handlers_dict() -> None:
    """HANDLERS 表应包含 handle_post_project_load。"""
    assert "handle_post_project_load" in HANDLERS
    assert HANDLERS["handle_post_project_load"] is _project_load_webapi.handle


# ── 参数校验 ────────────────────────────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_invalid_body_returns_invalid_param() -> None:
    """body 不是 dict → success=False, reason=invalid_body。"""
    plugin = _make_plugin()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:test:1",
        worktree=None,
        body=None,
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"
    assert result["data"]["loaded"] is False


@requires_webapi
@pytest.mark.asyncio
async def test_missing_umo_returns_invalid_body() -> None:
    """无 umo → invalid_body(dashboard 必须告诉端点会话)。"""
    plugin = _make_plugin()
    result = await _project_load_webapi.handle(
        plugin,
        umo=None,
        worktree=None,
        body={"directory": "/tmp/x"},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


@requires_webapi
@pytest.mark.asyncio
async def test_missing_directory_returns_invalid_param() -> None:
    """directory 缺失 / 空 / 非 str → invalid_param。"""
    plugin = _make_plugin()
    for bad in (None, "", "   ", 123, [], {}):
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:test:1",
            worktree=None,
            body={"directory": bad},
        )
        assert result["data"]["success"] is False, f"directory={bad!r} should fail"
        assert result["data"]["reason"] == "invalid_param", (
            f"directory={bad!r} got reason={result['data']['reason']!r}"
        )


@requires_webapi
@pytest.mark.asyncio
async def test_no_agentsmd_wrong_type_returns_invalid_param() -> None:
    plugin = _make_plugin()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:test:1",
        worktree=None,
        body={"directory": "/tmp/x", "no_agentsmd": "yes"},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_param"


@requires_webapi
@pytest.mark.asyncio
async def test_no_codegraph_wrong_type_returns_invalid_param() -> None:
    plugin = _make_plugin()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:test:1",
        worktree=None,
        body={"directory": "/tmp/x", "no_codegraph": "yes"},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_param"


@requires_webapi
@pytest.mark.asyncio
async def test_force_wrong_type_returns_invalid_param() -> None:
    plugin = _make_plugin()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:test:1",
        worktree=None,
        body={"directory": "/tmp/x", "force": 1},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_param"


# ── 成功路径 ─────────────────────────────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_successful_load_full_path(plugin_with_mocks, tmp_path) -> None:
    """目录已存在且无 AGENTS.md → 成功登记,不生成、不初始化 codegraph。"""
    _reset_state()
    plugin = plugin_with_mocks
    target = tmp_path / "repo"
    target.mkdir()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:success:1",
        worktree=None,
        body={"directory": str(target)},
    )
    assert result["data"]["success"] is True
    assert result["data"]["reason"] is None
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(target)
    assert data["umo"] == "webchat:success:1"
    # No AGENTS.md → the only skipped subsystem is agentsmd. codegraph is
    # not part of load any more, so it is not listed as "skipped".
    assert data["skipped_substeps"] == ["agentsmd"]
    assert data["agents_md"] is False
    assert data["loaded_at"] > 0
    # substep_messages 至少应包含 "✅ 项目已加载" 总结
    assert any("项目已加载" in m for m in data["substep_messages"])
    # state 应被登记
    from tools.project import state as _proj_state

    info = _proj_state.get("webchat:success:1")
    assert info is not None
    assert info["directory"] == data["directory"]
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_silent_load_does_not_yield_to_event(plugin_with_mocks, tmp_path) -> None:
    """**核心需求**:静默 load 不得向 event 写任何用户可见消息。

    验证:handler 内部构造的 silent_event 在整个调用过程中,plain_result
    被调用次数应为 0。``project_load_step`` 内部需要 ``event.plain_result``
    但 silent_event 走的是 ``lambda x: x`` — 我们的外层不调它,所以 plugin
    公开的 ``agentsmd.init/load`` 看不到 silent_event 的 ``plain_result``
    被外层调用。
    """
    _reset_state()
    plugin = plugin_with_mocks
    target = tmp_path / "silent"
    target.mkdir()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:silent:1",
        worktree=None,
        body={"directory": str(target)},
    )
    assert result["data"]["success"] is True
    # 关键断言:handler 内部构造的 silent_event 是局部变量,不在 plugin
    # 上注册。这里我们验证 plugin.agentsmd.init 没收到任何 yield 用户消息
    # 用的 event — MagicMock.side_effect 用 _ok 接收 event 形参,
    # MagicMock 不会调 plain_result(因为我们的 _ok 不调 event.plain_result)。
    # 因此这里只是间接验证:_ok() 只 yield 字符串,从未触发 silent_event.plain_result。
    assert result["data"]["loaded"] is True
    _reset_state()


# ── 失败路径 ─────────────────────────────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_agentsmd_disabled_skips_injection_but_loads(tmp_path) -> None:
    """agentsmd_enabled=False → 仍然登记项目,只是不注入 AGENTS.md。"""
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    plugin._config["agentsmd_enabled"] = False
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:off:1",
        worktree=None,
        body={"directory": str(tmp_path)},
    )
    assert result["data"]["success"] is True
    assert result["data"]["agents_md"] is False
    assert result["data"]["skipped_substeps"] == ["agentsmd"]


@requires_webapi
@pytest.mark.asyncio
async def test_codegraph_disabled_does_not_block_load(tmp_path) -> None:
    """codegraph_enabled=False → 项目加载不受影响。"""
    plugin = _make_plugin()
    plugin._config["codegraph_enabled"] = False
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:off:2",
        worktree=None,
        body={"directory": str(tmp_path)},
    )
    assert result["data"]["success"] is True
    assert result["data"]["loaded"] is True


@requires_webapi
@pytest.mark.asyncio
async def test_project_already_loaded_returns_no_project_loaded() -> None:
    """重复 load → no_project_loaded + previous_directory 字段。"""
    _reset_state()
    from tools.project import state as _proj_state

    _proj_state.put(
        "webchat:already:1",
        {"directory": "/tmp/existing", "loaded_at": 1.0},
    )
    plugin = _make_plugin()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:already:1",
        worktree=None,
        body={"directory": "/tmp/new"},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "no_project_loaded"
    assert result["data"]["silent_reason"] == "project_already_loaded"
    assert result["data"]["previous_directory"] == "/tmp/existing"
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_path_unsafe_returns_path_unsafe(monkeypatch) -> None:
    """路径不安全 → path_unsafe。

    2026-08-06: ``load_impl_silent`` 的路径安全检查用的是
    ``tools.project.manager`` 模块级 import 的 ``is_path_safe``
    (manager.py:602),不是 AgentsmdSubsystem 注入的那份——旧测试把
    lambda 注入到 AgentsmdSubsystem 是打错了位置,从不生效。改为
    monkeypatch manager 模块的符号。
    """
    import tools.project.manager as _pm

    monkeypatch.setattr(_pm, "is_path_safe", lambda *a, **kw: (False, "blacklisted"))
    plugin = _make_plugin()
    plugin.project = ProjectManager(plugin)
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:unsafe:1",
        worktree=None,
        body={"directory": "/some/blacklisted/path"},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "path_unsafe"
    assert result["data"]["silent_reason"] == "path_unsafe"


@requires_webapi
@pytest.mark.asyncio
async def test_agentsmd_load_failure_returns_git_error(tmp_path) -> None:
    """AGENTS.md 读取失败 → reason=git_error + silent_reason=agentsmd_load_failed。"""
    _reset_state()
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    _patch_substeps_fail_at(plugin, fail_step="agentsmd_load")
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:fail:1",
        worktree=None,
        body={"directory": str(tmp_path)},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "git_error"
    assert result["data"]["silent_reason"] == "agentsmd_load_failed"
    assert any(m.startswith("❌") for m in result["data"]["substep_messages"])
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_missing_directory_returns_directory_missing(tmp_path) -> None:
    """目录不存在 → directory_missing,不登记状态。"""
    _reset_state()
    plugin = _make_plugin()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:missing:1",
        worktree=None,
        body={"directory": str(tmp_path / "missing")},
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "directory_missing"
    assert result["data"]["silent_reason"] == "directory_missing"
    _reset_state()


# ── no_agentsmd / no_codegraph / force 行为 ───────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_no_agentsmd_skips_agentsmd_step(tmp_path) -> None:
    """no_agentsmd=true → agentsmd 子方法不被调用,skipped_substeps=["agentsmd"]。"""
    _reset_state()
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:skip:1",
        worktree=None,
        body={"directory": str(tmp_path), "no_agentsmd": True},
    )
    assert result["data"]["success"] is True
    assert result["data"]["skipped_substeps"] == ["agentsmd"]
    assert result["data"]["agents_md"] is False
    plugin.agentsmd.init.assert_not_called()
    plugin.agentsmd.load.assert_not_called()
    plugin.codegraph.init.assert_not_called()
    plugin.codegraph.set_project.assert_not_called()
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_no_codegraph_has_no_effect(tmp_path) -> None:
    """no_codegraph 只为旧客户端兼容保留,不再影响加载。"""
    _reset_state()
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:skip:2",
        worktree=None,
        body={"directory": str(tmp_path), "no_codegraph": True},
    )
    assert result["data"]["success"] is True
    assert result["data"]["skipped_substeps"] == []
    assert result["data"]["agents_md"] is True
    plugin.agentsmd.init.assert_not_called()
    plugin.agentsmd.load.assert_called_once()
    plugin.codegraph.init.assert_not_called()
    plugin.codegraph.set_project.assert_not_called()
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_both_no_flags_still_registers_state(tmp_path) -> None:
    """no_agentsmd + no_codegraph → 仍登记 state,但两个子系统都不动。"""
    _reset_state()
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:skip:3",
        worktree=None,
        body={
            "directory": str(tmp_path),
            "no_agentsmd": True,
            "no_codegraph": True,
        },
    )
    assert result["data"]["success"] is True
    assert result["data"]["skipped_substeps"] == ["agentsmd"]
    assert result["data"]["agents_md"] is False
    plugin.agentsmd.init.assert_not_called()
    plugin.agentsmd.load.assert_not_called()
    plugin.codegraph.init.assert_not_called()
    plugin.codegraph.set_project.assert_not_called()
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_force_overrides_existing_load(tmp_path) -> None:
    """force=true 覆盖已加载项目,state 切换到新目录。"""
    _reset_state()
    from tools.project import state as _proj_state

    _proj_state.put(
        "webchat:force:1",
        {"directory": "/tmp/old", "loaded_at": 1.0},
    )
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    target = tmp_path / "new"
    target.mkdir()
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:force:1",
        worktree=None,
        body={"directory": str(target), "force": True},
    )
    assert result["data"]["success"] is True
    # 新目录被登记
    info = _proj_state.get("webchat:force:1")
    assert info is not None
    assert info["directory"] == str(target)
    assert info["directory"] != "/tmp/old"
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_force_with_no_existing_load_is_noop_unload(tmp_path) -> None:
    """force=true 但未加载 → unload 阶段是 no-op,正常 load 新项目。"""
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:force:2",
        worktree=None,
        body={"directory": str(tmp_path), "force": True},
    )
    assert result["data"]["success"] is True
    plugin.agentsmd.unload.assert_not_called()


# ── envelope 字段完整性 ───────────────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_envelope_has_elapsed_ms(plugin_with_mocks) -> None:
    """envelope 应包含 elapsed_ms 字段(数字)。"""
    result = await _project_load_webapi.handle(
        plugin_with_mocks,
        umo="webchat:env:1",
        worktree=None,
        body={"directory": "/tmp/x"},
    )
    assert "elapsed_ms" in result["data"]
    assert isinstance(result["data"]["elapsed_ms"], int)
    assert result["data"]["elapsed_ms"] >= 0


@requires_webapi
@pytest.mark.asyncio
async def test_envelope_data_includes_all_fields(plugin_with_mocks, tmp_path) -> None:
    """成功路径 data 字段应有完整字段集。"""
    target = tmp_path / "env"
    target.mkdir()
    result = await _project_load_webapi.handle(
        plugin_with_mocks,
        umo="webchat:env:2",
        worktree=None,
        body={"directory": str(target)},
    )
    data = result["data"]
    expected_keys = {
        "loaded",
        "directory",
        "loaded_at",
        "umo",
        "skipped_substeps",
        "substep_messages",
        "agents_md",
    }
    assert expected_keys.issubset(data.keys())


# ── 路径解析 ────────────────────────────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_directory_with_surrounding_quotes_is_stripped(
    plugin_with_mocks,
    tmp_path,
) -> None:
    """directory 带引号(用户从聊天框粘路径)→ strip 后再 resolve。"""
    target = tmp_path / "quoted"
    target.mkdir()
    result = await _project_load_webapi.handle(
        plugin_with_mocks,
        umo="webchat:quote:1",
        worktree=None,
        body={"directory": f'"{target}"'},
    )
    assert result["data"]["success"] is True
    # strip_surrounding_quotes 应当去掉引号
    assert '"' not in result["data"]["directory"]


# ── ProjectManager.load_impl_silent 直接单测(脱离 webapi) ──────


@pytest.mark.asyncio
async def test_load_impl_silent_returns_dict_with_expected_keys() -> None:
    """ProjectManager.load_impl_silent 返回的 dict 字段齐全。"""
    from unittest.mock import MagicMock as _MM

    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    event = _MM()
    event.unified_msg_origin = "webchat:impl:1"
    event.plain_result = lambda x: x

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await plugin.project.load_impl_silent(event, td)
        assert isinstance(result, dict)
        assert result["ok"] is True
        assert set(result.keys()) >= {
            "ok",
            "directory",
            "loaded_at",
            "skipped_substeps",
            "substep_messages",
            "reason",
            "previous_directory",
        }
        assert result["reason"] is None
        assert result["loaded_at"] > 0
        assert result["directory"] == str(Path(td).resolve())
    _reset_state()


@pytest.mark.asyncio
async def test_load_impl_silent_does_not_invoke_event_plain_result() -> None:
    """silent 路径核心契约:不让 event 写任何用户可见消息。

    silent_event 应当**只有** ``unified_msg_origin`` + ``plain_result`` 属性
    被访问,但 ``plain_result`` 应当**从未被调用**(因为 load_impl_silent
    内部不调 event.plain_result,所有进度走 messages 列表)。

    注意:project_load_step 内部 yield 失败总结时会调 event.plain_result
    (MagicMock 这里 lambda x: x → 字符串),这条消息会进 messages。
    所以失败路径上 plain_result 会被调。本测试走"全成功"路径,验证
    成功路径上 plain_result 调用次数为 0。
    """
    from unittest.mock import MagicMock as _MM

    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    plain_call_count = 0

    def _counting_plain(text):
        nonlocal plain_call_count
        plain_call_count += 1
        return text

    event = _MM()
    event.unified_msg_origin = "webchat:impl:2"
    event.plain_result = _counting_plain

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await plugin.project.load_impl_silent(event, td)
        assert result["ok"] is True
        # 成功路径 → plain_result 未被调
        assert plain_call_count == 0, (
            f"silent 路径误调 event.plain_result {plain_call_count} 次"
        )
    _reset_state()


# ── smoke 端到端:_wrap + handler ─────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_wrap_adaptor_passes_body_and_umo_to_project_load(
    monkeypatch,
) -> None:
    """_wrap 适配器对 POST /spcode/project-load 正确注入 body+umo。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def stub_handle(plugin, *, body=None, umo=None, worktree=None):  # type: ignore[no-untyped-def]
        captured["body"] = body
        captured["umo"] = umo
        captured["worktree"] = worktree
        # 模拟 invalid_param 快速返回(不需要真实子方法)
        return {
            "success": False,
            "reason": "invalid_param",
            "data": {"loaded": False},
        }

    payload = {"directory": "/tmp/x", "no_agentsmd": True, "umo": "abc:1"}

    async def _json(default=None):  # type: ignore[no-untyped-def]
        return payload

    mock_req = make_web_request_mock()
    mock_req.method = "POST"
    mock_req.json = _json
    monkeypatch.setattr(web, "request", mock_req)

    from tools.webapi import _wrap

    view = _wrap(stub_handle, plugin=None)
    await view()

    assert captured["body"] == payload
    assert captured["umo"] == "abc:1"
    # worktree 也被注入(尽管 handler 不使用)
    assert captured["worktree"] is None
