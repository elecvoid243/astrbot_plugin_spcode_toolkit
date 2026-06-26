"""Tests for tools._helpers worktree-specific helpers.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §4.2
"""
from tools._helpers import _is_valid_ref_name


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