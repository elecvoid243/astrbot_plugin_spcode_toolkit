"""Integration tests for POST /spcode/git-remote-remove."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from tools.project import state as _proj_state
from tools.webapi.git_remote_remove import handle

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
    umo = "u:remote-remove"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_remove_existing_remote(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", "https://example.com/o/r.git")
    _git(repo, "remote", "add", "upstream", "git@github.com:org/up.git")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"remote": "origin"})

    assert result["data"]["success"] is True
    assert result["data"]["removed"] is True
    assert result["data"]["remote"] == "origin"
    # 剩余远端列表, 供前端即时刷新。
    assert result["data"]["remotes"] == ["upstream"]
    # git 侧确认已删除。
    assert _git(repo, "remote") == "upstream"


async def test_remove_missing_remote(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"remote": "origin"})

    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "remote_not_found"
    assert result["data"]["removed"] is False


async def test_remove_invalid_remote(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"remote": "bad remote"})

    assert result["data"]["reason"] == "invalid_remote"
    assert result["data"]["removed"] is False


async def test_remove_invalid_body():
    plugin = _make_plugin()
    result = await handle(plugin, body=None)
    assert result["data"]["reason"] == "invalid_body"
    assert result["data"]["removed"] is False
