"""Tests for POST /spcode/git-squash (2026-08-03).

Spec: docs/superpowers/specs/2026-08-03-git-squash-design.md (Astrbot repo)
Author: elecvoid243 @ 2026-08-03
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _make_plugin  # noqa: F401
from tools.project import state as _state
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_squash


def _run(coro):
    """Sync wrapper for async handler calls(同 test_git_revert.py)。"""
    return asyncio.run(coro)


@pytest.fixture
def squash_repo(tmp_path: Path):
    """4 个 commit 的仓库,注册为已加载项目。Yields (repo_path, umo)。"""
    repo = tmp_path / "squash_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    for i in range(1, 5):
        (repo / f"file{i}.txt").write_text(f"v{i}", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", f"file{i}.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", f"commit {i}"],
            check=True,
        )
    umo = "test:git-squash:1"
    _state.put(umo, {"directory": str(repo), "loaded_at": 1.0})
    yield repo, umo
    _state.pop(umo)


def _shas(repo: Path) -> list[str]:
    """全部 commit SHA,新→旧。"""
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _log_subjects(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--pretty=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


# ── body 校验 (4 cases) ──


def test_squash_body_none(squash_repo):
    _, umo = squash_repo
    plugin = _make_plugin()
    result = _run(git_squash.handle(plugin, umo=umo, body=None))
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_squash_single_commit_rejected(squash_repo):
    repo, umo = squash_repo
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": [_shas(repo)[0]], "message": "m"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_squash_duplicate_commits_rejected(squash_repo):
    repo, umo = squash_repo
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": [_shas(repo)[0], _shas(repo)[0]], "message": "m"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_squash_empty_message_rejected(squash_repo):
    repo, umo = squash_repo
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": _shas(repo)[:2], "message": "   "},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_MESSAGE


# ── 前置条件 (3 cases) ──


def test_squash_worktree_dirty(squash_repo):
    repo, umo = squash_repo
    (repo / "uncommitted.txt").write_text("x", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin, umo=umo, body={"commits": _shas(repo)[:2], "message": "m"}
        )
    )
    assert result["data"]["reason"] == ReasonCode.WORKTREE_DIRTY


def test_squash_commit_not_found(squash_repo):
    repo, umo = squash_repo
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": [_shas(repo)[0], "no-such-ref"], "message": "m"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.COMMIT_NOT_FOUND


def test_squash_no_project_loaded():
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(plugin, body={"commits": ["a", "b"], "message": "m"})
    )
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


# ── 连续性校验 (3 cases) ──


def test_squash_head_not_selected(squash_repo):
    repo, umo = squash_repo
    shas = _shas(repo)
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": [shas[1], shas[2]], "message": "m"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.HEAD_NOT_SELECTED


def test_squash_not_contiguous(squash_repo):
    repo, umo = squash_repo
    shas = _shas(repo)
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": [shas[0], shas[2]], "message": "m"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.NOT_CONTIGUOUS


def test_squash_root_commit_rejected(squash_repo):
    repo, umo = squash_repo
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": _shas(repo), "message": "m"},  # 全部 4 条
        )
    )
    assert result["data"]["reason"] == ReasonCode.ROOT_COMMIT


# ── happy path (3 cases) ──


def test_squash_top_two(squash_repo):
    """压缩顶部 2 条(乱序传入)→ 4 commits 变 3,树内容不变。"""
    repo, umo = squash_repo
    shas = _shas(repo)
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            # 故意乱序传入(后端按集合校验,不要求有序)
            body={"commits": [shas[1], shas[0]], "message": "combined"},
        )
    )
    data = result["data"]
    assert data["squashed"] is True
    assert data["squashed_count"] == 2
    assert data["old_head_sha"] == shas[0]
    assert len(data["new_sha"]) == 40
    assert data["message"] == "combined"
    assert "file4.txt" in data["files_touched"]
    # 历史:combined, commit 2, commit 1
    assert _log_subjects(repo) == ["combined", "commit 2", "commit 1"]
    # 树内容不变
    assert (repo / "file4.txt").read_text(encoding="utf-8") == "v4"


def test_squash_short_shas_accepted(squash_repo):
    """short sha 输入在集合比较前被解析为完整 SHA。"""
    repo, umo = squash_repo
    shas = _shas(repo)
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": [shas[0][:7], shas[1][:7]], "message": "m"},
        )
    )
    assert result["data"]["squashed"] is True


def test_squash_multiline_message_preserved(squash_repo):
    repo, umo = squash_repo
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={
                "commits": _shas(repo)[:3],
                "message": "combined\n\nbody text",
            },
        )
    )
    assert result["data"]["squashed"] is True
    body = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%B"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "combined" in body
    assert "body text" in body
    assert _log_subjects(repo) == ["combined", "commit 1"]


# ── hook 失败中间态 (1 case) ──


def test_squash_hook_failure_keeps_staged_changes(squash_repo):
    """pre-commit hook 拒绝:reset --soft 已生效,HEAD 在 oldest^,
    改动保留在暂存区(用户可手动补提交)。"""
    repo, umo = squash_repo
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(hook, 0o755)
    plugin = _make_plugin()
    result = _run(
        git_squash.handle(
            plugin,
            umo=umo,
            body={"commits": _shas(repo)[:2], "message": "m"},
        )
    )
    # 分类器未必能识别 hook 输出(git 不回显 "pre-commit hook" 字样),
    # 接受 hook_rejected 或 git_error;状态断言才是本 case 的重点。
    assert result["data"]["reason"] in (
        ReasonCode.HOOK_REJECTED,
        ReasonCode.GIT_ERROR,
    )
    assert result["data"]["squashed"] is False
    # HEAD 已移动到 commit 2(oldest=commit 3,base=commit 3^=commit 2)
    assert _log_subjects(repo) == ["commit 2", "commit 1"]
    # commit 3 + commit 4 的改动都仍在暂存区
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "A  file3.txt" in status
    assert "A  file4.txt" in status
