# tools/webapi/git_diff.py
"""GET /spcode/git-diff* — 渐进式 git diff 三端点。

Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.1
v2.32.0 (2026-10-25): 渐进式加载改造(与 dashboard 配套部署):
  - GET /spcode/git-diff        → 元数据端点(分页): 文件名/状态/增减行数
    + 全量汇总(stat/total), **不再携带 patch 文本**。原完整 patch 渲染是
    轮询路径上最贵的 git 操作,而 patch 只有在用户展开某个文件时才会被
    消费——现在改为按需拉取。
  - GET /spcode/git-diff/file   → 新增: 单文件 patch(≤256KB,scope 感知),
    path 必须出现在当前 diff 的文件列表中(防任意文件探测)。
  - GET /spcode/git-diff/full   → 新增: 旧行为的全量 patch(1MB 上限),
    唯一消费方是提交对话框的 AI commit message(scoped staged)。
v3.4: ETag in-memory 缓存 + HTTP 缓存头(304 短路)
v3.1: scope 参数(un-staged/staged/all)
v3.3: 4-in-1 git call(优化性能)
"""

from __future__ import annotations

import logging
import time as _time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from .._helpers import _validate_worktree_param, detect_console_encoding
from ..project import state as _proj_state
from ._helpers import (
    _JSONResponseCompat,
    _compute_git_etag,
    _run_git_async,
)
from .file_browser import (
    _common_cache_headers,
    _get_if_none_match,
    _make_304_response,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# ── 容量常量 ──
# full 端点(提交对话框)的 patch 硬上限,与 v1 的 git-diff 一致。
MAX_GIT_DIFF_BYTES = 1 * 1024 * 1024  # 1 MB
# file 端点(单文件按需)的 patch 硬上限。单文件 256KB 已远超常规源码
# diff;超出即截断,前端沿用"显示全部 N 行"的溢出降级链路。
MAX_FILE_DIFF_BYTES = 256 * 1024

# ── 分页常量(元数据端点) ──
DEFAULT_PAGE_LIMIT = 200
MAX_PAGE_LIMIT = 1000

_GIT_DIFF_ENCODING = detect_console_encoding()  # 进程内一次探测

# ── git-diff ETag in-memory 缓存(v3.4, 2026-06-21, P0 perf) ──
# WHY: dashboard 5-10s 轮询 git-diff 端点时,绝大多数请求 head SHA / 工作树
# / .git/index 都不变,无需每次跑 ``git rev-parse HEAD``(~10-20ms 进程启动)。
# 加 1.5s TTL 缓存可让 N 个并发轮询共享 1 个 git 调用。
# 设计:LRU + TTL(双条件),容量上限 64 个 directory,过期懒清理。
# 注意:仅在 asyncio 单事件循环上下文中使用,无 thread-safe 保护;
#      极端并发下只可能重复算 ETag,不会数据错乱。
_DIFF_ETAG_TTL: float = 1.5  # seconds
_DIFF_ETAG_CACHE_MAX = 64
_DIFF_ETAG_CACHE: OrderedDict[str, tuple[str, float]] = OrderedDict()

# ── 元数据解析缓存(v2.32.0 渐进式改造配套) ──
# WHY: 列表端点分页后,同一工作区状态下的翻页请求(offset=200/400/…)
# 不应各自重跑 numstat。key = (directory, scope, etag):etag 未变 ⇒ 状态
# 未变 ⇒ 解析结果复用是安全的(与 304 短路同一信任级别);etag 变化 ⇒
# key miss ⇒ 重跑。LRU 64 容量与 ETag 缓存对齐。
_META_CACHE_MAX = 64
_META_CACHE: OrderedDict[
    tuple[str, str, str], tuple[list[dict], str, dict[str, int]]
] = OrderedDict()

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


def _parse_raw_numstat_z(metadata_output: str) -> list[dict]:
    """Parse combined ``git diff --raw --numstat -z`` output.

    Raw records provide status and canonical paths. Numstat records provide
    line counts. Rename and copy records carry source and destination paths
    in separate NUL-delimited fields (v2.21 canonical-paths fix).

    v2.32.0: 每条记录新增 ``is_binary``——numstat 对二进制文件输出
    ``-\\t-\\t<path>``,替代旧流程从 patch 文本探测 "Binary files" 的方式
    (元数据端点不再携带 patch)。

    Args:
        metadata_output: Decoded NUL-delimited Git output.

    Returns:
        File metadata in raw Git output order.

    Raises:
        ValueError: A raw or numstat record has an invalid structure.
    """
    tokens = metadata_output.split("\0")
    cursor = 0
    raw_files: list[dict] = []

    while cursor < len(tokens) and tokens[cursor].startswith(":"):
        header = tokens[cursor]
        cursor += 1
        fields = header.split()
        if len(fields) != 5 or not fields[4]:
            raise ValueError(f"Invalid raw metadata header: {header!r}")

        raw_status = fields[4][0]
        if raw_status in {"A", "D", "R", "C"}:
            status = raw_status
        else:
            status = "M"

        if raw_status in {"R", "C"}:
            if cursor + 1 >= len(tokens):
                raise ValueError("Missing rename or copy path fields")
            source_path = tokens[cursor]
            path = tokens[cursor + 1]
            cursor += 2
            if not source_path or not path:
                raise ValueError("Empty rename or copy path field")
        else:
            if cursor >= len(tokens):
                raise ValueError("Missing raw metadata path field")
            path = tokens[cursor]
            cursor += 1
            if not path:
                raise ValueError("Empty raw metadata path field")

        raw_files.append({"path": path, "status": status})

    counts_by_path: dict[str, tuple[int, int]] = {}
    binary_by_path: dict[str, bool] = {}

    while cursor < len(tokens):
        record = tokens[cursor]
        cursor += 1
        if not record:
            continue

        parts = record.split("\t", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid numstat record: {record!r}")

        add_raw, delete_raw, path = parts
        if not path:
            if cursor + 1 >= len(tokens):
                raise ValueError("Missing numstat rename or copy path fields")
            source_path = tokens[cursor]
            path = tokens[cursor + 1]
            cursor += 2
            if not source_path or not path:
                raise ValueError("Empty numstat rename or copy path field")

        try:
            additions = 0 if add_raw == "-" else int(add_raw)
            deletions = 0 if delete_raw == "-" else int(delete_raw)
        except ValueError as exc:
            raise ValueError(f"Invalid numstat counts: {record!r}") from exc

        counts_by_path[path] = (additions, deletions)
        binary_by_path[path] = add_raw == "-" and delete_raw == "-"

    return [
        {
            "path": item["path"],
            "status": item["status"],
            "additions": counts_by_path.get(item["path"], (0, 0))[0],
            "deletions": counts_by_path.get(item["path"], (0, 0))[1],
            "is_binary": binary_by_path.get(item["path"], False),
        }
        for item in raw_files
    ]


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
        lines.append(f" {f['path']:<{max_path_len}} | {total:>{max_change_len}} {bar}")
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
    """为 git-diff 端点计算弱 ETag(委托给共享 helper)。

    v3.5 (2026-06-30): 委托给 ``_compute_git_etag`` 统一实现,加入 3 路
    porcelain 探测解决 ETag staleness。详见
    ``tools/webapi/_helpers.py:_compute_git_etag`` docstring。

    v3.4 P0 perf: 1.5s TTL in-memory 缓存保留(LRU 64 容量),让 dashboard
    5-10s polling 时 N 个请求共享 1 个 ETag 计算。
    """
    # 缓存查询(命中且未过期 → 直接返回)
    now = _time.monotonic()
    cached = _DIFF_ETAG_CACHE.get(directory)
    if cached is not None and (now - cached[1]) < _DIFF_ETAG_TTL:
        # LRU:命中时移到队尾
        _DIFF_ETAG_CACHE.move_to_end(directory)
        return cached[0]

    # 缓存未命中或过期 → 委托统一 helper 计算
    etag = await _compute_git_etag(git_bin, directory)

    # 写入缓存 + LRU 驱逐
    _DIFF_ETAG_CACHE[directory] = (etag, now)
    while len(_DIFF_ETAG_CACHE) > _DIFF_ETAG_CACHE_MAX:
        _DIFF_ETAG_CACHE.popitem(last=False)
    return etag


async def _load_diff_metadata(
    git_bin: str,
    directory: str,
    scope: str,
    etag: str,
    scope_args: list[str],
) -> tuple[list[dict], str, dict[str, int]] | None:
    """Load (records, stat, totals) for a (directory, scope, etag) state.

    Cache-first: a (directory, scope, etag) hit skips the numstat git
    call entirely — the etag already proves the worktree state is
    unchanged, so the parsed metadata is still valid. A miss runs
    ``git diff --raw --numstat -z`` once and fills the cache.

    Args:
        git_bin: Git binary path.
        directory: Repository directory (already worktree-validated).
        scope: Resolved diff scope.
        etag: Current worktree ETag (cache key component).
        scope_args: Positional args for the scope (from _SCOPE_GIT_ARGS).

    Returns:
        ``(records, stat, totals)`` where totals =
        ``{"files", "additions", "deletions"}`` over ALL records, or
        None when the git call fails (caller maps to its own envelope).
    """
    key = (directory, scope, etag)
    cached = _META_CACHE.get(key)
    if cached is not None:
        _META_CACHE.move_to_end(key)
        return cached

    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]
    metadata_result = await _run_git_async(
        git_prefix + ["diff", "--raw", "--numstat", "-z"] + scope_args,
        encoding="utf-8",
    )
    if not metadata_result["ok"]:
        return None

    try:
        records = _parse_raw_numstat_z(metadata_result.get("stdout", ""))
    except ValueError as exc:
        logger.warning("git-diff metadata parse failed: %s", exc)
        return None

    stat = _build_stat_text(records)
    totals = {
        "files": len(records),
        "additions": sum(r["additions"] for r in records),
        "deletions": sum(r["deletions"] for r in records),
    }

    _META_CACHE[key] = (records, stat, totals)
    while len(_META_CACHE) > _META_CACHE_MAX:
        _META_CACHE.popitem(last=False)
    return records, stat, totals


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
            "files_changed": [],
            "elapsed_ms": elapsed_ms,
        },
    }


