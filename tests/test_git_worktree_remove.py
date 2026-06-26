"""Tests for POST /spcode/git-worktree-remove endpoint.

PR-C (v2.14.0, 2026-06-26): REMOVE endpoint with 8-layer defense chain.
Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.2

Layered defense:
  L1: body type guard (non-dict → invalid_body)
  L2: _git_endpoint_preflight (5-step)
  L3: _resolve_target_worktree (format + list lookup)
  L4: cannot_remove_main (main worktree hard-forbidden)
  L5: worktree_locked (force=true does NOT bypass)
  L6: worktree_dirty (force=true bypasses)
  L7: git worktree remove [--force] <path>
  L8: _list_worktrees_safe refresh
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest


def _make_plugin_mock_with_loaded_project(directory):
    """Construct a MagicMock plugin that passes preflight with real directory."""
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    umo = "test:umo"
    plugin.get_loaded_project.return_value = {"directory": directory, "loaded_at": 0.0}
    return plugin, umo


def _init_git_repo(path, branch="main"):
    """Initialize a git repo at ``path`` with a single commit (helper for tests)."""
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


def _norm(p):
    """Normalize a path for cross-OS comparison (git porcelain uses ``/`` on Windows)."""
    return os.path.normpath(p)


# ── _map_remove_stderr_to_reason (4 tests) ──────────────────────────────


def test_stderr_not_working_tree():
    """``is not a working tree`` → worktree_not_found."""
    from tools.webapi.git_worktree_remove import _map_remove_stderr_to_reason

    assert (
        _map_remove_stderr_to_reason("fatal: '/target' is not a working tree")
        == "worktree_not_found"
    )


def test_stderr_locked():
    """``is locked`` → worktree_locked."""
    from tools.webapi.git_worktree_remove import _map_remove_stderr_to_reason

    assert (
        _map_remove_stderr_to_reason("fatal: '/target' is locked") == "worktree_locked"
    )


def test_stderr_dirty():
    """``contains modified or untracked files`` → worktree_dirty."""
    from tools.webapi.git_worktree_remove import _map_remove_stderr_to_reason

    assert (
        _map_remove_stderr_to_reason(
            "fatal: '/target' contains modified or untracked files"
        )
        == "worktree_dirty"
    )


def test_stderr_unknown():
    """未识别的 stderr → git_error。"""
    from tools.webapi.git_worktree_remove import _map_remove_stderr_to_reason

    assert _map_remove_stderr_to_reason("fatal: foo bar baz") == "git_error"


# ── handle() — business path tests (8 tests) ───────────────────────────


@pytest.mark.asyncio
async def test_remove_basic_linked_worktree(tmp_path):
    """删除 linked worktree → success + worktrees list 减 1。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked)},
    )
    assert result["data"]["reason"] is None, result["data"]["stderr"]
    # git porcelain emits forward slashes on Windows; normalize before comparing
    assert _norm(result["data"]["removed_path"]) == _norm(str(linked))
    # main worktree still present, linked removed
    remaining_paths = [wt["path"] for wt in result["data"]["worktrees"]]
    assert len(remaining_paths) == 1
    assert _norm(remaining_paths[0]) == _norm(str(primary))


@pytest.mark.asyncio
async def test_remove_main_worktree_rejected(tmp_path):
    """删除 main worktree → cannot_remove_main(即使 force=true 也硬禁)。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    primary.mkdir()
    _init_git_repo(primary)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(primary), "force": True},
    )
    assert result["data"]["reason"] == "cannot_remove_main"


@pytest.mark.asyncio
async def test_remove_locked_worktree_rejected(tmp_path):
    """删除已 locked worktree → worktree_locked(force=true 不绕过)。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "lock", str(linked)],
        check=True,
        capture_output=True,
    )

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "force": True},
    )
    assert result["data"]["reason"] == "worktree_locked"


@pytest.mark.asyncio
async def test_remove_dirty_without_force(tmp_path):
    """force=false 且 dirty → worktree_dirty。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )
    # Make linked dirty
    (linked / "uncommitted.txt").write_text("uncommitted")

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked)},
    )
    assert result["data"]["reason"] == "worktree_dirty"


@pytest.mark.asyncio
async def test_remove_force_bypasses_dirty(tmp_path):
    """force=true 跳过 dirty 检查 → success。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )
    (linked / "uncommitted.txt").write_text("uncommitted")

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "force": True},
    )
    assert result["data"]["reason"] is None, result["data"]["stderr"]
    assert _norm(result["data"]["removed_path"]) == _norm(str(linked))


@pytest.mark.asyncio
async def test_remove_unknown_path(tmp_path):
    """path 不在 worktree list → worktree_not_found。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(primary / "nonexistent")},
    )
    assert result["data"]["reason"] == "worktree_not_found"


@pytest.mark.asyncio
async def test_remove_dotdot_path_rejected(tmp_path):
    """path 含 ``..`` 段 → path_unsafe(format check)。

    用真实 git repo 以让 preflight 通过,path validation 在 L3 拒绝。
    """
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": "/foo/../escape"},
    )
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_remove_missing_path_field(tmp_path):
    """body 缺 path 字段 → path_unsafe(format check fails on None)。

    用真实 git repo 以让 preflight 通过。
    """
    from tools.webapi.git_worktree_remove import handle as remove_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={},
    )
    assert result["data"]["reason"] == "path_unsafe"


# ── Preflight tests (4 tests) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_feature_disabled():
    """preflight step 1: 任一 feature flag = False → feature_disabled。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": False, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/tmp",
        "loaded_at": 0.0,
    }
    result = await remove_handle(
        plugin,
        umo="test:umo",
        worktree=None,
        body={"path": "/x"},
    )
    assert result["data"]["reason"] == "feature_disabled"


@pytest.mark.asyncio
async def test_remove_no_project_loaded():
    """preflight step 2: 未加载项目 → no_project_loaded。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = None
    result = await remove_handle(
        plugin,
        umo="nonexistent",
        worktree=None,
        body={"path": "/x"},
    )
    assert result["data"]["reason"] == "no_project_loaded"


@pytest.mark.asyncio
async def test_remove_not_a_git_repo(tmp_path):
    """preflight step 5: rev-parse 失败 + "not a git repository" stderr → not_a_git_repo。"""
    from tools.webapi.git_worktree_remove import handle as remove_handle

    # 用真实存在的目录(非 git repo)以通过 preflight step 4(directory exists)
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": str(non_repo),
        "loaded_at": 0.0,
    }

    with patch("tools.webapi._helpers._run_git_async") as mock_run:
        mock_run.return_value = {
            "ok": False,
            "stderr": "fatal: not a git repository",
            "stdout": "",
            "code": 128,
        }
        result = await remove_handle(
            plugin,
            umo="test:umo",
            worktree=None,
            body={"path": str(non_repo / "x")},
        )
        assert result["data"]["reason"] == "not_a_git_repo"


@pytest.mark.asyncio
async def test_remove_directory_missing():
    """preflight step 4: 目录不存在 → directory_missing。

    用真正不存在的路径(在 Windows 上 ``""`` 会被 Path 规整为 ``.``,会绕过 step 4)。
    """
    from tools.webapi.git_worktree_remove import handle as remove_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/nonexistent/__remove_test_xyz__",
        "loaded_at": 0.0,
    }
    result = await remove_handle(
        plugin,
        umo="test:umo",
        worktree=None,
        body={"path": "/x"},
    )
    assert result["data"]["reason"] == "directory_missing"
