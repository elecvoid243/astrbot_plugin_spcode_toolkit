"""Tests for _validate_repo_relative_file (4-step file path defense).

Extracted from file_restore.py in PR-1.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

from tools.webapi._helpers import _validate_repo_relative_file


def test_rejects_empty_string(tmp_path: Path):
    target, err = _validate_repo_relative_file("", tmp_path)
    assert target is None
    assert err == "path_unsafe"


def test_rejects_absolute_path(tmp_path: Path):
    target, err = _validate_repo_relative_file("/etc/passwd", tmp_path)
    assert target is None
    assert err == "path_unsafe"


def test_rejects_backslash(tmp_path: Path):
    target, err = _validate_repo_relative_file(r"src\main.py", tmp_path)
    assert target is None
    assert err == "path_unsafe"


def test_rejects_dotdot(tmp_path: Path):
    target, err = _validate_repo_relative_file("../escape.py", tmp_path)
    assert target is None
    assert err == "path_unsafe"


def test_rejects_dot_in_git_internal(tmp_path: Path):
    """路径含 .git 段 → 拒绝(防止 LLM 读 .git 内部)"""
    target, err = _validate_repo_relative_file(".git/HEAD", tmp_path)
    assert target is None
    assert err == "path_unsafe"


def test_accepts_valid_relative_path(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x")
    target, err = _validate_repo_relative_file("src/main.py", tmp_path)
    assert err is None
    assert target is not None
    assert target.name == "main.py"


@pytest.mark.skipif(
    sys.platform == "win32" and not getattr(os, "supports_symlinks", False),
    reason="Windows 需管理员权限才能创建 symlink,本机无权限跳过",
)
def test_rejects_symlink_escape(tmp_path: Path):
    """symlink 指向 worktree 外 → 拒绝(realpath defense)"""
    (tmp_path / "escape").symlink_to(os.path.dirname(tmp_path))
    target, err = _validate_repo_relative_file("escape", tmp_path)
    # 行为对称于 file_restore 的 4 步防御:realpath != target → reject
    assert target is None
    assert err == "path_unsafe"


def test_accepts_nested_path(tmp_path: Path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "deep.py").write_text("x")
    target, err = _validate_repo_relative_file("a/b/c/deep.py", tmp_path)
    assert err is None
    assert target is not None
