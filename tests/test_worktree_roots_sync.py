"""_worktree_activation_inject keeps core dynamic roots in sync."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.tools import fs_access

UMO = "webchat:FriendMessage:webchat!tester!s1"


def _make_plugin(tmp_path, loaded_directory):
    plugin = SimpleNamespace()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}

    def get_loaded_project(_umo):
        return {"directory": loaded_directory}

    plugin.get_loaded_project = get_loaded_project
    plugin.wt_root = tmp_path / "wt"
    plugin.wt_root.mkdir(exist_ok=True)
    return plugin


@pytest.fixture(autouse=True)
def _clean_state():
    fs_access.reset()
    yield
    fs_access.reset()


@pytest.mark.asyncio
async def test_active_worktree_becomes_dynamic_root(tmp_path, monkeypatch):
    # NOTE: bare ``import main`` fails — main.py uses relative imports
    # (``from .tools...``); the suite-wide convention is the package path
    # (see tests/conftest.py "Path setup").
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit

    plugin = _make_plugin(tmp_path, str(tmp_path / "repo"))
    # NOTE: patch the package-qualified module — main.py resolves its relative
    # import to ``astrbot_plugin_spcode_toolkit.tools.worktree_activation``;
    # bare ``tools.worktree_activation`` is a distinct sys.modules entry
    # (see test_worktree_activation.py::test_hook_injects_temp_text_part).
    monkeypatch.setattr(
        "astrbot_plugin_spcode_toolkit.tools.worktree_activation._active_worktrees",
        {
            UMO: {
                "path": str(plugin.wt_root),
                "branch": "feat",
                "directory": str(tmp_path / "repo"),
            }
        },
    )

    req = SimpleNamespace(extra_user_content_parts=[])
    event = SimpleNamespace(unified_msg_origin=UMO)
    await SPCodeToolkit._worktree_activation_inject(plugin, event, req)
    assert fs_access.get_dynamic_roots(UMO) == (plugin.wt_root.resolve(),)


@pytest.mark.asyncio
async def test_no_activation_clears_dynamic_roots(tmp_path, monkeypatch):
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit

    plugin = _make_plugin(tmp_path, str(tmp_path / "repo"))
    monkeypatch.setattr(
        "astrbot_plugin_spcode_toolkit.tools.worktree_activation._active_worktrees",
        {},
    )
    fs_access.set_dynamic_roots(UMO, [tmp_path])

    req = SimpleNamespace(extra_user_content_parts=[])
    event = SimpleNamespace(unified_msg_origin=UMO)
    await SPCodeToolkit._worktree_activation_inject(plugin, event, req)
    assert fs_access.get_dynamic_roots(UMO) == ()
