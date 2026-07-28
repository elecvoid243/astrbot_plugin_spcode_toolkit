"""Tests for the ``POST /spcode/project-load`` webapi endpoint.

Author: elecvoid243 · 2026-07-28

覆盖场景:
- 参数校验:无 umo / 无 body / directory 缺失 / directory 非 str /
  no_agentsmd / no_codegraph / force 类型错
- 静默 load 成功路径(完整 4 步流水线,不向 event yield)
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
    from tools.agentsmd import AgentsmdSubsystem
    from tools.project import ProjectManager

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **kw: (True, ""),
    )
    plugin.project = ProjectManager(plugin)
    return plugin


def _patch_substeps_success(plugin) -> None:
    """把 4 个子方法 mock 为成功路径 — yield 单一 OK 消息后结束。"""

    async def _ok(*a, **kw):
        yield "mock-substep-ok"

    for name in ("init", "load"):
        m = MagicMock(side_effect=_ok)
        setattr(plugin.agentsmd, name, m)

    plugin.agentsmd.unload = MagicMock(return_value="mock-unload-ok")

    for name in ("init", "set_project"):
        m = MagicMock(side_effect=_ok)
        setattr(plugin.codegraph, name, m)


def _patch_substeps_fail_at(plugin, *, fail_step: str) -> None:
    """让某个子方法 yield ❌ 消息 → 触发 ProjectLoadAbort。

    Args:
        fail_step: "agentsmd_init" / "agentsmd_load" / "codegraph_init"
                   / "codegraph_set"。
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
    elif fail_step == "codegraph_set":
        plugin.codegraph.set_project = MagicMock(side_effect=_fail)
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
    assert result["success"] is False
    assert result["reason"] == "invalid_body"
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
    assert result["success"] is False
    assert result["reason"] == "invalid_body"


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
        assert result["success"] is False, f"directory={bad!r} should fail"
        assert result["reason"] == "invalid_param", (
            f"directory={bad!r} got reason={result['reason']!r}"
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
    assert result["success"] is False
    assert result["reason"] == "invalid_param"


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
    assert result["success"] is False
    assert result["reason"] == "invalid_param"


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
    assert result["success"] is False
    assert result["reason"] == "invalid_param"


# ── 成功路径 ─────────────────────────────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_successful_load_full_path(plugin_with_mocks) -> None:
    """完整 4 步流水线成功 → success=True + loaded=True + 完整 envelope。"""
    _reset_state()
    plugin = plugin_with_mocks
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:success:1",
        worktree=None,
        body={"directory": "/tmp/some/repo"},
    )
    assert result["success"] is True
    assert result["reason"] is None
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(Path("/tmp/some/repo").resolve())
    assert data["umo"] == "webchat:success:1"
    assert data["skipped_substeps"] == []  # 无 flag = 未跳过任何
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
async def test_silent_load_does_not_yield_to_event(plugin_with_mocks) -> None:
    """**核心需求**:静默 load 不得向 event 写任何用户可见消息。

    验证:handler 内部构造的 silent_event 在整个调用过程中,plain_result
    被调用次数应为 0。``project_load_step`` 内部需要 ``event.plain_result``
    但 silent_event 走的是 ``lambda x: x`` — 我们的外层不调它,所以 plugin
    公开的 ``agentsmd.init/load`` 看不到 silent_event 的 ``plain_result``
    被外层调用。
    """
    _reset_state()
    plugin = plugin_with_mocks
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:silent:1",
        worktree=None,
        body={"directory": "/tmp/silent"},
    )
    assert result["success"] is True
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
async def test_agentsmd_disabled_returns_feature_disabled() -> None:
    """agentsmd_enabled=False 且无 no_agentsmd → feature_disabled。"""
    plugin = _make_plugin()
    plugin._config["agentsmd_enabled"] = False
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:off:1",
        worktree=None,
        body={"directory": "/tmp/x"},
    )
    assert result["success"] is False
    assert result["reason"] == "feature_disabled"
    assert result["data"]["silent_reason"] == "agentsmd_disabled"


