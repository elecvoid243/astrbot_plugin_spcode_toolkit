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
