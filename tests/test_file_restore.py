"""Smoke test for webapi/file_restore.handle + envelope helpers.

Handler 从 main.py 搬出,行为不变。
"""

from __future__ import annotations

import pytest

from tools.webapi import file_restore

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


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
