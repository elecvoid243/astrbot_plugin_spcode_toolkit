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


# ── build_filename ───────────────────────────────────────


def test_build_filename_main_unchanged():
    from datetime import datetime

    from tools.todo_list import build_filename

    dt = datetime(2026, 9, 9, 22, 30)
    assert (
        build_filename("webchat:astrbot", when=dt) == "webchat_astrbot_202609092230.md"
    )


def test_build_filename_sub_prefix_is_disjoint():
    from datetime import datetime

    from tools.todo_list import TodoScope, build_filename

    dt = datetime(2026, 9, 9, 22, 30)
    name = build_filename(
        "webchat:astrbot", when=dt, scope=TodoScope("sub", "reviewer")
    )
    main = build_filename("webchat:astrbot", when=dt)
    assert name == "sub__reviewer__webchat_astrbot_202609092230.md"
    assert not name.startswith("webchat_astrbot_")
    assert main != name


def test_build_filename_hash_fallback_keeps_main_algorithm():
    import hashlib
    from datetime import datetime

    from tools.todo_list import TodoScope, build_filename

    dt = datetime(2026, 9, 9, 22, 30)
    umo = "webchat:<user:bad>"
    main = build_filename(umo, when=dt)
    expected_main = hashlib.sha256(umo.encode("utf-8")).hexdigest()[:16]
    assert main == f"{expected_main}_202609092230.md"

    sub = build_filename(umo, when=dt, scope=TodoScope("sub", "reviewer"))
    expected_sub = hashlib.sha256(f"sub:reviewer|{umo}".encode("utf-8")).hexdigest()[
        :16
    ]
    assert sub == f"sub__reviewer__{expected_sub}_202609092230.md"


# ── 查找隔离 ─────────────────────────────────────────────


def test_main_store_does_not_pick_up_subagent_file(tmp_path):
    from tools.todo_list import TodoScope, TodoStore

    umo = "webchat:FriendMessage:abc"
    sub_store = TodoStore(tmp_path, scope=TodoScope("sub", "reviewer"))
    sub_store.create(umo, title="sub", items=[{"title": "sub-item"}])
    assert list(tmp_path.glob("sub__reviewer__*.md"))

    main_store = TodoStore(tmp_path)
    result = main_store.query(umo)
    assert result["ok"] is False, "主 agent 绝不能读到 subagent 的列表"


def test_two_subagents_are_isolated(tmp_path):
    from tools.todo_list import TodoScope, TodoStore

    umo = "webchat:FriendMessage:abc"
    TodoStore(tmp_path, scope=TodoScope("sub", "coder")).create(
        umo, title="coder", items=[{"title": "c"}]
    )
    TodoStore(tmp_path, scope=TodoScope("sub", "reviewer")).create(
        umo, title="reviewer", items=[{"title": "r"}]
    )

    coder = TodoStore(tmp_path, scope=TodoScope("sub", "coder")).query(umo)
    reviewer = TodoStore(tmp_path, scope=TodoScope("sub", "reviewer")).query(umo)
    assert coder["list"]["title"] == "coder"
    assert reviewer["list"]["title"] == "reviewer"


def test_main_and_sub_write_different_files(tmp_path):
    from tools.todo_list import TodoScope, TodoStore

    umo = "webchat:FriendMessage:abc"
    TodoStore(tmp_path).create(umo, title="main", items=[{"title": "m"}])
    TodoStore(tmp_path, scope=TodoScope("sub", "reviewer")).create(
        umo, title="sub", items=[{"title": "s"}]
    )
    files = sorted(p.name for p in tmp_path.glob("*.md"))
    assert len(files) == 2
    assert sum(1 for f in files if f.startswith("sub__reviewer__")) == 1


# ── frontmatter agent_scope ──────────────────────────────


def test_create_writes_agent_scope(tmp_path):
    from tools.todo_list import TodoScope, TodoStore, parse_md

    umo = "webchat:FriendMessage:abc"
    TodoStore(tmp_path, scope=TodoScope("sub", "reviewer")).create(
        umo, title="t", items=[{"title": "x"}]
    )
    path = next(tmp_path.glob("sub__reviewer__*.md"))
    assert parse_md(path.read_text(encoding="utf-8"))["agent_scope"] == "sub:reviewer"


def test_legacy_file_without_agent_scope_parses_as_main():
    from tools.todo_list import parse_md

    legacy = (
        "---\n"
        "umo: webchat:FriendMessage:abc\n"
        "title: legacy\n"
        "created_at: 2026-01-01T00:00:00\n"
        "updated_at: 2026-01-01T00:00:00\n"
        "---\n\n"
        "# legacy\n\n"
        "- [ ] **(1)** old item\n"
    )
    assert parse_md(legacy)["agent_scope"] == "main"
