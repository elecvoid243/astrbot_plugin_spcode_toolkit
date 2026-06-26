"""Smoke test for webapi/git_worktrees.handle.

Handler 从 main.py 搬出,行为不变。

v2.14.0 扩展:返回数据应附加 ``locked`` / ``locked_reason`` 字段(纯增量)。
"""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest
from tools.project import state as _proj_state
from tools.webapi import git_worktrees

# Advisory-5 fix (2026-06-26): 不在模块级挂 asyncio mark — 否则同步 test 也会被
# 标记,在 strict 模式下触发 PytestWarning/失败。按函数装饰 async test 即可。


def test_make_git_worktrees_empty_envelope_has_required_fields():
    """空 envelope 必须含 loaded=False + reason 字段。"""
    env = git_worktrees._make_git_worktrees_empty_envelope(
        umo="test-umo", reason="no_project_loaded", elapsed_ms=5
    )
    assert env["status"] == "ok"
    assert env["data"]["loaded"] is False
    assert env["data"]["umo"] == "test-umo"
    assert env["data"]["reason"] == "no_project_loaded"
    assert env["data"]["worktrees"] is None
    assert env["data"]["elapsed_ms"] == 5


@pytest.mark.asyncio
async def test_handle_returns_no_project_loaded_envelope():
    """空 _loaded_projects 时,返回 no_project_loaded envelope。"""
    plugin = MagicMock()
    plugin.get_loaded_project.side_effect = lambda umo: _proj_state.get(umo)
    _proj_state.reset()
    result = await git_worktrees.handle(plugin)
    assert result["status"] == "ok"
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "no_project_loaded"
    assert result["data"]["worktrees"] is None


# ─── v2.14.0 locked/locked_reason 字段测试 (Task 1.6) ──────────────────


@pytest.fixture
def locked_worktree_repo(tmp_path):
    """Create a primary repo with one linked worktree (未 lock,用于后续 lock)。"""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)
    return primary, linked


def _make_plugin_mock(directory):
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": directory, "loaded_at": 0.0}
    return plugin


@pytest.mark.asyncio
async def test_git_worktrees_response_includes_locked_field(locked_worktree_repo):
    """GET 端点返回数据应附加 locked 字段(pure additive)。"""
    primary, linked = locked_worktree_repo
    subprocess.run(["git", "-C", str(primary), "worktree", "lock", str(linked)],
                   check=True, capture_output=True)
    plugin = _make_plugin_mock(str(primary))
    # Put project into state for handler fallback path
    _proj_state.reset()
    _proj_state.put("test-umo:1", {"directory": str(primary), "loaded_at": 100.0})
    # 使用项目标准的 run_cmd(参考现有 test_git_worktrees.py 用法)
    from unittest.mock import patch
    with patch("tools.webapi.git_worktrees.run_cmd") as mock_run:
        # 模拟 git worktree list --porcelain 输出(2 个 worktree,linked 已 lock)
        mock_run.return_value = {
            "ok": True,
            "stdout": (
                f"worktree {primary}\n"
                "HEAD abc1234\n"
                "branch refs/heads/main\n"
                "\n"
                f"worktree {linked}\n"
                "HEAD def5678\n"
                "branch refs/heads/feat\n"
                "locked\n"
            ),
            "stderr": "",
            "code": 0,
        }
        result = await git_worktrees.handle(plugin)
    _proj_state.reset()
    assert result["data"]["loaded"] is True
    worktrees = result["data"]["worktrees"]
    assert len(worktrees) == 2
    main_wt = next(w for w in worktrees if w["is_main"])
    linked_wt = next(w for w in worktrees if not w["is_main"])
    assert main_wt["locked"] is False
    assert main_wt["locked_reason"] is None
    assert linked_wt["locked"] is True
    assert linked_wt["locked_reason"] is None  # bare `locked` line → no reason


@pytest.mark.asyncio
async def test_git_worktrees_response_includes_locked_reason(locked_worktree_repo):
    """Locked with --reason → locked_reason 字段传递。"""
    primary, linked = locked_worktree_repo
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "lock", "--reason", "test reason", str(linked)],
        check=True, capture_output=True,
    )
    plugin = _make_plugin_mock(str(primary))
    _proj_state.reset()
    _proj_state.put("test-umo:2", {"directory": str(primary), "loaded_at": 100.0})
    from unittest.mock import patch
    with patch("tools.webapi.git_worktrees.run_cmd") as mock_run:
        mock_run.return_value = {
            "ok": True,
            "stdout": (
                f"worktree {primary}\n"
                "HEAD abc1234\n"
                "branch refs/heads/main\n"
                "\n"
                f"worktree {linked}\n"
                "HEAD def5678\n"
                "branch refs/heads/feat\n"
                "locked test reason\n"
            ),
            "stderr": "",
            "code": 0,
        }
        result = await git_worktrees.handle(plugin)
    _proj_state.reset()
    worktrees = result["data"]["worktrees"]
    assert worktrees is not None
    linked_wt = next(w for w in worktrees if not w["is_main"])
    assert linked_wt["locked"] is True
    assert linked_wt["locked_reason"] == "test reason"


@pytest.mark.asyncio
async def test_git_worktrees_unlocked_default_false(locked_worktree_repo):
    """Unlocked worktree → locked=False(与既有 v1 行为一致,无破坏)。"""
    primary, _ = locked_worktree_repo
    # linked 未 lock
    plugin = _make_plugin_mock(str(primary))
    _proj_state.reset()
    _proj_state.put("test-umo:3", {"directory": str(primary), "loaded_at": 100.0})
    from unittest.mock import patch
    with patch("tools.webapi.git_worktrees.run_cmd") as mock_run:
        mock_run.return_value = {
            "ok": True,
            "stdout": (
                f"worktree {primary}\n"
                "HEAD abc1234\n"
                "branch refs/heads/main\n"
            ),
            "stderr": "",
            "code": 0,
        }
        result = await git_worktrees.handle(plugin)
    _proj_state.reset()
    worktrees = result["data"]["worktrees"]
    assert worktrees is not None
    assert len(worktrees) == 1
    assert worktrees[0]["locked"] is False
    assert worktrees[0]["locked_reason"] is None
