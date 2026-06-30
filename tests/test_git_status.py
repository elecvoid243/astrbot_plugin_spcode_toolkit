"""Tests for GET /spcode/git-status HTTP endpoint (v2.13+).

纯函数解析器单元测试见 :mod:`tests.test_git_status_parsers`(避免
``@pytest.mark.asyncio`` 误标同步测试)。本文件只放 async 集成测试。

覆盖范围:
- Preflight 5 步: no_project_loaded / feature_disabled / directory_missing
  / not_a_git_repo / worktree_invalid
- Handler 集成(``tmp_path`` 跑真 git):
  - 空仓库(无 commits)→ branch=None, files=[]
  - 干净仓库(有 commits)→ branch=main, files=[], summary 全 0
  - 未暂存 / 已暂存 / 未跟踪 改动 → 各类计数正确
  - intent-to-add 特殊场景(scope=intent_to_add, 计入 unstaged)
  - Detached HEAD → branch=None
  - 有/无 upstream → upstream 字段切换(含 ahead/behind)
- ETag 缓存(同 git-log 模式)
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock
from tools.project import state as _proj_state
from tools.webapi import git_status as _gs

pytestmark = pytest.mark.asyncio


# ──────────────────────────────────────────────────────────
# Fixtures & helpers
# ──────────────────────────────────────────────────────────


@pytest.fixture
def plugin() -> Any:
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    """Init repo at path with an initial commit on ``main`` branch。"""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


def _call_handle(monkeypatch, plugin, **kwargs):
    """直接调 handle(),模拟 _wrap 注入 umo/worktree。"""
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock())
    return _gs.handle(plugin, **kwargs)


# ──────────────────────────────────────────────────────────
# Preflight — no git repo needed
# ──────────────────────────────────────────────────────────


class TestPreflight:
    async def test_no_project_loaded(self, monkeypatch, plugin) -> None:
        """无 umo + state 空 → no_project_loaded。"""
        _proj_state.reset()
        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is False
        assert result["data"]["reason"] == "no_project_loaded"

    async def test_feature_disabled(self, monkeypatch, plugin) -> None:
        """agentsmd_enabled = False → feature_disabled。"""
        _load_project(plugin, "u:m", str(Path.cwd()))
        plugin._config["agentsmd_enabled"] = False
        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is False
        assert result["data"]["reason"] == "feature_disabled"

    async def test_directory_missing(self, monkeypatch, plugin) -> None:
        """loaded 目录被删 → directory_missing。"""
        _load_project(plugin, "u:m", "/nonexistent/path/abc/123")
        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is False
        assert result["data"]["reason"] == "directory_missing"

    async def test_not_a_git_repo(self, monkeypatch, plugin, tmp_path: Path) -> None:
        """loaded 目录不是 git 仓库 → not_a_git_repo。"""
        plain = tmp_path / "plain"
        plain.mkdir()
        _load_project(plugin, "u:m", str(plain))
        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is False
        assert result["data"]["reason"] == "not_a_git_repo"

    async def test_worktree_invalid(self, monkeypatch, plugin, tmp_path: Path) -> None:
        """?worktree= 含 .. → worktree_invalid(由 preflight 第 3 步拦下)。"""
        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))
        result = await _call_handle(monkeypatch, plugin, worktree="../escape")
        assert result["data"]["loaded"] is False
        assert result["data"]["reason"] == "worktree_invalid"


# ──────────────────────────────────────────────────────────
# Handler integration — real git
# ──────────────────────────────────────────────────────────


class TestHandlerEmptyRepo:
    async def test_empty_repo_returns_null_branch(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """空仓库(无 commits)→ branch=None, files=[]。"""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is True
        assert result["data"]["branch"] is None
        assert result["data"]["upstream"] is None
        assert result["data"]["files"] == []
        assert result["data"]["summary"] == {
            "staged": 0,
            "unstaged": 0,
            "untracked": 0,
            "conflicts": 0,
            "total": 0,
        }


class TestHandlerCleanRepo:
    async def test_clean_repo_returns_main_no_files(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """干净仓库 → branch=main, files=[], summary 全 0。"""
        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is True
        assert result["data"]["branch"] == "main"
        # 无 upstream 推送(只是本地 init),应降级为 None
        assert result["data"]["upstream"] is None
        assert result["data"]["files"] == []
        assert result["data"]["summary"]["total"] == 0


class TestHandlerDirtyRepo:
    async def test_unstaged_only(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """修改已跟踪文件(未 git add)→ scope=unstaged, summary.unstaged=1。"""
        _init_git_repo(tmp_path)
        (tmp_path / "README.md").write_text("modified", encoding="utf-8")
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is True
        assert result["data"]["summary"]["unstaged"] == 1
        assert result["data"]["summary"]["staged"] == 0
        assert result["data"]["summary"]["untracked"] == 0
        f = result["data"]["files"][0]
        assert f["path"] == "README.md"
        assert f["scope"] == "unstaged"

    async def test_staged_only(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """git add 后无 worktree 改动 → scope=staged。"""
        _init_git_repo(tmp_path)
        (tmp_path / "README.md").write_text("modified", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["summary"]["staged"] == 1
        assert result["data"]["summary"]["unstaged"] == 0
        assert result["data"]["files"][0]["scope"] == "staged"

    async def test_untracked_file(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """新增未被 git add 的文件 → scope=untracked。"""
        _init_git_repo(tmp_path)
        (tmp_path / "new.txt").write_text("hello", encoding="utf-8")
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["summary"]["untracked"] == 1
        assert result["data"]["files"][0]["path"] == "new.txt"
        assert result["data"]["files"][0]["scope"] == "untracked"

    async def test_intent_to_add(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """``git add -N`` 意图标记 → scope=intent_to_add(计入 unstaged)。"""
        _init_git_repo(tmp_path)
        (tmp_path / "intent.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "-N", "intent.txt"], cwd=tmp_path, check=True)
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["summary"]["unstaged"] == 1
        assert result["data"]["summary"]["staged"] == 0
        assert result["data"]["files"][0]["scope"] == "intent_to_add"

    async def test_mixed_changes(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """staged + unstaged + untracked 三种状态各一,summary 计数正确。"""
        _init_git_repo(tmp_path)
        # staged: 编辑 README 后 add
        (tmp_path / "README.md").write_text("v2", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
        # unstaged: 再编辑一次 README
        (tmp_path / "README.md").write_text("v3", encoding="utf-8")
        # untracked
        (tmp_path / "fresh.txt").write_text("x", encoding="utf-8")
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        s = result["data"]["summary"]
        # README 同时 staged + worktree 改动 → modified_both(计入 staged)
        assert s["staged"] == 1
        assert s["untracked"] == 1
        assert s["total"] == 2

    async def test_detached_head(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """Detached HEAD(``git checkout SHA``)→ branch=None。"""
        _init_git_repo(tmp_path)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "checkout", "-q", sha], cwd=tmp_path, check=True)
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["loaded"] is True
        assert result["data"]["branch"] is None
        assert result["data"]["upstream"] is None


# ──────────────────────────────────────────────────────────
# Upstream tracking
# ──────────────────────────────────────────────────────────


class TestUpstream:
    async def test_no_upstream(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """本地新分支(未推送)→ upstream=None。"""
        _init_git_repo(tmp_path)
        subprocess.run(
            ["git", "checkout", "-q", "-b", "feature/x"], cwd=tmp_path, check=True,
        )
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["branch"] == "feature/x"
        assert result["data"]["upstream"] is None

    async def test_with_local_upstream(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """设置本地 upstream(``git branch --set-upstream-to``)→ upstream 字段填入。

        使用本地另一分支作 upstream,免去 bare remote 依赖。
        """
        _init_git_repo(tmp_path)
        # 创建 develop 分支并切回 main
        subprocess.run(["git", "checkout", "-q", "-b", "develop"], cwd=tmp_path, check=True)
        (tmp_path / "dev.txt").write_text("dev", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "dev", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)
        # main 比 develop 落后 1 个 commit;把 develop 设为 main 的 upstream
        subprocess.run(
            ["git", "branch", "--set-upstream-to=develop", "main"],
            cwd=tmp_path, check=True,
        )
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        assert result["data"]["branch"] == "main"
        assert result["data"]["upstream"] is not None
        assert result["data"]["upstream"]["branch"] == "develop"
        assert result["data"]["upstream"]["behind"] == 1
        assert result["data"]["upstream"]["ahead"] == 0

    async def test_ahead_of_upstream(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """ahead 计数:在 main 上提交,但 upstream 还指 develop(无新 commit)。"""
        _init_git_repo(tmp_path)
        # 创 develop 分支,内容与 main 一致(空 commit)
        subprocess.run(["git", "checkout", "-q", "-b", "develop"], cwd=tmp_path, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "branch", "--set-upstream-to=develop", "main"],
            cwd=tmp_path, check=True,
        )
        # main 上加 1 个 commit
        (tmp_path / "extra.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "extra", "-q"], cwd=tmp_path, check=True)
        _load_project(plugin, "u:m", str(tmp_path))

        result = await _call_handle(monkeypatch, plugin)
        upstream = result["data"]["upstream"]
        assert upstream is not None
        assert upstream["ahead"] == 1
        assert upstream["behind"] == 0


# ──────────────────────────────────────────────────────────
# ETag
# ──────────────────────────────────────────────────────────


class TestETag:
    async def test_returns_etag_header(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """首次调用返回 ETag 响应头。"""
        from astrbot.api import web

        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))
        monkeypatch.setattr(web, "request", make_web_request_mock())

        result = await _gs.handle(plugin)
        # _JSONResponseCompat 暴露 headers(继承 JSONResponse)
        assert "ETag" in result.headers
        assert result.headers["ETag"].startswith('W/"')
        assert result["data"]["loaded"] is True

    async def test_304_on_matching_if_none_match(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """第二次带正确 If-None-Match → 304 Not Modified。"""
        from astrbot.api import web

        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))

        # 第一次 → 拿 ETag
        monkeypatch.setattr(web, "request", make_web_request_mock())
        first = await _gs.handle(plugin)
        etag = first.headers["ETag"]

        # 第二次带 If-None-Match
        monkeypatch.setattr(
            web,
            "request",
            make_web_request_mock(headers={"If-None-Match": etag}),
        )
        second = await _gs.handle(plugin)
        # 304 响应:status_code=304, headers 含 ETag
        assert second.status_code == 304
        assert second.headers["ETag"] == etag

    # ──────────────────────────────────────────────────────────
    # v3.5 (2026-06-30) ETag staleness 修复: 4 个回归测试
    # 详细见 docs/superpowers/specs/2026-06-30-git-etag-staleness-fix.md
    # ──────────────────────────────────────────────────────────

    async def test_etag_changes_after_unstaged_edit(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """编辑 worktree 内文件 (不 git add) → ETag 必须变化。"""
        from astrbot.api import web

        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))

        _gs._STATUS_ETAG_CACHE.clear()
        monkeypatch.setattr(_gs, "_STATUS_ETAG_TTL", 0.0)

        monkeypatch.setattr(web, "request", make_web_request_mock())
        r1 = await _gs.handle(plugin)
        etag_before = r1.headers["ETag"]

        (tmp_path / "README.md").write_text("modified", encoding="utf-8")

        monkeypatch.setattr(
            web, "request",
            make_web_request_mock(headers={"If-None-Match": etag_before}),
        )
        r2 = await _gs.handle(plugin)
        assert r2.status_code == 200, (
            f"After unstaged edit, expected 200, got {r2.status_code} — "
            f"ETag stale: {etag_before!r}"
        )
        assert r2.headers["ETag"] != etag_before

    async def test_etag_changes_after_git_add(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """git add 后 → ETag 必须变化。"""
        from astrbot.api import web

        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))

        _gs._STATUS_ETAG_CACHE.clear()
        monkeypatch.setattr(_gs, "_STATUS_ETAG_TTL", 0.0)

        monkeypatch.setattr(web, "request", make_web_request_mock())
        r1 = await _gs.handle(plugin)
        etag_before = r1.headers["ETag"]

        (tmp_path / "new.txt").write_text("hi", encoding="utf-8")
        subprocess.run(["git", "add", "new.txt"], cwd=tmp_path, check=True)

        monkeypatch.setattr(
            web, "request",
            make_web_request_mock(headers={"If-None-Match": etag_before}),
        )
        r2 = await _gs.handle(plugin)
        assert r2.status_code == 200, (
            f"After git add, expected 200, got {r2.status_code}"
        )
        assert r2.headers["ETag"] != etag_before

    async def test_etag_changes_after_untracked_file(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """新增未跟踪文件 → ETag 必须变化。"""
        from astrbot.api import web

        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))

        _gs._STATUS_ETAG_CACHE.clear()
        monkeypatch.setattr(_gs, "_STATUS_ETAG_TTL", 0.0)

        monkeypatch.setattr(web, "request", make_web_request_mock())
        r1 = await _gs.handle(plugin)
        etag_before = r1.headers["ETag"]

        (tmp_path / "untracked.txt").write_text("u", encoding="utf-8")

        monkeypatch.setattr(
            web, "request",
            make_web_request_mock(headers={"If-None-Match": etag_before}),
        )
        r2 = await _gs.handle(plugin)
        assert r2.status_code == 200, (
            f"After untracked add, expected 200, got {r2.status_code}"
        )
        assert r2.headers["ETag"] != etag_before

    async def test_stale_etag_no_longer_304(
        self, monkeypatch, plugin, tmp_path: Path,
    ) -> None:
        """端到端: 编辑文件 → 带旧 ETag 请求 → 200 + 新文件列表 (不是 304)。"""
        from astrbot.api import web

        _init_git_repo(tmp_path)
        _load_project(plugin, "u:m", str(tmp_path))

        _gs._STATUS_ETAG_CACHE.clear()
        monkeypatch.setattr(_gs, "_STATUS_ETAG_TTL", 0.0)

        # 1) 首次
        monkeypatch.setattr(web, "request", make_web_request_mock())
        r1 = await _gs.handle(plugin)
        etag_before = r1.headers["ETag"]
        assert r1.status_code == 200

        # 2) 编辑 + 新增
        (tmp_path / "README.md").write_text("u", encoding="utf-8")
        (tmp_path / "untracked.txt").write_text("u", encoding="utf-8")

        # 3) 带旧 ETag → 必须 200 + 新文件
        monkeypatch.setattr(
            web, "request",
            make_web_request_mock(headers={"If-None-Match": etag_before}),
        )
        r2 = await _gs.handle(plugin)
        assert r2.status_code == 200, (
            f"Stale ETag must not cause 304 after worktree change. "
            f"Got {r2.status_code} — git-status ETag staleness regressed!"
        )
        paths = {f["path"] for f in r2["data"]["files"]}
        assert "README.md" in paths, f"files missing README.md: {paths}"
        assert "untracked.txt" in paths, f"files missing untracked.txt: {paths}"
