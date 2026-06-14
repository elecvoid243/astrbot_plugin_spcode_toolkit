"""
astrbot_plugin_spcode_toolkit — spcode 精简开发工具箱

为 LLM Agent 提供 7 个核心开发工具：
  - code_check（Python / C·C++ 语法+风格合并检查）
  - codegraph（语义索引 + 调用链追踪）
  - es_search（文件名极速搜索）
  - file_remove（沙箱化删除）
  - file_compare（文件差异比较）
  - todo_list（LLM 自我管理）

参考自 Irmia DevKit 大幅精简。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from astrbot.api import FunctionTool, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import StarTools
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.api.star import register

from .tools import (
    code_check,
    es_search,
    file_compare,
    file_remove,
    todo_list as _todo_list_mod,
)
from .tools._helpers import run_sync, unwrap, err_json
from .tools._config_filter import ALL_TOOL_NAMES, filter_enabled_tools
from .tools._codegraph_mcp import (
    SHELL_META_RE,
    build_cli_launcher,
    detect_codegraph_launcher,
    ensure_stdio_allowlist,
    resolve_project_path,
)
from .tools import agentsmd as _agentsmd_mod
from .tools.inta_shell import tools as _inta_shell_tools
from .tools.inta_shell.component import LocalInteractiveShellComponent
from .tools._path_safety import is_path_safe as _is_path_safe
import time as _time

# 让 main.py 可以动态添加 MethodType
from collections import defaultdict

# inta_shell 组件单例(v2.5: 由 initialize 设置,FunctionTool 通过模块级引用访问)
_inta_component: LocalInteractiveShellComponent | None = None
_inta_default_cwd: str = ""

# 防御性:老版本 AstrBot 可能没有 MCP 异常类
try:
    from astrbot.core.provider.func_tool_manager import (  # type: ignore
        MCPShutdownTimeoutError,
        MCPInitTimeoutError,
    )

    _HAS_MCP_EXCEPTIONS = True
except ImportError:
    _HAS_MCP_EXCEPTIONS = False
    MCPShutdownTimeoutError = None  # type: ignore
    MCPInitTimeoutError = None  # type: ignore

_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "last": 0.0})


def _record(name: str) -> None:
    try:
        entry = _stats[name]
        entry["count"] += 1
        entry["last"] = _time.time()
    except Exception:
        pass


_DEFAULT_CONFIG = {
    "es_path": "",  # Everything es.exe 路径（Windows）；Linux/macOS 留空
    "cppcheck_path": "",  # cppcheck.exe 路径（Windows/Linux/macOS）；C/C++ 检查时优先于 cpplint
    "cppcheck_shortcircuit": "error",  # cppcheck 短路策略：error/warning/never（仅 auto 模式生效）
    "codegraph_enabled": True,  # 是否启用 codegraph MCP 集成
    "codegraph_install_dir": "",  # codegraph 安装目录(含 node.exe); 留空则不启动 MCP
    "codegraph_project": "",  # codegraph daemon 默认操作的工程根目录
    "agentsmd_enabled": True,  # 是否启用 AGENTS.md 管理
    # inta_shell 配置(v2.5)
    "inta_shell_max_sessions": 10,  # 最大并发会话数
    "inta_shell_session_timeout": 1800,  # 会话空闲超时(秒)
    "inta_shell_block_unsafe": True,  # 是否阻止危险命令
    "inta_shell_default_cwd": "",  # 默认工作目录
    "allowed_ids": "",  # 逗号分隔额外允许的用户 ID
    "enabled_tools": [],  # 启用的工具名列表；空 = 全部禁用（安全默认）
    "file_remove_blacklist": [],  # file_remove 用户自定义黑名单：绝对路径前缀列表
}

# ALL_TOOL_NAMES 和 filter_enabled_tools 已提取到 tools/_config_filter.py
# 方便单元测试直接 import（避免 main.py 顶层依赖 astrbot.api）


# ── /project 命令注入文本常量(v2.7) ─────────────────────
# 注入到 system_prompt 的防重复 marker。
# 与 _agentsmd_mod.INJECTION_MARKER 同等用途——同一请求多次走钩子时不重复追加。
_PROJECT_GUIDANCE_MARKER = "# Use Codegraph"

# /project load 后注入到 system_prompt 末尾的指引。
# 设计目标:让 LLM 优先使用 codegraph 工具组而非 astrbot_file_grep_tool,
# 提升代码搜索/分析的效率与准确性(已建好语义索引,无需 grep 全文本)。
_PROJECT_CODEGRAPH_GUIDANCE = f"""
{_PROJECT_GUIDANCE_MARKER}
A codegraph project is loaded. When dealing with the code for this project:
- Priority use codegraph_* tool (e.g. codegraph_explore) for code lookup, call chain analysis, and symbol localization.
- When the codegraph_* tool is unavailable or when viewing non code index files (e.g. configurations, logs), return to a generic lookup tool like `astrbot_file_grep_tool`
"""


# ── Tool 类定义 ──────────────────────────────────────


@dataclass
class CodeCheckTool(FunctionTool):
    name: str = "code_check"
    description: str = (
        "Unified syntax + style check for a single Python or C/C++ source file. "
        "For both languages a single linter covers BOTH syntax errors and style "
        "issues, so one call replaces a 'syntax check then lint' workflow. "
        "Auto-detects the linter from the file extension: "
        ".py → ruff (PEP 8 + common lint rules); "
        ".c/.cpp/.cc/.cxx/.h/.hpp/.hxx → cpplint (Google C++ Style Guide). "
        "Returns a structured list of issues; the first 5 include surrounding "
        "source-context lines (→ marks the offending line). "
        "Other extensions (e.g. .js/.ts/.go/.nim) are NOT supported by this "
        "tool. Requires the linter to be installed: "
        "pip install ruff (Python) or pip install cpplint (C/C++)."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": (
                        "Path to the source file. Extension determines the linter: "
                        ".py → ruff; .c/.cpp/.cc/.cxx/.h/.hpp/.hxx → cpplint."
                    ),
                },
                "linter": {
                    "type": "string",
                    "enum": ["auto", "ruff", "cpplint"],
                    "description": (
                        "Override the linter. 'auto' (default) picks by extension."
                    ),
                    "default": "auto",
                },
            },
            "required": ["filepath"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        filepath: str,
        linter: str = "auto",
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            result = await run_sync(code_check.check, filepath, linter)
            return unwrap(result)
        except Exception as e:
            return err_json(f"code_check 失败: {e}")


@dataclass
class EsSearchTool(FunctionTool):
    name: str = "es_search"
    description: str = (
        "Fast FILENAME search (does not search file contents). "
        "Prefer this over reading whole directory trees to locate a file. "
        "Supports wildcards, regex, extension and path filters, case/whole-word"
        "toggles, and size/date sorting."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Filename or pattern. Examples: '*.py', 'main', 'config.json'. "
                        "On Windows, supports Everything syntax (ext:py, path:C:\\src). "
                        "On POSIX, basic wildcards only. "
                        "Must NOT start with '/' or '-' unless regex=true."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Limit search to this directory. Omit to search the whole system."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 100,
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat query as a regular expression (Windows only).",
                    "default": False,
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case-sensitive matching.",
                    "default": False,
                },
                "whole_word": {
                    "type": "boolean",
                    "description": "Match whole words only (Windows only).",
                    "default": False,
                },
                "file_type": {
                    "type": "string",
                    "enum": ["all", "file", "folder"],
                    "description": "Restrict result type to files, folders, or both.",
                    "default": "all",
                },
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "name",
                        "path",
                        "size",
                        "ext",
                        "date_modified",
                        "date_created",
                        "date_accessed",
                        "run_count",
                    ],
                    "description": (
                        "Sort field. Most options work on Windows; "
                        "POSIX backends only support name/path/size/date_modified."
                    ),
                },
                "ext": {
                    "type": "string",
                    "description": (
                        "Filter by file extension WITHOUT the leading dot, e.g. 'py', "
                        "'xlsx', 'exe'."
                    ),
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        query: str,
        path: str | None = None,
        max_results: int = 100,
        regex: bool = False,
        case_sensitive: bool = False,
        whole_word: bool = False,
        file_type: str = "all",
        sort_by: str | None = None,
        ext: str | None = None,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            result = await run_sync(
                es_search.search,
                query,
                path,
                max_results,
                regex,
                case_sensitive,
                whole_word,
                file_type,
                sort_by,
                ext,
            )
            return unwrap(result)
        except Exception as e:
            return err_json(f"es_search 失败: {e}")


@dataclass
class FileRemoveTool(FunctionTool):
    name: str = "astrbot_file_remove_tool"
    description: str = (
        "Delete an entire file or directory. Before deleting, it is necessary to ask the user. "
        "If delete fragments instead of the entire file, use `astrbot_file_edit_tool`. "
        "Deleting a DIRECTORY requires parameter 'confirm=true'. "
        "If a directory contains more than max_items files, the call returns a "
        "proposal asking for batch confirmation INSTEAD of deleting — read the "
        "proposal/options, then retry with confirm=true. "
        "Single files are deleted without confirm."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Absolute path of the file or directory to remove. "
                        "Must not contain '..' segments and must not be inside a "
                        "protected system directory or the user-configured "
                        "blacklist (see plugin config 'file_remove_blacklist')."
                    ),
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "Set to true to confirm a directory deletion. "
                        "Required for directories; ignored for single files."
                    ),
                    "default": False,
                },
                "max_items": {
                    "type": "integer",
                    "description": (
                        "If a directory contains more than this many files, return a "
                        "proposal for batch confirmation instead of deleting. "
                        "Defaults to 50."
                    ),
                    "default": 50,
                },
            },
            "required": ["path"],
        }
    )
    # 用户自定义黑名单（从插件配置 file_remove_blacklist 注入），
    # 不暴露给 LLM 作为 function parameter——是服务端策略。
    custom_blacklist: list[str] = field(default_factory=list)

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        path: str,
        confirm: bool = False,
        max_items: int = 50,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            result = await run_sync(
                file_remove.remove,
                path,
                confirm,
                max_items,
                list(self.custom_blacklist),
            )
            return unwrap(result)
        except Exception as e:
            return err_json(f"file_remove 失败: {e}")


@dataclass
class FileDiffTool(FunctionTool):
    name: str = "astrbot_file_compare_tool"
    description: str = (
        "Compares two text files and returns a structured diff: counts of added and "
        "removed lines, plus a unified diff. Files larger than 50MB are rejected. "
        "Reads as UTF-8 with a GBK fallback for Windows-encoded Chinese text. "
        "Use this to review the impact of an edit or to compare candidate alternatives."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "file_a": {
                    "type": "string",
                    "description": "Path of the first (baseline) file.",
                },
                "file_b": {
                    "type": "string",
                    "description": "Path of the second (modified) file.",
                },
            },
            "required": ["file_a", "file_b"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        file_a: str,
        file_b: str,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            result = await run_sync(file_compare.compare, file_a, file_b)
            return unwrap(result)
        except Exception as e:
            return err_json(f"file_compare 失败: {e}")


@dataclass
class _TodoToolBase(FunctionTool):
    """4 个 todo_* 工具的公共基类。

    封装 sender_key 提取、TodoStore 初始化、async dispatch。
    子类只需定义自己的 parameters / call()，其余样板代码继承自此基类。

    所有 call() 方法返回 ToolExecResult (JSON 字符串):
    - 成功路径经 _dispatch → unwrap() → JSON 字符串
    - 失败路径经 _err() 直接生成 JSON 字符串
    """

    def _err(self, error: str, proposal: str = "") -> str:
        """Build a JSON error response string with optional proposal.

        永远返回 JSON 字符串(与 unwrap() 风格一致),保证 call() 协议统一。
        """
        import json as _json

        payload: dict = {"ok": False, "error": error}
        if proposal:
            payload["proposal"] = proposal
        return _json.dumps(payload, ensure_ascii=False)

    def _setup(self, context) -> tuple | dict:
        """提取 sender_key,创建 store,返回 (store, sender_key) 元组。

        失败时返回 dict 错误响应(供 _dispatch 透传给 unwrap 包成 JSON 字符串)。
        """
        try:
            event = context.context.event
        except AttributeError:
            return {"ok": False, "error": "无 event 上下文"}
        sender_key = _todo_list_mod.extract_sender_key(event)
        data_dir = str(StarTools.get_data_dir())
        todos_dir = os.path.join(data_dir, "todos")
        store = _todo_list_mod.TodoStore(todos_dir)
        return store, sender_key

    async def _dispatch(self, context, fn, *args, **kwargs) -> str:
        """通用 dispatch: 记录调用 + setup + 异步执行 fn(store, sender_key, *args, **kwargs)。

        返回: 永远为 JSON 字符串(与 unwrap() 风格一致)。
        - setup 失败 → 直接 unwrap(setup_dict) 透传 proposal 字段
        - 业务异常 → err_json 包装
        """
        _record(self.name)
        try:
            setup = self._setup(context)
            if isinstance(setup, dict):  # 错误响应,经 unwrap 包成 JSON 字符串
                return unwrap(setup)
            store, sender_key = setup
            result = await run_sync(lambda: fn(store, sender_key, *args, **kwargs))
            return unwrap(result)
        except Exception as e:
            return err_json(f"{self.name} 失败: {e}")


@dataclass
class TodoCreateTool(_TodoToolBase):
    """Create a new todo list. Overwrites any existing list for current user."""

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
                        "Cannot be empty."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "List title. Empty = auto-generated from sender_key.",
                },
            },
            "required": ["items"],
        }
    )

    async def call(
        self, context, items: list[dict], title: str = "", **kwargs
    ) -> ToolExecResult:
        if not items:
            return self._err(
                "items 不能为空",
                proposal='至少提供一个 item: {"title": "..."}',
            )
        return await self._dispatch(
            context, lambda s, k: s.create(k, title=title, items=items)
        )


@dataclass
class TodoQueryTool(_TodoToolBase):
    """Read current todo list with full stats and attention items."""

    name: str = "todo_query"
    description: str = (
        "Read current todo list. Returns list + stats + attention_items. "
        "attention_items = IDs of in_progress items with non-empty notes "
        "(stuck/blocked items needing attention). "
        "If no list exists, returns proposal to call todo_create."
    )
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(self, context, **kwargs) -> ToolExecResult:
        return await self._dispatch(context, lambda s, k: s.query(k))


@dataclass
class TodoModifyTool(_TodoToolBase):
    """Modify todo list with 3 modes: add / update / delete."""

    name: str = "todo_modify"
    description: str = (
        "Modify an existing todo list. 3 modes: "
        "add=append items; "
        "update=change status/notes by item_ids; "
        "delete=remove by item_ids. "
        "For update: notes='' clears notes, omit notes keeps existing. "
        "For delete: use todo_clear() to delete the whole list. "
        "All operations return full list + stats. "
        "Any invalid id → all-or-nothing rollback."
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
                        "[update mode] New notes. "
                        "Empty string = clear notes. "
                        "Omit = keep existing."
                    ),
                },
            },
            "required": ["mode"],
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


@dataclass
class TodoClearTool(_TodoToolBase):
    """Delete the entire todo list (remove file) for current user."""

    name: str = "todo_clear"
    description: str = (
        "Delete the entire todo list for current user (removes the file). "
        "Use this to start fresh. "
        "For removing individual items, use todo_modify(mode='delete', item_ids=...)."
    )
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def call(self, context, **kwargs) -> ToolExecResult:
        return await self._dispatch(context, lambda s, k: s.clear(k))


# ── inta_shell 工具(v2.5 从 interactive_shell 插件集成) ─


@dataclass
class IntaShellStartTool(FunctionTool):
    name: str = "astrbot_inta_shell_start"
    description: str = (
        "Start a long-running interactive shell session for commands that need "
        "multiple rounds of input (e.g. npm init, python REPL, git add -p, "
        "interactive installers), or an always-on application (e.g. launch a server). Returns a session_id — keep using "
        "inta_shell_send / inta_shell_read to drive the session, and ALWAYS "
        "call inta_shell_stop when done. Does NOT support full TTY programs "
        "(vim, nano, less). For one-off non-interactive commands, prefer the "
        "regular `astrbot_execute_shell` tool."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Interactive command to start. If a non-interactive "
                        "form exists (e.g. `npm init -y` instead of `npm init`), "
                        "prefer that and use the regular execute_shell tool."
                    ),
                },
                "env": {
                    "type": "object",
                    "description": (
                        "Optional env-var dict. Keys and values must be strings."
                    ),
                },
            },
            "required": ["command"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        command: str,
        env: dict | None = None,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            event = context.context.event
            # WHY: tools.inta_shell.tools.start() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。如果再走 unwrap(str),会触发
            # "工具返回了非预期类型: str" 错误(虽然底层进程已被启动)。
            # 直接透传字符串结果即可。
            return await _inta_shell_tools.start(
                _inta_component,
                event.unified_msg_origin,
                command,
                env=env,
                default_cwd=_inta_default_cwd,
            )
        except Exception as e:
            return err_json(f"inta_shell_start 失败: {e}")


@dataclass
class IntaShellSendTool(FunctionTool):
    name: str = "astrbot_inta_shell_send"
    description: str = (
        "Send input text to an active inta_shell session to drive the program "
        "forward. Auto-appends a newline if missing. Common uses: answer "
        "prompts with 'y' / 'n' / empty (default), or type the next command. "
        "Pair with inta_shell_read to see the response."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID returned by inta_shell_start.",
                },
                "input": {
                    "type": "string",
                    "description": "Text to send to the interactive program.",
                },
                "send_eof": {
                    "type": "boolean",
                    "description": (
                        "If true, close stdin after sending (signals "
                        "end-of-input to the program)."
                    ),
                    "default": False,
                },
            },
            "required": ["session_id", "input"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        session_id: str,
        input: str,
        send_eof: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.send() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.send(
                _inta_component, session_id, input, send_eof=send_eof
            )
        except Exception as e:
            return err_json(f"inta_shell_send 失败: {e}")


@dataclass
class IntaShellReadTool(FunctionTool):
    name: str = "astrbot_inta_shell_read"
    description: str = (
        "Read output from an active inta_shell session. Blocks up to "
        "`timeout` seconds for new output. A prompt (e.g. `[Y/n]`, `>>>`) "
        "in the returned text usually means the program is waiting for "
        "inta_shell_send input."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID returned by inta_shell_start.",
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Max seconds to wait for output. Increase for slow programs."
                    ),
                    "default": 5.0,
                },
                "max_chars": {
                    "type": "number",
                    "description": ("Max characters to read. Caps large outputs."),
                    "default": 4096,
                },
            },
            "required": ["session_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        session_id: str,
        timeout: float = 5.0,
        max_chars: int = 4096,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.read() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.read(
                _inta_component, session_id, timeout=timeout, max_chars=max_chars
            )
        except Exception as e:
            return err_json(f"inta_shell_read 失败: {e}")


@dataclass
class IntaShellStopTool(FunctionTool):
    name: str = "astrbot_inta_shell_stop"
    description: str = (
        "Terminate an inta_shell session. ALWAYS call this when done to free "
        "resources (each session holds a process and pipes). Default: send "
        "Ctrl+C for graceful exit, then force-kill on timeout. Use `force=true` "
        "only if the session is fully unresponsive. Use inta_shell_list first "
        "to see what's still running."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to terminate.",
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "If true, kill immediately without sending Ctrl+C. "
                        "Use only when the session is fully unresponsive."
                    ),
                    "default": False,
                },
            },
            "required": ["session_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        session_id: str,
        force: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.stop() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.stop(
                _inta_component, session_id, force=force
            )
        except Exception as e:
            return err_json(f"inta_shell_stop 失败: {e}")


@dataclass
class IntaShellListTool(FunctionTool):
    name: str = "astrbot_inta_shell_list"
    description: str = (
        "List all active inta_shell sessions. Use to check what needs cleanup "
        "with inta_shell_stop before finishing a task."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.list_sessions() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.list_sessions(_inta_component)
        except Exception as e:
            return err_json(f"inta_shell_list 失败: {e}")


# ── 插件入口 ────────────────────────────────────────


_PLUGINS_TOOLS = [
    CodeCheckTool(),
    EsSearchTool(),
    FileRemoveTool(),
    FileDiffTool(),
    TodoCreateTool(),
    TodoQueryTool(),
    TodoModifyTool(),
    TodoClearTool(),
    IntaShellStartTool(),
    IntaShellSendTool(),
    IntaShellReadTool(),
    IntaShellStopTool(),
    IntaShellListTool(),
]


def _build_allowed_ids(context, config: dict) -> set[str]:
    """从配置 + AstrBot 全局管理员构建允许列表。"""
    allowed: set[str] = set()
    extra = config.get("allowed_ids", "")
    if extra:
        for x in extra.replace("，", ",").split(","):
            x = x.strip()
            if x:
                allowed.add(x)
    return allowed


@register(
    "astrbot_plugin_spcode_toolkit",
    "elecvoid243",
    (
        " spcode 开发工具箱 — 提供实用开发工具：code_check（Python / C-C++ 合并 lint）\n"
        "es_search、file_remove、file_comare、todo_list，并整合codegraph MCP\n"
        "部分实现基于Irmia DevKit插件。"
    ),
    "2.1.0",
)
class SPCodeToolkit(star.Star):
    def __init__(self, context: star.Context, config: dict = None) -> None:
        super().__init__(context)
        self.context = context

        # 合并配置
        _config = dict(_DEFAULT_CONFIG)
        if config:
            # 拍平嵌套分组(如 {codegraph:{install_dir:"..."}} → {codegraph_install_dir:"..."})
            config = self._flatten_config(config)
            for k, v in config.items():
                if v not in (None, "", []):
                    _config[k] = v
        self._config = _config  # 持久化,供异步 bootstrap + terminate 访问

        # inta_shell 配置快照(initialize 前暂存,initialize 中消费)
        self._inta_shell_cfg = {
            "max_sessions": int(_config.get("inta_shell_max_sessions", 10) or 10),
            "session_timeout": int(
                _config.get("inta_shell_session_timeout", 1800) or 1800
            ),
            "block_unsafe": bool(_config.get("inta_shell_block_unsafe", True)),
            "default_cwd": str(_config.get("inta_shell_default_cwd", "") or "").strip(),
        }
        # 注入 es_path 到环境变量供 es_search 读取
        if _config.get("es_path"):
            os.environ["ES_PATH"] = _config["es_path"]
        # 注入 cppcheck_path 到环境变量供 code_check._find_cppcheck 读取
        if _config.get("cppcheck_path"):
            os.environ["CPPCHECK_PATH"] = _config["cppcheck_path"]
        # 注入 cppcheck_shortcircuit 模式供 code_check._run_cppcheck 读取
        os.environ["CPPCHECK_SHORTCIRCUIT"] = _config.get(
            "cppcheck_shortcircuit", "all"
        )

        # codegraph 异步启动的 task 引用 + 并发锁
        self._codegraph_task: asyncio.Task | None = None
        self._codegraph_dir_locks: dict[str, asyncio.Lock] = {}

        # AGENTS.md 加载状态: {umo: {"path": str, "directory": str,
        # "last_content": str, "mtime": float}}
        # 每会话独立管理加载状态(v2.4 合并自 agentsmd 插件)
        self._loaded_agents: dict[str, dict] = {}

        # 已加载项目(per-umo)。/project load 时填充,/project unload 时清空。
        # 与 _loaded_agents 平行——一个跟踪 AGENTS.md,一个跟踪整个项目组合状态。
        # 格式: {umo: {"directory": str, "loaded_at": float}}
        self._loaded_projects: dict[str, dict] = {}

        # v2.8: /plan 模式状态(per-umo)。
        # True = plan 模式激活,LLM 工具列表会被 _plan_filter_tools 钩子
        # 过滤掉 plan_mode.blocked_tools 列出的写工具;
        # False/缺省 = build 模式(默认),工具列表完全不动。
        self._plan_mode: dict[str, bool] = {}

        # v2.8: plan 模式第一轮 reminder 注入标记(per-umo)。
        # 避免每轮都在 user message 末尾追加 system-reminder
        # (污染 prefix cache;参考 opencode 设计:reminder 仅在过渡点插入一次)。
        self._plan_reminded: dict[str, bool] = {}

        # 根据 enabled_tools 配置过滤实际注册的工具
        enabled_names, unknown = filter_enabled_tools(
            ALL_TOOL_NAMES,
            _config.get("enabled_tools") or [],
        )
        if unknown:
            logger.warning(
                f"enabled_tools 中包含未识别的工具名: {sorted(unknown)}，已忽略"
            )
        tools_to_register = [t for t in _PLUGINS_TOOLS if t.name in enabled_names]

        # 把 file_remove_blacklist 配置注入到 FileRemoveTool 实例。
        # 注意：必须在 enabled_tools 过滤之后做——未启用的工具不需要注入。
        for t in tools_to_register:
            if isinstance(t, FileRemoveTool):
                t.custom_blacklist = list(_config.get("file_remove_blacklist") or [])

        # 注册过滤后的工具
        self._tool_names = {t.name for t in tools_to_register}
        context.add_llm_tools(*tools_to_register)
        if tools_to_register:
            logger.info(
                f"spcode_toolkit ready — {len(tools_to_register)} tools registered: "
                f"{sorted(t.name for t in tools_to_register)}"
            )
        else:
            logger.warning(
                "spcode_toolkit ready — 0 tools registered (enabled_tools 为空)。"
                "请在 WebUI 配置页勾选要启用的工具后重启 AstrBot。"
            )

        # 异步启动 codegraph MCP(不阻塞插件加载)
        if _config.get("codegraph_enabled", True):
            self._codegraph_task = asyncio.create_task(self._bootstrap_codegraph_mcp())

    async def initialize(self) -> None:
        """插件激活（AstrBot 框架在 __init__ 后调用）。

        构造 inta_shell 组件单例。codegraph MCP 由 __init__ 内异步任务管理，
        不在这里重复处理。
        """
        cfg = self._inta_shell_cfg
        component = LocalInteractiveShellComponent(
            max_sessions=cfg["max_sessions"],
            session_timeout_seconds=cfg["session_timeout"],
            enable_block=cfg["block_unsafe"],
        )
        global _inta_component, _inta_default_cwd
        _inta_component = component
        _inta_default_cwd = cfg["default_cwd"]
        logger.info(
            "[inta_shell] initialized: max_sessions=%d, session_timeout=%ds, "
            "block_unsafe=%s, default_cwd=%s",
            cfg["max_sessions"],
            cfg["session_timeout"],
            cfg["block_unsafe"],
            cfg["default_cwd"] or "(auto)",
        )

    @staticmethod
    def _flatten_config(config: dict) -> dict:
        """拍平嵌套分组为扁平 dict。

        AstrBot 的 "type":"object" 分组会把子字段存入嵌套 dict:
          {"codegraph": {"install_dir": "...", "project": "..."}}
        →
          {"codegraph_install_dir": "...", "codegraph_project": "..."}

        顶级非 dict 字段原样保留。
        """
        flat: dict = {}
        for key, value in config.items():
            if isinstance(value, dict):
                flat.update(value)
            else:
                flat[key] = value
        return flat

    # ── /codegraph 命令组(AstrBot 规范: 命令组和子命令必须是插件类方法)───
    @filter.command_group("codegraph", alias={"cg"})
    def codegraph(self):
        """codegraph 项目管理指令组。"""
        pass

    # ── /project 命令组(v2.7 组合 agentsmd + codegraph) ───────────

    @filter.command_group("project")
    def project(self):
        """项目管理指令组(组合 agentsmd + codegraph 一键加载/卸载)。

        仅在 agentsmd_enabled 和 codegraph_enabled 都为 true 时可用。
        handler 入口会做 feature flag 校验,关闭时返回明确错误。
        """
        pass

    @project.command("load")
    async def project_load(self, event, directory: str):
        """/project load <directory>

        一键加载项目到当前会话:同时执行 agentsmd init+load 和 codegraph init+set,
        并在 system_prompt 注入 codegraph 优先使用指引。

        前置条件:
        - agentsmd_enabled = true
        - codegraph_enabled = true
        - 当前会话未加载其他项目(Q2=B 决策:重复 load 直接拒绝,需先 unload)
        """
        # 1. Feature flag 校验
        agentsmd_on = self._config.get("agentsmd_enabled", True)
        codegraph_on = self._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令要求 agentsmd_enabled 和 codegraph_enabled 都为 true。\n"
                "请在插件配置中启用这两项后重启 AstrBot。"
            )
            return

        # 2. 重复 load 拦截(Q2=B 决策)
        umo = event.unified_msg_origin
        if umo in self._loaded_projects:
            loaded = self._loaded_projects[umo]
            yield event.plain_result(
                f"❌ 当前会话已加载项目: {loaded['directory']}\n"
                f"请先执行 /project unload,再 load 新项目。"
            )
            return

        # 3. 路径解析与安全校验
        directory = _agentsmd_mod.strip_surrounding_quotes(directory)
        target = Path(directory).resolve()
        ok, reason = _is_path_safe(
            target, user_blacklist=self._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        # 4. 步骤 1/3: agentsmd(init 条件性 + load)
        agents_md_path = target / "AGENTS.md"
        if not agents_md_path.exists():
            yield event.plain_result(f"⏳ [1/3] AGENTS.md 不存在,正在 init: {target}")
            async for msg in self._agentsmd_init(event, str(target)):
                yield msg
        else:
            yield event.plain_result(
                f"ℹ️ [1/3] AGENTS.md 已存在,跳过 init: {agents_md_path}"
            )
        yield event.plain_result(f"⏳ [1/3] 正在 load AGENTS.md: {target}")
        async for msg in self._agentsmd_load(event, str(target)):
            yield msg

        # 5. 步骤 2/3: codegraph init + set
        yield event.plain_result(f"⏳ [2/3] codegraph init: {target}")
        async for msg in self._codegraph_init_or_uninit(event, str(target), init=True):
            yield msg

        yield event.plain_result(f"⏳ [2/3] codegraph set: {target}")
        async for msg in self._codegraph_set_project(event, str(target)):
            yield msg

        # 6. 记录状态(必须最后,即使前面步骤失败也记录以便 unload 清理)
        self._loaded_projects[umo] = {
            "directory": str(target),
            "loaded_at": _time.time(),
        }

        # 7. 步骤 3/3 汇总
        yield event.plain_result(
            f"✅ 项目已加载: {target}\n"
            f"已自动进行如下步骤:\n"
            f"  - 设定工作目录\n"
            f"  - AGENTS.md 注入到 system_prompt\n"
            f"  - 载入 codegraph 索引\n"
            f"\n若要卸载，请执行`/project unload`"
        )

    @project.command("unload")
    async def project_unload(self, event):
        """/project unload

        卸载当前会话已加载的项目:
        1. /agentsmd unload(清掉 AGENTS.md 注入)
        2. /codegraph set <codegraph_project>(配置中的默认项目;若未配置则跳过)
        3. 清空 self._loaded_projects[umo] 状态
        """
        # 1. Feature flag 校验
        agentsmd_on = self._config.get("agentsmd_enabled", True)
        codegraph_on = self._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令要求 agentsmd_enabled 和 codegraph_enabled 都为 true。"
            )
            return

        umo = event.unified_msg_origin
        if umo not in self._loaded_projects:
            yield event.plain_result("ℹ️ 当前会话未加载项目,无需 unload。")
            return

        # 2. agentsmd unload(同步返回单条消息)
        yield self._agentsmd_unload(event)

        # 3. codegraph set 回默认项目
        default_project = (self._config.get("codegraph_project") or "").strip()
        if default_project:
            yield event.plain_result(f"⏳ codegraph set 回默认项目: {default_project}")
            async for msg in self._codegraph_set_project(event, default_project):
                yield msg
        else:
            yield event.plain_result(
                "ℹ️ codegraph_project 未配置,跳过 codegraph set。"
                "MCP 当前默认项目维持原状。"
            )

        # 4. 清理状态(必须在最末,即便 set 失败也清,避免用户无法重试)
        info = self._loaded_projects.pop(umo)
        yield event.plain_result(
            f"✅ 项目已卸载: {info['directory']}\n"
            f"  - AGENTS.md 注入已移除\n"
            f"  - codegraph 默认项目已重置"
        )

    @codegraph.command("init")
    async def codegraph_init(self, event, directory: str):
        """/codegraph init <directory>

        初始化指定目录为 codegraph 项目(创建 .codegraph/ 索引)。
        """
        async for msg in self._codegraph_init_or_uninit(event, directory, init=True):
            yield msg

    @codegraph.command("uninit")
    async def codegraph_uninit(self, event, directory: str):
        """/codegraph uninit <directory>

        反初始化指定目录(删除 .codegraph/ 索引)。
        """
        async for msg in self._codegraph_init_or_uninit(event, directory, init=False):
            yield msg

    @codegraph.command("set")
    async def codegraph_set(self, event, directory: str):
        """/codegraph set <directory>

        修改 codegraph 的默认执行目录。后续 LLM 调用的 codegraph_* 工具
        会以新目录为根。
        """
        async for msg in self._codegraph_set_project(event, directory):
            yield msg

    # ── /agentsmd 命令组(v2.4 合并自独立 agentsmd 插件)──

    @filter.command_group("agentsmd")
    def agentsmd(self):
        """AGENTS.md 管理指令组(从独立插件合并)。"""
        pass

    @agentsmd.command("init")
    async def agentsmd_init(self, event, directory: str):
        """/agentsmd init <directory>"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self._agentsmd_init(event, directory):
            yield msg

    @agentsmd.command("load")
    async def agentsmd_load(self, event, directory: str):
        """/agentsmd load <directory>"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self._agentsmd_load(event, directory):
            yield msg

    @agentsmd.command("unload")
    async def agentsmd_unload(self, event):
        """/agentsmd unload"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        yield self._agentsmd_unload(event)

    @agentsmd.command("update")
    async def agentsmd_update(self, event):
        """/agentsmd update"""
        if not self._config.get("agentsmd_enabled", True):
            yield event.plain_result(
                "AGENTS.md 管理功能已被禁用(agentsmd_enabled=false)。"
            )
            return
        async for msg in self._agentsmd_update(event):
            yield msg

    def _build_mcp_cfg(self) -> dict | None:
        """根据当前 _config 构造 codegraph MCP 启动配置。

        供 _bootstrap_codegraph_mcp 和 _codegraph_set_project 复用。
        Returns None 如果 install_dir 未配置或验证失败(详细原因已在 _detect_from_install_dir 内 log)。
        """
        install_dir = (self._config.get("codegraph_install_dir") or "").strip()
        if not install_dir:
            return None
        cfg = detect_codegraph_launcher(install_dir=install_dir)
        if not cfg:
            return None
        # 若用户在插件配置了 codegraph_project,作为默认项目注入
        project = (self._config.get("codegraph_project") or "").strip()
        if project and not SHELL_META_RE.search(project):
            cfg = dict(cfg)  # 浅拷贝避免污染
            cfg["args"] = list(cfg["args"]) + ["--path", project]
        return cfg

    async def _bootstrap_codegraph_mcp(self) -> None:
        """插件加载后异步拉起 codegraph MCP server。失败不抛异常。"""
        # 防御性:即便 __init__ 已 gate,函数本身也再 check 一次,便于单测
        if not self._config.get("codegraph_enabled", True):
            return
        try:
            # v2.1: 必须显式配置 install_dir 才会启动 MCP(去除 auto-detect)
            install_dir = (self._config.get("codegraph_install_dir") or "").strip()
            if not install_dir:
                logger.info(
                    "codegraph_install_dir 未配置,跳过 MCP 集成"
                    "(spcode 其它工具照常工作;如需启用请配置 codegraph_install_dir)"
                )
                return

            ensure_stdio_allowlist()
            cfg = self._build_mcp_cfg()
            if not cfg:
                # install_dir 已配置但 _detect_from_install_dir 验证失败,
                # 详细原因已在 _detect_from_install_dir 内 log warning
                logger.warning(
                    f"codegraph_install_dir 验证失败,MCP 不启动: {install_dir!r}"
                )
                return

            mgr = self.context.get_llm_tool_manager()
            # 兼容用户在 mcp_server.json 手写过 codegraph 的情况
            if "codegraph" in mgr.mcp_server_runtime:
                logger.info("检测到已注册的 codegraph MCP,先停掉再用插件配置重启")
                await mgr.disable_mcp_server("codegraph")

            await mgr.enable_mcp_server(
                name="codegraph",
                config=cfg,
                timeout=180,  # 与 AstrBot DEFAULT_ENABLE_MCP_TIMEOUT_SECONDS 一致
            )
            logger.info(
                f"codegraph MCP 已启动: {Path(cfg['command']).name} "
                f"{' '.join(cfg['args'])}"
            )
        except Exception as e:
            logger.warning(f"codegraph MCP 启动失败,spcode 其它工具不受影响: {e}")

    async def terminate(self):
        """Star 框架在插件卸载/重载时调用。"""
        global _inta_component, _inta_default_cwd

        # 0. 停 inta_shell 交互式 Shell 组件
        if _inta_component is not None:
            try:
                logger.info("[inta_shell] terminating component...")
                summary = await _inta_component.shutdown()
                logger.info(
                    "[inta_shell] terminated: %d session(s) cleaned (graceful=%d forced=%d)",
                    summary.get("total", 0),
                    summary.get("graceful", 0),
                    summary.get("forced", 0),
                )
            except Exception as e:
                logger.warning("[inta_shell] shutdown error: %s", e)
            finally:
                _inta_component = None
                _inta_default_cwd = ""

        mgr = self.context.get_llm_tool_manager()

        # 1. 取消还在跑的 bootstrap 任务
        if self._codegraph_task and not self._codegraph_task.done():
            self._codegraph_task.cancel()
            try:
                await self._codegraph_task
            except (asyncio.CancelledError, Exception):
                pass

        # 2. 停 codegraph MCP server (只杀 serve --mcp 代理子进程)
        if "codegraph" in mgr.mcp_server_runtime:
            try:
                await mgr.disable_mcp_server("codegraph", timeout=15)
            except Exception as e:
                if (
                    _HAS_MCP_EXCEPTIONS
                    and MCPShutdownTimeoutError
                    and isinstance(e, MCPShutdownTimeoutError)
                ):
                    logger.warning("codegraph MCP 关闭超时,可能留有僵尸代理子进程")
                else:
                    raise

    async def _codegraph_set_project(self, event, directory: str):
        """`/codegraph set <dir>` 命令实现:修改 codegraph 的默认项目根目录。

        行为:
        1. 校验目录(必须存在)
        2. 更新 self._config["codegraph_project"]
        3. 如果 MCP server 正在跑,重启它以应用新的 --path 参数
        4. 后续 LLM 调用的 codegraph_* 工具会以新项目为默认根
        """
        # 1. 路径校验(必须存在的目录)
        target = resolve_project_path(
            directory,
            init=True,
            user_blacklist=self._config.get("file_remove_blacklist") or [],
        )
        if isinstance(target, str):  # 错误消息
            yield event.plain_result(target)
            return

        target_str = str(target)
        # 2. 更新配置
        old = (self._config.get("codegraph_project") or "").strip()
        self._config["codegraph_project"] = target_str
        logger.info(f"codegraph_project: {old!r} → {target_str!r}")

        mgr = self.context.get_llm_tool_manager()
        mcp_running = "codegraph" in mgr.mcp_server_runtime

        # 3a. 如果 MCP 没在跑(可能 install_dir 未配置 / 验证失败),
        # 不重启,只更新 config;bootstrap 或下次重启会生效
        if not mcp_running:
            yield event.plain_result(
                f"✅ codegraph_project 已更新为: {target_str}\n"
                "   (MCP 当前未运行,新项目将在下次启动 codegraph MCP 时生效)"
            )
            return

        # 3b. MCP 在跑 → 重启以应用新 --path
        try:
            yield event.plain_result(
                f"🔄 正在重启 codegraph MCP 以应用新项目: {target_str}..."
            )
            await mgr.disable_mcp_server("codegraph", timeout=15)
        except Exception as e:
            logger.warning(f"codegraph MCP 关闭失败(可能 zombie 进程): {e}")
            # 继续尝试启动新实例

        try:
            ensure_stdio_allowlist()
            cfg = self._build_mcp_cfg()
            if not cfg:
                yield event.plain_result(
                    f"⚠️ 已更新 codegraph_project,但无法构造新 MCP 配置"
                    f"(install_dir 可能已失效)。新值: {target_str}"
                )
                return
            await mgr.enable_mcp_server(
                name="codegraph",
                config=cfg,
                timeout=180,
            )
            yield event.plain_result(
                f"✅ codegraph 已切换到新项目: {target_str}\n"
                "   后续 LLM 调用的 codegraph_* 工具默认在此目录下操作"
            )
            logger.info(f"codegraph MCP 已重启,新 --path: {target_str}")
        except Exception as e:
            logger.warning(f"codegraph MCP 重启失败: {e}")
            yield event.plain_result(
                f"❌ codegraph MCP 重启失败: {e}\n"
                f"   (codegraph_project 已更新为 {target_str},"
                f"重启 AstrBot 后生效)"
            )

    async def _codegraph_init_or_uninit(self, event, directory: str, *, init: bool):
        """共享实现,init/uninit 只差一个 subcommand。"""
        # 1. 路径校验
        target = resolve_project_path(
            directory,
            init=init,
            user_blacklist=self._config.get("file_remove_blacklist") or [],
        )
        if isinstance(target, str):  # 错误消息
            yield event.plain_result(target)
            return

        # 2. 找 codegraph CLI 启动器
        # 优先用用户配置的 install_dir;若未配置(单次命令场景),fallback 到 auto-detect
        install_dir = (self._config.get("codegraph_install_dir") or "").strip() or None
        mcp_cfg = detect_codegraph_launcher(install_dir=install_dir)
        cli_launcher = build_cli_launcher(mcp_cfg)
        if not cli_launcher:
            yield event.plain_result(
                "❌ 找不到 codegraph CLI,请先 `npm install -g @colbymchenry/codegraph`"
            )
            return

        # 3. 并发锁(防止同目录并发 init/uninit 把 .codegraph/ 写坏)
        target_str = str(target)
        lock = self._codegraph_dir_locks.setdefault(target_str, asyncio.Lock())
        if lock.locked():
            yield event.plain_result(
                f"⏳ 目录 {target_str} 已有 codegraph 操作在跑,请等待完成"
            )
            return
        async with lock:
            # 4. 立即回应
            action = "初始化" if init else "反初始化"
            yield event.plain_result(
                f"⏳ 正在 {action} codegraph 项目 {target_str}...\n"
                f"   (大型项目可能耗时数分钟,期间请勿重复执行)"
            )

            # 5. 异步执行
            sub = "init" if init else "uninit"
            cmd_args = cli_launcher["args"] + [sub, target_str]
            if not init:
                cmd_args.append("--force")

            try:
                proc = await asyncio.create_subprocess_exec(
                    cli_launcher["command"],
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as e:
                yield event.plain_result(f"❌ 启动 codegraph 失败: {e}")
                return

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                yield event.plain_result(f"❌ codegraph {sub} 超时(300s),已终止")
                return

            if proc.returncode == 0:
                if init:
                    yield event.plain_result(
                        f"✅ codegraph 初始化完成: {target_str}\n"
                        f"   下一步:在对话中用 codegraph_status 验证索引,"
                        f"或直接用 codegraph_explore 触发懒加载建索引"
                    )
                else:
                    yield event.plain_result(
                        f"✅ codegraph 反初始化完成: {target_str}\n"
                        f"   (.codegraph/ 目录已删除)"
                    )
            else:
                err = (
                    (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
                )
                err_lower = err.lower()
                if init and ("not found" in err_lower or "no such file" in err_lower):
                    yield event.plain_result(
                        f"❌ 目录不存在: {target_str}\n   请先创建或检查路径"
                    )
                elif init and (
                    "already initialized" in err_lower or "exists" in err_lower
                ):
                    # 自动用 --force 重试一次
                    yield event.plain_result(
                        "⚠️ 目标目录已初始化 codegraph,自动用 --force 重试..."
                    )
                    retry_args = cli_launcher["args"] + [
                        sub,
                        target_str,
                        "--force",
                    ]
                    proc2 = await asyncio.create_subprocess_exec(
                        cli_launcher["command"],
                        *retry_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout2, stderr2 = await asyncio.wait_for(
                            proc2.communicate(), timeout=180
                        )
                        if proc2.returncode == 0:
                            yield event.plain_result(
                                f"✅ codegraph 重新初始化完成: {target_str}"
                            )
                        else:
                            err2 = (
                                (stderr2 or stdout2 or b"")
                                .decode("utf-8", errors="replace")
                                .strip()
                            )
                            yield event.plain_result(
                                f"❌ --force 重试也失败:\n{err2[:1500]}"
                            )
                    except asyncio.TimeoutError:
                        proc2.kill()
                        yield event.plain_result("❌ --force 重试超时")
                elif (not init) and (
                    "not initialized" in err_lower or "no .codegraph" in err_lower
                ):
                    yield event.plain_result(
                        "ℹ️ 目标目录未初始化 codegraph(没有 .codegraph/),无需 uninit"
                    )
                else:
                    yield event.plain_result(
                        f"❌ codegraph {sub} 失败(退出码 {proc.returncode}):\n{err[:1500]}"
                    )

    # ── /agentsmd 业务方法(v2.4)────────────────────

    async def _agentsmd_init(self, event, directory: str):
        """/agentsmd init <directory> 实现。"""
        directory = _agentsmd_mod.strip_surrounding_quotes(directory)
        target = Path(directory).resolve()

        ok, reason = _is_path_safe(
            target, user_blacklist=self._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        if not target.exists():
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                yield event.plain_result(f"❌ 无法创建目录 `{directory}`: {e}")
                return

        if not target.is_dir():
            yield event.plain_result(f"❌ `{directory}` 不是一个有效的目录。")
            return

        agents_md_path = target / "AGENTS.md"
        if agents_md_path.exists():
            yield event.plain_result(
                f"⚠️ 目录 `{directory}` 下已存在 AGENTS.md。\n"
                "如需重新生成,请先删除该文件后再执行 init。"
            )
            return

        yield event.plain_result(f"🔄 正在为 `{directory}` 生成 AGENTS.md,请稍候…")

        umo = event.unified_msg_origin
        provider = self.context.get_using_provider(umo=umo)
        init_template = _agentsmd_mod.resolve_init_template(self._config)

        content = await _agentsmd_mod.generate_agents_md_via_llm(
            provider, target, umo=umo, init_template=init_template
        )

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, content, encoding="utf-8"
            )
            yield event.plain_result(
                f"✅ 已在 `{directory}` 下创建 AGENTS.md({len(content)} 字符)。\n"
                f"路径: {agents_md_path}\n"
                f"您可以使用 `/agentsmd load {directory}` 将其加载到当前会话的系统提示词中。"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")

    async def _agentsmd_load(self, event, directory: str):
        """/agentsmd load <directory> 实现。"""
        directory = _agentsmd_mod.strip_surrounding_quotes(directory)
        target = Path(directory).resolve()

        ok, reason = _is_path_safe(
            target, user_blacklist=self._config.get("file_remove_blacklist")
        )
        if not ok:
            yield event.plain_result(f"❌ 路径不允许: {reason}")
            return

        agents_md_path = target / "AGENTS.md"
        if not agents_md_path.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到 AGENTS.md 文件。\n"
                f"请先使用 `/agentsmd init {directory}` 创建。"
            )
            return

        try:
            content = await asyncio.to_thread(
                agents_md_path.read_text, encoding="utf-8"
            )
            mtime = agents_md_path.stat().st_mtime
        except Exception as e:
            yield event.plain_result(f"❌ 读取 AGENTS.md 失败: {e}")
            return

        umo = event.unified_msg_origin
        self._loaded_agents[umo] = {
            "path": str(agents_md_path),
            "directory": str(target),
            "last_content": content,
            "mtime": mtime,
        }

        yield event.plain_result(
            f"✅ 已加载 `{directory}/AGENTS.md` 到当前会话的系统提示词。\n"
            f"内容长度: {len(content)} 字符\n"
            "后续每次 LLM 请求都会自动注入该内容。\n"
            "使用 `/agentsmd unload` 可卸载,使用 `/agentsmd update` 可手动更新。"
        )

    def _agentsmd_unload(self, event):
        """/agentsmd unload 实现。"""
        umo = event.unified_msg_origin
        if umo not in self._loaded_agents:
            return event.plain_result("ℹ️ 当前会话未加载任何 AGENTS.md。")
        info = self._loaded_agents.pop(umo)
        return event.plain_result(
            f"✅ 已卸载 AGENTS.md 注入。\n原文件: `{info['path']}`"
        )

    async def _agentsmd_update(self, event):
        """/agentsmd update 实现。"""
        umo = event.unified_msg_origin
        if umo not in self._loaded_agents:
            yield event.plain_result(
                "当前会话未加载 AGENTS.md。请先使用 `/agentsmd load <路径>` "
                "加载一个 AGENTS.md。"
            )
            return

        info = self._loaded_agents[umo]
        agents_md_path = Path(info["path"])
        dir_path = Path(info["directory"])
        existing_content = info["last_content"]

        if not agents_md_path.exists():
            yield event.plain_result(
                f"AGENTS.md 文件不存在: {agents_md_path}\n"
                "请检查文件是否被删除,或重新使用 `/agentsmd init` 初始化。"
            )
            return

        yield event.plain_result(
            "正在使用 LLM 重新生成 AGENTS.md (基于现有内容 + 最新目录结构) . .."
        )

        provider = self.context.get_using_provider(umo=umo)
        if provider is None:
            yield event.plain_result("当前会话未配置 LLM Provider,无法更新。")
            return

        init_template = _agentsmd_mod.resolve_init_template(self._config)
        prompt = (
            f"{init_template}\n\n"
            "以下是该项目的文件结构和关键文件内容摘要:\n\n"
            f"{_agentsmd_mod.scan_project_context(dir_path)}\n\n"
            "## 现有 AGENTS.md 内容\n\n"
            f"{existing_content}\n\n"
            "请比较现有实现和目录结构与 AGENTS.md 是否有差异,并更新 AGENTS.md\n\n"
            "请直接输出 AGENTS.md 的完整内容(Markdown 格式),不要添加任何额外说明,"
            "也不要使用 ```markdown 等代码块包裹整个内容。"
        )

        try:
            llm_resp = await provider.text_chat(
                prompt=prompt,
                session_id=umo,
                contexts=[],
                system_prompt="你是一名资深软件工程师,擅长为多种语言的项目编写规范文档。",
            )
        except Exception as e:
            yield event.plain_result(f"LLM 调用失败: {e}")
            return

        new_content = (getattr(llm_resp, "completion_text", "") or "").strip()
        new_content = _agentsmd_mod.strip_code_fence(new_content)
        new_content = new_content if new_content else existing_content

        try:
            await asyncio.to_thread(
                agents_md_path.write_text, new_content, encoding="utf-8"
            )
        except Exception as e:
            yield event.plain_result(f"❌ 写入 AGENTS.md 失败: {e}")
            return

        info["last_content"] = new_content
        info["mtime"] = agents_md_path.stat().st_mtime

        yield event.plain_result(
            f"AGENTS.md 已更新 ({len(new_content)} 字符)。\n"
            "后续 LLM 请求将自动使用新版本。"
        )

    @filter.on_llm_request()
    async def _agentsmd_inject_to_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """每次 LLM 请求前,若当前会话已加载 AGENTS.md,注入到 system_prompt 末尾。"""
        if not self._config.get("agentsmd_enabled", True):
            return
        umo = event.unified_msg_origin
        if umo not in self._loaded_agents:
            return

        info = self._loaded_agents[umo]
        agents_md_path = Path(info["path"])
        content = info.get("last_content", "")

        # 通过 mtime 检测变更(自动刷新缓存,无需手动 update)
        try:
            if agents_md_path.exists():
                current_mtime = agents_md_path.stat().st_mtime
                if current_mtime != info.get("mtime"):
                    content = await asyncio.to_thread(
                        agents_md_path.read_text, encoding="utf-8"
                    )
                    self._loaded_agents[umo]["last_content"] = content
                    self._loaded_agents[umo]["mtime"] = current_mtime
                    logger.debug(
                        f"[agentsmd] 检测到 AGENTS.md 已变更,已刷新缓存: {agents_md_path}"
                    )
            else:
                logger.warning(
                    f"[agentsmd] AGENTS.md 文件不存在,使用缓存内容: {agents_md_path}"
                )
        except Exception as e:
            logger.error(f"[agentsmd] 读取 AGENTS.md 失败,使用缓存: {e}")

        if not content:
            return

        # 防重复注入(同一请求)
        if _agentsmd_mod.INJECTION_MARKER in (req.system_prompt or ""):
            return

        # v2.8: 把项目目录也注入到 system_prompt(放在 AGENTS.md 之前),
        # 让 LLM 知道当前会话绑定到哪个项目。
        directory = info.get("directory", "")
        if req.system_prompt is None or req.system_prompt == "":
            req.system_prompt = _agentsmd_mod.build_injection(
                content, directory=directory
            ).lstrip("\n")
        else:
            req.system_prompt = req.system_prompt + _agentsmd_mod.build_injection(
                content, directory=directory
            )

        logger.debug(
            f"[agentsmd] 已向会话 {umo} 的 system_prompt 注入 AGENTS.md "
            f"({len(content)} 字符)"
        )

    @filter.on_llm_request()
    async def _project_inject_codegraph_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """/project load 后,把 codegraph 优先使用指引注入到 system_prompt 末尾。

        与 _agentsmd_inject_to_llm_request 平行,但走独立 marker 防重复。
        只在以下条件同时满足时注入:
        - codegraph_enabled = true
        - 当前 umo 已在 self._loaded_projects 中

        实现要点:
        - marker (`_PROJECT_GUIDANCE_MARKER`) 检测防重复
        - system_prompt = None 时用 lstrip("\n") 避免前置空行
        - 已存在 system_prompt 时追加在末尾
        """
        if not self._config.get("codegraph_enabled", True):
            return
        umo = event.unified_msg_origin
        if umo not in self._loaded_projects:
            return
        # 防重复(同一请求多次走钩子)
        if _PROJECT_GUIDANCE_MARKER in (req.system_prompt or ""):
            return
        if req.system_prompt is None or req.system_prompt == "":
            req.system_prompt = _PROJECT_CODEGRAPH_GUIDANCE.lstrip("\n")
        else:
            req.system_prompt = req.system_prompt + _PROJECT_CODEGRAPH_GUIDANCE
        logger.debug(f"[project] 已向会话 {umo} 的 system_prompt 注入 codegraph 指引")

    @filter.on_llm_request()
    async def _auth_guard(self, event, req: ProviderRequest):
        """L1 鉴权：非管理员从工具列表中移除本插件工具。"""
        if not req.func_tool:
            return
        # 当前 AstrBot 的 is_admin 实现
        is_admin = False
        try:
            is_admin = bool(event.is_admin())
        except Exception:
            pass
        if is_admin:
            return
        # 管理员可见全部；非管理员 → 全部隐藏（spcode_toolkit 是管理员工具集）
        kept = []
        removed = []
        for tool in req.func_tool.tools:
            if tool.name in self._tool_names:
                removed.append(tool.name)
                continue
            kept.append(tool)
        if removed:
            try:
                from astrbot.core.agent.tool import ToolSet

                new_set = ToolSet()
                for tool in kept:
                    new_set.add_tool(tool)
                req.func_tool = new_set
            except Exception as exc:
                logger.warning(f"spcode_toolkit 鉴权失败: {exc}")

    # ── v2.8: /plan 模式 — 工具过滤 + reminder 注入 + 简化的 /plan /build 命令 ──
    #
    # 设计要点(参考 opencode plan/build 模式):
    # 1. /build 模式(默认)下钩子 no-op,完全不影响 LLM 调用
    #    —— 与默认 AstrBot 行为完全一致(零开销)
    # 2. /plan 模式下:从 req.func_tool 中过滤 plan_mode_blocked_tools 列表
    # 3. reminder 仅在 plan 模式**第一轮**注入(后续轮次不再重复)
    # 4. reminder 放 user message 末尾(**不放 system_prompt**),保证 prefix cache
    # 5. 命令极简化:输入 /plan → 激活,输入 /build → 退出(回到默认)
    #
    # ⚠️ 配置键必须是**顶层扁平键**(`plan_mode_blocked_tools` / `plan_mode_reminder`),
    # 不能嵌套为 `{"plan_mode": {"blocked_tools": [...]}}`。
    # 原因:AstrBot 把 _conf_schema.json 中的 "object" 包装视为 UI 分组,
    # 实际 config 键全部扁平化到顶层。参考 spcode 其他配置:
    # `self._config.get("agentsmd_enabled")` / `self._config.get("codegraph_enabled")` 等。
    #    不引入 /plan on/off 等二级子命令,降低用户记忆负担
    # 6. 状态完全 per-umo(per 会话)隔离,跨 session 互不影响
    # 7. 配置由 _conf_schema.json 中的 plan_mode 节控制

    def _filter_func_tool(self, req: ProviderRequest, blocked: set[str]) -> int:
        """从 req.func_tool 中过滤掉 blocked 集合里的工具名,返回被过滤的数量。

        设计要点(参考 opencode plan 模式):
        1. 新建 ToolSet 替换原引用,避免 in-place 修改原 list
           —— 防止共享引用污染其他 session(多 agent run 共享 func_tool 时)
        2. 被过滤的工具**完全从 LLM 工具列表消失**(schema 不序列化)
           —— LLM 看不到也调不到,比"调用时拒绝"更干净
        3. 不存在的工具名静默跳过——配置可写"计划中"的工具名
        """
        if not req.func_tool or not blocked:
            return 0
        kept = [t for t in req.func_tool.tools if t.name not in blocked]
        actual_removed = len(req.func_tool.tools) - len(kept)
        if actual_removed == 0:
            return 0
        try:
            from astrbot.core.agent.tool import ToolSet

            new_set = ToolSet()
            for t in kept:
                new_set.add_tool(t)
            req.func_tool = new_set
            return actual_removed
        except Exception as exc:
            logger.warning(f"spcode_toolkit 工具过滤失败: {exc}")
            return 0

    @filter.on_llm_request()
    async def _plan_filter_tools(self, event, req: ProviderRequest):
        """v2.8: /plan 模式钩子 — 从 LLM 工具列表过滤写工具 + 注入 reminder。"""
        umo = event.unified_msg_origin
        if not self._plan_mode.get(umo, False):
            return  # build 模式(默认):不做事
        if not req.func_tool:
            return

        blocked_tools = self._config.get("plan_mode_blocked_tools") or []
        if blocked_tools:
            blocked_set = set(blocked_tools)
            removed_count = self._filter_func_tool(req, blocked_set)
            if removed_count > 0:
                logger.debug(
                    f"[plan] 会话 {umo}: 从工具列表过滤 {removed_count} 个写工具"
                )
        else:
            # plan 模式激活但没配置 blocked_tools = 配置错误,记 warning
            logger.warning(
                f"[plan] 会话 {umo} 处于 plan 模式但 plan_mode_blocked_tools 为空,"
                f"将不会过滤任何工具。请在 _conf_schema.json 配置。"
            )

        # plan 模式第一轮:在 user message 末尾追加 reminder(prefix cache 友好)
        if self._plan_reminded.get(umo, False):
            return

        reminder_template = (
            self._config.get("plan_mode_reminder") or ""
        ).strip()
        if not reminder_template:
            # 没配 reminder,标记为已注入(避免每轮检查)
            self._plan_reminded[umo] = True
            return

        # 替换 {blocked} 占位符
        blocked_str = ", ".join(sorted(set(blocked_tools))) if blocked_tools else "(none)"
        reminder_text = reminder_template.replace("{blocked}", blocked_str)
        if not reminder_text.lstrip().startswith("<system-reminder>"):
            reminder_text = f"<system-reminder>\n{reminder_text}\n</system-reminder>"

        # 追加到最后一条 user 消息(OpenAI 格式 dict)
        # WHY: 不放 system_prompt 是为了避免污染 prefix cache;
        #      user message 改动只影响本轮,后续轮 reminder 不再注入。
        if isinstance(req.contexts, list) and req.contexts:
            for msg in reversed(req.contexts):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        msg["content"] = content + "\n\n" + reminder_text
                    break

        self._plan_reminded[umo] = True
        logger.debug(
            f"[plan] 会话 {umo}: 已注入 plan 模式 reminder 到 user message"
        )

    @filter.command("plan")
    async def plan(self, event):
        """/plan — 进入 plan 模式(过滤写工具,提示 LLM 调研而非动手)

        与 opencode /plan 等价:激活后,_plan_filter_tools 钩子会从 LLM 工具列表
        过滤掉 plan_mode.blocked_tools 列出的写工具;首次 LLM 调用时自动注入
        plan 模式 reminder 到 user message。

        退出请使用 /build。
        """
        umo = event.unified_msg_origin
        was_active = self._plan_mode.get(umo, False)
        self._plan_mode[umo] = True
        # 重置 reminder 标记,下次 LLM 调用时再次注入
        self._plan_reminded.pop(umo, None)
        blocked = self._config.get("plan_mode_blocked_tools") or []
        if not blocked:
            yield event.plain_result(
                "⚠️ plan 模式已激活,但 plan_mode_blocked_tools 为空。\n"
                "将不会过滤任何工具。请在插件配置中填写要过滤的工具名。\n"
                "使用 /build 退出 plan 模式。"
            )
            return
        if was_active:
            # 已经在 plan 模式时再次输入,顺手重置 reminder 让 LLM 重新看到
            yield event.plain_result(
                f"🔄 plan 模式仍激活 (会话 {umo})\n"
                f"已过滤 {len(blocked)} 个写工具:{', '.join(blocked)}\n"
                f"reminder 已重置,下次 LLM 调用时重新注入。\n"
                f"使用 /build 退出 plan 模式。"
            )
        else:
            yield event.plain_result(
                f"✅ plan 模式已激活 (会话 {umo})\n"
                f"已过滤 {len(blocked)} 个写工具:{', '.join(blocked)}\n"
                f"LLM 仅可使用只读工具调研。使用 /build 退出。"
            )

    @filter.command("build")
    async def build(self, event):
        """/build — 退出 plan 模式,回到默认 build 模式(全部工具可用)

        与 opencode /build 等价:build 是默认状态,本命令等价于"关闭 plan 模式"。
        执行后,LLM 工具列表不再被过滤,下次 LLM 调用按完整工具集处理。
        """
        umo = event.unified_msg_origin
        was_active = self._plan_mode.pop(umo, False)
        self._plan_reminded.pop(umo, None)
        if was_active:
            yield event.plain_result(
                f"✅ plan 模式已关闭 (会话 {umo})。所有工具现已可用。"
            )
        else:
            yield event.plain_result(
                f"ℹ️ 已在 build 模式 (会话 {umo})。所有工具默认可用,无需切换。"
            )
