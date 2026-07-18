### Task 1: 哨兵解析 `_parse_stats_log_output` + 聚合 `_aggregate_stats`

**Files:**
- Create: `tools/webapi/git_stats.py`
- Test: `tests/test_git_stats.py`

**Interfaces:**
- Produces（Task 2 依赖）:
  - `_gs._parse_stats_log_output(raw: str) -> list[dict]`，返回 `[{"date": "YYYY-MM-DD", "files": [{"path": str, "additions": int, "deletions": int}]}]`（git log 输出序，新→旧）
  - `_gs._aggregate_stats(commits: list[dict], top_files: int) -> dict`，返回 `{"days": [...], "hot_files": [...], "totals": {...}, "range": {...}}`
  - 常量 `_gs._STATS_SENTINEL == "@@STATS@@"`、`_gs._PRETTY == "tformat:@@STATS@@%x00%aI"`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_git_stats.py`：

```python
"""Tests for the GET /spcode/git-stats HTTP endpoint.

Real git via tmp_path fixtures for handler tests (mirrors test_git_log.py);
parser tests are pure-string (no repo needed).
Spec: docs/superpowers/specs/2026-07-18-git-stats-endpoint-design.md
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock  # noqa: F401

# 启用 pytest-asyncio(strict 模式下纯同步测试也写成 async def,同
# test_git_diff.py 的既有约定)。
pytestmark = pytest.mark.asyncio

_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
_PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from tools.project import state as _proj_state  # noqa: E402
from tools.webapi import git_stats as _gs  # noqa: E402


@pytest.fixture
def plugin():
    """Per-test plugin instance (conftest 提供 _make_plugin)."""
    return _make_plugin()


# ── helpers ──


def _git(path: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, env=env)


def _init_git_repo(path: Path) -> None:
    # -b main: 固定初始分支名,避免 git 版本差异(master/main)影响 merge 测试
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")


def _commit(path: Path, files: dict[str, str], date: str, message: str = "c") -> None:
    """Commit {name: content} files at an explicit author/committer date."""
    for name, content in files.items():
        (path / name).write_text(content, encoding="utf-8")
    _git(path, "add", ".")
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    _git(path, "commit", "-q", "-m", message, env=env)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": str(directory), "loaded_at": time.time()})


async def _call_with_query(
    monkeypatch,
    plugin,
    query: dict | None = None,
    headers: dict | None = None,
    umo: str = "test:umo",
):
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock(query, headers))
    return await _gs.handle(plugin, umo=umo)


# ── Task 1: parser tests (pure string) ──


async def test_parse_single_commit_two_files():
    raw = (
        "@@STATS@@\x002026-07-10T10:00:00+08:00\n"
        "5\t2\tsrc/a.py\n"
        "3\t0\tREADME.md\n"
        "\n"
    )
    commits = _gs._parse_stats_log_output(raw)
    assert commits == [
        {
            "date": "2026-07-10",
            "files": [
                {"path": "src/a.py", "additions": 5, "deletions": 2},
                {"path": "README.md", "additions": 3, "deletions": 0},
            ],
        }
    ]


async def test_parse_merge_commit_has_no_numstat_lines():
    raw = (
        "@@STATS@@\x002026-07-11T10:00:00+08:00\n"
        "\n"
        "@@STATS@@\x002026-07-10T09:00:00+08:00\n"
        "1\t1\ta.py\n"
    )
    commits = _gs._parse_stats_log_output(raw)
    assert len(commits) == 2
    assert commits[0]["date"] == "2026-07-11"
    assert commits[0]["files"] == []
    assert commits[1]["files"] == [
        {"path": "a.py", "additions": 1, "deletions": 1}
    ]


async def test_parse_binary_file_counts_touch_with_zero_lines():
    raw = "@@STATS@@\x002026-07-10T10:00:00+08:00\n-\t-\tlogo.png\n"
    commits = _gs._parse_stats_log_output(raw)
    assert commits[0]["files"] == [
        {"path": "logo.png", "additions": 0, "deletions": 0}
    ]


async def test_parse_skips_malformed_block():
    raw = "@@STATS@@\x00garbage-not-a-date\n1\t1\ta.py\n"
    assert _gs._parse_stats_log_output(raw) == []


async def test_parse_empty_output():
    assert _gs._parse_stats_log_output("") == []


# ── Task 1: aggregation tests (pure) ──


async def test_aggregate_days_hot_files_totals_range():
    commits = [
        {
            "date": "2026-07-10",
            "files": [
                {"path": "a.py", "additions": 5, "deletions": 1},
                {"path": "b.py", "additions": 2, "deletions": 0},
            ],
        },
        {
            "date": "2026-07-10",
            "files": [{"path": "a.py", "additions": 1, "deletions": 1}],
        },
        {
            "date": "2026-07-12",
            "files": [{"path": "a.py", "additions": 3, "deletions": 3}],
        },
    ]
    out = _gs._aggregate_stats(commits, top_files=10)
    assert out["days"] == [
        {"date": "2026-07-10", "commits": 2, "additions": 8, "deletions": 2},
        {"date": "2026-07-12", "commits": 1, "additions": 3, "deletions": 3},
    ]
    assert out["hot_files"][0] == {
        "path": "a.py",
        "commits": 3,
        "additions": 9,
        "deletions": 5,
    }
    assert out["totals"] == {
        "commits": 3,
        "additions": 11,
        "deletions": 5,
        "files_changed": 2,
    }
    assert out["range"] == {"first": "2026-07-10", "last": "2026-07-12"}


async def test_aggregate_hot_files_sort_and_truncation():
    commits = [
        {
            "date": "2026-07-10",
            "files": [
                {"path": "x.py", "additions": 1, "deletions": 0},
                {"path": "y.py", "additions": 5, "deletions": 0},
                {"path": "z.py", "additions": 9, "deletions": 0},
            ],
        },
        {
            "date": "2026-07-11",
            "files": [{"path": "y.py", "additions": 1, "deletions": 0}],
        },
    ]
    out = _gs._aggregate_stats(commits, top_files=2)
    # y: 2 commits 居首;x/z 各 1 commit,z 以行数 tiebreak 胜出
    assert [f["path"] for f in out["hot_files"]] == ["y.py", "z.py"]
    # files_changed 是截断前的完整去重数
    assert out["totals"]["files_changed"] == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_git_stats.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.webapi.git_stats'`（收集阶段即失败）

- [ ] **Step 3: 实现 `tools/webapi/git_stats.py`（解析 + 聚合部分）**

创建 `tools/webapi/git_stats.py`：

```python
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
```

- [ ] **Step 4: 运行 Task 1 测试确认通过**

Run: `python -m pytest tests/test_git_stats.py -x -q -k "parse or aggregate"`
Expected: 7 passed（handler 测试此时尚未写入，无收集错误）

- [ ] **Step 5: Commit**

```bash
git add tools/webapi/git_stats.py tests/test_git_stats.py
git commit -m "feat: add git-stats log parser and aggregation core"
```

---
