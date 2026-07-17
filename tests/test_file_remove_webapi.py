"""Tests for POST /spcode/file-remove (delete any repo file).

作者: elecvoid243, 2026-07-18
Named *_webapi to distinguish from tests/test_file_remove.py, which
covers the LLM-facing function tool. Mirrors the test_file_write.py
pattern (real git repo in tmp_path + _make_plugin).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import file_remove as _frm

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


async def test_removes_code_file(plugin: Any, tmp_path: Path) -> None:
    """Arbitrary extensions (not just .md) can be deleted."""
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "dead.py").write_text("pass\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _frm.handle(
        plugin,
        umo="u:m",
        body={"path": "src/dead.py"},
    )

    assert result["data"]["reason"] is None
    assert result["data"]["deleted"] is True
    assert result["data"]["path"] == "src/dead.py"
    assert not (tmp_path / "src" / "dead.py").exists()


async def test_removes_untracked_file(plugin: Any, tmp_path: Path) -> None:
    """Untracked (never committed) files are deletable too."""
    _init_git_repo(tmp_path)
    (tmp_path / "scratch.txt").write_text("tmp", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _frm.handle(
        plugin,
        umo="u:m",
        body={"path": "scratch.txt"},
    )

    assert result["data"]["deleted"] is True
    assert not (tmp_path / "scratch.txt").exists()


# ── validation failures ─────────────────────────────────────────


async def test_rejects_invalid_body(plugin: Any) -> None:
    result = await _frm.handle(plugin, umo="u:m", body=None)
    assert result["data"]["reason"] == "invalid_body"


async def test_rejects_empty_path(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _frm.handle(plugin, umo="u:m", body={"path": "  "})
    assert result["data"]["reason"] == "invalid_param"


async def test_rejects_missing_file(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _frm.handle(plugin, umo="u:m", body={"path": "ghost.txt"})
    assert result["data"]["reason"] == "file_not_found"


async def test_rejects_directory(plugin: Any, tmp_path: Path) -> None:
    """Directories are never deleted by this endpoint."""
    _init_git_repo(tmp_path)
    (tmp_path / "subdir").mkdir()
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _frm.handle(plugin, umo="u:m", body={"path": "subdir"})
    assert result["data"]["reason"] == "git_error"
    assert (tmp_path / "subdir").is_dir()


async def test_rejects_path_traversal(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _frm.handle(plugin, umo="u:m", body={"path": "../outside.txt"})
    assert result["data"]["reason"] == "path_unsafe"


async def test_rejects_git_internals(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _frm.handle(plugin, umo="u:m", body={"path": ".git/config"})
    assert result["data"]["reason"] == "path_unsafe"
