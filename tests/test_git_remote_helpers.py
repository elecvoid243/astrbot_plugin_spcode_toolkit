"""Tests for shared git remote Web API helpers."""

import os
from unittest.mock import AsyncMock, patch

import pytest


def test_remote_name_validation():
    from tools.webapi._git_remote import _is_valid_remote_name

    assert _is_valid_remote_name("origin")
    assert _is_valid_remote_name("upstream")
    assert _is_valid_remote_name("team-origin")
    assert not _is_valid_remote_name("")
    assert not _is_valid_remote_name(None)
    assert not _is_valid_remote_name("-origin")
    assert not _is_valid_remote_name("bad name")
    assert not _is_valid_remote_name("bad..name")
    assert not _is_valid_remote_name("x" * 129)


def test_remote_url_validation():
    from tools.webapi._git_remote import _is_valid_remote_url

    assert _is_valid_remote_url("https://example.com/org/repo.git")
    assert _is_valid_remote_url("git@example.com:org/repo.git")
    assert _is_valid_remote_url("F:/tmp/remote.git")
    assert not _is_valid_remote_url("")
    assert not _is_valid_remote_url(None)
    assert not _is_valid_remote_url("https://example.com/a\nb")
    assert not _is_valid_remote_url("x" * 2049)


def test_remote_url_masking():
    from tools.webapi._git_remote import _mask_remote_url

    assert (
        _mask_remote_url("https://user:secret@example.com/org/repo.git")
        == "https://***@example.com/org/repo.git"
    )
    assert (
        _mask_remote_url("git@example.com:org/repo.git")
        == "***@example.com:org/repo.git"
    )


def test_remote_env_disables_prompts(monkeypatch):
    from tools.webapi._git_remote import _build_remote_git_env

    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    env = _build_remote_git_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == "echo"
    assert env["SSH_ASKPASS"] == "echo"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "PATH" in env


def test_remote_error_classification():
    from tools.webapi._git_remote import _classify_remote_error
    from tools.webapi._helpers import ReasonCode

    assert (
        _classify_remote_error("fatal: Authentication failed")
        == ReasonCode.AUTH_REQUIRED
    )
    assert (
        _classify_remote_error("fatal: Could not resolve host example")
        == ReasonCode.NETWORK_ERROR
    )
    assert _classify_remote_error("命令超时 (60.0s)") == ReasonCode.NETWORK_ERROR
    assert (
        _classify_remote_error("! [rejected] main -> main (non-fast-forward)")
        == ReasonCode.NON_FAST_FORWARD
    )
    assert (
        _classify_remote_error("remote: pre-receive hook declined")
        == ReasonCode.PUSH_REJECTED
    )
    assert _classify_remote_error("fatal: unexpected") == ReasonCode.GIT_ERROR


def test_upstream_parse():
    from tools.webapi._git_remote import _parse_upstream

    assert _parse_upstream("origin/main") == ("origin", "main")
    assert _parse_upstream("upstream/feature/x") == ("upstream", "feature/x")


def test_up_to_date_detection():
    from tools.webapi._git_remote import (
        _is_already_up_to_date,
        _is_everything_up_to_date,
    )

    assert _is_already_up_to_date("Already up to date.\n", "")
    assert _is_already_up_to_date("", "Already up-to-date.\n")
    assert not _is_already_up_to_date("Updating abc..def", "")
    assert _is_everything_up_to_date("", "Everything up-to-date\n")
    assert not _is_everything_up_to_date("main -> main", "")


def test_remote_verbose_parse_fetch_wins():
    """git remote -v 双行(fetch/push)解析, fetch URL 优先。"""
    from tools.webapi.git_remotes import _parse_remote_verbose

    tab = chr(9)
    out = (
        f"origin{tab}https://example.com/old.git (fetch)\n"
        f"origin{tab}https://example.com/old.git (push)\n"
        f"upstream{tab}git@github.com:o/u.git (push)\n"
    )
    parsed = _parse_remote_verbose(out)
    assert parsed == [
        {"name": "origin", "url": "https://example.com/old.git"},
        {"name": "upstream", "url": "git@github.com:o/u.git"},
    ]


def test_remote_verbose_parse_ignores_garbage():
    """空行 / 缺 tab 的行被忽略, 名称排序稳定。"""
    from tools.webapi.git_remotes import _parse_remote_verbose

    assert _parse_remote_verbose("  \nnot-a-remote-line\n") == []


@pytest.mark.asyncio
async def test_read_current_branch_and_detached():
    from tools.webapi._git_remote import _read_current_branch

    with patch(
        "tools.webapi._git_remote._run_git_async",
        new_callable=AsyncMock,
        return_value={"ok": True, "stdout": "main\n", "stderr": "", "code": 0},
    ):
        assert await _read_current_branch("git", "/repo") == "main"

    with patch(
        "tools.webapi._git_remote._run_git_async",
        new_callable=AsyncMock,
        return_value={"ok": True, "stdout": "HEAD\n", "stderr": "", "code": 0},
    ):
        assert await _read_current_branch("git", "/repo") is None


@pytest.mark.asyncio
async def test_read_upstream_missing():
    from tools.webapi._git_remote import _read_upstream

    with patch(
        "tools.webapi._git_remote._run_git_async",
        new_callable=AsyncMock,
        return_value={
            "ok": False,
            "stdout": "",
            "stderr": "fatal: no upstream configured",
            "code": 128,
        },
    ):
        assert await _read_upstream("git", "/repo") is None


@pytest.mark.asyncio
async def test_get_remote_url():
    from tools.webapi._git_remote import _get_remote_url

    with patch(
        "tools.webapi._git_remote._run_git_async",
        new_callable=AsyncMock,
        return_value={
            "ok": True,
            "stdout": "https://example.com/repo.git\n",
            "stderr": "",
            "code": 0,
        },
    ):
        assert await _get_remote_url("git", "/repo", "origin") == (
            True,
            "https://example.com/repo.git",
            "",
        )


@pytest.mark.asyncio
async def test_read_head_sha_failure_returns_empty():
    from tools.webapi._git_remote import _read_head_sha

    with patch(
        "tools.webapi._git_remote._run_git_async",
        new_callable=AsyncMock,
        return_value={"ok": False, "stdout": "", "stderr": "fatal", "code": 128},
    ):
        assert await _read_head_sha("git", "/repo") == ""
