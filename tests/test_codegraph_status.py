"""Tests for GET /spcode/codegraph-status endpoint.

Spec: docs/superpowers/specs/2026-06-28-codegraph-status-endpoint-design.md
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from tools.webapi.codegraph_status import handle


def _make_plugin(
    *,
    enabled: bool = True,
    mcp_running: bool = False,
    active_project: str = "",
) -> MagicMock:
    """Create a minimal plugin mock with codegraph state."""
    plugin = MagicMock()
    plugin._config = {
        "codegraph_enabled": enabled,
    }

    # Mock the LLM tool manager's mcp_server_runtime
    mgr = MagicMock()
    if mcp_running:
        mgr.mcp_server_runtime = {"codegraph": MagicMock()}
    else:
        mgr.mcp_server_runtime = {}
    plugin.context.get_llm_tool_manager.return_value = mgr

    # Inject active_project into tools.codegraph.state
    from tools.codegraph import state as cg_state

    cg_state.set_active_project_path(active_project)

    return plugin


# ── Codegraph disabled ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_returns_enabled_false():
    """codegraph_enabled=false -> enabled=false, mcp_running=false, active_project=''."""
    plugin = _make_plugin(enabled=False)
    result = await handle(plugin)
    assert result["status"] == "ok"
    d = result["data"]
    assert d["enabled"] is False
    assert d["mcp_running"] is False
    assert d["active_project"] == ""


# ── Enabled but no active project, MCP not running ─────────────


@pytest.mark.asyncio
async def test_enabled_no_project_mcp_off():
    """codegraph 启用但无项目, MCP 未跑 -> active_project=''."""
    plugin = _make_plugin(enabled=True, mcp_running=False, active_project="")
    result = await handle(plugin)
    assert result["status"] == "ok"
    d = result["data"]
    assert d["enabled"] is True
    assert d["mcp_running"] is False
    assert d["active_project"] == ""


# ── Enabled, no active project, MCP running ────────────────────


@pytest.mark.asyncio
async def test_enabled_no_project_mcp_on():
    """MCP 在跑但项目未设置 -> mcp_running=true, active_project=''."""
    plugin = _make_plugin(enabled=True, mcp_running=True, active_project="")
    result = await handle(plugin)
    assert result["status"] == "ok"
    d = result["data"]
    assert d["enabled"] is True
    assert d["mcp_running"] is True
    assert d["active_project"] == ""


# ── Enabled, project set, MCP running (happy path) ─────────────


@pytest.mark.asyncio
async def test_enabled_with_project_mcp_on():
    """标准 happy path: 有项目, MCP 在跑."""
    plugin = _make_plugin(
        enabled=True,
        mcp_running=True,
        active_project=r"C:\projects\myapp",
    )
    result = await handle(plugin)
    assert result["status"] == "ok"
    d = result["data"]
    assert d["enabled"] is True
    assert d["mcp_running"] is True
    assert d["active_project"] == r"C:\projects\myapp"


# ── Enabled, project set, MCP stopped ──────────────────────────


@pytest.mark.asyncio
async def test_enabled_with_project_mcp_off():
    """项目路径已记录但 MCP 已停止 -> active_project 保留."""
    plugin = _make_plugin(
        enabled=True,
        mcp_running=False,
        active_project=r"C:\projects\myapp",
    )
    result = await handle(plugin)
    assert result["status"] == "ok"
    d = result["data"]
    assert d["enabled"] is True
    assert d["mcp_running"] is False
    assert d["active_project"] == r"C:\projects\myapp"


# ── state.reset() clears active_project ────────────────────────


@pytest.mark.asyncio
async def test_state_reset_clears_active_project():
    """reset() 后 get_active_project_path() 返回空字符串."""
    from tools.codegraph import state as cg_state

    cg_state.set_active_project_path(r"D:\tmp")
    assert cg_state.get_active_project_path() == r"D:\tmp"
    cg_state.reset()
    assert cg_state.get_active_project_path() == ""


# ── Smoke: handler is callable and returns dict ────────────────


@pytest.mark.asyncio
async def test_handler_callable_returns_dict():
    """基础 smoke: 始终返回 dict."""
    plugin = _make_plugin()
    result = await handle(plugin)
    assert isinstance(result, dict)
