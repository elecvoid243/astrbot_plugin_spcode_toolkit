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
    raw = "@@STATS@@\x002026-07-10T10:00:00+08:00\n5\t2\tsrc/a.py\n3\t0\tREADME.md\n\n"
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
    assert commits[1]["files"] == [{"path": "a.py", "additions": 1, "deletions": 1}]


async def test_parse_binary_file_counts_touch_with_zero_lines():
    raw = "@@STATS@@\x002026-07-10T10:00:00+08:00\n-\t-\tlogo.png\n"
    commits = _gs._parse_stats_log_output(raw)
    assert commits[0]["files"] == [{"path": "logo.png", "additions": 0, "deletions": 0}]


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
    _commit(tmp_path, {"c.py": "y\n"}, "2026-07-12T10:00:00+08:00", message="main-work")
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


async def test_handle_rename_counted_as_delete_add(monkeypatch, plugin, tmp_path: Path):
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
        _commit(tmp_path, {"f.py": f"{i}\n"}, f"2026-07-{10 + i}T10:00:00+08:00")
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, query={"max_commits": "2"})
    data = result["data"]
    assert data["truncated"] is True
    assert data["max_commits"] == 2
    assert data["totals"]["commits"] == 2
    assert data["range"]["first"] == "2026-07-11"  # 最老一条被丢弃


async def test_handle_since_until_passthrough(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path)
    for i in range(3):
        _commit(tmp_path, {"f.py": f"{i}\n"}, f"2026-07-{10 + i}T10:00:00+08:00")
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

    r2 = await _call_with_query(monkeypatch, plugin, headers={"If-None-Match": etag})
    assert r2.status_code == 304
