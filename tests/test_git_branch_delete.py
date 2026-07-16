"""Tests for POST /spcode/git-branch-delete (v2.17.0).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.4
Author: elecvoid243 @ 2026-07-16
"""

import asyncio
import subprocess

import pytest

from tests.conftest import _make_plugin  # noqa: F401
from tools.project import state as _state
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_branch_delete


def _run(coro):
    """Sync wrapper for async handler calls."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── preflight (3 cases: no_umo / not_git_repo / cross worktree) ──

def test_delete_no_umo_loaded():
    plugin = _make_plugin()
    result = _run(git_branch_delete.handle(plugin, body={"name": "x"}))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


def test_delete_not_a_git_repo(tmp_path):
    target = tmp_path / "notgit"
    target.mkdir()
    umo = "test:del:nogit"
    _state.put(umo, {"directory": str(target), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(
            git_branch_delete.handle(plugin, umo=umo, body={"name": "x"})
        )
        assert result["data"]["reason"] == ReasonCode.NOT_A_GIT_REPO
    finally:
        _state.pop(umo)


def test_delete_worktree_invalid(tmp_path, existing_repo):
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    umo = "test:del:cross"
    _state.put(umo, {"directory": str(existing_repo), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(
            git_branch_delete.handle(
                plugin, umo=umo, worktree=str(other), body={"name": "x"},
            )
        )
        assert result["data"]["reason"] == ReasonCode.WORKTREE_INVALID
    finally:
        _state.pop(umo)


# ── body 校验 (3 cases) ──

def test_delete_body_none(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(plugin, umo=loaded_umo, body=None)
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_delete_name_missing(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(plugin, umo=loaded_umo, body={})
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_delete_name_invalid_chars(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "bad..name"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_BRANCH


# ── 业务 reason (5 cases) ──

def test_delete_current_branch_force_false(loaded_umo, existing_repo):
    """删 current branch + force=false → branch_is_current."""
    plugin = _make_plugin()
    # existing_repo 初始化时 current=main
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "main", "force": False},
        )
    )
    assert result["data"]["reason"] == ReasonCode.BRANCH_IS_CURRENT


def test_delete_current_branch_force_true_still_blocked(loaded_umo, existing_repo):
    """删 current branch + force=true 仍 branch_is_current(硬禁, 不绕过)。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "main", "force": True},
        )
    )
    # 硬禁:即便 force=true,current branch 也不能删
    assert result["data"]["reason"] == ReasonCode.BRANCH_IS_CURRENT


def test_delete_main_when_main_is_current(loaded_umo, existing_repo):
    """删 main (current=main) → branch_is_current。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "main"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.BRANCH_IS_CURRENT


def test_delete_branch_not_found(loaded_umo, existing_repo):
    """branch 不存在 → branch_not_found。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "nonexistent-branch"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.BRANCH_NOT_FOUND


def test_delete_unmerged_branch_force_false(loaded_umo, existing_repo):
    """force=false 删未合并 → branch_not_merged。

    existing_repo 里的 feature/x 分支与 main 都指向同一 commit,
    实际上 git 会判定为 "already merged",这条测试要构造 truly-unmerged 状态。
    """
    # 新建一个未合并分支(没有追溯到 main)
    subprocess.run(
        ["git", "-C", str(existing_repo), "checkout", "-q", "-b", "truly-unmerged"],
        check=True,
    )
    (existing_repo / "new.txt").write_text("not merged back")
    subprocess.run(["git", "-C", str(existing_repo), "add", "new.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(existing_repo), "commit", "-q", "-m", "feat: unmerged"],
        check=True,
    )
    # 切回 main(让 main current)
    subprocess.run(
        ["git", "-C", str(existing_repo), "checkout", "-q", "main"], check=True
    )
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "truly-unmerged"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.BRANCH_NOT_MERGED


def test_delete_remote_tracking_branch(loaded_umo, existing_repo):
    """删 origin/main (remote tracking) → git_error。

    spec §3.4 不支持删 remote branch,git 自己也会拒绝。
    """
    # 模拟一个 remote tracking 分支
    subprocess.run(
        [
            "git", "-C", str(existing_repo),
            "update-ref", "refs/remotes/origin/main",
            subprocess.run(
                ["git", "-C", str(existing_repo), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
        ],
        check=True,
    )
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "origin/main"},
        )
    )
    # git 会拒绝(remote tracking 不能直接 -d)
    assert result["data"]["reason"] in (
        ReasonCode.GIT_ERROR,
        ReasonCode.BRANCH_NOT_FOUND,
    )


