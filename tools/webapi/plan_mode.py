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
            # PR-3 (2026-06-23): 委托给 PlanModeController(webapi 端不再
            # 直接访问 main.py 的 self._plan_mode dict)
            "active": plugin._plan.is_active(umo),
            "umo": umo,
            "all_active_count": plugin._plan.count_active(),
        },
    }


async def handle_set(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    body: dict | None = None,
) -> dict:
    """Web API handler for ``POST /spcode/plan-mode``.

    v2.22.0 (2026-07-27): dashboard 的 plan/build chip 改为直接调本
    端点切换模式,不再向聊天框注入 ``/plan`` / ``/build`` 用户消息。
    切换后的 reminder 注入与工具过滤仍由 ``on_llm_request`` 钩子
    按 ``_plan_mode[umo]`` 状态驱动,与本端点解耦。

    Body:
        umo (str, required): 要切换的会话 unified_msg_origin。
            ``_wrap`` 适配器会从 JSON body 中同名字段注入。
        active (bool, required): True → plan 模式,False → build 模式。

    Returns:
        与 GET 相同的 envelope,额外带 ``changed`` 字段表示状态是否
        发生了翻转(幂等重复调用时为 False)。参数缺失/非法时返回
        ``{"status": "error", "message": ...}``。
    """
    active = (body or {}).get("active")
    if not umo:
        return {"status": "error", "message": "missing required field: umo"}
    if not isinstance(active, bool):
        return {
            "status": "error",
            "message": "missing or invalid field: active (bool required)",
        }
    was_active = plugin._plan.is_active(umo)
    if active:
        plugin._plan.activate(umo)
    else:
        plugin._plan.deactivate(umo)
    return {
        "status": "ok",
        "data": {
            "active": plugin._plan.is_active(umo),
            "umo": umo,
            "all_active_count": plugin._plan.count_active(),
            "changed": was_active != active,
        },
    }
