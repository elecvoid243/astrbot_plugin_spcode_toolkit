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

# /project load 后注入到 system_prompt 末尾的项目路径声明。
# v2.22 (2026-07-27): 从 agentsmd 子系统解耦 — 此前该文本由
# agentsmd/_core.py 的 PROJECT_PATH_PREFIX_TEMPLATE 持有,依附于
# /agentsmd load;解耦后由 project 子系统(tools/project/inject.py)
# 独立注入,/project load no_agentsmd 也能注入路径。
# 2026-10-08: 文本由中文改为英文。理由:同一 system_prompt 中
# 其余指引(PROJECT_CODEGRAPH_GUIDANCE / AGENTS.md 注入)已是英文,中英混排会
# 让模型对指引段的"指令"属性判别变弱。MARKER 与 TEMPLATE 必须成对改写 —
# MARKER 是 inject_guidance 的防重复哨兵(子串匹配),不同步会造成重复注入。
PROJECT_PATH_MARKER: str = "Current project working directory:"

PROJECT_PATH_GUIDANCE_TEMPLATE: str = """
Current project working directory: {directory}
When modifying or writing to the project, prefer `git worktree` (if available).
"""

# v2.24.1 (2026-08-15): codegraph >= 1.5 默认仅暴露 codegraph_explore 一个工具
# (官方有意裁剪, tools.ts DEFAULT_MCP_TOOLS = {'explore'}),故指引从 codegraph_*
# 泛称收紧为 codegraph_explore,避免 LLM 尝试调用不可见的 codegraph_node/status 等。
# 2026-08-15: 第二条改为"永远显式传 projectPath"——避免 LLM 省略参数时
# 落到 --path 默认项目之外的上下文(跨项目/多项目场景更可靠)。
PROJECT_CODEGRAPH_GUIDANCE: str = f"""
{PROJECT_GUIDANCE_MARKER}
A codegraph project is loaded. When dealing with the code for this project:
- Priority use the `codegraph_explore` tool for code lookup, call chain analysis, and symbol localization.
- Always explicitly pass the `projectPath` argument in every `codegraph_explore` call.
- When `codegraph_explore` is unavailable or when viewing non code index files (e.g. configurations, logs), return to a generic lookup tool like `astrbot_file_grep_tool`
"""


# GitDiffSidebar "激活 worktree" 后注入到 extra_user_content_parts 的指引。
# 2026-09-30 起改为普通 TextPart(不再 mark_as_temp):随 user 消息
# 持久化到会话历史,保持 prefix cache 连续性(temp 文本不落库,下一轮
# 请求在注入点分叉,上一轮 assistant 输出的缓存会被丢弃);每次请求
# 重新注入,切换/取消激活后新块附在新 user 消息尾部,模型以最新一条为准。
# 2026-10-08: 文本由中文改为英文(与 system_prompt 侧指引语种统一)。
# <active_worktree> 标签是块的边界标记,不随措辞变化 — 消费方(前端高亮/
# 测试)以它定位注入块,而非以正文文案定位。
ACTIVE_WORKTREE_GUIDANCE_TEMPLATE: str = """
<active_worktree>
Active git worktree: {worktree} (branch: {branch})
For file reads/writes, code changes and git operations on this project, use that worktree path as the working directory unless the user explicitly specifies another path.
</active_worktree>
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
# 措辞刻意用 "VERY frequently" / 粗体强调 / 防遗忘结尾句提升触发率。
# 注: v2.12 起 todo_add/todo_update/todo_delete 支持批量(item_ids 列表),
# 旧版 "Do NOT batch" 文案已移除(批量现在是官方推荐用法)。
TODO_GUIDANCE_MARKER: str = "# Use `todo_*` to record tasks"

TODO_GUIDANCE: str = f"""
{TODO_GUIDANCE_MARKER}
You have access to the `todo_*` tools to plan and track multi-step tasks.
Use these tools VERY frequently:
- Call `todo_create(items=[...])` **before** starting the first step of a multi-step task (3+ steps).
- Call `todo_update(item_ids=[N], status="in_progress")` when you start a step.
- Call `todo_update(item_ids=[N], status="done")` **as soon as** you complete a step.
- Your todo list is per-agent: subagents keep their own separate list, and the main agent cannot see it (and vice versa). Never assume another agent's items are yours.

