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

from typing import Any

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


# ── TestEnvelopes ────────────────────────────────────────────────────


class TestEnvelopes:
    """Envelope shape tests (mirror file_restore envelope tests)."""

    def test_empty_envelope_has_required_fields(self) -> None:
        env = file_discard_hunk._make_file_discard_hunk_empty_envelope(
            umo="u1", file="f.py", reason="invalid_body", elapsed_ms=5
        )
        assert env["status"] == "ok"
        assert env["data"]["discarded"] is False
        assert env["data"]["umo"] == "u1"
        assert env["data"]["file"] == "f.py"
        assert env["data"]["reason"] == "invalid_body"
        assert env["data"]["scope"] == "unstaged"  # default
        assert env["data"]["hunks_reverted"] == 0  # default
        assert env["data"]["elapsed_ms"] == 5

    def test_success_envelope_has_discarded_true(self) -> None:
        env = file_discard_hunk._make_file_discard_hunk_success_envelope(
            umo="u1", file="f.py", directory="/tmp/r", elapsed_ms=10,
            scope="staged", hunks=3, patch_sha="abc12345",
        )
        assert env["data"]["discarded"] is True
        assert env["data"]["reason"] is None
        assert env["data"]["scope"] == "staged"
        assert env["data"]["hunks_reverted"] == 3
        assert env["data"]["patch_sha256"] == "abc12345"
        assert env["data"]["worktree"] == "/tmp/r"


# ── TestHandlerBodyValidation ───────────────────────────────────────


class TestHandlerBodyValidation:
    """Handler steps 1-3 (body / file / patch_text) — no git calls."""

    @pytest.fixture
    def mock_plugin(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Any:
        """Build a minimal plugin + load a fake project."""
        from tests.conftest import _make_plugin
        from tools.project import state as _proj_state

        plugin = _make_plugin()
        # Load a fake project so the handler doesn't fail at no_project_loaded
        # when reaching step 6+ (we won't reach there in this class).
        # NOTE: 计划示例代码使用 ``state.set()``,但 tools/project/state.py
        # 提供的入口是 ``put(umo, info)``(canonical API,与其他 tests 一致)。
        _proj_state.put(
            "test-umo",
            {"directory": str(tmp_path), "loaded_at": 0},
        )
        return plugin

    @pytest.fixture
    def patch_web_request(self, monkeypatch: pytest.MonkeyPatch):
        """Helper to mock web.request.json() returning a given body."""
        from astrbot.api import web

        def _patch(body: Any) -> None:
            class _Req:
                @staticmethod
                async def json(default=None):
                    return body
            monkeypatch.setattr(web, "request", _Req)

        return _patch

    @pytest.mark.asyncio
    async def test_invalid_body_when_not_dict(
        self, mock_plugin, patch_web_request
    ) -> None:
        patch_web_request("not a dict")  # str, not dict
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["discarded"] is False
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_missing_file_field(
        self, mock_plugin, patch_web_request
    ) -> None:
        patch_web_request({"umo": "x", "patch_text": "diff --git a/x b/x"})
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["discarded"] is False
        assert result["data"]["reason"] == "missing_file"

    @pytest.mark.asyncio
    async def test_empty_file_field(
        self, mock_plugin, patch_web_request
    ) -> None:
        patch_web_request({"file": "  ", "patch_text": "x"})
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["reason"] == "missing_file"

    @pytest.mark.asyncio
    async def test_patch_text_not_string(
        self, mock_plugin, patch_web_request
    ) -> None:
        patch_web_request({"file": "x.py", "patch_text": 123})
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_empty_patch_text(
        self, mock_plugin, patch_web_request
    ) -> None:
        patch_web_request({"file": "x.py", "patch_text": ""})
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["reason"] == "patch_empty"

    @pytest.mark.asyncio
    async def test_patch_too_large(
        self, mock_plugin, patch_web_request
    ) -> None:
        big_patch = "x" * (256 * 1024 + 1)
        patch_web_request({"file": "x.py", "patch_text": big_patch})
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["reason"] == "patch_too_large"

    @pytest.mark.asyncio
    async def test_feature_disabled_agentsmd(
        self, mock_plugin, patch_web_request, monkeypatch
    ) -> None:
        mock_plugin._config["agentsmd_enabled"] = False
        patch_web_request({"file": "x.py", "patch_text": "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-a\n+A\n"})
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["reason"] == "feature_disabled"

    @pytest.mark.asyncio
    async def test_no_project_loaded(
        self, mock_plugin, patch_web_request, monkeypatch
    ) -> None:
        from tools.project import state as _proj_state
        _proj_state.reset()
        patch_web_request({"file": "x.py", "patch_text": "diff --git a/x.py b/x.py\n@@ -1 +1 @@\n-a\n+A\n"})
        result = await file_discard_hunk.handle(mock_plugin)
        assert result["data"]["reason"] == "no_project_loaded"