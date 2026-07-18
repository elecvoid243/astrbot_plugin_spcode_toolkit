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