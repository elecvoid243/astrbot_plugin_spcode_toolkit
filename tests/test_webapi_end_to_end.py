"""End-to-end smoke test for the spcode webapi surface.

The Dashboard talks to AstrBot via the 6 ``/spcode/*`` HTTP endpoints
registered by :func:`tools.webapi.register_webapi_routes`.  This
test exercises the route table and each handler in isolation:

* All 6 handlers are present, callable, and accept the
  ``plugin`` positional + keyword args documented in their
  signatures.
* The ``_wrap`` adapter pulls the right values out of a fake
  Starlette/Quart ``request`` and forwards them.
* ``register_webapi_routes`` is idempotent and defensive — a
  broken endpoint is logged but does not abort the others.

The tests use a plain :class:`unittest.mock.MagicMock` for
``plugin``; handler-specific state is set up inline.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tools.webapi import HANDLERS, ROUTES, _wrap, register_webapi_routes


# === All 6 handlers are present and call-return-dict =====================


def _make_minimal_plugin() -> MagicMock:
    """Plugin mock with the attributes most handlers touch."""
    plugin = MagicMock()
    plugin._loaded_projects = {}
    plugin._loaded_agents = {}
    plugin._plan_mode = {}
    plugin._plan_reminded = {}
    plugin._config = {
        "agentsmd_enabled": False,
        "codegraph_enabled": False,
        "git_path": "",
    }
    return plugin


# file_browser.handle reads ``path`` from ``web.request`` directly (legacy
# pattern: it predates the ``_wrap`` adapter's kwargs injection).  Exercising
# it requires a starlette/quart ``request`` contextvar; the dedicated
# ``tests/test_file_browser.py`` already covers that path in isolation.
_SKIP_FILE_BROWSER = frozenset({"handle_get_file_browser"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name",
    sorted(set(HANDLERS.keys()) - _SKIP_FILE_BROWSER),
)
async def test_handler_callable_returns_dict(handler_name: str) -> None:
    """Every handler in :data:`HANDLERS` can be awaited with a mock
    plugin and returns a dict (possibly an error envelope)."""
    plugin = _make_minimal_plugin()
    handler = HANDLERS[handler_name]
    # We pass empty kwargs so handlers fall through to their default
    # (no-umo / no-worktree / no-scope) branches.
    result = await handler(plugin)
    assert isinstance(result, dict), f"{handler_name} returned {type(result)!r}"


def test_file_browser_handler_excluded_from_smoke() -> None:
    """Pin the exclusion so we notice if a refactor enables plain calls."""
    assert "handle_get_file_browser" not in (
        set(HANDLERS.keys()) - _SKIP_FILE_BROWSER
    )


def test_routes_table_has_six_endpoints() -> None:
    """The route table lists exactly the 6 documented endpoints."""
    routes = {entry[0] for entry in ROUTES}
    assert routes == {
        "/spcode/project-status",
        "/spcode/plan-mode",
        "/spcode/git-worktrees",
        "/spcode/git-diff",
        "/spcode/file-browser",
        "/spcode/file-restore",
    }
    # Methods sanity: 5 GET + 1 POST
    methods = [m for entry in ROUTES for m in entry[1]]
    assert methods.count("GET") == 5
    assert methods.count("POST") == 1


# === _wrap adapter ====================================================


@pytest.mark.asyncio
async def test_wrap_injects_umo_from_get_query() -> None:
    captured: dict = {}

    async def handler(plugin, *, umo=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        return {"status": "ok"}

    request = MagicMock()
    request.method = "GET"
    request.query.get = MagicMock(side_effect=lambda k, default=None: {"umo": "abc:1"}.get(k, default))

    view = _wrap(handler, plugin=None)
    await view(request)
    assert captured["umo"] == "abc:1"


@pytest.mark.asyncio
async def test_wrap_injects_umo_from_post_body() -> None:
    captured: dict = {}

    async def handler(plugin, *, umo=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        return {"status": "ok"}

    request = MagicMock()
    request.method = "POST"

    async def _json():  # type: ignore[no-untyped-def]
        return {"umo": "xyz:2"}

    request.json = _json
    request.query.get = MagicMock(return_value=None)

    view = _wrap(handler, plugin=None)
    await view(request)
    assert captured["umo"] == "xyz:2"


@pytest.mark.asyncio
async def test_wrap_injects_scope_with_default() -> None:
    captured: dict = {}

    async def handler(plugin, *, scope=None):  # type: ignore[no-untyped-def]
        captured["scope"] = scope
        return {"status": "ok"}

    request = MagicMock()
    request.method = "GET"
    request.query.get = MagicMock(side_effect=lambda k, default=None: default)

    view = _wrap(handler, plugin=None)
    await view(request)
    # Default in _wrap is "unstaged" when the query string omits scope.
    assert captured["scope"] == "unstaged"


# === register_webapi_routes ===========================================


def test_register_webapi_routes_calls_context_six_times() -> None:
    """``register_webapi_routes`` must call ``register_web_api`` once per route."""
    plugin = MagicMock()
    register_webapi_routes(plugin)
    # 6 endpoints -> 6 register_web_api invocations
    assert plugin.context.register_web_api.call_count == 6


def test_register_webapi_routes_continues_on_failure() -> None:
    """A single failure must not abort the rest of the loop."""

    plugin = MagicMock()
    call_count = 0

    def _maybe_fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise RuntimeError("simulated failure")

    plugin.context.register_web_api.side_effect = _maybe_fail

    # Should not raise; should attempt all 6 routes.
    register_webapi_routes(plugin)
    assert call_count == 6
