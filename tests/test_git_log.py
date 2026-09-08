"""Tests for GET /spcode/git-log HTTP endpoint.

PR-2 of git workflow endpoints design.
Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §D
"""

from __future__ import annotations
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock
from tools.project import state as _proj_state
from tools.webapi import git_log as _gl

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin():
    return _make_plugin()


def _init_git_repo(path: Path, n_commits: int = 3) -> list[str]:
    """Init repo at path with n_commits; return SHAs oldest→newest."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    shas: list[str] = []
    for i in range(n_commits):
        (path / f"file{i}.txt").write_text(f"v{i}", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        msg = f"commit {i}: add file{i}.txt\n\nDetailed body for commit {i}"
        subprocess.run(["git", "commit", "-m", msg, "-q"], cwd=path, check=True)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        shas.append(sha)
    return shas


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


def _call_with_query(monkeypatch, plugin, **query):
    """Patch web.request with query dict then call handle()."""
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock(query=query))
    return _gl.handle(plugin)


# ──────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────


async def test_log_default_returns_commits(monkeypatch, plugin, tmp_path: Path):
    """默认 n=20,按 git log 默认顺序(最新优先)返回。"""
    shas = _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["loaded"] is True
    assert result["data"]["count"] == 3
    commits = result["data"]["commits"]
    assert len(commits) == 3
    # 最新优先
    assert commits[0]["sha"] == shas[-1]
    assert commits[0]["subject"].startswith("commit 2:")
    assert commits[0]["body"] is not None
    assert commits[0]["body"].startswith("Detailed body for commit 2")


async def test_log_n_param_limits_count(monkeypatch, plugin, tmp_path: Path):
    """n=2 限制返回数量。"""
    _init_git_repo(tmp_path, n_commits=5)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, n="2")
    assert result["data"]["count"] == 2
    assert len(result["data"]["commits"]) == 2


async def test_log_n_out_of_range_clamps_to_200(monkeypatch, plugin, tmp_path: Path):
    """n=1000 自动截到 200,不报错。"""
    _init_git_repo(tmp_path, n_commits=2)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, n="1000")
    assert result["data"]["count"] == 2


async def test_log_ref_filter(monkeypatch, plugin, tmp_path: Path):
    """ref=HEAD~2 只返回 1 个 commit。"""
    _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="HEAD~2")
    assert result["data"]["count"] == 1


async def test_log_path_filter(monkeypatch, plugin, tmp_path: Path):
    """path=file0.txt 只返回影响该文件的 commit。"""
    _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="file0.txt")
    assert result["data"]["count"] == 1


async def test_log_path_unsafe_rejected(monkeypatch, plugin, tmp_path: Path):
    """path 含 .. → path_unsafe。"""
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, path="../escape.py")
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "path_unsafe"


async def test_log_author_filter(monkeypatch, plugin, tmp_path: Path):
    """author=<pattern> 过滤。"""
    _init_git_repo(tmp_path, n_commits=2)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, author="t@")
    assert result["data"]["count"] == 2


async def test_log_invalid_param_too_long_ref(monkeypatch, plugin, tmp_path: Path):
    """ref 长度 > 512 → invalid_param。"""
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="x" * 600)
    assert result["data"]["reason"] == "invalid_param"


async def test_log_no_project_loaded(monkeypatch, plugin):
    """无 umo + state 空 → no_project_loaded。"""
    _proj_state.reset()
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "no_project_loaded"


async def test_log_worktree_invalid(monkeypatch, plugin, tmp_path: Path):
    """?worktree= 含 .. → worktree_invalid。"""
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    # umo / worktree 由 _wrap 注入;handle 接收 kwargs
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock())
    result = await _gl.handle(plugin, worktree="../other")
    assert result["data"]["reason"] == "worktree_invalid"


async def test_log_not_a_git_repo(monkeypatch, plugin, tmp_path: Path):
    """loaded 目录不是 git 仓库 → not_a_git_repo。"""
    non_git = tmp_path / "plain"
    non_git.mkdir()
    _load_project(plugin, "u:m", str(non_git))

    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_log_sha_format_full_fields(monkeypatch, plugin, tmp_path: Path):
    """每条 commit 必须有 sha/sha_short/author/committer/date/subject/body/parents/shortstat 全字段。"""
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    commit = result["data"]["commits"][0]
    assert len(commit["sha"]) == 40
    assert commit["sha_short"] == commit["sha"][:7]
    assert commit["author"]["name"] == "t"
    assert commit["author"]["email"] == "t@t"
    assert commit["committer"]["name"] == "t"
    assert "T" in commit["date"]
    assert commit["subject"].startswith("commit 0:")
    assert commit["body"] is not None
    assert commit["body"].startswith("Detailed body for commit 0")
    assert isinstance(commit["parents"], list)
    assert "files" in commit["shortstat"]
    assert "additions" in commit["shortstat"]
    assert "deletions" in commit["shortstat"]


async def test_log_feature_disabled(monkeypatch, plugin):
    """feature flag false → feature_disabled。"""
    plugin._config["agentsmd_enabled"] = False
    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["reason"] == "feature_disabled"


async def test_log_invalid_param_non_int_n(monkeypatch, plugin, tmp_path: Path):
    """n=abc → invalid_param。"""
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, n="abc")
    assert result["data"]["reason"] == "invalid_param"


# ──────────────────────────────────────────────────────────
# Regression: shortstat alignment with commits (2026-06-24)
#
# Previously ``_parse_log_shortstat`` appended a `{0,0,0}` entry for
# every non-blank line (commit header, author, date, message, file|
# stat-line), so ``shortstats[:n]`` truncated to mostly-zero entries
# and the per-index alignment to commits was off. Most commits then
# showed {files:0,additions:0,deletions:0} in the dashboard even
# though git knew the real stats.
# ──────────────────────────────────────────────────────────


def _init_repo_with_merge(path: Path) -> tuple[str, str]:
    """Init repo with: root commit → feature branch commit → main commit
    → merge commit (no-op merge). Return (merge_sha, feature_sha)."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    # WHY: ``git init`` on modern git defaults to ``main``, but older
    # git versions use ``master``. ``symbolic-ref HEAD`` works pre-commit
    # (unlike ``rev-parse --abbrev-ref HEAD`` which requires at least one
    # commit) and returns ``refs/heads/<branch>`` whose last segment is the
    # branch name.
    initial_branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Root commit on the default branch
    (path / "main.txt").write_text("main-v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "main: init", "-q"], cwd=path, check=True)

    # Feature branch with 1 commit
    subprocess.run(["git", "checkout", "-q", "-b", "feat"], cwd=path, check=True)
    (path / "feat.txt").write_text("feature-A\nfeature-B\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add feat.txt", "-q"], cwd=path, check=True
    )
    feature_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Default branch gets a parallel commit
    subprocess.run(["git", "checkout", "-q", initial_branch], cwd=path, check=True)
    (path / "main.txt").write_text("main-v1\nmain-v2 line\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "main: extend", "-q"],
        cwd=path,
        check=True,
    )

    # Merge — default branch absorbs feat (no overlapping paths here)
    subprocess.run(
        ["git", "merge", "--no-ff", "feat", "-m", "Merge branch 'feat'", "-q"],
        cwd=path,
        check=True,
    )
    merge_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    return merge_sha, feature_sha


