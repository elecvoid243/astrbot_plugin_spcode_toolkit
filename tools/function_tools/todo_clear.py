"""TodoClearTool — 删除整个 todo list(per umo)。"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .todo_base import _TodoToolBase


@dataclass
class TodoClearTool(_TodoToolBase):
    """Delete the entire todo list (remove file) for current umo (session channel)."""

    name: str = "todo_clear"
    description: str = (
        "Delete the entire todo list for current umo (session channel) (removes the file). "
        "Use this to start fresh. "
        "For removing individual items, use todo_modify(mode='delete', item_ids=...)."
    )
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(self, context, **kwargs) -> ToolExecResult:
        return await self._dispatch(context, lambda s, k: s.clear(k))
