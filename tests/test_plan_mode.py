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
