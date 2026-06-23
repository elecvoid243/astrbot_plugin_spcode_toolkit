"""Smoke test for webapi/git_worktrees.handle.

Handler 从 main.py 搬出,行为不变。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from tools.project import state as _proj_state
from tools.webapi import git_worktrees

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


def test_make_git_worktrees_empty_envelope_has_required_fields():
    """空 envelope 必须含 loaded=False + reason 字段。"""
    env = git_worktrees._make_git_worktrees_empty_envelope(
        umo="test-umo", reason="no_project_loaded", elapsed_ms=5
    )
    assert env["status"] == "ok"
    assert env["data"]["loaded"] is False
    assert env["data"]["umo"] == "test-umo"
    assert env["data"]["reason"] == "no_project_loaded"
    assert env["data"]["worktrees"] is None
    assert env["data"]["elapsed_ms"] == 5


async def test_handle_returns_no_project_loaded_envelope():
    """空 _loaded_projects 时,返回 no_project_loaded envelope。"""
    plugin = MagicMock()
    plugin.get_loaded_project.side_effect = lambda umo: _proj_state.get(umo)
    _proj_state.reset()
    result = await git_worktrees.handle(plugin)
    assert result["status"] == "ok"
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "no_project_loaded"
    assert result["data"]["worktrees"] is None
