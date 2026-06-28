# tools/webapi/__init__.py
"""Web API endpoint handlers, extracted from main.py.

This package owns the 14 ``/spcode/*`` HTTP endpoints consumed by the
Dashboard / WebUI:

  * ``/spcode/project-status``  (GET)
  * ``/spcode/plan-mode``       (GET)
  * ``/spcode/git-worktrees``   (GET)
  * ``/spcode/git-diff``        (GET)
  * ``/spcode/git-status``      (GET)   # v2.13+
  * ``/spcode/git-log``         (GET)   # v3.7
  * ``/spcode/file-browser``    (GET)
  * ``/spcode/file-restore``    (POST)
  * ``/spcode/git-stage``       (POST)  # v3.7
  * ``/spcode/git-unstage``     (POST)  # v3.7
  * ``/spcode/git-commit``      (POST)  # v3.7
  * ``/spcode/git-show``        (GET)   # v3.8 (2026-06-25)
  * ``/spcode/git-worktree-add``   (POST)  # v2.14.0 (2026-06-26) — PR-B ADD endpoint
  * ``/spcode/git-worktree-remove`` (POST) # v2.14.0 (2026-06-26) — PR-C REMOVE endpoint
  * ``/spcode/git-worktree-lock``   (POST) # v2.14.0 (2026-06-26) — PR-D LOCK endpoint
  * ``/spcode/git-worktree-unlock`` (POST) # v2.14.0 (2026-06-26) — PR-D UNLOCK endpoint
  * ``/spcode/codegraph-status``    (GET)  # v2.14.x (2026-06-28)

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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit

from . import (
    codegraph_status,  # v2.14.x (2026-06-28)
    file_browser,
    file_restore,
    git_commit,
    git_diff,
    git_log,
    git_show,
    git_stage,
    git_status,
    git_unstage,
    git_worktree_add,  # v2.14.0 (2026-06-26)
    git_worktree_lock,  # v2.14.0 (2026-06-26)
    git_worktree_remove,  # v2.14.0 (2026-06-26)
    git_worktree_unlock,  # v2.14.0 (2026-06-26)
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
        "/spcode/git-status",  # v2.13 (2026-06-24)
        ["GET"],
        git_status.handle,
        "获取已加载项目的 git 工作区状态(branch/upstream/staged/unstaged/untracked)",
    ),
    (
        "/spcode/git-log",
        ["GET"],
        git_log.handle,
        "获取已加载项目的 git 历史(8 字段标准粒度)",
    ),
    (
        "/spcode/git-show",  # v3.8 (2026-06-25)
        ["GET"],
        git_show.handle,
        "查看给定 ref 修改的文件列表 (name-status + numstat)",
    ),
    (
        "/spcode/git-stage",
        ["POST"],
        git_stage.handle,
        "git add(指定文件 or all,互斥)",
    ),
    (
        "/spcode/git-unstage",
        ["POST"],
        git_unstage.handle,
        "git reset HEAD(指定文件 or all,互斥)",
    ),
    (
        "/spcode/git-commit",
        ["POST"],
        git_commit.handle,
        "git commit(严格最小,仅 message)",
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
    (
        "/spcode/git-worktree-add",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_add.handle,
        "创建 git worktree(git CLI 旗标平铺)",
    ),
    (
        "/spcode/git-worktree-remove",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_remove.handle,
        "删除 git worktree (硬禁 main,locked 拒,force=true 跳过 dirty)",
    ),
    (
        "/spcode/git-worktree-lock",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_lock.handle,
        "锁定 git worktree(可选 --reason),main 允许但 git 自身拒绝",
    ),
    (
        "/spcode/git-worktree-unlock",  # v2.14.0 (2026-06-26)
        ["POST"],
        git_worktree_unlock.handle,
        "解锁 git worktree,main 允许但 git 自身拒绝",
    ),
    (
        "/spcode/codegraph-status",  # v2.14.x (2026-06-28)
        ["GET"],
        codegraph_status.handle,
        "获取 codegraph MCP 运行状态(供 dashboard 显示)",
    ),
]

# 旧方法名 -> 新模块级 handler (for back-compat / introspection)
HANDLERS: dict[str, Callable] = {
    "handle_get_project_status": project_status.handle,
    "handle_get_plan_mode": plan_mode.handle,
    "handle_get_git_worktrees": git_worktrees.handle,
    "handle_get_git_diff": git_diff.handle,
    "handle_get_git_status": git_status.handle,  # v2.13 (2026-06-24)
    "handle_get_git_log": git_log.handle,
    "handle_get_git_show": git_show.handle,  # v3.8 (2026-06-25)
    "handle_get_file_browser": file_browser.handle,
    "handle_post_file_restore": file_restore.handle,
    "handle_post_git_stage": git_stage.handle,
    "handle_post_git_unstage": git_unstage.handle,
    "handle_post_git_commit": git_commit.handle,
    "handle_post_git_worktree_add": git_worktree_add.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_lock": git_worktree_lock.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_remove": git_worktree_remove.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_unlock": git_worktree_unlock.handle,  # v2.14.0 (2026-06-26)
    "handle_get_codegraph_status": codegraph_status.handle,  # v2.14.x (2026-06-28)
}


def _wrap(handler: Callable, plugin: SPCodeToolkit) -> Callable:
    """Adapt a handler to the ``view_handler`` interface.

    Reads request fields from ``astrbot.api.web.request`` — the
    Quart-style proxy bound by AstrBot's ``bind_request_context`` for
    the lifetime of the request.  Individual endpoint modules can
    declare keyword-only parameters and the adapter forwards matching
    values from the request:

    * ``umo``             — GET  ``query.umo``  /  POST ``body.umo``
    * ``worktree``        — GET  ``query.worktree``  /  POST ``body.worktree``
    * ``scope``           — GET  ``query.scope`` (default ``"unstaged"``)
    * ``path``            — GET  ``query.path`` (default ``""``)
    * ``if_none_match``   — GET  ``headers.If-None-Match``
    * ``body``            — POST parsed JSON body (as dict)

    The wrapped callable accepts arbitrary positional/keyword args but
    ignores them — the framework invokes it as
    ``view_func(**path_values)`` (no positional request).  The
    plugin is closed over so the adapter stays a regular ``async def``.

    Note:
        v3.7.1 — Previously the adapter tried to pull a FastAPI
        ``Request`` from ``args[0]`` / ``kwargs["request"]``.  The
        framework never passes one (it binds the request via
        ``bind_request_context``), so ``request`` was always ``None``
        and every POST handler received ``body=None`` → ``invalid_body``.
        Switched to ``web.request`` so the adapter works regardless of
        how the caller invokes the wrapper.
    """
    sig = inspect.signature(handler)
    accepts = set(sig.parameters) - {"plugin"}

    async def view(*_args: Any, **_kwargs: Any) -> Any:
        # Late import: ``astrbot.api.web`` requires the plugin runtime
        # to be initialised.  Importing at module scope would break
        # unit tests that mock ``web.request`` before ``register_webapi_routes``
        # is called.
        from astrbot.api import web

        is_post = web.request.method == "POST"
        call_kwargs: dict[str, Any] = {}

        # Parse the JSON body once for POST requests — used by umo,
        # worktree and body injection below.
        body: dict = {}
        if is_post and (accepts & {"umo", "worktree", "body"}):
            body = (await web.request.json(default={})) or {}

        if "umo" in accepts:
            if is_post:
                call_kwargs["umo"] = body.get("umo")
            else:
                call_kwargs["umo"] = web.request.query.get("umo") or None

        if "worktree" in accepts:
            if is_post:
                call_kwargs["worktree"] = body.get("worktree")
            else:
                call_kwargs["worktree"] = web.request.query.get("worktree")

        if "scope" in accepts:
            call_kwargs["scope"] = web.request.query.get("scope") or "unstaged"

        if "path" in accepts:
            call_kwargs["path"] = web.request.query.get("path", "").strip()

        if "if_none_match" in accepts:
            call_kwargs["if_none_match"] = web.request.headers.get("if-none-match")

        if "body" in accepts:
            call_kwargs["body"] = body

        return await handler(plugin, **call_kwargs)

    return view


def register_webapi_routes(plugin: SPCodeToolkit) -> None:
    """Register all 16 ``/spcode/*`` routes against ``plugin.context``.

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
    "codegraph_status",  # v2.14.x (2026-06-28)
    "file_browser",
    "file_restore",
    "git_diff",
    "git_log",
    "git_show",
    "git_stage",
    "git_status",
    "git_unstage",
    "git_commit",
    "git_worktree_add",  # v2.14.0 (2026-06-26)
    "git_worktree_lock",  # v2.14.0 (2026-06-26)
    "git_worktree_remove",  # v2.14.0 (2026-06-26)
    "git_worktree_unlock",  # v2.14.0 (2026-06-26)
    "git_worktrees",
    "plan_mode",
    "project_status",
]
