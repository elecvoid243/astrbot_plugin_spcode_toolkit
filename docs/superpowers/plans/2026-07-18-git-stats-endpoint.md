# git-stats 端点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `GET /spcode/git-stats` 端点，服务端单次 `git log --numstat` 聚合出按日统计与热点文件排行，供 dashboard 变更热力图面板调用。

**Architecture:** 新模块 `tools/webapi/git_stats.py`（哨兵解析 + 聚合 + ETag + handler），全链路复用现有 git 端点约定（`_git_endpoint_preflight` / `_make_envelope` / `_run_git_async` / `ReasonCode` / ETag 缓存模式）；在 `tools/webapi/__init__.py` 的 `ROUTES` 注册一行。

**Tech Stack:** Python 3.10+、pytest（pytest-asyncio strict）、真实 git（tmp_path fixture，无 subprocess mock）。

**工作区:** `F:\github\astrbot_plugin_spcode_toolkit\.worktrees\feat-git-stats-endpoint`（branch `feat/git-stats-endpoint`）。所有命令均在该目录下执行。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-18-git-stats-endpoint-design.md`（本仓库同分支）
- 成功标志：`reason === null`（envelope 不输出 `success` 字段）
- `max_commits` 默认 5000，合法区间 1..20000；`top_files` 默认 10，合法区间 1..50；越界/非 int → `invalid_param`
- `ref` 默认 `HEAD`，长度 ≤512 且**禁止以 `-` 开头**（防选项注入）；`since`/`until` 走 git-log 同款 ISO 正则
- git stdout 硬顶 `MAX_STATS_BYTES = 8 * 1024 * 1024`，超出 → `git_error`
- 不新增 ReasonCode；全部复用 `_helpers.ReasonCode`
- 注释/日志用英文（主仓库 AGENTS.md 约定），模块 docstring 可中文（本仓库现状）
- 完成后 `ruff format` + `ruff check`（本仓库根有 ruff 配置）

---

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

### Task 2: ETag helper + `handle()` + 路由注册 + 端级测试

**Files:**
- Modify: `tools/webapi/git_stats.py`（追加 `_compute_stats_etag` 与 `handle`）
- Modify: `tools/webapi/__init__.py`（import + ROUTES 一行）
- Modify: `tests/test_webapi_end_to_end.py:389`（35 → 36）
- Test: `tests/test_git_stats.py`（追加 handler 测试）

**Interfaces:**
- Consumes: Task 1 的 `_parse_stats_log_output` / `_aggregate_stats` / `_PRETTY`；`_helpers._git_endpoint_preflight` 返回 `(err, ctx)`，`ctx = {"directory", "umo", "worktree"}`
- Produces: `handle(plugin: "SPCodeToolkit", *, umo: str | None = None, worktree: str | None = None) -> dict`，envelope data 字段见 spec §3.2；`_gs._STATS_ETAG_CACHE` / `_gs._STATS_ETAG_TTL`（测试用来清空/调零，镜像 `_LOG_ETAG_CACHE` 用法）；`_compute_stats_etag(...) -> tuple[str, str]` 返回 `(etag, head_sha)`，缓存值三元组 `(etag, head_sha, ts)`

- [ ] **Step 1: 写失败 handler 测试（追加到 `tests/test_git_stats.py` 末尾）**

```python
# ── Task 2: handler e2e tests (real git) ──


