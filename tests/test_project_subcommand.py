"""Tests for the ``/project`` subcommands and the dashboard-facing
``handle_get_project_status`` HTTP handler.

The decorated ``project`` method on the plugin is replaced by
``@filter.command_group`` with a ``RegisteringCommandable`` object, so
it is not directly callable from a unit test. Instead the tests drive
the internal ``_project_router`` (subcommand dispatch) and the
``_project_*_impl`` helpers (per-subcommand logic), which is the
testable seam the plugin exposes for this purpose.

Author: elecvoid243
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

# 注:sys.path 注入已统一在 tests/conftest.py 完成,这里不再重复。
# (历史原因:本文件原本用 ``Path(__file__).parent.parent.parent``
# 固定 3 层到主项目根,在 worktree 里会多走一层找不到包。)

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402
from tools.webapi import project_status as _project_status

SPCodeToolkit = _main_mod.SPCodeToolkit

import pytest  # noqa: E402


def _make_event(umo: str = "webchat:webchat!u!c1"):
    event = MagicMock()
    # plain_result returns a string carrying the rendered text. The
    # router / impl methods yield these directly, so callers can simply
    # ``"".join(msgs)`` to inspect output.
    event.plain_result = lambda x: x
    event.unified_msg_origin = umo
    return event


def _make_plugin():
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    plugin._loaded_projects = {}
    # agentsmd_unload reads from per-umo state via plugin.agentsmd.state —
    # 用空 AgentsStateManager 保证从未加载的 plugin 实例。
    # PR-5 (2026-06-23): agentsmd 状态从 plugin._loaded_agents dict 迁到
    # plugin.agentsmd.state (AgentsStateManager)。
    plugin._codegraph_projects = {}
    # Provide a permissive default config so feature-flag checks pass.
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": None,
    }
    # PR-5 (2026-06-23): 实例化 agentsmd 子系统;_patch_substeps_success
    # 会进一步把 .init / .load / .unload 替换为 MagicMock。
    from tools.agentsmd import AgentsmdSubsystem

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *args, **kwargs: (True, ""),
    )
    return plugin


def _run(coro):
    # WHY: 早期用 ``asyncio.get_event_loop().run_until_complete(coro)``,在
    # 第一个测试用 ``asyncio.run()``(即 ``_collect_async_gen``) 后,事件循环
    # 会被关闭, ``get_event_loop()`` 在 strict mode 下会抛
    # "There is no current event loop"。改用 ``asyncio.run()`` 隔离每个测试
    # 的事件循环,避免 suite 内的相互污染。
    return asyncio.run(coro)


async def _drive(plugin, event, sub_command, *args):
    """Drain the router into a list of message strings."""
    msgs = []
    async for msg in plugin._project_router(event, sub_command, *args):
        msgs.append(msg)
    return msgs


# WHY: 本文件的旧测试用 ``/tmp/...`` 之类的真实路径触发 ``/project load``,
# 依赖旧版"无条件记录 _loaded_projects" 的 bug 行为 — bug 修了之后
# 路径不存在导致子步骤失败, load 正确中止, 测试就 fail。
# (v2.7.1 修复: 子步骤 yield ❌ 即中止)
# 修法: 用 mock 模拟 4 个子步骤全部成功,让测试聚焦于 _project_router 的
# 分发逻辑和 _loaded_projects 的状态机,而不是子步骤的副作用。
def _patch_substeps_success(plugin):
    """Mock 4 个子步骤为成功路径 — yield 单一 OK 消息后结束。

    PR-5 (2026-06-23): agentsmd 子方法已搬到 plugin.agentsmd.* 上,
    codegraph 子方法暂留 plugin._codegraph_*。
    """

    async def _ok(*args, **kwargs):
        yield "mock-substep-ok"

    # agentsmd async gen 方法 — 挂到 plugin.agentsmd.<method>
    for method_name in ("init", "load"):
        m = MagicMock()
        m.side_effect = _ok
        setattr(plugin.agentsmd, method_name, m)
    # agentsmd 同步方法 — plugin.agentsmd.unload
    plugin.agentsmd.unload = MagicMock(return_value="mock-unload-ok")
    # codegraph 子方法 — PR-6 (2026-06-23) 已搬到 plugin.codegraph.<method>
    for method_name in ("init", "set_project"):
        m = MagicMock()
        m.side_effect = _ok
        setattr(plugin.codegraph, method_name, m)


@pytest.fixture
def plugin_with_mocks():
    """提供 plugin 实例并已 mock 4 个子步骤为成功路径。

    适用于需要触发 ``/project load`` 成功路径的测试。
    """
    p = _make_plugin()
    _patch_substeps_success(p)
    return p


@pytest.fixture
def plugin():
    return _make_plugin()


def test_project_load_registers_loaded_directory(plugin_with_mocks):
    """``/project load <dir>`` should register the directory under the
    event's umo so subsequent ``status`` queries succeed.

    v2.7.1: 改用 mock 子步骤,避免依赖真实文件系统(原版用 /tmp/some/repo,
    但路径不存在时子步骤会失败,新版会正确中止而非假成功)。
    """
    plugin = plugin_with_mocks
    event = _make_event()
    path = "/tmp/some/repo"
    msgs = _run(_drive(plugin, event, "load", path))
    text = "".join(msgs)
    assert "已加载" in text or path in text
    info = plugin._loaded_projects.get(event.unified_msg_origin)
    assert info is not None
    assert info["directory"] == str(Path(path).resolve())


def test_project_load_without_directory_arg_reports_error(plugin):
    """``/project load`` (no args) should yield an error, not crash."""
    event = _make_event()
    msgs = _run(_drive(plugin, event, "load"))
    text = "".join(msgs)
    assert text != ""
    assert "❌" in text or "load" in text.lower()
    assert event.unified_msg_origin not in plugin._loaded_projects


def test_project_unload_clears_directory(plugin_with_mocks):
    """``/project unload`` should remove the entry so ``status`` reports
    nothing loaded.

    v2.7.1: 改用 mock 子步骤,见 ``test_project_load_registers_loaded_directory``。
    """
    plugin = plugin_with_mocks
    event = _make_event()
    _run(_drive(plugin, event, "load", "/tmp/x"))
    assert event.unified_msg_origin in plugin._loaded_projects
    _run(_drive(plugin, event, "unload"))
    assert event.unified_msg_origin not in plugin._loaded_projects


def test_project_status_reports_loaded_project(plugin_with_mocks):
    """``/project status`` should mention the directory when one is loaded.

    v2.7.1: 改用 mock 子步骤,见 ``test_project_load_registers_loaded_directory``。
    """
    plugin = plugin_with_mocks
    event = _make_event()
    _run(_drive(plugin, event, "load", "/tmp/y"))
    msgs = _run(_drive(plugin, event, "status"))
    text = "".join(msgs)
    # The directory is resolved via Path.resolve(), which on Windows turns
    # "/tmp/y" into "<Drive>:\\tmp\\y". Assert against the resolved form.
    assert str(Path("/tmp/y").resolve()) in text
    assert "spcode-status" not in text


def test_project_status_reports_unloaded(plugin):
    event = _make_event()
    msgs = _run(_drive(plugin, event, "status"))
    text = "".join(msgs)
    assert "未加载" in text


def test_project_unknown_subcommand_is_rejected(plugin):
    """An unknown sub_command should yield an error message rather than
    silently passing through."""
    event = _make_event()
    msgs = _run(_drive(plugin, event, "explode"))
    text = "".join(msgs)
    assert text != ""
    assert "❌" in text or "未知" in text or "explode" in text
    # No state change.
    assert event.unified_msg_origin not in plugin._loaded_projects


def test_handle_get_project_status_returns_loaded(plugin):
    async def runner():
        plugin._loaded_projects["webchat:webchat!u!c2"] = {
            "directory": "/tmp/z",
            "loaded_at": 1700000000.0,
        }
        return await _project_status.handle(plugin)

    payload = _run(runner())
    assert payload["status"] == "ok"
    assert payload["data"]["loaded"] is True
    assert payload["data"]["directory"] == "/tmp/z"
    assert payload["data"]["umo"] == "webchat:webchat!u!c2"


def test_handle_get_project_status_returns_unloaded(plugin):
    async def runner():
        return await _project_status.handle(plugin)

    payload = _run(runner())
    assert payload["status"] == "ok"
    assert payload["data"]["loaded"] is False
    assert payload["data"]["directory"] is None


def test_handle_get_project_status_filters_by_umo(plugin):
    plugin._loaded_projects["webchat:webchat!u!c3"] = {
        "directory": "/tmp/a",
        "loaded_at": 1.0,
    }
    plugin._loaded_projects["webchat:webchat!u!c4"] = {
        "directory": "/tmp/b",
        "loaded_at": 2.0,
    }

    async def runner():
        return await _project_status.handle(plugin)

    payload = _run(runner())

    # No umo supplied → returns the most-recently-loaded entry.
    assert payload["data"]["loaded"] is True
    assert payload["data"]["directory"] == "/tmp/b"
    assert payload["data"]["all_loaded_count"] == 2


def test_handle_get_project_status_returns_copy_not_reference(plugin):
    """The handler must return a shallow copy so callers cannot mutate
    internal state."""
    plugin._loaded_projects["webchat:webchat!u!c5"] = {
        "directory": "/tmp/c",
        "loaded_at": 1.0,
    }

    async def runner():
        return await _project_status.handle(plugin)

    payload = _run(runner())
    payload["data"]["directory"] = "mutated"
    assert plugin._loaded_projects["webchat:webchat!u!c5"]["directory"] == "/tmp/c"


def test_get_loaded_project_helper(plugin):
    plugin._loaded_projects["webchat:webchat!u!c6"] = {
        "directory": "/tmp/d",
        "loaded_at": 3.0,
    }
    info = plugin.get_loaded_project("webchat:webchat!u!c6")
    assert info is not None
    assert info["directory"] == "/tmp/d"
    # Missing umo returns None.
    assert plugin.get_loaded_project("nope") is None
