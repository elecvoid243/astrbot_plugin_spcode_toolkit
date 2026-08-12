"""Integration tests for POST /spcode/git-pull."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from tools.project import state as _proj_state
from tools.webapi.git_pull import handle

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


def _init_bare(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--bare", "-q", "-b", "main", str(path)],
        check=True,
    )


def _clone(source: Path, target: Path) -> None:
    subprocess.run(["git", "clone", "-q", str(source), str(target)], check=True)


def _seed_remote(tmp_path: Path) -> tuple[Path, Path, Path]:
    bare = tmp_path / "remote.git"
    _init_bare(bare)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "a.txt").write_text("one", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "main")
    local = tmp_path / "local"
    other = tmp_path / "other"
    _clone(bare, local)
    _clone(bare, other)
    for repo in (local, other):
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
    return bare, local, other


def _commit(path: Path, name: str, content: str, message: str) -> str:
    (path / name).write_text(content, encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("init", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


def _load(path: Path, umo: str) -> str:
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_pull_noop(tmp_path: Path):
    _, local, _ = _seed_remote(tmp_path)
    plugin = _make_plugin()
    umo = _load(local, "u:pull-noop")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is True
    assert result["data"]["pulled"] is False
    assert result["data"]["updated"] is False


async def test_pull_fast_forward(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    remote_sha = _commit(other, "b.txt", "two", "add b")
    _git(other, "push")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-ff")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is True
    assert result["data"]["updated"] is True
    assert result["data"]["after_sha"] == remote_sha
    assert "b.txt" in result["data"]["files_touched"]


async def test_pull_fast_forward_with_explicit_remote(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    remote_sha = _commit(other, "b.txt", "two", "add b")
    _git(other, "push")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-explicit")

    result = await handle(
        plugin,
        umo=umo,
        body={"remote": "origin", "branch": "main"},
    )

    assert result["data"]["success"] is True
    assert result["data"]["after_sha"] == remote_sha


async def test_pull_ff_only_diverged(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    _commit(other, "other.txt", "other", "other")
    _git(other, "push")
    _commit(local, "local.txt", "local", "local")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-nff")

    result = await handle(plugin, umo=umo, body={"ff_only": True})

    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "non_fast_forward"


async def test_pull_merge_conflict(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    _commit(other, "a.txt", "remote", "remote change")
    _git(other, "push")
    _commit(local, "a.txt", "local", "local change")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-conflict")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "merge_conflict"
    assert result["data"]["operation"] == "merge"


async def test_pull_rebase_conflict(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    _commit(other, "a.txt", "remote", "remote change")
    _git(other, "push")
    _commit(local, "a.txt", "local", "local change")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-rebase-conflict")

    result = await handle(plugin, umo=umo, body={"rebase": True})

    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "rebase_conflict"
    assert result["data"]["operation"] == "rebase"


async def test_no_upstream(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo, "u:pull-no-upstream")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["reason"] == "no_upstream"


async def test_missing_explicit_remote(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo, "u:pull-missing-remote")

    result = await handle(plugin, umo=umo, body={"remote": "missing"})

    assert result["data"]["reason"] == "remote_not_found"


async def test_ff_only_and_rebase_are_mutually_exclusive():
    plugin = _make_plugin()
    result = await handle(plugin, body={"ff_only": True, "rebase": True})
    assert result["data"]["reason"] == "invalid_body"


async def test_non_bool_mode_rejected():
    plugin = _make_plugin()
    result = await handle(plugin, body={"ff_only": "yes"})
    assert result["data"]["reason"] == "invalid_body"


async def test_invalid_remote():
    plugin = _make_plugin()
    result = await handle(plugin, body={"remote": "bad remote"})
    assert result["data"]["reason"] == "invalid_remote"


async def test_invalid_branch():
    plugin = _make_plugin()
    result = await handle(plugin, body={"remote": "origin", "branch": "bad branch"})
    assert result["data"]["reason"] == "invalid_branch"


async def test_invalid_body():
    plugin = _make_plugin()
    result = await handle(plugin, body=None)
    assert result["data"]["reason"] == "invalid_body"


async def test_dirty_worktree_rejected(tmp_path: Path):
    _, local, _ = _seed_remote(tmp_path)
    (local / "dirty.txt").write_text("dirty", encoding="utf-8")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-dirty")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["reason"] == "worktree_dirty"
