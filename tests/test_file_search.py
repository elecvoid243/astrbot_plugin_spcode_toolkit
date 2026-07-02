"""Tests for POST /spcode/file-search.

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md

v2.15.0 (2026-07-02): Backend now uses ``python_ripgrep.search`` instead of
the rg CLI subprocess + Python fallback. All tests mock ``rg_search`` at the
module level (``tools.webapi.file_search.rg_search``).
"""

from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.webapi import file_search
from tools.webapi._helpers import ReasonCode


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def mock_plugin(tmp_path: Path) -> MagicMock:
    """Plugin mock with worktree = tmp_path (a real dir + real git repo)."""
    # Create a real git repo so _git_endpoint_preflight passes
    import subprocess

    subprocess.run(
        ["git", "init", str(tmp_path)], check=True, capture_output=True, text=True
    )
    # Add at least one commit so it's a real repo
    (tmp_path / "init.txt").write_text("init")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "init.txt"],
        check=True,
        capture_output=True,
        text=True,
    )
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    plugin = MagicMock()
    plugin._git_binary.return_value = "git"
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
    }
    # get_loaded_project returns the project info dict
    plugin.get_loaded_project.return_value = {
        "directory": str(tmp_path),
        "loaded_at": 1.0,
    }
    return plugin


@pytest.fixture
def write_files(tmp_path: Path) -> None:
    """Write a few files in tmp_path for searching."""
    (tmp_path / "auth.py").write_text(
        "def validate_user(token: str) -> bool:\n"
        "    if not token:\n"
        "        return False\n"
        "    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Auth Module\nUse validate_user to check tokens.\n",
        encoding="utf-8",
    )


# Helper: convert tmp_path absolute paths to relative + format matches
# the way python_ripgrep.search() returns them (path:line:content).
def _fmt(absolute_path: str, line_no: int, content: str) -> str:
    """Format a single match line the way python_ripgrep.search returns it."""
    return f"{absolute_path}:{line_no}:{content}\n"


# ── Tests: happy paths (rg_search returns hits) ─────────────────


@pytest.mark.asyncio
async def test_hit(mock_plugin, write_files):
    """Basic substring search → rg_search returns matching lines, parsed into results."""
    tmp_path = Path(mock_plugin.get_loaded_project.return_value["directory"])
    matches = [
        _fmt(str(tmp_path / "auth.py"), 1, "def validate_user(token: str) -> bool:"),
        _fmt(str(tmp_path / "README.md"), 2, "Use validate_user to check tokens."),
    ]
    with patch.object(file_search, "rg_search", return_value=matches) as m:
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            worktree=None,
            body={"pattern": "validate_user"},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] is None
    # backend field dropped in v2.15.0 (single backend)
    assert "backend" not in data
    assert data["result_count"] == 2
    paths = [r["path"] for r in data["results"]]
    assert "auth.py" in paths
    assert "README.md" in paths
    for r in data["results"]:
        assert "validate_user" in r["snippet"]
        assert r["line"] >= 1
        # column defaults to 1 (python_ripgrep doesn't expose column)
        assert r["column"] == 1
    # rg_search was called once with the escaped pattern + (?i) prefix (default case-insensitive)
    assert m.call_count == 1
    patterns_arg = m.call_args.kwargs.get("patterns") or m.call_args.args[0]
    # pattern was re.escape'd → exact substring; default case_sensitive=False prepends (?i)
    assert patterns_arg == ["(?i)validate_user"]


@pytest.mark.asyncio
async def test_miss(mock_plugin, write_files):
    """No matches → rg_search returns [] → 200, results=[], reason=None."""
    with patch.object(file_search, "rg_search", return_value=[]):
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "this_string_does_not_exist_xyz"},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] is None
    assert data["results"] == []
    assert data["result_count"] == 0
    assert data["truncated"] is False


# ── Tests: input validation ─────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_pattern(mock_plugin):
    """Empty pattern → invalid_pattern; rg_search NOT called."""
    with patch.object(file_search, "rg_search") as m:
        result = await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": ""}
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN
    m.assert_not_called()


@pytest.mark.asyncio
async def test_pattern_with_newline(mock_plugin):
    """Pattern with \\n → invalid_pattern (multi-line search not supported)."""
    with patch.object(file_search, "rg_search") as m:
        result = await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "foo\nbar"}
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN
    m.assert_not_called()


@pytest.mark.asyncio
async def test_pattern_too_long(mock_plugin):
    """Pattern > 256 chars → pattern_too_long."""
    with patch.object(file_search, "rg_search") as m:
        result = await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "a" * 257}
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.PATTERN_TOO_LONG
    m.assert_not_called()


@pytest.mark.asyncio
async def test_max_results_clamp(mock_plugin, write_files):
    """max_results > 1000 → clamp to 1000."""
    with patch.object(file_search, "rg_search", return_value=[]):
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "validate_user", "max_results": 5000},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["max_results"] == 1000


# ── Tests: search options ───────────────────────────────────────


@pytest.mark.asyncio
async def test_glob_filter(mock_plugin, write_files):
    """glob_filter='*.py' → passed through to rg_search globs kwarg."""
    with patch.object(file_search, "rg_search", return_value=[]) as m:
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "validate_user", "glob_filter": "*.py"},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    globs_arg = m.call_args.kwargs.get("globs")
    assert globs_arg == ["*.py"]
    assert data["reason"] is None


@pytest.mark.asyncio
async def test_case_sensitive_default_insensitive(mock_plugin, write_files):
    """Default (no case_sensitive flag) → pattern prefixed with (?i) for case-insensitive."""
    with patch.object(file_search, "rg_search", return_value=[]) as m:
        await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "validate_user"}
        )
    patterns_arg = m.call_args.kwargs.get("patterns") or m.call_args.args[0]
    # (?i) prefix is prepended for case-insensitive
    assert patterns_arg == ["(?i)validate_user"]


