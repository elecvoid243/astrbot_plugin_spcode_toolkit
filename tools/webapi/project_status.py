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
                        "all_loaded_count": int
                    }
                }
        """
        # Late import to avoid circular issues with the plugin module.
        from astrbot.api import web

        umo: str | None = None
        try:
            umo = web.request.query.get("umo") or None
        except Exception:
            umo = None

        if umo:
            info = plugin._loaded_projects.get(umo)
            if info is None:
                return {
                    "status": "ok",
                    "data": {
                        "loaded": False,
                        "directory": None,
                        "loaded_at": None,
                        "umo": umo,
                        "all_loaded_count": len(plugin._loaded_projects),
                    },
                }
            return {
                "status": "ok",
                "data": {
                    "loaded": True,
                    "directory": info.get("directory"),
                    "loaded_at": info.get("loaded_at"),
                    "umo": umo,
                    "all_loaded_count": len(plugin._loaded_projects),
                },
            }

        # No umo provided: return the most-recently-loaded project as a
        # convenience for callers that don't track umos (e.g. the dashboard).
        if not plugin._loaded_projects:
            return {
                "status": "ok",
                "data": {
                    "loaded": False,
                    "directory": None,
                    "loaded_at": None,
                    "umo": None,
                    "all_loaded_count": 0,
                },
            }
        # Pick the entry with the largest loaded_at (most recent).
        recent_umo, recent_info = max(
            plugin._loaded_projects.items(),
            key=lambda item: item[1].get("loaded_at", 0),
        )
        return {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": recent_info.get("directory"),
                "loaded_at": recent_info.get("loaded_at"),
                "umo": recent_umo,
                "all_loaded_count": len(plugin._loaded_projects),
            },
        }
