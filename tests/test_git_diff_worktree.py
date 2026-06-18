"""Tests for the ?worktree query parameter on GET /spcode/git-diff.

Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.3
Author: elecvoid243 @ 2026-06-18

6-step defense (each test exercises one step):
  1. Empty / whitespace / None rejection
  2. Path-traversal character rejection
  3. Absolute-path requirement
  4. Hidden-directory rejection
  5. Realpath / symlink resolution
  6. git-common-dir match (cross-repo defense)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.asyncio

# Match the sys.path pattern used by sibling test files.
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent  # F:\github
_PROJECT_DIR = Path(__file__).resolve().parent.parent  # F:\github\astrbot_plugin_spcode_toolkit
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ────────────────────────────────────────────────────────────────────
# Fixtures & helpers (mirror test_git_diff.py)
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


def _mock_query(monkeypatch, **values):
    """Mock astrbot.api.web.request.query with provided key/value pairs."""
    from astrbot.api import web
    mock = MagicMock()
    mock.get = lambda key, default=None: values.get(key, default)
    monkeypatch.setattr(web, "request", MagicMock(query=mock))


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────

async def test_worktree_param_returns_diff_for_linked_worktree(
    plugin, tmp_path, monkeypatch
):
    """A valid linked worktree returns its own diff (not the main worktree's)."""
    _init_git_repo(tmp_path)
    # Make a change in main worktree
    (tmp_path / "main_only.txt").write_text("main", encoding="utf-8")
    # Create linked worktree with a different change
    wt2 = tmp_path.parent / f"wt2_valid_{tmp_path.name}"
    subprocess.run(
        ["git", "-C", str(tmp_path), "worktree", "add", str(wt2), "-b", "wt2"],
        check=True,
    )
    (wt2 / "wt2_only.txt").write_text("wt2", encoding="utf-8")
    # Mark as intent-to-add so plain `git diff` reports the new file
    subprocess.run(["git", "add", "-N", "wt2_only.txt"], cwd=wt2, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(monkeypatch, worktree=str(wt2), umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    # Diff should reflect wt2's file, not main's
    paths = [f["path"] for f in data["files_changed"]]
    assert "wt2_only.txt" in paths
    assert "main_only.txt" not in paths


async def test_worktree_param_missing_falls_back_to_main(plugin, tmp_path, monkeypatch):
    """No worktree param → behaves as before (uses loaded project's directory)."""
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "a.txt"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(monkeypatch, umo="test:umo")
    result = await plugin.handle_get_git_diff()
    assert result["data"]["loaded"] is True
    paths = [f["path"] for f in result["data"]["files_changed"]]
    assert "a.txt" in paths


async def test_worktree_param_empty_uses_primary(plugin, tmp_path, monkeypatch):
    """Empty string worktree → treated as missing (spec §2.2)."""
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "a.txt"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(monkeypatch, worktree="", umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(tmp_path)


# ─── Spec §2.2: empty / whitespace → use primary (backward compat) ───

async def test_worktree_whitespace_only_uses_primary(plugin, tmp_path, monkeypatch):
    """Whitespace-only worktree → treated as missing (spec §2.2)."""
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "a.txt"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(monkeypatch, worktree="   ", umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(tmp_path)


# ─── Defense step 2: path traversal ──────────────────────────────────

async def test_worktree_path_traversal_rejected(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(monkeypatch, worktree="../../etc/passwd", umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] in {"worktree_invalid", "feature_disabled"}


# ─── Defense step 3: relative path rejected ──────────────────────────

async def test_worktree_relative_path_rejected(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(monkeypatch, worktree="subdir/wt", umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] in {"worktree_invalid", "feature_disabled"}


# ─── Defense step 4: hidden directory rejected ───────────────────────

async def test_worktree_hidden_dir_rejected(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    # Path to the main repo's .git dir (hidden)
    hidden = str(tmp_path / ".git")
    _mock_query(monkeypatch, worktree=hidden, umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] in {"worktree_invalid", "feature_disabled"}


# ─── Defense step 5: symlink to outside rejected ─────────────────────

async def test_worktree_symlink_to_outside_repo_rejected(
    plugin, tmp_path, monkeypatch
):
    """Symlink that resolves to a different repo's directory must be rejected."""
    # Create main repo
    _init_git_repo(tmp_path)
    # Create a separate repo (decoy)
    decoy = tmp_path.parent / f"decoy_{tmp_path.name}"
    decoy.mkdir(parents=True, exist_ok=True)
    _init_git_repo(decoy)
    # Create symlink inside main repo pointing to decoy
    symlink_path = tmp_path / "evil_link"
    try:
        symlink_path.symlink_to(decoy, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink not supported on this platform")

    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_query(monkeypatch, worktree=str(symlink_path), umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] in {"worktree_invalid", "feature_disabled"}


# ─── Defense step 6: cross-repo (different git-common-dir) ───────────

async def test_worktree_different_repo_rejected(plugin, tmp_path, monkeypatch):
    """A real path that belongs to a DIFFERENT repo must be rejected.

    This is the critical defense: even if all earlier checks pass, the
    resolved directory's git-common-dir must equal the loaded project's
    git-common-dir, or the worktree param is rejected.
    """
    _init_git_repo(tmp_path)
    decoy = tmp_path.parent / f"decoy2_{tmp_path.name}"
    decoy.mkdir(parents=True, exist_ok=True)
    _init_git_repo(decoy)
    _load_project(plugin, "test:umo", str(tmp_path))

    # Try to point at decoy (a real absolute path, exists, is a dir, is a git repo)
    _mock_query(monkeypatch, worktree=str(decoy), umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] in {"worktree_invalid", "feature_disabled"}


# ─── Edge case: non-existent path ────────────────────────────────────

async def test_worktree_nonexistent_path_rejected(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(
        monkeypatch,
        worktree=str(tmp_path / "does_not_exist"),
        umo="test:umo",
    )
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] in {"worktree_invalid", "feature_disabled"}


# ─── Regression: .worktrees/ subdirectory layout (git's official convention) ─

async def test_worktree_param_accepts_dotworktrees_subdir(
    plugin, tmp_path, monkeypatch
):
    """Git stores linked worktrees under `<repo>/.worktrees/<name>/` by default.

    The 6-step defense must NOT reject this layout. Earlier step 4 wrongly
    treated any dot-prefixed path component as hostile, breaking the standard
    `git worktree add .worktrees/<name>` pattern that real users rely on.
    """
    _init_git_repo(tmp_path)
    # Create a linked worktree under .worktrees/ (the official convention).
    wt_path = tmp_path / ".worktrees" / "feature-x"
    subprocess.run(
        [
            "git", "-C", str(tmp_path), "worktree", "add",
            str(wt_path), "-b", "feature-x",
        ],
        check=True,
    )
    (wt_path / "feat.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "feat.txt"], cwd=wt_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))

    _mock_query(monkeypatch, worktree=str(wt_path), umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    # Pre-fix bug: data.reason == "worktree_invalid", data.loaded == False.
    # Post-fix:    data.loaded is True and feat.txt is in files_changed.
    assert data["loaded"] is True, (
        f".worktrees/ layout wrongly rejected: reason={data.get('reason')!r}"
    )
    paths = [f["path"] for f in data["files_changed"]]
    assert "feat.txt" in paths


async def test_worktree_param_still_rejects_dotgit(
    plugin, tmp_path, monkeypatch
):
    """Step 4 must still defend against direct .git access.

    This test guards against an over-correction that would also delete
    the .git directory block (relying solely on step 6 for that defense).
    """
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    # Path with a `.git` component — should still be rejected by step 4.
    bad_path = str(tmp_path / ".git" / "config")
    _mock_query(monkeypatch, worktree=bad_path, umo="test:umo")
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "worktree_invalid"
