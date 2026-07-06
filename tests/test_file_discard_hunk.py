"""Unit tests for /spcode/file-discard-hunk endpoint and helpers.

Class layout:
- TestParsePatchHeader: _parse_patch_header() pure-function behavior
- TestEnvelopes: _make_file_discard_hunk_*_envelope() shape
- TestHandlerBodyValidation: handler body parsing / size / type checks
- TestHandlerFileSafety: handler 4-step defense / path_unsafe
- TestHandlerGitOps: handler real git operations (scope detect, --check, --reverse)
- TestE2E: route registration & HANDLERS dict
"""

from __future__ import annotations

import pytest  # noqa: F401  # used by TestEnvelopes / TestHandler* in Tasks 3-6
from tools.webapi import file_discard_hunk


# ── TestParsePatchHeader ────────────────────────────────────────────


class TestParsePatchHeader:
    """Pure-function tests for the patch header validator."""

    def test_valid_single_hunk_returns_no_err(self) -> None:
        patch = (
            "diff --git a/main.py b/main.py\n"
            "index 0000000..1111111 100644\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,1 +1,2 @@\n"
            "-old line\n"
            "+new line 1\n"
            "+new line 2\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="main.py")
        assert meta.err is None
        assert meta.hunk_count == 1
        assert meta.source_path == "main.py"
        assert meta.target_path == "main.py"

    def test_multi_hunk_counts_correctly(self) -> None:
        patch = (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,1 +1,2 @@\n"
            "-a\n"
            "+A\n"
            "+A2\n"
            "@@ -10,1 +11,2 @@\n"
            "-b\n"
            "+B\n"
            "+B2\n"
            "@@ -20,1 +22,2 @@\n"
            "-c\n"
            "+C\n"
            "+C2\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="main.py")
        assert meta.err is None
        assert meta.hunk_count == 3

    def test_no_diff_header_returns_patch_malformed(self) -> None:
        patch = "@@ -1,1 +1,2 @@\n-a\n+A\n"
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="x.py")
        assert meta.err == "patch_malformed"

    def test_no_hunk_header_returns_patch_malformed(self) -> None:
        patch = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            # intentionally no @@
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="x.py")
        assert meta.err == "patch_malformed"

    def test_multi_file_patch_returns_multi_file_patch(self) -> None:
        patch = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "-x\n"
            "+X\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-y\n"
            "+Y\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="a.py")
        assert meta.err == "multi_file_patch"

    def test_file_mismatch_returns_patch_file_mismatch(self) -> None:
        patch = (
            "diff --git a/other.py b/other.py\n"
            "--- a/other.py\n"
            "+++ b/other.py\n"
            "@@ -1 +1 @@\n"
            "-x\n+X\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="main.py")
        assert meta.err == "patch_file_mismatch"

    def test_parent_traversal_in_diff_header_returns_patch_unsafe_path(self) -> None:
        # 注意:patch 中 `a/../escape` 的 `..` 必须被检测
        patch = (
            "diff --git a/../escape b/../escape\n"
            "--- a/../escape\n"
            "+++ b/../escape\n"
            "@@ -1 +1 @@\n-x\n+X\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="../escape")
        assert meta.err == "patch_unsafe_path"

    def test_absolute_path_in_diff_header_returns_patch_unsafe_path(self) -> None:
        patch = (
            "diff --git a//etc/passwd b//etc/passwd\n"
            "--- a//etc/passwd\n"
            "+++ b//etc/passwd\n"
            "@@ -1 +1 @@\n-x\n+X\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="//etc/passwd")
        assert meta.err == "patch_unsafe_path"

    def test_dot_git_in_diff_header_returns_patch_unsafe_path(self) -> None:
        patch = (
            "diff --git a/.git/HEAD b/.git/HEAD\n"
            "--- a/.git/HEAD\n"
            "+++ b/.git/HEAD\n"
            "@@ -1 +1 @@\n-x\n+X\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file=".git/HEAD")
        assert meta.err == "patch_unsafe_path"

    def test_binary_marker_returns_patch_binary(self) -> None:
        patch = (
            "diff --git a/img.png b/img.png\n"
            "Binary files a/img.png and b/img.png differ\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="img.png")
        assert meta.err == "patch_binary"

    def test_garbled_hunk_header_returns_patch_malformed(self) -> None:
        patch = (
            "diff --git a/x.py b/x.py\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -abc +def @@\n"
        )
        meta = file_discard_hunk._parse_patch_header(patch, expected_file="x.py")
        assert meta.err == "patch_malformed"
