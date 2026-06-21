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
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from .tools._helpers import (  # noqa: E401
    run_cmd,
    run_sync,
    unwrap,
    err_json,
    detect_console_encoding,
    _parse_git_worktree_porcelain,
    _validate_worktree_param,
)  # run_cmd: 供 /spcode/git-diff & git-worktrees handler 使用
from astrbot.api.web import JSONResponse  # HTTP 缓存响应(v3.3)
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
import datetime as _datetime

# 让 main.py 可以动态添加 MethodType
from collections import defaultdict

# git-diff 端点专用常量(也用于 __init__ 启动期探测)
MAX_GIT_DIFF_BYTES = 1 * 1024 * 1024  # 1 MB 硬上限
_GIT_DIFF_ENCODING = detect_console_encoding()  # 进程内一次探测

# ── git-diff scope 映射(v3.1) ──
# 单一真相源:scope 名 → 传给 `git diff [args]` 的位置参数列表。
# 三种 scope 与 git 原生三段 diff 语义一一对应:
#   "unstaged" (默认) → 工作区 vs index(编辑器改动;v1 行为)
#   "staged"         → index vs HEAD(`git add` 后的内容)
#   "all"            → 工作区 vs HEAD(staged + unstaged 之和)
_VALID_SCOPES: frozenset[str] = frozenset({"unstaged", "staged", "all"})
_SCOPE_GIT_ARGS: dict[str, list[str]] = {
    "unstaged": [],
    "staged": ["--cached"],
    "all": ["HEAD"],
}
DEFAULT_SCOPE: str = "unstaged"  # 与 v1 行为严格兼容

# ── file-browser 端点常量(v3.2) ──
# 单一真相源,handler 与测试共享;详情见
# docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md §5
FILE_BROWSER_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB 文件大小硬上限
FILE_BROWSER_MAX_ENTRIES: int = 1000  # 单层目录最大返回项数
FILE_BROWSER_SNIFF_BYTES: int = 8192  # 8 KB 二进制探测窗口
_TYPE_ORDER: dict[str, int] = {"directory": 0, "file": 1, "symlink": 2}


def _parse_diff_status_map(diff_output: str) -> dict[str, str]:
    """从 ``git diff`` 原始输出解析 ``{path: status}`` 映射。

    性能优化 (v3.3,2026-06-21,git-diff 4 合 1 配套):
    替代原来的 ``git diff --name-status`` 单独调用。仅检查每个文件
    header 部分(第一个 hunk 之前的 ~10 行),跳过 hunk body 加速。

    支持的状态:
    - M (modify,默认)
    - A (add,``new file mode``)
    - D (delete,``deleted file mode``)
    - R (rename,``rename from/to`` 对;使用新路径作为 key)
    - C (copy,``copy from/to`` 对;使用新路径作为 key)

    已知限制(无测试覆盖,与 v1 行为一致):
    - 纯 mode change(``old mode`` / ``new mode``,无 content 改动)→ "M"
    - submodule 改动 → "M"
    - ``T``(type change)目前归为 "M",旧实现也是 "T" → 略退
    """
    status_by_path: dict[str, str] = {}
    current_path: str | None = None
    current_status: str = "M"
    in_hunk = False

    for line in diff_output.splitlines():
        if line.startswith("diff --git "):
            if current_path is not None:
                status_by_path[current_path] = current_status
            parts = line.split(" ")
            b_path = parts[3] if len(parts) >= 4 else ""
            current_path = b_path[2:] if b_path.startswith("b/") else b_path
            current_status = "M"
            in_hunk = False
            continue

        if current_path is None or in_hunk:
            continue

        if line.startswith("new file mode"):
            current_status = "A"
        elif line.startswith("deleted file mode"):
            current_status = "D"
        elif line.startswith("rename from "):
            current_status = "R"
        elif line.startswith("rename to "):
            current_path = line[len("rename to "):]
        elif line.startswith("copy from "):
            current_status = "C"
        elif line.startswith("copy to "):
            current_path = line[len("copy to "):]
        elif line.startswith("@@"):
            in_hunk = True
        # index / similarity / file header (--- / +++) / Binary files 等行:忽略

    if current_path is not None:
        status_by_path[current_path] = current_status

    return status_by_path


def _parse_numstat_counts(numstat_output: str) -> dict[str, tuple[int, int]]:
    """从 ``git diff --numstat`` 解析 ``{path: (add, del)}`` 映射。

    性能优化 (v3.3,2026-06-21,git-diff 4 合 1 配套):
    替代原 ``_parse_files_changed`` 中的 numstat 解析块,并修复了 rename/copy
    的 path 解析 bug(旧版用 ``old_path => new_path`` 整个串作 key,导致
    counts_by_path.get(new_path) 永远 miss,rename 文件的 additions/deletions
    错误地为 0)。此处用 new_path 作 key,与 _parse_diff_status_map 对齐。

    Binary 文件的 numstat 是 ``-`` ``-`` → ``(0, 0)``。
    """
    counts: dict[str, tuple[int, int]] = {}
    for line in numstat_output.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_raw, del_raw, path = parts[0], parts[1], parts[2]
        # rename/copy: numstat 输出 "<old> => <new>",取 new 部分作 key
        if " => " in path:
            path = path.split(" => ", 1)[1]
        add = 0 if add_raw == "-" else int(add_raw)
        delete = 0 if del_raw == "-" else int(del_raw)
        counts[path] = (add, delete)
    return counts


def _build_stat_text(files_changed: list[dict]) -> str:
    """根据 files_changed 构造 ``git --stat`` 风格的统计文本。

    性能优化 (v3.3,2026-06-21,git-diff 4 合 1 配套):
    替代原来的 ``git diff --stat`` 单独调用。不追求与 git 输出字节级一致
    (无测试断言比对),但格式相似(Dashboard 预览用)。
    """
    if not files_changed:
        return ""

    max_path_len = max(len(f["path"]) for f in files_changed)
    max_change_len = max(
        len(str(f["additions"] + f["deletions"])) for f in files_changed
    )
    lines: list[str] = []
    total_add = 0
    total_del = 0
    for f in files_changed:
        total = f["additions"] + f["deletions"]
        add = f["additions"]
        delete = f["deletions"]
        # git 用 50 字符的 +/- bar;空 bar 用 | 占位
        bar = "+" * min(add, 50) + "-" * min(delete, 50)
        if not bar:
            bar = "|"
        lines.append(
            f" {f['path']:<{max_path_len}} | {total:>{max_change_len}} {bar}"
        )
        total_add += add
        total_del += delete

    if len(files_changed) == 1:
        summary = (
            f" 1 file changed, {total_add} insertions(+), {total_del} deletions(-)"
        )
    else:
        summary = (
            f" {len(files_changed)} files changed, "
            f"{total_add} insertions(+), {total_del} deletions(-)"
        )

    return "\n".join(lines) + "\n" + summary


async def _compute_diff_etag(git_bin: str, directory: str) -> str:
    """为 git-diff 端点计算弱 ETag。

    组合三件信号:
    - ``git rev-parse HEAD`` SHA(commit 变 → diff 变)
    - worktree 根目录 mtime(新增/删除文件变)
    - ``.git/index`` mtime(``git add`` / ``git update-index`` 变)

    Why 不用 ``git diff-files --quiet`` 探针(50-200ms):绝大多数 polling 是无
    操作场景,HEAD/index 不变 → 直接 304;有编辑的漏检窗口 = 1 个 poll 周期
    (5-10s),由下一次 poll 自然纠正。接受这个 staleness 以换 ETag 计算的
    12ms 总开销(``rev-parse`` ~10ms + 2 stat ~2ms)。

    v3.3 (2026-06-21): 引入支持 HTTP 缓存。
    """
    head_sha = "no-head"
    try:
        head_result = await run_sync(
            run_cmd,
            [git_bin, "-C", directory, "rev-parse", "HEAD"],
            encoding="utf-8",
            timeout=5,
        )
        if head_result.get("ok") and head_result.get("stdout"):
            head_sha = head_result["stdout"]
    except Exception:
        pass

    wt_mtime = 0
    try:
        wt_mtime = int(Path(directory).stat().st_mtime)
    except OSError:
        pass

    idx_mtime = 0
    try:
        idx_mtime = int((Path(directory) / ".git" / "index").stat().st_mtime)
    except OSError:
        pass

    return f'W/"{head_sha}-{wt_mtime}-{idx_mtime}"'


