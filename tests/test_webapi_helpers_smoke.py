"""Smoke tests for tools.webapi._helpers module.

Verifies that cross-endpoint shared helpers (currently `_run_git_async`)
are importable from the new webapi subpackage.
"""
import asyncio



def test_run_git_async_importable_from_webapi_helpers():
    """_run_git_async must be importable from tools.webapi._helpers and be a coroutine function."""
    from tools.webapi import _helpers
    assert hasattr(_helpers, "_run_git_async")
    assert asyncio.iscoroutinefunction(_helpers._run_git_async)


def test_json_response_compat_importable_from_webapi_helpers():
    """`_JSONResponseCompat` 必须能 import 且继承自 JSONResponse。"""
    from tools.webapi._helpers import _JSONResponseCompat
    assert _JSONResponseCompat is not None
    assert isinstance(_JSONResponseCompat, type)
    from astrbot.api.web import JSONResponse
    assert issubclass(_JSONResponseCompat, JSONResponse)
