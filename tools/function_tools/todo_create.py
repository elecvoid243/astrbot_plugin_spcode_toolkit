"""TodoCreateTool — 创建新 todo list(覆盖当前 umo 的)。"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import ToolExecResult

from .. import todo_list as _todo_list_mod
from .todo_base import _TodoToolBase


@dataclass
class TodoCreateTool(_TodoToolBase):
    """Create a new todo list. Overwrites any existing list for current umo (session channel)."""

    name: str = "todo_create"
    description: str = (
        "Create a new todo list (overwrites existing). "
        "Use to start tracking multi-step work. "
        "Returns full list + stats. "
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
                        "Initial items. Each: {title, status?, notes?}. "
                        "status defaults to 'pending' if omitted. "
                        "Cannot be empty. "
                        "Mutually exclusive with from_path."
                    ),
                },
                "from_path": {
                    "type": "string",
                    "description": (
                        "Absolute path to a .md todo file to import (adopt semantics). "
                        "Format: same as TodoStore.render_md output — "
                        "frontmatter (umo/title/created_at/...) + H1 + "
                        "checkbox items (`[ ]` pending / `[x]` done / `[~]` in_progress / `[-]` cancelled). "
                        "Mutually exclusive with items. Size limit 1MB. "
                        "Adopt: the imported list takes your umo (current session) and now() timestamp; "
                        "item IDs are renumbered from 1. "
                        "Must be an absolute path; relative paths are rejected."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": (
                        "List title. "
                        "items 模式下: 空 = auto-generated from umo. "
                        "from_path 模式下: 非空覆盖文件中的 title; 空 (falsy, 不含纯空白) = 保留文件中的 title. "
                        "Whitespace-only title 视为有值(覆盖),与 v2.2.0 一致."
                    ),
                },
            },
            # required 留空 —— XOR 在 call() 内校验,工具 schema 不强制
        }
    )

    async def call(
        self,
        context,
        items: list[dict] | None = None,
        from_path: str = "",
        title: str = "",
        **kwargs,
    ) -> ToolExecResult:
        """XOR 校验 + 分发到 items 模式 / from_path 模式.

        v2.9 新增 from_path: 接受 .md 文件路径,领养(覆盖当前用户的现有 list,
        重排 ID 从 1,时间戳 = now,保留文件中的 status/notes)。
        """
        # XOR 校验
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

        # from_path 模式
        if from_path:
            items_parsed, parsed_title, err = _todo_list_mod.import_from_path(from_path)
            if err:
                return self._err(err, proposal="检查文件路径/格式/大小后重试")
            return await self._dispatch(
                context,
                lambda s, k: s.create(
                    k, title=title or parsed_title, items=items_parsed
                ),
            )

        # items 模式(v2.2.0 现状,行为不变)
        return await self._dispatch(
            context, lambda s, k: s.create(k, title=title, items=items)
        )
