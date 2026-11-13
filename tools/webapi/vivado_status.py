"""GET /spcode/vivado-status — vivado MCP 运行状态快照 (PR-4 2026-07-23)。

PR 2026-07-24: 复用 tools/vivado/availability.py 的 helper (去重)。
"""

from __future__ import annotations

import asyncio
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

# ── 安装探测缓存(v3.10) ──
# WHY: ``find_vivado_executable`` 会 invoke 运行包 worker(子进程),
# ``is_vivado_installed`` 同样查运行包; 实测一次约 370ms。两者都是
# 同步调用, 在 Hypercorn 单事件循环里会阻塞所有其他响应(会话切换时
# 表现为 /chat/configs 等被压到秒级)。这里做两件事:
#   1. 丢到 ``asyncio.to_thread``, 不占事件循环;
#   2. 10s 缓存 —— Vivado 安装路径不会在两次切换之间变化。
# ``enabled`` / ``mcp_running`` / ``sessions`` 仍然实时读取。
_VIVADO_PROBE_TTL: float = 10.0
_vivado_probe_cache: tuple[float, dict] | None = None


def _reset_probe_cache() -> None:
    """清空安装探测缓存(测试用)。"""
    global _vivado_probe_cache
    _vivado_probe_cache = None


def _probe_install(plugin: SPCodeToolkit) -> dict:
    """同步探测安装路径(会 invoke 运行包 worker),仅缓存过期时执行。"""
    return {
        "vivado_path": _get_vivado_path(plugin),
        "install_missing": not is_vivado_installed(),
    }


async def _probe_install_cached(plugin: SPCodeToolkit) -> dict:
    global _vivado_probe_cache
    now = time.monotonic()
    if _vivado_probe_cache is not None and _vivado_probe_cache[0] > now:
        return _vivado_probe_cache[1]
    probe = await asyncio.to_thread(_probe_install, plugin)
    _vivado_probe_cache = (time.monotonic() + _VIVADO_PROBE_TTL, probe)
    return probe


async def handle(plugin: SPCodeToolkit) -> dict:
    """GET /spcode/vivado-status - 返回 vivado MCP 快照 (envelope 格式)。

    Returns:
        {"status": "ok", "elapsed_ms": float, "reason": None,
         "data": {"enabled", "mcp_running", "vivado_path",
                  "install_missing", "degraded", "sessions": [...]}}
    """
    t0 = time.time()
    install = await _probe_install_cached(plugin)
    data = {
        "enabled": is_vivado_enabled(plugin),
        "mcp_running": is_vivado_mcp_running(plugin),
        "vivado_path": install["vivado_path"],
        "install_missing": install["install_missing"],
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


def _get_vivado_path(plugin: SPCodeToolkit) -> str:
    try:
        return find_vivado_executable(
            configured=(plugin._config.get("vivado_executable") or "")
        )
    except VivadoNotFoundError:
        return ""
    except Exception as e:
        logger.debug(f"vivado_status: find_vivado 失败: {e}")
        return ""
