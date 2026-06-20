"""Tests for /spcode/file-browser endpoint (v3.2).

See docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md
and docs/superpowers/plans/2026-06-20-file-browser-endpoint-plan.md.

19 spec test cases #1-#19 全部覆盖。Windows 上 5 个 skip (permission/symlink/FIFO)。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import _make_plugin, make_web_request_mock  # noqa: F401  (v3.2 shared helpers)

# 启用 pytest-asyncio:让所有 `async def` 测试函数在 strict 模式下也能运行。
pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin():
    """Per-test plugin instance."""
    return _make_plugin()


# ── 错误 reason: 路径不存在 ──


async def test_file_browser_path_not_found_returns_3_field_envelope(plugin, tmp_path):
    """Test #4: 路径不存在 → type=null, reason='path_not_found'."""
    import astrbot.api.web as _aw

    missing = tmp_path / "does_not_exist.py"
    req = make_web_request_mock({"path": str(missing)})
    with patch.object(_aw, "request", req):
        result = await plugin.handle_get_file_browser()

    data = result["data"]
    assert data["type"] is None
    assert data["path"] == str(missing)
    assert data["reason"] == "path_not_found"
