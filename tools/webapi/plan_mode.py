# tools/webapi/plan_mode.py
"""GET /spcode/plan-mode — 查询当前 umo 是否处于 plan mode。

注意:plan/build 切换是 per-session 严格的,
endpoint 不会"回退到最近一个 plan session"以避免误继承。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(
    plugin: "SPCodeToolkit",
) -> dict:
        """Web API handler for ``GET /spcode/plan-mode``.

        Query params:
            umo (optional): the unified message origin to query. When
                omitted the endpoint returns ``active=false`` (the
                default build state) and the umo as ``None`` —
                callers that don't know their umo should pass it
                explicitly. Unlike ``/spcode/project-status`` we do
                **not** fall back to "most recent plan-mode session"
                because the plan/build switch is strictly per-session
                and silently inheriting another session's mode would
                be confusing.

        Returns:
            A JSON envelope of the form::

                {
                    "status": "ok",
                    "data": {
                        "active": bool,        # True == plan, False == build
                        "umo": str | None,
                        "all_active_count": int  # number of umos in plan mode
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

        return {
            "status": "ok",
            "data": {
                "active": plugin._plan_mode_active(umo),
                "umo": umo,
                "all_active_count": plugin._plan_mode_active_count(),
            },
        }
