"""TodoQueryTool — 读当前 todo list(返回 list + stats + attention_items)。"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .todo_base import _TodoToolBase


@dataclass
class TodoQueryTool(_TodoToolBase):
    """Read current todo list with full stats and attention items."""

    name: str = "todo_query"
    description: str = (
        "Read current todo list. Returns list + stats + attention_items. "
        "attention_items = IDs of in_progress items with non-empty notes "
        "(stuck/blocked items needing attention). "
        "If no list exists, returns proposal to call todo_create."
    )
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(self, context, **kwargs) -> ToolExecResult:
        return await self._dispatch(context, lambda s, k: s.query(k))
