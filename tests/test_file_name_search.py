"""Tests for POST /spcode/file-name-search.

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md §5.6

The endpoint matches files by **basename** (not content), using
``python_ripgrep.files()`` for the candidate list (the rg binary is
bundled inside the python_ripgrep.pyd module, NOT on system PATH; we
must use the library, not ``subprocess.run(["rg", ...])``).

Pattern / regex / case_sensitive filtering is done in **Python**, after
the file list is returned by ``rg_files``.

All tests mock ``tools.webapi.file_name_search.rg_files`` at module
level so the test suite does not require real filesystem traversal
beyond a tmp_path with a few files for the per-result ``type``/``size``
probes.
"""

from __future__ import annotations
import os
import time as _time
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


def _abs_paths(directory: str | os.PathLike[str], *rel: str) -> list[str]:
    """Build a list of absolute paths that mimics ``python_ripgrep.files()``.

    On Windows the real library returns native backslash paths; we mirror
    that with ``os.path.join`` so the test exercises the same code path as
    production. ``os.path.relpath`` + ``.replace(os.sep, "/")`` in the
    handler is what converts them back to forward-slash repo-relative.
    """
    return [os.path.join(str(directory), p) for p in rel]


# ── Tests: basic match ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_match(mock_plugin, write_files, tmp_path):
    """Pattern 'auth' (default case-insensitive substring) matches auth*.py + AUTH.md."""
    abs_paths = _abs_paths(
        tmp_path,
        "src/api/auth.py",
        "src/api/auth_helper.py",
        "src/api/user.py",
        "tests/auth_test.py",
        "tests/user_test.py",
        "README.md",
        "AUTH.md",
    )
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth"},
        )
    data = result["data"]
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
async def test_no_match(mock_plugin, write_files, tmp_path):
    """No matches → empty results, reason=None."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py", "README.md")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
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
async def test_case_sensitive_excludes_uppercase(mock_plugin, write_files, tmp_path):
    """case_sensitive=true → 'auth' excludes AUTH.md (different case)."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py", "AUTH.md")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
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
async def test_case_insensitive_default(mock_plugin, write_files, tmp_path):
    """Default → case-insensitive (matches both auth and AUTH)."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py", "AUTH.md")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
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
async def test_regex_mode(mock_plugin, write_files, tmp_path):
    """regex=true → 'auth.*\\.py$' matches auth.py and auth_helper.py."""
    abs_paths = _abs_paths(
        tmp_path,
        "src/api/auth.py",
        "src/api/auth_helper.py",
        "AUTH.md",  # doesn't end in .py → must not match
    )
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
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
async def test_substring_default_escapes_regex(mock_plugin, write_files, tmp_path):
    """regex=False (default) → '.py' matches as literal '.'; AUTH.md is excluded."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py", "AUTH.md")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
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
async def test_glob_filter_passed_to_python_ripgrep(mock_plugin, write_files, tmp_path):
    """glob_filter='*.py' → rg_files is called with globs=['*.py']."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py", "README.md")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths) as m:
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "glob_filter": "*.py"},
        )
    data = result["data"]
    assert data["reason"] is None
    # rg_files(patterns=[], paths=[search_path], globs=[glob_filter])
    kwargs = m.call_args.kwargs
    assert m.call_args.args == ()  # no positional args
    assert kwargs.get("globs") == ["*.py"]
    # patterns=[]  → list all (no content regex filter)
    assert kwargs.get("patterns") == []


# ── Tests: max_results ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_results_clamp_to_two(mock_plugin, write_files, tmp_path):
    """max_results=2 with 5 matches → 2 returned, truncated=True."""
    abs_paths = _abs_paths(
        tmp_path,
        "src/api/auth.py",
        "src/api/auth_helper.py",
        "tests/auth_test.py",
        "tests/auth_test2.py",
        "AUTH.md",
    )
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
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
async def test_truncated_flag(mock_plugin, write_files, tmp_path):
    """truncated flag is True only when len(matches) > max_results."""
    abs_paths = _abs_paths(tmp_path, *[f"file_match_{i}.py" for i in range(5)])
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "file_match", "max_results": 2},
        )
    data = result["data"]
    assert data["truncated"] is True
    assert data["result_count"] == 2


@pytest.mark.asyncio
async def test_no_truncation_when_under_limit(mock_plugin, write_files, tmp_path):
    """truncated=False when matches ≤ max_results."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py", "AUTH.md")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "max_results": 100},
        )
    data = result["data"]
    assert data["truncated"] is False