async def test_log_shortstat_aligned_with_commits(monkeypatch, plugin, tmp_path: Path):
    """端到端:含 merge 的仓库,每条 commit 的 shortstat 必须与 git 真值一致。

    这是 2026-06-24 dashboard "history 页面某些 commit 全 0" 的回归测试。
    """
    merge_sha, feature_sha = _init_repo_with_merge(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    assert result["data"]["loaded"] is True
    commits = result["data"]["commits"]
    assert len(commits) == 4  # root + feature + main-extend + merge

    by_sha = {c["sha"]: c for c in commits}

    # ── Merge commit: 永远没有 stat(diff 是 merge 算法生成的,git 不汇报)
    merge = by_sha[merge_sha]
    assert merge["subject"].startswith("Merge branch"), merge["subject"]
    assert merge["shortstat"] == {"files": 0, "additions": 0, "deletions": 0}, (
        f"merge shortstat should be zeros, got {merge['shortstat']}"
    )

    # ── Feature commit: 1 新文件 2 行 → {files:1, additions:2, deletions:0}
    feature = by_sha[feature_sha]
    assert feature["subject"] == "feat: add feat.txt"
    assert feature["shortstat"]["files"] == 1, feature["shortstat"]
    assert feature["shortstat"]["additions"] == 2, feature["shortstat"]
    assert feature["shortstat"]["deletions"] == 0, feature["shortstat"]

    # ── Main extend commit: git shortstat 对 main.txt 的实际改动是
    # ``1 file changed, 2 insertions(+), 1 deletion(-)`` (相对首个父算
    # diff,旧 ``main-v1`` 行的删除 + 新 ``main-v1`` + ``main-v2 line``)。
    # 不去断言具体数字,只断言非全 0 + 文件数 = 1。
    main_extend = next(c for c in commits if c["subject"] == "main: extend")
    assert main_extend["shortstat"]["files"] == 1, main_extend["shortstat"]
    assert main_extend["shortstat"]["additions"] >= 1, main_extend["shortstat"]

    # ── 关键回归断言:不是 merge 的 commit 至少有一个 insertions > 0
    # (alignment 不能错位,否则 stat 会全 0)
    non_merge = [c for c in commits if c["sha"] != merge_sha]
    assert any(c["shortstat"]["additions"] > 0 for c in non_merge), (
        f"all non-merge commits show zero additions — regression: {non_merge}"
    )

    # ── 关键回归断言:每个非 merge commit 的 stat 都**非全 0**
    # (防止 2026-06-24 bug 的最直接信号:之前几乎所有 commit 的 stat
    # 都是 {0,0,0})
    for c in non_merge:
        s = c["shortstat"]
        assert s != {"files": 0, "additions": 0, "deletions": 0}, (
            f"non-merge commit {c['sha_short']} ({c['subject']}) "
            f"has all-zero shortstat — alignment regression: {s}"
        )


# ──────────────────────────────────────────────────────────
# Regression: ETag must include query filter dimensions (2026-07-01)
#
# Bug 背景:
#   用户在前端 git log 页面搜索 author=elec (URL 多带 author 参数),
#   然后点击"重置"按钮 (URL 不再带 author)。
#   两次 URL 在 query string 维度上不同,但 git log 端点 ETag 算法
#   只基于 ``(head_sha, wt_mtime, idx_mtime)``, **不包含** author /
#   path / since / until / ref / n 等 query 参数。
#   → 重置时,如果前端带着 author=elec 请求时的 ETag 来,后端判定
#     ETag 相同 → 返回 304 空 body → 前端 UI 显示空(因为它没有
#     缓存"不带 author 的版本",只缓存了"带 author 的版本")。
#
# 修复方向: ETag 必须把 query filter 维度纳入,这样 author 变化
# 一定导致 ETag 变化 → 304 误判被消除。
# ──────────────────────────────────────────────────────────


async def test_log_etag_changes_when_author_filter_changes(
    monkeypatch, plugin, tmp_path: Path
):
    """author filter 变 → ETag 必须变(否则重置 filter 会拿到 304 空 body)。

    设置 TTL=0 强制每次重算,清空 in-memory cache 避免跨测试污染。
    """
    from tools.webapi import git_log as _m
    from astrbot.api import web

    _init_git_repo(tmp_path, n_commits=2)
    _load_project(plugin, "u:m", str(tmp_path))

    _m._LOG_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_LOG_ETAG_TTL", 0.0)

    # 1) 初次: 不带 author
    monkeypatch.setattr(web, "request", make_web_request_mock(query={}))
    r1 = await _gl.handle(plugin)
    etag_default = r1.headers.get("etag")
    assert etag_default, f"first response missing ETag: {dict(r1.headers)}"

    # 2) 带 author=elec 过滤
    monkeypatch.setattr(web, "request", make_web_request_mock(query={"author": "elec"}))
    r2 = await _gl.handle(plugin)
    etag_author = r2.headers.get("etag")
    assert etag_author

    # 关键断言: ETag 必须不同
    assert etag_author != etag_default, (
        f"ETag must differ when author filter changes (304 staleness bug): "
        f"default={etag_default!r}, author=elec={etag_author!r}"
    )


async def test_log_etag_changes_when_path_filter_changes(
    monkeypatch, plugin, tmp_path: Path
):
    """path filter 变 → ETag 必须变。"""
    from tools.webapi import git_log as _m
    from astrbot.api import web

    _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    _m._LOG_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_LOG_ETAG_TTL", 0.0)

    monkeypatch.setattr(web, "request", make_web_request_mock(query={}))
    r1 = await _gl.handle(plugin)
    etag_default = r1.headers.get("etag")

    monkeypatch.setattr(
        web, "request", make_web_request_mock(query={"path": "file0.txt"})
    )
    r2 = await _gl.handle(plugin)
    etag_path = r2.headers.get("etag")

    assert etag_path != etag_default, (
        f"ETag must differ when path filter changes: "
        f"default={etag_default!r}, path=file0.txt={etag_path!r}"
    )


async def test_log_etag_changes_when_ref_filter_changes(
    monkeypatch, plugin, tmp_path: Path
):
    """ref 变 (HEAD → HEAD~2) → ETag 必须变。"""
    from tools.webapi import git_log as _m
    from astrbot.api import web

    _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    _m._LOG_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_LOG_ETAG_TTL", 0.0)

    monkeypatch.setattr(web, "request", make_web_request_mock(query={"ref": "HEAD"}))
    r1 = await _gl.handle(plugin)
    etag_head = r1.headers.get("etag")

    monkeypatch.setattr(web, "request", make_web_request_mock(query={"ref": "HEAD~2"}))
    r2 = await _gl.handle(plugin)
    etag_head2 = r2.headers.get("etag")

    assert etag_head != etag_head2, (
        f"ETag must differ when ref filter changes: "
        f"HEAD={etag_head!r}, HEAD~2={etag_head2!r}"
    )


async def test_log_reset_author_filter_returns_200_not_304(
    monkeypatch, plugin, tmp_path: Path
):
    """端到端: 重置 author filter → 带 author=elec 的 ETag → 必须 200 + 完整历史。

    这是用户报告的 bug 场景:
      - 用户搜索 author=elec
      - 点击"重置" (URL 不再带 author)
      - 前端带着 author=elec 请求时的 ETag (或默认 URL 的 ETag,但默认
        URL 还没被加载过,所以带 author 的 ETag)
      - 后端必须返回 200 + 完整 commits,不能是 304 空
    """
    from tools.webapi import git_log as _m
    from astrbot.api import web

    _init_git_repo(tmp_path, n_commits=2)
    _load_project(plugin, "u:m", str(tmp_path))

    _m._LOG_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_LOG_ETAG_TTL", 0.0)

    # 1) 用户搜索 author=elec
    monkeypatch.setattr(web, "request", make_web_request_mock(query={"author": "elec"}))
    r1 = await _gl.handle(plugin)
    etag_author = r1.headers.get("etag")
    assert r1.status_code == 200
    assert r1["data"]["count"] >= 0  # 任意数量,elec 仓库无此作者

    # 2) 重置:URL 不带 author,带 author 请求时的 ETag
    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(
            query={},
            headers={"If-None-Match": etag_author},
        ),
    )
    r2 = await _gl.handle(plugin)

    # 关键断言: 必须 200, 不能 304
    assert r2.status_code == 200, (
        f"Reset filter should return 200, got {r2.status_code} — "
        f"ETag from author=elec request incorrectly matched default "
        f"request (304 staleness bug)"
    )
    # 必须有完整历史
    assert r2["data"]["count"] == 2, (
        f"Reset filter should show full history (2 commits), "
        f"got {r2['data']['count']}: {r2['data']['commits']}"
    )


