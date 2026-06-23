"""Smoke test for webapi/file_browser.handle + helpers.

Handler 从 main.py 搬出,行为不变。
"""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from tools.webapi import file_browser

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


def test_build_error_response_has_required_fields():
    """_build_error_response 返回 type=None + path + reason。"""
    resp = file_browser._build_error_response("/tmp/x", "not_found")
    assert resp["type"] is None
    assert resp["path"] == "/tmp/x"
    assert resp["reason"] == "not_found"


def test_make_304_response_is_json_response_compat():
    """_make_304_response 返回 _JSONResponseCompat 且 status_code == 304。"""
    from tools.webapi._helpers import _JSONResponseCompat
    resp = file_browser._make_304_response({"ETag": "abc"})
    assert isinstance(resp, _JSONResponseCompat)
    assert resp.status_code == 304
    # body 是空 dict,dict-like get 返回 None(不 raise)
    assert resp.get("status_code") is None


def test_common_cache_headers_includes_etag():
    """_common_cache_headers 返回的 dict 含 ETag 字段。"""
    headers = file_browser._common_cache_headers("etag123")
    assert "ETag" in headers
    assert "Cache-Control" in headers


async def test_classify_entry_handles_missing_path():
    """_classify_entry 必须能处理 missing path(返回某种 result)。"""
    from pathlib import Path
    # 缺失的 path 不存在 → 应返回某种字符串分类
    result = file_browser._classify_entry(Path("Z:/definitely-not-exist-12345"))
    assert isinstance(result, tuple)
    assert len(result) == 2
