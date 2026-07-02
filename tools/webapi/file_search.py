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
import re
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
MAX_BYTES_PER_FILE: int = 1024 * 1024  # 与 rg --max-filesize=1M 对齐

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


# ── 纯 Python 兜底 ─────────────────────────────────────────────

_PYTHON_FALLBACK_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        ".idea",
        ".vscode",
    }
)


def _glob_to_re(glob: str) -> re.Pattern[str]:
    """把 shell glob (*.py → .*\\.py) 转成正则。

    支持逗号分隔的多 glob: ``*.py,*.md`` → ``^(.*\\.py|.*\\.md)$``
    """
    parts = glob.split(",")
    pat = "|".join(re.escape(p).replace(r"\*", ".*").replace(r"\?", ".") for p in parts)
    return re.compile(f"^({pat})$")


async def _run_python_fallback(
    *,
    pattern: str,
    directory: str,
    path_filter: str | None,
    glob_filter: str | None,
    case_sensitive: bool,
    regex: bool,
    max_results: int,
    context_chars: int,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """纯 Python 兜底:os.walk + re.finditer。

    Returns:
        (results, truncated, error_message_or_None)
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        try:
            pat = re.compile(pattern, flags)
        except re.error as exc:
            return [], False, f"invalid regex: {exc}"
    else:
        pat = re.compile(re.escape(pattern), flags)

    glob_re = _glob_to_re(glob_filter) if glob_filter else None
    search_root = Path(directory) / path_filter if path_filter else Path(directory)
    if not search_root.is_dir():
        return [], False, f"path_filter not found: {path_filter}"

    def _walk() -> tuple[list[dict[str, Any]], bool]:
        out: list[dict[str, Any]] = []
        was_truncated = False
        for root, dirs, files in os.walk(search_root, followlinks=False):
            # 原地修剪:跳过 _PYTHON_FALLBACK_SKIP_DIRS 与所有 dotfile 目录
            dirs[:] = [
                d
                for d in dirs
                if d not in _PYTHON_FALLBACK_SKIP_DIRS and not d.startswith(".")
            ]
            for fname in files:
                if len(out) >= max_results:
                    was_truncated = True
                    break
                if glob_re and not glob_re.match(fname):
                    continue
                fpath = Path(root) / fname
                try:
                    st = fpath.stat()
                except OSError:
                    continue
                if st.st_size > MAX_BYTES_PER_FILE:
                    continue
                try:
                    with fpath.open("r", encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, start=1):
                            m = pat.search(line)
                            if m:
                                out.append(
                                    {
                                        "path": str(
                                            fpath.relative_to(directory)
                                        ).replace("\\", "/"),
                                        "line": i,
                                        "column": m.start() + 1,
                                        "snippet": _make_snippet(
                                            line.rstrip("\n"),
                                            m.start(),
                                            len(m.group(0)),
                                            context_chars,
                                        ),
                                    }
                                )
                                if len(out) >= max_results:
                                    was_truncated = True
                                    break
                except OSError:
                    continue
            if was_truncated:
                break
        return out, was_truncated

    try:
        results, truncated = await asyncio.to_thread(_walk)
    except Exception as exc:
        return [], False, f"fallback error: {exc}"
    return results, truncated, None


async def _run_fallback_or_error(
    *,
    pattern: str,
    directory: str,
    path_filter: str | None,
    glob_filter: str | None,
    case_sensitive: bool,
    regex: bool,
    max_results: int,
    context_chars: int,
    effective_umo: str | None,
    worktree: str | None,
    _elapsed: Any,
) -> tuple[list[dict[str, Any]], bool, dict | None]:
    """运行 Python 兜底,把 3 类错误映射成对应的 envelope。

    Returns:
        (results, truncated, error_envelope_or_None)
        - 成功:  (results, truncated, None)
        - 失败:  ([], False, error_envelope)
    """
    results, truncated, fb_err = await _run_python_fallback(
        pattern=pattern,
        directory=directory,
        path_filter=path_filter,
        glob_filter=glob_filter,
        case_sensitive=case_sensitive,
        regex=regex,
        max_results=max_results,
        context_chars=context_chars,
    )
    if fb_err and fb_err.startswith("invalid regex"):
        return (
            [],
            False,
            _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PATTERN,
                elapsed_ms=_elapsed(),
                umo=effective_umo,
                worktree=worktree,
                directory=directory,
            ),
        )
    if fb_err and fb_err.startswith("path_filter"):
        return (
            [],
            False,
            _make_envelope(
                success=False,
                reason=ReasonCode.PATH_UNSAFE_FILTER,
                elapsed_ms=_elapsed(),
                umo=effective_umo,
                worktree=worktree,
                directory=directory,
            ),
        )
    if fb_err and fb_err.startswith("fallback error"):
        return (
            [],
            False,
            _make_envelope(
                success=False,
                reason=ReasonCode.SEARCH_UNAVAILABLE,
                elapsed_ms=_elapsed(),
                umo=effective_umo,
                worktree=worktree,
                directory=directory,
            ),
        )
    return results, truncated, None


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

    # 4. 实际搜索(rg 优先,失败则走 Python 兜底)
    backend_used = "python"
    results: list[dict[str, Any]] = []
    truncated = False

    if getattr(plugin, "_rg_available", False):
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
            results, truncated, fb_err = await _run_fallback_or_error(
                pattern=pattern,
                directory=directory,
                path_filter=path_filter,
                glob_filter=glob_filter,
                case_sensitive=case_sensitive,
                regex=regex,
                max_results=max_results,
                context_chars=context_chars,
                effective_umo=effective_umo,
                worktree=worktree,
                _elapsed=_elapsed,
            )
            if fb_err is not None:
                return fb_err
    else:
        # rg 不可用 → 直接走兜底
        results, truncated, fb_err = await _run_fallback_or_error(
            pattern=pattern,
            directory=directory,
            path_filter=path_filter,
            glob_filter=glob_filter,
            case_sensitive=case_sensitive,
            regex=regex,
            max_results=max_results,
            context_chars=context_chars,
            effective_umo=effective_umo,
            worktree=worktree,
            _elapsed=_elapsed,
        )
        if fb_err is not None:
            return fb_err

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
