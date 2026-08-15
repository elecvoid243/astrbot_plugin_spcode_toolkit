"""Unit tests for webapi/home_directory.handle.

Spec: D:\\AstrbotWorkSpace\\spcode-目录选择器设计方案.md §4.1
Author: elecvoid243, 2026-08-15
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from tools.webapi import home_directory

pytestmark = pytest.mark.asyncio


async def test_handle_returns_ok_with_home_field():
    """返回 status=ok + data.home 为宿主账户 home 绝对路径。"""
    result = await home_directory.handle(MagicMock())
    assert result["status"] == "ok"
    home = result["data"]["home"]
    assert isinstance(home, str)
    assert home
    assert home == os.path.expanduser("~")
    # home 必须为绝对路径(可直接作为 file-browser 的 path 参数)。
    assert os.path.isabs(home)


async def test_home_directory_route_registered():
    """ROUTES 表中必须存在 GET /spcode/home-directory 条目。"""
    from tools.webapi import HANDLERS, ROUTES

    matches = [
        r for r in ROUTES if r[0] == "/spcode/home-directory" and "GET" in r[1]
    ]
    assert len(matches) == 1
    assert matches[0][2] is home_directory.handle
    assert HANDLERS["handle_get_home_directory"] is home_directory.handle
