"""spcode 工具箱全局配置与状态(集中管理)。

从 main.py 抽出的模块级状态,目的:
- 减少 main.py 体积与职责
- 让 unittest 可在无 AstrBot runtime 下单独引用

包含:
- ``DEFAULT_CONFIG`` — 插件默认配置
- ``_stats`` / ``record()`` — 工具调用统计(供 dashboard 调试)
- ``MAX_GIT_DIFF_BYTES`` / ``_GIT_DIFF_ENCODING`` — git-diff 端点常量
- ``make_git_diff_empty_envelope()`` — git-diff 失败路径响应骨架
- ``make_git_worktrees_empty_envelope()`` — git-worktrees 失败路径响应骨架
- ``_PROJECT_GUIDANCE_MARKER`` / ``_PROJECT_CODEGRAPH_GUIDANCE`` — /project 指引
- ``_FILE_REMOVE_GUIDANCE_MARKER`` / ``_FILE_REMOVE_GUIDANCE`` — file_remove 优先使用指引

Author: elecvoid243
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from ._helpers import detect_console_encoding

# ── 默认配置 ─────────────────────────────────────────────


DEFAULT_CONFIG: dict[str, Any] = {
    "es_path": "",  # Everything es.exe 路径(Windows);Linux/macOS 留空
    "git_path": "",  # git 可执行文件绝对路径;留空走系统 PATH
    "cppcheck_path": "",  # cppcheck.exe 路径(Windows/Linux/macOS);C/C++ 检查时优先于 cpplint
    "cppcheck_shortcircuit": "error",  # cppcheck 短路策略:error/warning/never(仅 auto 模式生效)
    "codegraph_enabled": True,  # 是否启用 codegraph MCP 集成
    "codegraph_install_dir": "",  # codegraph 安装目录(含 node.exe);留空则不启动 MCP
    "codegraph_project": "",  # codegraph daemon 默认操作的工程根目录
    "agentsmd_enabled": True,  # 是否启用 AGENTS.md 管理
    # inta_shell 配置(v2.5)
    "inta_shell_max_sessions": 10,  # 最大并发会话数
    "inta_shell_session_timeout": 1800,  # 会话空闲超时(秒)
    "inta_shell_block_unsafe": True,  # 是否阻止危险命令
    "inta_shell_default_cwd": "",  # 默认工作目录
    "allowed_ids": "",  # 逗号分隔额外允许的用户 ID
    "enabled_tools": [],  # 启用的工具名列表;空 = 全部禁用(安全默认)
    "file_remove_blacklist": [],  # file_remove 用户自定义黑名单:绝对路径前缀列表
}


# ── 工具调用统计(供 dashboard 调试)─────────────────────
#
# 设计:_stats 是模块级 dict,供所有工具的 record() 共享写入。
# 暴露给外部(非下划线惯例)以便后续 /spcode/stats 等端点直接读。
# key = 工具名,value = {"count": int, "last": float}


_stats: dict[str, dict[str, float]] = defaultdict(
    lambda: {"count": 0, "last": 0.0}
)


def record(name: str) -> None:
    """记录一次工具调用:增加计数 + 更新 last 时间戳。

    异常防御:任何写入异常都应被 swallow,绝不能污染 LLM 调用路径。
    历史上 main.py 的实现也带 try/except;这里保持同样语义。
    """
    try:
        entry = _stats[name]
        entry["count"] += 1
        entry["last"] = time.time()
    except Exception:
        pass


# ── git-diff 端点常量 ──────────────────────────────────
#
# 历史:这两个常量原本是 main.py 模块级,
# 启动期会调用一次 ``detect_console_encoding()`` 把结果缓存到 _GIT_DIFF_ENCODING。


MAX_GIT_DIFF_BYTES: int = 1 * 1024 * 1024  # 1 MB 硬上限
_GIT_DIFF_ENCODING: str = detect_console_encoding()  # 进程内一次探测


def make_git_diff_empty_envelope(
    umo: str | None,
    reason: str,
    directory: str | None = None,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造未载入 / 失败路径的响应骨架(对称于 handle_get_project_status 风格)。

    Args:
        umo: 统一消息源标识,None 时表示 dashboard 端未指定。
        reason: 失败原因 code(no_project_loaded / not_a_git_repo / git_unavailable / ...)。
        directory: 项目目录(若已知)。
        stderr: git 进程 stderr(若有)。会被序列化到 data.stderr,便于客户端排错。
        elapsed_ms: 处理耗时(毫秒)。

    Returns:
        dict: ``{"status": "ok", "data": {...}}`` 形状的响应骨架。
    """
    return {
        "status": "ok",
        "data": {
            "loaded": False,
            "directory": directory,
            "umo": umo,
            "reason": reason,
            "diff": None,
            "stat": None,
            "files_changed": [],
            "truncated": False,
            "truncated_at_bytes": 0,
            "max_bytes": MAX_GIT_DIFF_BYTES,
            "elapsed_ms": elapsed_ms,
            "stderr": stderr,
        },
    }


# ── /project 命令注入文本常量(v2.7) ─────────────────────
#
# 注入到 system_prompt 的防重复 marker。
# 与 _agentsmd_mod.INJECTION_MARKER 同等用途——同一请求多次走钩子时不重复追加。


_PROJECT_GUIDANCE_MARKER: str = "# Use Codegraph"


# /project load 后注入到 system_prompt 末尾的指引。
# 设计目标:让 LLM 优先使用 codegraph 工具组而非 astrbot_file_grep_tool,
# 提升代码搜索/分析的效率与准确性(已建好语义索引,无需 grep 全文本)。
_PROJECT_CODEGRAPH_GUIDANCE: str = f"""
{_PROJECT_GUIDANCE_MARKER}
A codegraph project is loaded. When dealing with the code for this project:
- Priority use codegraph_* tool (e.g. codegraph_explore) for code lookup, call chain analysis, and symbol localization.
- When the codegraph_* tool is unavailable or when viewing non code index files (e.g. configurations, logs), return to a generic lookup tool like `astrbot_file_grep_tool`
"""


# astrbot_file_remove_tool 启用时注入到 system_prompt 末尾的指引。
# 设计目标:让 LLM 优先使用 file_remove 工具(自带路径安全 + 回收站)而非绕过。
# 无 session state 依赖——只靠 self._tool_names 作为 gate。
_FILE_REMOVE_GUIDANCE_MARKER: str = "# Delete only if when necessary"

_FILE_REMOVE_GUIDANCE: str = f"""
{_FILE_REMOVE_GUIDANCE_MARKER}
Priority use 'astrbot_file_remove' for file or directory deletion. DO NOT use shell commands (such as' rm '/' del ') or Python calls to bypass it.
"""


def make_git_worktrees_empty_envelope(
    *,
    umo: str | None = None,
    directory: str | None = None,
    reason: str,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造 ``/spcode/git-worktrees`` 未载入 / 失败路径的响应骨架。

    Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.2
    """
    return {
        "status": "ok",
        "data": {
            "loaded": False,
            "directory": directory,
            "umo": umo,
            "worktrees": None,
            "reason": reason,
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
        },
    }
