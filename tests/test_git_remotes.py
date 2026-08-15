"""Integration tests for GET /spcode/git-remotes."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from tools.project import state as _proj_state
from tools.webapi.git_remotes import handle

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
    (path / "README.md").write_text("init", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


def _load(path: Path) -> str:
    umo = "u:remotes"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_remotes_no_umo_loaded():
    plugin = _make_plugin()
    result = await handle(plugin)
    assert result["data"]["reason"] == "no_project_loaded"
    assert result["data"]["remotes"] == []


async def test_remotes_empty(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo)

    assert result["data"]["success"] is True
    assert result["data"]["remotes"] == []
    assert result["data"]["total"] == 0


async def test_remotes_lists_name_and_url(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "https://example.com/o/r.git")
    _git(repo, "remote", "add", "upstream", "git@github.com:org/up.git")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo)

    assert result["data"]["success"] is True
    assert result["data"]["remotes"] == [
        {"name": "origin", "url": "https://example.com/o/r.git"},
        {"name": "upstream", "url": "git@github.com:org/up.git"},
    ]
    assert result["data"]["total"] == 2