async def test_log_shortstat_values_nonzero_for_simple_repo(
    monkeypatch,
    plugin,
    tmp_path: Path,
):
    """简单 3 commit 仓库:每条 commit 的 additions 必须 > 0(校验现有断言太弱)。

    这是 ``test_log_sha_format_full_fields`` 的强化版 —— 旧测试只断言 keys
    存在,导致 parser 的错位 bug 一直未被发现。
    """
    _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    commits = result["data"]["commits"]
    assert len(commits) == 3
    # 3 个 commit 都改了文件(additions > 0)
    for i, c in enumerate(commits):
        assert c["shortstat"]["files"] >= 1, (
            f"commit {i} ({c['sha_short']}) files should be >= 1, "
            f"got {c['shortstat']} — shortstat alignment regression"
        )
        assert c["shortstat"]["additions"] >= 1, (
            f"commit {i} ({c['sha_short']}) additions should be >= 1, "
            f"got {c['shortstat']} — shortstat alignment regression"
        )


async def test_log_since_until_bare_datetime_accepted(
    monkeypatch, plugin, tmp_path: Path
):
    """Bare `YYYY-MM-DDTHH:MM:SS` (no timezone) must pass validation.

    2026-07-18 relaxation (Option A): the inner tz group of iso_date_re
    became optional so the stats panel's day-click filter (which sends
    local-time strings without offset) is not rejected with invalid_param.
    Git parses bare datetimes as local time.
    """
    _init_git_repo(tmp_path, n_commits=2)
    _load_project(plugin, "u:m", str(tmp_path))

    from astrbot.api import web

    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(
            query={
                "since": "2026-07-11T00:00:00",
                "until": "2026-07-11T23:59:59",
            }
        ),
    )
    result = await _gl.handle(plugin)
    # Must NOT be invalid_param; repo has commits so a normal 200 flow
    # (possibly with 0 matching commits) is expected.
    assert result["data"]["reason"] != "invalid_param"


