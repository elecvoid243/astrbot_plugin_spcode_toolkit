"""Tests for POST /spcode/git-worktree-unlock endpoint.

PR-D (v2.14.0, 2026-06-26): UNLOCK endpoint with 5-layer defense chain.
Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.4

Layered defense:
  L1: body type guard (non-dict → invalid_body)
  L2: _git_endpoint_preflight (5-step)
  L3: _resolve_target_worktree (format + list lookup)
  L4: git worktree unlock <path> + stderr → not_locked
  L5: _list_worktrees_safe refresh on success
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
async def test_unlock_basic(tmp_path):
    """Unlock a previously locked worktree → success, locked=False."""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

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
    result = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert result["data"]["reason"] is None, result["data"]["stderr"]
    assert result["data"]["locked"] is False
    # Verify on disk
    out = subprocess.run(
        ["git", "-C", str(primary), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    )
    # After unlock, the linked worktree section should not contain 'locked'
    sections = out.stdout.split("\n\n")
    linked_section = next(
        s for s in sections if _norm(linked.as_posix()) in _norm(s)
    )
    assert "locked" not in linked_section


@pytest.mark.asyncio
async def test_unlock_not_locked_rejected(tmp_path):
    """Unlock an unlocked worktree → not_locked (git self-rejects)."""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )
    # NOT pre-locked

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert result["data"]["reason"] == "not_locked", result["data"]["stderr"]


@pytest.mark.asyncio
async def test_unlock_unknown_path(tmp_path):
    """path 不在 worktree list → worktree_not_found。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await unlock_handle(
        plugin,
        umo=umo,
        worktree=None,
        body={"path": str(primary / "nonexistent")},
    )
    assert result["data"]["reason"] == "worktree_not_found"


@pytest.mark.asyncio
async def test_unlock_path_unsafe(tmp_path):
    """path 含 ``..`` 段 → path_unsafe (format check at L3)."""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": "/foo/../escape"}
    )
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_unlock_main_worktree_passthrough(tmp_path):
    """Unlock main worktree → handler 无业务闸,git 自身拒绝(不可锁/解锁 main)。

    设计意图(handler §3.4):UNLOCK **没有** handler 层的 "cannot unlock main" 业务闸
    (与 LOCK 对称)。但 git 自己有 main worktree 不能 lock/unlock 的硬性约束,
    所以最终会返回 git_error。

    本测试验证:handler **不会**返回 cannot_remove_main 之类的业务 reason,
    而是直接交给 git 处理(返回 git_error)。
    """
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    primary = tmp_path / "primary"
    _init_git_repo(primary)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(primary)}
    )
    # git 实际行为:fatal: The main working tree cannot be locked or unlocked
    assert result["data"]["reason"] == "git_error", result["data"]["stderr"]
    assert "main working tree" in result["data"]["stderr"].lower()


@pytest.mark.asyncio
async def test_unlock_git_returns_not_locked(tmp_path):
    """集成层:git worktree unlock 未锁时报 'is not locked' → not_locked。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    _init_git_repo(primary)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
        check=True,
        capture_output=True,
    )

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert result["data"]["reason"] == "not_locked", result["data"]["stderr"]


@pytest.mark.asyncio
async def test_unlock_success_envelope_shape(tmp_path):
    """完整 envelope:loaded/directory/umo/worktree/locked=false/worktrees[]。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

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
    result = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(primary)
    assert data["umo"] == umo
    assert _norm(data["worktree"]) == _norm(str(linked))
    assert data["locked"] is False
    assert isinstance(data["worktrees"], list)
    assert len(data["worktrees"]) == 2
    linked_in_list = next(
        w for w in data["worktrees"] if _norm(w["path"]) == _norm(str(linked))
    )
    assert linked_in_list["locked"] is False


@pytest.mark.asyncio
async def test_unlock_idempotent(tmp_path):
    """二次 unlock 已解锁的 worktree → not_locked (git 自检,非 idempotent)。

    设计意图:UNLOCK 不设计为 idempotent,未锁调用 → not_locked。
    这与 LOCK 对称:二次 lock 已锁的 → already_locked。
    """
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

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
    # First unlock: success
    r1 = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert r1["data"]["reason"] is None
    # Second unlock: not_locked
    r2 = await unlock_handle(
        plugin, umo=umo, worktree=None, body={"path": str(linked)}
    )
    assert r2["data"]["reason"] == "not_locked"


# ── Preflight tests (6 tests) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_invalid_body():
    """L1: non-dict body → invalid_body。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/tmp",
        "loaded_at": 0.0,
    }
    result = await unlock_handle(
        plugin, umo="test:umo", worktree=None, body="not a dict"
    )
    assert result["data"]["reason"] == "invalid_body"


@pytest.mark.asyncio
async def test_unlock_feature_disabled():
    """preflight step 1: 任一 feature flag = False → feature_disabled。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": False, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/tmp",
        "loaded_at": 0.0,
    }
    result = await unlock_handle(
        plugin, umo="test:umo", worktree=None, body={"path": "/x"}
    )
    assert result["data"]["reason"] == "feature_disabled"


@pytest.mark.asyncio
async def test_unlock_no_project_loaded():
    """preflight step 2: 未加载项目 → no_project_loaded。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = None
    result = await unlock_handle(
        plugin, umo="nonexistent", worktree=None, body={"path": "/x"}
    )
    assert result["data"]["reason"] == "no_project_loaded"


@pytest.mark.asyncio
async def test_unlock_directory_missing():
    """preflight step 4: 目录不存在 → directory_missing。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": "/nonexistent/__unlock_test_xyz__",
        "loaded_at": 0.0,
    }
    result = await unlock_handle(
        plugin, umo="test:umo", worktree=None, body={"path": "/x"}
    )
    assert result["data"]["reason"] == "directory_missing"


@pytest.mark.asyncio
async def test_unlock_not_a_git_repo(tmp_path):
    """preflight step 5: rev-parse 失败 + "not a git repository" → not_a_git_repo。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

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
        result = await unlock_handle(
            plugin,
            umo="test:umo",
            worktree=None,
            body={"path": str(non_repo / "x")},
        )
        assert result["data"]["reason"] == "not_a_git_repo"


@pytest.mark.asyncio
async def test_unlock_git_unavailable(tmp_path):
    """preflight step 5: git 二进制不存在 → git_unavailable。"""
    from tools.webapi.git_worktree_unlock import handle as unlock_handle

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
        result = await unlock_handle(
            plugin,
            umo="test:umo",
            worktree=None,
            body={"path": str(non_repo / "x")},
        )
        assert result["data"]["reason"] == "git_unavailable"