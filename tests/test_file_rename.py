"""Tests for POST /spcode/file-rename (same-dir rename, any file type).

作者: elecvoid243, 2026-07-18
Mirrors the test_file_write.py pattern (real git repo in tmp_path +
_make_plugin).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import file_rename as _fr

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


async def test_renames_code_file_same_dir(plugin: Any, tmp_path: Path) -> None:
    """Arbitrary extensions (not just .md) can be renamed."""
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "old.py").write_text("x = 1\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fr.handle(
        plugin,
        umo="u:m",
        body={"path": "src/old.py", "new_name": "new.py"},
    )

    assert result["data"]["reason"] is None
    assert result["data"]["renamed"] is True
    assert result["data"]["path"] == "src/old.py"
    assert result["data"]["new_path"] == "src/new.py"
    assert not (tmp_path / "src" / "old.py").exists()
    assert (tmp_path / "src" / "new.py").read_text(encoding="utf-8") == "x = 1\n"


async def test_renames_root_level_file(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fr.handle(
        plugin,
        umo="u:m",
        body={"path": ".gitignore", "new_name": ".gitignore.bak"},
    )

    assert result["data"]["renamed"] is True
    assert result["data"]["new_path"] == ".gitignore.bak"
    assert (tmp_path / ".gitignore.bak").exists()


async def test_backslash_path_normalized(plugin: Any, tmp_path: Path) -> None:
    """Windows-style separators are normalized before joining new_name."""
    _init_git_repo(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.txt").write_text("v", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _fr.handle(
        plugin,
        umo="u:m",
        body={"path": r"a\b.txt", "new_name": "c.txt"},
    )

    assert result["data"]["renamed"] is True
    assert result["data"]["new_path"] == "a/c.txt"
    assert (tmp_path / "a" / "c.txt").exists()


# ── validation failures ─────────────────────────────────────────


async def test_rejects_invalid_body(plugin: Any, tmp_path: Path) -> None:
    result = await _fr.handle(plugin, umo="u:m", body=None)
    assert result["data"]["reason"] == "invalid_body"


async def test_rejects_empty_new_name(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _fr.handle(
        plugin, umo="u:m", body={"path": "README.md", "new_name": "  "}
    )
    assert result["data"]["reason"] == "invalid_param"


async def test_rejects_new_name_with_separator(
    plugin: Any, tmp_path: Path
) -> None:
    """new_name must be a bare filename — separators mean cross-dir move."""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _fr.handle(
        plugin, umo="u:m", body={"path": "README.md", "new_name": "sub/x.md"}
    )
    assert result["data"]["reason"] == "path_unsafe"


async def test_rejects_same_name(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _fr.handle(
        plugin, umo="u:m", body={"path": "README.md", "new_name": "README.md"}
    )
    assert result["data"]["reason"] == "invalid_param"


async def test_rejects_missing_source(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _fr.handle(
        plugin, umo="u:m", body={"path": "ghost.txt", "new_name": "x.txt"}
    )
    assert result["data"]["reason"] == "file_not_found"


async def test_rejects_existing_target(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "taken.txt").write_text("t", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _fr.handle(
        plugin, umo="u:m", body={"path": "README.md", "new_name": "taken.txt"}
    )
    assert result["data"]["reason"] == "file_exists"


async def test_rejects_path_traversal(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    result = await _fr.handle(
        plugin, umo="u:m", body={"path": "../outside.txt", "new_name": "x.txt"}
    )
    assert result["data"]["reason"] == "path_unsafe"
