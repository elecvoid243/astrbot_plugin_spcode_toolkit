"""GET /spcode/vivado-status — vivado MCP 运行状态快照 (PR-4 2026-07-23)。"""

from __future__ import annotations

import importlib.util
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

from .._vivado_mcp import VivadoNotFoundError, find_vivado_executable
from ..vivado import state as _vivado_state

logger = logging.getLogger(__name__)


async def handle(plugin: "SPCodeToolkit") -> dict:
    """GET /spcode/vivado-status - 返回 vivado MCP 快照 (envelope 格式)。

    Returns:
        {"status": "ok", "elapsed_ms": float, "reason": None,
         "data": {"enabled", "mcp_running", "vivado_path",
                  "install_missing", "degraded", "sessions": [...]}}
    """
    t0 = time.time()
    data = {
        "enabled": _get_enabled(plugin),
        "mcp_running": _get_mcp_running(plugin),
        "vivado_path": _get_vivado_path(plugin),
        "install_missing": _get_install_missing(),
        "degraded": False,
        "sessions": [],
    }
    try:
        state = _vivado_state.get_state()
        data["sessions"] = [
            {"id": s.get("id", ""), "state": s.get("state", "")}
            for s in state.get_sessions()
        ]
    except Exception as e:
        logger.debug(f"vivado_status: sessions 读取失败: {e}")
        data["degraded"] = True
    elapsed_ms = (time.time() - t0) * 1000
    return {
        "status": "ok",
        "reason": None,
        "elapsed_ms": elapsed_ms,
        "data": data,
    }


def _get_enabled(plugin: "SPCodeToolkit") -> bool:
    return bool(plugin._config.get("vivado_enabled", True))


def _get_install_missing() -> bool:
    return importlib.util.find_spec("vivado_mcp") is None


def _get_mcp_running(plugin: "SPCodeToolkit") -> bool:
    try:
        mgr = plugin.context.get_llm_tool_manager()
        return "vivado" in mgr.mcp_server_runtime
    except Exception:
        return False


def _get_vivado_path(plugin: "SPCodeToolkit") -> str:
    try:
        return find_vivado_executable(
            configured=(plugin._config.get("vivado_executable") or "")
        )
    except VivadoNotFoundError:
        return ""
    except Exception as e:
        logger.debug(f"vivado_status: find_vivado 失败: {e}")
        return ""
