"""TodoUpdateTool — 更新一个或多个 item 的 status / notes。

v2.12 (PR-split-modify): 从 v2.6 的 todo_modify(mode='update', item_ids=, status=, notes=) 拆出。

notes 三态语义(与 v2.6 保持一致,行为不变):
- notes 未传(缺省) → 保留旧值
- notes=""          → 清空 notes
- notes="xxx"       → 覆盖 notes
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .. import todo_list as _todo_list_mod
from .todo_base import _TodoToolBase


@dataclass
class TodoUpdateTool(_TodoToolBase):
    """Update status / notes of one or more items in the current todo list."""

    name: str = "todo_update"
    description: str = (
        "Update status and/or notes of one or more items by id. "
        "item_ids must be a list[int] (batch update preferred). "
        "Any missing id → all-or-nothing rollback. "
        "Returns full list + stats."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "item_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "description": (
                        "Target item id(s). ALWAYS pass as a JSON array, "
                        "e.g. [3] or [1, 3, 5]. "
                        "Batch update is preferred over multiple single-item calls."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "cancelled"],
                    "description": (
                        "New status. OMIT this key to keep the current status."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": (
                        "New notes. OMIT this key to leave notes unchanged. "
                        'Pass empty string "" to clear notes.'
                    ),
                },
            },
            "required": ["item_ids"],
            "additionalProperties": False,
        }
    )

    async def call(
        self,
        context,
        item_ids: int | list[int] | None = None,
        status: str = "",
        notes: str | None = _todo_list_mod.UNSET_NOTES,
        **kwargs,
    ) -> ToolExecResult:
        if item_ids is None:
            return self._err(
                "item_ids 必填",
                proposal="传入 item_ids=[3] 或 item_ids=[1, 3, 5]",
            )
        # notes 三态 → TodoStore.update() 的 (notes, clear_notes) 二元
        if notes is None or notes is _todo_list_mod.UNSET_NOTES:
            # 未传 → 保留旧值
            actual_notes, actual_clear = "", False
        elif notes == "":
            # 空串 → 清空
            actual_notes, actual_clear = "", True
        else:
            # 非空 → 覆盖
            actual_notes, actual_clear = notes, False
        return await self._dispatch(
            context,
            lambda s, k: s.update(
                k,
                item_ids,
                status=status,
                notes=actual_notes,
                clear_notes=actual_clear,
            ),
        )
