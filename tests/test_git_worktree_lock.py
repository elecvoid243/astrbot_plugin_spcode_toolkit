"""Tests for POST /spcode/git-worktree-lock endpoint.

PR-D (v2.14.0, 2026-06-26): LOCK endpoint with 6-layer defense chain.
Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.3

Layered defense:
  L1: body type guard (non-dict → invalid_body)
  L2: _git_endpoint_preflight (5-step)
  L3: _resolve_target_worktree (format + list lookup)
  L4: already_locked (target is_main / locked state)
  L5: git worktree lock [--reason] <path>
  L6: _list_worktrees_safe refresh on success
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


def _norm(p):
    """Normalize a path for cross-OS comparison."""
    return os.path.normpath(p)


# ── handle() — business path tests (8 tests) ───────────────────────────


@pytest.mark.asyncio
async def test_lock_basic(tmp_path):
    """Lock a linked worktree (no reason) → success, locked=True."""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await lock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert result["data"]["reason"] is None, result["data"]["stderr"]
    assert result["data"]["locked"] is True
    assert result["data"]["lock_reason"] is None
    # Verify on disk
    out = subprocess.run(
        ["git", "-C", str(primary), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    )
    assert "locked" in out.stdout


@pytest.mark.asyncio
async def test_lock_with_reason(tmp_path):
    """Lock with --reason → success, locked=True, lock_reason=<text>."""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await lock_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "reason": "test reason"},
    )
    assert result["data"]["reason"] is None, result["data"]["stderr"]
    assert result["data"]["locked"] is True
    assert result["data"]["lock_reason"] == "test reason"


@pytest.mark.asyncio
async def test_lock_already_locked_rejected(tmp_path):
    """Lock already-locked worktree → already_locked (no business bypass)."""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )
    # Pre-lock
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "lock", str(linked)],
        check=True,
        capture_output=True,
    )

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await lock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert result["data"]["reason"] == "already_locked"


@pytest.mark.asyncio
async def test_lock_unknown_path(tmp_path):
    """path 不在 worktree list → worktree_not_found。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await lock_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(primary / "nonexistent")},
    )
    assert result["data"]["reason"] == "worktree_not_found"


@pytest.mark.asyncio
async def test_lock_path_unsafe(tmp_path):
    """path 含 ``..`` 段 → path_unsafe (format check at L3)."""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await lock_handle(
        plugin, umo=umo, worktree=None, body={"path": "/foo/../escape"}
    )
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_lock_main_worktree_passthrough(tmp_path):
    """Lock main worktree → handler 无业务闸,git 自身拒绝(不可锁/解锁 main)。

    设计意图(handler §3.3):LOCK **没有** handler 层的 "cannot lock main" 业务闸
    (与 REMOVE 不同)。但 git 自己有 main worktree 不能 lock/unlock 的硬性约束,
    所以最终会返回 git_error。

    本测试验证:handler **不会**返回 cannot_remove_main 之类的业务 reason,
    而是直接交给 git 处理(返回 git_error)。
    """
    from tools.webapi.git_worktree_lock import handle as lock_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await lock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(primary)}
    )
    # git 实际行为:fatal: The main working tree cannot be locked or unlocked
    # 不应是 already_locked / worktree_not_found(那些是其它闸),应是 git_error
    assert result["data"]["reason"] == "git_error", result["data"]["stderr"]
    assert "main working tree" in result["data"]["stderr"].lower()


@pytest.mark.asyncio
async def test_lock_git_returns_already_locked(tmp_path):
    """集成层:git worktree lock 已锁时报 'already locked' → already_locked。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

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
    result = await lock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert result["data"]["reason"] == "already_locked"


@pytest.mark.asyncio
async def test_lock_success_envelope_shape(tmp_path):
    """完整 envelope:loaded/directory/umo/worktree/locked/lock_reason/worktrees[]。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await lock_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(linked), "reason": "integration test"},
    )
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(primary)
    assert data["umo"] == umo
    # git porcelain uses forward slashes on Windows; normalize for cross-OS compare
    assert _norm(data["worktree"]) == _norm(str(linked))
    assert data["locked"] is True
    assert data["lock_reason"] == "integration test"
    assert isinstance(data["worktrees"], list)
    assert len(data["worktrees"]) == 2
    linked_in_list = next(
        w for w in data["worktrees"] if _norm(w["path"]) == _norm(str(linked))
    )
    assert linked_in_list["locked"] is True


# ── Preflight tests (6 tests) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_invalid_body():
    """L1: non-dict body → invalid_body。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/tmp",
        "loaded_at": 0.0,
    }
    result = await lock_handle(
        plugin, umo="test:umo", worktree=None, body="not a dict"
    )
    assert result["data"]["reason"] == "invalid_body"


@pytest.mark.asyncio
async def test_lock_feature_disabled():
    """preflight step 1: 任一 feature flag = False → feature_disabled。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": False, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/tmp",
        "loaded_at": 0.0,
    }
    result = await lock_handle(
        plugin, umo="test:umo", worktree=None, body={"path": "/x"}
    )
    assert result["data"]["reason"] == "feature_disabled"


@pytest.mark.asyncio
async def test_lock_no_project_loaded():
    """preflight step 2: 未加载项目 → no_project_loaded。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = None
    result = await lock_handle(
        plugin, umo="nonexistent", worktree=None, body={"path": "/x"}
    )
    assert result["data"]["reason"] == "no_project_loaded"


@pytest.mark.asyncio
async def test_lock_directory_missing():
    """preflight step 4: 目录不存在 → directory_missing。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/nonexistent/__lock_test_xyz__",
        "loaded_at": 0.0,
    }
    result = await lock_handle(
        plugin, umo="test:umo", worktree=None, body={"path": "/x"}
    )
    assert result["data"]["reason"] == "directory_missing"


@pytest.mark.asyncio
async def test_lock_not_a_git_repo(tmp_path):
    """preflight step 5: rev-parse 失败 + "not a git repository" → not_a_git_repo。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

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
        result = await lock_handle(
            plugin,
            umo="test:umo",
            worktree=None,
            body={"path": str(non_repo / "x")},
        )
        assert result["data"]["reason"] == "not_a_git_repo"


@pytest.mark.asyncio
async def test_lock_git_unavailable(tmp_path):
    """preflight step 5: git 二进制不存在 → git_unavailable。"""
    from tools.webapi.git_worktree_lock import handle as lock_handle

    non_repo = tmp_path / "non-git-bin"
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
            "error": "git 未安装或不在 PATH 中",
            "stderr": "",
            "stdout": "",
            "code": -1,
        }
        result = await lock_handle(
            plugin,
            umo="test:umo",
            worktree=None,
            body={"path": str(non_repo / "x")},
        )
        assert result["data"]["reason"] == "git_unavailable"