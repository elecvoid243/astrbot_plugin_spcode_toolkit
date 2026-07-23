"""VivadoManager - 4 个 /vivado 子命令 handler (PR-2 2026-07-23)。

不调 subprocess (用户明确: 仅 spcode 直接 spawn 的进程需 _NO_WINDOW_KWARGS,
此处通过 plugin.context.get_llm_tool_manager() 透传, AstrBot 内部处理)。
cmd_* 通过 yield 引导消息让用户用 chat 让 LLM 调 mcp_vivado__* 工具。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Callable

from .._vivado_mcp import VivadoNotFoundError, find_vivado_executable
from .state import VivadoState

logger = logging.getLogger(__name__)


class VivadoManager:
    """/vivado 子命令的 handler 集合。"""

    def __init__(self, *, state: VivadoState, plugin_getter: Callable) -> None:
        self._state = state
        self._plugin = plugin_getter

    async def cmd_path(self, event) -> AsyncIterator[str]:
        """/vivado path - 显示当前 VIVADO_PATH。"""
        try:
            path = find_vivado_executable(
                configured=(self._plugin()._config.get("vivado_executable") or "")
            )
        except VivadoNotFoundError as e:
            yield (
                f"❌ 未找到 Vivado 安装。\n"
                f"   请检查:\n"
                f"   - 插件配置 vivado_executable (spcode WebUI)\n"
                f"   - 系统环境变量 VIVADO_PATH\n"
                f"   - 默认安装路径 (Windows: D:/Xilinx/Vivado/*/, Linux: /opt/Xilinx/Vivado/*/)\n"
                f"   底层错误: {e}"
            )
            return
        yield f"✅ 当前 VIVADO_PATH:\n   {path}"

    async def cmd_status(self, event) -> AsyncIterator[str]:
        """/vivado status - 列出活跃 vivado sessions (state 缓存)."""
        if self._state.sessions_stale:
            yield "⚠️ sessions 数据已过期 (>5s), 状态可能不准确"
        sessions = self._state.get_sessions()
        if not sessions:
            yield (
                "ℹ️ 当前没有活跃的 Vivado sessions。\n"
                "   用 chat 让 LLM 调 `mcp_vivado__start_session` 启动"
            )
            return
        lines = [f"📋 活跃 Vivado sessions ({len(sessions)}):\n"]
        for s in sessions:
            sid = s.get("id", "?")
            st = s.get("state", "?")
            elapsed = s.get("started_at", 0)
            lines.append(f"  - {sid} ({st}) started={elapsed:.0f}")
        yield "\n".join(lines)

    async def cmd_start(self, event, session_id: str = "default") -> AsyncIterator[str]:
        """/vivado start [name] - 引导用户用 chat 启动 vivado session。"""
        if not self._state.mcp_running:
            yield "❌ vivado MCP 未运行, 请检查配置或查看 AstrBot 日志"
            return
        yield (
            f"💡 请让 LLM 在 chat 中调用:\n"
            f"   `mcp_vivado__start_session(session_id='{session_id}', timeout=60)`\n\n"
            f"   (这会启动一个新 vivado -mode tcl 进程, ~1GB RAM, 启动时间 10-30s)"
        )

    async def cmd_stop(self, event, session_id: str) -> AsyncIterator[str]:
        """/vivado stop <name> - 引导用户用 chat 停止 vivado session。"""
        if not self._state.mcp_running:
            yield "❌ vivado MCP 未运行"
            return
        yield (
            f"💡 请让 LLM 在 chat 中调用:\n"
            f"   `mcp_vivado__stop_session(session_id='{session_id}')`"
        )