def _compute_file_etag(path: Path) -> str | None:
    """为单个文件计算弱 ETag(mtime_ns + size),失败返回 ``None``。"""
    try:
        st = path.lstat()
    except OSError:
        return None
    return f'W/"{st.st_mtime_ns}-{st.st_size}"'


def _common_cache_headers(etag: str | None) -> dict[str, str]:
    """构造 HTTP 缓存响应头。

    Args:
        etag: 弱 ETag 字符串(如 ``W/"abc-123"``);为 None 时不带 ETag。

    Returns:
        ``{ETag, Cache-Control, Vary}`` dict(供 JSONResponse 注入)。
    """
    headers: dict[str, str] = {
        "Cache-Control": "private, must-revalidate",
        "Vary": "Cookie",
    }
    if etag:
        headers["ETag"] = etag
    return headers


def _get_if_none_match() -> str:
    """从当前 web request 取 ``If-None-Match`` 头,失败返回空串。"""
    try:
        from astrbot.api import web

        return web.request.headers.get("If-None-Match", "") or ""
    except Exception:
        return ""


def _make_304_response(headers: dict[str, str]) -> _JSONResponseCompat:
    """构造 304 Not Modified 响应(空 body + 缓存头)。"""
    return _JSONResponseCompat({}, status_code=304, headers=headers)


