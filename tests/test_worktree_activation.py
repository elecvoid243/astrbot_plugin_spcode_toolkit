"""Worktree 激活测试(2026-08-20 worktree-activate)。

覆盖:
- tools/worktree_activation.py 状态模块(绑定校验 / pop / reset)
- POST /spcode/worktree-activate 端点(激活 / 取消激活 / 防御链)
- GET /spcode/git-worktrees 响应的 active_worktree 字段(含过期清理)
- main.py 的 _worktree_activation_inject 钩子(结构性 / 注册性 / 行为性:
  extra_user_content_parts 注入 TextPart.mark_as_temp)

路径形式说明:
    git porcelain 输出正斜杠路径;python str(Path) 在 Windows 上是反斜杠。
    断言统一走 _norm()(os.path.normcase)比较,避免斜杠/大小写差异。

Author: elecvoid243, 2026-08-20
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import worktree_activation as _activation  # noqa: E402
from tools.project import state as _proj_state  # noqa: E402
from tools.webapi import git_worktrees as git_worktrees_module  # noqa: E402

UMO = "test:umo:wtact"


def _norm(p) -> str:
    """跨 OS 路径归一(斜杠 + 大小写)。"""
    return os.path.normcase(str(p))


# ── 工厂 / 辅助函数 ─────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_state():
    """每个测试前后清空 project / activation 模块级单例。

    注意双模块实例:本文件 ``tools.*``(顶层)与 main.py 相对导入的
    ``astrbot_plugin_spcode_toolkit.tools.*`` 是两个不同 module 对象,
    各有一份 dict — 两边都要清,否则跨测试状态泄漏。"""
    _proj_state.reset()
    _activation.reset()
    from astrbot_plugin_spcode_toolkit.tools import worktree_activation as _pkg_wa

    _pkg_wa.reset()
    yield
    _proj_state.reset()
    _activation.reset()
    _pkg_wa.reset()


def _make_plugin_mock_with_loaded_project(directory):
    """构造能通过 preflight 的 MagicMock plugin(仿 test_git_worktree_lock)。"""
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    umo = UMO
    plugin.get_loaded_project.return_value = {"directory": directory, "loaded_at": 0.0}
    return plugin, umo


def _init_git_repo(path, branch="main"):
    """在 path 处初始化带单个 commit 的 git 仓库。"""
    subprocess.run(
        ["git", "init", "-b", branch, str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    (path / "a.txt").write_text("a")
    subprocess.run(
        ["git", "-C", str(path), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def _make_repo_with_linked_worktree(tmp_path):
    """primary(带 commit) + linked worktree(feat 分支) 一起建好。"""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )
    return primary, linked


# ── 状态模块:tools/worktree_activation.py ──────────


def test_state_put_get_roundtrip():
    """put 后 get 返回完整记录(path/branch/directory)。"""
    _activation.put(UMO, path="/wt/feat", branch="feat", directory="/proj")
    info = _activation.get(UMO)
    assert info == {"path": "/wt/feat", "branch": "feat", "directory": "/proj"}


def test_state_get_for_directory_binding():
    """directory 一致才返回记录;不一致 / None / 未激活 → None。"""
    _activation.put(UMO, path="/wt/feat", branch="feat", directory="/proj")
    assert _activation.get_for_directory(UMO, "/proj") is not None
    # 项目切换 → 绑定失效
    assert _activation.get_for_directory(UMO, "/other") is None
    # directory 为 None → 失效
    assert _activation.get_for_directory(UMO, None) is None
    # 未激活的 umo → None
    assert _activation.get_for_directory("other:umo", "/proj") is None


def test_state_pop_and_reset():
    """pop 移除单条;reset 清空全部。"""
    _activation.put(UMO, path="/wt/feat", branch=None, directory="/proj")
    assert _activation.pop(UMO) is not None
    assert _activation.get(UMO) is None
    assert _activation.pop(UMO) is None  # 幂等
    _activation.put(UMO, path="/wt/x", branch="x", directory="/proj")
    _activation.reset()
    assert _activation.get(UMO) is None


# ── 端点:POST /spcode/worktree-activate ────────────


@pytest.mark.asyncio
async def test_activate_linked_worktree(tmp_path):
    """激活 linked worktree → success + active_worktree + 状态落盘。"""
    from tools.webapi.worktree_activate import handle

    primary, linked = _make_repo_with_linked_worktree(tmp_path)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await handle(plugin, umo=umo, worktree=None, body={"path": str(linked)})
    assert result["data"]["reason"] is None, result["data"]["stderr"]
    assert result["data"]["success"] is True
    assert _norm(result["data"]["active_worktree"]) == _norm(linked)
    info = _activation.get(umo)
    assert info is not None and info["branch"] == "feat"
    assert _norm(info["directory"]) == _norm(primary)


@pytest.mark.asyncio
async def test_activate_main_worktree_allowed(tmp_path):
    """激活 main worktree 同样合法(语义:让 LLM 工作在主工作树)。"""
    from tools.webapi.worktree_activate import handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await handle(plugin, umo=umo, worktree=None, body={"path": str(primary)})
    assert result["data"]["reason"] is None
    assert _norm(result["data"]["active_worktree"]) == _norm(primary)


@pytest.mark.asyncio
async def test_deactivate_with_null_path(tmp_path):
    """path=null → 取消激活:active_worktree=None,状态清除。"""
    from tools.webapi.worktree_activate import handle

    primary, linked = _make_repo_with_linked_worktree(tmp_path)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    await handle(plugin, umo=umo, worktree=None, body={"path": str(linked)})
    result = await handle(plugin, umo=umo, worktree=None, body={"path": None})
    assert result["data"]["reason"] is None
    assert result["data"]["active_worktree"] is None
    assert _activation.get(umo) is None


@pytest.mark.asyncio
async def test_deactivate_with_empty_string_path(tmp_path):
    """path 空串(未激活时) → 同样按取消激活处理,success。"""
    from tools.webapi.worktree_activate import handle

    primary, _linked = _make_repo_with_linked_worktree(tmp_path)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await handle(plugin, umo=umo, worktree=None, body={"path": "  "})
    assert result["data"]["reason"] is None
    assert result["data"]["active_worktree"] is None


@pytest.mark.asyncio
async def test_activate_path_not_in_list_rejected(tmp_path):
    """路径合法但不在 worktree 列表 → worktree_not_found。"""
    from tools.webapi.worktree_activate import handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await handle(
        plugin, umo=umo, worktree=None, body={"path": str(tmp_path / "nope")}
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "worktree_not_found"


@pytest.mark.asyncio
async def test_activate_relative_path_rejected(tmp_path):
    """相对路径 → path_unsafe。"""
    from tools.webapi.worktree_activate import handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await handle(
        plugin, umo=umo, worktree=None, body={"path": "relative/feat"}
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_activate_invalid_body_rejected():
    """body 非 dict → invalid_body。"""
    from tools.webapi.worktree_activate import handle

    plugin = MagicMock()
    result = await handle(plugin, umo=UMO, worktree=None, body=None)
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


# ── GET /spcode/git-worktrees:active_worktree 字段 ──


@pytest.mark.asyncio
async def test_git_worktrees_reports_active_worktree(tmp_path):
    """激活后 git-worktrees 响应带 active_worktree;未激活时为 None。"""
    primary, linked = _make_repo_with_linked_worktree(tmp_path)
    _proj_state.put(UMO, {"directory": str(primary), "loaded_at": 1.0})
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.side_effect = lambda umo: _proj_state.get(umo)

    result = await git_worktrees_module.handle(plugin)
    assert result["data"]["loaded"] is True
    assert result["data"]["active_worktree"] is None

    # activation 的 directory 与 project state 同形式(str(Path) 反斜杠)
    _activation.put(UMO, path=str(linked), branch="feat", directory=str(primary))
    result = await git_worktrees_module.handle(plugin)
    assert _norm(result["data"]["active_worktree"]) == _norm(linked)


@pytest.mark.asyncio
async def test_git_worktrees_prunes_stale_activation(tmp_path):
    """激活的 worktree 被删后,GET 返回 None 并清理过期状态。"""
    primary, linked = _make_repo_with_linked_worktree(tmp_path)
    _proj_state.put(UMO, {"directory": str(primary), "loaded_at": 1.0})
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.side_effect = lambda umo: _proj_state.get(umo)
    _activation.put(UMO, path=str(linked), branch="feat", directory=str(primary))

    subprocess.run(
        ["git", "-C", str(primary), "worktree", "remove", "--force", str(linked)],
        check=True,
        capture_output=True,
    )
    result = await git_worktrees_module.handle(plugin)
    assert result["data"]["active_worktree"] is None
    assert _activation.get(UMO) is None, "过期激活状态应被 GET 顺手清理"


def test_git_worktrees_empty_envelope_has_active_worktree_field():
    """空 envelope 也带 active_worktree=None(纯增量字段)。"""
    env = git_worktrees_module._make_git_worktrees_empty_envelope(
        umo=UMO, reason="no_project_loaded", elapsed_ms=1
    )
    assert env["data"]["active_worktree"] is None


# ── main.py 钩子:结构性 / 注册性 / 行为性 ───────────


def test_main_hook_exists_and_is_coroutine():
    """SPCodeToolkit._worktree_activation_inject 必须存在且为 async 方法。"""
    from astrbot_plugin_spcode_toolkit import main as _main_mod

    assert hasattr(_main_mod.SPCodeToolkit, "_worktree_activation_inject"), (
        "SPCodeToolkit 缺少 _worktree_activation_inject 方法 — "
        "激活注入钩子未挂到插件类上,AstrBot 框架不会调用它。"
    )
    method = getattr(_main_mod.SPCodeToolkit, "_worktree_activation_inject")
    assert asyncio.iscoroutinefunction(method)


def test_main_hook_registered_to_on_llm_request_event():
    """钩子必须被 @filter.on_llm_request() 注册到 OnLLMRequestEvent。"""
    from astrbot_plugin_spcode_toolkit import main as _main_mod

    from astrbot.core.star.star_handler import (
        EventType,
        star_handlers_registry,
    )

    method = _main_mod.SPCodeToolkit._worktree_activation_inject
    expected_full_name = f"{method.__module__}_{method.__name__}"
    handler = star_handlers_registry.get_handler_by_full_name(expected_full_name)
    assert handler is not None
    assert handler.event_type == EventType.OnLLMRequestEvent


def _make_hook_plugin(*, flags_on=True):
    """构造 __new__ 出的 plugin 实例(只挂 _config + get_loaded_project)。"""
    from astrbot_plugin_spcode_toolkit import main as _main_mod

    plugin = _main_mod.SPCodeToolkit.__new__(_main_mod.SPCodeToolkit)
    plugin._config = {
        "agentsmd_enabled": flags_on,
        "codegraph_enabled": flags_on,
    }
    plugin.get_loaded_project = lambda umo: _proj_state.get(umo)  # type: ignore[method-assign]
    return plugin


def _make_event(umo: str = UMO) -> MagicMock:
    ev = MagicMock()
    ev.unified_msg_origin = umo
    return ev


def _make_req() -> MagicMock:
    req = MagicMock()
    req.extra_user_content_parts = []
    return req


def test_hook_injects_temp_text_part(tmp_path):
    """激活 + 项目加载 + 路径存在 → 追加 mark_as_temp 的 TextPart。

    状态必须写进 main.py 相对导入的那个模块实例
    (``astrbot_plugin_spcode_toolkit.tools.worktree_activation``),
    否则钩子读到的是另一份空 dict。"""
    from astrbot.core.agent.message import TextPart
    from astrbot_plugin_spcode_toolkit.tools import (
        worktree_activation as _pkg_activation,
    )

    wt_dir = tmp_path / "linked"
    wt_dir.mkdir()
    _proj_state.put(UMO, {"directory": str(tmp_path), "loaded_at": 1.0})
    _pkg_activation.put(UMO, path=str(wt_dir), branch="feat", directory=str(tmp_path))
    plugin = _make_hook_plugin()
    req = _make_req()

    asyncio.run(plugin._worktree_activation_inject(_make_event(), req))

    assert len(req.extra_user_content_parts) == 1
    part = req.extra_user_content_parts[0]
    assert isinstance(part, TextPart)
    assert _norm(wt_dir) in _norm(part.text)
    assert "feat" in part.text
    # mark_as_temp:仅本轮请求可见,不持久化到会话历史
    assert getattr(part, "_no_save", False) is True


def test_hook_no_injection_cases(tmp_path):
    """feature off / 未加载项目 / 项目切换 / 路径消失 → 均不注入。"""
    from astrbot_plugin_spcode_toolkit.tools import (
        worktree_activation as _pkg_activation,
    )

    wt_dir = tmp_path / "linked"
    wt_dir.mkdir()

    # 1. feature flags 关闭
    _proj_state.put(UMO, {"directory": str(tmp_path), "loaded_at": 1.0})
    _pkg_activation.put(UMO, path=str(wt_dir), branch="feat", directory=str(tmp_path))
    plugin = _make_hook_plugin(flags_on=False)
    req = _make_req()
    asyncio.run(plugin._worktree_activation_inject(_make_event(), req))
    assert req.extra_user_content_parts == []

    # 2. 未加载项目
    _proj_state.reset()
    plugin = _make_hook_plugin()
    req = _make_req()
    asyncio.run(plugin._worktree_activation_inject(_make_event(), req))
    assert req.extra_user_content_parts == []

    # 3. 项目已切换(绑定 directory 不一致)
    _proj_state.put(UMO, {"directory": str(tmp_path / "other"), "loaded_at": 2.0})
    _pkg_activation.put(UMO, path=str(wt_dir), branch="feat", directory=str(tmp_path))
    req = _make_req()
    asyncio.run(plugin._worktree_activation_inject(_make_event(), req))
    assert req.extra_user_content_parts == []

    # 4. 激活路径已不存在(worktree 被删)
    _proj_state.put(UMO, {"directory": str(tmp_path), "loaded_at": 3.0})
    _pkg_activation.put(
        UMO, path=str(tmp_path / "gone"), branch="feat", directory=str(tmp_path)
    )
    req = _make_req()
    asyncio.run(plugin._worktree_activation_inject(_make_event(), req))
    assert req.extra_user_content_parts == []