# ── Tests: path_filter ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_path_filter_restricts_subdir(mock_plugin, write_files, tmp_path):
    """path_filter='src/api/' → rg_files gets the subdir as paths; results scoped."""
    abs_paths = _abs_paths(
        tmp_path,
        "src/api/auth.py",
        "tests/auth_test.py",  # returned by rg_files for test purposes
    )
    with patch.object(file_name_search, "rg_files", return_value=abs_paths) as m:
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "auth", "path_filter": "src/api/"},
        )
    data = result["data"]
    matched = [r["path"] for r in data["results"]]
    assert "src/api/auth.py" in matched
    # Handler's defense-in-depth filter: tests/auth_test.py is outside the
    # path_filter scope, so it must not be in the results.
    assert "tests/auth_test.py" not in matched
    # rg_files should be called with paths = <tmp_path>/src/api/ (subdir),
    # not <tmp_path> (repo root).
    kwargs = m.call_args.kwargs
    assert kwargs.get("paths") is not None
    paths_arg = kwargs["paths"]
    assert len(paths_arg) == 1
    assert "src" in paths_arg[0] and "api" in paths_arg[0]


# ── Tests: input validation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_pattern_returns_invalid(mock_plugin):
    """Empty pattern → INVALID_PATTERN; rg_files NOT called."""
    with patch.object(file_name_search, "rg_files") as m:
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": ""}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN
    m.assert_not_called()


@pytest.mark.asyncio
async def test_pattern_too_long_returns_too_long(mock_plugin):
    """257 chars → PATTERN_TOO_LONG; rg_files NOT called."""
    with patch.object(file_name_search, "rg_files") as m:
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "a" * 257}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.PATTERN_TOO_LONG
    m.assert_not_called()


@pytest.mark.asyncio
async def test_pattern_with_newline_returns_invalid(mock_plugin):
    """Pattern with \\n → INVALID_PATTERN (multi-line search not supported)."""
    with patch.object(file_name_search, "rg_files") as m:
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "foo\nbar"}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.INVALID_PATTERN
    m.assert_not_called()


@pytest.mark.asyncio
async def test_path_unsafe_filter_rejected(mock_plugin):
    """path_filter='../etc' → PATH_UNSAFE_FILTER; rg_files NOT called."""
    with patch.object(file_name_search, "rg_files") as m:
        result = await file_name_search.handle(
            mock_plugin,
            umo="test:umo",
            body={"pattern": "foo", "path_filter": "../etc"},
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.PATH_UNSAFE_FILTER
    m.assert_not_called()


# ── Tests: rg_files failure modes ───────────────────────────────


@pytest.mark.asyncio
async def test_rg_files_timeout_returns_search_timeout(mock_plugin, write_files):
    """rg_files() sleeps past SEARCH_TIMEOUT_SECONDS → SEARCH_TIMEOUT.

    Implementation: 把 ``file_name_search.SEARCH_TIMEOUT_SECONDS`` 临时改成
    ``0.001`` s,让真实 ``asyncio.wait_for`` 自然触发 ``TimeoutError``(避免
    patch 全局 ``asyncio.wait_for`` 影响 preflight 的 git 调用)。
    """

    def _slow(*_args, **_kwargs):
        _time.sleep(0.5)
        return []

    with patch.object(file_name_search, "rg_files", side_effect=_slow):
        with patch.object(file_name_search, "SEARCH_TIMEOUT_SECONDS", 0.001):
            result = await file_name_search.handle(
                mock_plugin, umo="test:umo", body={"pattern": "auth"}
            )
    data = result["data"]
    assert data["reason"] == ReasonCode.SEARCH_TIMEOUT


@pytest.mark.asyncio
async def test_python_ripgrep_import_error_returns_unavailable(mock_plugin):
    """rg_files() raises ImportError → SEARCH_UNAVAILABLE (library missing)."""
    with patch.object(
        file_name_search, "rg_files", side_effect=ImportError("no python_ripgrep")
    ):
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "auth"}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.SEARCH_UNAVAILABLE


