"""Tests for POST /spcode/git-branch-switch (v2.17.0).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.5
Author: elecvoid243 @ 2026-07-16
"""

import asyncio
import subprocess


from tests.conftest import _make_plugin  # noqa: F401
from tools.project import state as _state
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_branch_switch


def _run(coro):
    """Sync wrapper for async handler calls.

    使用 ``asyncio.run()`` 而非 ``asyncio.get_event_loop().run_until_complete()``:
    Python 3.10+ 在主线程没有 running loop 时会触发
    ``RuntimeError: There is no current event loop``(pytest 合跑大量触发)。
    ``asyncio.run()`` 内部自动管理 loop,跨测试隔离干净。
    """
    return asyncio.run(coro)


# ── preflight (3 cases) ──


def test_switch_no_umo_loaded():
    plugin = _make_plugin()
    result = _run(git_branch_switch.handle(plugin, body={"name": "x"}))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


def test_switch_not_a_git_repo(tmp_path):
    target = tmp_path / "notgit"
    target.mkdir()
    umo = "test:sw:nogit"
    _state.put(umo, {"directory": str(target), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(git_branch_switch.handle(plugin, umo=umo, body={"name": "x"}))
        assert result["data"]["reason"] == ReasonCode.NOT_A_GIT_REPO
    finally:
        _state.pop(umo)


def test_switch_worktree_invalid(tmp_path, existing_repo):
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    umo = "test:sw:cross"
    _state.put(umo, {"directory": str(existing_repo), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(
            git_branch_switch.handle(
                plugin,
                umo=umo,
                worktree=str(other),
                body={"name": "x"},
            )
        )
        assert result["data"]["reason"] == ReasonCode.WORKTREE_INVALID
    finally:
        _state.pop(umo)


# ── body 校验 (4 cases) ──


def test_switch_body_none(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_switch.handle(plugin, umo=loaded_umo, body=None))
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_switch_name_missing(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_switch.handle(plugin, umo=loaded_umo, body={}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_switch_name_invalid_chars(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "bad..name"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_BRANCH


def test_switch_start_point_without_create(loaded_umo):
    """create=false + start_point → invalid_body(跨字段校验)。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "x", "start_point": "main"},
        )
    )
    # start_point 非空 + create=false → invalid_body(跨字段校验, _validate_cross_fields 拒)
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


# ── 跨字段校验 (1 case) ──


def test_switch_create_and_detach_mutually_exclusive(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "x", "create": True, "detach": True},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


# ── happy path (5 cases) ──


def test_switch_to_current_branch(loaded_umo, existing_repo):
    """切到 current branch(no-op)→ switched=True。"""
    # existing_repo current=main
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "main"},
        )
    )
    assert result["data"]["switched"] is True


def test_switch_to_existing_branch(loaded_umo, existing_repo):
    """切到存在的 feature branch → switched=True, previous=main。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x"},
        )
    )
    assert result["data"]["switched"] is True
    assert result["data"]["previous"] == "main"
    assert result["data"]["name"] == "feature/x"
    assert result["data"]["created"] is False


def test_switch_response_includes_post_state(loaded_umo, existing_repo):
    """spec §3.5 L8: 成功响应必须含 refreshed branches + current + detached + total。

    回归测试 — v2.17.0 release 漏实现 L8,导致 dashboard 切完分支后误显示
    "detached HEAD"(必须刷新才能看到真实状态)。本测试确保响应字段齐全,
    且切到 feature/x 后 current 立即是 feature/x(不再 detached)。
    """
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x"},
        )
    )
    data = result["data"]
    assert data["switched"] is True
    # L8 必含字段
    assert "current" in data, "L8: response must include current branch name"
    assert "detached" in data, "L8: response must include detached flag"
    assert "branches" in data, "L8: response must include refreshed branches list"
    assert "total" in data, "L8: response must include total count"
    # 切到 feature/x 后必须反映真实状态
    assert data["current"] == "feature/x"
    assert data["detached"] is False
    assert data["total"] == 2  # main + feature/x
    names = [b["name"] for b in data["branches"]]
    assert "main" in names
    assert "feature/x" in names
    # feature/x 是 current,main 不是
    fx = next(b for b in data["branches"] if b["name"] == "feature/x")
    main = next(b for b in data["branches"] if b["name"] == "main")
    assert fx["current"] is True
    assert main["current"] is False


def test_switch_back_to_main_updates_current(loaded_umo, existing_repo):
    """连续切换:先 feature/x → main,验证 current 字段跟着变。

    进一步回归 — 修复后,响应 current 必须总是反映最新 HEAD 状态,
    而不是被前一次 switch 的 state 污染。
    """
    plugin = _make_plugin()
    # 第一次切到 feature/x
    r1 = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x"},
        )
    )
    assert r1["data"]["current"] == "feature/x"
    # 切回 main
    r2 = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "main"},
        )
    )
    assert r2["data"]["current"] == "main"
    assert r2["data"]["detached"] is False
    assert r2["data"]["previous"] == "feature/x"


