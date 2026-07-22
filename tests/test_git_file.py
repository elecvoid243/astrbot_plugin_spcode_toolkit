"""Tests for GET /spcode/git-file HTTP endpoint.

Spec: docs/superpowers/specs/2026-07-11-document-manager-backend-design.md §3.1
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock
from tools.project import state as _proj_state
from tools.webapi import git_file as _gf

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin() -> Any:
    return _make_plugin()


def _init_git_repo_with_commits(
    path: Path, contents: list[tuple[str, str]]
) -> list[str]:
    """contents = [(filename, text), ...]; returns SHAs oldest→newest."""
    # -c init.defaultBranch=main ensures the default branch is named "main"
    # so that ref="main" tests work on Windows (which defaults to "master").
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", "-b", "main"],
        cwd=path,
        check=True,
    )
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    shas: list[str] = []
    for fname, text in contents:
        (path / fname).write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"add {fname}", "-q"],
            cwd=path,
            check=True,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        shas.append(sha)
    return shas


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


def _call_with_query(monkeypatch: pytest.MonkeyPatch, plugin: Any, **query: str) -> Any:
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock(query=query))
    return _gf.handle(plugin)


# ─── Happy path ──────────────────────────────────────────────────────


async def test_git_file_default_head(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path,
        [("README.md", "v1\n"), ("README.md", "v2\n")],
    )
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="README.md")
    assert result["data"]["loaded"] is True
    assert result["data"]["ref"] == "HEAD"
    assert result["data"]["resolved_sha"] == shas[-1]
    assert result["data"]["path"] == "README.md"
    # _run_git_async rstrip trims trailing \r\n once; "\n" → "" via rstrip("\r\n")
    # so the blob's last "\n" disappears.
    assert result["data"]["content"] == "v2"
    assert result["data"]["is_binary"] is False
    assert result["data"]["truncated"] is False
    assert result["data"]["max_bytes"] == 1 * 1024 * 1024


async def test_git_file_explicit_ref(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    shas = _init_git_repo_with_commits(
        tmp_path,
        [("README.md", "v1\n"), ("README.md", "v2\n")],
    )
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref=shas[0], path="README.md")
    assert result["data"]["resolved_sha"] == shas[0]
    assert result["data"]["content"] == "v1"


async def test_git_file_branch_ref(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(
        tmp_path, [("README.md", "v1\n"), ("README.md", "v2\n")]
    )
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="main", path="README.md")
    assert result["data"]["loaded"] is True
    assert result["data"]["content"] == "v2"


async def test_git_file_head_parent(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(
        tmp_path,
        [("README.md", "v1\n"), ("README.md", "v2\n")],
    )
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="HEAD~1", path="README.md")
    assert result["data"]["content"] == "v1"


async def test_git_file_nested_path(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "spec.md").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x", "-q"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="docs/spec.md")
    assert result["data"]["content"] == "hello"


async def test_git_file_unicode_filename(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    (tmp_path / "笔记.md").write_text("中文内容", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x", "-q"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="笔记.md")
    assert result["data"]["content"] == "中文内容"


async def test_git_file_empty_file(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    (tmp_path / "empty.md").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x", "-q"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="empty.md")
    assert result["data"]["content"] == ""
    assert result["data"]["is_binary"] is False
    assert result["data"]["size"] == 0


async def test_git_file_is_binary_when_null_byte(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    (tmp_path / "blob.bin").write_bytes(b"hello\x00world")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x", "-q"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="blob.bin")
    assert result["data"]["is_binary"] is True
    assert result["data"]["content"] == ""
    assert result["data"]["size"] == 0


async def test_git_file_truncates_oversized_blob(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    big = "a" * (1 * 1024 * 1024 + 100)
    (tmp_path / "big.md").write_text(big, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x", "-q"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="big.md")
    assert result["data"]["truncated"] is True
    assert len(result["data"]["content"]) == 1 * 1024 * 1024
    # size is the byte length of the *returned* (truncated) content
    assert result["data"]["size"] == 1 * 1024 * 1024


# ─── Error path ──────────────────────────────────────────────────────


async def test_git_file_path_missing(
    monkeypatch: pytest.MonkeyPatch, plugin: Any
) -> None:
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "invalid_param"


async def test_git_file_path_with_null_byte(
    monkeypatch: pytest.MonkeyPatch, plugin: Any
) -> None:
    result = await _call_with_query(monkeypatch, plugin, path="bad\x00.md")
    assert result["data"]["reason"] == "invalid_param"


async def test_git_file_path_too_long(
    monkeypatch: pytest.MonkeyPatch, plugin: Any
) -> None:
    result = await _call_with_query(monkeypatch, plugin, path="x" * 600)
    assert result["data"]["reason"] == "invalid_param"


async def test_git_file_ref_too_long(
    monkeypatch: pytest.MonkeyPatch, plugin: Any
) -> None:
    result = await _call_with_query(
        monkeypatch, plugin, ref="x" * 600, path="README.md"
    )
    assert result["data"]["reason"] == "invalid_param"


async def test_git_file_parent_traversal(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("README.md", "v1\n")])
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="../escape.md")
    assert result["data"]["reason"] == "path_unsafe"


async def test_git_file_ref_not_found(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("README.md", "v1\n")])
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(
        monkeypatch,
        plugin,
        ref="0000000000000000000000000000000000000000",
        path="README.md",
    )
    assert result["data"]["reason"] == "ref_not_found"


async def test_git_file_missing_at_ref(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    # Commit a file then rename in a 2nd commit; ask for the old name at HEAD.
    (tmp_path / "old.md").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "mv", "old.md", "new.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "rename", "-q"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="old.md")
    assert result["data"]["reason"] == "file_missing_at_ref"


async def test_git_file_no_project_loaded(
    monkeypatch: pytest.MonkeyPatch, plugin: Any
) -> None:
    _proj_state.reset()
    result = await _call_with_query(monkeypatch, plugin, path="README.md")
    assert result["data"]["reason"] == "no_project_loaded"


async def test_git_file_feature_disabled(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("README.md", "v1\n")])
    _load_project(plugin, "u:m", str(tmp_path))
    plugin._config["agentsmd_enabled"] = False

    result = await _call_with_query(monkeypatch, plugin, path="README.md")
    assert result["data"]["reason"] == "feature_disabled"


async def test_git_file_not_a_git_repo(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    _load_project(plugin, "u:m", str(plain))

    result = await _call_with_query(monkeypatch, plugin, path="README.md")
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_git_file_worktree_invalid(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("README.md", "v1\n")])
    _load_project(plugin, "u:m", str(tmp_path))

    from astrbot.api import web

    monkeypatch.setattr(
        web, "request", make_web_request_mock(query={"path": "README.md"})
    )
    result = await _gf.handle(plugin, worktree="../other")
    assert result["data"]["reason"] == "worktree_invalid"
