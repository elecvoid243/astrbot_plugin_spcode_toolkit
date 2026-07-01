"""Tests for POST /spcode/git-commit HTTP endpoint.

PR-5 of git workflow endpoints design.
Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §E
"""

from __future__ import annotations
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import git_commit as _gc

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin():
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _stage_change(repo: Path, fname: str = "new.py", content: str = "x") -> None:
    (repo / fname).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", fname], cwd=repo, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


def _commit(
    plugin,
    body: dict[str, Any],
    *,
    umo: str | None = None,
    worktree: str | None = None,
):
    return _gc.handle(
        plugin,
        body=body,
        umo=umo,
        worktree=worktree,
    )


# ──────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────


async def test_commit_with_staged_changes(plugin, tmp_path: Path):
    """有 staged 改动 + message → 成功,返回 sha + committed_count。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path, "a.py", "a")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": "feat: add a.py"}, umo="u:m")
    assert result["data"]["committed"] is True
    assert result["data"]["reason"] is None
    assert isinstance(result["data"]["sha"], str)
    assert len(result["data"]["sha"]) == 40
    assert result["data"]["committed_count"] >= 1
    assert "a.py" in result["data"]["files"]

    # 验证 git 实际有 commit
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "feat: add a.py" in log.stdout


async def test_commit_message_empty(plugin, tmp_path: Path):
    """message="" → invalid_message。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": ""}, umo="u:m")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "invalid_message"


async def test_commit_message_missing(plugin, tmp_path: Path):
    """没给 message → invalid_message。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {}, umo="u:m")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "invalid_message"


async def test_commit_message_too_long(plugin, tmp_path: Path):
    """message > 8192 chars → invalid_message。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": "x" * 9000}, umo="u:m")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "invalid_message"


async def test_commit_message_not_string(plugin, tmp_path: Path):
    """message 不是 str(数字) → invalid_message。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": 123}, umo="u:m")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "invalid_message"


async def test_commit_invalid_body_not_dict(plugin, tmp_path: Path):
    """body 不是 dict → invalid_body。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, "not a dict", umo="u:m")  # type: ignore[arg-type]
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "invalid_body"


async def test_commit_no_staged_changes(plugin, tmp_path: Path):
    """没有 staged 改动 → nothing_to_commit。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": "msg"}, umo="u:m")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "nothing_to_commit"


async def test_commit_no_project_loaded(plugin):
    """state 空 → no_project_loaded。"""
    _proj_state.reset()
    result = await _commit(plugin, {"message": "msg"})
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "no_project_loaded"


async def test_commit_feature_disabled(plugin, tmp_path: Path):
    """feature flag false → feature_disabled。"""
    plugin._config["agentsmd_enabled"] = False
    result = await _commit(plugin, {"message": "msg"})
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "feature_disabled"


async def test_commit_not_a_git_repo(plugin, tmp_path: Path):
    """loaded 不是 git repo → not_a_git_repo。"""
    non_git = tmp_path / "plain"
    non_git.mkdir()
    _load_project(plugin, "u:m", str(non_git))

    result = await _commit(plugin, {"message": "msg"}, umo="u:m")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "not_a_git_repo"


async def test_commit_hook_rejected(plugin, tmp_path: Path):
    """pre-commit hook 拒绝 → hook_rejected。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    # 安装 pre-commit hook,输出 stderr 并失败(关键:必须写 stderr,
    # 否则 _classify_commit_error 无法识别)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook = hooks_dir / "pre-commit"
    hook.write_text(
        "#!/bin/sh\necho 'pre-commit hook failed: please lint first' >&2\nexit 1\n",
        encoding="utf-8",
    )
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": "should fail"}, umo="u:m")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "hook_rejected"


async def test_commit_user_identity_not_set(plugin, tmp_path: Path):
    """没有 user.email/name → identity_not_set(常见错误)。

    通过 monkeypatch 把 ``GIT_AUTHOR_NAME`` 强制设为空,模拟身份未设。
    """
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    # 真实的 git 会从 global config 拿到 user.* (测试机可能配了)。
    # 这里通过 monkeypatch env 来强制 empty ident,触发 git 的错误路径。
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("GIT_AUTHOR_NAME", "")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "")
        monkeypatch.setenv("GIT_COMMITTER_NAME", "")
        monkeypatch.setenv("GIT_COMMITTER_EMAIL", "")
        result = await _commit(plugin, {"message": "msg"}, umo="u:m")
    finally:
        monkeypatch.undo()

    assert result["data"]["committed"] is False
    assert result["data"]["reason"] in ("identity_not_set", "git_error")


async def test_commit_worktree_invalid(plugin, tmp_path: Path):
    """worktree 含 .. → worktree_invalid。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": "msg"}, umo="u:m", worktree="../other")
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "worktree_invalid"


async def test_commit_message_with_newlines_preserved(plugin, tmp_path: Path):
    """message 含 \\n\\n(标题+正文) → 原样保留。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))

    msg = "feat: title\n\nBody line 1\nBody line 2"
    result = await _commit(plugin, {"message": msg}, umo="u:m")
    assert result["data"]["committed"] is True

    # 验证 message 完整保留
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Body line 1" in log.stdout
    assert "Body line 2" in log.stdout


async def test_commit_files_empty_after_success(plugin, tmp_path: Path):
    """成功后 staged files 为空(commit 把 staged 转 committed,staged 应清空)。"""
    _init_git_repo(tmp_path)
    _stage_change(tmp_path, "x.py", "x")
    _load_project(plugin, "u:m", str(tmp_path))

    result = await _commit(plugin, {"message": "msg"}, umo="u:m")
    assert result["data"]["committed"] is True
    assert result["data"]["staged_count"] == 0
