"""Tests for the GET /spcode/git-worktrees HTTP endpoint.

Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.2
Author: elecvoid243 @ 2026-06-18
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Enable pytest-asyncio for `async def` test functions under strict mode.
pytestmark = pytest.mark.asyncio

# Match the sys.path pattern used by test_git_diff.py
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent  # F:\github
_PROJECT_DIR = Path(__file__).resolve().parent.parent  # F:\github\astrbot_plugin_spcode_toolkit
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ────────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ────────────────────────────────────────────────────────────────────

def _make_plugin() -> Any:
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    plugin._loaded_projects = {}
    plugin._loaded_agents = {}
    plugin._codegraph_projects = {}
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": None,
        "git_path": "",
    }
    return plugin


@pytest.fixture
def plugin():
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    plugin._loaded_projects[umo] = {
        "directory": str(directory),
        "loaded_at": time.time(),
    }


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────

async def test_returns_single_main_worktree(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await plugin.handle_get_git_worktrees()
    assert result["status"] == "ok"
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(tmp_path)
    assert data["umo"] == "test:umo"
    assert data["reason"] is None
    assert isinstance(data["worktrees"], list)
    assert len(data["worktrees"]) == 1
    wt = data["worktrees"][0]
    # Git returns forward slashes on Windows; normalize for comparison.
    import os
    assert os.path.normpath(wt["path"]) == os.path.normpath(str(tmp_path))
    assert wt["is_main"] is True
    assert wt["branch"] is not None
    assert len(wt["head_sha"]) > 0


async def test_returns_multiple_worktrees(plugin, tmp_path):
    _init_git_repo(tmp_path)
    wt2 = tmp_path.parent / f"wt2_multi_{tmp_path.name}"
    subprocess.run(
        ["git", "-C", str(tmp_path), "worktree", "add", str(wt2), "-b", "wt2"],
        check=True,
    )
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    assert data["loaded"] is True
    assert len(data["worktrees"]) == 2
    import os
    main_wt = next(w for w in data["worktrees"] if w["is_main"])
    assert os.path.normpath(main_wt["path"]) == os.path.normpath(str(tmp_path))
    assert main_wt["branch"] is not None
    other_wt = next(w for w in data["worktrees"] if not w["is_main"])
    assert os.path.normpath(other_wt["path"]) == os.path.normpath(str(wt2))
    assert other_wt["branch"] == "wt2"


async def test_marks_main_worktree_correctly(plugin, tmp_path):
    _init_git_repo(tmp_path)
    wt2 = tmp_path.parent / f"wt2_marks_{tmp_path.name}"
    subprocess.run(
        ["git", "-C", str(tmp_path), "worktree", "add", str(wt2), "-b", "wt2"],
        check=True,
    )
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    # First worktree in output is always main (git invariant)
    assert data["worktrees"][0]["is_main"] is True
    assert data["worktrees"][1]["is_main"] is False


async def test_detached_worktree_branch_is_none(plugin, tmp_path):
    _init_git_repo(tmp_path)
    wt2 = tmp_path.parent / f"wt2_detached_{tmp_path.name}"
    # Add a detached worktree (no -b flag)
    subprocess.run(
        ["git", "-C", str(tmp_path), "worktree", "add", "--detach", str(wt2)],
        check=True,
    )
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    assert len(data["worktrees"]) == 2
    detached = next(w for w in data["worktrees"] if not w["is_main"])
    assert detached["branch"] is None
    assert detached["head_sha"] != ""


async def test_no_project_loaded(plugin):
    # No project loaded
    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "no_project_loaded"
    assert data["worktrees"] is None
    assert data["umo"] is None


async def test_directory_missing(plugin, tmp_path):
    _load_project(plugin, "test:umo", str(tmp_path / "does_not_exist"))
    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "directory_missing"
    assert data["directory"] is not None


async def test_not_a_git_repo(plugin, tmp_path):
    # tmp_path exists but is not a git repo
    (tmp_path / "README.md").write_text("not a repo", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "not_a_git_repo"
    assert data["worktrees"] is None


async def test_feature_disabled(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    # Disable feature flag
    plugin._config["agentsmd_enabled"] = False
    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "feature_disabled"


async def test_returns_expected_shape(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_worktrees()
    data = result["data"]
    expected_keys = {
        "loaded", "directory", "umo", "worktrees", "reason", "stderr", "elapsed_ms",
    }
    assert expected_keys.issubset(data.keys()), data.keys()
