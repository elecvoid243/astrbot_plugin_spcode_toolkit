"""Tests for POST /spcode/file-write (generic text-file overwrite).

2026-07-17: workspace file-browser edit feature. Mirrors the
test_docs_crud.py pattern (real git repo in tmp_path + _make_plugin).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import file_write as _fw

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin() -> Any:
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


# ── happy path ──────────────────────────────────────────────────


async def test_overwrites_existing_code_file(plugin: Any, tmp_path: Path) -> None:
    """Arbitrary extensions (not just .md) are accepted."""
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('old')\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "src/main.py", "content": "print('new')\n"},
    )

    assert result["data"]["saved"] is True
    assert result["data"]["path"] == "src/main.py"
    assert result["data"]["size"] == len("print('new')\n".encode("utf-8"))
    assert (tmp_path / "src" / "main.py").read_text(encoding="utf-8") == (
        "print('new')\n"
    )


async def test_backslash_path_normalized(plugin: Any, tmp_path: Path) -> None:
    """Windows-style separators pass the 4-step defense (normalized)."""
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("old", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": r"a.txt", "content": "new"},
    )

    assert result["data"]["saved"] is True
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "new"


# ── target must exist (edit, not create) ─────────────────────────


async def test_missing_file_rejected_and_not_created(
    plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "ghost.txt", "content": "x"},
    )

    assert result["data"]["reason"] == "file_not_found"
    assert not (tmp_path / "ghost.txt").exists()


async def test_directory_target_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "docs", "content": "x"},
    )

    assert result["data"]["reason"] == "file_not_found"


# ── path / body validation ───────────────────────────────────────


async def test_dotdot_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "../escape.txt", "content": "x"},
    )

    assert result["data"]["reason"] == "path_unsafe"


async def test_git_internal_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": ".git/HEAD", "content": "x"},
    )

    assert result["data"]["reason"] == "path_unsafe"


async def test_non_string_path_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": 123, "content": "x"},
    )

    assert result["data"]["reason"] == "invalid_body"


async def test_empty_path_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "  ", "content": "x"},
    )

    assert result["data"]["reason"] == "invalid_param"


async def test_newline_in_path_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "a\nb.txt", "content": "x"},
    )

    assert result["data"]["reason"] == "invalid_param"


async def test_non_string_content_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "README.md", "content": None},
    )

    assert result["data"]["reason"] == "invalid_body"


async def test_missing_body_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(plugin, umo="u:m", body=None)

    assert result["data"]["reason"] == "invalid_body"


async def test_oversized_content_rejected(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fw.handle(
        plugin,
        umo="u:m",
        body={"path": "README.md", "content": "x" * (2 * 1024 * 1024 + 1)},
    )

    assert result["data"]["reason"] == "invalid_param"
