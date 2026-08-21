"""_worktree_activation_inject keeps core dynamic roots in sync.

Dynamic roots = [loaded project dir, active worktree] — both must be
writable in workspace mode even though they live outside data/workspaces.
The roots sync runs regardless of the guidance feature flags; only the
TextPart injection is flag-gated.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.tools import fs_access

UMO = "webchat:FriendMessage:webchat!tester!s1"


def _make_plugin(tmp_path, loaded_directory, *, flags_enabled=True):
    plugin = SimpleNamespace()
    plugin._config = {
        "agentsmd_enabled": flags_enabled,
        "codegraph_enabled": flags_enabled,
    }

    def get_loaded_project(_umo):
        if loaded_directory is None:
            return None
        return {"directory": loaded_directory}

    plugin.get_loaded_project = get_loaded_project
    plugin.repo_root = tmp_path / "repo"
    plugin.repo_root.mkdir(exist_ok=True)
    plugin.wt_root = tmp_path / "wt"
    plugin.wt_root.mkdir(exist_ok=True)
    return plugin


def _patch_activation(monkeypatch, *, active: bool, plugin):
    monkeypatch.setattr(
        "astrbot_plugin_spcode_toolkit.tools.worktree_activation._active_worktrees",
        {
            UMO: {
                "path": str(plugin.wt_root),
                "branch": "feat",
                "directory": str(plugin.repo_root),
            }
        }
        if active
        else {},
    )


@pytest.fixture(autouse=True)
def _clean_state():
    fs_access.reset()
    yield
    fs_access.reset()


@pytest.mark.asyncio
async def test_project_dir_and_worktree_become_dynamic_roots(tmp_path, monkeypatch):
    # NOTE: bare ``import main`` fails — main.py uses relative imports
    # (``from .tools...``); the suite-wide convention is the package path
    # (see tests/conftest.py "Path setup").
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit

    plugin = _make_plugin(tmp_path, str(tmp_path / "repo"))
    # NOTE: patch the package-qualified module — main.py resolves its relative
    # import to ``astrbot_plugin_spcode_toolkit.tools.worktree_activation``;
    # bare ``tools.worktree_activation`` is a distinct sys.modules entry
    # (see test_worktree_activation.py::test_hook_injects_temp_text_part).
    _patch_activation(monkeypatch, active=True, plugin=plugin)

    req = SimpleNamespace(extra_user_content_parts=[])
    event = SimpleNamespace(unified_msg_origin=UMO)
    await SPCodeToolkit._worktree_activation_inject(plugin, event, req)
    assert fs_access.get_dynamic_roots(UMO) == (
        plugin.repo_root.resolve(),
        plugin.wt_root.resolve(),
    )


@pytest.mark.asyncio
async def test_no_activation_keeps_project_dir_root(tmp_path, monkeypatch):
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit

    plugin = _make_plugin(tmp_path, str(tmp_path / "repo"))
    _patch_activation(monkeypatch, active=False, plugin=plugin)
    fs_access.set_dynamic_roots(UMO, [tmp_path])

    req = SimpleNamespace(extra_user_content_parts=[])
    event = SimpleNamespace(unified_msg_origin=UMO)
    await SPCodeToolkit._worktree_activation_inject(plugin, event, req)
    # Stale roots are replaced by the current state: project dir only.
    assert fs_access.get_dynamic_roots(UMO) == (plugin.repo_root.resolve(),)


@pytest.mark.asyncio
async def test_no_loaded_project_clears_dynamic_roots(tmp_path, monkeypatch):
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit

    plugin = _make_plugin(tmp_path, None)
    _patch_activation(monkeypatch, active=False, plugin=plugin)
    fs_access.set_dynamic_roots(UMO, [tmp_path])

    req = SimpleNamespace(extra_user_content_parts=[])
    event = SimpleNamespace(unified_msg_origin=UMO)
    await SPCodeToolkit._worktree_activation_inject(plugin, event, req)
    assert fs_access.get_dynamic_roots(UMO) == ()


@pytest.mark.asyncio
async def test_roots_sync_survives_disabled_guidance_flags(tmp_path, monkeypatch):
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit

    plugin = _make_plugin(tmp_path, str(tmp_path / "repo"), flags_enabled=False)
    _patch_activation(monkeypatch, active=True, plugin=plugin)

    req = SimpleNamespace(extra_user_content_parts=[])
    event = SimpleNamespace(unified_msg_origin=UMO)
    await SPCodeToolkit._worktree_activation_inject(plugin, event, req)
    # Roots sync is decoupled from the guidance flags; only the TextPart
    # injection is skipped.
    assert fs_access.get_dynamic_roots(UMO) == (
        plugin.repo_root.resolve(),
        plugin.wt_root.resolve(),
    )
    assert req.extra_user_content_parts == []
