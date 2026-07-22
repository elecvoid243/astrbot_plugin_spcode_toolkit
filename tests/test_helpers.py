"""Tests for tools/webapi/_helpers.py module-level helpers.

PR-of-file-discard-hunk: 9 new patch-related reason codes + shared
X_TRULY_STAGED / Y_WORKTREE frozensets extracted from file_restore.py.
"""


def test_reason_code_has_new_patch_constants() -> None:
    """PR-of-file-discard-hunk: 9 new patch-related reason codes."""
    from tools.webapi._helpers import ReasonCode

    expected = {
        "patch_empty": "patch_empty",
        "patch_too_large": "patch_too_large",
        "patch_malformed": "patch_malformed",
        "patch_unsafe_path": "patch_unsafe_path",
        "multi_file_patch": "multi_file_patch",
        "patch_file_mismatch": "patch_file_mismatch",
        "patch_binary": "patch_binary",
        "patch_check_failed": "patch_check_failed",
        "patch_apply_failed": "patch_apply_failed",
    }
    for attr_name, string_value in expected.items():
        assert hasattr(ReasonCode, attr_name.upper()), f"missing {attr_name}"
        assert getattr(ReasonCode, attr_name.upper()) == string_value


def test_x_truly_staged_and_y_worktree_constants() -> None:
    """PR-of-file-discard-hunk: shared porcelain X/Y constants extracted."""
    from tools.webapi._helpers import X_TRULY_STAGED, Y_WORKTREE

    # X 真正已暂存的 git 状态码(非 intent-to-add)
    assert "M" in X_TRULY_STAGED
    assert "D" in X_TRULY_STAGED
    assert "R" in X_TRULY_STAGED
    assert "A" not in X_TRULY_STAGED  # intent-to-add 的 X 是 ' '

    # Y worktree 改动
    assert "M" in Y_WORKTREE
    assert "A" in Y_WORKTREE
    assert "D" in Y_WORKTREE
    assert " " not in Y_WORKTREE  # 空格不算改动
