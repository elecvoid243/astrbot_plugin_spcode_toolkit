"""Tests for tools._helpers worktree-specific helpers.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §4.2
"""
import os
import subprocess

import pytest

from tools._helpers import (
    _is_valid_ref_name,
    _list_worktrees_safe,
    _resolve_target_worktree,
    _validate_new_worktree_path,
)


# ─── _is_valid_ref_name tests (Task 1.3) ────────────────────────────────


def test_valid_simple_branch():
    assert _is_valid_ref_name("feature") is True


def test_valid_branch_with_slash():
    assert _is_valid_ref_name("feature/user-auth") is True


def test_valid_commit_sha():
    assert _is_valid_ref_name("abc1234") is True
    assert _is_valid_ref_name("abc1234567890abcdef1234567890abcdef123456") is True


def test_valid_HEAD_shorthand():
    assert _is_valid_ref_name("HEAD~3") is True
    assert _is_valid_ref_name("HEAD^") is True


def test_valid_tag():
    assert _is_valid_ref_name("v1.0.0") is True


def test_invalid_empty_string():
    assert _is_valid_ref_name("") is False


def test_invalid_double_dot():
    assert _is_valid_ref_name("feature..x") is False


def test_invalid_starts_with_dash():
    assert _is_valid_ref_name("-feature") is False


def test_invalid_contains_space():
    assert _is_valid_ref_name("feature x") is False


def test_invalid_contains_tilde():
    assert _is_valid_ref_name("feature~x") is False


def test_invalid_contains_caret():
    assert _is_valid_ref_name("feature^x") is False


def test_invalid_contains_colon():
    assert _is_valid_ref_name("feature:x") is False


def test_invalid_contains_question():
    assert _is_valid_ref_name("feature?x") is False


def test_invalid_contains_asterisk():
    assert _is_valid_ref_name("feature*x") is False


def test_invalid_contains_open_bracket():
    assert _is_valid_ref_name("feature[x") is False


def test_too_long_branch():
    assert _is_valid_ref_name("a" * 1025) is False  # > 1024 limit


# ─── _validate_new_worktree_path tests (Task 1.4) ───────────────────────


def test_validate_new_path_accepts_absolute_posix(tmp_path):
    target = str(tmp_path / "feature")
    ok, err = _validate_new_worktree_path(target)
    assert ok == target
    assert err is None


def test_validate_new_path_accepts_absolute_windows_style(tmp_path):
    """Windows 风格绝对路径同样接受(无 `\\` 段时)。"""
    target = str(tmp_path / "feature")
    ok, err = _validate_new_worktree_path(target)
    assert err is None
    assert ok is not None


