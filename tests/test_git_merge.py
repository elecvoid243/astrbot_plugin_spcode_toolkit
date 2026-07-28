"""Unit tests for POST /spcode/git-merge."""

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


class TestGitMergeBodyValidation:
    """Body validation and cross-field mutual exclusion."""

    @pytest.mark.asyncio
    async def test_invalid_body_none(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body=None)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_missing_source(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"message": "hi"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_empty_source(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": ""})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_no_ff_and_ff_only_mutual_exclusion(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(
            plugin, body={"source": "dev", "no_ff": True, "ff_only": True}
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_squash_and_no_ff_mutual_exclusion(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(
            plugin, body={"source": "dev", "squash": True, "no_ff": True}
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_squash_and_ff_only_mutual_exclusion(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(
            plugin, body={"source": "dev", "squash": True, "ff_only": True}
        )
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_invalid_ref_name(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": "bad branch name"})
        assert result["data"]["reason"] == "invalid_branch"

    @pytest.mark.asyncio
    async def test_message_too_long(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": "dev", "message": "x" * 8193})
        assert result["data"]["reason"] == "invalid_body"


class TestGitMergePreflight:
    """Preflight checks: operation_in_progress, worktree_dirty."""

    @pytest.mark.asyncio
    async def test_operation_in_progress(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_merge._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_merge._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = "merge"
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "operation_in_progress"

    @pytest.mark.asyncio
    async def test_worktree_dirty(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        with (
            patch(
                "tools.webapi.git_merge._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_merge._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch(
                "tools.webapi.git_merge._run_git_async", new_callable=AsyncMock
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
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "worktree_dirty"

    @pytest.mark.asyncio
    async def test_squash_skips_dirty_check(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        call_log = []

        async def mock_run(args, **kwargs):
            call_log.append(args)
            if "merge" in args:
                return {
                    "ok": True,
                    "stdout": "Squash commit -- not updating HEAD\n",
                    "stderr": "",
                    "code": 0,
                }
            if "diff" in args and "--cached" in args:
                return {"ok": True, "stdout": "file.py\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_merge._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_merge._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"source": "dev", "squash": True})
        # Should NOT have called status --porcelain (dirty check skipped)
        status_calls = [a for a in call_log if "status" in a and "--porcelain" in a]
        assert len(status_calls) == 0
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["squash"] is True


class TestGitMergeExecution:
    """Execution paths: success, fast-forward, conflict, already-up-to-date."""

    @pytest.mark.asyncio
    async def test_already_up_to_date(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "merge" in args:
                return {
                    "ok": True,
                    "stdout": "Already up to date.\n",
                    "stderr": "",
                    "code": 0,
                }
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_merge._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_merge._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "merge_already_up_to_date"
        assert result["data"]["merged"] is False

    @pytest.mark.asyncio
    async def test_merge_conflict(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "status" in args and "--porcelain" in args:
                # dirty check → clean
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "merge" in args:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "CONFLICT (content): Merge conflict in src/a.py\n",
                    "code": 1,
                }
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_merge._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_merge._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run),
            patch(
                "tools.webapi.git_merge._list_conflicted_files",
                new_callable=AsyncMock,
                return_value=[{"path": "src/a.py", "status": "UU"}],
            ),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "merge_conflict"
        assert result["data"]["conflict"] is True
        assert len(result["data"]["conflicted_files"]) == 1

    @pytest.mark.asyncio
    async def test_successful_merge_commit(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "status" in args and "--porcelain" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "merge" in args:
                return {
                    "ok": True,
                    "stdout": "Merge made by the 'ort' strategy.\n",
                    "stderr": "",
                    "code": 0,
                }
            if "rev-parse" in args and "HEAD" in args:
                return {"ok": True, "stdout": "abc123\n", "stderr": "", "code": 0}
            if "log" in args:
                return {
                    "ok": True,
                    "stdout": "Merge branch 'dev'\n",
                    "stderr": "",
                    "code": 0,
                }
            if "show" in args:
                return {
                    "ok": True,
                    "stdout": "src/a.py\nsrc/b.py\n",
                    "stderr": "",
                    "code": 0,
                }
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_merge._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_merge._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run),
            patch(
                "tools.webapi.git_merge._read_post_mutation_branch_state",
                new_callable=AsyncMock,
            ) as mock_state,
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            mock_state.return_value = {
                "branches": [],
                "total": 0,
                "current": "main",
                "detached": False,
            }
            result = await handle(plugin, body={"source": "dev"})

        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["merged"] is True
        assert data["merge_sha"] == "abc123"
        assert data["fast_forward"] is False

    @pytest.mark.asyncio
    async def test_unrelated_histories(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "status" in args and "--porcelain" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "merge" in args:
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": "fatal: refusing to merge unrelated histories\n",
                    "code": 128,
                }
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with (
            patch(
                "tools.webapi.git_merge._git_endpoint_preflight",
                new_callable=AsyncMock,
            ) as mock_pf,
            patch(
                "tools.webapi.git_merge._detect_conflict_operation",
                new_callable=AsyncMock,
            ) as mock_detect,
            patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run),
        ):
            mock_pf.return_value = (
                None,
                {"directory": "/repo", "umo": "u1", "worktree": "/repo"},
            )
            mock_detect.return_value = None
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "unrelated_histories"
