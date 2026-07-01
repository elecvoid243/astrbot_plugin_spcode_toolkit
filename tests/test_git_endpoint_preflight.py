"""Tests for _git_endpoint_preflight shared helper.

PR-1 of git workflow endpoints design.
"""

from __future__ import annotations
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _make_plugin
from tools.webapi._helpers import _git_endpoint_preflight

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin():
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


async def test_preflight_feature_disabled(plugin):
    """feature flag 为 false → 返回 (envelope, None),reason=feature_disabled。"""
    plugin._config["agentsmd_enabled"] = False
    err, ctx = await _git_endpoint_preflight(plugin, umo="u:m", worktree_param=None)
    assert err is not None and ctx is None
    assert err["data"]["reason"] == "feature_disabled"


async def test_preflight_no_project_loaded(plugin):
    """无 umo + 无 fallback → reason=no_project_loaded。"""
    from tools.project import state as _proj_state

    _proj_state.reset()

    err, ctx = await _git_endpoint_preflight(plugin, umo=None, worktree_param=None)
    assert err is not None
    assert err["data"]["reason"] == "no_project_loaded"


async def test_preflight_directory_missing(plugin, tmp_path: Path):
    """loaded 项目目录不存在 → reason=directory_missing。"""
    from tools.project import state as _proj_state

    _proj_state.reset()
    _proj_state.put("u:m", {"directory": str(tmp_path / "deleted")})

    err, ctx = await _git_endpoint_preflight(plugin, umo="u:m", worktree_param=None)
    assert err is not None
    assert err["data"]["reason"] == "directory_missing"


async def test_preflight_not_a_git_repo(plugin, tmp_path: Path):
    """loaded 是目录但不是 git 仓库 → reason=not_a_git_repo。"""
    from tools.project import state as _proj_state

    _proj_state.reset()
    _proj_state.put("u:m", {"directory": str(tmp_path)})

    err, ctx = await _git_endpoint_preflight(plugin, umo="u:m", worktree_param=None)
    assert err is not None
    assert err["data"]["reason"] == "not_a_git_repo"


async def test_preflight_success_with_real_git_repo(plugin, tmp_path: Path):
    """loaded 是真 git 仓库 → 返回 (None, ctx)。"""
    _init_git_repo(tmp_path)

    from tools.project import state as _proj_state

    _proj_state.reset()
    _proj_state.put("u:m", {"directory": str(tmp_path), "loaded_at": 0.0})

    err, ctx = await _git_endpoint_preflight(plugin, umo="u:m", worktree_param=None)
    assert err is None
    assert ctx["directory"] == str(tmp_path)
    assert ctx["umo"] == "u:m"
