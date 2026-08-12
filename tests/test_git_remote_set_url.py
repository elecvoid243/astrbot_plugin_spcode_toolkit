"""Integration tests for POST /spcode/git-remote-set-url."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from tools.project import state as _proj_state
from tools.webapi.git_remote_set_url import handle

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
    umo = "u:remote"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_adds_missing_origin(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)
    url = "https://example.com/org/remote.git"

    result = await handle(plugin, umo=umo, body={"remote": "origin", "url": url})

    assert result["data"]["success"] is True
    assert result["data"]["action"] == "added"
    assert _git(repo, "remote", "get-url", "origin") == url


async def test_updates_existing_origin(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "https://example.com/old.git")
    plugin = _make_plugin()
    umo = _load(repo)
    url = "https://example.com/new.git"

    result = await handle(plugin, umo=umo, body={"remote": "origin", "url": url})

    assert result["data"]["success"] is True
    assert result["data"]["action"] == "updated"
    assert _git(repo, "remote", "get-url", "origin") == url


async def test_same_url_is_unchanged(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    url = "https://example.com/same.git"
    _git(repo, "remote", "add", "origin", url)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"remote": "origin", "url": url})

    assert result["data"]["success"] is True
    assert result["data"]["action"] == "unchanged"
    assert _git(repo, "remote", "get-url", "origin") == url


async def test_invalid_url():
    plugin = _make_plugin()
    result = await handle(plugin, body={"remote": "origin", "url": "bad\nurl"})
    assert result["data"]["reason"] == "invalid_url"


async def test_invalid_remote():
    plugin = _make_plugin()
    result = await handle(
        plugin,
        body={"remote": "bad remote", "url": "https://example.com/repo.git"},
    )
    assert result["data"]["reason"] == "invalid_remote"


async def test_invalid_body():
    plugin = _make_plugin()
    result = await handle(plugin, body=None)
    assert result["data"]["reason"] == "invalid_body"
