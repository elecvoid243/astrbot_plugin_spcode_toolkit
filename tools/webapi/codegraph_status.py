"""GET /spcode/codegraph-status — 获取 codegraph MCP 运行状态。"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(plugin: "SPCodeToolkit") -> dict:
    """返回 codegraph MCP 运行状态。

    始终返回 status=ok 的 envelope。即使 codegraph 未启用也不报错，
    仅返回 enabled=false 供调用方识别。

    Returns:
        dict: {
            "status": "ok",
            "data": {
                "enabled": bool,
                "mcp_running": bool,
                "active_project": str,  # 空字符串=未设定
            }
        }
    """
    enabled = plugin._config.get("codegraph_enabled", True)
    if not enabled:
        return {
            "status": "ok",
            "data": {
                "enabled": False,
                "mcp_running": False,
                "active_project": "",
            },
        }

    # 查 MCP 运行时状态
    mgr = plugin.context.get_llm_tool_manager()
    mcp_running = "codegraph" in mgr.mcp_server_runtime

    # 取运行时记录的项目路径(模块级 import 避免与当前文件名混淆)
    from ..codegraph import state as cg_state

    active_project = cg_state.get_active_project_path()

    return {
        "status": "ok",
        "data": {
            "enabled": True,
            "mcp_running": mcp_running,
            "active_project": active_project,
        },
    }
