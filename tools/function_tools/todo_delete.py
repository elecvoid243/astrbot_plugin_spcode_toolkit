"""TodoDeleteTool — 删一个或多个 item。

v2.12 (PR-split-modify): 从 v2.6 的 todo_modify(mode='delete', item_ids=...) 拆出。

item_ids 接受:
- int > 0        → 删单条
- list[int > 0]  → 批量删多条

清空整个 list 请用 todo_clear(独立工具),本工具不接受 0 作为 ID。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .todo_base import _TodoToolBase


@dataclass
class TodoDeleteTool(_TodoToolBase):
    """Delete one or more items by id from the current todo list."""

    name: str = "todo_delete"
    description: str = (
        "Delete one or more items by id from the current todo list. "
        "item_ids must be a list[int] (batch delete preferred). "
        "Any missing id → all-or-nothing rollback. "
        "Returns full list + stats. "
        "For removing the ENTIRE list (not individual items), use todo_clear."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "item_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 100,
                    "description": (
                        "Target item id(s) to delete. ALWAYS pass as a JSON array, "
                        "e.g. [3] or [1, 3, 5]. "
                        "0 is rejected (use todo_clear to wipe the entire list)."
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
        **kwargs,
    ) -> ToolExecResult:
        if item_ids is None:
            return self._err(
                "item_ids 必填",
                proposal="传入 item_ids=[3] 或 item_ids=[1, 3, 5]",
            )
        # v2.12 (PR-split-modify): item_ids=0 是历史 todo_modify(mode='delete', item_ids=0)
        # 清空整个 list 的旧约定。v2.12 起该语义已迁移到 todo_clear,本工具拒绝 0
        # 并显式 proposal 引导 LLM 切到 todo_clear,避免 LLM 用 0 调用拿到含糊错误。
        if item_ids == 0 or (isinstance(item_ids, list) and 0 in item_ids):
            return self._err(
                "todo_delete 不接受 item_ids=0;0 代表清空整个 list",
                proposal="改用 todo_clear() 清空整个列表,或传入正整数 id 删除具体条目",
            )
        return await self._dispatch(context, lambda s, k: s.delete(k, item_ids))
