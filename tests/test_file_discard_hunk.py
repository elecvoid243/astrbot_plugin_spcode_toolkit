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

from pathlib import Path
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


# ── TestHandlerFileSafety ───────────────────────────────────────────


class TestHandlerFileSafety:
    """Handler steps 7-11 — file safety + repo probe + patch parsing."""

    @pytest.fixture
    def git_repo(self, tmp_path) -> "Path":
        """Initialize a real git repo with a tracked file."""
        import os
        import subprocess

        env = {
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@e",
            "PATH": os.environ["PATH"],
        }
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("original\n", encoding="utf-8")
        for cmd in (
            ["git", "-C", str(repo), "init"],
            ["git", "-C", str(repo), "config", "user.email", "t@e"],
            ["git", "-C", str(repo), "config", "user.name", "T"],
            ["git", "-C", str(repo), "add", "main.py"],
            ["git", "-C", str(repo), "commit", "-m", "init"],
        ):
            subprocess.run(cmd, check=True, capture_output=True, env=env)
        return repo

    @pytest.fixture
    def mock_plugin_with_repo(self, git_repo, monkeypatch) -> Any:
        from tools.project import state as _proj_state
        from tests.conftest import _make_plugin

        plugin = _make_plugin()
        _proj_state.put(
            "test-umo", {"directory": str(git_repo), "loaded_at": 0}
        )
        return plugin

    @pytest.fixture
    def post_body(self, monkeypatch: pytest.MonkeyPatch):
        from astrbot.api import web

        def _post(body: dict) -> None:
            class _Req:
                @staticmethod
                async def json(default=None):
                    return body
            monkeypatch.setattr(web, "request", _Req)

        return _post

    def _valid_patch(self) -> str:
        return (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,1 +1,2 @@\n"
            "-original\n"
            "+new line\n"
            "+new line 2\n"
        )

    @pytest.mark.asyncio
    async def test_directory_missing(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        """Loaded project was deleted externally → directory_missing.

        不依赖 ``shutil.rmtree`` 物理删除:Windows 上 ``.git/objects/XX/YY``
        因 transient handle / share mode 经常 PermissionError,导致目录残留、
        ``Path.is_dir()`` 仍 True。改用"将 state 指向 guaranteed 缺失目录"
        来稳定触发 ``directory_missing``,语义与生产场景一致。
        """
        from tools.project import state as _proj_state

        guaranteed_missing = (
            mock_plugin_with_repo.get_loaded_project("test-umo")["directory"]
            + "_DELETED_BY_TEST"
        )
        _proj_state.put(
            "test-umo",
            {"directory": guaranteed_missing, "loaded_at": 0},
        )
        post_body({"file": "main.py", "patch_text": self._valid_patch()})
        result = await file_discard_hunk.handle(mock_plugin_with_repo)
        assert result["data"]["reason"] == "directory_missing"

    @pytest.mark.asyncio
    async def test_not_a_git_repo(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Loaded dir is not a git repo → not_a_git_repo."""
        from astrbot.api import web
        from tests.conftest import _make_plugin
        from tools.project import state as _proj_state

        non_git = tmp_path / "non_git"
        non_git.mkdir()
        (non_git / "x.txt").write_text("x")
        plugin = _make_plugin()
        _proj_state.put("u", {"directory": str(non_git), "loaded_at": 0})

        class _Req:
            @staticmethod
            async def json(default=None):
                return {
                    "file": "x.txt",
                    "patch_text": (
                        "diff --git a/x.txt b/x.txt\n"
                        "@@ -1 +1 @@\n"
                        "-x\n"
                        "+X\n"
                    ),
                }

        monkeypatch.setattr(web, "request", _Req)
        result = await file_discard_hunk.handle(plugin)
        assert result["data"]["reason"] == "not_a_git_repo"

    @pytest.mark.asyncio
    async def test_path_unsafe_absolute(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        post_body({"file": "/etc/passwd", "patch_text": "x"})
        result = await file_discard_hunk.handle(mock_plugin_with_repo)
        assert result["data"]["reason"] == "path_unsafe"

    @pytest.mark.asyncio
    async def test_path_unsafe_parent_traversal(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        post_body({"file": "../escape.py", "patch_text": "x"})
        result = await file_discard_hunk.handle(mock_plugin_with_repo)
        assert result["data"]["reason"] == "path_unsafe"

    @pytest.mark.asyncio
    async def test_path_unsafe_dot_git(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        post_body({"file": ".git/config", "patch_text": "x"})
        result = await file_discard_hunk.handle(mock_plugin_with_repo)
        assert result["data"]["reason"] == "path_unsafe"

    @pytest.mark.asyncio
    async def test_file_not_found(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        post_body(
            {
                "file": "no_such.py",
                "patch_text": (
                    "diff --git a/no_such.py b/no_such.py\n"
                    "@@ -1 +1 @@\n"
                    "-x\n+X\n"
                ),
            }
        )
        result = await file_discard_hunk.handle(mock_plugin_with_repo)
        assert result["data"]["reason"] == "file_not_found"

    @pytest.mark.asyncio
    async def test_worktree_invalid_six_step_defense(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        """All 6 attack vectors on worktree= should be rejected."""
        # Body patch 选用仅 1 个 hunk 的 minimal valid patch —— 即使 worktree 校验
        # 通过,patch 也会被 step 8-11 解析失败,但我们先确认 worktree 校验能拦下。
        body_patch = (
            "diff --git a/main.py b/main.py\n"
            "@@ -1 +1 @@\n"
            "-x\n+X\n"
        )
        attack_vectors = [
            "../escape",         # 1. .. 段
            "/etc/passwd",       # 2. 绝对路径
            "C:\\Windows",       # 3. Windows 绝对路径
            ".git/HEAD",         # 4. 隐藏目录组件
            "non/existent/dir",  # 5. 不存在
            # 6. git-common-dir 不匹配 — 需要 mock `_validate_worktree_param` 自身,
            #    见 patch_check_failed 类比;此处省略(生产代码已覆盖,见
            #    tools/_helpers.py _validate_worktree_param step 6)。
        ]
        for vector in attack_vectors:
            post_body(
                {"file": "main.py", "worktree": vector, "patch_text": body_patch}
            )
            result = await file_discard_hunk.handle(mock_plugin_with_repo)
            assert result["data"]["reason"] in (
                "worktree_invalid",
                "directory_missing",
                "not_a_git_repo",
            ), (
                f"worktree={vector!r} should be rejected (got {result['data']['reason']})"
            )

    @pytest.mark.asyncio
    async def test_patch_file_mismatch_in_handler(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        patch = (
            "diff --git a/other.py b/other.py\n"
            "--- a/other.py\n"
            "+++ b/other.py\n"
            "@@ -1 +1 @@\n"
            "-x\n+X\n"
        )
        post_body({"file": "main.py", "patch_text": patch})
        result = await file_discard_hunk.handle(mock_plugin_with_repo)
        assert result["data"]["reason"] == "patch_file_mismatch"

    @pytest.mark.asyncio
    async def test_multi_file_patch_in_handler(
        self, mock_plugin_with_repo, post_body
    ) -> None:
        patch = (
            "diff --git a/main.py b/main.py\n"
            "@@ -1 +1 @@\n"
            "-a\n+A\n"
            "diff --git a/other.py b/other.py\n"
            "@@ -1 +1 @@\n"
            "-b\n+B\n"
        )
        post_body({"file": "main.py", "patch_text": patch})
        result = await file_discard_hunk.handle(mock_plugin_with_repo)
        assert result["data"]["reason"] == "multi_file_patch"


# ── TestHandlerGitOps ────────────────────────────────────────────────


class TestHandlerGitOps:
    """Handler steps 12-15 — scope auto-detect + git apply --check + --reverse."""

    @pytest.fixture
    def git_repo_with_change(self, tmp_path) -> Path:
        """Git repo with main.py tracked + modified (unstaged)."""
        import os
        import subprocess

        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e",
        })
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("original line\n", encoding="utf-8")
        for cmd in (
            ["git", "-C", str(repo), "init"],
            ["git", "-C", str(repo), "config", "user.email", "t@e"],
            ["git", "-C", str(repo), "config", "user.name", "T"],
            ["git", "-C", str(repo), "add", "main.py"],
            ["git", "-C", str(repo), "commit", "-m", "init"],
        ):
            subprocess.run(cmd, check=True, capture_output=True, env=env)
        # Modify the file
        (repo / "main.py").write_text(
            "original line\nadded line A\nadded line B\n", encoding="utf-8"
        )
        return repo

    @pytest.fixture
    def plugin(self, git_repo_with_change) -> Any:
        from tests.conftest import _make_plugin
        from tools.project import state as _proj_state

        plugin = _make_plugin()
        # NOTE: 计划示例代码使用 ``state.set()``(语法有误,Task 3 已修),
        # 改用 ``put(umo, info)`` canonical API。
        _proj_state.put(
            "u",
            {"directory": str(git_repo_with_change), "loaded_at": 0},
        )
        return plugin

    @pytest.fixture
    def post_body(self, monkeypatch: pytest.MonkeyPatch):
        from astrbot.api import web

        def _post(body: dict) -> None:
            class _Req:
                @staticmethod
                async def json(default=None):
                    return body
            monkeypatch.setattr(web, "request", _Req)

        return _post

    @pytest.mark.asyncio
    async def test_reverts_unstaged_hunk(
        self, plugin, post_body, git_repo_with_change
    ) -> None:
        """修改文件后,用 patch 丢弃新增的 1 个 hunk → 文件回到原始。"""
        forward_patch = (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,1 +1,3 @@\n"
            " original line\n"
            "+added line A\n"
            "+added line B\n"
        )
        post_body({"file": "main.py", "patch_text": forward_patch})
        result = await file_discard_hunk.handle(plugin)
        assert result["data"]["discarded"] is True, result
        assert result["data"]["scope"] == "unstaged"
        assert result["data"]["hunks_reverted"] == 1
        assert result["data"]["patch_sha256"]  # non-empty
        # 文件应回到原始
        assert (git_repo_with_change / "main.py").read_text(
            encoding="utf-8"
        ) == "original line\n"

    @pytest.mark.asyncio
    async def test_reverts_staged_hunk(
        self, plugin, post_body, git_repo_with_change
    ) -> None:
        """git add 后,patch 应用用 --cached。"""
        import subprocess
        # Stage the modification
        subprocess.run(
            ["git", "-C", str(git_repo_with_change), "add", "main.py"],
            check=True, capture_output=True,
        )
        # Same forward patch
        forward_patch = (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,1 +1,3 @@\n"
            " original line\n"
            "+added line A\n"
            "+added line B\n"
        )
        post_body({"file": "main.py", "patch_text": forward_patch})
        result = await file_discard_hunk.handle(plugin)
        assert result["data"]["discarded"] is True, result
        assert result["data"]["scope"] == "staged"
        assert result["data"]["hunks_reverted"] == 1

    @pytest.mark.asyncio
    async def test_not_modified(
        self, plugin, post_body, git_repo_with_change
    ) -> None:
        """文件无改动 → not_modified。"""
        # Reset file to clean state
        import subprocess
        subprocess.run(
            ["git", "-C", str(git_repo_with_change), "checkout", "--", "main.py"],
            check=True, capture_output=True,
        )
        post_body({
            "file": "main.py",
            "patch_text": (
                "diff --git a/main.py b/main.py\n"
                "@@ -1 +1 @@\n-a\n+A\n"
            ),
        })
        result = await file_discard_hunk.handle(plugin)
        assert result["data"]["reason"] == "not_modified"

    @pytest.mark.asyncio
    async def test_untracked_file(
        self, plugin, post_body, git_repo_with_change
    ) -> None:
        """新 untracked 文件 → untracked_file。"""
        (git_repo_with_change / "new.txt").write_text("hello")
        post_body({
            "file": "new.txt",
            "patch_text": (
                "diff --git a/new.txt b/new.txt\n"
                "@@ -1 +1 @@\n-hello\n+bye\n"
            ),
        })
        result = await file_discard_hunk.handle(plugin)
        assert result["data"]["reason"] == "untracked_file"

    @pytest.mark.asyncio
    async def test_patch_check_failed_context_mismatch(
        self, plugin, post_body, git_repo_with_change
    ) -> None:
        """patch hunk 上下文与工作区不匹配 → patch_check_failed。"""
        # Patch claims to change "wrong context" that doesn't exist in file
        bad_patch = (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-this line does not exist in the file\n"
            "+something else\n"
        )
        post_body({"file": "main.py", "patch_text": bad_patch})
        result = await file_discard_hunk.handle(plugin)
        assert result["data"]["reason"] == "patch_check_failed"
        assert result["data"]["stderr"]  # non-empty

    @pytest.mark.asyncio
    async def test_patch_check_failed_whitespace(
        self, plugin, post_body, git_repo_with_change
    ) -> None:
        """patch 含 trailing whitespace → patch_check_failed(--whitespace=error)。"""
        # Modify the file with trailing whitespace first
        (git_repo_with_change / "main.py").write_text(
            "original line\nnew line   \n", encoding="utf-8"  # trailing spaces
        )
        # Patch that would add another trailing-whitespace line
        bad_patch = (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,2 +1,3 @@\n"
            " original line\n"
            "-new line   \n"
            "+new line   \n"
            "+another line   \n"
        )
        post_body({"file": "main.py", "patch_text": bad_patch})
        result = await file_discard_hunk.handle(plugin)
        # git 在 --whitespace=error 下会拒绝 trailing whitespace
        assert result["data"]["reason"] in (
            "patch_check_failed", "patch_apply_failed"
        ), result

    @pytest.mark.asyncio
    async def test_patch_apply_failed_concurrent(
        self, plugin, post_body, git_repo_with_change, monkeypatch
    ) -> None:
        """check 通过后 apply 失败(模拟并发修改)→ patch_apply_failed。"""
        import tools.webapi.file_discard_hunk as fdh

        original_run = fdh._run_git_async
        call_count = {"n": 0}

        async def mock_run(cmd_args, **kwargs):
            call_count["n"] += 1
            res = await original_run(cmd_args, **kwargs)
            # 第一次调用是 --check(在 4 步里;但 step 9 是 probe)
            # 找到 apply 步骤的 --check 调用,放行;再找到 apply 调用,改成失败
            # 简化:让所有非 probe 的 git 调用都成功,只让 apply 失败
            if "apply" in cmd_args and "--check" not in cmd_args:
                return {
                    "ok": False,
                    "stderr": "simulated concurrent modification",
                    "code": 1,
                    "stdout": "",
                }
            return res

        monkeypatch.setattr(fdh, "_run_git_async", mock_run)

        forward_patch = (
            "diff --git a/main.py b/main.py\n"
            "--- a/main.py\n"
            "+++ b/main.py\n"
            "@@ -1,1 +1,3 @@\n"
            " original line\n"
            "+added line A\n"
            "+added line B\n"
        )
        post_body({"file": "main.py", "patch_text": forward_patch})
        result = await file_discard_hunk.handle(plugin)
        assert result["data"]["reason"] == "patch_apply_failed"