"""Tests for the POST /spcode/file-restore HTTP endpoint.

Real git is used (no subprocess mocking) via tmp_path fixtures. The handler
must be exercised through a constructed SPCodeToolkit instance using the
``plugin`` fixture (from conftest).

Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md
Author: elecvoid243 @ 2026-06-22
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# 启用 pytest-asyncio strict 模式
pytestmark = pytest.mark.asyncio

# sys.path setup(与 tests/test_git_diff.py 相同)
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
_PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402
from tests.conftest import _make_plugin  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ── Fixtures & helpers ───────────────────────────────────


@pytest.fixture
def plugin():
    """Per-test plugin instance."""
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    """Initialize a minimal git repo in tmp_path with one initial commit."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    """Inject a project into plugin._loaded_projects bypassing /project load."""
    plugin._loaded_projects[umo] = {
        "directory": str(directory),
        "loaded_at": time.time(),
    }


def _patch_post_body(monkeypatch, body: Any) -> None:
    """Mock astrbot.api.web.request with get_json() returning body."""
    from astrbot.api import web

    mock = MagicMock()
    mock.get_json = MagicMock(return_value=body)
    monkeypatch.setattr(web, "request", mock)


# ── T1: handler exists + invalid_body + missing_file ─────


async def test_restore_invalid_body_returns_invalid_body(plugin, monkeypatch):
    """body 不是 dict 时返回 invalid_body(无需 git 调用)。"""
    _patch_post_body(monkeypatch, body="not a dict")
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "invalid_body"
    assert data["file"] == ""


async def test_restore_missing_file_field_returns_missing_file(plugin, monkeypatch):
    """body 缺 file 字段时返回 missing_file。"""
    _patch_post_body(monkeypatch, body={"umo": "u:m"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "missing_file"


async def test_restore_empty_file_returns_missing_file(plugin, monkeypatch):
    """body file 字段是空字符串或仅空白时返回 missing_file。"""
    _patch_post_body(monkeypatch, body={"file": "   "})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "missing_file"


# ── T2: gating (no project / feature flag / directory / git probe) ────


async def test_restore_no_project_loaded_returns_no_project_loaded(
    plugin, monkeypatch
):
    """_loaded_projects 空时返回 no_project_loaded。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._loaded_projects.clear()
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "no_project_loaded"
    assert data["file"] == "main.py"


async def test_restore_umo_not_in_loaded_returns_no_project_loaded(
    plugin, monkeypatch
):
    """指定 umo 不在 _loaded_projects 中时返回 no_project_loaded。"""
    _patch_post_body(monkeypatch, body={"file": "main.py", "umo": "unknown:umo"})
    plugin._loaded_projects.clear()
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "no_project_loaded"


async def test_restore_feature_disabled_agentsmd(plugin, monkeypatch):
    """agentsmd_enabled=False 时返回 feature_disabled。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._config["agentsmd_enabled"] = False
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "feature_disabled"


async def test_restore_feature_disabled_codegraph(plugin, monkeypatch):
    """codegraph_enabled=False 时返回 feature_disabled。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._config["codegraph_enabled"] = False
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "feature_disabled"


async def test_restore_directory_missing(plugin, monkeypatch):
    """loaded 目录已被删除时返回 directory_missing。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    plugin._loaded_projects["u:m"] = {
        "directory": str(Path("/nonexistent/please/ignore/12345/abc")),
        "loaded_at": time.time(),
    }
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "directory_missing"


async def test_restore_not_a_git_repo(plugin, tmp_path, monkeypatch):
    """加载的目录不是 git 仓库时返回 not_a_git_repo。"""
    _patch_post_body(monkeypatch, body={"file": "main.py"})
    not_repo = tmp_path / "not_a_repo"
    not_repo.mkdir()
    plugin._loaded_projects["u:m"] = {
        "directory": str(not_repo),
        "loaded_at": time.time(),
    }
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "not_a_git_repo"


# ── T3: file 路径安全(4 步防御) ──────────────────────


async def test_restore_rejects_absolute_path(plugin, tmp_path, monkeypatch):
    """file 是绝对路径(以 / 开头)时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "/etc/passwd"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_windows_absolute_path(
    plugin, tmp_path, monkeypatch
):
    """file 是 Windows 盘符绝对路径时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "C:\\Windows\\system.ini"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_parent_traversal(plugin, tmp_path, monkeypatch):
    """file 含 .. 段时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "../foo.py"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_dot_git_path(plugin, tmp_path, monkeypatch):
    """file 指向 .git 内部时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": ".git/config"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_backslash_path(plugin, tmp_path, monkeypatch):
    """file 含反斜杠(Windows 风格路径分隔符)时返回 path_unsafe。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "src\\foo.py"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "path_unsafe"


async def test_restore_rejects_symlink_escape(plugin, tmp_path, monkeypatch):
    """file 是 symlink 且指向 worktree 外时返回 path_unsafe。

    Windows 10/11 默认禁止普通用户创建 symlink(WinError 1314),需要
    开发者模式或管理员权限。若 os.symlink 失败,跳过此测试(其他 6 个
    路径安全测试仍覆盖完整防御链)。
    """
    _init_git_repo(tmp_path)
    outside_dir = tmp_path.parent
    outside = outside_dir / f"outside_secret_{os.getpid()}.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        try:
            os.symlink(str(outside), tmp_path / "escape_link")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"无法创建 symlink(平台/权限限制): {exc}")
        subprocess.run(["git", "add", "-N", "escape_link"], cwd=tmp_path, check=True)
        _load_project(plugin, "u:m", str(tmp_path))
        _patch_post_body(monkeypatch, body={"file": "escape_link"})
        result = await plugin.handle_post_file_restore()
        assert result["data"]["reason"] == "path_unsafe"
    finally:
        try:
            (tmp_path / "escape_link").unlink()
        except FileNotFoundError:
            pass
        try:
            outside.unlink()
        except FileNotFoundError:
            pass


