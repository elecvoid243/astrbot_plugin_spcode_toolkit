"""Tests for POST / PATCH / DELETE /spcode/docs HTTP endpoint.

Spec: docs/superpowers/specs/2026-07-11-document-manager-backend-design.md §3.2-§3.4
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import docs_crud as _dc

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


# ── POST ─────────────────────────────────────────────────────────


async def test_post_creates_new_file(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_post_docs(
        plugin, umo="u:m", body={"path": "docs/new.md", "content": "# hi\n"}
    )

    assert result["data"]["saved"] is True
    assert result["data"]["created"] is True
    assert result["data"]["path"] == "docs/new.md"
    assert (tmp_path / "docs" / "new.md").read_text(encoding="utf-8") == "# hi\n"


async def test_post_overwrites_existing_file(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("old", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_post_docs(
        plugin, umo="u:m", body={"path": "docs/x.md", "content": "new"}
    )

    assert result["data"]["saved"] is True
    assert result["data"]["created"] is False
    assert (tmp_path / "docs" / "x.md").read_text(encoding="utf-8") == "new"


async def test_post_mkdir_parents(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_post_docs(
        plugin,
        umo="u:m",
        body={"path": "deep/nested/dir/file.md", "content": "x"},
    )

    assert result["data"]["saved"] is True
    assert (tmp_path / "deep" / "nested" / "dir" / "file.md").exists()


async def test_post_rejects_non_md_extension(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_post_docs(
        plugin, umo="u:m", body={"path": "docs/notes.txt", "content": "x"}
    )

    assert result["data"]["reason"] == "invalid_param"


async def test_post_rejects_oversized_content(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    big = "a" * (2 * 1024 * 1024 + 1)
    result = await _dc.handle_post_docs(
        plugin, umo="u:m", body={"path": "docs/big.md", "content": big}
    )

    assert result["data"]["reason"] == "invalid_param"
    assert not (tmp_path / "docs" / "big.md").exists()


async def test_post_invalid_body_when_not_dict(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_post_docs(plugin, umo="u:m", body=None)
    assert result["data"]["reason"] == "invalid_body"

    result = await _dc.handle_post_docs(
        plugin,
        umo="u:m",
        body="not a dict",  # type: ignore[arg-type]
    )
    assert result["data"]["reason"] == "invalid_body"


async def test_post_rejects_path_with_null_byte(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_post_docs(
        plugin, umo="u:m", body={"path": "docs/bad\x00.md", "content": "x"}
    )
    assert result["data"]["reason"] == "invalid_param"


async def test_post_rejects_parent_traversal(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_post_docs(
        plugin, umo="u:m", body={"path": "../escape.md", "content": "x"}
    )
    assert result["data"]["reason"] == "path_unsafe"


async def test_post_no_project_loaded(plugin: Any) -> None:
    _proj_state.reset()
    result = await _dc.handle_post_docs(plugin, body={"path": "x.md", "content": "y"})
    assert result["data"]["reason"] == "no_project_loaded"


async def test_post_feature_disabled(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    plugin._config["agentsmd_enabled"] = False

    result = await _dc.handle_post_docs(
        plugin, umo="u:m", body={"path": "x.md", "content": "y"}
    )
    assert result["data"]["reason"] == "feature_disabled"


# ── PATCH ─────────────────────────────────────────────────────────


async def test_patch_renames_file(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "old.md").write_text("data", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(
        plugin,
        umo="u:m",
        body={"path": "docs/old.md", "new_path": "docs/new.md"},
    )

    assert result["data"]["renamed"] is True
    assert result["data"]["path"] == "docs/old.md"
    assert result["data"]["new_path"] == "docs/new.md"
    assert not (tmp_path / "docs" / "old.md").exists()
    assert (tmp_path / "docs" / "new.md").read_text(encoding="utf-8") == "data"


async def test_patch_rejects_missing_old(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(
        plugin,
        umo="u:m",
        body={"path": "docs/missing.md", "new_path": "docs/new.md"},
    )
    assert result["data"]["reason"] == "file_not_found"


async def test_patch_rejects_existing_new(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "old.md").write_text("old", encoding="utf-8")
    (tmp_path / "docs" / "new.md").write_text("existing", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(
        plugin,
        umo="u:m",
        body={"path": "docs/old.md", "new_path": "docs/new.md"},
    )
    assert result["data"]["reason"] == "file_exists"


async def test_patch_rejects_same_path(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(
        plugin,
        umo="u:m",
        body={"path": "docs/x.md", "new_path": "docs/x.md"},
    )
    assert result["data"]["reason"] == "invalid_param"


async def test_patch_invalid_body_when_not_dict(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(plugin, umo="u:m", body=None)
    assert result["data"]["reason"] == "invalid_body"


async def test_patch_rejects_non_md_new_path(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "old.md").write_text("o", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(
        plugin,
        umo="u:m",
        body={"path": "docs/old.md", "new_path": "docs/new.txt"},
    )
    assert result["data"]["reason"] == "invalid_param"


async def test_patch_rejects_parent_traversal(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "old.md").write_text("o", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(
        plugin,
        umo="u:m",
        body={"path": "docs/old.md", "new_path": "../escape.md"},
    )
    assert result["data"]["reason"] == "path_unsafe"


async def test_patch_mkdir_parents_for_new_path(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "old.md").write_text("o", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_patch_docs(
        plugin,
        umo="u:m",
        body={"path": "docs/old.md", "new_path": "deep/nested/new.md"},
    )
    assert result["data"]["renamed"] is True
    assert (tmp_path / "deep" / "nested" / "new.md").exists()


# ── DELETE ──────────────────────────────────────────────────────────


async def test_delete_removes_file(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "x.md"
    target.write_text("bye", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_delete_docs(plugin, umo="u:m", body={"path": "docs/x.md"})

    assert result["data"]["deleted"] is True
    assert result["data"]["path"] == "docs/x.md"
    assert not target.exists()


async def test_delete_rejects_missing_file(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_delete_docs(
        plugin, umo="u:m", body={"path": "docs/missing.md"}
    )
    assert result["data"]["reason"] == "file_not_found"


async def test_delete_rejects_non_md_path(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_delete_docs(
        plugin, umo="u:m", body={"path": "docs/notes.txt"}
    )
    assert result["data"]["reason"] == "invalid_param"


async def test_delete_rejects_directory_path(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    weird_dir = tmp_path / "weird.md"
    weird_dir.mkdir()
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_delete_docs(plugin, umo="u:m", body={"path": "weird.md"})
    assert result["data"]["reason"] == "git_error"
    assert "directory" in result["data"]["stderr"]
    assert weird_dir.exists()


async def test_delete_invalid_body_when_not_dict(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_delete_docs(plugin, umo="u:m", body=None)
    assert result["data"]["reason"] == "invalid_body"


async def test_delete_rejects_parent_traversal(plugin: Any, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _dc.handle_delete_docs(
        plugin, umo="u:m", body={"path": "../escape.md"}
    )
    assert result["data"]["reason"] == "path_unsafe"


async def test_delete_no_project_loaded(plugin: Any) -> None:
    _proj_state.reset()
    result = await _dc.handle_delete_docs(plugin, body={"path": "x.md"})
    assert result["data"]["reason"] == "no_project_loaded"
