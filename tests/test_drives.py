"""Unit tests for webapi/drives.handle.

Spec: D:\\AstrBotWorkSpace\\spcode-目录选择器设计方案.md（2026-08-15 增补：多盘符导航）
Author: elecvoid243, 2026-08-15
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from tools.webapi import drives

pytestmark = pytest.mark.asyncio


async def test_handle_returns_ok_with_drives_list():
    """返回 status=ok + data.drives 为非空绝对路径列表。"""
    result = await drives.handle(MagicMock())
    assert result["status"] == "ok"
    lst = result["data"]["drives"]
    assert isinstance(lst, list)
    assert lst, "drives 不应为空(至少包含 C:\\ 或 /)"
    for d in lst:
        assert isinstance(d, str)
        assert os.path.isabs(d), f"drive 必须是绝对路径: {d!r}"


async def test_drives_route_registered():
    """ROUTES 表中必须存在 GET /spcode/drives 条目。"""
    from tools.webapi import HANDLERS, ROUTES

    matches = [r for r in ROUTES if r[0] == "/spcode/drives" and "GET" in r[1]]
    assert len(matches) == 1
    assert matches[0][2] is drives.handle
    assert HANDLERS["handle_get_drives"] is drives.handle
