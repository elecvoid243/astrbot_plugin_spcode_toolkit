"""Smoke test for webapi/project_status.handle.

Handler 直接从 main.py 搬出,行为不变。
测试不依赖 main.py 内部 _loaded_projects 状态,只验证 handler 可被调用并返回 dict。
"""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from tools.webapi import project_status

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


async def test_handle_returns_dict_with_status_field():
    """handler 必须返回 dict 且含 status 字段。"""
    plugin = MagicMock()
    plugin._loaded_projects = {}
    result = await project_status.handle(plugin, umo=None)
    assert isinstance(result, dict)
    assert "status" in result
    assert result["status"] == "ok"
    assert "data" in result


async def test_handle_returns_not_loaded_for_empty_projects():
    """空 _loaded_projects 时,data.loaded == False。

    注:umo 字段值来自运行时 web.request.query,本 smoke test
    只验证行为分支,不依赖 web 全局上下文。
    """
    plugin = MagicMock()
    plugin._loaded_projects = {}
    result = await project_status.handle(plugin)
    assert result["data"]["loaded"] is False
    assert result["data"]["directory"] is None
    assert result["data"]["all_loaded_count"] == 0
    assert "umo" in result["data"]
