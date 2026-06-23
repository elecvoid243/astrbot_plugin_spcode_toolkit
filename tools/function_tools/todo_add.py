"""TodoAddTool — 追加一个或多个 item 到现有 todo list。

v2.12 (PR-split-modify): 从 v2.6 的 todo_modify(mode='add', items=...) 拆出。
原 `todo_modify` 是一个 3-in-1 工具,LLM 经常把 mode 传错(add/update/delete),
故拆为 3 个职责单一的工具,每个工具的 schema 仅暴露所需参数,
让 LLM 不会再混淆语义。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .todo_base import _TodoToolBase


@dataclass
class TodoAddTool(_TodoToolBase):
    """Append one or more items to the current todo list."""

    name: str = "todo_add"
    description: str = (
        "Append one or more items to the current todo list. "
        "Use this to extend an existing list (created via todo_create). "
        "Returns full list + stats. "
        "Returns ok=False with proposal if list does not exist or item count would exceed limit. "
        "4 statuses: pending `[ ]`, in_progress `[~]`, done `[x]`, cancelled `[-]`."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "cancelled"],
                            },
                            "notes": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                    "minItems": 1,
                    "description": (
                        "Items to append. Each: {title, status?, notes?}. "
                        "status defaults to 'pending' if omitted. "
                        "Cannot be empty."
                    ),
                },
            },
            "required": ["items"],
        }
    )

    async def call(
        self,
        context,
        items: list[dict] | None = None,
        **kwargs,
    ) -> ToolExecResult:
        if not items:
            return self._err(
                "items 必填且非空",
                proposal="传入 items=[{title: '...'}, ...]",
            )
        return await self._dispatch(context, lambda s, k: s.add(k, items))
