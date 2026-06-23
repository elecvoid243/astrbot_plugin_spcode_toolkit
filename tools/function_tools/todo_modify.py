"""TodoModifyTool — 修改 todo list(add / update / delete 三种 mode)。"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .. import todo_list as _todo_list_mod
from .todo_base import _TodoToolBase


@dataclass
class TodoModifyTool(_TodoToolBase):
    """Modify todo list with 3 modes: add / update / delete."""

    name: str = "todo_modify"
    description: str = (
        "Modify an existing todo list. Pick exactly one mode:\n"
        "• mode='add':    todo_modify(mode='add', items=[{title, status?, notes?}, ...])\n"
        "• mode='update': todo_modify(mode='update', item_ids=N or [N,...], status=?, notes=?)\n"
        "• mode='delete': todo_modify(mode='delete', item_ids=N or [N,...])\n"
        "Returns full list + stats. Any invalid id → all-or-nothing rollback."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["add", "update", "delete"],
                    "description": "Operation mode.",
                },
                "items": {
                    "type": "array",
                    "items": {"type": "object"},
                    "minItems": 1,
                    "description": "[Required for add mode] Items to append. Each: {title, status?, notes?}.",
                },
                "item_ids": {
                    "anyOf": [
                        {"type": "integer"},
                        {"type": "array", "items": {"type": "integer"}, "minItems": 1},
                    ],
                    "description": "[Required for update/delete mode] Target item id(s). ",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "cancelled"],
                    "description": "[Required for update mode] New status. Omit = keep existing.",
                },
                "notes": {
                    "type": "string",
                    "description": (
                        "[update mode] New notes value. "
                        'OVERWRITE: pass a non-empty string, e.g. "blocked on review". '
                        'CLEAR: pass the empty string "". '
                        "KEEP: OMIT this key entirely from the JSON object — "
                        "do NOT write null, \"\", or any placeholder to express 'keep'; "
                        "leaving the key out means 'leave the existing notes unchanged'."
                    ),
                },
            },
            "required": ["mode"],
            "additionalProperties": False,
        }
    )

    async def call(
        self,
        context,
        mode: str,
        items: list[dict] | None = None,
        item_ids: int | list[int] | None = None,
        status: str = "",
        notes: str | None = _todo_list_mod.UNSET_NOTES,
        **kwargs,
    ) -> ToolExecResult:
        """notes 三态语义:
        - notes=None (未传) → 保留旧值
        - notes=""   (空串) → 清空 notes
        - notes="x"  (内容) → 覆盖 notes
        """
        if mode == "add" and (items is None or not items):
            return self._err(
                "add 模式必须提供非空 items",
                proposal="传入 items=[{...}, ...]",
            )
        if mode in ("update", "delete") and item_ids is None:
            return self._err(
                f"{mode} 模式必须提供 item_ids",
                proposal="传入 item_ids=3 或 item_ids=[1, 3, 5]",
            )
        return await self._dispatch(
            context,
            lambda s, k: s.modify(
                k,
                mode=mode,
                items=items,
                item_ids=item_ids,
                status=status,
                notes=notes,
            ),
        )