# ──────────────────────────────────────────────────────────
# Task A1 (2026-09-08): grep commit-message filter
# ──────────────────────────────────────────────────────────


async def test_log_grep_matches_subject_and_body(monkeypatch, plugin, tmp_path: Path):
    """grep 匹配提交信息(子串,忽略大小写,含 body)。"""
    _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    # subject 命中:"commit 1:"
    result = await _call_with_query(monkeypatch, plugin, grep="COMMIT 1:")
    assert result["data"]["count"] == 1
    assert result["data"]["commits"][0]["subject"].startswith("commit 1:")

    # body 命中:"Detailed body for commit 2"
    result = await _call_with_query(monkeypatch, plugin, grep="detailed body for commit 2")
    assert result["data"]["count"] == 1
    assert result["data"]["commits"][0]["subject"].startswith("commit 2:")


async def test_log_grep_no_match_returns_empty(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path, n_commits=2)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, grep="no-such-text-xyz")
    assert result["data"]["success"] is True
    assert result["data"]["count"] == 0
    assert result["data"]["commits"] == []


async def test_log_grep_too_long_invalid_param(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, grep="x" * 513)
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_param"


async def test_log_grep_author_flag_interaction(monkeypatch, plugin, tmp_path: Path):
    """文档化行为:grep 的 -i / --fixed-strings 是 git 全局标志,会作用于 --author。

    单独传 author 时按 BRE 正则匹配(邮箱 t@t 命中 "t.");
    叠加 grep 后 --fixed-strings 全局生效,author 退化为字面子串 → 不命中。
    """
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    regex_only = await _call_with_query(monkeypatch, plugin, author="t.")
    assert regex_only["data"]["count"] == 1

    combined = await _call_with_query(
        monkeypatch, plugin, author="t.", grep="commit 0:"
    )
    assert combined["data"]["count"] == 0