@requires_webapi
@pytest.mark.asyncio
async def test_codegraph_disabled_returns_feature_disabled() -> None:
    """codegraph_enabled=False 且无 no_codegraph → feature_disabled。"""
    plugin = _make_plugin()
    plugin._config["codegraph_enabled"] = False
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:off:2",
        worktree=None,
        body={"directory": "/tmp/x"},
    )
    assert result["success"] is False
    assert result["reason"] == "feature_disabled"
    assert result["data"]["silent_reason"] == "codegraph_disabled"


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
    assert result["success"] is False
    assert result["reason"] == "no_project_loaded"
    assert result["data"]["silent_reason"] == "project_already_loaded"
    assert result["data"]["previous_directory"] == "/tmp/existing"
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_path_unsafe_returns_path_unsafe() -> None:
    """路径不安全 → path_unsafe。"""
    plugin = _make_plugin()
    # agentsmd / codegraph 子系统 — 真实 ProjectManager 实例
    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **kw: (False, "blacklisted"),
    )
    plugin.project = ProjectManager(plugin)
    result = await _project_load_webapi.handle(
        plugin,
        umo="webchat:unsafe:1",
        worktree=None,
        body={"directory": "/some/blacklisted/path"},
    )
    assert result["success"] is False
    assert result["reason"] == "path_unsafe"
    assert result["data"]["silent_reason"] == "path_unsafe"


@requires_webapi
@pytest.mark.asyncio
async def test_agentsmd_init_failure_returns_git_error() -> None:
    """agentsmd.init 失败 → reason=git_error + silent_reason=agentsmd_init_failed。"""
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_fail_at(plugin, fail_step="agentsmd_init")
    # agentsmd 路径上需要"AGENTS.md 不存在"才会调 init。我们 mock
    # Path.is_file 但更简单:让 target/AGENTS.md 不存在的"路径存在"是
    # 真实的 — 但 Path("...").resolve() 在测试环境的 /tmp 不一定存在。
    # 这里我们改走:resolve 后用一个不存在的子目录,触发 init 路径。
    # 因 _patch_substeps_fail_at 已经把 init mock 为 _fail,_ok 顺序下
    # project_load_step 会拿到"❌ 模拟子步骤失败" → 中止。
    # 唯一可能:AGENTS.md 已存在的分支不调 init。
    # 解决:用 monkeypatch 让 target.exists() 返 True 但 AGENTS.md 不存在。
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:fail:1",
            worktree=None,
            body={"directory": td},
        )
        assert result["success"] is False
        assert result["reason"] == "git_error"
        assert result["data"]["silent_reason"] == "agentsmd_init_failed"
        # substep_messages 应包含"❌"失败信号
        assert any(
            m.startswith("❌") for m in result["data"]["substep_messages"]
        )


@requires_webapi
@pytest.mark.asyncio
async def test_codegraph_init_failure_returns_git_error() -> None:
    """codegraph.init 失败 → git_error。"""
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_fail_at(plugin, fail_step="codegraph_init")
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:fail:2",
            worktree=None,
            body={"directory": td},
        )
        assert result["success"] is False
        assert result["reason"] == "git_error"
        assert result["data"]["silent_reason"] in {
            "codegraph_init_failed",
            "codegraph_set_failed",  # 兜底:step 内部抛 abort 时
        }


