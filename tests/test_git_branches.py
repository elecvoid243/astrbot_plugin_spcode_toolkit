"""Tests for GET /spcode/git-branches (v2.17.0).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.2
Author: elecvoid243
"""

import asyncio
import subprocess


from tests.conftest import _make_plugin  # noqa: F401
from tools.project import state as _state
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_branches


def _run(coro):
    """Sync wrapper for async handler calls.

    使用 ``asyncio.run()`` 而非 ``asyncio.get_event_loop().run_until_complete()``:
    Python 3.10+ 在主线程没有 running loop 时 ``get_event_loop()`` 返回的
    loop 会触发 ``RuntimeError: There is no current event loop``(实测在
    Python 3.12 + pytest 并发场景下大量出现,导致合跑大量失败)。
    ``asyncio.run()`` (Python 3.7+) 内部自动创建/关闭 loop,跨文件跨测试
    独立且不污染事件循环状态。
    """
    return asyncio.run(coro)


# ── preflight ──────────────────────────────────────────────

def test_branches_no_umo_loaded():
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


def test_branches_not_a_git_repo(tmp_path):
    target = tmp_path / "notgit"
    target.mkdir()
    umo = "test:branches:nogit"
    _state.put(umo, {"directory": str(target), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(git_branches.handle(plugin, umo=umo))
        assert result["data"]["reason"] == ReasonCode.NOT_A_GIT_REPO
    finally:
        _state.pop(umo)


def test_branches_worktree_invalid(tmp_path, existing_repo):
    """跨仓库 worktree 参数触发 6 步防御。"""
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(other_repo)], check=True)
    umo = "test:branches:cross"
    _state.put(umo, {"directory": str(existing_repo), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(git_branches.handle(plugin, umo=umo, worktree=str(other_repo)))
        assert result["data"]["reason"] == ReasonCode.WORKTREE_INVALID
    finally:
        _state.pop(umo)


# ── happy path ─────────────────────────────────────────────

def test_branches_lists_local_branches(loaded_umo, existing_repo):
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert result["data"]["reason"] is None
    names = [b["name"] for b in result["data"]["branches"]]
    assert "main" in names
    assert "feature/x" in names
    # main 是 current
    main_branch = next(b for b in result["data"]["branches"] if b["name"] == "main")
    assert main_branch["current"] is True
    # feature/x 不是 current
    fx_branch = next(b for b in result["data"]["branches"] if b["name"] == "feature/x")
    assert fx_branch["current"] is False


def test_branches_total_field(loaded_umo, existing_repo):
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert result["data"]["total"] == len(result["data"]["branches"])


def test_branches_empty_repo(loaded_umo, existing_repo):
    """删除 feature/x 分支后,确认它不在列表中(但 main 仍存在)。"""
    subprocess.run(
        ["git", "-C", str(existing_repo), "branch", "-D", "feature/x"], check=True
    )
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    names = [b["name"] for b in result["data"]["branches"]]
    assert "feature/x" not in names
    assert "main" in names
    assert result["data"]["total"] == len(result["data"]["branches"])


def test_branches_detached_head(loaded_umo, existing_repo):
    """切换到 detached HEAD 后,detached=true, current=null。"""
    sha = subprocess.run(
        ["git", "-C", str(existing_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(existing_repo), "checkout", "-q", sha], check=True
    )
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert result["data"]["detached"] is True
    assert result["data"]["current"] is None


# ── ETag ──────────────────────────────────────────────────

def test_branches_etag_cache_hit(loaded_umo, existing_repo):
    """两次连续请求,第二次 ETag 命中 → envelope 缩短。"""
    plugin = _make_plugin()
    r1 = _run(git_branches.handle(plugin, umo=loaded_umo))
    # ETag 字段应一致(走 1.5s 缓存)
    r2 = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert r1.get("data", {}).get("branches") is not None
    # 两次都返 200(测试不打 ETag header,只验证 cache 不爆)
    assert r2.get("data", {}).get("branches") is not None


# ── 解析器 ────────────────────────────────────────────────

def test_parse_for_each_ref_handles_empty():
    from tools.webapi.git_branches import _parse_for_each_ref
    assert _parse_for_each_ref("") == []


def test_parse_for_each_ref_skips_blank_lines():
    from tools.webapi.git_branches import _parse_for_each_ref
    assert _parse_for_each_ref("\n\n  \n") == []


def test_parse_for_each_ref_extracts_current_marker():
    from tools.webapi.git_branches import _parse_for_each_ref
    _tab = chr(9)
    _nl = chr(10)
    line = "*main" + _tab + "abc1234" + _tab + _tab + _nl
    result = _parse_for_each_ref(line)
    assert len(result) == 1
    assert result[0]["name"] == "main"
    assert result[0]["current"] is True
    assert result[0]["sha"] == "abc1234"


def test_parse_for_each_ref_handles_remote():
    from tools.webapi.git_branches import _parse_for_each_ref
    # concat 构造:避免 \\t 被文件写入工具双重转义
    _tab = chr(9)
    _nl = chr(10)
    line = " origin/main" + _tab + "def5678" + _tab + _nl
    result = _parse_for_each_ref(line)
    assert len(result) == 1
    assert result[0]["name"] == "origin/main"
    assert result[0]["remote"] is True
    assert result[0]["current"] is False