async def test_handle_aggregation_e2e(monkeypatch, plugin, tmp_path: Path):
    """2 commits across 2 days → days/totals/hot_files/range 精确匹配。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "1\n2\n"}, "2026-07-10T10:00:00+08:00")
    _commit(
        tmp_path,
        {"a.py": "1\n2\n3\n", "b.md": "x\n"},
        "2026-07-11T10:00:00+08:00",
    )
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    assert data["reason"] is None
    assert data["loaded"] is True
    assert data["totals"] == {
        "commits": 2,
        "additions": 4,
        "deletions": 0,
        "files_changed": 2,
    }
    assert data["days"] == [
        {"date": "2026-07-10", "commits": 1, "additions": 2, "deletions": 0},
        {"date": "2026-07-11", "commits": 1, "additions": 2, "deletions": 0},
    ]
    assert data["hot_files"][0] == {
        "path": "a.py",
        "commits": 2,
        "additions": 3,
        "deletions": 0,
    }
    assert data["range"] == {"first": "2026-07-10", "last": "2026-07-11"}
    assert data["truncated"] is False
    assert data["max_commits"] == 5000


async def test_handle_merge_commit_counted_with_zero_lines(
    monkeypatch, plugin, tmp_path: Path
):
    """merge commit 计入 commits、行数为 0(关键不变量:无 numstat 行)。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "base\n"}, "2026-07-10T10:00:00+08:00")
    _git(tmp_path, "checkout", "-q", "-b", "feat")
    _commit(tmp_path, {"b.py": "x\n"}, "2026-07-11T10:00:00+08:00", message="feat")
    _git(tmp_path, "checkout", "-q", "main")
    _commit(
        tmp_path, {"c.py": "y\n"}, "2026-07-12T10:00:00+08:00", message="main-work"
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-13T10:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-13T10:00:00+08:00",
    }
    _git(tmp_path, "merge", "--no-ff", "-m", "merge feat", "feat", env=env)
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    assert data["totals"]["commits"] == 4  # base + feat + main-work + merge
    day_0713 = next(d for d in data["days"] if d["date"] == "2026-07-13")
    assert day_0713["commits"] == 1
    assert day_0713["additions"] == 0
    assert day_0713["deletions"] == 0


async def test_handle_binary_file_zero_lines(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    _git(tmp_path, "add", ".")
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-10T10:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-10T10:00:00+08:00",
    }
    _git(tmp_path, "commit", "-q", "-m", "bin", env=env)
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    assert data["totals"]["commits"] == 1
    assert data["totals"]["additions"] == 0
    assert data["totals"]["deletions"] == 0
    assert data["hot_files"] == [
        {"path": "logo.png", "commits": 1, "additions": 0, "deletions": 0}
    ]


