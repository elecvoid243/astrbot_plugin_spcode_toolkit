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
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}([+-]\d{2}:?\d{2}|Z))?$"
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