@pytest.mark.asyncio
async def test_case_sensitive_true(mock_plugin, write_files):
    """case_sensitive=true → no (?i) prefix."""
    with patch.object(file_search, "rg_search", return_value=[]) as m:
        await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "validate_user", "case_sensitive": True},
        )
    patterns_arg = m.call_args.kwargs.get("patterns") or m.call_args.args[0]
    assert patterns_arg == ["validate_user"]


@pytest.mark.asyncio
async def test_regex(mock_plugin, write_files):
    """regex=true with valid regex → pattern NOT re.escape'd."""
    with patch.object(file_search, "rg_search", return_value=[]) as m:
        await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": r"validate_\w+", "regex": True},
        )
    patterns_arg = m.call_args.kwargs.get("patterns") or m.call_args.args[0]
    # regex=true → re.escape NOT applied, just (?i) prefix
    assert patterns_arg == ["(?i)validate_\\w+"]


@pytest.mark.asyncio
async def test_invalid_regex(mock_plugin):
    """regex=true with bad regex → invalid_pattern (preflight catches re.error)."""
    # To trigger the preflight validation, we re.compile(pattern) before calling rg.
    # The handler should catch re.error and return invalid_pattern.
    with patch.object(file_search, "rg_search") as m:
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "[unclosed", "regex": True},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN
    m.assert_not_called()


@pytest.mark.asyncio
async def test_path_unsafe_filter(mock_plugin):
    """path_filter with '..' → path_unsafe_filter; rg_search NOT called."""
    with patch.object(file_search, "rg_search") as m:
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "foo", "path_filter": "../etc"},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.PATH_UNSAFE_FILTER
    m.assert_not_called()


@pytest.mark.asyncio
async def test_worktree_invalid(mock_plugin):
    """worktree that doesn't exist → worktree_invalid."""
    with patch.object(file_search, "rg_search") as m:
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            worktree="/nonexistent/path/that/does/not/exist",
            body={"pattern": "foo"},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.WORKTREE_INVALID
    m.assert_not_called()


# ── Tests: python_ripgrep-specific failure modes ───────────────


@pytest.mark.asyncio
async def test_python_ripgrep_import_error(mock_plugin, write_files):
    """ImportError on rg_search → SEARCH_UNAVAILABLE."""
    # Simulate the library not being installed: import inside the function fails.
    # Easiest way: have rg_search raise ImportError when called.
    with patch.object(
        file_search, "rg_search", side_effect=ImportError("no python_ripgrep")
    ):
        result = await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "validate_user"}
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.SEARCH_UNAVAILABLE


@pytest.mark.asyncio
async def test_python_ripgrep_timeout(mock_plugin, write_files):
    """rg_search takes > timeout → SEARCH_TIMEOUT (asyncio.wait_for raises TimeoutError).

    实现: 把 file_search.SEARCH_TIMEOUT_SECONDS 临时改成 0.001s,让真实
    asyncio.wait_for 自然触发 TimeoutError(避免 patch asyncio.wait_for
    这种全局副作用影响 preflight 里的 git 调用)。
    """
    import time as _time

    def _slow(*_args, **_kwargs):
        # Sleep > 0.001s 即可触发 wait_for timeout。
        _time.sleep(0.5)
        return []

    with patch.object(file_search, "rg_search", side_effect=_slow):
        with patch.object(file_search, "SEARCH_TIMEOUT_SECONDS", 0.001):
            result = await file_search.handle(
                mock_plugin, umo="test:umo", body={"pattern": "validate_user"}
            )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.SEARCH_TIMEOUT


@pytest.mark.asyncio
async def test_python_ripgrep_other_exception(mock_plugin, write_files):
    """Generic exception in rg_search → SEARCH_UNAVAILABLE (defensive)."""
    with patch.object(file_search, "rg_search", side_effect=RuntimeError("boom")):
        result = await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "validate_user"}
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.SEARCH_UNAVAILABLE


# ── Tests: path resolution & response shape ─────────────────────


@pytest.mark.asyncio
async def test_path_relative_to_directory(mock_plugin, write_files):
    """rg_search returns absolute paths; handler must convert to relative POSIX."""
    tmp_path = Path(mock_plugin.get_loaded_project.return_value["directory"])
    abs_path = str(tmp_path / "auth.py")
    matches = [_fmt(abs_path, 1, "def validate_user(token: str) -> bool:")]
    with patch.object(file_search, "rg_search", return_value=matches):
        result = await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "validate_user"}
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    # Path should be relative to directory, POSIX-style (forward slashes).
    assert data["results"][0]["path"] == "auth.py"


@pytest.mark.asyncio
async def test_truncated_flag_when_more_than_max_results(mock_plugin, write_files):
    """If rg_search returns more than max_results, truncated=True and result is clamped."""
    # Build 5 fake matches
    tmp_path = Path(mock_plugin.get_loaded_project.return_value["directory"])
    matches = [
        _fmt(str(tmp_path / "auth.py"), i, f"line {i} validate_user")
        for i in range(1, 6)
    ]
    with patch.object(file_search, "rg_search", return_value=matches):
        result = await file_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "validate_user", "max_results": 3},
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["result_count"] == 3
    assert data["truncated"] is True
    assert data["max_results"] == 3


@pytest.mark.asyncio
async def test_no_backend_field_in_success_response(mock_plugin, write_files):
    """Success response should NOT include 'backend' (single backend now)."""
    with patch.object(file_search, "rg_search", return_value=[]):
        result = await file_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "validate_user"}
        )
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert "backend" not in data


# Author: spcode_impl, 2026-07-02 15:50