async def test_handle_rename_counted_as_delete_add(
    monkeypatch, plugin, tmp_path: Path
):
    """--no-renames 下 rename 按删旧+增新计入(旧路径 del、新路径 add)。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"old.py": "1\n2\n3\n"}, "2026-07-10T10:00:00+08:00")
    _git(tmp_path, "mv", "old.py", "new.py")
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-11T10:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-11T10:00:00+08:00",
    }
    _git(tmp_path, "commit", "-q", "-m", "rename", env=env)
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    data = result["data"]
    by_path = {f["path"]: f for f in data["hot_files"]}
    # 新路径: rename commit 的 +3;旧路径: 初始 +3 与 rename 的 -3
    assert by_path["new.py"]["additions"] == 3
    assert by_path["old.py"]["deletions"] == 3


async def test_handle_max_commits_truncation(monkeypatch, plugin, tmp_path: Path):
    """max_commits=2 对 3 commits → truncated=true,只统计最新 2 条。"""
    _init_git_repo(tmp_path)
    for i in range(3):
        _commit(
            tmp_path, {"f.py": f"{i}\n"}, f"2026-07-{10 + i}T10:00:00+08:00"
        )
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(
        monkeypatch, plugin, query={"max_commits": "2"}
    )
    data = result["data"]
    assert data["truncated"] is True
    assert data["max_commits"] == 2
    assert data["totals"]["commits"] == 2
    assert data["range"]["first"] == "2026-07-11"  # 最老一条被丢弃


async def test_handle_since_until_passthrough(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    for i in range(3):
        _commit(
            tmp_path, {"f.py": f"{i}\n"}, f"2026-07-{10 + i}T10:00:00+08:00"
        )
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(
        monkeypatch,
        plugin,
        query={"since": "2026-07-11T00:00:00", "until": "2026-07-11T23:59:59"},
    )
    data = result["data"]
    assert data["totals"]["commits"] == 1
    # 07-11 的 commit 把 f.py 从 "0\n" 改写为 "1\n": 一行删+一行增
    assert data["days"] == [
        {"date": "2026-07-11", "commits": 1, "additions": 1, "deletions": 1}
    ]


async def test_handle_invalid_params(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "1\n"}, "2026-07-10T10:00:00+08:00")
    _load_project(plugin, "test:umo", str(tmp_path))

    for query in (
        {"max_commits": "abc"},
        {"max_commits": "0"},
        {"max_commits": "20001"},
        {"top_files": "0"},
        {"top_files": "51"},
        {"top_files": "x"},
        {"ref": "-n"},  # 选项注入防御
        {"since": "not-a-date"},
        {"until": "2026/07/10"},
    ):
        result = await _call_with_query(monkeypatch, plugin, query=query)
        assert result["data"]["reason"] == "invalid_param", (
            f"query={query} should be invalid_param, got {result['data']}"
        )


async def test_handle_no_project_loaded(monkeypatch, plugin):
    result = await _call_with_query(monkeypatch, plugin, umo="ghost:umo")
    assert result["data"]["reason"] == "no_project_loaded"


async def test_handle_not_a_git_repo(monkeypatch, plugin, tmp_path: Path):
    non_git = tmp_path / "plain"
    non_git.mkdir()
    _load_project(plugin, "test:umo", str(non_git))
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_handle_empty_repository(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)  # 无 commit
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "empty_repository"


async def test_handle_etag_304_short_circuit(monkeypatch, plugin, tmp_path: Path):
    """同 key 二次请求带 If-None-Match → 304;TTL=0 强制重算。"""
    _init_git_repo(tmp_path)
    _commit(tmp_path, {"a.py": "1\n"}, "2026-07-10T10:00:00+08:00")
    _load_project(plugin, "test:umo", str(tmp_path))
    _gs._STATS_ETAG_CACHE.clear()
    monkeypatch.setattr(_gs, "_STATS_ETAG_TTL", 0.0)

    r1 = await _call_with_query(monkeypatch, plugin)
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag, f"first response missing ETag: {dict(r1.headers)}"

    r2 = await _call_with_query(
        monkeypatch, plugin, headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_git_stats.py -x -q`
Expected: FAIL — `AttributeError: module 'tools.webapi.git_stats' has no attribute 'handle'`（或 `_STATS_ETAG_CACHE`）

- [ ] **Step 3: 实现 ETag helper 与 `handle()`（追加到 `tools/webapi/git_stats.py`）**

在 `_aggregate_stats` 之后追加：

```python
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
    query_fingerprint = (
        f"{ref}|{max_commits}|{top_files}|{since or ''}|{until or ''}"
    )
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
```

- [ ] **Step 4: 注册路由**

`tools/webapi/__init__.py`：
1. import 区（按字母序，在 `git_show` 之后）加 `git_stats,`；
2. docstring 端点清单追加 ``* ``/spcode/git-stats``       (GET)   # v2.21 (2026-07-18)``；
3. `ROUTES` 列表（建议放在 `git-show` 条目之后）追加：

```python
    (
        "/spcode/git-stats",  # v2.21 (2026-07-18)
        ["GET"],
        git_stats.handle,
        "获取已加载项目的变更统计(按日聚合 + 热点文件,供 stats 面板调用)",
    ),
```

`tests/test_webapi_end_to_end.py:389`：`assert plugin.context.register_web_api.call_count == 35` → `== 36`。

- [ ] **Step 5: 运行新测试确认通过**

Run: `python -m pytest tests/test_git_stats.py tests/test_webapi_end_to_end.py -q`
Expected: 全部 passed（18 + end_to_end 既有用例）

- [ ] **Step 6: 全量回归 + lint**

Run: `python -m pytest tests/ -q`
Expected: 全部 passed（基线 1318 + 新增 18 ≈ 1336，0 failed）

Run: `ruff format tools/webapi/git_stats.py tests/test_git_stats.py && ruff check tools/webapi/git_stats.py tests/test_git_stats.py`
Expected: 无改动/无告警（如有自动修复则复跑 Step 5）

- [ ] **Step 7: Commit**

```bash
git add tools/webapi/git_stats.py tools/webapi/__init__.py tests/test_git_stats.py tests/test_webapi_end_to_end.py
git commit -m "feat: add GET /spcode/git-stats endpoint with server-side aggregation"
```

---

## Self-Review 记录

- **Spec 覆盖**：§3 契约（Task 2 Step 3）、§4 解析/聚合（Task 1）、§5 防护（Task 2：bytes 上限/参数校验/ETag）、§6 注册（Task 2 Step 4）、§7 测试 10 项 → 本计划 18 个测试全覆盖（解析 5 + 聚合 2 + handler 11，含 spec 列出的全部场景）✅
- **占位符**：无 TBD/TODO；所有步骤含完整代码与精确命令 ✅
- **类型一致**：`_parse_stats_log_output` → `_aggregate_stats` 的 dict 形状在两侧一致；测试断言的字段名（`hot_files`/`files_changed`/`range.first`）与 envelope 一致 ✅
