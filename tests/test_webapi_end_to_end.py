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
#
# git_log.handle reads ``n/ref/path/author/since/until`` from ``web.request.query``
# directly (because _wrap only injects umo/worktree/...; query-string filters
# like n=20&author=foo are handled inline).  The dedicated
# ``tests/test_git_log.py`` covers the full path with monkeypatched web.request.
_SKIP_FILE_BROWSER = frozenset(
    {
        "handle_get_file_browser",
        "handle_get_git_log",  # PR-2 (2026-06-24)
        "handle_get_git_show",  # v3.8 (2026-06-25)
    }
)


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
    assert "handle_get_file_browser" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)


def test_git_log_handler_excluded_from_smoke() -> None:
    """Pin the exclusion (PR-2): git_log uses web.request.query inline."""
    assert "handle_get_git_log" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)


def test_git_show_handler_excluded_from_smoke() -> None:
    """Pin the exclusion (v3.8): git_show uses web.request.query inline."""
    assert "handle_get_git_show" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)


def test_routes_table_has_thirteen_endpoints() -> None:
    """The route table lists the 13 documented endpoints.

    8 GET + 5 POST = 13。
    v2.14.0 (2026-06-26) 新增 /spcode/git-worktree-add。
    """
    routes = {entry[0] for entry in ROUTES}
    assert routes == {
        "/spcode/project-status",
        "/spcode/plan-mode",
        "/spcode/git-worktrees",
        "/spcode/git-diff",
        "/spcode/git-status",  # v2.13 (2026-06-24)
        "/spcode/git-log",  # PR-2 (2026-06-24)
        "/spcode/git-show",  # v3.8 (2026-06-25)
        "/spcode/git-stage",  # PR-3 (2026-06-24)
        "/spcode/git-unstage",  # PR-4 (2026-06-24)
        "/spcode/git-commit",  # PR-5 (2026-06-24)
        "/spcode/file-browser",
        "/spcode/file-restore",
        "/spcode/git-worktree-add",  # v2.14.0 (2026-06-26)
    }
    # Methods sanity: 8 GET + 5 POST
    methods = [m for entry in ROUTES for m in entry[1]]
    assert methods.count("GET") == 8
    assert methods.count("POST") == 5


# === _wrap adapter ====================================================


@pytest.mark.asyncio
async def test_wrap_injects_umo_from_get_query(monkeypatch) -> None:
    """GET 路径:``_wrap`` 从 ``web.request.query`` 读取 ``umo``。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, umo=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        return {"status": "ok"}

    mock_req = make_web_request_mock(query={"umo": "abc:1"})
    mock_req.method = "GET"
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()
    assert captured["umo"] == "abc:1"


@pytest.mark.asyncio
async def test_wrap_injects_umo_from_post_body(monkeypatch) -> None:
    """POST 路径:``_wrap`` 从 ``web.request.json()`` 读取 ``umo``。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, umo=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        return {"status": "ok"}

    mock_req = make_web_request_mock()

    async def _json(default=None):  # type: ignore[no-untyped-def]
        return {"umo": "xyz:2"}

    mock_req.method = "POST"
    mock_req.json = _json
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()
    assert captured["umo"] == "xyz:2"


@pytest.mark.asyncio
async def test_wrap_injects_scope_with_default(monkeypatch) -> None:
    """GET 缺省 scope → 注入 ``"unstaged"``。"""
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, scope=None):  # type: ignore[no-untyped-def]
        captured["scope"] = scope
        return {"status": "ok"}

    mock_req = make_web_request_mock(query={})
    mock_req.method = "GET"
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()
    assert captured["scope"] == "unstaged"


# === _wrap adapter — real framework call pattern ======================
# v3.7 regression: AstrBot's registered_web_api dispatcher invokes the
# wrapped handler as ``view_func(**path_values)`` — no positional
# ``request`` argument.  The previous _wrap tried to pull ``request``
# from ``args[0]`` and silently got ``None``, so every POST endpoint
# (git-stage / git-unstage / git-commit) received ``body=None`` and
# returned ``invalid_body``.  These tests pin down the framework
# contract: ``_wrap`` must read the request from
# ``astrbot.api.web.request`` (the Quart-style proxy bound by the
# framework's ``bind_request_context``), regardless of how the caller
# invokes the wrapper.


@pytest.mark.asyncio
async def test_wrap_post_body_via_web_request(monkeypatch) -> None:
    """POST with body — framework calls ``view()`` with no positional args.

    ``_wrap`` must read body from ``web.request.json()`` and forward
    body / umo / worktree as kwargs to the handler.
    """
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, body=None, umo=None, worktree=None):  # type: ignore[no-untyped-def]
        captured["body"] = body
        captured["umo"] = umo
        captured["worktree"] = worktree
        return {"status": "ok"}

    payload = {
        "files": ["a.py", "b.py"],
        "umo": "abc:1",
        "worktree": "feat/x",
    }

    async def _json(default=None):  # type: ignore[no-untyped-def]
        return payload

    mock_req = make_web_request_mock()
    mock_req.method = "POST"
    mock_req.json = _json
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    # Real framework call pattern — no positional args.
    await view()

    assert captured["body"] == payload
    assert captured["umo"] == "abc:1"
    assert captured["worktree"] == "feat/x"


@pytest.mark.asyncio
async def test_wrap_get_query_via_web_request(monkeypatch) -> None:
    """GET with query — framework calls ``view()`` with no positional args.

    ``_wrap`` must read umo/worktree/scope/path from ``web.request.query``.
    """
    from astrbot.api import web
    from tests.conftest import make_web_request_mock

    captured: dict = {}

    async def handler(plugin, *, umo=None, worktree=None, scope=None):  # type: ignore[no-untyped-def]
        captured["umo"] = umo
        captured["worktree"] = worktree
        captured["scope"] = scope
        return {"status": "ok"}

    mock_req = make_web_request_mock(
        query={"umo": "u:m", "worktree": "feat/y", "scope": "staged"},
    )
    mock_req.method = "GET"
    monkeypatch.setattr(web, "request", mock_req)

    view = _wrap(handler, plugin=None)
    await view()

    assert captured["umo"] == "u:m"
    assert captured["worktree"] == "feat/y"
    assert captured["scope"] == "staged"


# === register_webapi_routes ===========================================


def test_register_webapi_routes_calls_context_thirteen_times() -> None:
    """``register_webapi_routes`` must call ``register_web_api`` once per route."""
    plugin = MagicMock()
    register_webapi_routes(plugin)
    # 13 endpoints (v2.14.0: + /spcode/git-worktree-add)
    assert plugin.context.register_web_api.call_count == 13


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

    # Should not raise; should attempt all 13 routes.
    register_webapi_routes(plugin)
    assert call_count == 13


# ─── PR-B (v2.14.0, 2026-06-26) ────────────────────────────────────


def test_git_worktree_add_route_registered() -> None:
    """git_worktree_add 应在 ROUTES 表中注册 (PR-B ADD endpoint)。"""
    routes = [r[0] for r in ROUTES]
    assert "/spcode/git-worktree-add" in routes


def test_handlers_dict_has_add_entry() -> None:
    """HANDLERS 表应包含 handle_post_git_worktree_add。"""
    assert "handle_post_git_worktree_add" in HANDLERS