# ── happy path (2 cases) ──

def test_delete_merged_branch(loaded_umo, existing_repo):
    """删已合并 feature/x → deleted=True。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo, body={"name": "feature/x"},
        )
    )
    assert result["data"]["deleted"] is True
    assert result["data"]["was_current"] is False


def test_delete_unmerged_with_force(loaded_umo, existing_repo):
    """force=true 删未合并 → deleted=True。"""
    # 先创建未合并分支
    subprocess.run(
        ["git", "-C", str(existing_repo), "checkout", "-q", "-b", "will-force"],
        check=True,
    )
    (existing_repo / "force.txt").write_text("force delete")
    subprocess.run(["git", "-C", str(existing_repo), "add", "force.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(existing_repo), "commit", "-q", "-m", "wip"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(existing_repo), "checkout", "-q", "main"], check=True
    )
    plugin = _make_plugin()
    result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo,
            body={"name": "will-force", "force": True},
        )
    )
    assert result["data"]["deleted"] is True
    assert result["data"]["force"] is True


# ── 集成 (3 cases) ──

def test_delete_then_not_in_branches(loaded_umo, existing_repo):
    """删后 git-branches 不再返回。"""
    from tools.webapi import git_branches

    plugin = _make_plugin()
    # 删除 feature/x
    delete_result = _run(
        git_branch_delete.handle(
            plugin, umo=loaded_umo,
            body={"name": "feature/x", "force": True},
        )
    )
    assert delete_result["data"]["deleted"] is True
    # 再查 branches
    branch_result = _run(
        git_branches.handle(plugin, umo=loaded_umo)
    )
    names = [b["name"] for b in branch_result["data"]["branches"]]
    assert "feature/x" not in names
    assert "main" in names


def test_delete_branch_checked_out_other_worktree_fails(loaded_umo, existing_repo):
    """删正在其他 worktree checkout 的分支 → git_error。

    通过 ``git worktree add`` 创建第二个 worktree 检出目标分支,然后尝试删除。
    """
    # 创建第二 worktree,checkout 待删的 branch
    other_dir = existing_repo.parent / "other_worktree"
    if other_dir.exists():
        # cleanup before
        subprocess.run(
            ["git", "-C", str(existing_repo), "worktree", "remove", "--force",
             str(other_dir)],
            check=False,
        )
    try:
        subprocess.run(
            [
                "git", "-C", str(existing_repo), "worktree", "add",
                str(other_dir), "feature/x",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        # 如果 worktree add 失败(例如 existed 残留),跳过测试
        pytest.skip("无法创建 secondary worktree")
    try:
        plugin = _make_plugin()
        result = _run(
            git_branch_delete.handle(
                plugin, umo=loaded_umo, body={"name": "feature/x"},
            )
        )
        # git 在分支被其他 worktree checked out 时会拒绝(用 -d 不是 -D)
        # 这里能 fallthrough 到 git_error 也是合规的(branch 仍存活)
        assert result["data"]["reason"] in (
            ReasonCode.GIT_ERROR,
            ReasonCode.BRANCH_NOT_MERGED,
        )
    finally:
        # 清理
        subprocess.run(
            ["git", "-C", str(existing_repo), "worktree", "remove", "--force",
             str(other_dir)],
            check=False,
        )
