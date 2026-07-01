"""端到端冒烟测试:验证 4 工具 → TodoStore → 文件系统 → 重新读取 整条链路。

v2.6 重构后,冒烟测试确保整个 todo_list 工作流的完整生命周期无回归。
v2.10 切到 umo 隔离后,本测试的 sender 输入已改写为 umo 形式,验证落盘/读取/修改整链路正常。

__author__: todo_impl_subagent_4
__created__: 2026-06-13
"""

from pathlib import Path


def test_full_lifecycle_v2_6(tmp_path: Path):
    """create → query → add → update(3 态) → delete → clear 完整链路。"""
    from tools import todo_list

    store = todo_list.TodoStore(tmp_path)
    umo = "test:smoke:PrivateMessage:test-smoke"

    # 1. create
    r1 = store.create(
        umo,
        title="重构计划",
        items=[
            {"title": "分析", "status": "pending"},
            {"title": "实现", "status": "pending"},
        ],
    )
    assert r1["ok"] is True
    assert r1["item_count"] == 2
    assert "list" in r1
    assert "stats" in r1
    assert "attention_items" in r1

    # 2. query
    r2 = store.query(umo)
    assert r2["ok"] is True
    assert r2["list"]["title"] == "重构计划"
    assert r2["stats"]["total"] == 2

    # 3. modify(add) — 追加 1 个
    r3 = store.modify(umo, mode="add", items=[{"title": "测试"}])
    assert r3["ok"] is True
    assert r3["item_ids"] == [3]
    assert r3["item_count"] == 3

    # 4. modify(update) — 改 status
    r4 = store.modify(umo, mode="update", item_ids=1, status="done")
    assert r4["ok"] is True
    assert r4["item_ids"] == [1]

    # 5. modify(update) — 传 notes="xxx" 覆盖
    r5 = store.modify(umo, mode="update", item_ids=2, notes="正在实现")
    assert r5["ok"] is True
    q5 = store.query(umo)
    assert q5["list"]["items"][1]["notes"] == "正在实现"

    # 6. modify(update) — 传 notes="" 清空
    r6 = store.modify(umo, mode="update", item_ids=2, notes="")
    assert r6["ok"] is True
    q6 = store.query(umo)
    assert q6["list"]["items"][1]["notes"] == ""

    # 7. modify(update) — notes=None 保留旧值
    # 先设置一个 notes
    store.modify(umo, mode="update", item_ids=3, status="in_progress", notes="阻塞中")
    # 然后只改 status,notes 不传(None = 保留)
    r7 = store.modify(umo, mode="update", item_ids=3, status="pending")
    assert r7["ok"] is True
    q7 = store.query(umo)
    # notes 应保留 r7 之前设置的"阻塞中"
    assert q7["list"]["items"][2]["notes"] == "阻塞中"

    # 8. modify(delete) — 删 1 个
    r8 = store.modify(umo, mode="delete", item_ids=1)
    assert r8["ok"] is True
    assert r8["item_count"] == 2

    # 9. clear — 清空整个列表
    r9 = store.clear(umo)
    assert r9["ok"] is True
    assert r9["deleted"] == "list"

    # 10. 确认文件被删
    files = list(tmp_path.glob("*.md"))
    assert files == [], f"list 文件应已被删除, 仍存在: {files}"


def test_query_returns_proposal_when_no_list(tmp_path: Path):
    """query 在 list 不存在时返回 proposal。"""
    from tools import todo_list

    store = todo_list.TodoStore(tmp_path)
    r = store.query("test:noone:PrivateMessage:test-noone")
    assert r["ok"] is False
    assert "proposal" in r
    assert "todo_create" in r["proposal"]


def test_clear_returns_proposal_when_no_list(tmp_path: Path):
    """clear 在 list 不存在时返回 proposal。"""
    from tools import todo_list

    store = todo_list.TodoStore(tmp_path)
    r = store.clear("test:noone:PrivateMessage:test-noone")
    assert r["ok"] is False
    assert "proposal" in r
