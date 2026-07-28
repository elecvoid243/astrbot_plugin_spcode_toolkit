"""Unit tests for POST /spcode/git-conflict-resolve."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch


# Mock astrbot.api.web before any tools.webapi import triggers it
class _FakeJSONResponse:
    """Minimal stand-in for astrbot.api.web.JSONResponse in tests."""

    def __init__(self, content=None, status_code=200, headers=None):
        self._content = content
        self.status_code = status_code
        self.headers = headers or {}

    def __getitem__(self, key):
        return self._content[key]

    def get(self, key, default=None):
        return self._content.get(key, default)


_web_mock = MagicMock()
_web_mock.JSONResponse = _FakeJSONResponse
sys.modules.setdefault("astrbot.api.web", _web_mock)
sys.modules.setdefault("astrbot.api", MagicMock(web=_web_mock))

import pytest  # noqa: E402


class TestRebuildFileFromHunks:
    def test_all_hunks_resolved(self):
        from tools.webapi._helpers import ConflictHunk
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks

        content = "line1\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> dev\nline7\n"
        lines = content.split("\n")
        hunks = [
            ConflictHunk(
                index=0,
                start_line=2,
                end_line=6,
                ours="ours\n",
                theirs="theirs\n",
                base=None,
                ours_label="HEAD",
                theirs_label="dev",
            )
        ]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "theirs"})
        assert result == "line1\ntheirs\nline7\n"

    def test_partial_resolution_returns_none(self):
        from tools.webapi._helpers import ConflictHunk
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks

        content = "<<<<<<< HEAD\no1\n=======\nt1\n>>>>>>> b\nmid\n<<<<<<< HEAD\no2\n=======\nt2\n>>>>>>> b\n"
        lines = content.split("\n")
        hunks = [
            ConflictHunk(
                index=0,
                start_line=1,
                end_line=5,
                ours="o1\n",
                theirs="t1\n",
                base=None,
                ours_label="HEAD",
                theirs_label="b",
            ),
            ConflictHunk(
                index=1,
                start_line=7,
                end_line=11,
                ours="o2\n",
                theirs="t2\n",
                base=None,
                ours_label="HEAD",
                theirs_label="b",
            ),
        ]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "ours"})
        assert result is None

    def test_base_choice(self):
        from tools.webapi._helpers import ConflictHunk
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks

        content = "<<<<<<< HEAD\nours\n||||||| base\nbaseval\n=======\ntheirs\n>>>>>>> dev\n"
        lines = content.split("\n")
        hunks = [
            ConflictHunk(
                index=0,
                start_line=1,
                end_line=7,
                ours="ours\n",
                theirs="theirs\n",
                base="baseval\n",
                ours_label="HEAD",
                theirs_label="dev",
            )
        ]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "base"})
        assert result == "baseval\n"


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestResolveBodyValidation:
    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(_make_plugin(), body=None)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_file_and_all_both_provided(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(), body={"file": "a.py", "all": True, "resolution": "ours"}
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_neither_file_nor_all(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(_make_plugin(), body={"resolution": "ours"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_hunks_and_resolution_mutual_exclusion(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={
                "file": "a.py",
                "hunks": [{"index": 0, "choice": "ours"}],
                "resolution": "theirs",
            },
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_all_with_custom_forbidden(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={"all": True, "resolution": "custom", "content": "x"},
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_all_with_hunks_forbidden(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={"all": True, "hunks": [{"index": 0, "choice": "ours"}]},
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_custom_without_content(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(), body={"file": "a.py", "resolution": "custom"}
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_content_too_large(self):
        from tools.webapi.git_conflict_resolve import handle

        result = await handle(
            _make_plugin(),
            body={
                "file": "a.py",
                "resolution": "custom",
                "content": "x" * (1024 * 1024 + 1),
            },
        )
        assert result["data"]["reason"] == "invalid_param"


class TestResolveExecution:
    @pytest.mark.asyncio
    async def test_no_conflict_in_progress(self):
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"file": "a.py", "resolution": "ours"})
        assert result["data"]["reason"] == "no_conflict_in_progress"

    @pytest.mark.asyncio
    async def test_file_not_conflicted(self):
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_resolve._list_conflicted_files",
                new_callable=AsyncMock,
                return_value=[{"path": "other.py", "status": "UU"}],
            ),
            patch(
                "tools.webapi.git_conflict_resolve._validate_repo_relative_file",
                return_value=(MagicMock(), None),
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "merge"
            result = await handle(plugin, body={"file": "a.py", "resolution": "ours"})
        assert result["data"]["reason"] == "file_not_conflicted"

    @pytest.mark.asyncio
    async def test_batch_all_ours(self):
        from tools.webapi.git_conflict_resolve import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_resolve._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_resolve._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_resolve._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "tools.webapi.git_conflict_resolve._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "merge"
            # First call returns conflicted, second call (after resolve) returns empty
            mock_list.side_effect = [
                [{"path": "a.py", "status": "UU"}, {"path": "b.py", "status": "AU"}],
                [],
            ]
            result = await handle(plugin, body={"all": True, "resolution": "ours"})
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["resolved"] is True
        assert data["mode"] == "all"
        assert data["files_resolved"] == 2
        assert data["all_resolved"] is True
