"""Tests for POST /spcode/git-unstage HTTP endpoint.

PR-4 of git workflow endpoints design.
Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §C
"""

from __future__ import annotations
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import git_unstage as _gu

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin():
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


def _unstage(
    plugin, body: dict[str, Any], *, umo: str | None = None, worktree: str | None = None
):
    return _gu.handle(
        plugin,
        body=body,
        umo=umo,
        worktree=worktree,
    )


# ──────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────


async def test_unstage_specific_files(plugin, tmp_path: Path):
    """files=["a.py","b.py"] → 两个文件被 unstage,staged_count=0。"""
    _init_git_repo(tmp_path)
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "b.py").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    # 确认已暂存
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "A  a.py" in status.stdout
    assert "A  b.py" in status.stdout

    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"files": ["a.py", "b.py"]}, umo="u:m")
    assert result["data"]["unstaged"] is True
    assert result["data"]["staged_count"] == 0
    assert result["data"]["reason"] is None

    # 验证 git 实际取消暂存(文件从 staged → untracked,因为 a.py 原本是
    # 新文件未提交过,reset 后 git 认为它从未进入 index)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    # 验证: 不再 staged,且 diff --cached 为空
    cached = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "a.py" not in cached.stdout
    assert "b.py" not in cached.stdout


async def test_unstage_all(plugin, tmp_path: Path):
    """all=true → 取消所有 staged。"""
    _init_git_repo(tmp_path)
    (tmp_path / "x.py").write_text("x", encoding="utf-8")
    (tmp_path / "y.py").write_text("y", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"all": True}, umo="u:m")
    assert result["data"]["unstaged"] is True
    assert result["data"]["staged_count"] == 0

    # 验证: 全部不再 staged
    cached = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "x.py" not in cached.stdout
    assert "y.py" not in cached.stdout


async def test_unstage_files_and_all_mutually_exclusive(plugin, tmp_path: Path):
    """同时给 files + all → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"files": ["a.py"], "all": True}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_unstage_neither_files_nor_all(plugin, tmp_path: Path):
    """两者都不给 → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_unstage_empty_files_array(plugin, tmp_path: Path):
    """files=[] → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"files": []}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_unstage_invalid_body_not_dict(plugin, tmp_path: Path):
    """body 不是 dict → invalid_body。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, "not a dict", umo="u:m")  # type: ignore[arg-type]
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "invalid_body"


async def test_unstage_path_unsafe(plugin, tmp_path: Path):
    """files 含 .. 段 → path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"files": ["../escape.py"]}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "path_unsafe"


async def test_unstage_path_dot_git_rejected(plugin, tmp_path: Path):
    """.git/HEAD → path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"files": [".git/HEAD"]}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "path_unsafe"


async def test_unstage_no_project_loaded(plugin):
    """state 空 → no_project_loaded。"""
    _proj_state.reset()
    result = await _unstage(plugin, {"files": ["a.py"]})
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "no_project_loaded"


async def test_unstage_feature_disabled(plugin, tmp_path: Path):
    """feature flag false → feature_disabled。"""
    plugin._config["agentsmd_enabled"] = False
    result = await _unstage(plugin, {"files": ["a.py"]})
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "feature_disabled"


async def test_unstage_not_a_git_repo(plugin, tmp_path: Path):
    """loaded 目录不是 git repo → not_a_git_repo。"""
    non_git = tmp_path / "plain"
    non_git.mkdir()
    _load_project(plugin, "u:m", str(non_git))

    result = await _unstage(plugin, {"files": ["a.py"]}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_unstage_invalid_files_type(plugin, tmp_path: Path):
    """files 不是 str 列表(而是 int) → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"files": [123]}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_unstage_too_many_files(plugin, tmp_path: Path):
    """files 数量 > 100 → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    files = [f"f{i}.py" for i in range(101)]
    result = await _unstage(plugin, {"files": files}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_unstage_all_not_bool(plugin, tmp_path: Path):
    """all 不是 bool(字符串) → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"all": "yes"}, umo="u:m")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_unstage_worktree_invalid(plugin, tmp_path: Path):
    """worktree 含 .. → worktree_invalid。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _unstage(plugin, {"files": ["a.py"]}, umo="u:m", worktree="../other")
    assert result["data"]["unstaged"] is False
    assert result["data"]["reason"] == "worktree_invalid"
