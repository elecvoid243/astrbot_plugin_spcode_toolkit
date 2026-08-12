"""Unit tests for POST /spcode/git-conflict-abort."""

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


class TestAbortValidation:
    @pytest.mark.asyncio
    async def test_no_conflict_in_progress(self):
        from tools.webapi.git_conflict_abort import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_abort._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_abort._detect_conflict_operation",
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
    async def test_successful_abort(self):
        from tools.webapi.git_conflict_abort import handle

        plugin = _make_plugin()
        detect_calls = [0]

        async def mock_detect(*a, **kw):
            detect_calls[0] += 1
            return "merge" if detect_calls[0] == 1 else None

        with (
            patch(
                "tools.webapi.git_conflict_abort._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_abort._detect_conflict_operation",
                side_effect=mock_detect,
            ),
            patch(
                "tools.webapi.git_conflict_abort._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
            ),
            patch(
                "tools.webapi.git_conflict_abort._read_operation_ref",
                new_callable=AsyncMock,
                return_value=("abc123", "feat: x"),
            ),
            patch(
                "tools.webapi.git_conflict_abort._read_post_mutation_branch_state",
                new_callable=AsyncMock,
                return_value={"branches": [], "total": 0, "current": "main", "detached": False},
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            result = await handle(plugin, body={})
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["aborted"] is True
        assert data["operation"] == "merge"
        assert data["operation_ref"] == "abc123"

    @pytest.mark.asyncio
    async def test_abort_git_failure(self):
        from tools.webapi.git_conflict_abort import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_conflict_abort._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_abort._detect_conflict_operation",
                new_callable=AsyncMock,
                return_value="cherry_pick",
            ),
            patch(
                "tools.webapi.git_conflict_abort._run_git_async",
                new_callable=AsyncMock,
                return_value={"ok": False, "stdout": "", "stderr": "fatal: error", "code": 128},
            ),
            patch(
                "tools.webapi.git_conflict_abort._read_operation_ref",
                new_callable=AsyncMock,
                return_value=("def456", "fix: y"),
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            result = await handle(plugin, body={})
        assert result["data"]["reason"] == "git_error"
        assert result["data"]["aborted"] is False

    @pytest.mark.asyncio
    async def test_rebase_abort_uses_rebase_abort(self):
        from tools.webapi.git_conflict_abort import handle

        plugin = _make_plugin()
        calls = []
        detect_calls = [0]

        async def mock_detect(*args, **kwargs):
            detect_calls[0] += 1
            return "rebase" if detect_calls[0] == 1 else None

        async def mock_run(args, **kwargs):
            calls.append(args)
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_conflict_abort._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_conflict_abort._detect_conflict_operation",
                side_effect=mock_detect,
            ),
            patch(
                "tools.webapi.git_conflict_abort._run_git_async",
                side_effect=mock_run,
            ),
            patch(
                "tools.webapi.git_conflict_abort._read_operation_ref",
                new_callable=AsyncMock,
                return_value=("abc123", "commit"),
            ),
            patch(
                "tools.webapi.git_conflict_abort._read_post_mutation_branch_state",
                new_callable=AsyncMock,
                return_value={
                    "branches": [],
                    "total": 0,
                    "current": "main",
                    "detached": False,
                },
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            result = await handle(plugin, body={})

        assert result["data"]["aborted"] is True
        assert calls[0][-2:] == ["rebase", "--abort"]
