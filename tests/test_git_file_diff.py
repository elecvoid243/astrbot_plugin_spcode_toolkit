"""Tests for GET /spcode/git-file-diff HTTP endpoint.

Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-design.md
Author: elecvoid243, 2026-10-06
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock
from tools.project import state as _proj_state
from tools.webapi import git_file_diff as _gfd

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin() -> Any:
    return _make_plugin()


def _init_git_repo_with_commits(
    path: Path, contents: list[tuple[str, str]]
) -> list[str]:
    """contents = [(filename, text), ...]; returns SHAs oldest→newest."""
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
    return _gfd.handle(plugin)


# ─── Task 1: 参数校验 / preflight / ref 解析 ─────────────────────────


async def test_missing_from_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(monkeypatch, plugin, to="HEAD", path="a.txt")
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_missing_to_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_missing_path_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "to": "HEAD"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_ref_too_long_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "a" * 600, "to": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_ref_with_newline_returns_invalid_param(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD\n-x", "to": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "invalid_param"


async def test_no_project_loaded(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    r = await _call_with_query(
        monkeypatch, plugin, **{"from": "HEAD", "to": "HEAD", "path": "a.txt"}
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "no_project_loaded"


async def test_from_ref_not_found(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch,
        plugin,
        **{"from": "nonexistent-ref", "to": "HEAD", "path": "a.txt"},
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "ref_not_found"
    assert r["data"]["failing_ref"] == "from"


async def test_to_ref_not_found(
    monkeypatch: pytest.MonkeyPatch, plugin: Any, tmp_path: Path
) -> None:
    _init_git_repo_with_commits(tmp_path, [("a.txt", "v1\n")])
    _load_project(plugin, "u1", str(tmp_path))
    r = await _call_with_query(
        monkeypatch,
        plugin,
        **{"from": "HEAD", "to": "nonexistent-ref", "path": "a.txt"},
    )
    assert r["data"]["success"] is False
    assert r["data"]["reason"] == "ref_not_found"
    assert r["data"]["failing_ref"] == "to"
