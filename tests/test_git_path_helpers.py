# Author: elecvoid243
# Date: 2026-07-23
"""Tests for shared Git path helpers in tools/webapi/_helpers.

Covers ``_get_staged_files`` which is shared by the git-stage,
git-unstage, and git-commit endpoints so all three return canonical
repository-relative paths.
"""

from __future__ import annotations

import pytest

from tools.webapi import _helpers

pytestmark = pytest.mark.asyncio


async def test_get_staged_files_uses_nul_delimited_output(monkeypatch) -> None:
    """The staged-file query must preserve path contents exactly."""
    calls: list[list[str]] = []

    async def fake_run_git_async(
        cmd_args: list[str],
        **kwargs,
    ) -> dict:
        calls.append(cmd_args)
        return {
            "ok": True,
            "stdout": ("中文 文档.txt\0 leading.txt\0trailing.txt \0line\nbreak.txt\0"),
            "stderr": "",
            "code": 0,
        }

    monkeypatch.setattr(_helpers, "_run_git_async", fake_run_git_async)

    files = await _helpers._get_staged_files("git", "/repo")

    assert files == [
        "中文 文档.txt",
        " leading.txt",
        "trailing.txt ",
        "line\nbreak.txt",
    ]
    assert calls == [
        [
            "git",
            "-C",
            "/repo",
            "-c",
            "color.ui=never",
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--diff-filter=AMRD",
        ]
    ]


async def test_get_staged_files_soft_fails_to_empty_list(monkeypatch) -> None:
    """A failed post-write staged query keeps the existing soft-fail behavior."""

    async def fake_run_git_async(
        cmd_args: list[str],
        **kwargs,
    ) -> dict:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "failed",
            "code": 1,
        }

    monkeypatch.setattr(_helpers, "_run_git_async", fake_run_git_async)

    assert await _helpers._get_staged_files("git", "/repo") == []