async def test_restore_file_not_found(plugin, tmp_path, monkeypatch):
    """file 解析后路径不存在时返回 file_not_found。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "does_not_exist.py"})
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "file_not_found"


# ── T4: git status 预检 + git checkout + 成功路径 ────────


async def test_restore_not_modified_returns_not_modified(
    plugin, tmp_path, monkeypatch
):
    """文件无改动时返回 not_modified(无可恢复内容)。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "README.md"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "not_modified"


async def test_restore_untracked_file_returns_untracked(
    plugin, tmp_path, monkeypatch
):
    """未 ``git add -N`` 的新文件返回 untracked_file,stderr 含 git 输出。"""
    _init_git_repo(tmp_path)
    (tmp_path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "new.py"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is False
    assert data["reason"] == "untracked_file"
    assert data["stderr"] != ""  # git status --porcelain 输出


async def test_restore_modifies_file_back_to_index(
    plugin, tmp_path, monkeypatch
):
    """修改文件后 restore,内容真的回到 HEAD。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified content", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "README.md"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is True
    assert data["reason"] is None
    assert data["file"] == "README.md"
    # 文件内容真的被还原到 HEAD
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "init"


async def test_restore_intent_to_add_file(plugin, tmp_path, monkeypatch):
    """``git add -N new.py`` 后写内容,restore 取消新增意图。"""
    _init_git_repo(tmp_path)
    (tmp_path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "new.py"], cwd=tmp_path, check=True)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"file": "new.py"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is True
    # 新增意图被取消(文件重新变回 untracked 但内容仍在)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout
    assert "new.py" in status


async def test_restore_response_envelope_shape(plugin, tmp_path, monkeypatch):
    """成功响应字段齐全且类型正确。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"umo": "u:m", "file": "README.md"})
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    # 必含字段
    for key in (
        "restored", "directory", "umo", "worktree", "file", "scope",
        "elapsed_ms", "stderr", "reason",
    ):
        assert key in data, f"missing key: {key}"
    # 类型
    assert data["restored"] is True
    assert isinstance(data["directory"], str)
    assert data["scope"] == "unstaged"
    assert isinstance(data["elapsed_ms"], int)


async def test_restore_logs_audit_trail(
    plugin, tmp_path, monkeypatch
):
    """成功路径有 INFO 级别审计日志(通过 monkeypatch 拦截 logger.info)。

    AstrBot 的 ``logger`` 来自 ``astrbot.api``,其内部包装了标准 logging,
    不会向 pytest 的 caplog 传播。因此用 monkeypatch 替换 ``info`` 方法,
    验证 handler 在成功路径调了一次 ``info("...file-restore...")``。
    """
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(monkeypatch, body={"umo": "u:m", "file": "README.md"})

    # 拦截 handler 模块的 logger.info
    import astrbot_plugin_spcode_toolkit.main as _m
    info_calls: list[str] = []
    original_info = _m.logger.info
    _m.logger.info = lambda msg, *args, **kwargs: info_calls.append(msg)
    try:
        await plugin.handle_post_file_restore()
    finally:
        _m.logger.info = original_info

    # 至少 1 条含 "file-restore" 标记的 INFO
    assert any("file-restore" in c for c in info_calls), info_calls


# ── T5: worktree 6 步防御 + git_path 配置 ────────────


async def test_restore_with_worktree_param_succeeds(
    plugin, tmp_path, monkeypatch
):
    """合法 worktree + file,restore 在 worktree 内执行。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(
        monkeypatch, body={"worktree": str(tmp_path), "file": "README.md"}
    )
    result = await plugin.handle_post_file_restore()
    data = result["data"]
    assert data["restored"] is True
    assert data["worktree"] == str(tmp_path)


async def test_restore_worktree_param_cross_repo_rejected(
    plugin, tmp_path, monkeypatch
):
    """?worktree= 指向其它 git 仓库(跨 repo)时,被 worktree 6 步防御拒绝。"""
    _init_git_repo(tmp_path)
    # 创建另一个独立 repo
    other = tmp_path / "other_repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    _load_project(plugin, "u:m", str(tmp_path))
    _patch_post_body(
        monkeypatch, body={"worktree": str(other), "file": "README.md"}
    )
    result = await plugin.handle_post_file_restore()
    assert result["data"]["reason"] == "worktree_invalid"


async def test_restore_uses_configured_git_path(plugin):
    """git_path 配置后,plugin._git_binary() 返回配置值。"""
    plugin._config["git_path"] = "/custom/path/to/git.exe"
    assert plugin._git_binary() == "/custom/path/to/git.exe"


async def test_restore_falls_back_to_git_when_path_empty(plugin):
    """git_path=""(默认)时,plugin._git_binary() 返回 "git"。"""
    plugin._config["git_path"] = ""
    assert plugin._git_binary() == "git"
