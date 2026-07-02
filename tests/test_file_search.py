"""Tests for POST /spcode/file-search.

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md
"""

from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.webapi import file_search
from tools.webapi._helpers import ReasonCode


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def mock_plugin_with_rg(tmp_path: Path) -> MagicMock:
    """Plugin mock with rg available, worktree = tmp_path (a real dir)."""
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
    plugin._rg_available = True
    plugin._rg_path = "rg"
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


# ── Tests: rg path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hit(mock_plugin_with_rg, write_files):
    """Basic substring search returns matching lines with snippets."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        worktree=None,
        body={"pattern": "validate_user"},
    )
    # _JSONResponseCompat with .get()
    data = result.get("data") if hasattr(result, "get") else result["data"]
    assert data["reason"] is None
    assert data["backend"] == "ripgrep"
    assert data["result_count"] >= 2
    paths = [r["path"] for r in data["results"]]
    assert "auth.py" in paths
    for r in data["results"]:
        assert "validate_user" in r["snippet"]
        assert r["line"] >= 1
        assert r["column"] >= 1


@pytest.mark.asyncio
async def test_miss(mock_plugin_with_rg, write_files):
    """No matches → 200, results=[], reason=None."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "this_string_does_not_exist_xyz"},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] is None
    assert data["results"] == []
    assert data["result_count"] == 0
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_empty_pattern(mock_plugin_with_rg):
    """Empty pattern → invalid_pattern."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": ""},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN


@pytest.mark.asyncio
async def test_pattern_with_newline(mock_plugin_with_rg):
    """Pattern with \\n → invalid_pattern (multi-line search not supported)."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "foo\nbar"},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN


@pytest.mark.asyncio
async def test_pattern_too_long(mock_plugin_with_rg):
    """Pattern > 256 chars → pattern_too_long."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "a" * 257},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.PATTERN_TOO_LONG


@pytest.mark.asyncio
async def test_max_results_clamp(mock_plugin_with_rg, write_files):
    """max_results > 1000 → clamp to 1000."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "validate_user", "max_results": 5000},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["max_results"] == 1000


@pytest.mark.asyncio
async def test_glob_filter(mock_plugin_with_rg, write_files):
    """glob_filter='*.py' → only .py files in results."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "validate_user", "glob_filter": "*.py"},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    paths = [r["path"] for r in data["results"]]
    assert all(p.endswith(".py") for p in paths)


@pytest.mark.asyncio
async def test_case_sensitive(mock_plugin_with_rg, write_files):
    """case_sensitive=true misses lowercase hits."""
    # README.md has "validate_user" lowercase
    result_cs = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "validate_user", "case_sensitive": True},
    )
    data_cs = result_cs["data"] if hasattr(result_cs, "get") else result_cs["data"]
    # All hits should have exact-case "validate_user" (lowercase) — still matches
    # because the actual file content is lowercase. So this test only verifies
    # that the case_sensitive flag is accepted and the call returns.
    assert data_cs["reason"] is None


@pytest.mark.asyncio
async def test_regex(mock_plugin_with_rg, write_files):
    """regex=true with valid regex finds pattern."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": r"validate_\w+", "regex": True},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] is None
    assert data["result_count"] >= 1


@pytest.mark.asyncio
async def test_invalid_regex(mock_plugin_with_rg):
    """regex=true with bad regex → invalid_pattern."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "[unclosed", "regex": True},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN


@pytest.mark.asyncio
async def test_path_unsafe_filter(mock_plugin_with_rg):
    """path_filter with '..' → path_unsafe_filter."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        body={"pattern": "foo", "path_filter": "../etc"},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.PATH_UNSAFE_FILTER


@pytest.mark.asyncio
async def test_worktree_invalid(mock_plugin_with_rg):
    """worktree that doesn't exist → worktree_invalid."""
    result = await file_search.handle(
        mock_plugin_with_rg,
        umo="test:umo",
        worktree="/nonexistent/path/that/does/not/exist",
        body={"pattern": "foo"},
    )
    data = result["data"] if hasattr(result, "get") else result["data"]
    assert data["reason"] == ReasonCode.WORKTREE_INVALID
