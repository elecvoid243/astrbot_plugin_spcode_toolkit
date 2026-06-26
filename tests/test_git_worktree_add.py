"""Tests for POST /spcode/git-worktree-add endpoint.

PR-B (v2.14.0, 2026-06-26): ADD endpoint with 7-layer defense chain.
Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.1
"""
from __future__ import annotations

import os
import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest


# ── Task 2.1: _validate_add_cross_fields ─────────────────────────────



def test_cross_validate_default_ok():
    """默认 create=false / force=false / detach=false / base=None / branch="x" → ok。"""
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, None, "feature")
    assert err is None


def test_cross_validate_create_true_ok():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, False, False, None, "new-feat")
    assert err is None


def test_cross_validate_create_with_base_ok():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, False, False, "main", "new-feat")
    assert err is None


def test_cross_validate_force_true_ok():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, True, False, None, "existing")
    assert err is None


def test_cross_validate_detach_with_branch_ok():
    """detach + branch 视为 commit ref,合法。"""
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, True, None, "abc123")
    assert err is None


def test_cross_validate_create_and_force_both_true_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, True, False, None, "x")
    assert err is not None
    assert "create" in err.lower() and "force" in err.lower()


def test_cross_validate_detach_and_create_both_true_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, False, True, None, "x")
    assert err is not None


def test_cross_validate_detach_and_force_both_true_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, True, True, None, "x")
    assert err is not None


def test_cross_validate_base_without_create_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, "main", "x")
    assert err is not None


def test_cross_validate_missing_branch_when_not_detach_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, None, None)
    assert err is not None


def test_cross_validate_empty_branch_when_not_detach_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, None, "")
    assert err is not None


# ── Task 2.2: _build_git_worktree_add_args ───────────────────────────


def test_build_args_basic_checkout():
    """add <path> <branch> (create=False, detach=False) → ['add', path, branch]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "feat", False, False, False, None
    )
    assert args == ["add", "/target", "feat"]


def test_build_args_create_new_branch():
    """create=True → ['add', '-b', branch, path]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "new-feat", True, False, False, None
    )
    assert args == ["add", "-b", "new-feat", "/target"]


def test_build_args_create_with_base():
    """create=True + base → ['add', '-b', branch, path, base]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "new-feat", True, False, False, "main"
    )
    assert args == ["add", "-b", "new-feat", "/target", "main"]


def test_build_args_force_reset_existing():
    """force=True → ['add', '-B', branch, path]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "existing", False, True, False, None
    )
    assert args == ["add", "-B", "existing", "/target"]


def test_build_args_detached_at_head():
    """detach=True, branch=None → ['add', '--detach', path]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", None, False, False, True, None
    )
    assert args == ["add", "--detach", "/target"]


def test_build_args_detached_at_commit():
    """detach=True, branch=<sha> → ['add', '--detach', path, sha]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "abc1234", False, False, True, None
    )
    assert args == ["add", "--detach", "/target", "abc1234"]


# ── Task 2.3: _map_add_stderr_to_reason ──────────────────────────────


def test_stderr_branch_already_checked_out():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'feature' is already checked out at '/path'"
    assert _map_add_stderr_to_reason(stderr) == "cannot_create_existing"


def test_stderr_branch_already_exists():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'feature' already exists"
    assert _map_add_stderr_to_reason(stderr) == "cannot_create_existing"


def test_stderr_branch_not_valid_name():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'fea..ture' is not a valid branch name"
    assert _map_add_stderr_to_reason(stderr) == "invalid_branch"


def test_stderr_missing_branch_name():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'feature' is a missing branch name"
    assert _map_add_stderr_to_reason(stderr) == "cannot_checkout_missing"


def test_stderr_path_already_exists():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: '/target' already exists"
    assert _map_add_stderr_to_reason(stderr) == "path_exists_nonempty"


def test_stderr_invalid_worktree_name():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: '/foo:bar' cannot be used as a worktree name"
    assert _map_add_stderr_to_reason(stderr) == "invalid_param"


def test_stderr_invalid_start_point():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: invalid start point: badref"
    assert _map_add_stderr_to_reason(stderr) == "invalid_param"


def test_stderr_unknown_returns_git_error():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: unknown error XYZ"
    assert _map_add_stderr_to_reason(stderr) == "git_error"


def test_stderr_empty_returns_git_error():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    assert _map_add_stderr_to_reason("") == "git_error"
