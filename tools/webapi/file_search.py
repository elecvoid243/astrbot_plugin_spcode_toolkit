"""POST /spcode/file-search — 在已加载项目内按内容搜索文件。

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md

v2.15.0 (2026-07-02) refactor: 后端从 rg CLI 子进程 + Python 兜底 改为
直接调用 ``python_ripgrep.search``(AstrBot 自带依赖,见
``astrbot/core/computer/booters/local.py:13``)。统一在
``asyncio.to_thread`` + 5s 超时下执行,保持事件循环空闲。

注: 本模块的 ``rg_search`` 是在文件顶层 ``from python_ripgrep import search
as rg_search`` 引入的,便于测试时用 ``unittest.mock.patch.object(
tools.webapi.file_search, "rg_search", ...)`` 替换。
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from python_ripgrep import search as rg_search

from ._helpers import (
    _JSONResponseCompat,
    _make_envelope,
    _validate_repo_relative_file,
    ReasonCode,
)
from .git_log import _git_endpoint_preflight

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# ── 端点常量 ──
SEARCH_TIMEOUT_SECONDS: float = 5.0
DEFAULT_MAX_RESULTS: int = 200
MAX_MAX_RESULTS: int = 1000
DEFAULT_CONTEXT_CHARS: int = 60
MAX_CONTEXT_CHARS: int = 200
MAX_PATTERN_LENGTH: int = 256
MAX_SNIPPET_LENGTH: int = 160

# python_ripgrep 输出的单行格式: ``<absolute_path>:<line_no>:<content>\n``
# path 与 line 都不会含 ":"(path 不会含换行/冒号;line 是纯数字),
# 因此第一对冒号前是 path,第一对冒号后是 line_no,剩余是 content。
# 用非贪婪 + 锚定首尾处理 Windows 绝对路径 (``C:\...``) 里的反斜杠冒号不会误伤
# —— ripgrep 输出是 POSIX 风格正斜杠(它把 OS-native 路径统一成 "/" 打印)。
_RG_LINE_RE = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<content>.*)$")


# ── snippet 切片 ────────────────────────────────────────────────


def _make_snippet(
    line: str,
    match_start: int,
    match_len: int,
    context_chars: int,
) -> str:
    """从一整行中切出含匹配段的 snippet(前后各 context_chars 字符)。

    v2.15.0 (2026-07-02) refactor: 当前主路径下未使用(新实现直接把整行
    content 作为 snippet 返回,不做切片)。保留此函数供未来:
      - 切到 rg --json 路径(可拿到 match 偏移)
      - 或切到本地 re.search + _make_snippet 的二级处理
    等场景复用。
    """
    s = max(0, match_start - context_chars)
    e = min(len(line), match_start + match_len + context_chars)
    snippet = line[s:e]
    prefix = "..." if s > 0 else ""
    suffix = "..." if e < len(line) else ""
    full = prefix + snippet + suffix
    if len(full) <= MAX_SNIPPET_LENGTH:
        return full
    # 超长时以 match 为中心重新切
    match_in_snippet = snippet[match_start - s : match_start - s + match_len]
    mid = snippet.find(match_in_snippet)
    if mid < 0:
        return full[:MAX_SNIPPET_LENGTH] + "..."
    half = MAX_SNIPPET_LENGTH // 2 - len(match_in_snippet) // 2 - 3
    half = max(10, half)
    s2 = max(0, mid - half)
    e2 = min(len(snippet), mid + len(match_in_snippet) + half)
    return (
        ("..." if s2 > 0 else "")
        + snippet[s2:e2]
        + ("..." if e2 < len(snippet) else "")
    )


# ── 主 handler ─────────────────────────────────────────────────


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/file-search handler.

    Returns:
        ``_JSONResponseCompat`` envelope, status=200 on success, 200 on
        failure (reason in ``data.reason``, envelope shape 统一)。
    """
    t0 = time.time()

    def _elapsed() -> int:
        return int((time.time() - t0) * 1000)

    body = body or {}

    # 1. pattern 校验
    pattern = (body.get("pattern") or "").strip()
    if not pattern:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PATTERN,
            elapsed_ms=_elapsed(),
            umo=umo,
            worktree=worktree,
        )
    if len(pattern) > MAX_PATTERN_LENGTH:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATTERN_TOO_LONG,
            elapsed_ms=_elapsed(),
            umo=umo,
            worktree=worktree,
        )
    if "\n" in pattern or "\r" in pattern:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PATTERN,
            elapsed_ms=_elapsed(),
            umo=umo,
            worktree=worktree,
        )

    case_sensitive = bool(body.get("case_sensitive", False))
    regex = bool(body.get("regex", False))
    # 注: ``body.get("context_chars", ...)`` 不再解析 — python_ripgrep 的输出
    # 不含 match 偏移,新实现直接把整行 content 当 snippet,不再做 context 切片。
    # 保留 ``DEFAULT_CONTEXT_CHARS`` / ``MAX_CONTEXT_CHARS`` 常量仅作为 API
    # 文档(请求体可带该字段,被忽略)。
    try:
        max_results = max(
            1,
            min(int(body.get("max_results", DEFAULT_MAX_RESULTS)), MAX_MAX_RESULTS),
        )
        # 静默消费 context_chars(类型校验副作用): int 转换失败 → INVALID_PATTERN
        int(body.get("context_chars", DEFAULT_CONTEXT_CHARS))
    except (TypeError, ValueError):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PATTERN,
            elapsed_ms=_elapsed(),
            umo=umo,
            worktree=worktree,
        )

    path_filter = (body.get("path_filter") or "").strip() or None
    glob_filter = (body.get("glob_filter") or "").strip() or None

    # 2. preflight
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        if "loaded" not in err["data"]:
            err["data"]["loaded"] = False
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # 3. path_filter 4 步防御
    if path_filter:
        _, path_err = _validate_repo_relative_file(path_filter, Path(directory))
        if path_err is not None:
            return _make_envelope(
                success=False,
                reason=ReasonCode.PATH_UNSAFE_FILTER,
                elapsed_ms=_elapsed(),
                umo=effective_umo,
                worktree=worktree,
                directory=directory,
            )

    # 4. 构造 rg_search 用的 pattern
    # - regex=False: 用户期望字面子串 → re.escape
    # - regex=True : 用户给的是正则 → 原样(用 re.compile 预校验语法)
    # - case_sensitive=False: 始终用 (?i) 前缀(只有 regex/re.escape 后才接)
    try:
        if regex:
            re.compile(pattern)  # 提前捕获语法错 → INVALID_PATTERN
            base_pattern = pattern
        else:
            base_pattern = re.escape(pattern)
    except re.error:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PATTERN,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )
    rg_pattern = base_pattern if case_sensitive else f"(?i){base_pattern}"

    # 5. 搜索路径 = directory 下的 path_filter(若提供)
    search_path = os.path.join(directory, path_filter) if path_filter else directory

    # 6. 调用 python_ripgrep.search(包在 to_thread + 5s 超时里,保持事件循环空闲)
    def _do_search() -> list[str]:
        return rg_search(
            patterns=[rg_pattern],
            paths=[search_path] if search_path else None,
            globs=[glob_filter] if glob_filter else None,
            line_number=True,
        )

    try:
        raw_lines: list[str] = await asyncio.wait_for(
            asyncio.to_thread(_do_search),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_TIMEOUT,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )
    except ImportError:
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[file-search] python_ripgrep 调用失败: {exc!s}")
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )

    # 7. 解析结果(绝对路径 → 相对 directory 的 POSIX 路径)
    dir_prefix = os.path.normpath(directory).replace("\\", "/")
    dir_prefix_lc = dir_prefix.lower() + "/"
    results: list[dict[str, Any]] = []
    truncated = False
    for raw in raw_lines:
        if len(results) >= max_results:
            truncated = True
            break
        # python_ripgrep 返回每行末尾带 "\n"(见 _do_search 调用),裁掉
        line_str = raw.rstrip("\r\n")
        m = _RG_LINE_RE.match(line_str)
        if m is None:
            continue
        abs_path = m.group("path")
        # ripgrep 输出是 POSIX 分隔符(它内部用 / 打印)
        rel_path = abs_path.replace("\\", "/")
        if dir_prefix and rel_path.lower().startswith(dir_prefix_lc):
            rel_path = rel_path[len(dir_prefix) + 1 :]
        line_no = int(m.group("line"))
        # column 默认 1 — python_ripgrep 的 line_number 格式不带列偏移
        # (该库未暴露 column 字段;若要带列需改用 rg --json 路径)
        content = m.group("content")
        results.append(
            {
                "path": rel_path,
                "line": line_no,
                "column": 1,
                "snippet": content,  # 整行作为 snippet(已含 match)
            }
        )

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=directory,
            pattern=pattern,
            result_count=len(results),
            max_results=max_results,
            truncated=truncated,
            results=results,
        ),
        status_code=200,
    )


# Author: spcode_impl, 2026-07-02 15:50
