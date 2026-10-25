"""Tests for GET /spcode/git-diff/file + GET /spcode/git-diff/full.

v2.32.0 (2026-10-25) 渐进式加载配套:
- file 端点: 单文件按需 patch(≤256KB 截断 + truncated 标记),path 必须
  出现在当前 diff 的文件列表中(防任意文件探测),二进制文件直接标记。
- full 端点: 旧 git-diff 的完整行为(diff 1MB 上限 + files_changed +
  stat),唯一消费方是提交对话框的 AI commit message(scope=staged)。

Run: cd astrbot_plugin_spcode_toolkit && python -m pytest tests/test_git_diff_file.py -q
(与 test_git_diff.py 相同的 conftest fixture / web.request mock 体系。)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin, make_web_request_mock  # noqa: F401
from tools.project import state as _proj_state
from tools.webapi import git_diff as _gd

pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin():
    """Per-test plugin instance (conftest 提供 _make_plugin)."""
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.reset()
    _proj_state.put(umo, {"directory": directory, "loaded_at": 1.0})
    plugin.get_loaded_project = _proj_state.get


def _mock_request(monkeypatch, params: dict | None = None, headers: dict | None = None):
    from astrbot.api import web

    monkeypatch.setattr(
        web, "request", make_web_request_mock(params or {}, headers=headers)
    )


# ── file 端点 ────────────────────────────────────────────────────────


async def test_file_endpoint_returns_patch_for_changed_file(
    plugin, tmp_path, monkeypatch
):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified content\n", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_request(monkeypatch, {"path": "README.md"})

    result = await _gd.handle_file(plugin)
    data = result["data"]
    assert data["loaded"] is True
    assert data["path"] == "README.md"
    assert data["is_binary"] is False
    assert data["truncated"] is False
    # patch 文本包含改动行
    assert "modified content" in data["diff"]
    assert data["diff"].startswith("diff --git ")


async def test_file_endpoint_scope_staged_returns_cached_patch(
    plugin, tmp_path, monkeypatch
):
    """scope=staged → git diff --cached -- <path>,返回已暂存内容。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("staged change\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("staged + extra edit\n", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_request(monkeypatch, {"path": "README.md", "scope": "staged"})

    result = await _gd.handle_file(plugin)
    data = result["data"]
    assert data["scope"] == "staged"
    # staged patch 显示原始 staged 内容,不含后续 unstaged edit
    assert "staged change" in data["diff"]
    assert "extra edit" not in data["diff"]


async def test_file_endpoint_rejects_missing_path_param(plugin, monkeypatch):
    _load_project(plugin, "test:umo", "whatever")
    _mock_request(monkeypatch, {})

    result = await _gd.handle_file(plugin)
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "path_required"


async def test_file_endpoint_rejects_path_not_in_diff(plugin, tmp_path, monkeypatch):
    """path 不在当前 diff 的文件列表中 → path_not_in_diff(防任意文件探测)。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("untouched\n", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_request(monkeypatch, {"path": "secret.txt"})

    result = await _gd.handle_file(plugin)
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "path_not_in_diff"


async def test_file_endpoint_marks_binary_file(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02\x03" * 32)
    # intent-to-add:让二进制文件进入 git diff 的文件列表(numstat "-"-" → is_binary)
    subprocess.run(["git", "add", "-N", "image.bin"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_request(monkeypatch, {"path": "image.bin"})

    result = await _gd.handle_file(plugin)
    data = result["data"]
    assert data["is_binary"] is True
    assert data["diff"] is None


async def test_file_endpoint_truncates_large_patch(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    big = "x" * (256 * 1024 + 50_000)  # ~310KB 单文件
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    # intent-to-add 让 untracked 文件进入 git diff
    subprocess.run(["git", "add", "-N", "big.txt"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_request(monkeypatch, {"path": "big.txt"})

    result = await _gd.handle_file(plugin)
    data = result["data"]
    assert data["truncated"] is True
    assert data["truncated_at_bytes"] == 256 * 1024
    assert data["max_bytes"] == 256 * 1024
    assert len(data["diff"].encode("utf-8")) <= 256 * 1024


async def test_file_endpoint_no_etag_304(plugin, tmp_path, monkeypatch):
    """file 端点不做 If-None-Match 短路:带匹配 ETag 仍返回 200 + patch。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified\n", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))

    # 先走一次列表端点拿 etag
    _mock_request(monkeypatch, {})
    r1 = await _gd.handle(plugin)
    etag = r1.headers.get("etag")
    assert etag

    _mock_request(
        monkeypatch,
        {"path": "README.md"},
        headers={"If-None-Match": etag},
    )
    result = await _gd.handle_file(plugin)
    assert result["data"]["loaded"] is True
    assert result["data"]["diff"]


# ── full 端点 ────────────────────────────────────────────────────────


async def test_full_endpoint_returns_legacy_contract(
    plugin, tmp_path, monkeypatch
):
    """full 端点保留 v1 git-diff 响应契约(diff/stat/files_changed/截断字段)。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified content\n", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_request(monkeypatch, {})

    result = await _gd.handle_full(plugin)
    data = result["data"]
    for key in ("loaded", "diff", "stat", "files_changed", "truncated",
                "truncated_at_bytes", "max_bytes", "scope"):
        assert key in data, f"missing key: {key}"
    assert data["loaded"] is True
    assert data["diff"] and "modified content" in data["diff"]
    assert data["truncated"] is False
    assert any(f["path"] == "README.md" for f in data["files_changed"])


async def test_full_endpoint_scope_staged_content(
    plugin, tmp_path, monkeypatch
):
    """内容级 scope 断言自元数据端点迁移至此:staged patch 显示原始
    staged 内容,不含后续 unstaged edit。"""
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("staged change\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("staged + extra edit\n", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    _mock_request(monkeypatch, {"scope": "staged"})

    result = await _gd.handle_full(plugin)
    data = result["data"]
    assert data["scope"] == "staged"
    assert "staged change" in data["diff"]
    assert "extra edit" not in data["diff"]


async def test_full_endpoint_no_project_loaded(plugin, monkeypatch):
    _proj_state.reset()
    _mock_request(monkeypatch, {})

    result = await _gd.handle_full(plugin)
    assert result["data"]["loaded"] is False
    assert result["data"]["reason"] == "no_project_loaded"
