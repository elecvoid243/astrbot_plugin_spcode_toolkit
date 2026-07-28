"""Unit tests for GET /spcode/git-conflict-status."""

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


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestConflictStatusNoConflict:
    @pytest.mark.asyncio
    async def test_no_conflict_returns_in_conflict_false(self):
        from tools.webapi.git_conflict_status import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_status._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_status._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin)
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["in_conflict"] is False
        assert data["operation"] is None
        assert data["all_resolved"] is True
        assert data["conflicted_files"] == []


class TestConflictStatusWithConflict:
    @pytest.mark.asyncio
    async def test_merge_conflict_with_hunks(self, tmp_path):
        from tools.webapi.git_conflict_status import handle

        plugin = _make_plugin()
        conflicted = [{"path": "src/a.py", "status": "UU"}]
        file_content = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> dev\n"

        # Create the actual file so Path.exists() works
        # Use write_bytes to avoid Windows \r\n translation
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "a.py").write_bytes(file_content.encode("utf-8"))

        with (
            patch(
                "tools.webapi.git_conflict_status._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_status._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_status._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "tools.webapi.git_conflict_status._read_operation_ref",
                new_callable=AsyncMock,
            ) as mock_ref,
            patch(
                "tools.webapi.git_conflict_status._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
            ),
            patch(
                "tools.webapi.git_conflict_status._read_three_way",
                new_callable=AsyncMock,
            ) as mock_tw,
        ):
            mock_pf.return_value = (
                None,
                {"directory": str(tmp_path), "umo": "u1", "worktree": str(tmp_path)},
            )
            mock_detect.return_value = "merge"
            mock_list.return_value = conflicted
            mock_ref.return_value = ("abc123", "feat: x")
            mock_tw.return_value = {
                "base": None,
                "ours": "ours\n",
                "theirs": "theirs\n",
                "truncated": False,
            }
            result = await handle(plugin)

        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["in_conflict"] is True
        assert data["operation"] == "merge"
        assert data["operation_ref"] == "abc123"
        assert data["operation_subject"] == "feat: x"
        assert len(data["conflicted_files"]) == 1
        cf = data["conflicted_files"][0]
        assert cf["path"] == "src/a.py"
        assert cf["status"] == "UU"
        assert len(cf["hunks"]) == 1
        assert cf["hunks"][0]["ours"] == "ours\n"
        assert cf["hunks"][0]["theirs"] == "theirs\n"
        assert cf["binary"] is False

    @pytest.mark.asyncio
    async def test_binary_file_detected(self, tmp_path):
        from tools.webapi.git_conflict_status import handle

        plugin = _make_plugin()
        conflicted = [{"path": "logo.png", "status": "UU"}]

        src_dir = tmp_path
        (src_dir / "logo.png").write_bytes(b"\x89PNG\x00\x01\x02")

        with (
            patch(
                "tools.webapi.git_conflict_status._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_status._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_status._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "tools.webapi.git_conflict_status._read_operation_ref",
                new_callable=AsyncMock,
            ) as mock_ref,
            patch(
                "tools.webapi.git_conflict_status._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
            ),
            patch(
                "tools.webapi.git_conflict_status._read_three_way",
                new_callable=AsyncMock,
            ) as mock_tw,
        ):
            mock_pf.return_value = (
                None,
                {"directory": str(tmp_path), "umo": "u1", "worktree": str(tmp_path)},
            )
            mock_detect.return_value = "cherry_pick"
            mock_list.return_value = conflicted
            mock_ref.return_value = ("def456", "fix: y")
            mock_tw.return_value = {
                "base": None,
                "ours": None,
                "theirs": None,
                "truncated": False,
            }
            result = await handle(plugin)

        data = result["data"] if isinstance(result, dict) else result._content["data"]
        cf = data["conflicted_files"][0]
        assert cf["binary"] is True
        assert cf["hunks"] == []

    @pytest.mark.asyncio
    async def test_resolved_files_computed(self, tmp_path):
        from tools.webapi.git_conflict_status import handle

        plugin = _make_plugin()
        conflicted = [{"path": "src/a.py", "status": "UU"}]

        # staged output includes both resolved and conflicted paths (NUL-separated)
        staged_stdout = "src/a.py\0src/utils.py\0"

        async def mock_run(args, **kwargs):
            if "diff" in args and "--cached" in args:
                return {"ok": True, "stdout": staged_stdout, "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_conflict_status._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_status._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_status._list_conflicted_files",
                new_callable=AsyncMock,
            ) as mock_list,
            patch(
                "tools.webapi.git_conflict_status._read_operation_ref",
                new_callable=AsyncMock,
            ) as mock_ref,
            patch(
                "tools.webapi.git_conflict_status._run_git_async",
                side_effect=mock_run,
            ),
            patch(
                "tools.webapi.git_conflict_status._read_three_way",
                new_callable=AsyncMock,
            ) as mock_tw,
        ):
            mock_pf.return_value = (
                None,
                {"directory": str(tmp_path), "umo": "u1", "worktree": str(tmp_path)},
            )
            mock_detect.return_value = "merge"
            mock_list.return_value = conflicted
            mock_ref.return_value = ("abc", "msg")
            mock_tw.return_value = {
                "base": None,
                "ours": None,
                "theirs": None,
                "truncated": False,
            }
            result = await handle(plugin)

        data = result["data"] if isinstance(result, dict) else result._content["data"]
        # src/a.py is conflicted, src/utils.py is resolved
        assert data["resolved_files"] == ["src/utils.py"]
        assert data["total_conflicted"] == 1
        assert data["total_resolved"] == 1
        assert data["all_resolved"] is False
