"""v2.35 (PR-merge-todo) 测试 — 单一 todo_manage 工具的 6 个 action。

覆盖:
- action→参数矩阵:query / create / add / update / delete / clear 的正向行为
- 参数校验:非法 action、缺 items、缺 item_ids、create 的 XOR、delete 拒绝 0
- notes 三态(未传 / 空串 / 有值)
- schema 契约:action enum 6 值、required=["action"]、无 mode 字段
- subagent scope 注入(自 v2.28 起的行为,从 test_todo_split_tools 迁移)

`TodoStore` 的 6 个方法签名未变,store 层行为由 tests/test_todo_list.py 覆盖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch):
    """patch StarTools.get_data_dir → 临时目录,让 TodoStore 落到 tmp_path/todos。"""
    todos_dir = tmp_path / "todos"
    todos_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "astrbot.api.star.StarTools.get_data_dir",
        lambda name: tmp_path,
    )
    return todos_dir


@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.context.event = MagicMock()
    return ctx


def _make_call_with_umo(umo: str):
    def _patched_extract_umo(event):
        return umo

    return _patched_extract_umo


def _call(tool, ctx, umo: str, **kwargs):
    from tools import todo_list

    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        raw = asyncio_run(tool.call(ctx, **kwargs))
    return json.loads(raw)


# ─────────────────────────────────────────────────────────
# 1. schema 契约
# ─────────────────────────────────────────────────────────


def test_tool_name_and_action_enum():
    from tools.function_tools import TodoManageTool

    tool = TodoManageTool()
    assert tool.name == "todo_manage"
    assert tool.parameters["required"] == ["action"]
    assert tool.parameters["properties"]["action"]["enum"] == [
        "query",
        "create",
        "add",
        "update",
        "delete",
        "clear",
    ]


def test_schema_has_no_mode_field():
    """旧 todo_modify 的 mode 字段不应残留。"""
    from tools.function_tools import TodoManageTool

    props = TodoManageTool().parameters["properties"]
    assert "mode" not in props
    for key in ("items", "from_path", "title", "item_ids", "status", "notes"):
        assert key in props


def test_schema_action_matrix_documented():
    """description 必须逐行列出 6 个 action(参数矩阵可读性护栏)。"""
    from tools.function_tools import TodoManageTool

    desc = TodoManageTool().description
    for action in ("query", "create", "add", "update", "delete", "clear"):
        assert f"action='{action}'" in desc, f"description 缺少 action={action} 的说明"


# ─────────────────────────────────────────────────────────
# 2. action=query
# ─────────────────────────────────────────────────────────


def test_query_without_list_returns_proposal(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(TodoManageTool(), mock_context, "u:query-none", action="query")
    assert result["ok"] is False
    assert "todo_manage" in result["proposal"]


def test_query_returns_list(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:query-ok"
    todo_list.TodoStore(tmp_data_dir).create(umo, title="t", items=[{"title": "a"}])

    result = _call(TodoManageTool(), mock_context, umo, action="query")
    assert result["ok"] is True
    assert result["data"]["list"]["items"][0]["title"] == "a"


# ─────────────────────────────────────────────────────────
# 3. action=create
# ─────────────────────────────────────────────────────────


def test_create_with_items(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:create-items"
    result = _call(
        TodoManageTool(),
        mock_context,
        umo,
        action="create",
        items=[{"title": "a"}, {"title": "b"}],
    )
    assert result["ok"] is True
    assert result["data"]["item_count"] == 2
    assert todo_list.TodoStore(tmp_data_dir).query(umo)["ok"] is True


def test_create_requires_items_or_from_path(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(TodoManageTool(), mock_context, "u:create-none", action="create")
    assert result["ok"] is False
    assert "items" in result["error"] and "from_path" in result["error"]


def test_create_rejects_items_and_from_path_together(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(
        TodoManageTool(),
        mock_context,
        "u:create-xor",
        action="create",
        items=[{"title": "x"}],
        from_path="C:/tmp/whatever.md",
    )
    assert result["ok"] is False
    assert "二选一" in result["error"]


# ─────────────────────────────────────────────────────────
# 4. action=add
# ─────────────────────────────────────────────────────────


def test_add_appends_items(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:add-ok"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "existing"}])

    result = _call(
        TodoManageTool(), mock_context, umo, action="add", items=[{"title": "new"}]
    )
    assert result["ok"] is True
    assert result["data"]["item_ids"] == [2]
    assert store.query(umo)["list"]["items"][-1]["title"] == "new"


def test_add_rejects_empty_items(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(TodoManageTool(), mock_context, "u:add-empty", action="add", items=[])
    assert result["ok"] is False
    assert "items" in result["error"]


def test_add_without_list_returns_proposal(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(
        TodoManageTool(), mock_context, "u:add-none", action="add", items=[{"title": "x"}]
    )
    assert result["ok"] is False
    assert "todo_manage" in result["proposal"]


# ─────────────────────────────────────────────────────────
# 5. action=update
# ─────────────────────────────────────────────────────────


def test_update_changes_status(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:update-status"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}, {"title": "b"}])

    result = _call(
        TodoManageTool(),
        mock_context,
        umo,
        action="update",
        item_ids=[1, 2],
        status="done",
    )
    assert result["ok"] is True
    assert all(it["status"] == "done" for it in result["data"]["items"])


def test_update_notes_overwrite(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:update-notes"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a", "notes": "旧"}])

    result = _call(
        TodoManageTool(), mock_context, umo, action="update", item_ids=[1], notes="新"
    )
    assert result["ok"] is True
    assert store.query(umo)["list"]["items"][0]["notes"] == "新"


def test_update_notes_clear_with_empty_string(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:update-clear-notes"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a", "notes": "待清空"}])

    result = _call(
        TodoManageTool(), mock_context, umo, action="update", item_ids=[1], notes=""
    )
    assert result["ok"] is True
    assert store.query(umo)["list"]["items"][0]["notes"] == ""


def test_update_notes_keep_when_omitted(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:update-keep-notes"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a", "notes": "保留"}])

    result = _call(
        TodoManageTool(),
        mock_context,
        umo,
        action="update",
        item_ids=[1],
        status="in_progress",
    )
    assert result["ok"] is True
    item = store.query(umo)["list"]["items"][0]
    assert item["notes"] == "保留"
    assert item["status"] == "in_progress"


def test_update_requires_item_ids(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(
        TodoManageTool(), mock_context, "u:update-none", action="update", status="done"
    )
    assert result["ok"] is False
    assert "item_ids" in result["error"]


def test_update_rolls_back_on_missing_id(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:update-rollback"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}, {"title": "b"}])

    result = _call(
        TodoManageTool(),
        mock_context,
        umo,
        action="update",
        item_ids=[1, 999],
        status="done",
    )
    assert result["ok"] is False
    assert "999" in result["error"]
    assert all(
        it["status"] == "pending" for it in store.query(umo)["list"]["items"]
    )


# ─────────────────────────────────────────────────────────
# 6. action=delete
# ─────────────────────────────────────────────────────────


def test_delete_removes_items(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:delete-ok"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}, {"title": "b"}, {"title": "c"}])

    result = _call(
        TodoManageTool(), mock_context, umo, action="delete", item_ids=[1, 3]
    )
    assert result["ok"] is True
    assert result["data"]["deleted"] == 2
    assert [it["id"] for it in store.query(umo)["list"]["items"]] == [2]


def test_delete_rejects_zero_id_and_points_to_clear(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(
        TodoManageTool(), mock_context, "u:delete-zero", action="delete", item_ids=[0]
    )
    assert result["ok"] is False
    assert "clear" in result["proposal"]


def test_delete_requires_item_ids(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(TodoManageTool(), mock_context, "u:delete-none", action="delete")
    assert result["ok"] is False
    assert "item_ids" in result["error"]


# ─────────────────────────────────────────────────────────
# 7. action=clear
# ─────────────────────────────────────────────────────────


def test_clear_removes_entire_list(tmp_data_dir, mock_context):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    umo = "u:clear-ok"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}])

    result = _call(TodoManageTool(), mock_context, umo, action="clear")
    assert result["ok"] is True
    assert result["data"]["deleted"] == "list"
    assert store.query(umo)["ok"] is False


def test_clear_without_list_returns_proposal(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(TodoManageTool(), mock_context, "u:clear-none", action="clear")
    assert result["ok"] is False
    assert "todo list" in result["proposal"]


# ─────────────────────────────────────────────────────────
# 8. 非法 action
# ─────────────────────────────────────────────────────────


def test_invalid_action_rejected(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(TodoManageTool(), mock_context, "u:bad-action", action="bogus")
    assert result["ok"] is False
    assert "action" in result["error"]
    assert "query" in result["proposal"]


def test_missing_action_rejected(tmp_data_dir, mock_context):
    from tools.function_tools import TodoManageTool

    result = _call(TodoManageTool(), mock_context, "u:no-action")
    assert result["ok"] is False
    assert "action" in result["error"]


# ─────────────────────────────────────────────────────────
# 9. v2.28.0: subagent scope 注入
# ─────────────────────────────────────────────────────────


def _ctx_with_extra(extra):
    ctx = MagicMock()
    ctx.context.event = MagicMock()
    ctx.context.extra = extra
    return ctx


def test_setup_injects_sub_scope_from_extra(tmp_data_dir):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    tool = TodoManageTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo("u:1")):
        store, _ = tool._setup(
            _ctx_with_extra({"is_subagent": True, "subagent_name": "reviewer"})
        )
    assert store._scope == todo_list.TodoScope("sub", "reviewer")


def test_setup_main_scope_for_main_agent(tmp_data_dir):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    tool = TodoManageTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo("u:1")):
        store, _ = tool._setup(_ctx_with_extra({}))
    assert store._scope == todo_list.MAIN_SCOPE


def test_setup_respects_isolation_off(tmp_data_dir):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    tool = TodoManageTool()
    tool.subagent_isolation = False
    with patch.object(todo_list, "extract_umo", _make_call_with_umo("u:1")):
        store, _ = tool._setup(
            _ctx_with_extra({"is_subagent": True, "subagent_name": "reviewer"})
        )
    assert store._scope == todo_list.MAIN_SCOPE


def test_setup_passes_ttl_days(tmp_data_dir, monkeypatch):
    from tools import todo_list
    from tools.function_tools import TodoManageTool

    captured = {}
    real_store = todo_list.TodoStore

    def _spy(base_dir, scope=None, ttl_days=0):
        captured["ttl_days"] = ttl_days
        return real_store(base_dir, scope=scope, ttl_days=0)

    monkeypatch.setattr(todo_list, "TodoStore", _spy)

    tool = TodoManageTool()
    tool.subagent_ttl_days = 7
    with patch.object(todo_list, "extract_umo", _make_call_with_umo("u:1")):
        tool._setup(_ctx_with_extra({}))

    assert captured["ttl_days"] == 7


def test_subagent_todo_isolated_end_to_end(tmp_data_dir):
    """subagent 与主 agent 各写各的文件,互不可见。"""
    from tools import todo_list

    umo = "u:scope-e2e"
    main_store = todo_list.TodoStore(tmp_data_dir)
    main_store.create(umo, title="main", items=[{"title": "m"}])

    sub_store = todo_list.TodoStore(
        tmp_data_dir, scope=todo_list.TodoScope("sub", "reviewer")
    )
    sub_store.create(umo, title="sub", items=[{"title": "s"}])

    assert main_store.query(umo)["list"]["title"] == "main"
    assert sub_store.query(umo)["list"]["title"] == "sub"
    assert len(list(tmp_data_dir.glob("*.md"))) == 2
