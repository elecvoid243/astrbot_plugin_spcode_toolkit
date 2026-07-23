"""MCP bootstrap: vivado daemon 启动 + 关闭 (PR-1 2026-07-23)。"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from . import state as _state
from .launcher import build_vivado_launcher_cfg


def build_mcp_cfg(plugin, *, state: "_state.VivadoState") -> dict | None:
    """构造 vivado MCP 启动配置。

    Returns:
        dict 或 None (vivado_enabled=False 或 launcher 不可用)。
    """
    if not plugin._config.get("vivado_enabled", True):
        return None
    return build_vivado_launcher_cfg(plugin)


async def bootstrap_mcp(plugin, *, state: "_state.VivadoState") -> None:
    """插件加载后异步拉起 vivado MCP server。失败不抛异常。

    1. 双重 gate (__init__ + 函数本身)
    2. launcher 不可用 → logger.info 跳过
    3. ensure_stdio_allowlist() (幂等)
    4. mgr.enable_mcp_server("vivado", cfg, timeout=180)
    5. 成功 → state.mark_running(True); 失败 → logger.warning + mark_running(False)
    """
    if not plugin._config.get("vivado_enabled", True):
        return

    cfg = build_mcp_cfg(plugin, state=state)
    if cfg is None:
        logger.info(
            "vivado-mcp 不可用 (未装或 launcher 失败), 跳过 MCP 集成"
            "(spcode 其它工具照常工作; 如需启用请 `pip install vivado-mcp`)"
        )
        return

    from .._vivado_mcp import ensure_stdio_allowlist
    ensure_stdio_allowlist()

    try:
        mgr = plugin.context.get_llm_tool_manager()
        if "vivado" in mgr.mcp_server_runtime:
            logger.info("检测到已注册的 vivado MCP, 先停掉再用插件配置重启")
            await mgr.disable_mcp_server("vivado", timeout=15)
        await mgr.enable_mcp_server(
            name="vivado",
            config=cfg,
            timeout=180,
        )
        state.mark_running(True)
        logger.info(
            f"vivado MCP 已启动: {cfg['command']} {' '.join(cfg['args'])}"
        )
    except Exception as e:
        state.mark_running(False)
        logger.warning(f"vivado MCP 启动失败, spcode 其它工具不受影响: {e}")


async def shutdown_mcp(plugin, *, state: "_state.VivadoState") -> None:
    """关闭 vivado MCP (terminate 时调用)。失败不抛异常。"""
    task = state.get_task()
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    state.clear_task()

    if not state.mcp_running:
        return
    try:
        mgr = plugin.context.get_llm_tool_manager()
        if "vivado" in mgr.mcp_server_runtime:
            await mgr.disable_mcp_server("vivado", timeout=15)
    except Exception as e:
        logger.warning(f"vivado MCP 关闭失败: {e}")
    finally:
        state.mark_running(False)
