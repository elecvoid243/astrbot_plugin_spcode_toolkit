"""Unit tests for POST /spcode/git-conflict-continue."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch


# Mock astrbot.api.web before any tools.webapi import triggers it
class _FakeJSONResponse:
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


class TestContinueValidation:
    @pytest.mark.asyncio
    async def test_no_conflict_in_progress(self):
        from tools.webapi.git_conflict_continue import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_continue._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_continue._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={})
        assert result["data"]["reason"] == "no_conflict_in_progress"

    @pytest.mark.asyncio
    async def test_unresolved_conflicts_remain(self):
        from tools.webapi.git_conflict_continue import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_continue._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_continue._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_continue._list_conflicted_files",
                new_callable=AsyncMock,
                return_value=[{"path": "a.py", "status": "UU"}],
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "merge"
            result = await handle(plugin, body={})
        assert result["data"]["reason"] == "unresolved_conflicts_remain"

    @pytest.mark.asyncio
    async def test_message_too_long(self):
        from tools.webapi.git_conflict_continue import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"message": "x" * 8193})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_successful_merge_continue(self):
        from tools.webapi.git_conflict_continue import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "commit" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "rev-parse" in args and "HEAD" in args:
                return {"ok": True, "stdout": "sha456\n", "stderr": "", "code": 0}
            if "log" in args:
                return {"ok": True, "stdout": "Merge branch 'x'\n", "stderr": "", "code": 0}
            if "show" in args:
                return {"ok": True, "stdout": "a.py\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_conflict_continue._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_continue._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_conflict_continue._list_conflicted_files",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("tools.webapi.git_conflict_continue._run_git_async", side_effect=mock_run),
            patch(
                "tools.webapi.git_conflict_continue._read_post_mutation_branch_state",
                new_callable=AsyncMock,
                return_value={"branches": [], "total": 0, "current": "main", "detached": False},
            ),
            patch("tools.webapi.git_conflict_continue._build_git_env", return_value=None),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "merge"
            result = await handle(plugin, body={})
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["continued"] is True
        assert data["operation"] == "merge"
        assert data["commit_sha"] == "sha456"

    @pytest.mark.asyncio
    async def test_rebase_continue_uses_rebase_continue(self):
        from tools.webapi.git_conflict_continue import handle

        plugin = _make_plugin()
        calls = []

        async def mock_run(args, **kwargs):
            calls.append(args)
            if "rebase" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "rev-parse" in args and "HEAD" in args:
                return {"ok": True, "stdout": "abc123\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_conflict_continue._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_continue._detect_conflict_operation",
                new_callable=AsyncMock,
                return_value="rebase",
            ),
            patch(
                "tools.webapi.git_conflict_continue._list_conflicted_files",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "tools.webapi.git_conflict_continue._run_git_async",
                side_effect=mock_run,
            ),
            patch(
                "tools.webapi.git_conflict_continue._read_post_mutation_branch_state",
                new_callable=AsyncMock,
                return_value={
                    "branches": [],
                    "total": 0,
                    "current": "main",
                    "detached": False,
                },
            ),
            patch(
                "tools.webapi.git_conflict_continue._build_git_env",
                return_value=None,
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            result = await handle(plugin, body={})

        assert result["data"]["continued"] is True
        rebase_call = next(call for call in calls if "rebase" in call)
        assert "rebase" in rebase_call
        assert "--continue" in rebase_call
