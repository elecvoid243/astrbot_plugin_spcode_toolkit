"""Tests for POST /spcode/git-stage HTTP endpoint.

PR-3 of git workflow endpoints design.
Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §B
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from tools.project import state as _proj_state
from tools.webapi import git_stage as _gs

from tests.conftest import _make_plugin

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


def _stage(
    plugin, body: dict[str, Any], *, umo: str | None = None, worktree: str | None = None
):
    """Call git_stage.handle with given body + umo/worktree kwargs.

    `_wrap` injects body/umo/worktree via kwargs when handler declares them.
    """
    return _gs.handle(
        plugin,
        body=body,
        umo=umo,
        worktree=worktree,
    )


# ──────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────


async def test_stage_specific_files(plugin, tmp_path: Path):
    """files=["a.py","b.py"] → 两个文件被暂存,staged_count=2。"""
    _init_git_repo(tmp_path)
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "b.py").write_text("b", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": ["a.py", "b.py"]}, umo="u:m")
    assert result["data"]["staged"] is True
    assert result["data"]["staged_count"] == 2
    assert sorted(result["data"]["files"]) == ["a.py", "b.py"]
    assert result["data"]["reason"] is None

    # 验证 git 实际暂存
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    # "A  a.py" 和 "A  b.py" 表示已暂存
    assert "A  a.py" in status.stdout
    assert "A  b.py" in status.stdout


async def test_stage_all(plugin, tmp_path: Path):
    """all=true → 工作区所有改动(不含 untracked,但 intent-to-add 会暂存)。"""
    _init_git_repo(tmp_path)
    (tmp_path / "x.py").write_text("x", encoding="utf-8")
    (tmp_path / "y.py").write_text("y", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-N", "x.py"], cwd=tmp_path, check=True
    )  # intent-to-add
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"all": True}, umo="u:m")
    assert result["data"]["staged"] is True
    assert result["data"]["staged_count"] >= 1
    assert "x.py" in result["data"]["files"]

    # 验证: x.py 现在是 staged (A 或 AM)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "x.py" in status.stdout


async def test_stage_files_and_all_mutually_exclusive(plugin, tmp_path: Path):
    """同时给 files + all → invalid_files(不允许)。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": ["a.py"], "all": True}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_stage_neither_files_nor_all(plugin, tmp_path: Path):
    """两者都不给 → invalid_files(必须指定一个)。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_stage_empty_files_array(plugin, tmp_path: Path):
    """files=[] → invalid_files(空数组视为无输入)。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": []}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_stage_invalid_body_not_dict(plugin, tmp_path: Path):
    """body 不是 dict → invalid_body。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, "not a dict", umo="u:m")  # type: ignore[arg-type]
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "invalid_body"


async def test_stage_path_unsafe(plugin, tmp_path: Path):
    """files 含 .. 段 → path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": ["../escape.py"]}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "path_unsafe"


async def test_stage_path_dot_git_rejected(plugin, tmp_path: Path):
    """.git/HEAD → path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": [".git/HEAD"]}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "path_unsafe"


async def test_stage_no_project_loaded(plugin):
    """state 空 → no_project_loaded。"""
    _proj_state.reset()
    result = await _stage(plugin, {"files": ["a.py"]})
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "no_project_loaded"


async def test_stage_feature_disabled(plugin, tmp_path: Path):
    """feature flag false → feature_disabled。"""
    plugin._config["agentsmd_enabled"] = False
    result = await _stage(plugin, {"files": ["a.py"]})
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "feature_disabled"


async def test_stage_not_a_git_repo(plugin, tmp_path: Path):
    """loaded 目录不是 git repo → not_a_git_repo。"""
    non_git = tmp_path / "plain"
    non_git.mkdir()
    _load_project(plugin, "u:m", str(non_git))

    result = await _stage(plugin, {"files": ["a.py"]}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_stage_invalid_files_type(plugin, tmp_path: Path):
    """files 不是 str 列表(而是 int) → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": [123]}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_stage_too_many_files(plugin, tmp_path: Path):
    """files 数量 > 100 → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    files = [f"f{i}.py" for i in range(101)]
    result = await _stage(plugin, {"files": files}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_stage_all_not_bool(plugin, tmp_path: Path):
    """all 不是 bool(字符串) → invalid_files。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"all": "yes"}, umo="u:m")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "invalid_files"