class _JSONResponseCompat(JSONResponse):
    """JSONResponse 子类,补回 dict-like 访问能力。

    Why: AstrBot 框架的 ``_response_from_result`` 看到 ``Response`` 实例会原样
    透传(head/status_code 都被框架消化);但项目里的 web API 单元测试大量使用
    ``result["data"]`` 这种 dict 取值写法。继承 ``JSONResponse`` 既能享受
    framework 的 status_code/headers 注入,又保持现有测试无需重写。

    v3.3 (2026-06-21): 引入以支持 HTTP 缓存 (ETag/304 + Cache-Control)。
    """

    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content, status_code=status_code, headers=headers)
        self._content = content

    def __getitem__(self, key: str) -> Any:
        return self._content[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._content.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self._content

    def __iter__(self) -> Any:
        return iter(self._content)


def _build_error_response(path: str | Path, reason: str) -> dict:
    """构造错误响应(3 字段:type=null, path, reason)。

    详见 docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md §6.4。
    """
    return {"type": None, "path": str(path), "reason": reason}


def _is_binary(path: Path, sniff_bytes: int = FILE_BROWSER_SNIFF_BYTES) -> bool:
    """扫前 sniff_bytes 字节,含 NUL 字节 → 二进制。

    Git 自身也用相同启发式(`xd0` 工具的 ``is_binary``);见 spec §7.1。
    """
    try:
        with path.open("rb") as f:
            chunk = f.read(sniff_bytes)
    except OSError:
        return True  # 不可读 → 当 binary 处理
    return b"\x00" in chunk


def _classify_entry(p: Path) -> str:
    """不跟随 symlink 的类型分类。

    返回值: ``directory`` / ``file`` / ``symlink`` / ``special`` (FIFO/socket/device)。

    WHY: Python 3.12 build 缺 ``Path.is_dir(follow_symlinks=)`` 参数(仅在
    Python ≥ 3.13 才正式支持);改用 ``os.stat`` + ``stat.S_ISDIR`` 走底层
    ``stat.S_IFMT`` 判定,与 spec §7.3 "用 lstat 不跟随" 等价。
    """
    if p.is_symlink():
        return "symlink"
    try:
        st = os.stat(p, follow_symlinks=False)
    except OSError:
        return "special"
    mode = stat.S_IFMT(st.st_mode)
    if mode == stat.S_IFDIR:
        return "directory"
    if mode == stat.S_IFREG:
        return "file"
    return "special"


def _safe_lstat_mtime(path: Path) -> float | None:
    """安全读 mtime: lstat 失败 / st_mtime 访问失败 → None。

    不抛异常;与 spec §6 "文件 mtime 失败" 边界一致。
    """
    try:
        st = path.lstat()
    except OSError:
        return None
    try:
        return float(st.st_mtime)
    except OSError:
        return None


def _make_entry(p: Path) -> dict:
    """构造单个 entry dict(目录列表项)。

    不跟随 symlink (lstat)。
    性能优化 (v3.3,2026-06-21): 之前对每个 entry 调 5-7 次 stat / follow syscalls
    (lstat + is_symlink + os.stat(follow=False) + lstat + is_symlink + is_symlink + readlink + exists);
    现版 1 次 lstat 拿全部 mode/size/mtime 信息,symlink 额外 1 次 readlink + 1 次
    target_exists 探测。1000 项目录从 ~5000-7000 syscalls 降到 ~1000-2000。
    """
    try:
        st = p.lstat()  # 唯一一次主 syscall
    except OSError:
        raise
    mode = st.st_mode
    is_sym = stat.S_ISLNK(mode)
    if is_sym:
        etype = "symlink"
    elif stat.S_ISDIR(mode):
        etype = "directory"
    elif stat.S_ISREG(mode):
        etype = "file"
    else:
        etype = "special"
    # mtime: 极个别 FS(网络盘 / 特殊 FUSE)在 lstat 成功但 st_mtime
    # 访问时仍会抛 OSError,沿用旧 _safe_lstat_mtime 的兜底语义。
    try:
        mtime: float | None = float(st.st_mtime)
    except (OSError, ValueError):
        mtime = None
    entry: dict = {
        "path": str(p),
        "name": p.name,
        "type": etype,
        "size": st.st_size,
        "mtime": mtime,
        "is_symlink": is_sym,
    }
    if is_sym:
        entry["target"] = os.readlink(p)
        # target_exists: 相对 symlink 应相对 symlink 父目录解析
        # (旧实现用 Path(target).exists() 实际是相对 CWD,有 bug;此处修正)
        target = entry["target"]
        if not os.path.isabs(target):
            target = os.path.join(str(p.parent), target)
        try:
            entry["target_exists"] = os.path.exists(target)
        except OSError:
            entry["target_exists"] = False
    return entry


def _build_file_response(path: Path) -> dict:
    """构造文件响应。三种 reason 路径(成功 / file_too_large / binary_file)。

    ``read_text`` 抛 OSError / UnicodeDecodeError 统一视为 binary
    (spec §7.5 注释说明)。
    """
    try:
        st = path.lstat()
    except OSError as exc:
        return _build_error_response(path, _classify_oserror(exc))
    base: dict = {
        "type": "file",
        "path": str(path),
        "name": path.name,
        "size": st.st_size,
        "mtime": _safe_lstat_mtime(path),
        "max_bytes": FILE_BROWSER_MAX_BYTES,
    }
    if st.st_size > FILE_BROWSER_MAX_BYTES:
        return {
            **base,
            "encoding": None,
            "is_binary": False,
            "content": None,
            "reason": "file_too_large",
        }
    if _is_binary(path):
        return {
            **base,
            "encoding": None,
            "is_binary": True,
            "content": None,
            "reason": "binary_file",
        }
    # 文本正常;read_text 抛异常统一视为 binary
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {
            **base,
            "encoding": None,
            "is_binary": None,
            "content": None,
            "reason": "binary_file",
        }
    return {
        **base,
        "encoding": "utf-8",
        "is_binary": False,
        "content": content,
        "reason": None,
    }


def _classify_oserror(exc: OSError) -> str:
    """把 OSError 映射到 reason 字符串(EACCES/PermissionError → permission_denied)。

    其他 OSError 视为 path_not_found(资源不可用)。
    """
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if exc.errno == 13:  # EACCES
        return "permission_denied"
    return "path_not_found"


def _build_directory_response(path: Path) -> dict:
    """构造目录响应。

    - 过滤以 ``.`` 开头的隐藏项
    - 按 ``(_TYPE_ORDER[type], name)`` 排序:directory → file → symlink
    - 超过 ``FILE_BROWSER_MAX_ENTRIES`` 截断
    """
    raw_entries: list[dict] = []
    for child in path.iterdir():
        if child.name.startswith("."):
            continue
        try:
            raw_entries.append(_make_entry(child))
        except OSError:
            continue  # lstat 失败(罕见;权限/竞态)— 跳过
    raw_entries.sort(key=lambda e: (_TYPE_ORDER[e["type"]], e["name"]))
    truncated = len(raw_entries) > FILE_BROWSER_MAX_ENTRIES
    entries = raw_entries[:FILE_BROWSER_MAX_ENTRIES]
    return {
        "type": "directory",
        "path": str(path),
        "entry_count": len(entries),
        "truncated": truncated,
        "max_entries": FILE_BROWSER_MAX_ENTRIES,
        "entries": entries,
        "reason": "directory_listing_truncated" if truncated else None,
    }


def _build_symlink_response(path: Path) -> dict:
    """构造顶层 symlink 响应。

    symlink → 文件: target_exists=True(若 target 存在)
    悬空 symlink: target_exists=False
    """
    st = path.lstat()
    target = os.readlink(path)
    return {
        "type": "symlink",
        "path": str(path),
        "name": path.name,
        "size": st.st_size,
        "mtime": _safe_lstat_mtime(path),
        "is_symlink": True,
        "target": target,
        "target_exists": Path(target).exists(),
        "reason": None,
    }


def _make_git_diff_empty_envelope(
    umo: str | None,
    reason: str,
    directory: str | None = None,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造未载入 / 失败路径的响应骨架(对称于 handle_get_project_status 风格)。"""
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
        },
    }


def _make_git_worktrees_empty_envelope(
    *,
    umo: str | None = None,
    directory: str | None = None,
    reason: str,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造 /spcode/git-worktrees 未载入 / 失败路径的响应骨架。

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
    "git_path": "",  # git 可执行文件绝对路径;留空走系统 PATH
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


# ── /project 命令组(v2.7) ─────────────────────


class _ProjectLoadAbort(BaseException):
    """私有信号异常 — ``_project_load_step`` 用以中止 ``_project_load_impl``。

    为什么用 ``BaseException`` 而非 ``Exception``?
        子方法(``_agentsmd_init`` 等)和 helpers 内部有大量
        ``except Exception`` 兜底(见 ``_codegraph_set_project`` 等)。
        用 ``BaseException`` 可避免该异常被这些 ``except`` 误吞,
        确保中止信号一定能传到 :meth:`_project_load_impl` 顶层。

    捕获方: :meth:`_project_load_impl` 的 ``try/except _ProjectLoadAbort``
    块,捕获后 ``return`` 即可。
    """

    def __init__(self, step_label: str) -> None:
        self.step_label = step_label
        super().__init__(step_label)


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


# astrbot_file_remove_tool 启用时注入到 system_prompt 末尾的指引。
# 设计目标:让 LLM 优先使用 file_remove 工具(自带路径安全 + 回收站)而非绕过。
# 无 session state 依赖——只靠 self._tool_names 作为 gate。
_FILE_REMOVE_GUIDANCE_MARKER = "# Delete only if when necessary"

_FILE_REMOVE_GUIDANCE = f"""
{_FILE_REMOVE_GUIDANCE_MARKER}
Priority use 'astrbot_file_remove' for file or directory deletion. DO NOT use shell commands (such as' rm '/' del ') or Python calls to bypass it.
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
    name: str = "astrbot_file_remove"
    description: str = (
        "Delete an entire file or directory. Before deleting, it is necessary to ask the user. "
        "If delete fragments instead of the entire file, use `astrbot_file_edit_tool`. "
        "Deleting a DIRECTORY requires parameter 'confirm=true'. "
        "If a directory contains more than max_items files, the call returns a "
        "proposal asking for batch confirmation INSTEAD of deleting — read the "
        "proposal/options, then retry with confirm=true. "
        "Single files are deleted without confirm. "
        "Items are sent to the system recycle bin (recoverable), not permanently deleted."
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
    name: str = "astrbot_file_compare"
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

    封装 umo 提取、TodoStore 初始化、async dispatch。
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
        """提取 umo,创建 store,返回 (store, umo) 元组。

        失败时返回 dict 错误响应(供 _dispatch 透传给 unwrap 包成 JSON 字符串)。

        v2.11: 隔离键从 sender_key (platform:sender_id) 切到 umo (unified_msg_origin)。
        """
        try:
            event = context.context.event
        except AttributeError:
            return {"ok": False, "error": "无 event 上下文"}
        umo = _todo_list_mod.extract_umo(event)
        data_dir = str(StarTools.get_data_dir())
        todos_dir = os.path.join(data_dir, "todos")
        store = _todo_list_mod.TodoStore(todos_dir)
        return store, umo

    async def _dispatch(self, context, fn, *args, **kwargs) -> str:
        """通用 dispatch: 记录调用 + setup + 异步执行 fn(store, umo, *args, **kwargs)。

        返回: 永远为 JSON 字符串(与 unwrap() 风格一致)。
        - setup 失败 → 直接 unwrap(setup_dict) 透传 proposal 字段
        - 业务异常 → err_json 包装
        """
        _record(self.name)
        try:
            setup = self._setup(context)
            if isinstance(setup, dict):  # 错误响应,经 unwrap 包成 JSON 字符串
                return unwrap(setup)
            store, umo = setup
            result = await run_sync(lambda: fn(store, umo, *args, **kwargs))
            return unwrap(result)
        except Exception as e:
            return err_json(f"{self.name} 失败: {e}")


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
                        "OVERWRITE: pass a non-empty string, e.g. \"blocked on review\". "
                        "CLEAR: pass the empty string \"\". "
                        "KEEP: OMIT this key entirely from the JSON object — "
                        "do NOT write null, \"\", or any placeholder to express 'keep'; "
                        "leaving the key out means 'leave the existing notes unchanged'."
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
    """Delete the entire todo list (remove file) for current umo (session channel)."""

    name: str = "todo_clear"
    description: str = (
        "Delete the entire todo list for current umo (session channel) (removes the file). "
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
    ("spcode 开发工具箱 — 提供实用开发工具, 部分实现基于Irmia DevKit插件。"),
    "2.9.0",
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

        # ── git 可用性探测 ──
        # 启动期一次同步探测,缺失不阻塞插件加载(端点注册照常);
        # 失败仅记 WARNING,用户首次调用 /spcode/git-diff 时会得到
        # reason="git_unavailable" 的结构化响应。
        try:
            import subprocess as _sp

            _git_probe = _sp.run(
                [self._git_binary(), "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding=_GIT_DIFF_ENCODING,
                errors="replace",
            )
            if _git_probe.returncode == 0:
                _first_line = (
                    (_git_probe.stdout or "").splitlines()[0]
                    if _git_probe.stdout
                    else "unknown"
                )
                logger.info(f"[git-diff] detected: {_first_line}")
            else:
                logger.warning(
                    f"[git-diff] git 探测失败(returncode={_git_probe.returncode}, "
                    f"stderr={(_git_probe.stderr or '').strip()!r})"
                    " — /spcode/git-diff 端点将不可用。"
                    " 请安装 git 或在插件配置中设置 git_path。"
                )
        except FileNotFoundError:
            logger.warning(
                "[git-diff] git 未安装或不在 PATH 中"
                " — /spcode/git-diff 端点将不可用。"
                " 请安装 git 或在插件配置中设置 git_path。"
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"[git-diff] 启动期探测异常: {exc!s}")

    async def initialize(self) -> None:
        """插件激活（AstrBot 框架在 __init__ 后调用）。

        构造 inta_shell 组件单例。codegraph MCP 由 __init__ 内异步任务管理，
        不在这里重复处理。
        """
        # Register dashboard-facing web API so the UI can query the currently
        # loaded project without polluting chat history with a /project status
        # command. The endpoint accepts an optional ``umo`` query param; when
        # the dashboard does not know the umo it can omit it and receive the
        # most-recently-loaded project (single-user dashboards are fine with
        # this fallback).
        try:
            self.context.register_web_api(
                route="/spcode/project-status",
                view_handler=self.handle_get_project_status,
                methods=["GET"],
                desc="获取 spcode 当前会话已加载的项目信息（供 dashboard 调用）",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"注册 spcode project-status web API 失败: {exc!s}")

        # 与 project-status 平行的端点：返回当前已载入项目的未暂存 git diff，
        # 供本地 dashboard 显示编辑器中尚未提交的工作区改动。
        try:
            self.context.register_web_api(
                route="/spcode/git-diff",
                view_handler=self.handle_get_git_diff,
                methods=["GET"],
                desc="获取 spcode 当前会话已载入项目的未暂存 git diff（供 dashboard 调用）",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"注册 spcode git-diff web API 失败: {exc!s}")

        # 注册 /spcode/git-worktrees(v3.0 worktree 切换器):
        # dashboard 读取此端点填充 GitDiffSidebar 的 worktree 下拉。
        try:
            self.context.register_web_api(
                route="/spcode/git-worktrees",
                view_handler=self.handle_get_git_worktrees,
                methods=["GET"],
                desc="获取 spcode 当前会话已载入项目的 git worktree 列表（供 dashboard 调用）",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"注册 spcode git-worktrees web API 失败: {exc!s}")

        # v3.2: 注册 /spcode/file-browser — 供 dashboard 文件浏览器调用。
        # 详见 docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md
        try:
            self.context.register_web_api(
                route="/spcode/file-browser",
                view_handler=self.handle_get_file_browser,
                methods=["GET"],
                desc="读取文件内容或列出单层目录（供 dashboard 文件浏览器调用）",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"注册 spcode file-browser web API 失败: {exc!s}")

        # v2.8.1: 注册 /spcode/plan-mode —
        # dashboard 读取此端点驱动 SpcodePlanModeChip 的状态显示与切换。
        # 与 /spcode/project-status 平行的查询端点,只读,POST 切换走聊天命令
        # (发送 /plan 或 /build,与 /project load 走同一条路径)。
        try:
            self.context.register_web_api(
                route="/spcode/plan-mode",
                view_handler=self.handle_get_plan_mode,
                methods=["GET"],
                desc="获取 spcode 当前会话的 plan/build 模式状态（供 dashboard 调用）",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"注册 spcode plan-mode web API 失败: {exc!s}")

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

    def _git_binary(self) -> str:
        """解析 git 二进制路径。

        优先级:配置 `git_path` > 默认 `"git"`(走系统 PATH)。
        空字符串、纯空白都会被规整为 `"git"`。

        Returns:
            git 可执行文件名或绝对路径。
        """
        raw = self._config.get("git_path") or "git"
        return raw.strip() or "git"

    # 注意:旧的 ``_parse_files_changed`` 方法(v3.3 之前)在 2026-06-21 的
    # git-diff 4 合 1 重构中已被 ``_parse_diff_status_map`` + ``_parse_numstat_counts``
    # 替代;status / numstat 现在分别从 ``git diff`` (raw) 和 ``git diff --numstat``
    # 输出解析,join 在 handler 处内联。保留此注释作为变更记录。

    # ── /codegraph 命令组(AstrBot 规范: 命令组和子命令必须是插件类方法)───
    @filter.command_group("codegraph", alias={"cg"})
    def codegraph(self):
        """codegraph 项目管理指令组。"""
        pass

    # ── /project 命令组(v2.7 组合 agentsmd + codegraph) ───────────

    @filter.command_group("project")
    def project(self, event, sub_command: str = "", *args):
        """``/project`` 指令组的统一入口。

        装饰器模式下,本方法被 ``@filter.command_group`` 替换为
        ``RegisteringCommandable``,实际不会执行;真正的分发由
        :meth:`_project_router` 处理,以便单元测试可以直接调用。

        Args:
            event: AstrBot 事件对象(由框架注入)。
            sub_command: ``/project`` 后面的第一个子命令(load / unload / status / ...)。
            *args: 子命令对应的额外参数。
        """
        return None

    async def _project_router(self, event, sub_command: str, *args):
        """Implementation of the ``/project`` command group.

        Dispatches ``sub_command`` to the matching ``_project_*_impl`` helper
        and yields its messages. Unknown sub-commands yield a single error
        message.

        Args:
            event: AstrBot 事件对象。
            sub_command: 子命令字符串(load / unload / status / ...)。
            *args: 子命令对应的额外参数。

        Yields:
            Plain text messages for the user.
        """
        sub = (sub_command or "").strip().lower()
        if sub == "load":
            if not args:
                yield event.plain_result("❌ /project load 需要 <directory> 参数。")
                return
            async for msg in self._project_load_impl(event, args[0]):
                yield msg
            return
        if sub == "unload":
            async for msg in self._project_unload_impl(event):
                yield msg
            return
        if sub == "status":
            async for msg in self._project_status_impl(event):
                yield msg
            return
        # Unknown subcommand.
        yield event.plain_result(
            f"❌ 未知子命令: {sub_command!r}。支持: load / unload / status"
        )
        return

    @project.command("load")
    async def project_load(self, event, directory: str):
        """/project load <directory>

        一键加载项目到当前会话(委托给 ``_project_load_impl``)。

        Args:
            event: AstrBot 事件对象。
            directory: 用户提供的项目目录路径。
        """
        # Delegate to the testable helper so unit tests can exercise the
        # state-mutation path without depending on the @register decorator.
        async for msg in self._project_load_impl(event, directory):
            yield msg
        return

    async def _project_load_step(self, event, sub_gen, step_label: str):
        """Forward messages from a sub-step; abort on first "❌" message.

        用作 :meth:`_project_load_impl` 中所有 4 个子步骤的统一包装层:

        - **透传**: ``sub_gen`` 产出的每条消息都原样 ``yield`` 出去
        - **检测**: 任何以 ``"❌"`` 开头的消息视为失败
        - **中止**: 失败时 yield 一条总结消息,然后抛 :class:`_ProjectLoadAbort`
          终止整个 ``_project_load_impl`` 流程(stop at first error)

        为什么用异常而不是 flag?
            ``_project_load_impl`` 自己也是 async generator, ``return`` 只能
            终止自身;无法从 ``async for`` 循环内部跳出整个流。抛出一个私有
            异常是最干净的方式 — 父函数用 ``try/except _ProjectLoadAbort``
            接住后直接 ``return`` 即可。

        为什么"❌"而不是返回值?
            子方法(``_agentsmd_init`` / ``_agentsmd_load`` /
            ``_codegraph_init_or_uninit`` / ``_codegraph_set_project``)都
            遵循 "yield 错误消息 + return" 模式,从不抛异常。``❌`` 前缀是
            它们的统一约定(见 :data:`tools.agentsmd`)。``⚠️`` 不算失败 —
            ``_codegraph_init_or_uninit`` 在 "已初始化 → 自动 --force 重试"
            路径上以 ``⚠️`` 起头但最终可能成功。

        Args:
            event: AstrBot 事件对象(用于 yield abort 总结消息)。
            sub_gen: 子方法返回的 async generator,**不消耗**,只在这里转发。
            step_label: 本步的人类可读标签,如 ``"[1/3] AGENTS.md 加载"``。

        Yields:
            ``sub_gen`` 的全部消息 + (若失败) 一条 abort 总结消息。

        Raises:
            _ProjectLoadAbort: ``sub_gen`` 至少 yield 过一次以 ``"❌"``
                开头的消息。调用方应捕获并 ``return``。
        """
        failed = False
        async for msg in sub_gen:
            yield msg
            # msg 在生产里是 ``MessageEventResult``(由 ``event.plain_result()``
            # 返回),在单元测试里 mock 可能直接 yield 字符串。这里做"防御式"
            # 抽取,主路径(MER)走 ``.chain[0].text``;测试/mock 路径走 str。
            text: str | None = None
            if isinstance(msg, str):
                text = msg
            else:
                chain = getattr(msg, "chain", None)
                if chain:
                    first = chain[0]
                    text = getattr(first, "text", None)
            if isinstance(text, str) and text.startswith("❌"):
                failed = True
        if failed:
            yield event.plain_result(
                f"❌ {step_label} 失败,/project load 中止。"
                "请根据上方错误信息修复后,重试 /project load <directory>。"
            )
            raise _ProjectLoadAbort(step_label)

    async def _project_load_impl(self, event, directory: str):
        """Implementation of :meth:`project_load`.

        Performs the multi-step project load: feature-flag check, duplicate
        load guard, path safety, agentsmd init+load, codegraph init+set,
        records the load into ``self._loaded_projects[umo]``, and finally
        yields a summary message.

        任一子步骤失败(yield 任何以 ``❌`` 开头的消息)→ 立即中止整个 load:
        后续子方法不会被调用, ``_loaded_projects[umo]`` 不会被填充,
        也不会 yield "✅ 项目已加载"。``⚠️`` 不算失败
        (见 :meth:`_project_load_step`)。

        Args:
            event: AstrBot 事件对象。
            directory: 用户提供的项目目录路径。

        Yields:
            Plain text messages for the user。
        """
        umo = event.unified_msg_origin
        # 1. Feature flag 校验
        agentsmd_on = self._config.get("agentsmd_enabled", True)
        codegraph_on = self._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令需要先启用 codegraph 和 AGENTS.md 功能。\n"
                "请在插件配置中打开这两项后再试一次。"
            )
            return

        # 2. 重复 load 拦截(Q2=B 决策)
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

        # 4. 多步加载(任一子步骤失败 → 立即中止,不再登记 _loaded_projects)
        try:
            # 步骤 1/3: agentsmd(init 条件性 + load)
            agents_md_path = target / "AGENTS.md"
            if not agents_md_path.exists():
                yield event.plain_result(
                    f"⏳ [1/3] AGENTS.md 不存在,正在 init: {target}"
                )
                async for msg in self._project_load_step(
                    event,
                    self._agentsmd_init(event, str(target)),
                    "[1/3] AGENTS.md 初始化",
                ):
                    yield msg
            else:
                yield event.plain_result(
                    f"ℹ️ [1/3] AGENTS.md 已存在,跳过 init: {agents_md_path}"
                )
            yield event.plain_result(f"⏳ [1/3] 正在 load AGENTS.md: {target}")
            async for msg in self._project_load_step(
                event,
                self._agentsmd_load(event, str(target)),
                "[1/3] AGENTS.md 加载",
            ):
                yield msg

            # 步骤 2/3: codegraph init + set
            yield event.plain_result(f"⏳ [2/3] codegraph init: {target}")
            async for msg in self._project_load_step(
                event,
                self._codegraph_init_or_uninit(event, str(target), init=True),
                "[2/3] codegraph init",
            ):
                yield msg

            yield event.plain_result(f"⏳ [2/3] codegraph set: {target}")
            async for msg in self._project_load_step(
                event,
                self._codegraph_set_project(event, str(target)),
                "[2/3] codegraph set",
            ):
                yield msg
        except _ProjectLoadAbort:
            return

        # 5. 记录状态(仅在所有子步骤都成功后才登记)
        loaded_at_ts = _time.time()
        self._loaded_projects[umo] = {
            "directory": str(target),
            "loaded_at": loaded_at_ts,
        }

        yield event.plain_result(
            f"✅ 项目已加载: {target}\n"
            f"已自动进行如下步骤:\n"
            f"  - 设定工作目录\n"
            f"  - AGENTS.md 注入到 system_prompt\n"
            f"  - 载入 codegraph 索引\n"
            f"\n若要卸载，请执行`/project unload`\n"
        )

    @project.command("unload")
    async def project_unload(self, event):
        """/project unload(委托给 ``_project_unload_impl``)。

        Args:
            event: AstrBot 事件对象。
        """
        async for msg in self._project_unload_impl(event):
            yield msg
        return

    async def _project_unload_impl(self, event):
        """Implementation of :meth:`project_unload`.

        Unloads the current session's project: feature-flag check, no-op guard,
        agentsmd unload, codegraph set to default, and finally clears
        ``self._loaded_projects[umo]``.

        Args:
            event: AstrBot 事件对象。

        Yields:
            Plain text messages for the user.
        """
        # 1. Feature flag 校验
        agentsmd_on = self._config.get("agentsmd_enabled", True)
        codegraph_on = self._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            yield event.plain_result(
                "❌ /project 命令需要先启用 codegraph 和 AGENTS.md 功能。\n"
                "请在插件配置中打开这两项后再试一次。"
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
            f"  - codegraph 默认项目已重置\n"
        )

    @project.command("status")
    async def project_status(self, event):
        """/project status(委托给 ``_project_status_impl``)。

        Args:
            event: AstrBot 事件对象。
        """
        async for msg in self._project_status_impl(event):
            yield msg
        return

    async def _project_status_impl(self, event):
        """Implementation of :meth:`project_status`.

        Reads ``self._loaded_projects[umo]`` and yields a human-readable
        status for the chat response. The authoritative state used by the
        dashboard's spcode chip is exposed separately via
        :meth:`handle_get_project_status` (mounted at
        ``GET /spcode/project-status``) — that endpoint is the single
        source of truth for the dashboard; the chat response is plain
        text and intentionally does NOT carry any hidden marker.

        Args:
            event: AstrBot 事件对象。

        Yields:
            Plain text messages for the user.
        """
        umo = event.unified_msg_origin
        info = self._loaded_projects.get(umo)
        if info is None:
            yield event.plain_result("📂 当前会话未加载项目")
            return
        directory = info.get("directory", "")
        loaded_at_ts = info.get("loaded_at", 0)
        loaded_at_str = (
            _datetime.datetime.fromtimestamp(loaded_at_ts).strftime("%Y-%m-%d %H:%M:%S")
            if loaded_at_ts
            else "未知"
        )
        yield event.plain_result(
            f"📂 当前已加载项目\n路径: {directory}\n加载于: {loaded_at_str}\n"
        )

    def get_loaded_project(self, umo: str) -> dict | None:
        """Get the loaded project info for a given unified message origin.

        Args:
            umo: The unified message origin (session key).

        Returns:
            A dict with `directory` and `loaded_at` keys, or ``None`` if no
            project is currently loaded for the given ``umo``.
        """
        info = self._loaded_projects.get(umo)
        if info is None:
            return None
        # Return a shallow copy so callers cannot mutate internal state.
        return dict(info)

    async def handle_get_project_status(self) -> dict:
        """Web API handler for ``GET /spcode/project-status``.

        Query params:
            umo (optional): the unified message origin to query. When omitted
                the endpoint returns the most-recently-loaded project across
                all umos (the dashboard can use this fallback when it does not
                know its umo).

        Returns:
            A JSON envelope of the form::

                {
                    "status": "ok",
                    "data": {
                        "loaded": bool,
                        "directory": str | None,
                        "loaded_at": float | None,
                        "umo": str | None,
                        "all_loaded_count": int
                    }
                }
        """
        # Late import to avoid circular issues with the plugin module.
        from astrbot.api import web

        umo: str | None = None
        try:
            umo = web.request.query.get("umo") or None
        except Exception:
            umo = None

        if umo:
            info = self._loaded_projects.get(umo)
            if info is None:
                return {
                    "status": "ok",
                    "data": {
                        "loaded": False,
                        "directory": None,
                        "loaded_at": None,
                        "umo": umo,
                        "all_loaded_count": len(self._loaded_projects),
                    },
                }
            return {
                "status": "ok",
                "data": {
                    "loaded": True,
                    "directory": info.get("directory"),
                    "loaded_at": info.get("loaded_at"),
                    "umo": umo,
                    "all_loaded_count": len(self._loaded_projects),
                },
            }

        # No umo provided: return the most-recently-loaded project as a
        # convenience for callers that don't track umos (e.g. the dashboard).
        if not self._loaded_projects:
            return {
                "status": "ok",
                "data": {
                    "loaded": False,
                    "directory": None,
                    "loaded_at": None,
                    "umo": None,
                    "all_loaded_count": 0,
                },
            }
        # Pick the entry with the largest loaded_at (most recent).
        recent_umo, recent_info = max(
            self._loaded_projects.items(),
            key=lambda item: item[1].get("loaded_at", 0),
        )
        return {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": recent_info.get("directory"),
                "loaded_at": recent_info.get("loaded_at"),
                "umo": recent_umo,
                "all_loaded_count": len(self._loaded_projects),
            },
        }

    def _plan_mode_active(self, umo: str | None) -> bool:
        """Return whether the given umo is currently in plan mode.

        Centralizes the ``self._plan_mode`` lookup so the web API
        handler and any future internal callers agree on the
        "build == not plan" semantics (a key that is present but
        ``False`` is treated as **build** mode, the same way the
        ``@filter.on_llm_request`` hook does).

        Args:
            umo: Unified message origin to query, or ``None``.

        Returns:
            ``True`` if the umo is currently in plan mode,
            ``False`` otherwise (including unknown umo / ``None``).
        """
        if not umo:
            return False
        return bool(self._plan_mode.get(umo, False))

    def _plan_mode_active_count(self) -> int:
        """Count how many umos currently have plan mode active.

        The dashboard can use this for telemetry / "X sessions are in
        plan mode" indicators. Iterates the full dict because the
        per-umo map is expected to stay small (one entry per active
        session).
        """
        return sum(1 for active in self._plan_mode.values() if active)

    async def handle_get_plan_mode(self) -> dict:
        """Web API handler for ``GET /spcode/plan-mode``.

        Query params:
            umo (optional): the unified message origin to query. When
                omitted the endpoint returns ``active=false`` (the
                default build state) and the umo as ``None`` —
                callers that don't know their umo should pass it
                explicitly. Unlike ``/spcode/project-status`` we do
                **not** fall back to "most recent plan-mode session"
                because the plan/build switch is strictly per-session
                and silently inheriting another session's mode would
                be confusing.

        Returns:
            A JSON envelope of the form::

                {
                    "status": "ok",
                    "data": {
                        "active": bool,        # True == plan, False == build
                        "umo": str | None,
                        "all_active_count": int  # number of umos in plan mode
                    }
                }
        """
        # Late import to avoid circular issues with the plugin module.
        from astrbot.api import web

        umo: str | None = None
        try:
            umo = web.request.query.get("umo") or None
        except Exception:
            umo = None

        return {
            "status": "ok",
            "data": {
                "active": self._plan_mode_active(umo),
                "umo": umo,
                "all_active_count": self._plan_mode_active_count(),
            },
        }

    async def handle_get_git_worktrees(self) -> dict:
        """Web API handler for ``GET /spcode/git-worktrees``.

        返回当前已加载项目主目录的 git worktree 列表。
        第一个 worktree 是主 worktree（is_main: true）,
        其余是同仓库的链接 worktree。

        Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.2
        """
        import time as _time

        t0 = _time.time()

        def _elapsed() -> int:
            return int((_time.time() - t0) * 1000)

        # 1. umo 解析（与 git-diff handler 保持一致）
        umo: str | None = None
        try:
            from astrbot.api import web

            umo = web.request.query.get("umo") or None
        except Exception:
            umo = None

        # 2. 解析已加载项目
        if umo:
            info = self._loaded_projects.get(umo)
        else:
            if not self._loaded_projects:
                info = None
            else:
                # 退回到最近加载的项目
                most_recent_umo = max(
                    self._loaded_projects,
                    key=lambda k: self._loaded_projects[k].get("loaded_at", 0.0),
                )
                info = self._loaded_projects[most_recent_umo]

        if info is None:
            return _make_git_worktrees_empty_envelope(
                umo=umo, reason="no_project_loaded", elapsed_ms=_elapsed()
            )

        # If we fell back via the "most recent" path, promote the resolved umo.
        if umo is None:
            # Find the key that maps to `info` (small dict; linear scan is fine).
            for k, v in self._loaded_projects.items():
                if v is info:
                    umo = k
                    break

        directory = info.get("directory", "")

        # 3. Feature flag 校验
        if not (
            self._config.get("agentsmd_enabled", True)
            and self._config.get("codegraph_enabled", True)
        ):
            return _make_git_worktrees_empty_envelope(
                umo=umo,
                directory=directory,
                reason="feature_disabled",
                elapsed_ms=_elapsed(),
            )

        # 4. 目录存在性
        if not Path(directory).is_dir():
            return _make_git_worktrees_empty_envelope(
                umo=umo,
                directory=directory,
                reason="directory_missing",
                elapsed_ms=_elapsed(),
            )

        # 5. Git 仓库探测
        git_bin = self._git_binary()
        probe = run_cmd(
            [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
            encoding="utf-8",
        )
        if not probe["ok"]:
            combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
            if "not a git repository" in combined:
                return _make_git_worktrees_empty_envelope(
                    umo=umo,
                    directory=directory,
                    reason="not_a_git_repo",
                    elapsed_ms=_elapsed(),
                )
            if "未安装" in probe.get("error", ""):
                return _make_git_worktrees_empty_envelope(
                    umo=umo,
                    directory=directory,
                    reason="git_unavailable",
                    elapsed_ms=_elapsed(),
                )
            return _make_git_worktrees_empty_envelope(
                umo=umo,
                directory=directory,
                reason="git_error",
                stderr=probe.get("stderr", "") or probe.get("error", ""),
                elapsed_ms=_elapsed(),
            )

        # 6. 列出 worktrees
        list_result = run_cmd(
            [git_bin, "-C", directory, "worktree", "list", "--porcelain"],
            encoding="utf-8",
        )
        if not list_result["ok"]:
            return _make_git_worktrees_empty_envelope(
                umo=umo,
                directory=directory,
                reason="git_error",
                stderr=list_result.get("stderr", "") or list_result.get("error", ""),
                elapsed_ms=_elapsed(),
            )

        try:
            worktrees = _parse_git_worktree_porcelain(list_result["stdout"])
        except ValueError as e:
            logger.warning(f"[git-worktrees] porcelain parse failed: {e}")
            return _make_git_worktrees_empty_envelope(
                umo=umo,
                directory=directory,
                reason="git_error",
                stderr=str(e),
                elapsed_ms=_elapsed(),
            )

        elapsed = _elapsed()
        logger.debug(
            f"[git-worktrees] listed {len(worktrees)} worktrees in {elapsed}ms"
        )
        return {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": directory,
                "umo": umo,
                "worktrees": worktrees,
                "reason": None,
                "stderr": "",
                "elapsed_ms": elapsed,
            },
        }

    async def handle_get_git_diff(self) -> dict:
        """Web API handler for ``GET /spcode/git-diff``.

        Returns a JSON envelope of the form::

            {
                "status": "ok",
                "data": {
                    "loaded": bool,
                    "directory": str | None,
                    "umo": str | None,
                    "diff": str | None,
                    "stat": str | None,
                    "files_changed": [{"path", "status", "additions", "deletions"}],
                    "truncated": bool,
                    "truncated_at_bytes": int,
                    "max_bytes": int,
                    "elapsed_ms": int,
                    "reason": str | None
                }
            }

        The endpoint is only "valid" (loaded=True) when:
          - agentsmd_enabled AND codegraph_enabled are both true
          - A project is loaded for the requested (or most-recent) umo
          - The loaded directory still exists and is a git repository
        Otherwise returns ``loaded=False`` with a structured ``reason`` code.
        """
        t0 = _time.time()
        from astrbot.api import web

        umo: str | None = None
        worktree_param: str | None = None
        try:
            umo_raw = web.request.query.get("umo")
            umo = umo_raw if umo_raw else None
            # worktree: 保留空字符串以触发 6-step 校验(不要 ``or None``)
            wt_raw = web.request.query.get("worktree")
            worktree_param = wt_raw if wt_raw is not None else None
        except Exception:
            umo = None
            worktree_param = None

        # ── scope 解析(v3.1,Q1-OOR:在 feature flag 校验之前) ──
        # 默认 "unstaged" 与 v1 行为严格一致;非法值 → invalid_scope;
        # 大小写不敏感;空字符串视同缺省(避免误报)。
        scope: str = DEFAULT_SCOPE
        try:
            scope_raw = web.request.query.get("scope")
            if scope_raw is not None:
                candidate = scope_raw.strip().lower()
                if candidate and candidate not in _VALID_SCOPES:
                    # _elapsed() 尚未定义,inline 计算 elapsed_ms
                    return _make_git_diff_empty_envelope(
                        umo=None,
                        reason="invalid_scope",
                        elapsed_ms=int((_time.time() - t0) * 1000),
                    )
                scope = candidate or DEFAULT_SCOPE
        except Exception:
            scope = DEFAULT_SCOPE

        git_bin = self._git_binary()

        def _elapsed() -> int:
            return int((_time.time() - t0) * 1000)

        # 1. Feature flag 校验
        if not (
            self._config.get("agentsmd_enabled", True)
            and self._config.get("codegraph_enabled", True)
        ):
            return _make_git_diff_empty_envelope(
                umo=umo, reason="feature_disabled", elapsed_ms=_elapsed()
            )

        # 2. umo 解析与回退
        if umo:
            info = self._loaded_projects.get(umo)
        else:
            if not self._loaded_projects:
                info = None
            else:
                _, info = max(
                    self._loaded_projects.items(),
                    key=lambda kv: kv[1].get("loaded_at", 0),
                )

        if info is None:
            return _make_git_diff_empty_envelope(
                umo=umo, reason="no_project_loaded", elapsed_ms=_elapsed()
            )

        directory = info.get("directory", "")

        # 2.5 ?worktree= 参数校验(6-step defense;spec §2.3)
        # Spec: trim 后空 → 视同缺省(向后兼容 v1)。
        # 其他任何值都必须通过 6 步校验,否则返回 worktree_invalid。
        if worktree_param is not None and worktree_param.strip():
            validated_wt, wt_err = _validate_worktree_param(
                git_bin, directory, worktree_param
            )
            if wt_err is not None:
                logger.warning(
                    f"[git-diff] rejected ?worktree={worktree_param!r} "
                    f"(loaded={directory!r})"
                )
                return _make_git_diff_empty_envelope(
                    umo=umo,
                    reason=wt_err,
                    directory=directory,
                    elapsed_ms=_elapsed(),
                )
            directory = validated_wt  # use the validated worktree path

        # 3. 目录存在性
        if not Path(directory).is_dir():
            return _make_git_diff_empty_envelope(
                umo=umo,
                reason="directory_missing",
                directory=directory,
                elapsed_ms=_elapsed(),
            )

        # 3.5 HTTP 缓存(v3.3,2026-06-21): 在跑 git probe / diff 之前
        # 先算 ETag 并检查 If-None-Match,命中直接 304 短路。Dashboard 5-10s
        # polling 时大多数请求命中缓存(无 commit/无 git add/无 add-delete),
        # 跳过 3 个 git 调用(probe + diff + numstat)从 ~1-2s 降到 ~12ms。
        etag = await _compute_diff_etag(git_bin, directory)
        cache_headers = _common_cache_headers(etag)
        if _get_if_none_match() == etag:
            return _make_304_response(cache_headers)

        # 4. Git repository probe — git outputs UTF-8 on every platform,
        # so we always decode with utf-8 regardless of the Windows console
        # codepage (cp936/GBK on zh-CN systems would otherwise mojibake
        # non-ASCII characters in subsequent diff output).
        probe = await run_sync(
            run_cmd,
            [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
            encoding="utf-8",
        )
        if not probe["ok"]:
            combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
            if "not a git repository" in combined:
                return _make_git_diff_empty_envelope(
                    umo=umo,
                    reason="not_a_git_repo",
                    directory=directory,
                    elapsed_ms=_elapsed(),
                )
            if "未安装" in probe.get("error", ""):
                return _make_git_diff_empty_envelope(
                    umo=umo,
                    reason="git_unavailable",
                    directory=directory,
                    elapsed_ms=_elapsed(),
                )
            return _make_git_diff_empty_envelope(
                umo=umo,
                reason="git_error",
                directory=directory,
                stderr=probe.get("stderr", "") or probe.get("error", ""),
                elapsed_ms=_elapsed(),
            )

        # 5. Concurrently collect raw diff + numstat.
        # 性能优化 (v3.3,2026-06-21,git-diff 4 合 1):
        # 之前 4 路 asyncio.gather 同时跑 `git diff` / `--name-status` /
        # `--numstat` / `--stat`,在 Windows 上每次多 3 次 process spawn
        # (~150-300ms) 且 3 个进程互相竞争 work tree I/O。
        # 现版 2 路并发:慢的 `git diff` (full body) + 快的 `git diff --numstat`
        # (只 walk tree,无 file read)。status 从 raw diff Python 解析(stat
        # 也从 files_changed 构造)。实测 3 个 git 调用 + 3 个 thread pool
        # worker 减到 1+1;Windows 冷启动开销从 4× ~80ms 降到 2× ~80ms。
        # All git invocations output UTF-8, so we decode with utf-8 to avoid
        # mojibake on cp936 Windows hosts.
        git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]
        scope_args = _SCOPE_GIT_ARGS[scope]
        raw_result, numstat_result = await asyncio.gather(
            run_sync(run_cmd, git_prefix + ["diff"] + scope_args, encoding="utf-8"),
            run_sync(
                run_cmd, git_prefix + ["diff", "--numstat"] + scope_args, encoding="utf-8"
            ),
        )

        if not raw_result["ok"]:
            return _make_git_diff_empty_envelope(
                umo=umo,
                reason="git_error",
                directory=directory,
                stderr=raw_result.get("stderr", ""),
                elapsed_ms=_elapsed(),
            )

        # 6. 截断与解析(single raw diff → 3 views in Python)
        raw = raw_result["stdout"]
        truncated = len(raw) > MAX_GIT_DIFF_BYTES
        diff = raw[:MAX_GIT_DIFF_BYTES]
        status_by_path = _parse_diff_status_map(raw)
        counts_by_path = _parse_numstat_counts(numstat_result.get("stdout", ""))
        files_changed = [
            {
                "path": path,
                "status": status,
                "additions": counts_by_path.get(path, (0, 0))[0],
                "deletions": counts_by_path.get(path, (0, 0))[1],
            }
            for path, status in status_by_path.items()
        ]
        stat = _build_stat_text(files_changed)

        return _JSONResponseCompat(
            {
                "status": "ok",
                "data": {
                    "loaded": True,
                    "directory": directory,
                    "umo": umo,
                    "scope": scope,  # ← 新增 v3.1:回显 scope 解析结果
                    "diff": diff,
                    "stat": stat,
                    "files_changed": files_changed,
                    "truncated": truncated,
                    "truncated_at_bytes": MAX_GIT_DIFF_BYTES if truncated else 0,
                    "max_bytes": MAX_GIT_DIFF_BYTES,
                    "elapsed_ms": _elapsed(),
                    "reason": None,
                },
            },
            status_code=200,
            headers=cache_headers,
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
        #    v2.9: init 时要求目录下至少存在一个代码文件(对齐 /agentsmd init);
        #    uninit 故意跳过此检查(允许对空目录 uninit,语义上无害)。
        target = resolve_project_path(
            directory,
            init=init,
            user_blacklist=self._config.get("file_remove_blacklist") or [],
            require_code_files=init,
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

        # v2.9: 显式拒绝不存在的目录(取消自动 mkdir,避免对路径拼错场景静默创建空目录)
        if not target.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 不存在。\n请先创建该目录,或确认路径是否正确。"
            )
            return
        if not target.is_dir():
            yield event.plain_result(f"❌ `{directory}` 不是一个有效的目录。")
            return

        # v2.9: 要求目录下至少存在一个代码文件,避免对空目录或纯文档目录误用。
        # AGENTS.md 是给"在此仓库工作的编程代理"用的规范,没有代码的项目无意义。
        if not _agentsmd_mod.has_code_files(target):
            supported = ", ".join(
                f".{ext}" for ext in sorted(_agentsmd_mod.CODE_FILE_EXTENSIONS)
            )
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到代码文件。\n"
                f"AGENTS.md 仅用于代码项目,支持的后缀: {supported}\n"
                "请确认目录是否正确,或选择包含源代码的目录。"
            )
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

        # v2.9: 显式校验目标目录存在且是目录,避免把"目录不存在"和
        # "目录存在但缺 AGENTS.md"混为同一个错误消息。
        if not target.exists():
            yield event.plain_result(
                f"❌ 目录 `{directory}` 不存在。\n请先创建该目录,或确认路径是否正确。"
            )
            return
        if not target.is_dir():
            yield event.plain_result(f"❌ `{directory}` 不是一个目录。")
            return

        # v2.9: 代码文件检测(与 init 对齐)
        if not _agentsmd_mod.has_code_files(target):
            supported = ", ".join(
                f".{ext}" for ext in sorted(_agentsmd_mod.CODE_FILE_EXTENSIONS)
            )
            yield event.plain_result(
                f"❌ 目录 `{directory}` 下未找到代码文件。\n"
                f"AGENTS.md 仅用于代码项目,支持的后缀: {supported}\n"
                "请确认目录是否正确,或选择包含源代码的目录。"
            )
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
    async def _file_remove_inject_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """astrbot_file_remove 启用时,把"优先使用 file_remove"指引注入到 system_prompt 末尾。

        触发条件(全部满足):
        - `astrbot_file_remove` 在 self._tool_names 中(说明用户已启用)
        - 同 req.system_prompt 中尚未包含 marker(防重复注入)

        设计要点(对照 _project_inject_codegraph_guidance):
        1. 无 session state / 无 feature flag——_tool_names 即 gate
        2. system_prompt = None 时用 lstrip("\\n") 避免前置空行
        3. 已存在 system_prompt 时追加在末尾
        """
        # 同时接受新旧两个工具名,避免单点失败
        if not (
            "astrbot_file_remove" in self._tool_names
            or "astrbot_file_remove_tool" in self._tool_names
        ):
            return
        if _FILE_REMOVE_GUIDANCE_MARKER in (req.system_prompt or ""):
            return
        if req.system_prompt is None or req.system_prompt == "":
            req.system_prompt = _FILE_REMOVE_GUIDANCE.lstrip("\n")
        else:
            req.system_prompt = req.system_prompt + _FILE_REMOVE_GUIDANCE
        logger.debug("[file_remove] 已向 system_prompt 注入优先使用指引")

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

        reminder_template = (self._config.get("plan_mode_reminder") or "").strip()
        if not reminder_template:
            # 没配 reminder,标记为已注入(避免每轮检查)
            self._plan_reminded[umo] = True
            return

        # 替换 {blocked} 占位符
        blocked_str = (
            ", ".join(sorted(set(blocked_tools))) if blocked_tools else "(none)"
        )
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
        logger.debug(f"[plan] 会话 {umo}: 已注入 plan 模式 reminder 到 user message")

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

    # ── file-browser 端点(v3.2)─────────────────────────────────────
    #
    # 详见 docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md
    # 入口路由在 initialize() 中注册(本方法只是 view_handler)。

    async def handle_get_file_browser(self) -> dict:
        """Web API handler for ``GET /spcode/file-browser``.

        Query params:
          - ``path`` (必填):绝对路径;指向文件 / 目录 / 符号链接。

        Response envelope::
            {"status": "ok", "data": {<type-specific>}}

        type ∈ {file, directory, symlink, null}。null 表示错误,
        此时 data 含 ``reason`` 字段说明错误类型。
        """
        import astrbot.api.web as _aw

        raw_path = _aw.request.query.get("path", "").strip()
        if not raw_path:
            return {"status": "ok", "data": _build_error_response("", "path_not_found")}
        path = Path(raw_path)
        # 不跟随 symlink 的路径校验(spec §7.2):broken symlink 时
        # path.exists() 返回 False,所以**额外**用 path.is_symlink() 检查
        if not path.exists() and not path.is_symlink():
            return {"status": "ok", "data": _build_error_response(path, "path_not_found")}
        # 分支调度(spec §7.3):
        #   1. directory → _build_directory_response(+ Cache-Control)
        #   2. file      → _build_file_response(+ ETag/304 路径)
        #   3. symlink   → _build_symlink_response(无缓存)
        #   4. else      → special_file (FIFO/socket/device,无缓存)
        try:
            entry_type = _classify_entry(path)
            if entry_type == "directory":
                data = _build_directory_response(path)
                # 目录 mtime 作为 ETag(廉价;漏检 in-place 编辑,
                # 接受 1 个 poll 周期延迟,同 git-diff ETag 策略)
                dir_etag = _compute_file_etag(path)
                # max-age=2 让 5-10s polling 期间大部分请求直接不发请求
                # (浏览器命中本地缓存),2s 后再用 ETag revalidate
                cache_headers = _common_cache_headers(dir_etag)
                cache_headers["Cache-Control"] = "private, max-age=2, must-revalidate"
                if dir_etag and _get_if_none_match() == dir_etag:
                    return _make_304_response(cache_headers)
                return _JSONResponseCompat(
                    {"status": "ok", "data": data}, headers=cache_headers
                )
            elif entry_type == "file":
                data = _build_file_response(path)
                # 文件内容 ETag(mtime_ns + size):100% 准确
                file_etag = _compute_file_etag(path)
                cache_headers = _common_cache_headers(file_etag)
                if file_etag and _get_if_none_match() == file_etag:
                    return _make_304_response(cache_headers)
                return _JSONResponseCompat(
                    {"status": "ok", "data": data}, headers=cache_headers
                )
            elif entry_type == "symlink":
                data = _build_symlink_response(path)
            else:
                data = _build_error_response(path, "special_file")
        except (PermissionError, OSError) as exc:
            logger.warning("file-browser: OSError on %s: %s", path, exc)
            return {"status": "ok", "data": _build_error_response(path, _classify_oserror(exc))}
        # symlink / special / 其他类型:不缓存(状态不稳定或语义不明确)
        return {"status": "ok", "data": data}