# ──────────────────────────────────────────────────────────
# Task A2 (2026-09-08): hash-like ref 规范化与错误映射
# ──────────────────────────────────────────────────────────


async def test_log_short_sha_ref_resolves(monkeypatch, plugin, tmp_path: Path):
    """缩写 SHA 作为 ref → 以该提交为起点,并回显 resolved_ref。"""
    shas = _init_git_repo(tmp_path, n_commits=3)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref=shas[1][:8])
    assert result["data"]["success"] is True
    assert result["data"]["resolved_ref"] == shas[1]
    assert result["data"]["commits"][0]["sha"] == shas[1]


async def test_log_unknown_sha_ref_not_found(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="deadbeef")
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "ref_not_found"


async def test_log_ref_dash_prefix_invalid_param(monkeypatch, plugin, tmp_path: Path):
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="--all")
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_param"


# ──────────────────────────────────────────────────────────
# Task A3 (2026-09-08): tags 映射注入 + ETag 纳入 refs/tags
# ──────────────────────────────────────────────────────────


async def test_log_commits_carry_tags(monkeypatch, plugin, tmp_path: Path):
    """tag 指向的 commit 带 tags 字段;无 tag 的 commit 为 []。"""
    shas = _init_git_repo(tmp_path, n_commits=2)
    subprocess.run(["git", "tag", "v0.1.0", shas[0]], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "tag", "-a", "v0.2.0", "-m", "release", shas[1]],
        cwd=tmp_path,
        check=True,
    )
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin)
    by_sha = {c["sha"]: c["tags"] for c in result["data"]["commits"]}
    assert by_sha[shas[0]] == ["v0.1.0"]
    assert by_sha[shas[1]] == ["v0.2.0"]


