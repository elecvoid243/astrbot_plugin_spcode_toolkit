"""Tests for tools._helpers._parse_git_worktree_porcelain locked extension.

Uses subset assertions (not `==`) so they remain backward-compat with the
existing strict-dict test in tests/test_helpers_git.py::test_parse_single_main_worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §4.1
"""

from tools._helpers import _parse_git_worktree_porcelain


def test_parse_unlocked_worktree():
    """普通 worktree 没有 locked 行 → locked=False, locked_reason=None。"""
    text = "worktree /path/to/main\nHEAD abc123\nbranch refs/heads/main\n"
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 1
    assert result[0]["locked"] is False
    assert result[0]["locked_reason"] is None
    # branch prefix stripping preserved (regression guard)
    assert result[0]["branch"] == "main"


def test_parse_locked_worktree_no_reason():
    """仅有 `locked` 行 → locked=True, locked_reason=None。"""
    text = "worktree /path/to/feature\nHEAD def456\nbranch refs/heads/feature\nlocked\n"
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 1
    assert result[0]["locked"] is True
    assert result[0]["locked_reason"] is None


def test_parse_locked_worktree_with_reason():
    """`locked <reason>` 单行 → locked=True, locked_reason=<text>。"""
    text = (
        "worktree /path/to/feature\n"
        "HEAD def456\n"
        "branch refs/heads/feature\n"
        "locked in use by background agent\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert result[0]["locked"] is True
    assert result[0]["locked_reason"] == "in use by background agent"


def test_parse_locked_with_multiline_reason():
    """git 2.30+ 多行 reason:locked 行后跟额外 reason 行。

    实际 git 行为:`locked <reason-text>` 单行优先,但某些 git 版本
    在 reason 含特殊字符时会把 reason 放到下一行(空行分隔前先写 reason)。

    为防御性,我们接受:locked 行后**紧跟**的非空且非"worktree "/"HEAD "/"branch "/"locked "
    开头的行视为 multiline reason 续行。
    """
    text = (
        "worktree /path/to/feature\n"
        "HEAD def456\n"
        "branch refs/heads/feature\n"
        "locked\n"
        "reason line one\n"
        "reason line two\n"
        "\n"
        "worktree /path/to/main\n"
        "HEAD abc\n"
        "branch refs/heads/main\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 2
    assert result[0]["locked"] is True
    # multiline reason concatenated with newlines preserved
    assert "reason line one" in result[0]["locked_reason"]
    assert "reason line two" in result[0]["locked_reason"]


def test_parse_multiple_worktrees_mixed_lock_state():
    """多个 worktree 混合 locked/unlocked → 各自正确标记。"""
    text = (
        "worktree /path/to/main\n"
        "HEAD abc\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /path/to/feat1\n"
        "HEAD def\n"
        "branch refs/heads/feat1\n"
        "locked\n"
        "\n"
        "worktree /path/to/feat2\n"
        "HEAD ghi\n"
        "branch refs/heads/feat2\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 3
    assert result[0]["locked"] is False
    assert result[1]["locked"] is True
    assert result[2]["locked"] is False


def test_parse_main_worktree_locked_defaults_false():
    """main worktree 默认 locked=False(除非有 locked 行)。"""
    text = "worktree /path/to/main\nHEAD abc\nbranch refs/heads/main\n"
    result = _parse_git_worktree_porcelain(text)
    assert result[0]["is_main"] is True
    assert result[0]["locked"] is False