If you do not use these tools when planning, you may forget important tasks — and that is unacceptable.
"""


CODE_CHECK_GUIDANCE_MARKER: str = "# Use `code_check` for linting"

CODE_CHECK_GUIDANCE: str = f"""
{CODE_CHECK_GUIDANCE_MARKER}
When you need to lint or inspect a Python or C/C++ source file:
- Priority use the built-in `code_check` tool. It runs ruff (for .py) or cppcheck + clang-format (for .c/.cpp/.h/.hpp).
- DO NOT call `ruff check`, `cppcheck`, or `clang-format` via `subprocess.run([...])` or shell.
"""


CODE_FORMAT_GUIDANCE_MARKER: str = "# Use `code_format` for formatting"

CODE_FORMAT_GUIDANCE: str = f"""
{CODE_FORMAT_GUIDANCE_MARKER}
When you need to format a Python or C/C++/Java/JS/TS/C# source file:
- Priority use the built-in `code_format` tool. It runs ruff format (for .py) or clang-format (for other supported extensions) internally without spawning a subprocess.
- DO NOT call `ruff format`, `clang-format`, or any other external formatter via `subprocess.run([...])` or shell.
"""

CODE_CRAP_GUIDANCE_MARKER: str = "# Use `code_crap` for change-risk checks"

CODE_CRAP_GUIDANCE: str = f"""
{CODE_CRAP_GUIDANCE_MARKER}
When you write or modify Python or C/C++ code, use the built-in `code_crap` tool to measure change risk (CRAP combines cyclomatic complexity with test coverage per function):
- After implementing or refactoring functions in a file, run `code_crap` on it; fix functions flagged `moderate`/`high` (split complex functions, add tests).
- Pass `lcov` when a coverage file exists; without it scores are worst-case (coverage assumed 0%).
"""

# vivado-mcp 集成 (PR-5 2026-07-23)
# 2026-10-08: 指引正文由中文改为英文(system_prompt 内所有指引语种统一)。
# VIVADO_INJECTION_MARKER 是 inject_vivado_guidance 的防重复哨兵(子串匹配),
# 必须出现在渲染结果首行 — 改措辞时不要动 {marker} 位置与文案。
VIVADO_INJECTION_MARKER: str = (
    "# === vivado-mcp integration guidance (auto-injected by spcode) ==="
)

VIVADO_WRITE_TOOLS: tuple[str, ...] = (
    "mcp_vivado__add_files",
    "mcp_vivado__close_project",
    "mcp_vivado__create_project",
    "mcp_vivado__generate_bitstream",
    "mcp_vivado__open_project",
    "mcp_vivado__program_device",
    "mcp_vivado__run_implementation",
    "mcp_vivado__run_synthesis",
    "mcp_vivado__run_tcl",
    "mcp_vivado__start_session",
    "mcp_vivado__stop_session",
)

VIVADO_GUIDANCE_TEMPLATE: str = """{marker}
[vivado-mcp integration]
This plugin integrates vivado-mcp (21 tools), communicating over the MCP protocol via Python stdio.
- Default session_id = "{session_default}". Use a dedicated session for long-running tasks.
- Write tools ({write_tool_count} of them: {write_tools_sample}) are callable only in build mode (i.e. not while /plan is active).
- Programming the device (program_device) is irreversible — verify the bitstream is correct first.
- start_session spawns a `vivado -mode tcl` subprocess (~1GB RAM); enable it with care in containers / CI.
- For vivado session status, use the chat command /vivado status or GET /spcode/vivado-status.
- Vivado path precedence: spcode config `vivado_executable` > system `VIVADO_PATH` env > auto-detect.""".strip()
