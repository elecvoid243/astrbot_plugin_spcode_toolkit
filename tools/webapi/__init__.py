# tools/webapi/__init__.py
"""Web API endpoint handlers, extracted from main.py.

This package owns the 25 ``/spcode/*`` HTTP endpoints consumed by the
Dashboard / WebUI:

  * ``/spcode/project-status``  (GET)
  * ``/spcode/plan-mode``       (GET)
  * ``/spcode/git-worktrees``   (GET)
  * ``/spcode/git-diff``        (GET)
  * ``/spcode/git-status``      (GET)   # v2.13+
  * ``/spcode/git-log``         (GET)   # v3.7
  * ``/spcode/file-browser``    (GET)
  * ``/spcode/file-restore``    (POST)
  * ``/spcode/file-discard-hunk`` (POST) # v2.16.0 (2026-07-06)
  * ``/spcode/file-search``     (POST)  # v2.15.0 (2026-07-02)
  * ``/spcode/file-name-search`` (POST) # v2.15.0 (2026-07-02)
  * ``/spcode/git-stage``       (POST)  # v3.7
  * ``/spcode/git-unstage``     (POST)  # v3.7
  * ``/spcode/git-commit``      (POST)  # v3.7
  * ``/spcode/git-init``        (POST)  # v2.17.0 (2026-07-15) — git init 端点
  * ``/spcode/git-show``        (GET)   # v3.8 (2026-06-25)
  * ``/spcode/git-worktree-add``   (POST)  # v2.14.0 (2026-06-26) — PR-B ADD endpoint
  * ``/spcode/git-worktree-remove`` (POST) # v2.14.0 (2026-06-26) — PR-C REMOVE endpoint
  * ``/spcode/git-worktree-lock``   (POST)  # v2.14.0 (2026-06-26) — PR-D LOCK endpoint
  * ``/spcode/git-worktree-unlock`` (POST)  # v2.14.0 (2026-06-26) — PR-D UNLOCK endpoint
  * ``/spcode/codegraph-status``    (GET)  # v2.14.x (2026-06-28)

  * ``/spcode/git-file``         (GET)   # spec B (2026-07-11)
  * ``/spcode/docs``             (POST)  # spec B (2026-07-11) — create/upsert
  * ``/spcode/docs``             (PATCH) # spec B (2026-07-11) — rename
  * ``/spcode/docs``             (DELETE) # spec B (2026-07-11) — delete

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
    docs_crud,  # spec B (2026-07-11): POST/PATCH/DELETE /spcode/docs
    file_browser,
    file_discard_hunk,  # v2.16.0 (2026-07-06)
    file_name_search,  # v2.15.0 (2026-07-02)
    file_restore,
    file_search,  # v2.15.0 (2026-07-02)
    git_branches,  # v2.17.0 (2026-07-16) — PR-C GET endpoint
    git_commit,
    git_diff,
    git_file,  # spec B (2026-07-11): GET /spcode/git-file
    git_init,  # v2.17.0 (2026-07-16) — PR-B POST endpoint
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
        "/spcode/git-branches",  # v2.17.0 (2026-07-16) — PR-C
        ["GET"],
        git_branches.handle,
        "列出已加载项目的本地和远程分支",
    ),
    (
        "/spcode/git-show",  # v3.8 (2026-06-25)
        ["GET"],
        git_show.handle,
        "查看给定 ref 修改的文件列表 (name-status + numstat)",
    ),
    (
        "/spcode/git-init",  # v2.17.0 (2026-07-15)
        ["POST"],
        git_init.handle,
        "在已存在空目录上 git init(独立 preflight,无 umo 解析)",
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
        "/spcode/file-search",  # v2.15.0 (2026-07-02)
        ["POST"],
        file_search.handle,
        "在已加载项目(指定 worktree)内按内容搜索文件",
    ),
    (
        "/spcode/file-name-search",  # v2.15.0 (2026-07-02)
        ["POST"],
        file_name_search.handle,
        "在已加载项目(指定 worktree)内按文件名(basename)匹配文件",
    ),
    (
        "/spcode/file-restore",
        ["POST"],
        file_restore.handle,
        "恢复工作区中某一文件相对 index 的改动",
    ),
    (
        "/spcode/file-discard-hunk",  # v2.16.0 (2026-07-06)
        ["POST"],
        file_discard_hunk.handle,
        "按 hunk 丢弃工作区改动(unified diff 文本入参,git apply --reverse)",
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
    (
        "/spcode/git-file",  # spec B (2026-07-11)
        ["GET"],
        git_file.handle,
        "获取 ref 下某文件的完整内容(blob),供 dashboard 文档管理子页面",
    ),
    (
        "/spcode/docs",  # spec B (2026-07-11) — create / upsert
        ["POST"],
        docs_crud.handle_post_docs,
        "创建或覆盖 docs 文件(写到工作区,后续走 Git Diff 提交)",
    ),
    (
        "/spcode/docs",  # spec B (2026-07-11) — rename
        ["PATCH"],
        docs_crud.handle_patch_docs,
        "重命名 docs 文件(纯文件系统 mv,git 后续识别 rename)",
    ),
    (
        "/spcode/docs",  # spec B (2026-07-11) — delete
        ["DELETE"],
        docs_crud.handle_delete_docs,
        "从工作区删除 docs 文件(unlink,不调 git rm)",
    ),
]

# 旧方法名 -> 新模块级 handler (for back-compat / introspection)
HANDLERS: dict[str, Callable] = {
    "handle_get_project_status": project_status.handle,
    "handle_get_plan_mode": plan_mode.handle,
    "handle_get_git_worktrees": git_worktrees.handle,
    "handle_get_git_diff": git_diff.handle,
    "handle_get_git_status": git_status.handle,  # v2.13 (2026-06-24)
    "handle_get_git_branches": git_branches.handle,  # v2.17.0 (2026-07-16)
    "handle_get_git_log": git_log.handle,
    "handle_get_git_show": git_show.handle,  # v3.8 (2026-06-25)
    "handle_get_file_browser": file_browser.handle,
    "handle_post_file_discard_hunk": file_discard_hunk.handle,
    "handle_post_file_search": file_search.handle,  # v2.15.0 (2026-07-02)
    "handle_post_file_name_search": file_name_search.handle,  # v2.15.0 (2026-07-02)
    "handle_post_file_restore": file_restore.handle,
    "handle_post_git_stage": git_stage.handle,
    "handle_post_git_unstage": git_unstage.handle,
    "handle_post_git_commit": git_commit.handle,
    "handle_post_git_init": git_init.handle,  # v2.17.0 (2026-07-15)
    "handle_post_git_worktree_add": git_worktree_add.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_lock": git_worktree_lock.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_remove": git_worktree_remove.handle,  # v2.14.0 (2026-06-26)
    "handle_post_git_worktree_unlock": git_worktree_unlock.handle,  # v2.14.0 (2026-06-26)
    "handle_get_codegraph_status": codegraph_status.handle,  # v2.14.x (2026-06-28)
    "handle_get_git_file": git_file.handle,  # spec B (2026-07-11)
    "handle_post_docs": docs_crud.handle_post_docs,  # spec B (2026-07-11)
    "handle_patch_docs": docs_crud.handle_patch_docs,  # spec B (2026-07-11)
    "handle_delete_docs": docs_crud.handle_delete_docs,  # spec B (2026-07-11)
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
    """Register all 25 ``/spcode/*`` routes against ``plugin.context``.

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
    "docs_crud",  # spec B (2026-07-11)
    "file_browser",
    "file_discard_hunk",  # v2.16.0 (2026-07-06)
    "file_name_search",  # v2.15.0 (2026-07-02)
    "file_restore",
    "file_search",  # v2.15.0 (2026-07-02)
    "git_diff",
    "git_file",  # spec B (2026-07-11)
    "git_init",  # v2.17.0 (2026-07-15)
    "git_branches",  # v2.17.0 (2026-07-16)
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