async def test_stage_worktree_invalid(plugin, tmp_path: Path):
    """worktree 含 .. → worktree_invalid。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": ["a.py"]}, umo="u:m", worktree="../other")
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "worktree_invalid"


# ──────────────────────────────────────────────────────────
# gitignore 强制暂存场景(2026-06-24)
#
# 背景:`git add <file>` 对"tracked-but-now-ignored"和"untracked-ignored"
# 文件默认拒绝,需 `-f`。Dashboard 上用户逐个点击 = 显式意图,应使用
# `git add -f -- <files>`;批量 `all=true` 仍保守,不加 `-f`,避免意外
# 把 ignored 文件一并暂存。
# ──────────────────────────────────────────────────────────


async def test_stage_files_force_adds_gitignored_tracked_file(plugin, tmp_path: Path):
    """tracked + now-ignored 文件 → files 模式必须用 -f 成功暂存。

    场景:dashboard/src/assets/mdi-subset/* 既被 dashboard/.gitignore
    忽略,又曾在 index 里(早先 commit)。工作区有新版但 `git add`
    默认拒绝,返回 git_error。修复后 files 模式用 `git add -f --`。
    """
    _init_git_repo(tmp_path)
    # 提交初始版本(让文件进入 index)
    (tmp_path / "subset").mkdir()
    (tmp_path / "subset" / "icon.css").write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add icon", "-q"], cwd=tmp_path, check=True)
    # 现在把目录加入 .gitignore
    (tmp_path / ".gitignore").write_text("subset/\n", encoding="utf-8")
    # 工作区修改文件(触发 tracked-but-ignored 状态)
    (tmp_path / "subset" / "icon.css").write_text("v2", encoding="utf-8")

    _load_project(plugin, "u:m", str(tmp_path))

    # 验证 git add 不加 -f 会失败(锁死基线行为)
    bare = subprocess.run(
        ["git", "add", "--", "subset/icon.css"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert bare.returncode != 0
    assert "ignored by one of your .gitignore files" in (bare.stderr + bare.stdout)
    subprocess.run(
        ["git", "reset", "HEAD", "--", "subset/icon.css"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    # 通过端点(files 模式)必须能成功暂存
    result = await _stage(plugin, {"files": ["subset/icon.css"]}, umo="u:m")
    assert result["data"]["staged"] is True, result
    assert "subset/icon.css" in result["data"]["files"]

    # 验证 git index 真的有新版本
    blob = subprocess.run(
        ["git", "ls-files", "--stage", "subset/icon.css"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "icon.css" in blob.stdout
    # 旧 blob 不再在 index 中(worktree 改了,index 应跟着改)
    cat = subprocess.run(
        ["git", "show", ":subset/icon.css"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert cat.stdout == "v2"


async def test_stage_files_force_adds_gitignored_untracked_file(plugin, tmp_path: Path):
    """untracked + ignored 文件 → files 模式必须用 -f 成功暂存。"""
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("secrets/\n", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "token.txt").write_text("x", encoding="utf-8")

    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"files": ["secrets/token.txt"]}, umo="u:m")
    assert result["data"]["staged"] is True, result
    assert "secrets/token.txt" in result["data"]["files"]

    # 验证 file 在 index 里
    listed = subprocess.run(
        ["git", "ls-files", "--stage"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "secrets/token.txt" in listed.stdout


async def test_stage_all_does_not_force_add_gitignored(plugin, tmp_path: Path):
    """all=true 不加 -f:gitignored 文件不应被自动暂存。

    批量"全部暂存"必须保守,避免用户没注意就把 .env 等文件打包。
    """
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "secret.txt").write_text("x", encoding="utf-8")
    # 另一个常规文件(用来确认 all 仍能暂存未 ignored 的改动)
    (tmp_path / "normal.txt").write_text("y", encoding="utf-8")

    _load_project(plugin, "u:m", str(tmp_path))

    result = await _stage(plugin, {"all": True}, umo="u:m")
    assert result["data"]["staged"] is True, result

    # ignored 文件不应出现在 staged files
    assert "ignored/secret.txt" not in result["data"]["files"]
    # 常规改动应被暂存
    assert "normal.txt" in result["data"]["files"]

    # git 实际状态:ignored 文件未进入 index
    # 注:`git ls-files --stage <path>` 即便文件不在 index 也返回 0,
    # 必须检查 stdout 是否为空(空 = 未暂存)
    listed = subprocess.run(
        ["git", "ls-files", "--stage", "ignored/secret.txt"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0
    assert "ignored/secret.txt" not in listed.stdout  # 空 stdout = 未暂存
