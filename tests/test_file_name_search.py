"""Tests for POST /spcode/file-name-search.

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md §5.6

The endpoint matches files by **basename** (not content), using
``rg --files`` for the candidate list (respects ``.gitignore``, fast) and
then filtering by user pattern (substring or regex, case-sensitive or not).

All tests mock ``subprocess.run`` at the module level
(``tools.webapi.file_name_search.subprocess.run``) so the test suite does
not require an actual ``rg`` binary in PATH and stays deterministic.
"""

from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.webapi import file_name_search
from tools.webapi._helpers import ReasonCode


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def mock_plugin(tmp_path: Path) -> MagicMock:
    """Plugin mock with worktree = tmp_path (a real git repo for preflight)."""
    import subprocess as _sp

    _sp.run(
        ["git", "init", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / "init.txt").write_text("init", encoding="utf-8")
    _sp.run(
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
    _sp.run(
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
    plugin.get_loaded_project.return_value = {
        "directory": str(tmp_path),
        "loaded_at": 1.0,
    }
    return plugin


@pytest.fixture
def write_files(tmp_path: Path) -> None:
    """Write a representative tree of files for filename matching.

    Layout::

        tmp_path/
        ├── src/
        │   └── api/
        │       ├── auth.py
        │       ├── auth_helper.py
        │       └── user.py
        ├── tests/
        │   ├── auth_test.py
        │   └── user_test.py
        ├── README.md
        └── AUTH.md
    """
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "api" / "auth.py").write_text(
        "def validate_user():\n    return True\n", encoding="utf-8"
    )
    (tmp_path / "src" / "api" / "auth_helper.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "api" / "user.py").write_text(
        "def user():\n    return 2\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "auth_test.py").write_text(
        "def test_auth():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "user_test.py").write_text(
        "def test_user():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Readme\n", encoding="utf-8")
    (tmp_path / "AUTH.md").write_text("# AUTH (uppercase)\n", encoding="utf-8")


def _make_completed_proc(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> MagicMock:
    """Build a subprocess.CompletedProcess-like mock."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def _rg_files_output(*paths: str) -> str:
    """Build a ``rg --files`` stdout string (one path per line, POSIX)."""
    return "\n".join(paths)


# ── Tests: basic match ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_match(mock_plugin, write_files):
    """Pattern 'auth' (default case-insensitive substring) matches auth*.py + AUTH.md."""
    paths = [
        "src/api/auth.py",
        "src/api/auth_helper.py",
        "src/api/user.py",
        "tests/auth_test.py",
        "tests/user_test.py",
        "README.md",
        "AUTH.md",
    ]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth"},
        )
    data = result["data"] if hasattr(result, "__getitem__") else result["data"]
    assert data["reason"] is None
    matched = [r["path"] for r in data["results"]]
    assert "src/api/auth.py" in matched
    assert "src/api/auth_helper.py" in matched
    assert "tests/auth_test.py" in matched
    assert "AUTH.md" in matched  # case-insensitive default
    # user.py / README.md / user_test.py should not match
    assert "src/api/user.py" not in matched
    assert "README.md" not in matched
    assert "tests/user_test.py" not in matched


@pytest.mark.asyncio
async def test_no_match(mock_plugin, write_files):
    """No matches → empty results, reason=None."""
    paths = ["src/api/auth.py", "README.md"]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "nonexistent_xyz"},
        )
    data = result["data"]
    assert data["reason"] is None
    assert data["results"] == []
    assert data["result_count"] == 0
    assert data["truncated"] is False


# ── Tests: case sensitivity ──────────────────────────────────────


@pytest.mark.asyncio
async def test_case_sensitive_excludes_uppercase(mock_plugin, write_files):
    """case_sensitive=true → 'auth' excludes AUTH.md (different case)."""
    paths = [
        "src/api/auth.py",
        "AUTH.md",
    ]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "case_sensitive": True},
        )
    data = result["data"]
    matched = [r["path"] for r in data["results"]]
    assert "src/api/auth.py" in matched
    assert "AUTH.md" not in matched


@pytest.mark.asyncio
async def test_case_insensitive_default(mock_plugin, write_files):
    """Default → case-insensitive (matches both auth and AUTH)."""
    paths = ["src/api/auth.py", "AUTH.md"]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth"},
        )
    data = result["data"]
    matched = [r["path"] for r in data["results"]]
    assert "src/api/auth.py" in matched
    assert "AUTH.md" in matched


# ── Tests: regex vs substring ────────────────────────────────────


@pytest.mark.asyncio
async def test_regex_mode(mock_plugin, write_files):
    """regex=true → 'auth.*\\.py$' matches auth.py and auth_helper.py."""
    paths = [
        "src/api/auth.py",
        "src/api/auth_helper.py",
        "AUTH.md",  # doesn't end in .py → must not match
    ]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": r"auth.*\.py$", "regex": True},
        )
    data = result["data"]
    matched = [r["path"] for r in data["results"]]
    assert "src/api/auth.py" in matched
    assert "src/api/auth_helper.py" in matched
    assert "AUTH.md" not in matched


@pytest.mark.asyncio
async def test_substring_default_escapes_regex(mock_plugin, write_files):
    """regex=False (default) → '.py' matches as literal '.'; AUTH.md is excluded."""
    paths = [
        "src/api/auth.py",
        "AUTH.md",  # 'auth' substring → would match default but
        # we use '.py' literal pattern here.
    ]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": ".py"},
        )
    data = result["data"]
    matched = [r["path"] for r in data["results"]]
    # '.py' as escaped literal matches basename 'auth.py' (ends with .py) but
    # not 'AUTH.md'.
    assert "src/api/auth.py" in matched
    assert "AUTH.md" not in matched


# ── Tests: glob filter ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_glob_filter_passed_to_rg(mock_plugin, write_files):
    """glob_filter='*.py' → rg argv contains '-g *.py'."""
    paths = ["src/api/auth.py", "README.md"]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ) as m:
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "glob_filter": "*.py"},
        )
    data = result["data"]
    argv = m.call_args.args[0]
    assert "-g" in argv
    g_idx = argv.index("-g")
    assert argv[g_idx + 1] == "*.py"
    assert data["reason"] is None


# ── Tests: max_results ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_results_clamp_to_two(mock_plugin, write_files):
    """max_results=2 with 5 matches → 2 returned, truncated=True."""
    paths = [
        "src/api/auth.py",
        "src/api/auth_helper.py",
        "tests/auth_test.py",
        "tests/auth_test2.py",
        "AUTH.md",
    ]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "max_results": 2},
        )
    data = result["data"]
    assert data["result_count"] == 2
    assert data["truncated"] is True
    assert data["max_results"] == 2
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_truncated_flag(mock_plugin, write_files):
    """truncated flag is True only when len(matches) > max_results."""
    paths = [f"file_match_{i}.py" for i in range(5)]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "file_match", "max_results": 2},
        )
    data = result["data"]
    assert data["truncated"] is True
    assert data["result_count"] == 2


@pytest.mark.asyncio
async def test_no_truncation_when_under_limit(mock_plugin, write_files):
    """truncated=False when matches ≤ max_results."""
    paths = ["src/api/auth.py", "AUTH.md"]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "max_results": 100},
        )
    data = result["data"]
    assert data["truncated"] is False


# ── Tests: path_filter ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_path_filter_restricts_subdir(mock_plugin, write_files):
    """path_filter='src/api/' → only files under that subdir are searched."""
    paths = [
        "src/api/auth.py",
        "tests/auth_test.py",  # outside path_filter
    ]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ) as m:
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "path_filter": "src/api/"},
        )
    data = result["data"]
    matched = [r["path"] for r in data["results"]]
    assert "src/api/auth.py" in matched
    # tests/auth_test.py is outside the subdir — it should not be in the
    # results even though it would match the pattern.
    assert "tests/auth_test.py" not in matched
    # rg argv's last positional arg should be the subdir (not the repo root).
    argv = m.call_args.args[0]
    last_arg = argv[-1]
    assert "src" in last_arg and "api" in last_arg


# ── Tests: input validation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_pattern_returns_invalid(mock_plugin):
    """Empty pattern → INVALID_PATTERN; subprocess.run NOT called."""
    with patch.object(file_name_search.subprocess, "run") as m:
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": ""}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN
    m.assert_not_called()


@pytest.mark.asyncio
async def test_pattern_too_long_returns_too_long(mock_plugin):
    """257 chars → PATTERN_TOO_LONG; subprocess.run NOT called."""
    with patch.object(file_name_search.subprocess, "run") as m:
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "a" * 257}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.PATTERN_TOO_LONG
    m.assert_not_called()


@pytest.mark.asyncio
async def test_pattern_with_newline_returns_invalid(mock_plugin):
    """Pattern with \\n → INVALID_PATTERN (multi-line search not supported)."""
    with patch.object(file_name_search.subprocess, "run") as m:
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "foo\nbar"}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN
    m.assert_not_called()


@pytest.mark.asyncio
async def test_path_unsafe_filter_rejected(mock_plugin):
    """path_filter='../etc' → PATH_UNSAFE_FILTER; subprocess.run NOT called."""
    with patch.object(file_name_search.subprocess, "run") as m:
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "foo", "path_filter": "../etc"},
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.PATH_UNSAFE_FILTER
    m.assert_not_called()


# ── Tests: rg-specific failure modes ────────────────────────────


@pytest.mark.asyncio
async def test_rg_not_found_returns_unavailable(mock_plugin, write_files):
    """FileNotFoundError (rg missing) → SEARCH_UNAVAILABLE."""
    with patch.object(
        file_name_search.subprocess,
        "run",
        side_effect=FileNotFoundError("rg not installed"),
    ):
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "auth"}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.SEARCH_UNAVAILABLE


@pytest.mark.asyncio
async def test_rg_timeout_returns_search_timeout(mock_plugin, write_files):
    """subprocess.run sleeps > SEARCH_TIMEOUT_SECONDS → SEARCH_TIMEOUT.

    Implementation: 把 ``file_name_search.SEARCH_TIMEOUT_SECONDS`` 临时改成
    ``0.001`` s,让真实 ``asyncio.wait_for`` 自然触发 ``TimeoutError``(避免
    patch 全局 ``asyncio.wait_for`` 影响 preflight 的 git 调用)。
    """
    import time as _time

    def _slow(*_args, **_kwargs):
        _time.sleep(0.5)
        return _make_completed_proc()

    with patch.object(file_name_search.subprocess, "run", side_effect=_slow):
        with patch.object(file_name_search, "SEARCH_TIMEOUT_SECONDS", 0.001):
            result = await file_name_search.handle(
                mock_plugin, umo="test:umo", body={"pattern": "auth"}
            )
    data = result["data"]
    assert data["reason"] == ReasonCode.SEARCH_TIMEOUT


# ── Tests: preflight failure paths ──────────────────────────────


@pytest.mark.asyncio
async def test_no_project_loaded_returns_no_project(mock_plugin):
    """No project loaded + umo=None → NO_PROJECT_LOADED; rg NOT called."""
    # Don't pass umo so the preflight tries the state fallback (which the
    # autouse conftest fixture reset to empty).
    with patch.object(file_name_search.subprocess, "run") as m:
        result = await file_name_search.handle(
            mock_plugin, umo=None, body={"pattern": "auth"}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.NO_PROJECT_LOADED
    m.assert_not_called()


@pytest.mark.asyncio
async def test_worktree_invalid_returns_worktree_invalid(mock_plugin):
    """worktree that doesn't exist → WORKTREE_INVALID; rg NOT called."""
    with patch.object(file_name_search.subprocess, "run") as m:
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            worktree="/nonexistent/path/that/does/not/exist",
            body={"pattern": "foo"},
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.WORKTREE_INVALID
    m.assert_not_called()


# ── Tests: response shape ────────────────────────────────────────


@pytest.mark.asyncio
async def test_result_shape(mock_plugin, write_files):
    """Each result has exactly {path, name, type, size} with correct types."""
    paths = ["src/api/auth.py"]
    with patch.object(
        file_name_search.subprocess,
        "run",
        return_value=_make_completed_proc(_rg_files_output(*paths)),
    ):
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "auth"}
        )
    data = result["data"]
    assert len(data["results"]) == 1
    r = data["results"][0]
    assert set(r.keys()) == {"path", "name", "type", "size"}
    assert r["path"] == "src/api/auth.py"
    assert r["name"] == "auth.py"
    assert r["type"] == "file"
    assert isinstance(r["size"], int)
    assert r["size"] > 0  # file has content
    # No line/column/snippet fields (path-level match only).
    for forbidden in ("line", "column", "snippet"):
        assert forbidden not in r


# Author: spcode_impl, 2026-07-02 16:35
