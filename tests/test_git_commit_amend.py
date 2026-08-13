"""Unit/integration tests for POST /spcode/git-commit-amend."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from tools.project import state as _proj_state
from tools.webapi.git_commit_amend import handle

from tests.conftest import _make_plugin

pytestmark = pytest.mark.asyncio


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("hi", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


def _load(path: Path) -> str:
    umo = "u:amend"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_amend_replaces_message(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    before = _git(repo, "rev-parse", "HEAD")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "fix: new message"})

    assert result["data"]["success"] is True
    assert result["data"]["amended"] is True
    assert result["data"]["before_sha"] == before
    assert result["data"]["after_sha"] != before
    assert _git(repo, "log", "-1", "--pretty=%s") == "fix: new message"


async def test_amend_multiline_message(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(
        plugin,
        umo=umo,
        body={"message": "fix: subject\n\nbody line"},
    )

    assert result["data"]["success"] is True
    assert result["data"]["subject"] == "fix: subject"
    assert "body line" in result["data"]["message"]


async def test_empty_repository(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "x"})

    assert result["data"]["reason"] == "empty_repository"


async def test_staged_changes_rejected(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "a.txt")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "x"})

    assert result["data"]["reason"] == "staged_changes_present"


async def test_merge_commit_rejected(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "f.txt").write_text("f", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "feature")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature", "-q")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "x"})

    assert result["data"]["reason"] == "cannot_amend_merge_commit"


async def test_invalid_message():
    plugin = _make_plugin()
    result = await handle(plugin, body={"message": ""})
    assert result["data"]["reason"] == "invalid_message"
