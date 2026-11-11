"""function_tools 子包 — 7 个 LLM FunctionTool 类的集中注册表。

PR-2 (2026-06-23) 从 main.py 提取。设计原则:
1. 一文件一工具类(便于定位 + 单测)
2. 共用基类 (`_TodoToolBase`) 单独文件(todo_base.py)
3. 共享 helper (`_common.py`) 抽离 record_and_run 模板
4. main.py 通过 ALL_TOOL_CLASSES 统一注册,避免重复列举

设计依据: `docs/superpowers/specs/2026-06-22-main-py-refactor-design.md`
"""

from __future__ import annotations

# 共享 helper(内部,导出供 tests 调试用)
from ._common import record_and_run  # noqa: F401

# 6 个独立工具(v2.14 加 code_format,v2.25 加 code_crap)
from .code_check import CodeCheckTool
from .code_crap import CodeCrapTool
from .code_format import CodeFormatTool
from .es_search import EsSearchTool
from .file_diff import FileDiffTool
from .file_remove import FileRemoveTool

# 1 个 todo 工具(基类 _TodoToolBase 也导出,供 tests/test_*.py 验证继承)
# v2.35 (PR-merge-todo): 6 个独立工具(todo_create / query / add / update /
# delete / clear)合并为单一 todo_manage(6 个 action)。action→参数矩阵在
# description 中逐行声明,call() 内做校验并返回可执行 proposal。
from .todo_base import _TodoToolBase  # noqa: F401
from .todo_manage import TodoManageTool

# 集中注册表: main.py 直接迭代此列表传给 context.add_llm_tools(...)
# 顺序与原 _PLUGINS_TOOLS 一致(独立工具 6 → todo 1)
# v2.14 (2026-06-25): 加 CodeFormatTool 到独立工具首位(与 code_check 配对)。
ALL_TOOL_CLASSES = [
    CodeCheckTool,
    CodeFormatTool,
    CodeCrapTool,
    EsSearchTool,
    FileRemoveTool,
    FileDiffTool,
    TodoManageTool,
]

__all__ = [
    "ALL_TOOL_CLASSES",
    "CodeCheckTool",
    "CodeCrapTool",
    "CodeFormatTool",
    "EsSearchTool",
    "FileRemoveTool",
    "FileDiffTool",
    "TodoManageTool",
]