def _parse_page_params() -> tuple[int, int]:
    """解析并夹取列表端点的分页参数(offset / limit)。

    Returns:
        ``(offset, limit)``;offset 非负,limit 夹取在
        [1, MAX_PAGE_LIMIT]。非法输入回退默认值(与 scope 的宽容解析
        同风格——dashboard 是唯一调用方,不存在恶意分页)。
    """
    offset = 0
    limit = DEFAULT_PAGE_LIMIT
    try:
        from astrbot.api import web

        raw_offset = web.request.query.get("offset")
        raw_limit = web.request.query.get("limit")
        if raw_offset is not None:
            offset = max(0, int(raw_offset))
        if raw_limit is not None:
            limit = min(MAX_PAGE_LIMIT, max(1, int(raw_limit)))
    except Exception:
        offset = 0
        limit = DEFAULT_PAGE_LIMIT
    return offset, limit


async def _run_git_diff_query(
    plugin: SPCodeToolkit,
    umo: str | None,
    worktree_param: str | None,
    scope: str,
    *,
    http_cache: bool = True,
) -> tuple[dict | None, dict]:
    """git-diff 系列端点共用的前置检查 + 元数据装载。

    Steps mirror the legacy handle() flow: feature flags → umo 解析与
    回退 → worktree 6 步防御 → 目录存在性 → ETag/304 → repo probe →
    元数据(缓存优先)。

    Args:
        plugin: SPCodeToolkit instance.
        umo: Requested umo (may be None → most-recent fallback).
        worktree_param: Raw ?worktree= value (None/empty = primary).
        scope: Resolved diff scope.
        http_cache: False 时跳过 If-None-Match 304 短路(按需的 file
            端点必须真正执行——它携带自己的 payload,不能复用列表的
            304 语义)。

    Returns:
        ``(error_envelope, None)`` on any preflight / git failure —
        the envelope is ready to return as-is. On success
        ``(None, ctx)`` with ctx = {"directory", "umo", "git_bin",
        "records", "stat", "totals", "etag", "cache_headers"}.
        When ``http_cache`` is True and If-None-Match matches, returns
        ``(None, {"not_modified": True, ...})`` instead.
    """

    def _elapsed(t0: float) -> int:
        return int((_time.time() - t0) * 1000)

    t0 = _time.time()
    git_bin = plugin._git_binary()

    # 1. Feature flag 校验
    if not (
        plugin._config.get("agentsmd_enabled", True)
        and plugin._config.get("codegraph_enabled", True)
    ):
        return _make_git_diff_empty_envelope(
            umo=umo, reason="feature_disabled", elapsed_ms=_elapsed(t0)
        ), {}

    # 2. umo 解析与回退
    if umo:
        info = plugin.get_loaded_project(umo)
    else:
        all_items = _proj_state.items()
        if not all_items:
            info = None
        else:
            _, info = max(
                all_items.items(),
                key=lambda kv: kv[1].get("loaded_at", 0),
            )

    if info is None:
        return _make_git_diff_empty_envelope(
            umo=umo, reason="no_project_loaded", elapsed_ms=_elapsed(t0)
        ), {}

    directory = info.get("directory", "")

    # 2.5 ?worktree= 参数校验(6-step defense;spec §2.3)
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
                elapsed_ms=_elapsed(t0),
            ), {}
        directory = validated_wt  # use the validated worktree path

    # 3. 目录存在性
    if not Path(directory).is_dir():
        return _make_git_diff_empty_envelope(
            umo=umo,
            reason="directory_missing",
            directory=directory,
            elapsed_ms=_elapsed(t0),
        ), {}

    # 3.5 HTTP 缓存(v3.3,2026-06-21): 在跑 git probe / diff 之前
    # 先算 ETag 并检查 If-None-Match,命中直接 304 短路。
    etag = await _compute_diff_etag(git_bin, directory)
    cache_headers = _common_cache_headers(etag)
    if http_cache and _get_if_none_match() == etag:
        return None, {"not_modified": True, "etag": etag,
                      "cache_headers": cache_headers}

    # 4. Git repository probe — git outputs UTF-8 on every platform,
    # so we always decode with utf-8 regardless of the Windows console
    # codepage.
    probe = await _run_git_async(
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
                elapsed_ms=_elapsed(t0),
            ), {}
        if "未安装" in probe.get("error", ""):
            return _make_git_diff_empty_envelope(
                umo=umo,
                reason="git_unavailable",
                directory=directory,
                elapsed_ms=_elapsed(t0),
            ), {}
        return _make_git_diff_empty_envelope(
            umo=umo,
            reason="git_error",
            directory=directory,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
            elapsed_ms=_elapsed(t0),
        ), {}

    loaded = await _load_diff_metadata(
        git_bin, directory, scope, etag, _SCOPE_GIT_ARGS[scope]
    )
    if loaded is None:
        return _make_git_diff_empty_envelope(
            umo=umo,
            reason="git_error",
            directory=directory,
            elapsed_ms=_elapsed(t0),
        ), {}

    records, stat, totals = loaded
    return None, {
        "directory": directory,
        "umo": umo,
        "git_bin": git_bin,
        "records": records,
        "stat": stat,
        "totals": totals,
        "etag": etag,
        "cache_headers": cache_headers,
        "t0": t0,
    }


