"""Integration tests for POST /spcode/git-push."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from tools.project import state as _proj_state
from tools.webapi.git_push import handle

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


def _commit(path: Path, name: str, content: str) -> str:
    (path / name).write_text(content, encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", f"add {name}")
    return _git(path, "rev-parse", "HEAD")


def _load(path: Path, umo: str) -> str:
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


def _init_bare(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(path)],
        check=True,
    )


def _clone(source: Path, target: Path) -> None:
    subprocess.run(["git", "clone", "-q", str(source), str(target)], check=True)


async def test_first_push_sets_upstream(tmp_path: Path):
    bare = tmp_path / "remote.git"
    _init_bare(bare)
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(bare))
    plugin = _make_plugin()
    umo = _load(repo, "u:push-first")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is True
    assert result["data"]["pushed"] is True
    assert result["data"]["set_upstream"] is True
    assert result["data"]["upstream"] == "origin/main"
    assert _git(repo, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/main"


async def test_push_new_commit_and_noop(tmp_path: Path):
    bare = tmp_path / "remote.git"
    _init_bare(bare)
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "main")
    plugin = _make_plugin()
    umo = _load(repo, "u:push")
    sha = _commit(repo, "a.txt", "a")

    first = await handle(plugin, umo=umo, body={})
    assert first["data"]["success"] is True
    assert first["data"]["pushed"] is True
    assert first["data"]["local_sha"] == sha

    second = await handle(plugin, umo=umo, body={})
    assert second["data"]["success"] is True
    assert second["data"]["pushed"] is False


async def test_non_fast_forward(tmp_path: Path):
    bare = tmp_path / "remote.git"
    _init_bare(bare)
    one = tmp_path / "one"
    _init_repo(one)
    _git(one, "remote", "add", "origin", str(bare))
    _git(one, "push", "-u", "origin", "main")
    two = tmp_path / "two"
    _clone(bare, two)
    _git(two, "config", "user.email", "t@t")
    _git(two, "config", "user.name", "t")
    _commit(two, "two.txt", "two")
    _git(two, "push")
    _commit(one, "one.txt", "one")
    plugin = _make_plugin()
    umo = _load(one, "u:push-nff")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "non_fast_forward"


async def test_missing_remote(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo, "u:push-missing")
    result = await handle(plugin, umo=umo, body={"remote": "missing"})
    assert result["data"]["reason"] == "remote_not_found"


async def test_detached_head(tmp_path: Path):
    bare = tmp_path / "remote.git"
    _init_bare(bare)
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "checkout", "--detach", "HEAD")
    plugin = _make_plugin()
    umo = _load(repo, "u:push-detached")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["reason"] == "detached_head"


async def test_invalid_remote():
    plugin = _make_plugin()
    result = await handle(plugin, body={"remote": "bad remote"})
    assert result["data"]["reason"] == "invalid_remote"


async def test_invalid_branch():
    plugin = _make_plugin()
    result = await handle(plugin, body={"branch": "bad branch"})
    assert result["data"]["reason"] == "invalid_branch"


async def test_invalid_body():
    plugin = _make_plugin()
    result = await handle(plugin, body=None)
    assert result["data"]["reason"] == "invalid_body"
