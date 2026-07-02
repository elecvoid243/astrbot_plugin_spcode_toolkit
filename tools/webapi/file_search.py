"""POST /spcode/file-search — 在已加载项目内按内容搜索文件。

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md

后端实现:ripgrep 优先(plugin._rg_available=True);缺失则走纯 Python 兜底。
v2.15.0 (2026-07-02) — Task 2 实现 rg 优先路径,Python 兜底在 Task 3。
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import subprocess as _sp
import sys as _sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

_NO_WINDOW: dict = (
    {"creationflags": _sp.CREATE_NO_WINDOW} if _sys.platform == "win32" else {}
)


# ── snippet 切片 ───────────────────────────────────────────────


def _make_snippet(
    line: str,
    match_start: int,
    match_len: int,
    context_chars: int,
) -> str:
    """从一整行中切出含匹配段的 snippet(前后各 context_chars 字符)。"""
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


# ── ripgrep 调用 ───────────────────────────────────────────────


async def _run_ripgrep(
    *,
    pattern: str,
    directory: str,
    path_filter: str | None,
    glob_filter: str | None,
    case_sensitive: bool,
    regex: bool,
    max_results: int,
    rg_path: str,
) -> dict[str, Any]:
    """调用 ripgrep 并返回 stdout(str)+ 错误信息(若有)。

    Returns:
        {"ok": True, "stdout": str} | {"ok": False, "error": str, "kind": str}
        kind ∈ {"missing", "timeout", "regex_error", "other"}
    """
    cmd: list[str] = [
        rg_path,
        "--json",
        "--no-config",
        "--no-heading",
        "--line-number",
        "--column",
        "--no-messages",
        "--max-columns=200",
        "--max-columns-preview",
        "--max-filesize=1M",
        "--no-follow",
    ]
    if not case_sensitive:
        cmd.append("--ignore-case")
    if not regex:
        cmd.append("--fixed-strings")
    if glob_filter:
        cmd.extend(["--glob", glob_filter])
    cmd.extend(["--max-count", str(max_results)])
    cmd.append("--")
    cmd.append(pattern)
    cmd.append(os.path.join(directory, path_filter) if path_filter else directory)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=directory or None,
            **_NO_WINDOW,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "kind": "missing",
            "error": f"{rg_path} 未安装或不在 PATH 中",
        }

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {
            "ok": False,
            "kind": "timeout",
            "error": f"rg timeout ({SEARCH_TIMEOUT_SECONDS}s)",
        }

    if proc.returncode == 0:
        return {"ok": True, "stdout": stdout_bytes.decode("utf-8", errors="replace")}
    if proc.returncode == 1:
        return {"ok": True, "stdout": ""}  # no matches
    err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
    kind = "regex_error" if (regex and "regex" in err_msg.lower()) else "other"
    return {
        "ok": False,
        "kind": kind,
        "error": err_msg or f"rg exit {proc.returncode}",
    }


def _parse_ripgrep_json(
    raw: str,
    max_results: int,
    context_chars: int,
    directory: str,
) -> tuple[list[dict[str, Any]], bool]:
    """解析 rg --json NDJSON 流,返回 (results, truncated)。

    ``directory`` 用于将 rg 输出的绝对路径裁剪为相对路径(spec §5.2 要求
    path 字段是相对文件路径)。
    """
    results: list[dict[str, Any]] = []
    truncated = False
    # directory 标准化一次(尾部去掉可能的分隔符,统一分隔符为 /)
    dir_prefix = os.path.normpath(directory).replace("\\", "/")
    for line in raw.splitlines():
        if len(results) >= max_results:
            truncated = True
            break
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data", {})
        path = (data.get("path") or {}).get("text", "")
        line_no = int(data.get("line_number") or 0)
        submatches = data.get("submatches") or []
        if not path or not line_no or not submatches:
            continue
        sub = submatches[0]
        col = int(sub.get("start") or 0) + 1  # 0-based → 1-based
        full_line = (data.get("lines") or {}).get("text", "").rstrip("\n")
        match_text = (sub.get("match") or {}).get("text", "")
        match_start = int(sub.get("start") or 0)
        snippet = _make_snippet(
            full_line,
            match_start,
            len(match_text),
            context_chars,
        )
        # 裁剪为相对于 directory 的路径(用 POSIX 风格分隔符)
        rel_path = path.replace("\\", "/")
        if dir_prefix and rel_path.lower().startswith(dir_prefix.lower() + "/"):
            rel_path = rel_path[len(dir_prefix) + 1 :]
        results.append(
            {
                "path": rel_path,
                "line": line_no,
                "column": col,
                "snippet": snippet,
            }
        )
    return results, truncated


# ── 主 handler ────────────────────────────────────────────────


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/file-search handler."""
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
    try:
        max_results = max(
            1,
            min(int(body.get("max_results", DEFAULT_MAX_RESULTS)), MAX_MAX_RESULTS),
        )
        context_chars = max(
            10,
            min(
                int(body.get("context_chars", DEFAULT_CONTEXT_CHARS)),
                MAX_CONTEXT_CHARS,
            ),
        )
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

    # 4. 实际搜索(rg 路径)
    if not getattr(plugin, "_rg_available", False):
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )

    rg_path = getattr(plugin, "_rg_path", "rg")
    rg_result = await _run_ripgrep(
        pattern=pattern,
        directory=directory,
        path_filter=path_filter,
        glob_filter=glob_filter,
        case_sensitive=case_sensitive,
        regex=regex,
        max_results=max_results,
        rg_path=rg_path,
    )
    if rg_result["ok"]:
        results, truncated = _parse_ripgrep_json(
            rg_result["stdout"],
            max_results,
            context_chars,
            directory,
        )
        backend_used = "ripgrep"
    elif rg_result.get("kind") == "timeout":
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_TIMEOUT,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )
    elif rg_result.get("kind") == "regex_error":
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PATTERN,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )
    else:
        logger.warning(
            f"[file-search] rg failed ({rg_result.get('error')!r}),"
            " falling back to Python"
        )
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=directory,
            pattern=pattern,
            backend=backend_used,
            result_count=len(results),
            max_results=max_results,
            truncated=truncated,
            results=results,
        ),
        status_code=200,
    )
