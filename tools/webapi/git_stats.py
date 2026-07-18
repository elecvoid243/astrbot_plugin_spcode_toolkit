"""GET /spcode/git-stats — 变更统计服务端聚合(按日 + 热点文件)。

Spec: docs/superpowers/specs/2026-07-18-git-stats-endpoint-design.md

单次 ``git log --pretty=tformat:@@STATS@@%x00%aI --numstat --no-renames``
调用,Python 侧聚合,前端拿到即可渲染。
"""

from __future__ import annotations

import logging
import re
import time as _time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from .file_browser import (
    _common_cache_headers,
    _get_if_none_match,
    _make_304_response,
)
from ._helpers import (
    _JSONResponseCompat,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# ── 端点常量 ──
MAX_COMMITS_DEFAULT = 5000
MAX_COMMITS_HARD = 20000
TOP_FILES_DEFAULT = 10
TOP_FILES_HARD = 50
MAX_PARAM_LENGTH = 512
MAX_STATS_BYTES = 8 * 1024 * 1024  # 8 MB stdout 硬顶

# 哨兵对齐(沿用 git_log 的 shortstat 哨兵先例):每条 commit 的 format 块
# 以 ``@@STATS@@\x00<ISO日期>`` 开头,后续 numstat 行归属该 commit。
# 关键不变量: merge commit 后**没有** numstat 行 → 计 commit、行数 0。
_STATS_SENTINEL = "@@STATS@@"
_PRETTY = f"tformat:{_STATS_SENTINEL}%x00%aI"

# ISO 日期块首行: ``2026-07-10T10:00:00+08:00`` → 取作者本地日期部分
_ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}")

# since/until 校验(与 git_log 同一正则)
_ISO_PARAM_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}([+-]\d{2}:?\d{2}|Z)?)?$"
)

# ── ETag in-memory 缓存(复用 git-log 模式: HEAD sha + mtimes + TTL) ──
# 值三元组: (etag, head_sha, monotonic_ts)。head_sha 一并缓存是因为
# handler 的 envelope 需要 resolved_sha 字段,而 ETag 计算时已经
# rev-parse 过 HEAD,留存可避免第二次子进程调用。
_STATS_ETAG_TTL: float = 1.5
_STATS_ETAG_CACHE_MAX = 64
_STATS_ETAG_CACHE: OrderedDict[str, tuple[str, str, float]] = OrderedDict()


# ──────────────────────────────────────────────────────────
# 解析器
# ──────────────────────────────────────────────────────────


def _parse_stats_log_output(raw: str) -> list[dict]:
    """Parse ``git log --pretty=tformat:@@STATS@@%x00%aI --numstat --no-renames``.

    Output structure (one block per commit)::

        @@STATS@@\x00<ISO datetime>\n<adds>\t<dels>\t<path>\n...\n
        @@STATS@@\x00<ISO datetime>\n...\n

    Merge commits have a header line but zero numstat lines.
    Binary files show ``-`` for adds/dels → counted as 0 lines but the
    file touch is still recorded.

    Args:
        raw: Raw stdout from the git subprocess.

    Returns:
        ``[{"date": "YYYY-MM-DD", "files": [{"path", "additions",
        "deletions"}]}]`` in git-log order (newest first). Malformed
        blocks (non-ISO header) are skipped defensively.
    """
    commits: list[dict] = []
    if not raw:
        return commits
    segments = raw.split(_STATS_SENTINEL + "\x00")
    for seg in segments:
        if not seg.strip():
            continue
        lines = seg.split("\n")
        m = _ISO_DATE_RE.match(lines[0].strip())
        if m is None:
            continue
        files: list[dict] = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            adds_raw, dels_raw, path = parts
            files.append(
                {
                    "path": path,
                    "additions": int(adds_raw) if adds_raw.isdigit() else 0,
                    "deletions": int(dels_raw) if dels_raw.isdigit() else 0,
                }
            )
        commits.append({"date": m.group(1), "files": files})
    return commits


def _aggregate_stats(commits: list[dict], top_files: int) -> dict:
    """Aggregate per-commit parse output into days / hot_files / totals / range.

    Args:
        commits: Output of :func:`_parse_stats_log_output`.
        top_files: Cap for the hot_files list (totals stay untruncated).

    Returns:
        ``{"days": [...asc], "hot_files": [...], "totals": {...},
        "range": {"first", "last"}}``. hot_files sort: commits desc →
        (additions+deletions) desc → path asc. ``files_changed`` counts
        ALL touched files (pre-truncation).
    """
    days_map: dict[str, dict] = {}
    files_map: dict[str, dict] = {}
    total_adds = 0
    total_dels = 0
    for c in commits:
        date = c["date"]
        d = days_map.setdefault(
            date, {"date": date, "commits": 0, "additions": 0, "deletions": 0}
        )
        d["commits"] += 1
        for f in c["files"]:
            d["additions"] += f["additions"]
            d["deletions"] += f["deletions"]
            total_adds += f["additions"]
            total_dels += f["deletions"]
            hf = files_map.setdefault(
                f["path"],
                {"path": f["path"], "commits": 0, "additions": 0, "deletions": 0},
            )
            hf["commits"] += 1
            hf["additions"] += f["additions"]
            hf["deletions"] += f["deletions"]
    days = sorted(days_map.values(), key=lambda x: x["date"])
    hot_files = sorted(
        files_map.values(),
        key=lambda x: (-x["commits"], -(x["additions"] + x["deletions"]), x["path"]),
    )[:top_files]
    dates = sorted(days_map.keys())
    return {
        "days": days,
        "hot_files": hot_files,
        "totals": {
            "commits": len(commits),
            "additions": total_adds,
            "deletions": total_dels,
            "files_changed": len(files_map),
        },
        "range": {
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
        },
    }