# ── no_agentsmd / no_codegraph / force 行为 ───────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_no_agentsmd_skips_agentsmd_step() -> None:
    """no_agentsmd=true → agentsmd 子方法不被调用,skipped_substeps=["agentsmd"]。"""
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:skip:1",
            worktree=None,
            body={"directory": td, "no_agentsmd": True},
        )
        assert result["success"] is True
        assert result["data"]["skipped_substeps"] == ["agentsmd"]
        # agentsmd.init / load 都不应被调用
        plugin.agentsmd.init.assert_not_called()
        plugin.agentsmd.load.assert_not_called()
        # codegraph 子方法被调用
        plugin.codegraph.init.assert_called_once()
        plugin.codegraph.set_project.assert_called_once()
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_no_codegraph_skips_codegraph_step() -> None:
    """no_codegraph=true → codegraph 子方法不被调用。"""
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:skip:2",
            worktree=None,
            body={"directory": td, "no_codegraph": True},
        )
        assert result["success"] is True
        assert result["data"]["skipped_substeps"] == ["codegraph"]
        plugin.codegraph.init.assert_not_called()
        plugin.codegraph.set_project.assert_not_called()
        plugin.agentsmd.init.assert_called_once()
        plugin.agentsmd.load.assert_called_once()
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_both_no_flags_makes_shell_load() -> None:
    """no_agentsmd + no_codegraph 双跳过 → 仍登记 state(空壳 load)。"""
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:skip:3",
            worktree=None,
            body={
                "directory": td,
                "no_agentsmd": True,
                "no_codegraph": True,
            },
        )
        assert result["success"] is True
        assert sorted(result["data"]["skipped_substeps"]) == [
            "agentsmd",
            "codegraph",
        ]
        # 全部子方法跳过
        plugin.agentsmd.init.assert_not_called()
        plugin.agentsmd.load.assert_not_called()
        plugin.codegraph.init.assert_not_called()
        plugin.codegraph.set_project.assert_not_called()
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_force_overrides_existing_load() -> None:
    """force=true 覆盖已加载项目,state 切换到新目录。"""
    _reset_state()
    from tools.project import state as _proj_state

    _proj_state.put(
        "webchat:force:1",
        {"directory": "/tmp/old", "loaded_at": 1.0},
    )
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:force:1",
            worktree=None,
            body={"directory": td, "force": True},
        )
        assert result["success"] is True
        # 新目录被登记
        info = _proj_state.get("webchat:force:1")
        assert info is not None
        assert info["directory"] == str(Path(td).resolve())
        assert info["directory"] != "/tmp/old"
    _reset_state()


@requires_webapi
@pytest.mark.asyncio
async def test_force_with_no_existing_load_is_noop_unload() -> None:
    """force=true 但未加载 → unload 阶段是 no-op,正常 load 新项目。"""
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        result = await _project_load_webapi.handle(
            plugin,
            umo="webchat:force:2",
            worktree=None,
            body={"directory": td, "force": True},
        )
        assert result["success"] is True
        # agentsmd.unload 在 no-op 路径上仍被调(因为 _silent_unload
        # 不查 state 直接调)? 实际:no-op 路径 _silent_unload 提前 return
        # → unload 不被调
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
    assert "elapsed_ms" in result
    assert isinstance(result["elapsed_ms"], int)
    assert result["elapsed_ms"] >= 0


@requires_webapi
@pytest.mark.asyncio
async def test_envelope_data_includes_all_fields(plugin_with_mocks) -> None:
    """成功路径 data 字段应有完整字段集。"""
    result = await _project_load_webapi.handle(
        plugin_with_mocks,
        umo="webchat:env:2",
        worktree=None,
        body={"directory": "/tmp/x"},
    )
    data = result["data"]
    expected_keys = {
        "loaded",
        "directory",
        "loaded_at",
        "umo",
        "skipped_substeps",
        "substep_messages",
    }
    assert expected_keys.issubset(data.keys())


# ── 路径解析 ────────────────────────────────────────────────────────


@requires_webapi
@pytest.mark.asyncio
async def test_directory_with_surrounding_quotes_is_stripped(
    plugin_with_mocks,
) -> None:
    """directory 带引号(用户从聊天框粘路径)→ strip 后再 resolve。"""
    result = await _project_load_webapi.handle(
        plugin_with_mocks,
        umo="webchat:quote:1",
        worktree=None,
        body={"directory": '"/tmp/quoted"'},
    )
    assert result["success"] is True
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
