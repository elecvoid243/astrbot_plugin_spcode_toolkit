"""POST /spcode/file-name-search — 在已加载项目内按文件名(basename)匹配文件。

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md §5.6

v1.0 (2026-07-02): 与 ``file_search``(内容搜索)并列;不同点:
  - 后端: ``subprocess.run(["rg", "--files", directory, ...])`` 拿候选列表
    (``python_ripgrep`` 库只暴露 ``search()`` 即 content-only,无 ``--files``;
    路径级匹配只能用 rg CLI 子进程)。
  - 响应 schema 不同: ``{path, name, type, size}``(无 line/column/snippet,
    因为这是路径级匹配不是行级匹配)。
  - ``context_chars`` 字段被忽略(filename 模式无 snippet 切片语义)。

共用 file-search 的 ReasonCode + preflight + path_filter 4 步防御。5s 超时
由 ``asyncio.wait_for(asyncio.to_thread(...))`` 包裹,事件循环保持空闲。
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
import subprocess
import sys
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
MAX_PATTERN_LENGTH: int = 256

# 抑制 pythonw.exe 启动下子进程弹 cmd 黑窗。与 tools._helpers 同名常量
# 行为一致,但 webapi 层不依赖 tools._helpers(跨层耦合),此处平行复刻。
_NO_WINDOW_KWARGS: dict[str, int] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


# ── 主 handler ─────────────────────────────────────────────────


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/file-name-search handler.

    Returns:
        ``_JSONResponseCompat`` envelope, status=200 on success, 200 on
        failure (reason in ``data.reason``)。
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
    # ``context_chars`` 在 filename 模式下无意义,handler 静默忽略(spec §5.6.1)
    try:
        max_results = max(
            1,
            min(int(body.get("max_results", DEFAULT_MAX_RESULTS)), MAX_MAX_RESULTS),
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

    # 4. 编译 basename 匹配 pattern
    # - regex=False: 用户期望字面子串 → re.escape
    # - regex=True : 用户给的是正则 → 原样(用 re.compile 预校验语法)
    # - case_sensitive=False: re.IGNORECASE
    # - case_sensitive=True : 无 flag
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
    compiled = re.compile(base_pattern, 0 if case_sensitive else re.IGNORECASE)

    # 5. 构造 rg argv
    # - -g: glob 过滤(传给 rg 端,不是 Python 端)
    # - --ignore-case: 仅在 !case_sensitive 时加(影响 glob 大小写敏感行为)
    # - search_path 放最后(标准 rg 习惯;handler 内部 _do_run 的 argv 顺序)
    argv: list[str] = ["rg", "--files", "--no-config"]
    if glob_filter:
        argv += ["-g", glob_filter]
    if not case_sensitive:
        argv += ["--ignore-case"]
    # search_path: 若 path_filter,搜索该子目录;否则搜整个 worktree
    search_path = os.path.join(directory, path_filter) if path_filter else directory
    argv += [search_path]

    # 6. 调用 rg: ``asyncio.to_thread`` 把阻塞的 subprocess 丢进线程池,
    # ``asyncio.wait_for`` 限制 5s 超时,事件循环保持空闲。
    #   - 超时: asyncio.TimeoutError → SEARCH_TIMEOUT
    #   - rg 二进制缺失: subprocess.run 抛 FileNotFoundError → SEARCH_UNAVAILABLE
    #   - 其他异常: 兜底 SEARCH_UNAVAILABLE
    def _do_run() -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
            **_NO_WINDOW_KWARGS,
        )

    try:
        proc: subprocess.CompletedProcess = await asyncio.wait_for(
            asyncio.to_thread(_do_run),
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
    except subprocess.TimeoutExpired:
        # 双保险:asyncio.wait_for 之外,subprocess.run 自身也有 timeout=5s。
        # 在某些边界场景下,subprocess.run 可能在 asyncio.wait_for 取消之前
        # 先 raise TimeoutExpired(进程尚未结束,Python 端先观察到超时)。
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_TIMEOUT,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )
    except FileNotFoundError:
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[file-name-search] rg 调用失败: %s", exc)
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )

    # 7. 解析 rg 输出
    #   - returncode=0: 至少一个匹配
    #   - returncode=1: 无匹配(rg 的"软失败",不视为错误)
    #   - 其他: 错误 → SEARCH_UNAVAILABLE
    if proc.returncode not in (0, 1):
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
            stderr=proc.stderr or "",
        )

    # rg --files 输出是 repo-relative POSIX 路径(它内部用 / 打印,跨平台一致)
    # 我们的 search_path 可能是 directory 根,可能是 directory/path_filter 子目录。
    # 解析时不需要剥前缀 —— rg 已经返回相对路径,只要保证后面 os.path.join 时
    # 用 directory 作为根,os.path.basename(rel_path) 就能正确取到文件名。
    #
    # 防御性 path_filter 校验:rg 在 search_path 下应只返回该子目录内的文件,但
    # 极端场景下 rg 可能输出边界外路径(例如 path_filter 含 symlink 跳出)。我们
    # 在 Python 端做二次确认:rel_path 必须以 path_filter 为前缀(规范化带尾 /)。
    # 这条防御同时也是 unit test 的关键 —— 测试通过 mock subprocess.run 注入
    # 越界路径,验证 handler 不会把它们放进 results。
    filter_prefix: str | None = None
    if path_filter:
        filter_prefix = path_filter if path_filter.endswith("/") else path_filter + "/"

    results: list[dict[str, Any]] = []
    truncated = False
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        if len(results) >= max_results:
            truncated = True
            break
        rel_path = raw.replace("\\", "/")
        if filter_prefix and not rel_path.startswith(filter_prefix):
            # 越界 → 跳过(防御性)
            continue
        basename = os.path.basename(rel_path)
        if not compiled.search(basename):
            continue
        # type/size: 实际文件系统探测
        abs_path = os.path.join(directory, rel_path)
        if os.path.isdir(abs_path):
            entry_type = "dir"
            entry_size = 0
        else:
            entry_type = "file"
            try:
                entry_size = os.path.getsize(abs_path)
            except OSError:
                # 文件被删/无权限 → 容错为 0
                entry_size = 0
        results.append(
            {
                "path": rel_path,
                "name": basename,
                "type": entry_type,
                "size": entry_size,
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


# Author: spcode_impl, 2026-07-02 16:35
