"""Smoke test for webapi/plan_mode.handle.

Handler 从 main.py 搬出,行为不变。
"""

from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from tools.webapi import plan_mode

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


async def test_handle_returns_dict_with_plan_mode_status():
    """handler 返回的 dict 必含 data.active 字段。"""
    plugin = MagicMock()
    # PR-3 (2026-06-23): webapi 端委托给 PlanModeController
    plugin._plan = MagicMock()
    plugin._plan.is_active = MagicMock(return_value=False)
    plugin._plan.count_active = MagicMock(return_value=0)
    result = await plan_mode.handle(plugin)
    assert isinstance(result, dict)
    assert result["status"] == "ok"
    assert "active" in result["data"]
    assert result["data"]["active"] is False
    assert result["data"]["all_active_count"] == 0
    assert "umo" in result["data"]


async def test_handle_returns_active_true_when_plan_mode():
    """_plan.is_active() 返回 True 时,data.active == True。"""
    plugin = MagicMock()
    plugin._plan = MagicMock()
    plugin._plan.is_active = MagicMock(return_value=True)
    plugin._plan.count_active = MagicMock(return_value=1)
    result = await plan_mode.handle(plugin)
    assert result["data"]["active"] is True
    assert result["data"]["all_active_count"] == 1


# ── POST /spcode/plan-mode (handle_set) — v2.22.0 ─────────────────
#
# dashboard plan/build chip 改为直接调 API 切换模式,不再向聊天框
# 注入 /plan /build 用户消息。handler 委托给真实的
# PlanModeController(非 mock),验证端到端状态翻转。


def _make_plugin_with_real_plan() -> MagicMock:
    """Plugin mock whose ``_plan`` is a real PlanModeController."""
    from tools.security.plan_mode import PlanModeController

    plugin = MagicMock()
    plugin._plan = PlanModeController(lambda: {})
    return plugin


async def test_handle_set_activates_plan_mode():
    """active=True 激活 plan 模式,changed=True,all_active_count=1。"""
    plugin = _make_plugin_with_real_plan()
    result = await plan_mode.handle_set(plugin, umo="u1", body={"active": True})
    assert result["status"] == "ok"
    assert result["data"]["active"] is True
    assert result["data"]["changed"] is True
    assert result["data"]["all_active_count"] == 1
    assert result["data"]["umo"] == "u1"
    assert plugin._plan.is_active("u1") is True


async def test_handle_set_deactivates_plan_mode():
    """已激活的 umo 传 active=False 退出 plan 模式。"""
    plugin = _make_plugin_with_real_plan()
    plugin._plan.activate("u1")
    result = await plan_mode.handle_set(plugin, umo="u1", body={"active": False})
    assert result["status"] == "ok"
    assert result["data"]["active"] is False
    assert result["data"]["changed"] is True
    assert result["data"]["all_active_count"] == 0
    assert plugin._plan.is_active("u1") is False


async def test_handle_set_noop_when_state_unchanged():
    """重复设置相同状态时 changed=False(幂等)。"""
    plugin = _make_plugin_with_real_plan()
    result = await plan_mode.handle_set(plugin, umo="u1", body={"active": False})
    assert result["status"] == "ok"
    assert result["data"]["changed"] is False


async def test_handle_set_rejects_missing_umo():
    """缺 umo 返回 error envelope,不触碰状态。"""
    plugin = _make_plugin_with_real_plan()
    result = await plan_mode.handle_set(plugin, umo=None, body={"active": True})
    assert result["status"] == "error"
    assert plugin._plan.count_active() == 0


async def test_handle_set_rejects_non_bool_active():
    """active 非 bool(缺失/字符串)返回 error envelope。"""
    plugin = _make_plugin_with_real_plan()
    result = await plan_mode.handle_set(plugin, umo="u1", body={})
    assert result["status"] == "error"
    result = await plan_mode.handle_set(plugin, umo="u1", body={"active": "yes"})
    assert result["status"] == "error"
    assert plugin._plan.count_active() == 0


async def test_plan_mode_post_route_registered():
    """ROUTES 表中必须存在 POST /spcode/plan-mode 条目。"""
    from tools.webapi import ROUTES

    matches = [r for r in ROUTES if r[0] == "/spcode/plan-mode" and "POST" in r[1]]
    assert len(matches) == 1
    assert matches[0][2] is plan_mode.handle_set
