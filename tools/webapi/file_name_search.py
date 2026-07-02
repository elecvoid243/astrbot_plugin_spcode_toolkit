"""POST /spcode/file-name-search — 在已加载项目内按文件名(basename)匹配文件。

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md §5.6

v2.0 (2026-07-02): 后端从 ``subprocess.run(["rg", "--files", ...])`` 改为
``python_ripgrep.files()``(AstrBot 自带依赖,与 file-search 的
``python_ripgrep.search`` 平行)。修复:rg 二进制在 AstrBot runtime 里
NOT on system PATH(它被 bundled 在 ``python_ripgrep.pyd`` 模块内部),
原 subprocess 路径总是 ``FileNotFoundError`` → ``search_unavailable``。

现在:
  - 用 ``rg_files(patterns=[], paths=[dir], globs=[glob])`` 拿候选文件列表
  - pattern / regex / case_sensitive 过滤在 Python 端做(不传给 rg 库)
  - 5s 超时由 ``asyncio.wait_for(asyncio.to_thread(...))`` 包裹
  - reason mapping:
      - ``SEARCH_TIMEOUT``   → ``asyncio.TimeoutError``(5s)
      - ``SEARCH_UNAVAILABLE`` → ``ImportError``(库未装)或 generic ``Exception``
        (python_ripgrep.files 内部 rg 调用失败)

路径处理:
  - ``rg_files`` 返回 OS-native 绝对路径(Windows 上是反斜杠)
  - 用 ``os.path.relpath(raw, directory)`` 转成相对路径
  - 再 ``.replace(os.sep, "/")`` 统一为 POSIX 风格(跨平台响应一致)
  - ``path_filter`` 防御:handler 在 Python 端二次确认每个结果以 path_filter
    为前缀,防止 rg 库在 path_filter 边界外输出(TOCTOU / 符号链接等)。

响应 schema: ``{path, name, type, size}``(无 line/column/snippet,因这是
路径级匹配不是行级匹配)。``context_chars`` 字段被忽略(filename 模式无
snippet 切片语义)。
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from python_ripgrep import files as rg_files

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


# ── 工具函数 ─────────────────────────────────────────────────


def _path_is_in_subdir(rel_path: str, path_filter: str) -> bool:
    """检查 ``rel_path`` 是否在 ``path_filter`` 子目录内(防御性二次确认)。

    Args:
        rel_path: POSIX 风格相对路径,如 ``src/api/auth.py``。
        path_filter: 用户传入的子目录前缀,如 ``src/api`` 或 ``src/api/``。

    Returns:
        True → 在子目录内;False → 越界(应被过滤掉)。
    """
    if not path_filter:
        return True
    # 规范化 path_filter:确保以 "/" 结尾,避免 "src/api" 误匹配 "src/api_other"
    prefix = path_filter if path_filter.endswith("/") else path_filter + "/"
    return rel_path.startswith(prefix)


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
    matcher = re.compile(base_pattern, 0 if case_sensitive else re.IGNORECASE).search

    # 5. 搜索路径 = directory 下的 path_filter(若提供)
    search_path = os.path.join(directory, path_filter) if path_filter else directory

    # 6. 调用 python_ripgrep.files(包在 to_thread + 5s 超时里,保持事件循环空闲)
    #   - 超时: asyncio.TimeoutError → SEARCH_TIMEOUT
    #   - 库未装 / rg 内部失败: ImportError / Exception → SEARCH_UNAVAILABLE
    def _do_list() -> list[str]:
        return rg_files(
            patterns=[],  # empty = list all (no content regex)
            paths=[search_path] if search_path else None,
            globs=[glob_filter] if glob_filter else None,
        )

    try:
        raw_paths: list[str] = await asyncio.wait_for(
            asyncio.to_thread(_do_list),
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
        logger.warning(f"[file-name-search] python_ripgrep.files 调用失败: {exc!s}")
        return _make_envelope(
            success=False,
            reason=ReasonCode.SEARCH_UNAVAILABLE,
            elapsed_ms=_elapsed(),
            umo=effective_umo,
            worktree=worktree,
            directory=directory,
        )

    # 7. 解析 + 过滤
    #   - ``raw_paths``:python_ripgrep.files 返回 OS-native 绝对路径
    #   - ``os.path.relpath`` → 相对 directory 的路径(OS-native 分隔符)
    #   - ``.replace(os.sep, "/")`` → 统一为 POSIX 风格(跨平台响应一致)
    #   - ``_path_is_in_subdir`` 防御:即使 rg_files 给 path_filter 搜了
    #     <directory>/<path_filter> 子目录,Python 端再校验一次,防止 TOCTOU
    #     或符号链接导致越界路径。
    results: list[dict[str, Any]] = []
    truncated = False
    for raw_path in raw_paths:
        if len(results) >= max_results:
            truncated = True
            break
        try:
            rel_path = os.path.relpath(raw_path, directory).replace(os.sep, "/")
        except (OSError, ValueError):
            # raw_path 在不同盘符 / 不可解析 → 跳过
            continue
        if not rel_path or rel_path == ".":
            continue
        if path_filter and not _path_is_in_subdir(rel_path, path_filter):
            continue
        basename = os.path.basename(rel_path)
        if not matcher(basename):
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


# Author: spcode_impl, 2026-07-02 17:40