@pytest.mark.asyncio
async def test_rg_files_generic_exception_returns_unavailable(mock_plugin):
    """rg_files() raises generic Exception → SEARCH_UNAVAILABLE (defensive)."""
    with patch.object(file_name_search, "rg_files", side_effect=RuntimeError("kaboom")):
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "auth"}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.SEARCH_UNAVAILABLE


# ── Tests: preflight failure paths ──────────────────────────────


@pytest.mark.asyncio
async def test_no_project_loaded_returns_no_project(mock_plugin):
    """No project loaded + umo=None → NO_PROJECT_LOADED; rg_files NOT called."""
    # Don't pass umo so the preflight tries the state fallback (which the
    # autouse conftest fixture reset to empty).
    with patch.object(file_name_search, "rg_files") as m:
        result = await file_name_search.handle(
            mock_plugin, umo=None, body={"pattern": "auth"}
        )
    data = result["data"]
    assert data["reason"] == ReasonCode.NO_PROJECT_LOADED
    m.assert_not_called()


@pytest.mark.asyncio
async def test_worktree_invalid_returns_worktree_invalid(mock_plugin):
    """worktree that doesn't exist → WORKTREE_INVALID; rg_files NOT called."""
    with patch.object(file_name_search, "rg_files") as m:
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
async def test_result_shape(mock_plugin, write_files, tmp_path):
    """Each result has exactly {path, name, type, size} with correct types."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths):
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


# ── Tests: library integration ───────────────────────────────────


@pytest.mark.asyncio
async def test_uses_python_ripgrep_files_not_subprocess(
    mock_plugin, write_files, tmp_path
):
    """rg_files is invoked with the documented signature: patterns=[], paths=[dir], globs=[...]."""
    abs_paths = _abs_paths(tmp_path, "src/api/auth.py")
    with patch.object(file_name_search, "rg_files", return_value=abs_paths) as m:
        await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "auth"}
        )
    # The handler must NOT spawn an rg subprocess anymore (rg is not on PATH
    # in the AstrBot runtime; the .pyd bundles it internally).
    kwargs = m.call_args.kwargs
    assert kwargs.get("patterns") == []
    assert kwargs.get("paths") is not None
    # No line_number / max_count / etc. — those would be for content search.
    assert "line_number" not in kwargs


@pytest.mark.asyncio
async def test_path_forward_slash_normalization(mock_plugin, write_files, tmp_path):
    """rg_files returns native backslash paths; response 'path' is forward-slash relative."""
    # Real python_ripgrep.files() on Windows returns paths with backslashes.
    raw = [
        str(tmp_path) + "\\src\\api\\auth.py",
        str(tmp_path) + "\\AUTH.md",
    ]
    with patch.object(file_name_search, "rg_files", return_value=raw):
        result = await file_name_search.handle(
            mock_plugin, umo="test:umo", body={"pattern": "auth"}
        )
    data = result["data"]
    assert data["reason"] is None
    paths = [r["path"] for r in data["results"]]
    # No backslashes in the response path field (POSIX-style for cross-platform
    # consistency with the rest of the API).
    for p in paths:
        assert "\\" not in p, f"backslash in response path: {p!r}"
    assert "src/api/auth.py" in paths
    assert "AUTH.md" in paths


# Author: spcode_impl, 2026-07-02 17:40