# ──────────────────────────────────────────────────────────
# ETag 计算(镜像 git_log._compute_log_etag,独立缓存实例)
# ──────────────────────────────────────────────────────────


async def _compute_stats_etag(
    git_bin: str,
    directory: str,
    *,
    query_fingerprint: str = "",
) -> tuple[str, str]:
    """Compute a weak ETag for the git-stats endpoint.

    Args:
        git_bin: Resolved git executable path.
        directory: Worktree root.
        query_fingerprint: Stable ``|``-joined fingerprint of the query
            params (ref/max_commits/top_files/since/until) so any param
            change yields a different ETag (no 304 staleness).

    Returns:
        ``(etag, head_sha)`` — weak ETag string plus the resolved HEAD
        sha (``"no-head"`` when unresolvable). head_sha doubles as the
        envelope's ``resolved_sha`` field, sparing a second subprocess.
    """
    cache_key = (
        f"{directory}\x00{query_fingerprint}" if query_fingerprint else directory
    )
    now = _time.monotonic()
    cached = _STATS_ETAG_CACHE.get(cache_key)
    if cached is not None and (now - cached[2]) < _STATS_ETAG_TTL:
        _STATS_ETAG_CACHE.move_to_end(cache_key)
        return cached[0], cached[1]

    head_sha = "no-head"
    try:
        head_result = await _run_git_async(
            [git_bin, "-C", directory, "rev-parse", "HEAD"],
            timeout=5.0,
            encoding="utf-8",
        )
        if head_result.get("ok") and head_result.get("stdout"):
            head_sha = head_result["stdout"].strip()
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

    if query_fingerprint:
        etag = f'W/"{head_sha}-{wt_mtime}-{idx_mtime}-{query_fingerprint}"'
    else:
        etag = f'W/"{head_sha}-{wt_mtime}-{idx_mtime}"'

    _STATS_ETAG_CACHE[cache_key] = (etag, head_sha, now)
    while len(_STATS_ETAG_CACHE) > _STATS_ETAG_CACHE_MAX:
        _STATS_ETAG_CACHE.popitem(last=False)
    return etag, head_sha


# ──────────────────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────────────────


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-stats handler.

    Spec: docs/superpowers/specs/2026-07-18-git-stats-endpoint-design.md
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. Query 参数解析 ──
    from astrbot.api import web

    query = web.request.query if hasattr(web, "request") else {}

    def _qget(key: str, default: str | None = None) -> str | None:
        try:
            v = query.get(key)
            return v if v else default
        except Exception:
            return default

    def _invalid() -> dict:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            loaded=False,
            umo=umo,
            worktree=worktree,
        )

    max_commits_raw = _qget("max_commits")
    if max_commits_raw is not None:
        try:
            max_commits = int(max_commits_raw)
        except ValueError:
            return _invalid()
        if not (1 <= max_commits <= MAX_COMMITS_HARD):
            return _invalid()
    else:
        max_commits = MAX_COMMITS_DEFAULT

    top_files_raw = _qget("top_files")
    if top_files_raw is not None:
        try:
            top_files = int(top_files_raw)
        except ValueError:
            return _invalid()
        if not (1 <= top_files <= TOP_FILES_HARD):
            return _invalid()
    else:
        top_files = TOP_FILES_DEFAULT

    ref = _qget("ref") or "HEAD"
    since = _qget("since")
    until = _qget("until")

    # 长度 + 选项注入 + ISO 校验
    if len(ref) > MAX_PARAM_LENGTH or ref.startswith("-"):
        return _invalid()
    for val in (since, until):
        if val and (len(val) > MAX_PARAM_LENGTH or not _ISO_PARAM_RE.match(val)):
            return _invalid()

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # ── 3. ETag 检查(query 指纹纳入,防 304 误判) ──
    query_fingerprint = f"{ref}|{max_commits}|{top_files}|{since or ''}|{until or ''}"
    etag, resolved_sha = await _compute_stats_etag(
        plugin._git_binary(),
        directory,
        query_fingerprint=query_fingerprint,
    )
    cache_headers = _common_cache_headers(etag)
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)

    # ── 4. 单次 git log 调用(max+1 判 truncated) ──
    git_bin = plugin._git_binary()
    log_args = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "log",
        f"--pretty={_PRETTY}",
        "--numstat",
        "--no-renames",
        f"-n{max_commits + 1}",
    ]
    if since:
        log_args.append(f"--since={since}")
    if until:
        log_args.append(f"--until={until}")
    log_args.append(ref)

    raw_result = await _run_git_async(log_args, encoding="utf-8")
    if not raw_result["ok"]:
        stderr = raw_result.get("stderr", "")
        if "does not have any commits" in stderr or "ambiguous" in stderr.lower():
            reason = ReasonCode.EMPTY_REPOSITORY
        else:
            reason = ReasonCode.GIT_ERROR
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr,
        )

    raw = raw_result["stdout"]
    if len(raw) > MAX_STATS_BYTES:
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"git output exceeded {MAX_STATS_BYTES} bytes",
        )

    # ── 5. 解析 + 截断 + 聚合 ──
    commits = _parse_stats_log_output(raw)
    truncated = len(commits) > max_commits
    if truncated:
        commits = commits[:max_commits]  # git log 新→旧,丢弃最老的
    agg = _aggregate_stats(commits, top_files)

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            ref=ref,
            resolved_sha=resolved_sha,
            days=agg["days"],
            hot_files=agg["hot_files"],
            totals=agg["totals"],
            range=agg["range"],
            truncated=truncated,
            max_commits=max_commits,
        ),
        status_code=200,
        headers=cache_headers,
    )
