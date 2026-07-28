"""Unit tests for POST /spcode/git-cherry-pick."""

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


class TestCherryPickBodyValidation:
    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from tools.webapi.git_cherry_pick import handle

        result = await handle(_make_plugin(), body=None)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_missing_ref(self):
        from tools.webapi.git_cherry_pick import handle

        result = await handle(_make_plugin(), body={})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_empty_ref(self):
        from tools.webapi.git_cherry_pick import handle

        result = await handle(_make_plugin(), body={"ref": ""})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_invalid_mainline_zero(self):
        from tools.webapi.git_cherry_pick import handle

        result = await handle(_make_plugin(), body={"ref": "abc", "mainline": 0})
        assert result["data"]["reason"] == "invalid_param"

    @pytest.mark.asyncio
    async def test_invalid_mainline_negative(self):
        from tools.webapi.git_cherry_pick import handle

        result = await handle(_make_plugin(), body={"ref": "abc", "mainline": -1})
        assert result["data"]["reason"] == "invalid_param"

    @pytest.mark.asyncio
    async def test_invalid_mainline_string(self):
        from tools.webapi.git_cherry_pick import handle

        result = await handle(_make_plugin(), body={"ref": "abc", "mainline": "x"})
        assert result["data"]["reason"] == "invalid_param"

    @pytest.mark.asyncio
    async def test_invalid_mainline_bool(self):
        from tools.webapi.git_cherry_pick import handle

        result = await handle(_make_plugin(), body={"ref": "abc", "mainline": True})
        assert result["data"]["reason"] == "invalid_param"


class TestCherryPickExecution:
    @pytest.mark.asyncio
    async def test_operation_in_progress(self):
        from tools.webapi.git_cherry_pick import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_cherry_pick._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_cherry_pick._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "revert"
            result = await handle(plugin, body={"ref": "abc"})
        assert result["data"]["reason"] == "operation_in_progress"

    @pytest.mark.asyncio
    async def test_commit_not_found(self):
        from tools.webapi.git_cherry_pick import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_cherry_pick._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_cherry_pick._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=False),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "bad"})
        assert result["data"]["reason"] == "commit_not_found"

    @pytest.mark.asyncio
    async def test_worktree_dirty(self):
        from tools.webapi.git_cherry_pick import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_cherry_pick._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_cherry_pick._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=True),
            patch(
                "tools.webapi.git_cherry_pick._run_git_async", new_callable=AsyncMock
            ) as mock_run,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            mock_run.return_value = {
                "ok": True,
                "stdout": " M file.py\n",
                "stderr": "",
                "code": 0,
            }
            result = await handle(plugin, body={"ref": "abc"})
        assert result["data"]["reason"] == "worktree_dirty"

    @pytest.mark.asyncio
    async def test_successful_pick(self):
        from tools.webapi.git_cherry_pick import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "status" in args and "--porcelain" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "cherry-pick" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "rev-parse" in args and "HEAD" in args:
                return {"ok": True, "stdout": "new123\n", "stderr": "", "code": 0}
            if "log" in args:
                return {"ok": True, "stdout": "fix: bug\n", "stderr": "", "code": 0}
            if "show" in args:
                return {"ok": True, "stdout": "src/x.py\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_cherry_pick._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_cherry_pick._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=True),
            patch("tools.webapi.git_cherry_pick._run_git_async", side_effect=mock_run),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "abc123"})
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["picked"] is True
        assert data["new_sha"] == "new123"
        assert data["original_message"] == "fix: bug"

    @pytest.mark.asyncio
    async def test_cherry_pick_conflict(self):
        from tools.webapi.git_cherry_pick import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "status" in args and "--porcelain" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "cherry-pick" in args:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "CONFLICT (content): Merge conflict in f.py\n",
                    "code": 1,
                }
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_cherry_pick._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_cherry_pick._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=True),
            patch("tools.webapi.git_cherry_pick._run_git_async", side_effect=mock_run),
            patch(
                "tools.webapi.git_cherry_pick._list_conflicted_files",
                new_callable=AsyncMock,
                return_value=[{"path": "f.py", "status": "UU"}],
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "abc123"})
        assert result["data"]["reason"] == "cherry_pick_conflict"
        assert result["data"]["conflict"] is True

    @pytest.mark.asyncio
    async def test_cherry_pick_empty(self):
        from tools.webapi.git_cherry_pick import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "status" in args and "--porcelain" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "cherry-pick" in args:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "The previous cherry-pick is now empty\n",
                    "code": 1,
                }
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_cherry_pick._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_cherry_pick._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=True),
            patch("tools.webapi.git_cherry_pick._run_git_async", side_effect=mock_run),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "abc123"})
        assert result["data"]["reason"] == "cherry_pick_empty"