async def test_log_etag_changes_when_tag_added(monkeypatch, plugin, tmp_path: Path):
    """新建 tag 不改 HEAD/index,但 ETag 必须变化(不能 304 复用旧 tags)。"""
    shas = _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    first = await _call_with_query(monkeypatch, plugin)
    etag1 = first.headers["ETag"]
    assert first["data"]["commits"][0]["tags"] == []

    subprocess.run(["git", "tag", "v9.9.9", shas[0]], cwd=tmp_path, check=True)

    from astrbot.api import web

    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(query={}, headers={"If-None-Match": etag1}),
    )
    second = await _gl.handle(plugin)
    assert second["data"]["commits"][0]["tags"] == ["v9.9.9"]


# ──────────────────────────────────────────────────────────
# Final-fix (2026-09-08): 打错的 ref ≠ ref_ambiguous
# ──────────────────────────────────────────────────────────
# 背景:git 对**任何**无法解析的 revision 都打印
# ``fatal: ambiguous argument '<x>': unknown revision or path not in the
# working tree.``,旧实现用 ``"ambiguous" in stderr`` 判定,把所有打错的
# 分支 / tag 名都误报成 ref_ambiguous。真正的短对象 id 歧义只会来自
# ``short object ID <x> is ambiguous``(短 id 前缀匹配到多个对象)。


async def test_log_nonexistent_branch_ref_not_found(
    monkeypatch, plugin, tmp_path: Path
):
    """不存在的分支名 → ref_not_found(而非 ref_ambiguous)。"""
    _init_git_repo(tmp_path, n_commits=1)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _call_with_query(monkeypatch, plugin, ref="no-such-branch")
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "ref_not_found"


# 以下两条 stderr 均为 git 2.43.0.windows.1 实测原文:
#   1) ``git log no-such-branch`` —— 普通的 revision 解析失败;
#   2) ``git log 4d44`` —— 两个 blob 共享 4 位前缀,git 先报短 id 歧义,
#      再回退到 ambiguous argument。顺序不变量:短 id 歧义判定必须在前。
_AMBIGUOUS_ARGUMENT_STDERR = (
    "fatal: ambiguous argument 'no-such-branch': unknown revision or path "
    "not in the working tree.\n"
    "Use '--' to separate paths from revisions, like this:\n"
    "'git <command> [<revision>...] -- [<file>...]'\n"
)
_SHORT_OBJECT_ID_AMBIGUOUS_STDERR = (
    "error: short object ID 4d44 is ambiguous\n"
    "hint: The candidates are:\n"
    "hint:   4d44b82 blob\n"
    "hint:   4d44f90 blob\n"
    "fatal: ambiguous argument '4d44': unknown revision or path not in the "
    "working tree.\n"
    "Use '--' to separate paths from revisions, like this:\n"
    "'git <command> [<revision>...] -- [<file>...]'\n"
)


async def test_classify_log_failure_unknown_revision_is_not_found():
    """``ambiguous argument ... unknown revision`` → ref_not_found。"""
    assert _gl._classify_log_failure(_AMBIGUOUS_ARGUMENT_STDERR) == "ref_not_found"


async def test_classify_log_failure_short_object_id_is_ambiguous():
    """只有 ``short object ID ... is ambiguous`` → ref_ambiguous。"""
    assert (
        _gl._classify_log_failure(_SHORT_OBJECT_ID_AMBIGUOUS_STDERR)
        == "ref_ambiguous"
    )


async def test_classify_log_failure_empty_repository_wins():
    """empty_repository 判定优先于其余 stderr 家族。"""
    stderr = "fatal: your current branch 'main' does not have any commits yet"
    assert _gl._classify_log_failure(stderr) == "empty_repository"


async def test_classify_log_failure_fallback_git_error():
    """无法归类 → git_error 兜底。"""
    assert _gl._classify_log_failure("fatal: unrelated failure") == "git_error"