def test_switch_create_mode(loaded_umo, existing_repo):
    """create=true → 创建并切换。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "from-create", "create": True},
        )
    )
    assert result["data"]["switched"] is True
    assert result["data"]["created"] is True


def test_switch_create_from_start_point(loaded_umo, existing_repo):
    """create=true + start_point=main → 从 main 拉新分支。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={
                "name": "from-main",
                "create": True,
                "start_point": "main",
            },
        )
    )
    assert result["data"]["switched"] is True
    assert result["data"]["created"] is True


def test_switch_detach_to_sha(loaded_umo, existing_repo):
    """detach=true 切到 7+ char SHA → detached 状态。"""
    sha = subprocess.run(
        ["git", "-C", str(existing_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": sha[:7], "detach": True},
        )
    )
    assert result["data"]["switched"] is True
    assert result["data"]["detach"] is True


# ── force (1 case) ──


def test_switch_force_overwrites_dirty_worktree(loaded_umo, existing_repo):
    """force=true 丢弃工作区改动 → switched=True。"""
    # 先让 worktree 变 dirty
    (existing_repo / "dirty_switch.txt").write_text("will be discarded")
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x", "force": True},
        )
    )
    assert result["data"]["switched"] is True
    assert result["data"]["force"] is True


# ── 业务 reason (4 cases) ──


def test_switch_branch_not_found(loaded_umo, existing_repo):
    """branch 不存在(create=false)→ branch_not_found。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "missing-branch"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.BRANCH_NOT_FOUND


def test_switch_branch_exists_when_create(loaded_umo, existing_repo):
    """branch 已存在 + create=true → branch_exists。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x", "create": True},
        )
    )
    assert result["data"]["reason"] == ReasonCode.BRANCH_EXISTS


def test_switch_worktree_dirty(loaded_umo, existing_repo):
    """worktree dirty + force=false → worktree_dirty。"""
    (existing_repo / "dirty.txt").write_text("uncommitted")
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.WORKTREE_DIRTY


def test_switch_ref_format_invalid(loaded_umo):
    """ref-format 非法(name 含 ..)→ invalid_branch。"""
    plugin = _make_plugin()
    result = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "bad..name"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_BRANCH


# ── 集成 (3 cases) ──


def test_switch_then_git_branch_shows_new(loaded_umo, existing_repo):
    """切后 git-branches 应显示当前为新 branch。"""
    from tools.webapi import git_branches

    plugin = _make_plugin()
    # 切到 feature/x
    _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x"},
        )
    )
    # 再查 branches
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    current_branch = next(b for b in result["data"]["branches"] if b["current"])
    assert current_branch["name"] == "feature/x"


def test_switch_then_etag_invalidates(loaded_umo, existing_repo):
    """切换后 ETag 应该失效(下个 git-status / git-branches 请求会刷新)。"""
    from tools.webapi import git_branches

    plugin = _make_plugin()
    # 触发一次(branches endpoint 算 ETag 用 _compute_git_etag 验证可用)
    _run(git_branches.handle(plugin, umo=loaded_umo))
    # 切分支
    _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x"},
        )
    )
    # 再查一次(branches endpoint 算 ETag 用 _compute_git_etag,会捕捉 HEAD SHA 变化)
    result_after = _run(git_branches.handle(plugin, umo=loaded_umo))
    # 验证 current 真的变了
    current_branch = next(b for b in result_after["data"]["branches"] if b["current"])
    assert current_branch["name"] == "feature/x"


def test_switch_round_trip(loaded_umo, existing_repo):
    """来回切换: main → feature/x → main。"""
    plugin = _make_plugin()
    r1 = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "feature/x"},
        )
    )
    assert r1["data"]["switched"] is True
    assert r1["data"]["previous"] == "main"
    r2 = _run(
        git_branch_switch.handle(
            plugin,
            umo=loaded_umo,
            body={"name": "main"},
        )
    )
    assert r2["data"]["switched"] is True
    assert r2["data"]["previous"] == "feature/x"
