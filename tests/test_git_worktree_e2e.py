"""End-to-end smoke tests for git worktree management endpoints.

PR-D (v2.14.0, 2026-06-26): full lifecycle E2E covering ADD / REMOVE / LOCK / UNLOCK.

Each test sets up a real git repo with ``subprocess`` (no mocks for git),
exercises the handler end-to-end, and asserts both the response envelope
shape and the on-disk state.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

import pytest


def _init_git_repo(path, branch="main") -> None:
    """Initialize a git repo at ``path`` with a single commit."""
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


def _make_plugin(directory: str) -> tuple[MagicMock, str]:
    """Construct a MagicMock plugin that passes preflight."""
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    umo = "test:umo"
    plugin.get_loaded_project.return_value = {
        "directory": directory,
        "loaded_at": 0.0,
    }
    return plugin, umo


def _norm(p: str) -> str:
    return os.path.normpath(p)


# ── E2E case 1: full lifecycle add → lock → unlock → remove ──────────


@pytest.mark.asyncio
async def test_e2e_add_lock_unlock_remove_cycle(tmp_path):
    """完整生命周期:add → lock → unlock → remove。"""
    from tools.webapi.git_worktree_add import handle as add_handle
    from tools.webapi.git_worktree_lock import handle as lock_handle
    from tools.webapi.git_worktree_unlock import handle as unlock_handle
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)

    plugin, umo = _make_plugin(str(primary))

    # ── ADD ──
    r = await add_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "branch": "feat-cycle", "create": True},
    )
    assert r["data"]["reason"] is None, r["data"]["stderr"]
    assert _norm(r["data"]["worktree"]) == _norm(str(linked))

    # ── LOCK ──
    r = await lock_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "reason": "integration test"},
    )
    assert r["data"]["reason"] is None, r["data"]["stderr"]
    assert r["data"]["locked"] is True
    assert r["data"]["lock_reason"] == "integration test"

    # ── UNLOCK ──
    r = await unlock_handle(plugin, umo=umo, worktree=None, body={"path": str(linked)})
    assert r["data"]["reason"] is None, r["data"]["stderr"]
    assert r["data"]["locked"] is False

    # ── REMOVE ──
    r = await remove_handle(plugin, umo=umo, worktree=None, body={"path": str(linked)})
    assert r["data"]["reason"] is None, r["data"]["stderr"]
    assert _norm(r["data"]["removed_path"]) == _norm(str(linked))


# ── E2E case 2: locked worktree cannot be removed ────────────────────


@pytest.mark.asyncio
async def test_e2e_remove_locked_worktree_rejected(tmp_path):
    """locked worktree → REMOVE 必须被拒(force=true 也不绕过)。"""
    from tools.webapi.git_worktree_add import handle as add_handle
    from tools.webapi.git_worktree_lock import handle as lock_handle
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)

    plugin, umo = _make_plugin(str(primary))

    # ADD + LOCK
    await add_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "branch": "feat-locked", "create": True},
    )
    await lock_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "reason": "must unlock first"},
    )

    # REMOVE without force → worktree_locked
    r = await remove_handle(plugin, umo=umo, worktree=None, body={"path": str(linked)})
    assert r["data"]["reason"] == "worktree_locked"

    # REMOVE with force=true → STILL worktree_locked (force 不绕过 locked 闸)
    r = await remove_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked), "force": True}
    )
    assert r["data"]["reason"] == "worktree_locked"

    # Verify worktree still exists on disk
    assert linked.exists()


# ── E2E case 3: remove main worktree always rejected ─────────────────


@pytest.mark.asyncio
async def test_e2e_remove_main_always_rejected(tmp_path):
    """main worktree → REMOVE 必须被拒(force=true 也不绕过)。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)

    plugin, umo = _make_plugin(str(primary))

    # Without force
    r = await remove_handle(plugin, umo=umo, worktree=None, body={"path": str(primary)})
    assert r["data"]["reason"] == "cannot_remove_main"

    # With force=true → STILL cannot_remove_main
    r = await remove_handle(
        plugin, umo=umo, worktree=None, body={"path": str(primary), "force": True}
    )
    assert r["data"]["reason"] == "cannot_remove_main"

    # Verify main worktree still exists
    assert primary.exists()


# ── E2E case 4: ADD creates branch + checkout ────────────────────────


@pytest.mark.asyncio
async def test_e2e_add_creates_branch_and_checkout(tmp_path):
    """ADD with new branch → 验证 branch 已创建 + linked worktree 已 checkout。"""
    from tools.webapi.git_worktree_add import handle as add_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)

    plugin, umo = _make_plugin(str(primary))

    # ADD with create=True (default) and branch=feat-x
    r = await add_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "branch": "feat-checkout", "create": True},
    )
    assert r["data"]["reason"] is None, r["data"]["stderr"]

    # Verify branch exists
    out = subprocess.run(
        ["git", "-C", str(primary), "branch", "--list", "feat-checkout"],
        capture_output=True,
        text=True,
    )
    assert "feat-checkout" in out.stdout

    # Verify linked worktree is checked out on feat-checkout
    out = subprocess.run(
        ["git", "-C", str(linked), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "feat-checkout"


# ── E2E case 5: two concurrent ADDs to different paths ──────────────


@pytest.mark.asyncio
async def test_e2e_concurrent_adds_different_paths(tmp_path):
    """连续 ADD 两次到不同 path → 两个 worktree 都应成功。"""
    from tools.webapi.git_worktree_add import handle as add_handle

    primary = tmp_path / "primary"
    linked_a = tmp_path / "linked_a"
    linked_b = tmp_path / "linked_b"
    _init_git_repo(primary)

    plugin, umo = _make_plugin(str(primary))

    # First ADD
    r1 = await add_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked_a), "branch": "feat-a", "create": True},
    )
    assert r1["data"]["reason"] is None, r1["data"]["stderr"]
    assert _norm(r1["data"]["worktree"]) == _norm(str(linked_a))

    # Second ADD (different path, different branch)
    r2 = await add_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked_b), "branch": "feat-b", "create": True},
    )
    assert r2["data"]["reason"] is None, r2["data"]["stderr"]
    assert _norm(r2["data"]["worktree"]) == _norm(str(linked_b))

    # Verify both on disk
    assert linked_a.exists()
    assert linked_b.exists()
    assert linked_a.is_dir()
    assert linked_b.is_dir()

    # Verify both branches exist
    out = subprocess.run(
        ["git", "-C", str(primary), "branch", "--list"],
        capture_output=True,
        text=True,
    )
    assert "feat-a" in out.stdout
    assert "feat-b" in out.stdout
