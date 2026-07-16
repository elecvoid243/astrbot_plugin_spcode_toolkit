"""Tests for POST /spcode/git-init (v2.17.0).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.1
"""

import asyncio
import subprocess
from pathlib import Path

from tools.webapi import git_init
from tools.webapi._helpers import ReasonCode

from tests.conftest import _make_plugin  # noqa: F401 (re-export for clarity)


def _run(coro):
    """Sync wrapper for async handler calls."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── happy path ──────────────────────────────────────────────


def test_init_empty_dir_default_main(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    assert result["data"]["initialized"] is True
    assert result["data"]["initial_branch"] == "main"
    assert (empty_dir / ".git").is_dir()


def test_init_empty_dir_custom_branch(empty_dir):
    plugin = _make_plugin()
    result = _run(
        git_init.handle(
            plugin, body={"path": str(empty_dir), "initial_branch": "develop"}
        )
    )
    assert result["data"]["initialized"] is True
    assert result["data"]["initial_branch"] == "develop"


def test_init_bare(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir), "bare": True}))
    assert result["data"]["initialized"] is True
    assert result["data"]["bare"] is True


# ── body 校验 ───────────────────────────────────────────────


def test_init_body_none(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body=None))
    assert result["data"]["initialized"] is False
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_init_path_missing(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_path_not_string(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": 123}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_initial_branch_not_string(empty_dir):
    plugin = _make_plugin()
    result = _run(
        git_init.handle(plugin, body={"path": str(empty_dir), "initial_branch": []})
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


# ── 路径安全 ────────────────────────────────────────────────


def test_init_path_traversal(tmp_path):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": "../../../etc"}))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


def test_init_path_blacklisted(tmp_path):
    target = tmp_path / "win"
    target.mkdir()
    plugin = _make_plugin()
    plugin._config["file_remove_blacklist"] = [str(target)]
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


def test_init_path_empty_string(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": ""}))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


# ── 目录状态 ────────────────────────────────────────────────


def test_init_path_does_not_exist(tmp_path):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(tmp_path / "nope")}))
    assert result["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_path_is_a_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(f)}))
    assert result["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_directory_not_empty(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "x.txt").write_text("x")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_already_a_git_repo(tmp_path):
    target = tmp_path / "already"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.ALREADY_A_GIT_REPO


# ── git init 失败 ──────────────────────────────────────────


def test_init_git_binary_unavailable(empty_dir, monkeypatch):
    plugin = _make_plugin()
    plugin._git_binary = lambda: "/nonexistent/git-binary-xyz"
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    # 可能 init_failed 或 git_unavailable,看哪条更具体
    assert result["data"]["reason"] in (
        ReasonCode.INIT_FAILED,
        ReasonCode.GIT_UNAVAILABLE,
    )


def test_init_subprocess_exception(empty_dir, monkeypatch):
    plugin = _make_plugin()
    from tools.webapi import _helpers

    async def fake_run(*args, **kwargs):
        return {"ok": False, "returncode": 1, "stderr": "fatal: bad", "stdout": ""}

    monkeypatch.setattr(_helpers, "_run_git_async", fake_run)
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    assert result["data"]["reason"] == ReasonCode.INIT_FAILED
    assert "fatal: bad" in result["data"]["stderr"]


# ── 集成 ──────────────────────────────────────────────────


def test_init_then_git_status_works(empty_dir):
    """init 后,git status 应能正常工作。"""
    plugin = _make_plugin()
    _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    result = subprocess.run(
        ["git", "-C", str(empty_dir), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_init_then_validate_worktree_param_passes(empty_dir):
    """init 后,_validate_worktree_param 应能接受该路径作为 worktree candidate。

    WHY:loaded_dir 必须是 candidate 所属 git repo 的根(否则
    `_resolve_git_common_dir(loaded_dir)` 找不到 .git,直接抛异常,
    step 6 校验会以 worktree_invalid 拒绝 candidate)。
    """
    plugin = _make_plugin()
    _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    from tools._helpers import _validate_worktree_param

    validated, err = _validate_worktree_param("git", str(empty_dir), str(empty_dir))
    assert err is None
    assert Path(validated).resolve() == empty_dir.resolve()


# ── v2.17.1: force 标志 ────────────────────────────────────


def test_init_force_true_nonempty_dir_with_file(tmp_path):
    """force=true + 非空目录(含文件) -> 成功 init,文件保留 untracked。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": True}))
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True
    assert (target / ".git").is_dir()
    # 原文件保留
    assert (target / "README.md").read_text(encoding="utf-8") == "hello"


def test_init_force_true_nonempty_dir_with_subdirectory(tmp_path):
    """force=true + 非空目录(含子目录) -> 成功 init。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "src").mkdir()
    (target / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": True}))
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True


def test_init_force_true_still_rejects_already_git_repo(tmp_path):
    """force=true + 已有 .git -> 仍返回 already_a_git_repo(hard-ban)。"""
    target = tmp_path / "already"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": True}))
    assert result["data"]["initialized"] is False
    assert result["data"]["reason"] == ReasonCode.ALREADY_A_GIT_REPO


def test_init_force_true_still_rejects_path_not_directory(tmp_path):
    """force=true + 不存在路径 -> 仍返回 path_not_directory。"""
    plugin = _make_plugin()
    result = _run(
        git_init.handle(plugin, body={"path": str(tmp_path / "nope"), "force": True})
    )
    assert result["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_force_true_still_rejects_path_unsafe(tmp_path):
    """force=true + 含 .. 路径 -> 仍返回 path_unsafe。"""
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": "../../../etc", "force": True}))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


def test_init_force_true_bare_true_nonempty_dir(tmp_path):
    """force=true + bare=true + 非空目录 -> 成功 init bare repo。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(
        git_init.handle(plugin, body={"path": str(target), "force": True, "bare": True})
    )
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True
    assert result["data"]["bare"] is True


def test_init_force_true_empty_dir_still_works(empty_dir):
    """force=true + 空目录 -> 成功 init(与 force=false 一致)。"""
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir), "force": True}))
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True


def test_init_force_default_false_rejects_nonempty(tmp_path):
    """不传 force(默认 false) -> 非空目录仍被拒绝 - 向后兼容验证。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_force_explicit_false_rejects_nonempty(tmp_path):
    """显式 force=false -> 非空目录仍被拒绝。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": False}))
    assert result["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_force_string_type_rejected(tmp_path):
    """force='true'(字符串) -> invalid_param。"""
    target = tmp_path / "occupied"
    target.mkdir()
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": "true"}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_force_int_type_rejected(tmp_path):
    """force=1(int) -> invalid_param。"""
    target = tmp_path / "occupied"
    target.mkdir()
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": 1}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_force_null_type_rejected(tmp_path):
    """force=null(None) -> invalid_param(严格 bool 校验)。"""
    target = tmp_path / "occupied"
    target.mkdir()
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": None}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_force_true_response_echoes_force(tmp_path):
    """成功响应必须回显 force 字段(便于前端审计)。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "x.txt").write_text("x", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target), "force": True}))
    assert result["data"]["initialized"] is True
    assert "force" in result["data"]
    assert result["data"]["force"] is True
