# tools/webapi/__init__.py
"""Web API endpoint handlers, extracted from main.py.

This package owns the 6 ``/spcode/*`` HTTP endpoints consumed by the
Dashboard / WebUI:

  * ``/spcode/project-status``  (GET)
  * ``/spcode/plan-mode``       (GET)
  * ``/spcode/git-worktrees``   (GET)
  * ``/spcode/git-diff``        (GET)
  * ``/spcode/file-browser``    (GET)
  * ``/spcode/file-restore``    (POST)

Each endpoint lives in its own module (e.g. ``project_status.handle``).
``register_webapi_routes`` is the single entry-point main.py calls
during ``initialize()``; it iterates :data:`ROUTES` and adapts every
handler to the ``view_handler`` interface via :func:`_wrap`.

See docs/superpowers/specs/2026-06-23-webapi-extraction-design.md
for the full architecture.
"""
from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from main import SPCodeToolkit

from tools.webapi import (
    file_browser,
    file_restore,
    git_diff,
    git_worktrees,
    plan_mode,
    project_status,
)

logger = logging.getLogger(__name__)

# (route, methods, handler, desc) — registration order is stable
ROUTES: list[tuple[str, list[str], Callable, str]] = [
    (
        "/spcode/project-status",
        ["GET"],
        project_status.handle,
        "获取 spcode 当前会话已加载的项目信息(供 dashboard 调用)",
    ),
    (
        "/spcode/plan-mode",
        ["GET"],
        plan_mode.handle,
        "获取 spcode 当前 plan-mode 状态(只读)",
    ),
    (
        "/spcode/git-worktrees",
        ["GET"],
        git_worktrees.handle,
        "列出已加载项目的 git worktree 列表",
    ),
    (
        "/spcode/git-diff",
        ["GET"],
        git_diff.handle,
        "获取已加载项目的未暂存 git diff",
    ),
    (
        "/spcode/file-browser",
        ["GET"],
        file_browser.handle,
        "读取文件内容或列出单层目录",
    ),
    (
        "/spcode/file-restore",
        ["POST"],
        file_restore.handle,
        "恢复工作区中某一文件相对 index 的改动",
    ),
]

# 旧方法名 -> 新模块级 handler (for back-compat / introspection)
HANDLERS: dict[str, Callable] = {
    "handle_get_project_status": project_status.handle,
    "handle_get_plan_mode": plan_mode.handle,
    "handle_get_git_worktrees": git_worktrees.handle,
    "handle_get_git_diff": git_diff.handle,
    "handle_get_file_browser": file_browser.handle,
    "handle_post_file_restore": file_restore.handle,
}


def _wrap(handler: Callable, plugin: "SPCodeToolkit") -> Callable:
    """Adapt a handler to the ``view_handler`` interface.

    Inspects ``handler``'s signature and pulls matching request fields
    out of the Starlette/Quart ``request`` object so individual
    endpoint modules can stay self-contained and avoid the
    `web.request.query.get(...)` boilerplate.

    Supported injected kwargs (handler must declare the same name):

    * ``umo``             — GET  ``query.umo``  /  POST ``body.umo``
    * ``worktree``        — GET  ``query.worktree``  /  POST ``body.worktree``
    * ``scope``           — GET  ``query.scope`` (default ``"unstaged"``)
    * ``path``            — GET  ``query.path`` (default ``""``)
    * ``if_none_match``   — GET  ``headers.If-None-Match``
    * ``body``            — POST parsed JSON body (as dict)

    The returned callable accepts whatever positional/keyword args
    ``register_web_api`` passes (typically a single ``request``
    positional).  The original ``plugin`` is closed over so the
    adapter can stay a regular ``async def``.
    """
    sig = inspect.signature(handler)
    accepts = set(sig.parameters) - {"plugin"}

    async def view(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request")
        if request is None and args:
            request = args[0]
        call_kwargs: dict[str, Any] = {}

        if "umo" in accepts and request is not None:
            if getattr(request, "method", "GET") == "POST":
                try:
                    _body = await request.json() or {}
                except Exception:
                    _body = {}
                call_kwargs["umo"] = _body.get("umo")
            else:
                call_kwargs["umo"] = request.query.get("umo") or None

        if "worktree" in accepts and request is not None:
            if getattr(request, "method", "GET") == "POST":
                _body = call_kwargs.get("body")
                if _body is None:
                    try:
                        _body = await request.json() or {}
                    except Exception:
                        _body = {}
                    call_kwargs["body"] = _body
                call_kwargs["worktree"] = _body.get("worktree")
            else:
                call_kwargs["worktree"] = request.query.get("worktree")

        if "scope" in accepts and request is not None:
            call_kwargs["scope"] = request.query.get("scope") or "unstaged"

        if "path" in accepts and request is not None:
            call_kwargs["path"] = request.query.get("path", "").strip()

        if "if_none_match" in accepts and request is not None:
            call_kwargs["if_none_match"] = request.headers.get("if-none-match")

        if "body" in accepts and request is not None and "body" not in call_kwargs:
            if getattr(request, "method", "GET") == "POST":
                try:
                    call_kwargs["body"] = await request.json() or {}
                except Exception:
                    call_kwargs["body"] = {}
            else:
                call_kwargs["body"] = {}

        return await handler(plugin, **call_kwargs)

    return view


def register_webapi_routes(plugin: "SPCodeToolkit") -> None:
    """Register all 6 ``/spcode/*`` routes against ``plugin.context``.

    Called once from ``main.py.initialize()``.  Failures are logged
    but never raised — a single broken endpoint should not block
    plugin load.
    """
    for route, methods, handler, desc in ROUTES:
        try:
            plugin.context.register_web_api(
                route=route,
                view_handler=_wrap(handler, plugin),
                methods=methods,
                desc=desc,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("注册 spcode %s web API 失败: %s", route, exc)


__all__ = [
    "ROUTES",
    "HANDLERS",
    "_wrap",
    "register_webapi_routes",
    "file_browser",
    "file_restore",
    "git_diff",
    "git_worktrees",
    "plan_mode",
    "project_status",
]
