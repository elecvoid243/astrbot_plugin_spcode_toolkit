"""Envelope factory tests for /spcode/file-restore.

Temporary file: will be merged into tests/test_file_restore.py after
the handler skeleton lands. Kept separate so TDD steps are atomic.

Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md §8
Author: elecvoid243 @ 2026-06-22
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
_PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402


def test_empty_envelope_minimal():
    """最小调用:只给 reason,其它字段走默认。"""
    env = _main_mod._make_file_restore_empty_envelope(reason="no_project_loaded")
    assert env["status"] == "ok"
    assert env["data"]["restored"] is False
    assert env["data"]["reason"] == "no_project_loaded"
    assert env["data"]["scope"] == "unstaged"
    assert env["data"]["file"] == ""
    assert env["data"]["elapsed_ms"] == 0
    assert env["data"]["directory"] is None


def test_empty_envelope_full():
    """完整调用:所有字段都被正确填充。"""
    env = _main_mod._make_file_restore_empty_envelope(
        umo="u:m",
        file="main.py",
        directory="/tmp/repo",
        worktree="/tmp/repo",
        scope="unstaged",
        reason="path_unsafe",
        stderr="some stderr",
        elapsed_ms=12,
    )
    data = env["data"]
    assert data["restored"] is False
    assert data["umo"] == "u:m"
    assert data["file"] == "main.py"
    assert data["directory"] == "/tmp/repo"
    assert data["worktree"] == "/tmp/repo"
    assert data["scope"] == "unstaged"
    assert data["reason"] == "path_unsafe"
    assert data["stderr"] == "some stderr"
    assert data["elapsed_ms"] == 12


def test_empty_envelope_worktree_falls_back_to_directory():
    """不传 worktree 时,worktree 字段降级为 directory(对称于 file-browser 的 path 处理)。"""
    env = _main_mod._make_file_restore_empty_envelope(
        directory="/tmp/repo", reason="git_error"
    )
    assert env["data"]["worktree"] == "/tmp/repo"


def test_success_envelope():
    env = _main_mod._make_file_restore_success_envelope(
        umo="u:m", file="main.py", directory="/tmp/repo", elapsed_ms=42
    )
    assert env["status"] == "ok"
    data = env["data"]
    assert data["restored"] is True
    assert data["reason"] is None
    assert data["file"] == "main.py"
    assert data["directory"] == "/tmp/repo"
    assert data["worktree"] == "/tmp/repo"
    assert data["scope"] == "unstaged"
    assert data["stderr"] == ""
    assert data["elapsed_ms"] == 42
