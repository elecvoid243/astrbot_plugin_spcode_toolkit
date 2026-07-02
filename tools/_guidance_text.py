"""LLM system_prompt 注入文本常量(从 main.py 提取)。

设计:marker + 完整文本配对。
- marker 用于防重复注入检测(同一请求多次走钩子时)
- 完整文本是注入到 system_prompt 末尾的引导

历史:
    v2.7 由 main.py 第 152-173 行内部定义,本文件是 PR-1 提取的迁移目标。
"""

from __future__ import annotations

# /project load 后注入到 system_prompt 末尾的指引。
PROJECT_GUIDANCE_MARKER: str = "# Use Codegraph"

PROJECT_CODEGRAPH_GUIDANCE: str = f"""
{PROJECT_GUIDANCE_MARKER}
A codegraph project is loaded. When dealing with the code for this project:
- Priority use `codegraph_*` tool (e.g. codegraph_explore) for code lookup, call chain analysis, and symbol localization.
- When the code to look up is not in the current project directory, explicitly provide the path.
- When the codegraph_* tool is unavailable or when viewing non code index files (e.g. configurations, logs), return to a generic lookup tool like `astrbot_file_grep_tool`
"""


# astrbot_file_remove_tool 启用时注入到 system_prompt 末尾的指引。
# 设计目标:让 LLM 优先使用 file_remove 工具(自带路径安全 + 回收站)而非绕过。
# 无 session state 依赖——只靠 self._tool_names 作为 gate。
FILE_REMOVE_GUIDANCE_MARKER: str = "# Delete files only when necessary"

FILE_REMOVE_GUIDANCE: str = f"""
{FILE_REMOVE_GUIDANCE_MARKER}
Priority use 'astrbot_file_remove' for file or directory deletion. DO NOT use shell commands (such as' rm '/' del ') or Python calls to bypass it.
"""


# 6 个 todo_* 工具启用时注入到 system_prompt 末尾的约束。
# 设计目标:让 LLM 在 multi-step 任务中"先建 list、再动手、逐条标 done",
# 仿照 OpenCode anthropic.txt "Task Management" 段 + todowrite.txt 模板。
# 措辞刻意用 "VERY frequently" / 粗体强调 / Do NOT batch 提升触发率与单步粒度。
TODO_GUIDANCE_MARKER: str = "# Use `todo_*` to record tasks"

TODO_GUIDANCE: str = f"""
{TODO_GUIDANCE_MARKER}
You have access to the `todo_*` tools to plan and track multi-step tasks.
Use these tools VERY frequently:
- Call `todo_create(items=[...])` **before** starting the first step of a multi-step task (3+ steps).
- Call `todo_update(item_ids=[N], status="in_progress")` when you start a step.
- Call `todo_update(item_ids=[N], status="done")` **as soon as** you complete a step.

If you do not use these tools when planning, you may forget important tasks — and that is unacceptable.
"""


CODE_CHECK_GUIDANCE_MARKER: str = "# Use `code_check` for linting"

CODE_CHECK_GUIDANCE: str = f"""
{CODE_CHECK_GUIDANCE_MARKER}
When you need to lint or inspect a Python or C/C++ source file:
- Priority use the built-in `code_check` tool. It runs ruff (for .py) or cpplint / cppcheck (for .c/.cpp/.h/.hpp).
- DO NOT call `ruff check`, `cpplint`, or `cppcheck` via `subprocess.run([...])` or shell.
"""


CODE_FORMAT_GUIDANCE_MARKER: str = "# Use `code_format` for formatting"

CODE_FORMAT_GUIDANCE: str = f"""
{CODE_FORMAT_GUIDANCE_MARKER}
When you need to format a Python or C/C++/Java/JS/TS/C# source file:
- Priority use the built-in `code_format` tool. It runs ruff format (for .py) or AStyle (for other supported extensions) internally without spawning a subprocess.
- DO NOT call `ruff format`, `astyle`, or any other external formatter via `subprocess.run([...])` or shell.
"""
