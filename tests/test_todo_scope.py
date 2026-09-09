"""v2.28.0 — todo scope 隔离:解析 / 命名 / 查找 / frontmatter。

Spec: docs/superpowers/specs/2026-09-09-todo-subagent-isolation-design.md
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_ctx(extra):
    """构造 ContextWrapper[AstrAgentContext] 的最小替身。"""
    return SimpleNamespace(context=SimpleNamespace(extra=extra))


# ── resolve_scope ────────────────────────────────────────


def test_resolve_scope_main_for_main_agent():
    from tools.todo_list import MAIN_SCOPE, resolve_scope

    assert resolve_scope(_run_ctx({})) == MAIN_SCOPE
    assert resolve_scope(_run_ctx({"main_agent_runner": object()})) == MAIN_SCOPE


def test_resolve_scope_sub_for_subagent():
    from tools.todo_list import TodoScope, resolve_scope

    scope = resolve_scope(_run_ctx({"is_subagent": True, "subagent_name": "reviewer"}))
    assert scope == TodoScope("sub", "reviewer")
    assert scope.key == "sub:reviewer"


def test_resolve_scope_degrades_to_main_when_name_missing():
    from tools.todo_list import MAIN_SCOPE, resolve_scope

    assert resolve_scope(_run_ctx({"is_subagent": True})) == MAIN_SCOPE
    assert (
        resolve_scope(_run_ctx({"is_subagent": True, "subagent_name": "   "}))
        == MAIN_SCOPE
    )
    assert (
        resolve_scope(_run_ctx({"is_subagent": True, "subagent_name": None}))
        == MAIN_SCOPE
    )


def test_resolve_scope_degrades_on_bad_extra():
    from tools.todo_list import MAIN_SCOPE, resolve_scope

    assert resolve_scope(_run_ctx(None)) == MAIN_SCOPE
    assert resolve_scope(_run_ctx("not-a-dict")) == MAIN_SCOPE
    assert resolve_scope(SimpleNamespace()) == MAIN_SCOPE
    assert resolve_scope(object()) == MAIN_SCOPE


# ── sanitize_name ────────────────────────────────────────


def test_sanitize_name_replaces_illegal_chars():
    from tools.todo_list import sanitize_name

    assert sanitize_name("a<b>c:d") == "a_b_c_d"
    assert sanitize_name("  reviewer  ") == "reviewer"


def test_sanitize_name_empty_returns_empty():
    from tools.todo_list import sanitize_name

    assert sanitize_name("") == ""
    assert sanitize_name("   ") == ""
    assert sanitize_name(None) == ""


def test_sanitize_name_long_names_do_not_collide():
    from tools.todo_list import sanitize_name

    a = sanitize_name("x" * 24 + "aaaa")
    b = sanitize_name("x" * 24 + "bbbb")
    assert a != b, "截断必须带哈希后缀,否则两个不同 subagent 会写进同一文件"
    assert len(a) == 32  # 24 + sha256[:8]
