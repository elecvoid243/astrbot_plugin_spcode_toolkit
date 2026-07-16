"""Tests for POST /spcode/git-branch-create (v2.17.0).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.3
Author: elecvoid243 @ 2026-07-16
"""

import asyncio
import subprocess


from tests.conftest import _make_plugin  # noqa: F401
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_branch_create


def _run(coro):
    """Sync wrapper for async handler calls.

    使用 ``asyncio.run()`` 而非 ``asyncio.get_event_loop().run_until_complete()``:
    Python 3.10+ 在主线程没有 running loop 时 ``get_event_loop()`` 会触发
    ``RuntimeError: There is no current event loop``(实测合跑 pytest 大量触发)。
    ``asyncio.run()`` 内部自动管理 loop 创建/关闭,跨测试隔离干净。
    """
    return asyncio.run(coro)


# ── preflight ──────────────────────────────────────────────

def test_create_no_umo_loaded():
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(plugin, body={"name": "x"}))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


def test_create_empty_repo(loaded_umo, existing_repo):
    """HEAD 不存在(空仓库)→ empty_repository / git_error 兜底。"""
    subprocess.run(
        ["git", "-C", str(existing_repo), "update-ref", "-d", "HEAD"], check=True
    )
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "x"}
    ))
    assert result["data"]["reason"] in (
        ReasonCode.EMPTY_REPOSITORY,
        ReasonCode.GIT_ERROR,
    )


# ── body 校验 ──────────────────────────────────────────────

def test_create_body_none(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(plugin, umo=loaded_umo, body=None))
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_create_name_missing(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(plugin, umo=loaded_umo, body={}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_create_name_invalid_chars(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "bad..name"}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_BRANCH


def test_create_name_is_HEAD(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "HEAD"}
    ))
    # HEAD 不应作为分支名(rev parse 解析失败)
    assert result["data"]["reason"] == ReasonCode.INVALID_BRANCH


# ── happy path ─────────────────────────────────────────────

def test_create_default_from_HEAD(loaded_umo, existing_repo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "new-branch"}
    ))
    assert result["data"]["created"] is True
    assert result["data"]["name"] == "new-branch"
    # 验证 git 真的创建了
    out = subprocess.run(
        ["git", "-C", str(existing_repo), "branch", "--list", "new-branch"],
        capture_output=True, text=True, check=True,
    )
    assert "new-branch" in out.stdout


def test_create_with_start_point(loaded_umo, existing_repo):
    sha = subprocess.run(
        ["git", "-C", str(existing_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo,
        body={"name": "from-sha", "start_point": sha},
    ))
    assert result["data"]["created"] is True
    assert result["data"]["start_point"] == sha


def test_create_force_overwrites(loaded_umo, existing_repo):
    """force=true 覆盖已存在分支。"""
    plugin = _make_plugin()
    r1 = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "x-branch"}
    ))
    assert r1["data"]["created"] is True
    r2 = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "x-branch", "force": True}
    ))
    assert r2["data"]["created"] is True
    assert r2["data"]["force"] is True


# ── 错误 ──────────────────────────────────────────────────

def test_create_branch_exists(loaded_umo, existing_repo):
    """已存在分支 + force=false → branch_exists。"""
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "feature/x"}
    ))
    assert result["data"]["reason"] == ReasonCode.BRANCH_EXISTS


def test_create_start_point_invalid(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo,
        body={"name": "x", "start_point": "../bad-ref"},
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM
