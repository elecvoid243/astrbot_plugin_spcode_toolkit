"""v2.28.0 — subagent todo 文件的 TTL 惰性清理。

Spec: docs/superpowers/specs/2026-09-09-todo-subagent-isolation-design.md §4
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write(path: Path, text: str = "x") -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _age(path: Path, days: float) -> None:
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_cleanup_removes_only_old_sub_files(tmp_path):
    from tools.todo_list import cleanup_stale_sub_files

    stale = _write(tmp_path / "sub__reviewer__webchat_a_202601010000.md")
    fresh = _write(tmp_path / "sub__coder__webchat_a_202609090000.md")
    main = _write(tmp_path / "webchat_a_202609090000.md")
    _age(stale, 40)
    _age(fresh, 1)
    _age(main, 400)  # 主 agent 的文件再老也不许删

    removed = cleanup_stale_sub_files(tmp_path, ttl_days=30)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()
    assert main.exists()


def test_cleanup_skips_keep_path(tmp_path):
    from tools.todo_list import cleanup_stale_sub_files

    keep = _write(tmp_path / "sub__reviewer__webchat_a_202601010000.md")
    _age(keep, 40)

    assert cleanup_stale_sub_files(tmp_path, ttl_days=30, keep=keep) == 0
    assert keep.exists()


def test_cleanup_disabled_when_ttl_zero(tmp_path):
    from tools.todo_list import cleanup_stale_sub_files

    stale = _write(tmp_path / "sub__reviewer__webchat_a_202601010000.md")
    _age(stale, 400)

    assert cleanup_stale_sub_files(tmp_path, ttl_days=0) == 0
    assert stale.exists()


def test_store_init_triggers_cleanup(tmp_path, monkeypatch):
    from tools import todo_list

    monkeypatch.setattr(todo_list, "_last_cleanup_mono", 0.0)
    stale = _write(tmp_path / "sub__reviewer__webchat_a_202601010000.md")
    _age(stale, 40)

    todo_list.TodoStore(tmp_path, ttl_days=30)

    assert not stale.exists()


def test_store_init_throttles_cleanup(tmp_path, monkeypatch):
    from tools import todo_list

    monkeypatch.setattr(todo_list, "_last_cleanup_mono", 0.0)
    first = _write(tmp_path / "sub__reviewer__webchat_a_202601010000.md")
    _age(first, 40)
    todo_list.TodoStore(tmp_path, ttl_days=30)
    assert not first.exists()

    second = _write(tmp_path / "sub__coder__webchat_a_202601010000.md")
    _age(second, 40)
    todo_list.TodoStore(tmp_path, ttl_days=30)  # 1 小时内不再清理

    assert second.exists(), "节流生效:第二次构造不应再扫盘"


def test_store_init_cleanup_failure_does_not_raise(tmp_path, monkeypatch):
    from tools import todo_list

    monkeypatch.setattr(todo_list, "_last_cleanup_mono", 0.0)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(todo_list, "cleanup_stale_sub_files", _boom)

    store = todo_list.TodoStore(tmp_path, ttl_days=30)  # 不得抛异常
    assert store._scope.kind == "main"