def test_validate_new_path_rejects_dotdot(tmp_path):
    target = str(tmp_path / ".." / "escape")
    ok, err = _validate_new_worktree_path(target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_relative(tmp_path):
    target = "./feature"
    ok, err = _validate_new_worktree_path(target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_dot_git_component(tmp_path):
    target = str(tmp_path / ".git" / "feature")
    ok, err = _validate_new_worktree_path(target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_empty():
    ok, err = _validate_new_worktree_path("")
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_too_long(tmp_path):
    target = str(tmp_path / ("a" * 5000))
    ok, err = _validate_new_worktree_path(target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_missing_parent(tmp_path):
    target = str(tmp_path / "nonexistent_dir" / "feature")
    ok, err = _validate_new_worktree_path(target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_backslash():
    ok, err = _validate_new_worktree_path("C:\\Users\\foo\\feature")
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_none_input():
    ok, err = _validate_new_worktree_path(None)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_blacklisted(monkeypatch, tmp_path):
    """防御 4: 黑名单路径(如 C:\\Windows 等)被拒绝。

    通过 monkeypatch 设置 file_remove_blacklist 配置项,验证 helper 拒绝该路径。
    """
    from tools import _helpers
    monkeypatch.setattr(_helpers, "_FILE_REMOVE_BLACKLIST", [str(tmp_path)])
    target = str(tmp_path / "feature")
    ok, err = _validate_new_worktree_path(target)
    assert ok is None
    assert err == "path_unsafe"


# ─── _resolve_target_worktree tests (Task 1.5) ─────────────────────────


def _make_test_repo_with_two_worktrees(tmp_path):
    """Helper: create primary + linked worktree, return (primary, linked).

    无 monkeypatch 参数 — 测试不需要 mock,使用真实 git 命令(tmp_path 自动清理)。
    """
    primary = tmp_path / "primary"
    primary.mkdir()
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "a.txt"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)
    return primary, linked


def test_resolve_target_finds_existing(tmp_path):
    primary, linked = _make_test_repo_with_two_worktrees(tmp_path)
    target_wt, err = _resolve_target_worktree("git", str(primary), str(linked))
    assert err is None
    assert target_wt is not None
    # Windows / POSIX 大小写不敏感比较
    assert os.path.normcase(target_wt["path"]) == os.path.normcase(str(linked))
    assert target_wt["is_main"] is False


def test_resolve_target_unknown_returns_not_found(tmp_path):
    primary, _ = _make_test_repo_with_two_worktrees(tmp_path)
    target_wt, err = _resolve_target_worktree(
        "git", str(primary), str(primary / "does_not_exist")
    )
    assert target_wt is None
    assert err == "worktree_not_found"


def test_resolve_target_dotdot_returns_unsafe():
    target_wt, err = _resolve_target_worktree("git", "/tmp", "/foo/../escape")
    assert target_wt is None
    assert err == "path_unsafe"


def test_resolve_target_empty_path():
    target_wt, err = _resolve_target_worktree("git", "/tmp", "")
    assert target_wt is None
    assert err == "path_unsafe"


def test_resolve_target_relative_path():
    target_wt, err = _resolve_target_worktree("git", "/tmp", "relative/path")
    assert target_wt is None
    assert err == "path_unsafe"


# ─── _list_worktrees_safe tests (Task 1.9) ─────────────────────────────


def _make_primary_with_two_worktrees(tmp_path):
    """Helper: primary + 2 linked worktrees, return primary dir path."""
    primary = tmp_path / "primary"
    linked1 = tmp_path / "linked1"
    linked2 = tmp_path / "linked2"
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
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked1), "-b", "f1"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked2), "-b", "f2"],
                   check=True, capture_output=True)
    return primary


@pytest.mark.asyncio
async def test_list_worktrees_safe_returns_all(tmp_path):
    """列出 primary + 2 linked → 返回 3 个 worktree dict。"""
    primary = _make_primary_with_two_worktrees(tmp_path)
    result = await _list_worktrees_safe("git", str(primary))
    assert len(result) == 3
    assert result[0]["is_main"] is True
    assert not result[1]["is_main"]
    assert not result[2]["is_main"]


@pytest.mark.asyncio
async def test_list_worktrees_safe_includes_locked_field(tmp_path):
    """list 输出包含 locked 字段(Task 1.1 扩展后)。"""
    primary = _make_primary_with_two_worktrees(tmp_path)
    # Lock first linked
    subprocess.run(["git", "-C", str(primary), "worktree", "lock", str(tmp_path / "linked1")],
                   check=True, capture_output=True)
    result = await _list_worktrees_safe("git", str(primary))
    assert result[0]["locked"] is False
    assert result[1]["locked"] is True
    assert result[2]["locked"] is False


@pytest.mark.asyncio
async def test_list_worktrees_safe_invalid_dir_returns_empty():
    """primary 不是 git repo → 返回空 list(不抛异常)。"""
    result = await _list_worktrees_safe("git", "/nonexistent/path")
    assert result == []


@pytest.mark.asyncio
async def test_list_worktrees_safe_git_unavailable(tmp_path):
    """git binary 缺失 → 返回空 list(不抛 FileNotFoundError)。"""
    primary = tmp_path / "primary"
    primary.mkdir()
    # 用不存在的 binary 触发 FileNotFoundError
    result = await _list_worktrees_safe("/nonexistent/git-binary", str(primary))
    assert result == []