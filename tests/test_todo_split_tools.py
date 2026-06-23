"""v2.12 (PR-split-modify) 测试 — todo_add / todo_update / todo_delete 3 个新工具。

每个工具的 `call()` 测试:
- mock context + event,patch `StarTools.get_data_dir` + `extract_umo`,
  走真实 TodoStore 落盘链路,验证返回 JSON / 错误 / 边界条件
- schema 测试:验证 parameters dict 的 required 字段、enum 字段、不含 mode 字段

旧 `TodoModifyTool` 已删除,本文件不测试 modify 路径(由 test_todo_list.py
覆盖 TodoStore.modify() 后端逻辑)。
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


# ── 通用 fixture:每个 test 独立 temp data_dir,避免 umo 文件相互污染 ──


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
    """构造一个最小可用 context,event.extract_umo 走 patch 控制。"""
    ctx = MagicMock()
    ctx.context.event = MagicMock()
    return ctx


def _make_call_with_umo(umo: str):
    """构造一个 patch lambda,使 TodoStore.extract_umo(event) 返回固定 umo。"""

    def _patched_extract_umo(event):
        return umo

    return _patched_extract_umo


# ─────────────────────────────────────────────────────────
# 1. TodoAddTool
# ─────────────────────────────────────────────────────────


def test_todo_add_tool_appends_items(tmp_data_dir, mock_context):
    """todo_add(items=...) 真实落盘 + 追加成功。

    注意:工具返回会被 _dispatch → unwrap 包成 {"ok": True, "data": {...}},
    data 内的字段才是 TodoStore.add() 的原始返回。
    """
    from tools import todo_list
    from tools.function_tools import TodoAddTool

    umo = "test:user-a:PrivateMessage:split-add-1"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "existing"}])

    tool = TodoAddTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, items=[{"title": "new1"}]))
        )

    assert result["ok"] is True
    data = result["data"]
    assert data["item_ids"] == [2]
    assert data["item_count"] == 2
    assert data["items"][0]["title"] == "new1"

    # 落盘校验
    q = store.query(umo)
    assert q["list"]["items"][-1]["title"] == "new1"


def test_todo_add_tool_rejects_empty_items(tmp_data_dir, mock_context):
    """todo_add(items=[]) → 错误,不入数据库。"""
    from tools.function_tools import TodoAddTool

    tool = TodoAddTool()
    result = json.loads(asyncio_run(tool.call(mock_context, items=[])))
    assert result["ok"] is False
    assert "items" in result["error"]


def test_todo_add_tool_rejects_none_items(tmp_data_dir, mock_context):
    """todo_add(items=None) → 错误。"""
    from tools.function_tools import TodoAddTool

    tool = TodoAddTool()
    result = json.loads(asyncio_run(tool.call(mock_context, items=None)))
    assert result["ok"] is False


def test_todo_add_tool_rejects_when_no_list(tmp_data_dir, mock_context):
    """无现有 list 时 todo_add 返回 proposal。"""
    from tools import todo_list
    from tools.function_tools import TodoAddTool

    umo = "test:nobody:PrivateMessage:split-add-no-list"
    tool = TodoAddTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, items=[{"title": "x"}]))
        )
    assert result["ok"] is False
    assert "proposal" in result
    assert "todo_create" in result["proposal"]


def test_todo_add_tool_schema_no_mode():
    """todo_add 的 schema 不应包含 `mode` 字段(独立工具,不再有 mode 概念)。"""
    from tools.function_tools import TodoAddTool

    tool = TodoAddTool()
    props = tool.parameters["properties"]
    assert "items" in props
    assert "mode" not in props, "todo_add 不应再有 mode 字段"
    assert "item_ids" not in props
    assert "status" not in props
    assert "notes" not in props
    assert "required" not in tool.parameters or tool.parameters.get("required") == ["items"]
    # required 字段必须恰好是 ["items"]
    assert tool.parameters["required"] == ["items"]


# ─────────────────────────────────────────────────────────
# 2. TodoUpdateTool
# ─────────────────────────────────────────────────────────


def test_todo_update_tool_changes_status(tmp_data_dir, mock_context):
    """todo_update(item_ids=..., status=...) 改状态。

    返回包在 result["data"] 内(unwrap 协议)。
    """
    from tools import todo_list
    from tools.function_tools import TodoUpdateTool

    umo = "test:user-a:PrivateMessage:split-update-1"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}, {"title": "b"}])

    tool = TodoUpdateTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, item_ids=[1, 2], status="done"))
        )

    assert result["ok"] is True
    data = result["data"]
    assert data["item_ids"] == [1, 2]
    assert all(it["status"] == "done" for it in data["items"])
    # 落盘校验
    q = store.query(umo)
    assert all(it["status"] == "done" for it in q["list"]["items"])


def test_todo_update_tool_overwrites_notes(tmp_data_dir, mock_context):
    """todo_update notes='x' → 覆盖旧值。"""
    from tools import todo_list
    from tools.function_tools import TodoUpdateTool

    umo = "test:user-a:PrivateMessage:split-update-notes"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a", "notes": "原始"}])

    tool = TodoUpdateTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, item_ids=1, notes="新内容"))
        )

    assert result["ok"] is True
    q = store.query(umo)
    assert q["list"]["items"][0]["notes"] == "新内容"


def test_todo_update_tool_clears_notes_with_empty_string(tmp_data_dir, mock_context):
    """todo_update notes='' → 清空 notes(配合 schema docstring)。"""
    from tools import todo_list
    from tools.function_tools import TodoUpdateTool

    umo = "test:user-a:PrivateMessage:split-update-clear"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a", "notes": "待清空"}])

    tool = TodoUpdateTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, item_ids=1, notes=""))
        )

    assert result["ok"] is True
    q = store.query(umo)
    assert q["list"]["items"][0]["notes"] == ""


def test_todo_update_tool_keeps_notes_when_omitted(tmp_data_dir, mock_context):
    """todo_update 不传 notes → 保留旧值(三态语义)。"""
    from tools import todo_list
    from tools.function_tools import TodoUpdateTool

    umo = "test:user-a:PrivateMessage:split-update-keep"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a", "notes": "保留我"}])

    tool = TodoUpdateTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, item_ids=1, status="in_progress"))
        )

    assert result["ok"] is True
    q = store.query(umo)
    assert q["list"]["items"][0]["notes"] == "保留我"
    assert q["list"]["items"][0]["status"] == "in_progress"


def test_todo_update_tool_rejects_missing_item_ids(tmp_data_dir, mock_context):
    """todo_update 缺 item_ids → 错误。"""
    from tools.function_tools import TodoUpdateTool

    tool = TodoUpdateTool()
    result = json.loads(asyncio_run(tool.call(mock_context, status="done")))
    assert result["ok"] is False
    assert "item_ids" in result["error"]


def test_todo_update_tool_rolls_back_on_missing_id(tmp_data_dir, mock_context):
    """todo_update 含不存在的 id → 全量回滚,数据原封不动。"""
    from tools import todo_list
    from tools.function_tools import TodoUpdateTool

    umo = "test:user-a:PrivateMessage:split-update-rollback"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}, {"title": "b"}])

    tool = TodoUpdateTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, item_ids=[1, 999], status="done"))
        )

    assert result["ok"] is False
    assert "999" in result["error"]
    # 数据未变
    q = store.query(umo)
    assert q["list"]["items"][0]["status"] == "pending"
    assert q["list"]["items"][1]["status"] == "pending"


def test_todo_update_tool_schema_no_mode():
    """todo_update 的 schema 不应包含 `mode` 字段。"""
    from tools.function_tools import TodoUpdateTool

    tool = TodoUpdateTool()
    props = tool.parameters["properties"]
    assert "item_ids" in props
    assert "status" in props
    assert "notes" in props
    assert "mode" not in props
    assert "items" not in props
    assert tool.parameters["required"] == ["item_ids"]


# ─────────────────────────────────────────────────────────
# 3. TodoDeleteTool
# ─────────────────────────────────────────────────────────


def test_todo_delete_tool_removes_items(tmp_data_dir, mock_context):
    """todo_delete(item_ids=...) 删条。返回包在 result["data"] 内。"""
    from tools import todo_list
    from tools.function_tools import TodoDeleteTool

    umo = "test:user-a:PrivateMessage:split-delete-1"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}, {"title": "b"}, {"title": "c"}])

    tool = TodoDeleteTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, item_ids=[1, 3]))
        )

    assert result["ok"] is True
    data = result["data"]
    assert data["deleted"] == 2
    assert data["item_count"] == 1
    assert data["item_ids"] == [1, 3]

    q = store.query(umo)
    assert [it["id"] for it in q["list"]["items"]] == [2]


def test_todo_delete_tool_rejects_zero_id(tmp_data_dir, mock_context):
    """todo_delete(item_ids=0) → 错误,引导用户用 todo_clear。"""
    from tools.function_tools import TodoDeleteTool

    tool = TodoDeleteTool()
    result = json.loads(asyncio_run(tool.call(mock_context, item_ids=0)))
    assert result["ok"] is False
    assert "todo_clear" in result.get("proposal", "")


def test_todo_delete_tool_rejects_none_item_ids(tmp_data_dir, mock_context):
    """todo_delete 缺 item_ids → 错误。"""
    from tools.function_tools import TodoDeleteTool

    tool = TodoDeleteTool()
    result = json.loads(asyncio_run(tool.call(mock_context)))
    assert result["ok"] is False
    assert "item_ids" in result["error"]


def test_todo_delete_tool_rolls_back_on_missing_id(tmp_data_dir, mock_context):
    """todo_delete 含不存在的 id → 全量回滚,数据原封不动。"""
    from tools import todo_list
    from tools.function_tools import TodoDeleteTool

    umo = "test:user-a:PrivateMessage:split-delete-rollback"
    store = todo_list.TodoStore(tmp_data_dir)
    store.create(umo, title="t", items=[{"title": "a"}, {"title": "b"}])

    tool = TodoDeleteTool()
    with patch.object(todo_list, "extract_umo", _make_call_with_umo(umo)):
        result = json.loads(
            asyncio_run(tool.call(mock_context, item_ids=[1, 999]))
        )

    assert result["ok"] is False
    # 落盘数据未变(query 不受 unwrap 包装,直接是 store 原始返回)
    q = store.query(umo)
    assert q["ok"] is True
    assert len(q["list"]["items"]) == 2  # 原封不动,无 item_count 字段在 list 上


def test_todo_delete_tool_schema_no_mode():
    """todo_delete 的 schema 不应包含 `mode` 字段。"""
    from tools.function_tools import TodoDeleteTool

    tool = TodoDeleteTool()
    props = tool.parameters["properties"]
    assert "item_ids" in props
    assert "mode" not in props
    assert "items" not in props
    assert "status" not in props
    assert "notes" not in props
    assert tool.parameters["required"] == ["item_ids"]


# ─────────────────────────────────────────────────────────
# 4. 跨工具契约保护:不再有 TodoModifyTool 类
# ─────────────────────────────────────────────────────────


def test_todo_modify_class_removed():
    """v2.12 起 TodoModifyTool 已删除(被 3 个独立工具取代)。"""
    from tools.function_tools import (  # noqa: F401
        TodoAddTool,
        TodoClearTool,
        TodoCreateTool,
        TodoDeleteTool,
        TodoQueryTool,
        TodoUpdateTool,
    )
    import tools.function_tools as ft

    # TodoModifyTool 应已从模块中移除(任何引用都 ImportError)
    assert not hasattr(ft, "TodoModifyTool"), (
        "TodoModifyTool 已废弃,不应再导出。三个独立工具 "
        "(TodoAddTool / TodoUpdateTool / TodoDeleteTool) 取代之。"
    )


def test_todo_modify_tool_name_no_longer_registered():
    """3 个新工具的 .name 字段正确(LLM 通过 name 调用)。"""
    from tools.function_tools import (
        TodoAddTool,
        TodoDeleteTool,
        TodoUpdateTool,
    )

    assert TodoAddTool().name == "todo_add"
    assert TodoUpdateTool().name == "todo_update"
    assert TodoDeleteTool().name == "todo_delete"


# ─────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────


def asyncio_run(coro):
    """本地 async runner,避免污染顶层 import asyncio。"""
    import asyncio

    return asyncio.run(coro)
