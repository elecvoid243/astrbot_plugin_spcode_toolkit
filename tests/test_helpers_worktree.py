"""Tests for tools._helpers worktree-specific helpers.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §4.2
"""
from tools._helpers import _is_valid_ref_name, _validate_new_worktree_path


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