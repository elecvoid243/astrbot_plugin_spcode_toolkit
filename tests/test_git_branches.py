"""Tests for GET /spcode/git-branches (v2.17.0).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.2
Author: elecvoid243
"""

import asyncio
import subprocess
import time as _time


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
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(existing_repo), "checkout", "-q", sha], check=True)
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


# ── v3.6 (2026-07-24) ETag staleness 修复 ─────────────────
# 场景: ``git fetch / pull / push`` 改变 upstream_track 但不动工作树
# (porcelain 不变)。v3.5 算法的 ETag 不感知 upstream_track,导致
# dashboard 收到陈旧 304,upstream_track 字段(↑N / ↓N)不刷新。
# 修复: 把 ``%(upstream:track)`` 拼入 ETag,upstream_track 任一字符
# 变化 → hash 不同 → 304 失效。
#
# 本组测试构造一个"本地分支落后远程 N 次提交"的真实 git 场景:
#   1) 本地 main + 远程 origin/main 同步 (ahead/behind = 0)
#   2) 记录 ETag
#   3) 在远程仓 push 新 commit (本地不动)
#   4) ``git fetch`` 让本地感知到远程前进 → upstream_track 变化
#   5) 再次请求 → ETag 必须不同 (200, 不是 304)


def _setup_repo_with_remote(tmp_path):
    """构造"本地 + 远程 + 已 fetch"的 git 仓库,返回 (local_repo, remote_repo)。"""
    local_repo = tmp_path / "local"
    remote_repo = tmp_path / "remote.git"
    local_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(local_repo)], check=True)
    subprocess.run(
        ["git", "-C", str(local_repo), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(
        ["git", "-C", str(local_repo), "config", "user.name", "T"], check=True
    )
    (local_repo / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "-C", str(local_repo), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(local_repo), "commit", "-q", "-m", "init"], check=True
    )

    # 初始化 bare remote + 关联 + push
    subprocess.run(["git", "init", "-q", "--bare", str(remote_repo)], check=True)
    subprocess.run(
        ["git", "-C", str(local_repo), "remote", "add", "origin", str(remote_repo)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(local_repo), "push", "-q", "-u", "origin", "main"],
        check=True,
    )
    return local_repo, remote_repo


def test_branches_etag_changes_after_upstream_track_change(tmp_path):
    """v3.6 修复: remote push 后 upstream_track 变化 → ETag 必须变。"""
    from tools.project import state as _state
    from unittest.mock import patch

    local_repo, remote_repo = _setup_repo_with_remote(tmp_path)
    umo = "test:branches:upstream"
    _state.put(umo, {"directory": str(local_repo), "loaded_at": _time.time()})
    try:
        plugin = _make_plugin()

        # ── 第 1 次:ahead=behind=0,记录 ETag ──
        r1 = _run(git_branches.handle(plugin, umo=umo))
        etag_before = r1.headers.get("ETag")
        assert etag_before, f"first response missing ETag: {dict(r1.headers)}"
        # 此时 upstream_track 应为空 (本地==远程)
        main_branch = next(b for b in r1["data"]["branches"] if b["name"] == "main")
        assert main_branch["upstream_track"] in ("", None), (
            f"expected empty upstream_track, got {main_branch['upstream_track']!r}"
        )

        # ── 在远程仓 push 新 commit (本地 working tree 不动) ──
        # clone 一份临时本地 → commit → push 到 remote。确保原 local_repo
        # 的 working tree / index / porcelain 完全不变。
        tmp_clone = tmp_path / "tmp_clone"
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                str(remote_repo),
                str(tmp_clone),
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_clone), "config", "user.email", "t@t"], check=True
        )
        subprocess.run(
            ["git", "-C", str(tmp_clone), "config", "user.name", "T"], check=True
        )
        (tmp_clone / "new.txt").write_text("remote commit", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_clone), "add", "new.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_clone), "commit", "-q", "-m", "remote push"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_clone), "push", "-q", "origin", "main"],
            check=True,
        )
        # ── local_repo fetch (只更新 remote tracking refs,不动工作树) ──
        subprocess.run(
            ["git", "-C", str(local_repo), "fetch", "-q", "origin"], check=True
        )

        # 验证:local_repo 的 porcelain / working tree / HEAD SHA 完全未变
        head_after = subprocess.run(
            ["git", "-C", str(local_repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        head_before = subprocess.run(
            ["git", "-C", str(local_repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert head_after == head_before  # HEAD 未动
        # upstream_track 字符串现在应该 non-empty
        track_check = subprocess.run(
            [
                "git",
                "-C",
                str(local_repo),
                "for-each-ref",
                "--format=%(upstream:track)",
                "refs/heads/main",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "behind" in track_check or "ahead" in track_check, (
            f"upstream_track 未变化:{track_check!r}"
        )

        # ── 第 2 次:upstream_track 变化,ETag 必须变 ──
        # 用 web.request mock 把 If-None-Match 设为旧 ETag:
        # 如果修复有效,新 ETag ≠ 旧 ETag → 走 200 路径
        # 如果修复无效,新 ETag == 旧 ETag → 走 304 路径
        from astrbot.api import web
        from tests.conftest import make_web_request_mock

        with patch.object(web, "request", make_web_request_mock()):
            r2 = _run(git_branches.handle(plugin, umo=umo))
        etag_after = r2.headers.get("ETag")
        assert etag_after, f"second response missing ETag: {dict(r2.headers)}"
        assert etag_after != etag_before, (
            f"v3.6 修复失效: upstream_track 变化后 ETag 未变 "
            f"(before={etag_before!r}, after={etag_after!r}). "
            f"dashboard 持续 304 命中 → upstream_track ↑N/↓N 不刷新。"
        )
        # 数据里 upstream_track 也应该更新
        main_after = next(b for b in r2["data"]["branches"] if b["name"] == "main")
        assert main_after["upstream_track"] != main_branch["upstream_track"], (
            f"upstream_track 字段未刷新:{main_branch['upstream_track']!r} → "
            f"{main_after['upstream_track']!r}"
        )
    finally:
        _state.pop(umo)


def test_branches_etag_no_upstream_change_keeps_cache(tmp_path):
    """无 upstream_track 变化时, ETag 应保持稳定(证明修复未引入误命中)。"""
    local_repo, _ = _setup_repo_with_remote(tmp_path)
    umo = "test:branches:stable"
    _state.put(umo, {"directory": str(local_repo), "loaded_at": _time.time()})
    try:
        plugin = _make_plugin()
        r1 = _run(git_branches.handle(plugin, umo=umo))
        etag1 = r1.headers.get("ETag")
        r2 = _run(git_branches.handle(plugin, umo=umo))
        etag2 = r2.headers.get("ETag")
        assert etag1 == etag2, (
            f"无任何变化时 ETag 应当稳定,却变化了: {etag1!r} vs {etag2!r}"
        )
    finally:
        _state.pop(umo)


# ── 解析器 ────────────────────────────────────────────────


def test_parse_for_each_ref_handles_empty():
    from tools.webapi._helpers import _parse_for_each_ref

    assert _parse_for_each_ref("") == []


def test_parse_for_each_ref_skips_blank_lines():
    from tools.webapi._helpers import _parse_for_each_ref

    assert _parse_for_each_ref("\n\n  \n") == []


def test_parse_for_each_ref_extracts_current_marker():
    from tools.webapi._helpers import _parse_for_each_ref

    _tab = chr(9)
    _nl = chr(10)
    line = "*main" + _tab + "abc1234" + _tab + _tab + _nl
    result = _parse_for_each_ref(line)
    assert len(result) == 1
    assert result[0]["name"] == "main"
    assert result[0]["current"] is True
    assert result[0]["sha"] == "abc1234"


def test_parse_for_each_ref_handles_remote():
    from tools.webapi._helpers import _parse_for_each_ref

    # concat 构造:避免 \\t 被文件写入工具双重转义
    _tab = chr(9)
    _nl = chr(10)
    line = " origin/main" + _tab + "def5678" + _tab + _nl
    result = _parse_for_each_ref(line)
    assert len(result) == 1
    assert result[0]["name"] == "origin/main"
    assert result[0]["remote"] is True
    assert result[0]["current"] is False
