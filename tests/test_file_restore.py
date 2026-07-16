"""Smoke test for webapi/file_restore.handle + envelope helpers.

Handler 从 main.py 搬出,行为不变。

PR-of-restore-edge-cases (2026-07-15): 端点现在恢复三类原本"无法撤销"的
状态 ——
1. 未跟踪新增文件 (``?? path``):直接从工作区 unlink。
2. 工作区删除 (`` D path``):``git checkout -- <file>``。
3. 已暂存删除 (``D  path``):``git checkout HEAD -- <file>``。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.project import state as _proj_state
from tools.webapi import file_restore

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


# ── E2E helpers (mirrors tests/test_git_workflow_e2e.py style) ───────


@pytest.fixture
def plugin() -> Any:
    from tests.conftest import _make_plugin

    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


def _make_request(body: dict[str, Any] | None) -> MagicMock:
    """Mock ``astrbot.api.web.request`` so handle()'s JSON parse returns body."""
    request_mock = MagicMock()
    request_mock.json = AsyncMock(return_value=body)
    return request_mock


def test_make_file_restore_empty_envelope_has_required_fields():
    """空 envelope 必须含 restored=False + reason 字段。"""
    env = file_restore._make_file_restore_empty_envelope(
        umo="test-umo", file="foo.py", reason="invalid_body", elapsed_ms=3
    )
    assert env["status"] == "ok"
    assert env["data"]["restored"] is False
    assert env["data"]["umo"] == "test-umo"
    assert env["data"]["file"] == "foo.py"
    assert env["data"]["reason"] == "invalid_body"
    assert env["data"]["scope"] == "unstaged"  # default
    assert env["data"]["elapsed_ms"] == 3


def test_make_file_restore_success_envelope_has_restored_true():
    """success envelope: restored=True, reason=None, scope 回显。"""
    env = file_restore._make_file_restore_success_envelope(
        umo="test-umo", file="bar.py", directory="/tmp/x", elapsed_ms=10, scope="staged"
    )
    assert env["data"]["restored"] is True
    assert env["data"]["reason"] is None
    assert env["data"]["scope"] == "staged"
    assert env["data"]["directory"] == "/tmp/x"
    assert env["data"]["worktree"] == "/tmp/x"


def test_validate_restore_file_rejects_absolute_path():
    """绝对路径被 _validate_restore_file 拒绝。"""
    from pathlib import Path

    target, err = file_restore._validate_restore_file("/etc/passwd", Path("/tmp"))
    assert target is None
    assert err == "path_unsafe"


def test_validate_restore_file_rejects_parent_traversal():
    """.. 段被拒绝。"""
    from pathlib import Path

    target, err = file_restore._validate_restore_file("../escape.py", Path("/tmp/repo"))
    assert target is None
    assert err == "path_unsafe"


def test_validate_restore_file_rejects_dot_git():
    """含 .git 段的路径被拒绝。"""
    from pathlib import Path

    target, err = file_restore._validate_restore_file(".git/config", Path("/tmp/repo"))
    assert target is None
    assert err == "path_unsafe"


# ── Edge-case restores (PR-of-restore-edge-cases 2026-07-15) ──────────
#
# 以上 sync 测试只覆盖 envelope helper + path-safety。
# 以下是针对 ``handle()`` 端的 4 个 git status 分支,确认此前被硬拒的三类
# (untracked / worktree-only delete / staged delete) 现在能正确恢复。
#
# 每个 test 都真实跑 git —— 单测 conftest 不需要 docker / 网络。
#
# 注:``from astrbot.api import web`` 是一个 module attr handle() 内部用的对象。
# ``monkeypatch.setattr("astrbot.api.web.request", mock)`` 让
# ``await web.request.json(default=None)`` 返回我们构造的 body。
# 这正是 AstrBot 服务运行时注入 Request 对象的方式(由框架在调用 handler
# 之前 setattr),测试在这里镜像同一行为。




async def test_restore_untracked_file_deletes_worktree_file(
    plugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``?? path`` — 撤销新增:从 worktree 直接 unlink 文件。"""
    _init_git_repo(tmp_path)
    new_file = tmp_path / "new.py"
    new_file.write_text("x = 1\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    monkeypatch.setattr(
        "astrbot.api.web.request",
        _make_request({"file": "new.py", "umo": "u:m"}),
    )

    result = await file_restore.handle(plugin)
    assert result["data"]["restored"] is True, result
    assert result["data"]["reason"] is None
    assert result["data"]["scope"] == "unstaged"
    assert not new_file.exists(), "untracked file should be removed after restore"


async def test_restore_worktree_deleted_file_recovers_from_index(
    plugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`` D path`` — ``git checkout -- <file>``:从 index 重新拉出已删文件。"""
    _init_git_repo(tmp_path)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("y = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add", "-q"], cwd=tmp_path, check=True)
    tracked.unlink()  # 用户手动 rm 工作区文件,但 index 还在记录
    _load_project(plugin, "u:m", str(tmp_path))

    monkeypatch.setattr(
        "astrbot.api.web.request",
        _make_request({"file": "tracked.py", "umo": "u:m"}),
    )

    result = await file_restore.handle(plugin)
    assert result["data"]["restored"] is True, result
    assert result["data"]["scope"] == "unstaged"
    assert tracked.exists(), "worktree-deleted file should be re-materialized from index"
    assert tracked.read_text(encoding="utf-8") == "y = 1\n"


async def test_restore_staged_deleted_file_recovers_from_head(
    plugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``D  path`` — ``git checkout HEAD -- <file>``:从 HEAD 重新拉出。"""
    _init_git_repo(tmp_path)
    tracked = tmp_path / "staged_del.py"
    tracked.write_text("z = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "staged_del.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add", "-q"], cwd=tmp_path, check=True)
    # ``git rm`` 同时 staged + 删除 worktree,porcelain 展示为 ``D  path``
    # (X='D' Y=' ')。我们的 is_truly_staged 分支会跑 checkout HEAD --。
    subprocess.run(["git", "rm", "staged_del.py"], cwd=tmp_path, check=True, capture_output=True)
    _load_project(plugin, "u:m", str(tmp_path))

    monkeypatch.setattr(
        "astrbot.api.web.request",
        _make_request({"file": "staged_del.py", "umo": "u:m"}),
    )

    result = await file_restore.handle(plugin)
    assert result["data"]["restored"] is True, result
    assert result["data"]["scope"] == "staged"
    assert tracked.exists(), "staged-deleted file should be recovered from HEAD"
    assert tracked.read_text(encoding="utf-8") == "z = 1\n"


async def test_restore_modified_worktree_unchanged_after_fix(
    plugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """回归保护:`` M path``(纯 worktree 改动)恢复行为不变。"""
    _init_git_repo(tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "mod.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "v1", "-q"], cwd=tmp_path, check=True)
    f.write_text("v2 — corrupt me\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    monkeypatch.setattr(
        "astrbot.api.web.request",
        _make_request({"file": "mod.py", "umo": "u:m"}),
    )

    result = await file_restore.handle(plugin)
    assert result["data"]["restored"] is True, result
    assert result["data"]["scope"] == "unstaged"
    assert f.read_text(encoding="utf-8") == "v1\n"
