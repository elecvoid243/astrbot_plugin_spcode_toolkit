"""Smoke tests for tools.webapi._helpers module.

Verifies that cross-endpoint shared helpers (currently `_run_git_async`)
are importable from the new webapi subpackage.
"""
import asyncio

import pytest


def test_run_git_async_importable_from_webapi_helpers():
    """_run_git_async must be importable from tools.webapi._helpers and be a coroutine function."""
    from tools.webapi import _helpers
    assert hasattr(_helpers, "_run_git_async")
    assert asyncio.iscoroutinefunction(_helpers._run_git_async)
