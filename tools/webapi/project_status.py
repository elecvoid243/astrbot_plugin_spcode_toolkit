# tools/webapi/project_status.py
"""GET /spcode/project-status — 列出已加载项目状态。"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
) -> dict:
    """Web API handler for ``GET /spcode/project-status``.

    Query params:
        umo (optional): the unified message origin to query. When omitted
            the endpoint returns the most-recently-loaded project across
            all umos (the dashboard can use this fallback when it does not
            know its umo).

    Returns:
        A JSON envelope of the form::

            {
                "status": "ok",
                "data": {
                    "loaded": bool,
                    "directory": str | None,
                    "loaded_at": float | None,
                    "umo": str | None,
                    "all_loaded_count": int,
                    "skipped_substeps": list[str]  # (2026-07-25) 新增
                }
            }

    字段说明:
        - ``skipped_substeps``: 用户在 ``/project load`` 时显式 opt-out
          跳过的子步骤名列表。可能值: ``"agentsmd"`` / ``"codegraph"``。
          空 list 表示完整执行了所有子步骤。
          前端可据此在 dashboard 上显示「项目已加载,但跳过了 AGENTS.md/codegraph」
          之类的提示。

    PR-7 (2026-06-23): 数据源从 ``plugin._loaded_projects`` 迁移到
    ``tools.project.state`` 模块级单例(handler 调用
    ``plugin.get_loaded_project(umo)`` 走统一查询接口)。
    """
    # Late import to avoid circular issues with the plugin module.
    from astrbot.api import web

    umo_param: str | None = umo
    if umo_param is None:
        try:
            umo_param = web.request.query.get("umo") or None
        except Exception:
            umo_param = None

    # 通过 plugin.get_loaded_project(umo) 统一查询,内部已迁到
    # tools.project.state。
    if umo_param:
        info = plugin.get_loaded_project(umo_param)
        # 列举总数 — 走 state.items()
        from ..project import state as _proj_state

        all_count = len(_proj_state.items())
        if info is None:
            return {
                "status": "ok",
                "data": {
                    "loaded": False,
                    "directory": None,
                    "loaded_at": None,
                    "umo": umo_param,
                    "all_loaded_count": all_count,
                    "skipped_substeps": [],
                },
            }
        return {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": info.get("directory"),
                "loaded_at": info.get("loaded_at"),
                "umo": umo_param,
                "all_loaded_count": all_count,
                # 2026-07-25: 暴露给前端,空 list 表示未跳过任何子步骤。
                # 旧 state(无 skipped_substeps 字段)用 .get 兜底默认空 set,
                # 与行为契约一致(默认 load = 未跳过任何子步骤)。
                "skipped_substeps": sorted(
                    info.get("skipped_substeps", set())
                ),
            },
        }

    # No umo provided: return the most-recently-loaded project as a
    # convenience for callers that don't track umos (e.g. the dashboard).
    from ..project import state as _proj_state

    all_items = _proj_state.items()
    if not all_items:
        return {
            "status": "ok",
            "data": {
                "loaded": False,
                "directory": None,
                "loaded_at": None,
                "umo": None,
                "all_loaded_count": 0,
                "skipped_substeps": [],
            },
        }
    # Pick the entry with the largest loaded_at (most recent).
    recent_umo, recent_info = max(
        all_items.items(),
        key=lambda item: item[1].get("loaded_at", 0),
    )
    return {
        "status": "ok",
        "data": {
            "loaded": True,
            "directory": recent_info.get("directory"),
            "loaded_at": recent_info.get("loaded_at"),
            "umo": recent_umo,
            "all_loaded_count": len(all_items),
            "skipped_substeps": sorted(
                recent_info.get("skipped_substeps", set())
            ),
        },
    }
