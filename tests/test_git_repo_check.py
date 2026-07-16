"""Tests for GET /spcode/git-repo-check (v2.18.0).

验证"判断给定目录是否为 git 仓库"的独立端点。该端点直接接受 ``path`` 参数,
不依赖 umo / 项目加载状态,与 ``file-browser`` / ``git-init`` 一致。

Author: elecvoid243
Created: 2026-07-16
"""

import asyncio

from tools.webapi import git_repo_check
from tools.webapi._helpers import ReasonCode

from tests.conftest import _make_plugin


def _run(coro):
    """Sync wrapper for async handler calls."""
    return asyncio.run(coro)


# ── 参数校验 ─────────────────────────────────────────────


def test_repo_check_empty_path():
    """path 为空时返回 invalid_param。"""
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM
    assert result["data"].get("is_git_repo") is None
    assert result["data"].get("git_available") is None


def test_repo_check_whitespace_path():
    """path 仅空白时返回 invalid_param。"""
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path="   "))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


# ── 路径安全 ─────────────────────────────────────────────


def test_repo_check_path_not_directory(tmp_path):
    """path 指向文件而非目录 -> path_not_directory。"""
    filepath = tmp_path / "file.txt"
    filepath.write_text("hi", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path=str(filepath)))
    assert result["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY
    assert result["data"].get("is_git_repo") is None


def test_repo_check_path_unsafe():
    """系统敏感目录 -> path_unsafe。"""
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path="C:\\Windows\\System32"))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


# ── 核心:git repo probe ──────────────────────────────────


def test_repo_check_is_git_repo(existing_repo):
    """git 仓库 -> is_git_repo=True, git_available=True, reason=None。"""
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path=str(existing_repo)))
    data = result["data"]
    assert data["reason"] is None
    assert data["is_git_repo"] is True
    assert data["git_available"] is True
    assert data["directory"] == str(existing_repo.resolve())


def test_repo_check_not_a_git_repo(tmp_path):
    """非 git 目录 -> is_git_repo=False, git_available=True, reason=not_a_git_repo。"""
    notgit = tmp_path / "notgit"
    notgit.mkdir()
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path=str(notgit)))
    data = result["data"]
    assert data["reason"] == ReasonCode.NOT_A_GIT_REPO
    assert data["is_git_repo"] is False
    assert data["git_available"] is True


def test_repo_check_git_unavailable(monkeypatch, existing_repo):
    """git 可执行文件不存在 -> git_available=False, reason=git_unavailable。"""
    plugin = _make_plugin()
    monkeypatch.setattr(plugin, "_git_binary", lambda: "/nonexistent/git/binary")
    result = _run(git_repo_check.handle(plugin, path=str(existing_repo)))
    data = result["data"]
    assert data["reason"] == ReasonCode.GIT_UNAVAILABLE
    assert data["git_available"] is False
    assert data.get("is_git_repo") is None


# ── 附加验证 ─────────────────────────────────────────────


def test_repo_check_envelope_structure(existing_repo):
    """成功路径的 envelope 包含必需字段。"""
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path=str(existing_repo)))
    data = result["data"]
    assert "elapsed_ms" in data
    assert "directory" in data


def test_repo_check_elapsed_ms_is_number(existing_repo):
    """elapsed_ms 应为非负数值。"""
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path=str(existing_repo)))
    elapsed = result["data"]["elapsed_ms"]
    assert isinstance(elapsed, (int, float))
    assert elapsed >= 0


def test_repo_check_no_umo_dependency(existing_repo):
    """端点不依赖 umo:不传 umo 也能正常工作。"""
    plugin = _make_plugin()
    result = _run(git_repo_check.handle(plugin, path=str(existing_repo)))
    assert result["data"]["is_git_repo"] is True
    # 响应中不应有 umo 字段
    assert "umo" not in result["data"]
