"""GET /spcode/vivado-status — vivado MCP 运行状态快照 (PR-4 2026-07-23)。

PR 2026-07-24: 复用 tools/vivado/availability.py 的 helper (去重)。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

from .._vivado_mcp import VivadoNotFoundError, find_vivado_executable
from ..vivado import state as _vivado_state
from ..vivado.availability import (
    is_vivado_enabled,
    is_vivado_installed,
    is_vivado_mcp_running,
)

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
        "enabled": is_vivado_enabled(plugin),
        "mcp_running": is_vivado_mcp_running(plugin),
        "vivado_path": _get_vivado_path(plugin),
        "install_missing": not is_vivado_installed(),  # 反转语义保持 API 不变
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