async def handle(
    plugin: SPCodeToolkit,
) -> dict:
    """Web API handler for ``GET /spcode/git-diff`` — 元数据分页端点。

    v2.32.0 渐进式改造:响应不再携带 patch 文本;文件列表分页;新增
    is_binary / etag / total_* / has_more 字段。patch 按需走
    ``GET /spcode/git-diff/file``;全量 patch(提交对话框)走
    ``GET /spcode/git-diff/full``。

    Returns a JSON envelope of the form::

        {
            "status": "ok",
            "data": {
                "loaded": bool,
                "directory": str | None,
                "umo": str | None,
                "scope": str,
                "etag": str,
                "files_changed": [  # 单页(offset..offset+limit)
                    {"path", "status", "additions", "deletions", "is_binary"}
                ],
                "stat": str,
                "total_files": int,        # 全量(跨页)统计
                "total_additions": int,
                "total_deletions": int,
                "offset": int,
                "limit": int,
                "has_more": bool,
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
    scope: str = DEFAULT_SCOPE
    try:
        scope_raw = web.request.query.get("scope")
        if scope_raw is not None:
            candidate = scope_raw.strip().lower()
            if candidate and candidate not in _VALID_SCOPES:
                return _make_git_diff_empty_envelope(
                    umo=None,
                    reason="invalid_scope",
                    elapsed_ms=0,
                )
            scope = candidate or DEFAULT_SCOPE
    except Exception:
        scope = DEFAULT_SCOPE

    offset, limit = _parse_page_params()

    error, ctx = await _run_git_diff_query(plugin, umo, worktree_param, scope)
    if error is not None:
        return error
    if ctx.get("not_modified"):
        return _make_304_response(ctx["cache_headers"])

    records = ctx["records"]
    page = records[offset : offset + limit]
    t0 = ctx["t0"]

    return _JSONResponseCompat(
        {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": ctx["directory"],
                "umo": ctx["umo"],
                "scope": scope,
                "etag": ctx["etag"],
                "files_changed": page,
                "stat": ctx["stat"],
                "total_files": ctx["totals"]["files"],
                "total_additions": ctx["totals"]["additions"],
                "total_deletions": ctx["totals"]["deletions"],
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < len(records),
                "elapsed_ms": int((_time.time() - t0) * 1000),
                "reason": None,
            },
        },
        status_code=200,
        headers=ctx["cache_headers"],
    )


async def handle_file(
    plugin: SPCodeToolkit,
) -> dict:
    """Web API handler for ``GET /spcode/git-diff/file`` — 单文件按需 patch。

    v2.32.0 渐进式改造新增。path 必须出现在当前 diff 的文件列表中
    (防任意文件探测);二进制文件直接标记返回,不渲染 patch。

    Returns::

        {
            "status": "ok",
            "data": {
                "loaded": bool,
                "path": str,
                "scope": str,
                "is_binary": bool,
                "diff": str | None,     # unified patch(≤256KB)
                "truncated": bool,
                "truncated_at_bytes": int,
                "max_bytes": int,
                "elapsed_ms": int,
                "reason": str | None
            }
        }
    """
    from astrbot.api import web

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    umo: str | None = None
    worktree_param: str | None = None
    file_path = ""
    try:
        umo_raw = web.request.query.get("umo")
        umo = umo_raw if umo_raw else None
        wt_raw = web.request.query.get("worktree")
        worktree_param = wt_raw if wt_raw is not None else None
        file_path = (web.request.query.get("path") or "").strip()
    except Exception:
        umo = None
        worktree_param = None
        file_path = ""

    scope: str = DEFAULT_SCOPE
    try:
        scope_raw = web.request.query.get("scope")
        if scope_raw is not None:
            candidate = scope_raw.strip().lower()
            if candidate and candidate not in _VALID_SCOPES:
                return _make_git_diff_empty_envelope(
                    umo=None,
                    reason="invalid_scope",
                    elapsed_ms=_elapsed(),
                )
            scope = candidate or DEFAULT_SCOPE
    except Exception:
        scope = DEFAULT_SCOPE

    if not file_path:
        return _make_git_diff_empty_envelope(
            umo=umo, reason="path_required", elapsed_ms=_elapsed()
        )

    error, ctx = await _run_git_diff_query(
        plugin, umo, worktree_param, scope, http_cache=False
    )
    if error is not None:
        return error
    if ctx.get("not_modified"):  # pragma: no cover - http_cache=False
        return _make_git_diff_empty_envelope(
            umo=ctx.get("umo"),
            reason="git_error",
            elapsed_ms=_elapsed(),
        )

    records: list[dict] = ctx["records"]
    match = next((r for r in records if r["path"] == file_path), None)
    if match is None:
        return _make_git_diff_empty_envelope(
            umo=ctx["umo"],
            reason="path_not_in_diff",
            directory=ctx["directory"],
            elapsed_ms=_elapsed(),
        )

    if match.get("is_binary"):
        return _JSONResponseCompat(
            {
                "status": "ok",
                "data": {
                    "loaded": True,
                    "path": file_path,
                    "scope": scope,
                    "is_binary": True,
                    "diff": None,
                    "truncated": False,
                    "truncated_at_bytes": 0,
                    "max_bytes": MAX_FILE_DIFF_BYTES,
                    "elapsed_ms": _elapsed(),
                    "reason": None,
                },
            },
            status_code=200,
        )

    patch_result = await _run_git_async(
        [
            ctx["git_bin"],
            "-C",
            ctx["directory"],
            "-c",
            "color.ui=never",
            "diff",
        ]
        + _SCOPE_GIT_ARGS[scope]
        + ["--", file_path],
        encoding="utf-8",
    )
    if not patch_result["ok"]:
        return _make_git_diff_empty_envelope(
            umo=ctx["umo"],
            reason="git_error",
            directory=ctx["directory"],
            stderr=patch_result.get("stderr", ""),
            elapsed_ms=_elapsed(),
        )

    raw = patch_result["stdout"]
    truncated = len(raw) > MAX_FILE_DIFF_BYTES
    patch = raw[:MAX_FILE_DIFF_BYTES]

    return _JSONResponseCompat(
        {
            "status": "ok",
            "data": {
                "loaded": True,
                "path": file_path,
                "scope": scope,
                "is_binary": False,
                "diff": patch,
                "truncated": truncated,
                "truncated_at_bytes": MAX_FILE_DIFF_BYTES if truncated else 0,
                "max_bytes": MAX_FILE_DIFF_BYTES,
                "elapsed_ms": _elapsed(),
                "reason": None,
            },
        },
        status_code=200,
    )


async def handle_full(
    plugin: SPCodeToolkit,
) -> dict:
    """Web API handler for ``GET /spcode/git-diff/full`` — 旧式全量 patch。

    v2.32.0 渐进式改造新增:保留 v1 ``GET /spcode/git-diff`` 的完整行为
    (full patch 1MB 上限 + files_changed + stat),供**提交对话框的 AI
    commit message**使用(scope=staged)。侧栏轮询永远不调本端点。

    Response shape == 旧 git-diff 响应(diff/stat/files_changed/truncated
    /truncated_at_bytes/max_bytes),外加 is_binary 字段与 scope 回显。
    """
    from astrbot.api import web

    umo: str | None = None
    worktree_param: str | None = None
    try:
        umo_raw = web.request.query.get("umo")
        umo = umo_raw if umo_raw else None
        wt_raw = web.request.query.get("worktree")
        worktree_param = wt_raw if wt_raw is not None else None
    except Exception:
        umo = None
        worktree_param = None

    scope: str = DEFAULT_SCOPE
    try:
        scope_raw = web.request.query.get("scope")
        if scope_raw is not None:
            candidate = scope_raw.strip().lower()
            if candidate and candidate not in _VALID_SCOPES:
                return _make_git_diff_empty_envelope(
                    umo=None,
                    reason="invalid_scope",
                    elapsed_ms=0,
                )
            scope = candidate or DEFAULT_SCOPE
    except Exception:
        scope = DEFAULT_SCOPE

    error, ctx = await _run_git_diff_query(plugin, umo, worktree_param, scope)
    if error is not None:
        return error
    if ctx.get("not_modified"):
        return _make_304_response(ctx["cache_headers"])

    t0 = ctx["t0"]
    git_prefix = [ctx["git_bin"], "-C", ctx["directory"], "-c", "color.ui=never"]
    raw_result = await _run_git_async(
        git_prefix + ["diff"] + _SCOPE_GIT_ARGS[scope], encoding="utf-8"
    )
    if not raw_result["ok"]:
        return _make_git_diff_empty_envelope(
            umo=ctx["umo"],
            reason="git_error",
            directory=ctx["directory"],
            stderr=raw_result.get("stderr", ""),
            elapsed_ms=int((_time.time() - t0) * 1000),
        )

    raw = raw_result["stdout"]
    truncated = len(raw) > MAX_GIT_DIFF_BYTES
    diff = raw[:MAX_GIT_DIFF_BYTES]

    return _JSONResponseCompat(
        {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": ctx["directory"],
                "umo": ctx["umo"],
                "scope": scope,
                "diff": diff,
                "stat": ctx["stat"],
                "files_changed": ctx["records"],
                "truncated": truncated,
                "truncated_at_bytes": MAX_GIT_DIFF_BYTES if truncated else 0,
                "max_bytes": MAX_GIT_DIFF_BYTES,
                "elapsed_ms": int((_time.time() - t0) * 1000),
                "reason": None,
            },
        },
        status_code=200,
        headers=ctx["cache_headers"],
    )
