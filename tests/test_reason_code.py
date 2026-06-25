"""Tests for ReasonCode class and _make_envelope factory in tools/webapi/_helpers.py.

PR-1 of the git workflow endpoints design.
Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §3,§4
"""
from tools.webapi._helpers import ReasonCode, _make_envelope


def test_reason_code_has_all_general_codes():
    """Common preflight failure reasons — used by all 10 endpoints eventually."""
    assert ReasonCode.FEATURE_DISABLED == "feature_disabled"
    assert ReasonCode.NO_PROJECT_LOADED == "no_project_loaded"
    assert ReasonCode.WORKTREE_INVALID == "worktree_invalid"
    assert ReasonCode.DIRECTORY_MISSING == "directory_missing"
    assert ReasonCode.NOT_A_GIT_REPO == "not_a_git_repo"
    assert ReasonCode.GIT_UNAVAILABLE == "git_unavailable"
    assert ReasonCode.GIT_ERROR == "git_error"


def test_reason_code_has_body_param_codes():
    """Body / param validation reasons — used by POST endpoints."""
    assert ReasonCode.INVALID_BODY == "invalid_body"
    assert ReasonCode.INVALID_FILES == "invalid_files"
    assert ReasonCode.INVALID_ALL == "invalid_all"
    assert ReasonCode.EMPTY_MESSAGE == "empty_message"
    assert ReasonCode.MESSAGE_TOO_LONG == "message_too_long"
    assert ReasonCode.INVALID_PARAM == "invalid_param"


def test_reason_code_has_path_scope_codes():
    """File path / scope reasons — used by write endpoints + log path filter."""
    assert ReasonCode.PATH_UNSAFE == "path_unsafe"
    assert ReasonCode.NOTHING_STAGED == "nothing_staged"
    assert ReasonCode.PRE_COMMIT_HOOK_FAILED == "pre_commit_hook_failed"
    assert ReasonCode.EMPTY_REPOSITORY == "empty_repository"


# ──────────────────────────────────────────────────────────────────────
# _make_envelope factory tests
# ──────────────────────────────────────────────────────────────────────


def test_make_envelope_success_shape():
    """success=True 时 data 含 reason=None / stderr='',其它字段透传。"""
    env = _make_envelope(
        success=True, elapsed_ms=42,
        staged=True, files=["a.py"], staged_count=1,
    )
    assert env == {
        "status": "ok",
        "data": {
            "staged": True,
            "files": ["a.py"],
            "staged_count": 1,
            "reason": None,
            "stderr": "",
            "elapsed_ms": 42,
        },
    }


def test_make_envelope_failure_shape():
    """success=False 时 data 含 reason + (可选)stderr。"""
    env = _make_envelope(
        success=False, reason="invalid_body",
        elapsed_ms=5, staged=False,
    )
    assert env["status"] == "ok"
    assert env["data"]["staged"] is False
    assert env["data"]["reason"] == "invalid_body"
    assert env["data"]["stderr"] == ""
    assert env["data"]["elapsed_ms"] == 5


def test_make_envelope_failure_with_stderr():
    """failure 路径可传 stderr(写端点 hook 失败用)。"""
    env = _make_envelope(
        success=False, reason="pre_commit_hook_failed",
        stderr="pre-commit hook failed", elapsed_ms=100,
    )
    assert env["data"]["stderr"] == "pre-commit hook failed"


def test_make_envelope_extra_kwargs_pass_through():
    """data 字段 = 透传 kwargs(写端点必传 directory/umo/worktree)。"""
    env = _make_envelope(
        success=True, elapsed_ms=10,
        directory="/repo", umo="u:m", worktree="/repo",
    )
    data = env["data"]
    assert data["directory"] == "/repo"
    assert data["umo"] == "u:m"
    assert data["worktree"] == "/repo"
    assert data["reason"] is None
