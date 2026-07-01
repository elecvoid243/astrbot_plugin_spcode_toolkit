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


# === Task 5.1: Chunk 5 entry-point (TDD smoke) ==========================


def test_register_webapi_routes_callable():
    """`register_webapi_routes` must exist and be callable."""
    from tools.webapi import register_webapi_routes

    assert callable(register_webapi_routes)


def test_wrap_function_returns_async_callable():
    """`_wrap` must adapt a plain async handler into a coroutine function.

    The adapter should pass through `plugin` and any keyword-only
    params the handler declares, so view-handlers can be written as
    `async def handle(plugin, *, umo=None) -> dict`.
    """
    from tools.webapi import _wrap
    import inspect

    async def fake_handler(plugin, *, umo=None):
        return {"status": "ok", "umo": umo}

    wrapped = _wrap(fake_handler, plugin=None)
    # Must be coroutine function so register_web_api accepts it.
    assert asyncio.iscoroutinefunction(wrapped) or inspect.iscoroutinefunction(wrapped)
