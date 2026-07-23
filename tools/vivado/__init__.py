"""vivado 子包 facade (PR-2 2026-07-23)。

公开 API:
    VivadoSubsystem - 统一门面, 委托到子模块
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from . import state as _state_module
from .bootstrap import bootstrap_mcp, shutdown_mcp
from .manager import VivadoManager
from .state import VivadoState

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


class VivadoSubsystem:
    """vivado-mcp 集成的统一门面, 仿 AgentsmdSubsystem 设计。"""

    def __init__(self, *, plugin: "SPCodeToolkit") -> None:
        self._plugin_getter: Callable[[], "SPCodeToolkit"] = lambda: plugin
        self._state = _state_module.get_state()
        self._manager = VivadoManager(
            state=self._state,
            plugin_getter=self._plugin_getter,
        )

    async def bootstrap(self) -> None:
        await bootstrap_mcp(self._plugin_getter(), state=self._state)

    async def shutdown(self) -> None:
        await shutdown_mcp(self._plugin_getter(), state=self._state)

    async def on_llm_request(self, event, req) -> None:
        """注入 vivado 工具指引 — 委托给 inject.inject_vivado_guidance。"""
        from .inject import inject_vivado_guidance

        await inject_vivado_guidance(self._plugin_getter(), req)

    async def cmd_status(self, event):
        async for msg in self._manager.cmd_status(event):
            yield msg

    async def cmd_start(self, event, session_id: str = "default"):
        async for msg in self._manager.cmd_start(event, session_id):
            yield msg

    async def cmd_stop(self, event, session_id: str):
        async for msg in self._manager.cmd_stop(event, session_id):
            yield msg

    async def cmd_path(self, event):
        async for msg in self._manager.cmd_path(event):
            yield msg


__all__ = [
    "VivadoSubsystem",
    "VivadoManager",
    "VivadoState",
    "bootstrap_mcp",
    "shutdown_mcp",
]
