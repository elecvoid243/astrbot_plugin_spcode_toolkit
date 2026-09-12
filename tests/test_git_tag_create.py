"""Tests for POST /spcode/git-tag-create (2026-09-12).

提交对话框"顺带打 tag"的后端:git-commit 成功后前端携带返回 SHA 调用
本端点,把轻量 tag 精确锚定到刚创建的 commit 上。
"""

import asyncio
import subprocess

from tests.conftest import _make_plugin  # noqa: F401
from tools.webapi import git_tag_create
from tools.webapi._helpers import ReasonCode


def _run(coro):
    return asyncio.run(coro)


def _rev_parse(repo, rev: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", rev],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


# ── preflight ──────────────────────────────────────────────


def test_tag_no_umo_loaded():
    plugin = _make_plugin()
    result = _run(git_tag_create.handle(plugin, body={"tag": "v1"}))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


# ── body / param 校验 ──────────────────────────────────────


def test_tag_body_none(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_tag_create.handle(plugin, umo=loaded_umo, body=None))
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_tag_missing(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_tag_create.handle(plugin, umo=loaded_umo, body={}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_tag_invalid_chars(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_tag_create.handle(plugin, umo=loaded_umo, body={"tag": "bad..name"})
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_tag_too_long(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_tag_create.handle(plugin, umo=loaded_umo, body={"tag": "v" * 257})
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_tag_rev_invalid(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_tag_create.handle(
            plugin, umo=loaded_umo, body={"tag": "v1", "rev": "../bad"}
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


# ── happy path ─────────────────────────────────────────────


def test_tag_default_HEAD(loaded_umo, existing_repo):
    """rev 缺省 → 在 HEAD 上创建轻量 tag,并回读完整 SHA。"""
    plugin = _make_plugin()
    result = _run(git_tag_create.handle(plugin, umo=loaded_umo, body={"tag": "v1.0.0"}))
    data = result["data"]
    assert data["created"] is True
    assert data["tag"] == "v1.0.0"
    assert data["rev"] == "HEAD"
    assert data["sha"] == _rev_parse(existing_repo)
    # 验证 git 真的创建了
    out = subprocess.run(
        ["git", "-C", str(existing_repo), "tag", "--list", "v1.0.0"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "v1.0.0" in out.stdout


def test_tag_pinned_to_rev(loaded_umo, existing_repo):
    """rev=显式 SHA → tag 锚定到该 commit(提交对话框的锁定语义)。"""
    sha = _rev_parse(existing_repo)
    subprocess.run(
        ["git", "-C", str(existing_repo), "commit", "-q", "--allow-empty", "-m", "2nd"],
        check=True,
    )
    plugin = _make_plugin()
    result = _run(
        git_tag_create.handle(
            plugin, umo=loaded_umo, body={"tag": "v0.9.0", "rev": sha}
        )
    )
    data = result["data"]
    assert data["created"] is True
    # tag 指向旧 commit,不是新的 HEAD
    assert data["sha"] == sha
    assert _rev_parse(existing_repo, "v0.9.0") == sha
    assert _rev_parse(existing_repo) != sha


# ── 错误 ──────────────────────────────────────────────────


def test_tag_already_exists(loaded_umo):
    plugin = _make_plugin()
    r1 = _run(git_tag_create.handle(plugin, umo=loaded_umo, body={"tag": "dup"}))
    assert r1["data"]["created"] is True
    r2 = _run(git_tag_create.handle(plugin, umo=loaded_umo, body={"tag": "dup"}))
    assert r2["data"]["reason"] == ReasonCode.TAG_ALREADY_EXISTS


def test_tag_rev_not_found(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_tag_create.handle(
            plugin, umo=loaded_umo, body={"tag": "v1", "rev": "nonexistent-ref"}
        )
    )
    assert result["data"]["reason"] == ReasonCode.REF_NOT_FOUND


# ── 错误分类器 ─────────────────────────────────────────────


def test_classify_tag_error():
    f = git_tag_create._classify_tag_error
    assert f("fatal: tag 'dup' already exists") == ReasonCode.TAG_ALREADY_EXISTS
    assert f("fatal: 'v1.0' is not a valid tag name.") == ReasonCode.INVALID_PARAM
    assert f("fatal: 'zz' is not a valid 'rev' object") == ReasonCode.REF_NOT_FOUND
    assert f("fatal: some other failure") == ReasonCode.GIT_ERROR
