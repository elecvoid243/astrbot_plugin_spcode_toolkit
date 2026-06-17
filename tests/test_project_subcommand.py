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
import sys
from pathlib import Path
from unittest.mock import MagicMock

# main.py 用相对导入,需把项目父目录加到 sys.path 然后以包形式导入。
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

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
    # _agentsmd_unload reads from these — populate empty defaults so the
    # test fixture matches a fresh, never-used plugin instance.
    plugin._loaded_agents = {}
    plugin._codegraph_projects = {}
    # Provide a permissive default config so feature-flag checks pass.
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": None,
    }
    return plugin


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _drive(plugin, event, sub_command, *args):
    """Drain the router into a list of message strings."""
    msgs = []
    async for msg in plugin._project_router(event, sub_command, *args):
        msgs.append(msg)
    return msgs


@pytest.fixture
def plugin():
    return _make_plugin()


def test_project_load_registers_loaded_directory(plugin):
    """``/project load <dir>`` should register the directory under the
    event's umo so subsequent ``status`` queries succeed."""
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


def test_project_unload_clears_directory(plugin):
    """``/project unload`` should remove the entry so ``status`` reports
    nothing loaded."""
    event = _make_event()
    _run(_drive(plugin, event, "load", "/tmp/x"))
    assert event.unified_msg_origin in plugin._loaded_projects
    _run(_drive(plugin, event, "unload"))
    assert event.unified_msg_origin not in plugin._loaded_projects


def test_project_status_reports_loaded_project(plugin):
    """``/project status`` should mention the directory when one is loaded."""
    event = _make_event()
    _run(_drive(plugin, event, "load", "/tmp/y"))
    msgs = _run(_drive(plugin, event, "status"))
    text = "".join(msgs)
    # The directory is resolved via Path.resolve(), which on Windows turns
    # "/tmp/y" into "<Drive>:\\tmp\\y". Assert against the resolved form.
    assert str(Path("/tmp/y").resolve()) in text
    assert "spcode-status" in text  # the hidden JSON marker for the dashboard


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
        return await plugin.handle_get_project_status()

    payload = _run(runner())
    assert payload["status"] == "ok"
    assert payload["data"]["loaded"] is True
    assert payload["data"]["directory"] == "/tmp/z"
    assert payload["data"]["umo"] == "webchat:webchat!u!c2"


def test_handle_get_project_status_returns_unloaded(plugin):
    async def runner():
        return await plugin.handle_get_project_status()

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
        return await plugin.handle_get_project_status()

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
        return await plugin.handle_get_project_status()

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
