"""TodoManageTool — 用单个工具管理 todo list(6 个 action)。

v2.35 (PR-merge-todo): 6 个独立工具(todo_create / todo_query / todo_add /
todo_update / todo_delete / todo_clear)合并为单一 ``todo_manage``。

动机:todo 工具原本占插件工具面的一半,且每个工具的 description 都要重复
"Returns full list + stats";合并后 LLM 只需记住一个工具名,action 与参数的
对应关系在 description 中逐行列出。

与 v2.6 的 todo_modify(mode=add|update|delete)区别:
- 覆盖 6 个 action(含 query / create / clear),不再需要 6 个工具名
- call() 内做 action→参数矩阵校验,缺参数时返回可执行的 proposal
- ``TodoStore`` 的 6 个方法签名不变 —— 行为等价锚在 store 层
"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .. import todo_list as _todo_list_mod
from .todo_base import _TodoToolBase

_ACTIONS: tuple[str, ...] = ("query", "create", "add", "update", "delete", "clear")

_STATUS_ENUM: list[str] = ["pending", "in_progress", "done", "cancelled"]


@dataclass
class TodoManageTool(_TodoToolBase):
    """Read or modify the current todo list via a single ``action`` parameter."""

    name: str = "todo_manage"
    description: str = (
        "Read or modify the current todo list. Pick exactly one action:\n"
        "• action='query':  no params → returns list + stats + attention_items.\n"
        "• action='create': items=[{title, status?, notes?}, ...] (required, "
        "or from_path=<abs .md path> instead); title?\n"
        "• action='add':    items=[{title, status?, notes?}, ...] (required)\n"
        "• action='update': item_ids=[3] or [1,3,5] (required); status?; notes?\n"
        "• action='delete': item_ids=[3] or [1,3,5] (required)\n"
        "• action='clear':  no params → removes the entire list file.\n"
        "Returns full list + stats. Any invalid id → all-or-nothing rollback. "
        "4 statuses: pending `[ ]`, in_progress `[~]`, done `[x]`, cancelled `[-]`."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "Operation to perform. Required.",
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "status": {"type": "string", "enum": _STATUS_ENUM},
                            "notes": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                    "minItems": 1,
                    "description": (
                        "[create / add] Items to write. Each: {title, status?, notes?}. "
                        "status defaults to 'pending' if omitted."
                    ),
                },
                "from_path": {
                    "type": "string",
                    "description": (
                        "[create only, mutually exclusive with items] Absolute path to "
                        "a .md todo file to import (frontmatter + checkbox items)."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": (
                        "[create only] List title. Empty = auto-generated from umo. "
                        "from_path mode: empty keeps the file's title."
                    ),
                },
                "item_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 1,
                    "maxItems": 100,
                    "description": (
                        "[update / delete] Target item id(s). ALWAYS pass as a JSON "
                        "array, e.g. [3] or [1, 3, 5]. 0 is rejected "
                        "(use action='clear' to wipe the entire list)."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": _STATUS_ENUM,
                    "description": (
                        "[update only] New status. OMIT this key to keep the "
                        "current status."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": (
                        '[update only] New notes. OMIT to leave unchanged; '
                        'pass empty string "" to clear.'
                    ),
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        }
    )

    async def call(
        self,
        context,
        action: str = "",
        items: list[dict] | None = None,
        from_path: str = "",
        title: str = "",
        item_ids: int | list[int] | None = None,
        status: str = "",
        notes: str | None = _todo_list_mod.UNSET_NOTES,
        **kwargs,
    ) -> ToolExecResult:
        """按 action 分发到 TodoStore 的 6 个方法。

        action→参数矩阵:
        - query  : 无参数
        - create : items 或 from_path(二选一),title 可选
        - add    : items 必填
        - update : item_ids 必填,status / notes 可选
        - delete : item_ids 必填(拒绝 0)
        - clear  : 无参数
        """
        action = (action or "").strip().lower()
        if action not in _ACTIONS:
            return self._err(
                f"action 必填且必须是 {list(_ACTIONS)} 之一",
                proposal=(
                    "例如 action='query' 查看列表;"
                    "action='update', item_ids=[3], status='done' 标记完成"
                ),
            )

        if action == "query":
            return await self._dispatch(context, lambda s, k: s.query(k))

        if action == "clear":
            return await self._dispatch(context, lambda s, k: s.clear(k))

        if action == "create":
            if from_path and items:
                return self._err(
                    "items 与 from_path 必须二选一,不能同时传",
                    proposal="items=[{...}] 或 from_path='/abs/path/to/file.md'",
                )
            if not from_path and not items:
                return self._err(
                    "items 与 from_path 必须二选一,不能都不传",
                    proposal="items=[{...}] 或 from_path='/abs/path/to/file.md'",
                )
            if from_path:
                items_parsed, parsed_title, err = _todo_list_mod.import_from_path(
                    from_path
                )
                if err:
                    return self._err(
                        err, proposal="检查文件路径/格式/大小后重试"
                    )
                return await self._dispatch(
                    context,
                    lambda s, k: s.create(
                        k, title=title or parsed_title, items=items_parsed
                    ),
                )
            return await self._dispatch(
                context, lambda s, k: s.create(k, title=title, items=items)
            )

        if action == "add":
            if not items:
                return self._err(
                    "add 需要非空 items",
                    proposal="传入 items=[{title: '...'}, ...]",
                )
            return await self._dispatch(context, lambda s, k: s.add(k, items))

        # update / delete 共用 item_ids 必填校验
        if item_ids is None:
            return self._err(
                f"{action} 需要 item_ids",
                proposal="传入 item_ids=[3] 或 item_ids=[1, 3, 5]",
            )

        if action == "delete":
            if item_ids == 0 or (isinstance(item_ids, list) and 0 in item_ids):
                return self._err(
                    "delete 不接受 item_ids=0;0 代表清空整个 list",
                    proposal=(
                        "改用 action='clear' 清空整个列表,"
                        "或传入正整数 id 删除具体条目"
                    ),
                )
            return await self._dispatch(context, lambda s, k: s.delete(k, item_ids))

        # update: notes 三态 → (notes, clear_notes) 二元
        if notes is None or notes is _todo_list_mod.UNSET_NOTES:
            actual_notes, actual_clear = "", False
        elif notes == "":
            actual_notes, actual_clear = "", True
        else:
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
