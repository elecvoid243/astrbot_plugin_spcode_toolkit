"""Tests for GET/POST /spcode/git-stash HTTP endpoint.

2026-08-21 (elecvoid243): stash 列表(含文件明细) + stash push -u。
Pattern: tests/test_git_stage.py(直接调 handler,真实 git 仓库断言)。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from tools.project import state as _proj_state
from tools.webapi import git_stash as _gt

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


def _push(plugin, body, *, umo=None, worktree=None):
    return _gt.handle_push(plugin, body=body, umo=umo, worktree=worktree)


def _pop(plugin, body, *, umo=None, worktree=None):
    return _gt.handle_pop(plugin, body=body, umo=umo, worktree=worktree)


def _drop(plugin, body, *, umo=None, worktree=None):
    return _gt.handle_drop(plugin, body=body, umo=umo, worktree=worktree)


def _list(plugin, *, umo=None, worktree=None):
    return _gt.handle_list(plugin, umo=umo, worktree=worktree)


# ──────────────────────────────────────────────────────────
# POST /spcode/git-stash (push)
# ──────────────────────────────────────────────────────────


async def test_push_stashes_tracked_and_untracked(plugin, tmp_path: Path):
    """改动 + 未跟踪文件一并贮藏(-u),工作区随之干净。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed", encoding="utf-8")
    (tmp_path / "new.txt").write_text("brand new", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _push(plugin, {"message": "my work"}, umo="u:m")
    assert result["data"]["stashed"] is True
    assert result["data"]["reason"] is None
    assert result["data"]["ref"] == "stash@{0}"
    assert result["data"]["stash_count"] == 1

    files = {f["path"]: f for f in result["data"]["files"]}
    assert "README.md" in files
    assert files["README.md"]["untracked"] is False
    assert "new.txt" in files
    assert files["new.txt"]["untracked"] is True

    # 工作区应已干净(改动与未跟踪文件都被贮藏)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""


async def test_push_without_message_uses_default_subject(plugin, tmp_path: Path):
    """不传 message → git 默认 "WIP on <branch>: ..." 主题。"""
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _push(plugin, {}, umo="u:m")
    assert result["data"]["stashed"] is True
    assert result["data"]["message"].startswith("WIP on")


async def test_push_clean_worktree_returns_nothing_to_stash(plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _push(plugin, {}, umo="u:m")
    assert result["data"]["stashed"] is False
    assert result["data"]["reason"] == "nothing_to_stash"


async def test_push_invalid_message(plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    r1 = await _push(plugin, {"message": "   "}, umo="u:m")
    assert r1["data"]["reason"] == "invalid_message"
    r2 = await _push(plugin, {"message": 123}, umo="u:m")
    assert r2["data"]["reason"] == "invalid_message"
    r3 = await _push(plugin, None, umo="u:m")
    assert r3["data"]["reason"] == "invalid_body"


async def test_push_no_project_loaded(plugin):
    result = await _push(plugin, {}, umo="u:none")
    assert result["data"]["stashed"] is False
    assert result["data"]["reason"] == "no_project_loaded"


# ──────────────────────────────────────────────────────────
# GET /spcode/git-stash (list)
# ──────────────────────────────────────────────────────────


async def test_list_empty(plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _list(plugin, umo="u:m")
    assert result["data"]["count"] == 0
    assert result["data"]["stashes"] == []
    assert result["data"]["truncated"] is False


async def test_list_returns_stashes_with_file_details(plugin, tmp_path: Path):
    """两次贮藏 → 列表按新→旧排序,文件带增删行数。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    # 第 1 次:改 README(1 增 1 删)
    (tmp_path / "README.md").write_text("changed", encoding="utf-8")
    await _push(plugin, {"message": "first"}, umo="u:m")

    # 第 2 次:新文件 new.txt(2 行,untracked)
    (tmp_path / "new.txt").write_text("l1\nl2", encoding="utf-8")
    await _push(plugin, {"message": "second"}, umo="u:m")

    result = await _list(plugin, umo="u:m")
    assert result["data"]["count"] == 2
    stashes = result["data"]["stashes"]
    # git 默认主题为 "On <branch>: <message>";分支名随 git 配置,只锚尾部。
    assert stashes[0]["message"].endswith(": second")
    assert stashes[1]["message"].endswith(": first")

    second_files = {f["path"]: f for f in stashes[0]["files"]}
    assert second_files["new.txt"]["additions"] == 2
    assert second_files["new.txt"]["deletions"] == 0
    assert second_files["new.txt"]["untracked"] is True

    first_files = {f["path"]: f for f in stashes[1]["files"]}
    assert first_files["README.md"]["additions"] == 1
    assert first_files["README.md"]["deletions"] == 1
    assert stashes[1]["file_count"] == 1
    assert stashes[1]["timestamp"] > 0


async def test_list_no_project_loaded(plugin):
    result = await _list(plugin, umo="u:none")
    assert result["data"]["count"] == 0
    assert result["data"]["reason"] == "no_project_loaded"


# ──────────────────────────────────────────────────────────
# POST /spcode/git-stash-pop
# ──────────────────────────────────────────────────────────


async def test_pop_restores_files_and_drops_entry(plugin, tmp_path: Path):
    """pop → tracked + untracked 改动回到工作区,stash 条目被删除。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed", encoding="utf-8")
    (tmp_path / "new.txt").write_text("brand new", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    await _push(plugin, {}, umo="u:m")

    result = await _pop(plugin, {"index": 0}, umo="u:m")
    assert result["data"]["popped"] is True
    assert result["data"]["reason"] is None
    assert result["data"]["ref"] == "stash@{0}"
    assert result["data"]["stash_count"] == 0

    files = {f["path"]: f for f in result["data"]["files"]}
    assert files["README.md"]["untracked"] is False
    assert files["new.txt"]["untracked"] is True

    # 工作区恢复为贮藏前的状态
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "changed"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "brand new"

    # stash 列表已空
    listing = await _list(plugin, umo="u:m")
    assert listing["data"]["count"] == 0


async def test_pop_conflict_keeps_entry(plugin, tmp_path: Path):
    """HEAD 在贮藏后前进了同区域 → stash_conflict,条目保留未 drop。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("stashed change", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    await _push(plugin, {}, umo="u:m")

    # 干净树上提交同文件的新内容,制造 pop 时的内容冲突
    (tmp_path / "README.md").write_text("committed change", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "c2", "-q"], cwd=tmp_path, check=True)

    result = await _pop(plugin, {"index": 0}, umo="u:m")
    assert result["data"]["popped"] is False
    assert result["data"]["reason"] == "stash_conflict"

    listing = await _list(plugin, umo="u:m")
    assert listing["data"]["count"] == 1


async def test_pop_requires_clean_worktree(plugin, tmp_path: Path):
    """工作区有改动 → worktree_dirty,不执行 pop。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    await _push(plugin, {}, umo="u:m")

    (tmp_path / "other.txt").write_text("dirty", encoding="utf-8")
    result = await _pop(plugin, {"index": 0}, umo="u:m")
    assert result["data"]["popped"] is False
    assert result["data"]["reason"] == "worktree_dirty"
    # 条目仍在
    listing = await _list(plugin, umo="u:m")
    assert listing["data"]["count"] == 1


async def test_pop_missing_entry_returns_stash_not_found(plugin, tmp_path: Path):
    """index 不存在 / 完全没有 stash → stash_not_found。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    empty = await _pop(plugin, {"index": 0}, umo="u:m")
    assert empty["data"]["reason"] == "stash_not_found"

    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    await _push(plugin, {}, umo="u:m")
    missing = await _pop(plugin, {"index": 5}, umo="u:m")
    assert missing["data"]["reason"] == "stash_not_found"


async def test_pop_invalid_params(plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    r1 = await _pop(plugin, None, umo="u:m")
    assert r1["data"]["reason"] == "invalid_body"
    r2 = await _pop(plugin, {"index": -1}, umo="u:m")
    assert r2["data"]["reason"] == "invalid_param"
    r3 = await _pop(plugin, {"index": "0"}, umo="u:m")
    assert r3["data"]["reason"] == "invalid_param"


async def test_pop_no_project_loaded(plugin):
    result = await _pop(plugin, {"index": 0}, umo="u:none")
    assert result["data"]["popped"] is False
    assert result["data"]["reason"] == "no_project_loaded"


# ──────────────────────────────────────────────────────────
# POST /spcode/git-stash-drop
# ──────────────────────────────────────────────────────────


async def test_drop_removes_entry_and_reindexes(plugin, tmp_path: Path):
    """drop 只删条目不碰工作区;删 stash@{0} 后旧条目顶上来。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("first change", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    await _push(plugin, {"message": "first"}, umo="u:m")
    (tmp_path / "README.md").write_text("second change", encoding="utf-8")
    await _push(plugin, {"message": "second"}, umo="u:m")

    result = await _drop(plugin, {"index": 0}, umo="u:m")
    assert result["data"]["dropped"] is True
    assert result["data"]["reason"] is None
    assert result["data"]["ref"] == "stash@{0}"
    assert result["data"]["stash_count"] == 1

    # 剩下的是旧的 "first";工作区保持干净(drop 不触碰工作区)
    listing = await _list(plugin, umo="u:m")
    assert listing["data"]["count"] == 1
    assert listing["data"]["stashes"][0]["message"].endswith(": first")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""


async def test_drop_missing_entry_returns_stash_not_found(plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    empty = await _drop(plugin, {"index": 0}, umo="u:m")
    assert empty["data"]["reason"] == "stash_not_found"

    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    await _push(plugin, {}, umo="u:m")
    missing = await _drop(plugin, {"index": 3}, umo="u:m")
    assert missing["data"]["reason"] == "stash_not_found"


async def test_drop_invalid_params(plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    r1 = await _drop(plugin, None, umo="u:m")
    assert r1["data"]["reason"] == "invalid_body"
    r2 = await _drop(plugin, {"index": -2}, umo="u:m")
    assert r2["data"]["reason"] == "invalid_param"
    r3 = await _drop(plugin, {"index": True}, umo="u:m")
    assert r3["data"]["reason"] == "invalid_param"


async def test_drop_no_project_loaded(plugin):
    result = await _drop(plugin, {"index": 0}, umo="u:none")
    assert result["data"]["dropped"] is False
    assert result["data"]["reason"] == "no_project_loaded"
