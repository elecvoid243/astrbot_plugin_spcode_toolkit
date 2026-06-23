"""function_tools 子包 — 13 个 LLM FunctionTool 类的集中注册表。

PR-2 (2026-06-23) 从 main.py 提取。设计原则:
1. 一文件一工具类(便于定位 + 单测)
2. 共用基类 (`_TodoToolBase`) 单独文件(todo_base.py)
3. 共享 helper (`_common.py`) 抽离 record_and_run 模板
4. inta_shell 5 个工具通过 `tools.inta_shell.runtime` 模块级单例访问 component/cwd
5. main.py 通过 ALL_TOOL_CLASSES 统一注册,避免重复列举

设计依据: `docs/superpowers/specs/2026-06-22-main-py-refactor-design.md`
"""
from __future__ import annotations

# 共享 helper(内部,导出供 tests 调试用)
from ._common import record_and_run  # noqa: F401

# 4 个独立工具
from .code_check import CodeCheckTool
from .es_search import EsSearchTool
from .file_diff import FileDiffTool
from .file_remove import FileRemoveTool

# 4 个 todo 工具(基类 _TodoToolBase 也导出,供 tests/test_*.py 验证继承)
from .todo_base import _TodoToolBase  # noqa: F401
from .todo_clear import TodoClearTool
from .todo_create import TodoCreateTool
from .todo_modify import TodoModifyTool
from .todo_query import TodoQueryTool

# 5 个 inta_shell 工具
from .inta_shell_list import IntaShellListTool
from .inta_shell_read import IntaShellReadTool
from .inta_shell_send import IntaShellSendTool
from .inta_shell_start import IntaShellStartTool
from .inta_shell_stop import IntaShellStopTool

# 集中注册表: main.py 直接迭代此列表传给 context.add_llm_tools(...)
# 顺序与原 _PLUGINS_TOOLS 一致(独立工具 4 → todo 4 → inta_shell 5)
ALL_TOOL_CLASSES = [
    CodeCheckTool,
    EsSearchTool,
    FileRemoveTool,
    FileDiffTool,
    TodoCreateTool,
    TodoQueryTool,
    TodoModifyTool,
    TodoClearTool,
    IntaShellStartTool,
    IntaShellSendTool,
    IntaShellReadTool,
    IntaShellStopTool,
    IntaShellListTool,
]

__all__ = [
    "ALL_TOOL_CLASSES",
    "CodeCheckTool",
    "EsSearchTool",
    "FileRemoveTool",
    "FileDiffTool",
    "TodoCreateTool",
    "TodoQueryTool",
    "TodoModifyTool",
    "TodoClearTool",
    "IntaShellStartTool",
    "IntaShellSendTool",
    "IntaShellReadTool",
    "IntaShellStopTool",
    "IntaShellListTool",
]
