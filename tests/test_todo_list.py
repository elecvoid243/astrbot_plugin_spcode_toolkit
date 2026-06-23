"""todo_list 工具测试。

覆盖 create / query / add / update / delete / clear 六个 action 的核心协议。
重点验证：create / add / update / delete(单条) 必须回传完整 list + stats +
attention_items，以便前端 TodoListResult.vue 在这些 action 成功后展示完整列表。
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# main.py 用相对导入 (from .tools import ...),需把项目父目录加到 sys.path
# 然后以包形式导入 main。这样 `from .tools import` 才能 resolve。
# 模板: tests/test_codegraph_cmd.py:10
_PROJECT_PARENT = ROOT.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
from astrbot_plugin_spcode_toolkit import main as main_mod  # noqa: E402

from tools import todo_list  # noqa: E402

UMO = "test:user-a:PrivateMessage:test-user-a"

# v2.9: 测试 fixture 路径
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "webchat_todo_fixture.md"


def _new_store(tmp_path: Path) -> todo_list.TodoStore:
    return todo_list.TodoStore(tmp_path)


# ── 1. create 返回完整 list + stats ─────────────────


def test_create_returns_full_list_and_stats(tmp_path: Path):
    """create 成功后应回传 list / stats / attention_items（与 query 一致）。"""
    store = _new_store(tmp_path)
    items = [
        {"title": "设计协议", "status": "done"},
        {"title": "写后端", "status": "in_progress", "notes": "等评审"},
        {"title": "写前端", "status": "pending"},
    ]
    r = store.create(UMO, title="重构 todo_list", items=items)

    assert r["ok"] is True
    assert r["list_title"] == "重构 todo_list"
    assert r["item_count"] == 3

    # 新需求：必须包含完整 list + stats
    assert "list" in r, f"create 应回传 list 字段，实际 keys={list(r.keys())}"
    assert "stats" in r, f"create 应回传 stats 字段，实际 keys={list(r.keys())}"
    assert "attention_items" in r

    # list 结构正确
    assert r["list"]["title"] == "重构 todo_list"
    assert len(r["list"]["items"]) == 3
    assert r["list"]["items"][0]["title"] == "设计协议"
    assert r["list"]["items"][0]["status"] == "done"
    assert r["list"]["items"][1]["status"] == "in_progress"
    assert r["list"]["items"][1]["attention"] is True  # in_progress + notes

    # stats 正确
    assert r["stats"]["done"] == 1
    assert r["stats"]["in_progress"] == 1
    assert r["stats"]["pending"] == 1
    assert r["stats"]["effective_total"] == 3
    assert r["stats"]["progress_pct"] == 33

    # attention_items 包含 id
    assert r["attention_items"] == [2]


# ── 2. create 覆盖旧列表时 previous_item_count 仍正确 ─


def test_create_overwrite_previous_count(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(UMO, title="旧", items=[{"title": "a"}, {"title": "b"}])

    r = store.create(UMO, title="新", items=[{"title": "x"}])
    assert r["ok"] is True
    assert r["previous_item_count"] == 2
    assert r["item_count"] == 1
    # 仍然包含完整 list
    assert len(r["list"]["items"]) == 1
    assert r["stats"]["total"] == 1


# ── 2.5 v2.2.0: 移除 from_file / source_file / loaded_from ──


def test_create_no_longer_accepts_from_file(tmp_path: Path):
    """v2.2.0: create 签名已移除 from_file。"""
    import inspect

    store = _new_store(tmp_path)
    sig = inspect.signature(store.create)
    assert "from_file" not in sig.parameters, (
        f"create() should not have from_file param, got {list(sig.parameters.keys())}"
    )


def test_create_result_has_no_source_file_field(tmp_path: Path):
    """v2.2.0: 移除 from_file 后,创建结果不应含 source_file / loaded_from 字段。"""
    store = _new_store(tmp_path)
    r = store.create(UMO, title="t", items=[{"title": "a"}])
    assert r["ok"] is True
    assert "source_file" not in r
    assert "loaded_from" not in r


def test_create_empty_items_returns_error(tmp_path: Path):
    """v2.2.0: items 为空(None / []) 时 create 应返回 error,不再 auto-discover。"""
    store = _new_store(tmp_path)
    # 先 seed 一个文件证明它不该被 auto-discover 加载
    seed = store.create(UMO, title="old", items=[{"title": "x"}])

    r_none = store.create(UMO)
    assert r_none["ok"] is False
    assert "error" in r_none
    assert "list" not in r_none

    r_empty = store.create(UMO, items=[])
    assert r_empty["ok"] is False
    assert "error" in r_empty
    assert "list" not in r_empty

    # 旧的 seed 文件不应被改动(说明没有走 auto-discover 路径)
    assert Path(seed["file"]).is_file()


# ── 3. add 返回完整 list + stats ─────────────────────


def test_add_returns_full_list_and_stats(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(UMO, title="计划", items=[{"title": "task1"}])

    r = store.add(UMO, [{"title": "task2", "status": "in_progress"}])
    assert r["ok"] is True
    # v2.2.0: 单条时仍返回 list 形式
    assert r["item_ids"] == [2]
    assert r["item_count"] == 2

    # 关键：add 后也应能看到完整列表
    assert "list" in r
    assert "stats" in r
    assert len(r["list"]["items"]) == 2
    assert r["list"]["items"][1]["title"] == "task2"
    assert r["stats"]["total"] == 2
    assert r["stats"]["in_progress"] == 1


# ── 4. add 失败时不应回传 list ───────────────────────


def test_add_when_no_list_returns_proposal(tmp_path: Path):
    store = _new_store(tmp_path)
    r = store.add(UMO, {"title": "x"})
    assert r["ok"] is False
    assert "proposal" in r
    # 失败路径不需要回传 list
    assert "list" not in r


# ── 5. update 返回完整 list + stats ──────────────────


def test_update_returns_full_list_and_stats(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(
        UMO,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "pending"},
            {"title": "c", "status": "pending"},
        ],
    )

    # 把 #2 标记为 done
    r = store.update(UMO, item_ids=2, status="done")
    assert r["ok"] is True
    # v2.2.0: 单条时仍返回 list 形式,无 item_id (int) / item (dict) 兼容字段
    assert r["item_ids"] == [2]
    assert r["items"][0]["status"] == "done"

    # 关键：update 后也能看到完整列表 + 进度变化
    assert "list" in r
    assert "stats" in r
    assert len(r["list"]["items"]) == 3
    assert r["stats"]["done"] == 1
    assert r["stats"]["pending"] == 2
    # effective_total 排除 cancelled
    assert r["stats"]["effective_total"] == 3
    assert r["stats"]["progress_pct"] == 33


# ── 6. update in_progress + notes → attention 标记 ─


def test_update_marks_attention(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])

    r = store.update(UMO, item_ids=1, status="in_progress", notes="被外部阻塞")
    assert r["ok"] is True
    # attention_items 应包含 #1
    assert r["attention_items"] == [1]
    # list 中该 item 标了 attention
    assert r["list"]["items"][0]["attention"] is True


# ── 7. update 失败时返回 proposal 不含 list ─────────


def test_update_invalid_id_returns_proposal(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])

    r = store.update(UMO, item_ids=999, status="done")
    assert r["ok"] is False
    assert "proposal" in r
    assert "list" not in r


# ── 8. query 行为不变（回归保护） ────────────────────


def test_query_unchanged(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(
        UMO,
        title="回归",
        items=[{"title": "a", "status": "done"}],
    )
    r = store.query(UMO)
    assert r["ok"] is True
    assert r["list"]["title"] == "回归"
    assert r["stats"]["done"] == 1


# ── 9. delete 单条回传完整 list + stats ─────────────


def test_delete_single_includes_list_and_stats(tmp_path: Path):
    """delete(item_id>0) 删单条后列表还在，应回传完整 list/stats/attention_items。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "in_progress", "notes": "等评审"},
            {"title": "c", "status": "pending"},
        ],
    )

    r = store.delete(UMO, item_ids=2)
    assert r["ok"] is True
    assert r["deleted"] == 1
    assert r["item_count"] == 2

    # 关键：delete 单条后也要回传完整 list
    assert "list" in r, f"delete 应回传 list 字段，实际 keys={list(r.keys())}"
    assert "stats" in r
    assert "attention_items" in r
    assert len(r["list"]["items"]) == 2
    # 删掉的 #2 确实不在了
    ids = [it["id"] for it in r["list"]["items"]]
    assert 2 not in ids
    assert ids == [1, 3]
    # stats 反映新状态
    assert r["stats"]["total"] == 2
    # 删了带 notes 的 in_progress 项后，attention_items 为空
    assert r["attention_items"] == []


def test_delete_in_progress_with_notes_refreshes_attention(tmp_path: Path):
    """删除 in_progress+notes 项后 attention_items 应即时更新。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        items=[
            {"title": "x", "status": "in_progress", "notes": "blocker"},
            {"title": "y", "status": "pending"},
        ],
    )
    # 删掉唯一 in_progress 项
    r = store.delete(UMO, item_ids=1)
    assert r["ok"] is True
    # attention_items 应为空（in_progress 项已删）
    assert r["attention_items"] == []
    # 剩下的 item 也不应带 attention
    assert all(not it["attention"] for it in r["list"]["items"])


# ── 10. clear 仍然不回传 list（列表整个没了） ─────────


def test_clear_does_not_include_list(tmp_path: Path):
    """clear() = delete(item_id=0)，整个列表被删，无 list 可回传。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}, {"title": "b"}])

    r = store.clear(UMO)
    assert r["ok"] is True
    assert r["deleted"] == "list"
    # clear 整个列表没了，保持精简，不回传 list
    assert "list" not in r


# Removed in v2.2.0: test_delete_with_item_id_zero_does_not_include_list (delete no longer accepts 0; see test_delete_no_longer_handles_zero_sentinel)

# ── 11. delete 失败路径不污染 list 字段 ──────────────


def test_delete_nonexistent_id_returns_error_without_list(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}])

    r = store.delete(UMO, item_ids=999)
    assert r["ok"] is False
    assert "error" in r
    assert "list" not in r


def test_delete_no_list_returns_proposal(tmp_path: Path):
    store = _new_store(tmp_path)
    r = store.delete(UMO, item_ids=1)
    assert r["ok"] is False
    assert "proposal" in r
    assert "list" not in r


# ══════════════════════════════════════════════════════════════════════
# Phase 3: batch item_id (int | list[int]) for update / delete
# ══════════════════════════════════════════════════════════════════════


# ── 12. _normalize_item_ids 工具函数 ───────────────────


def test_normalize_single_int():
    """单个 int 必须被包裹成 [int]。"""
    assert todo_list._normalize_item_ids(3) == [3]


def test_normalize_single_zero_with_allow_zero_returns_sentinel():
    """allow_zero=True 时,0 返回 [0] 供 delete 判定 clear-list。"""
    assert todo_list._normalize_item_ids(0, allow_zero=True) == [0]


def test_normalize_single_zero_without_allow_zero_raises():
    """update 等场景下 0 是非法 ID(IDs 从 1 开始)。"""
    import pytest

    with pytest.raises(ValueError, match="item_id=0 is not valid"):
        todo_list._normalize_item_ids(0)


def test_normalize_list_dedupes_preserving_order():
    """列表必须去重,并保留首次出现的顺序。"""
    assert todo_list._normalize_item_ids([2, 1, 2, 3, 1]) == [2, 1, 3]


def test_normalize_list_with_zero_raises():
    """v2.2.0: list 中含 0 必须报错(0 永远不是合法 ID,不能用于 clear-list)。"""
    import pytest

    with pytest.raises(ValueError, match="0 is not valid"):
        todo_list._normalize_item_ids([1, 0, 2])


def test_normalize_empty_list_raises():
    """空列表必须报错,不允许 LLM 传 item_id=[] 当 no-op。"""
    import pytest

    with pytest.raises(ValueError, match="empty list"):
        todo_list._normalize_item_ids([])


def test_normalize_negative_raises():
    """负数 ID 非法。"""
    import pytest

    with pytest.raises(ValueError, match="must be positive"):
        todo_list._normalize_item_ids(-1)
    with pytest.raises(ValueError, match="must be positive"):
        todo_list._normalize_item_ids([1, -5, 2])


def test_normalize_non_int_raises():
    """非 int 类型(字符串、bool、None、dict)都拒绝。"""
    import pytest

    # bool 是 int 的子类,要显式拒绝
    with pytest.raises(ValueError, match="bool"):
        todo_list._normalize_item_ids(True)
    with pytest.raises(ValueError, match="bool"):
        todo_list._normalize_item_ids([1, False, 2])
    # 字符串
    with pytest.raises(ValueError, match="str"):
        todo_list._normalize_item_ids("1")
    with pytest.raises(ValueError, match="str"):
        todo_list._normalize_item_ids([1, "2"])
    # None
    with pytest.raises(ValueError, match="NoneType"):
        todo_list._normalize_item_ids(None)
    # dict
    with pytest.raises(ValueError, match="dict"):
        todo_list._normalize_item_ids({"id": 1})


# ── 13. 批量 update 成功路径 ──────────────────────────


def test_update_batch_with_list_of_ids(tmp_path: Path):
    """update(item_id=[...]) 一次更新多条,共用同一个 status。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "pending"},
            {"title": "c", "status": "pending"},
            {"title": "d", "status": "pending"},
        ],
    )

    r = store.update(UMO, item_ids=[1, 3], status="done")
    assert r["ok"] is True
    # 批量永远返回 item_ids(list)
    assert r["item_ids"] == [1, 3]
    # 批量时不带单数 item_id(只单条时才有,见 test_update_returns_full_list_and_stats)
    assert "item_id" not in r
    # items 是被改的快照列表,与 item_ids 一一对应
    assert len(r["items"]) == 2
    assert [it["id"] for it in r["items"]] == [1, 3]
    assert all(it["status"] == "done" for it in r["items"])
    # 全量 list 状态也回传
    assert "list" in r
    assert "stats" in r
    assert r["stats"]["done"] == 2
    assert r["stats"]["pending"] == 2


def test_update_batch_with_notes(tmp_path: Path):
    """批量 update + notes:所有目标 item 写同一段 notes。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        items=[
            {"title": "a", "status": "in_progress"},
            {"title": "b", "status": "in_progress"},
        ],
    )

    r = store.update(UMO, item_ids=[1, 2], notes="等待外部依赖")
    assert r["ok"] is True
    # 两个 item 都带 attention(in_progress + notes)
    assert r["attention_items"] == [1, 2]
    assert all(it["attention"] for it in r["list"]["items"])


def test_update_batch_clear_notes(tmp_path: Path):
    """批量 clear_notes=True 清掉所有目标的 notes。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        items=[
            {"title": "a", "status": "in_progress", "notes": "blocker1"},
            {"title": "b", "status": "in_progress", "notes": "blocker2"},
        ],
    )
    # 先确认 attention 存在
    q = store.query(UMO)
    assert q["attention_items"] == [1, 2]

    r = store.update(UMO, item_ids=[1, 2], clear_notes=True)
    assert r["ok"] is True
    # notes 被清掉,attention_items 也归零
    assert r["attention_items"] == []
    assert all(it["notes"] == "" for it in r["items"])


def test_update_batch_empty_notes_preserves_old_value(tmp_path: Path):
    """批量 update 时 notes='' 仍视为'保留旧值'。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        items=[
            {"title": "a", "status": "in_progress", "notes": "原值1"},
            {"title": "b", "status": "in_progress", "notes": "原值2"},
        ],
    )

    r = store.update(UMO, item_ids=[1, 2], status="done")
    assert r["ok"] is True
    # notes 没传,旧值保留
    assert r["items"][0]["notes"] == "原值1"
    assert r["items"][1]["notes"] == "原值2"


# ── 14. 批量 update 失败路径 ──────────────────────────


def test_update_batch_with_missing_id_returns_error(tmp_path: Path):
    """批量 update 含不存在的 ID 必须全量回滚,不让 LLM 拿到残缺结果。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        items=[{"title": "a"}, {"title": "b"}, {"title": "c"}],
    )

    r = store.update(UMO, item_ids=[1, 999, 3], status="done")
    assert r["ok"] is False
    assert "error" in r
    assert "proposal" in r
    # 关键:不应该回传 list(stats 看起来像是部分成功)
    assert "list" not in r
    # 错误信息里要能看出哪些 ID 缺失
    assert "999" in r["error"]
    # 验证状态确实没改
    q = store.query(UMO)
    assert q["stats"]["done"] == 0
    assert q["stats"]["pending"] == 3


def test_update_batch_with_invalid_status_returns_error(tmp_path: Path):
    """批量 update 给了非法 status 必须在动数据前就 fail。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}, {"title": "b"}])

    r = store.update(UMO, item_ids=[1, 2], status="bogus")
    assert r["ok"] is False
    assert "bogus" in r["error"]
    assert "list" not in r
    # 数据原封不动
    q = store.query(UMO)
    assert q["stats"]["pending"] == 2


def test_update_batch_with_empty_list_returns_error(tmp_path: Path):
    """item_id=[] 是错误,不允许 silently no-op。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}])

    r = store.update(UMO, item_ids=[], status="done")
    assert r["ok"] is False
    assert "error" in r
    # 数据未动
    q = store.query(UMO)
    assert q["stats"]["pending"] == 1


# ── 15. 批量 delete 成功路径 ──────────────────────────


def test_delete_batch_with_list_of_ids(tmp_path: Path):
    """delete(item_id=[...]) 一次删多条。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "in_progress", "notes": "blocker"},
            {"title": "c", "status": "pending"},
            {"title": "d", "status": "pending"},
        ],
    )

    r = store.delete(UMO, item_ids=[2, 4])
    assert r["ok"] is True
    assert r["deleted"] == 2
    assert r["item_ids"] == [2, 4]
    assert r["item_count"] == 2
    # list 状态实时刷新(删了带 notes 的 in_progress,attention 归零)
    assert "list" in r
    assert "stats" in r
    assert r["stats"]["total"] == 2
    assert r["attention_items"] == []
    remaining_ids = [it["id"] for it in r["list"]["items"]]
    assert remaining_ids == [1, 3]


# Removed in v2.2.0: test_delete_batch_single_id_back_compat (replaced by test_delete_result_has_no_legacy_item_id_field)

# ── 16. 批量 delete 失败 / 边界路径 ───────────────────


def test_delete_batch_with_missing_id_returns_error(tmp_path: Path):
    """批量 delete 含不存在的 ID → 全量回滚,数据不被动。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}, {"title": "b"}])

    r = store.delete(UMO, item_ids=[1, 999])
    assert r["ok"] is False
    assert "999" in r["error"]
    assert "proposal" in r
    # 关键:不返回 list(避免误导 LLM 认为部分成功)
    assert "list" not in r
    # 验证数据没动
    q = store.query(UMO)
    assert q["stats"]["total"] == 2


def test_delete_batch_with_zero_in_list_returns_error(tmp_path: Path):
    """list 里塞 0 必须报错——v2.2.0 后 delete 不再接受 0(单项或批量)。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}])

    r = store.delete(UMO, item_ids=[1, 0])
    assert r["ok"] is False
    assert "error" in r
    # 列表必须还在(没被 clear 误触发)
    q = store.query(UMO)
    assert q["ok"] is True


def test_delete_batch_with_empty_list_returns_error(tmp_path: Path):
    """item_id=[] 是错误。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}])

    r = store.delete(UMO, item_ids=[])
    assert r["ok"] is False
    assert "error" in r
    q = store.query(UMO)
    assert q["stats"]["total"] == 1


# Removed in v2.2.0: test_delete_single_zero_still_clears_list (delete no longer accepts 0)

# ── 17. 边界:None / 字符串 / dict 直接报错 ──────────────


def test_update_with_string_item_id_returns_error(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}])
    r = store.update(UMO, item_ids="1", status="done")  # type: ignore[arg-type]
    assert r["ok"] is False
    assert "error" in r


def test_delete_with_dict_item_id_returns_error(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "a"}])
    r = store.delete(UMO, item_ids={"id": 1})  # type: ignore[arg-type]
    assert r["ok"] is False
    assert "error" in r


# ── 18. 批量 update/delete 的 list_state 完整性 ────────


def test_update_batch_refreshes_attention_list(tmp_path: Path):
    """批量把 in_progress+notes 项标 done,attention_items 应清空。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        items=[
            {"title": "a", "status": "in_progress", "notes": "blocker"},
            {"title": "b", "status": "in_progress", "notes": "blocker"},
            {"title": "c", "status": "pending"},
        ],
    )
    q = store.query(UMO)
    assert sorted(q["attention_items"]) == [1, 2]

    r = store.update(UMO, item_ids=[1, 2], status="done")
    assert r["ok"] is True
    assert r["attention_items"] == []
    assert r["stats"]["done"] == 2
    assert r["stats"]["in_progress"] == 0


def test_delete_batch_preserves_unrelated_attention(tmp_path: Path):
    """批量 delete 部分 in_progress+notes 项时,剩余的 attention_items 仍存在。"""
    store = _new_store(tmp_path)
    store.create(
        UMO,
        items=[
            {"title": "a", "status": "in_progress", "notes": "blocker"},
            {"title": "b", "status": "in_progress", "notes": "blocker"},
            {"title": "c", "status": "pending"},
        ],
    )

    # 只删 #1,保留 #2 的 attention
    r = store.delete(UMO, item_ids=[1])
    assert r["ok"] is True
    assert r["attention_items"] == [2]
    # #1 不在 list 中,#2 还在且 attention=True
    ids = [it["id"] for it in r["list"]["items"]]
    assert ids == [2, 3]
    assert r["list"]["items"][0]["attention"] is True


# ── 18b. v2.2.0 契约:delete 不再处理 item_ids=0 哨兵 ──────────


def test_delete_no_longer_handles_zero_sentinel(tmp_path: Path):
    """v2.2.0: delete 不再处理 item_ids=0(清空列表由 todo_clear 工具负责)。

    旧实现:item_id=0 → 整 list 删除 (sentinel 语义)
    新实现:item_ids=0 → ValueError,返回 ok=False,文件原封不动
    """
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "x"}])

    # delete(item_ids=0) 应返回错误而不是清空
    r = store.delete(UMO, item_ids=0)
    assert r["ok"] is False, f"item_ids=0 should be rejected, got {r}"
    assert "error" in r
    assert "0" in r["error"]

    # 文件应仍存在
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1, f"file should still exist, got {files}"

    # list 内容也未被动
    q = store.query(UMO)
    assert q["ok"] is True
    assert q["stats"]["total"] == 1


def test_delete_result_has_no_legacy_item_id_field(tmp_path: Path):
    """v2.2.0: delete 返回 dict 不应含单条兼容字段 item_id (int)。

    旧实现:单条 delete 返回 {item_id: int, item_ids: list}
    新实现:统一只返回 {item_ids: list},对齐 add/update 的 list-only 契约
    """
    store = _new_store(tmp_path)
    store.create(
        UMO,
        title="t",
        items=[{"title": "a"}, {"title": "b"}, {"title": "c"}],
    )

    # 单条删除
    r = store.delete(UMO, item_ids=2)
    assert r["ok"] is True
    assert "item_ids" in r
    assert r["item_ids"] == [2]
    # 关键:不应含旧兼容字段
    assert "item_id" not in r, (
        f"delete 应不再返回 legacy item_id (int), got keys={list(r.keys())}"
    )

    # 批量删除同样不带 item_id
    r2 = store.delete(UMO, item_ids=[1, 3])
    assert r2["ok"] is True
    assert "item_id" not in r2, (
        f"batch delete 应不再返回 legacy item_id, got keys={list(r2.keys())}"
    )


def test_clear_method_still_works_independently(tmp_path: Path):
    """v2.2.0: clear() 不再是 delete(0) 的别名,而是独立实现。

    main.py 的 action=='clear' 仍需可用,所以 clear() 保留并直接 unlink 文件。
    """
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}])

    r = store.clear(UMO)
    assert r["ok"] is True
    assert r["deleted"] == "list"
    # 文件被删
    files = list(tmp_path.glob("*.md"))
    assert files == [], f"files should be gone, got {files}"

    # 二次 clear 应返回 proposal (no list)
    r2 = store.clear(UMO)
    assert r2["ok"] is False
    assert "proposal" in r2


# ══════════════════════════════════════════════════════════════════════
# Phase 4: batch add (item: dict | list[dict])
# ══════════════════════════════════════════════════════════════════════

# ── 19. _normalize_items 工具函数 ─────────────────────


def test_normalize_items_single_dict():
    """单个 dict 必须被包裹成 [dict]。"""
    assert todo_list._normalize_items({"title": "x"}) == [{"title": "x"}]


def test_normalize_items_list_preserves_order():
    """list 保留顺序,且 dict 引用透传(调用方构造时已用 {} 字面量)。"""
    items = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
    out = todo_list._normalize_items(items)
    assert out == items
    assert [it["title"] for it in out] == ["a", "b", "c"]


def test_normalize_items_empty_list_raises():
    """空列表报错,不允许 LLM 传 item=[] 当 no-op。"""
    import pytest

    with pytest.raises(ValueError, match="empty list"):
        todo_list._normalize_items([])


def test_normalize_items_non_dict_input_raises():
    """非 dict / 非 list 的输入直接拒绝。"""
    import pytest

    for bad in ["x", 1, 1.5, True, None]:
        with pytest.raises(ValueError):
            todo_list._normalize_items(bad)


def test_normalize_items_list_with_non_dict_raises():
    """list 中含非 dict 元素必须报错,告诉 LLM 哪一项坏掉。"""
    import pytest

    with pytest.raises(ValueError, match=r"item\[1\]"):
        todo_list._normalize_items([{"title": "a"}, "bad", {"title": "c"}])
    with pytest.raises(ValueError, match=r"item\[0\]"):
        todo_list._normalize_items([123, {"title": "a"}])


# ── 20. 批量 add 成功路径 ─────────────────────────────


def test_add_batch_with_list_of_dicts(tmp_path: Path):
    """add(item=[...]) 一次追加多条,各自带 title/status/notes。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="计划", items=[{"title": "first"}])

    r = store.add(
        UMO,
        [
            {"title": "a", "status": "in_progress", "notes": "blocker"},
            {"title": "b", "status": "pending"},
            {"title": "c", "status": "done"},
        ],
    )
    assert r["ok"] is True
    # 批量永远返回 item_ids(list)
    assert r["item_ids"] == [2, 3, 4]
    # 批量时不带单数 item_id
    assert "item_id" not in r
    # items 是新增的快照列表
    assert len(r["items"]) == 3
    assert [it["title"] for it in r["items"]] == ["a", "b", "c"]
    assert [it["status"] for it in r["items"]] == ["in_progress", "pending", "done"]
    # 列表与统计刷新
    assert r["item_count"] == 4
    assert r["stats"]["total"] == 4
    # in_progress+notes → attention
    assert r["attention_items"] == [2]


def test_add_batch_preserves_mixed_statuses(tmp_path: Path):
    """批量 add 每个 item 可独立指定 status,不会被强制成同一个。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])

    r = store.add(
        UMO,
        [
            {"title": "a", "status": "done"},
            {"title": "b", "status": "cancelled"},
            {"title": "c", "status": "in_progress", "notes": "wait"},
        ],
    )
    assert r["ok"] is True
    statuses = [it["status"] for it in r["items"]]
    assert statuses == ["done", "cancelled", "in_progress"]
    # stats 正确反映各种状态
    assert r["stats"]["done"] == 1
    assert r["stats"]["cancelled"] == 1
    assert r["stats"]["in_progress"] == 1
    # effective_total 排除 cancelled
    assert r["stats"]["effective_total"] == 3


def test_add_batch_ids_are_contiguous(tmp_path: Path):
    """批量 add 必须分配连续自增 ID,不出现跳号。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}, {"title": "y"}])

    r = store.add(
        UMO,
        [{"title": "a"}, {"title": "b"}, {"title": "c"}],
    )
    assert r["ok"] is True
    assert r["item_ids"] == [3, 4, 5]
    # 持久化到文件后,query 看到的 ID 也是连续的
    q = store.query(UMO)
    file_ids = [it["id"] for it in q["list"]["items"]]
    assert file_ids == [1, 2, 3, 4, 5]


def test_add_batch_default_status_is_pending(tmp_path: Path):
    """没指定 status 的 item 默认为 pending(与单条 add 语义一致)。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])  # 已有 1 个 pending

    r = store.add(UMO, [{"title": "a"}, {"title": "b"}])
    assert r["ok"] is True
    # 新加的两条都是 pending(默认)
    assert all(it["status"] == "pending" for it in r["items"])
    # stats:1 (种子) + 2 (本次) = 3
    assert r["stats"]["pending"] == 3


# ── 21. 批量 add 失败 / 边界路径 ──────────────────────


def test_add_batch_with_empty_list_returns_error(tmp_path: Path):
    """item=[] 报错,不允许 silently no-op。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])

    r = store.add(UMO, [])
    assert r["ok"] is False
    assert "error" in r
    # 列表不能动
    q = store.query(UMO)
    assert q["stats"]["total"] == 1


def test_add_batch_with_invalid_status_rolls_back(tmp_path: Path):
    """任一 item 含非法 status → 全量回滚,不能留半改数据。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])

    r = store.add(
        UMO,
        [
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "bogus"},  # 这条非法
            {"title": "c"},
        ],
    )
    assert r["ok"] is False
    assert "bogus" in r["error"]
    assert "1" in r["error"] or "[1]" in r["error"]  # 告诉是哪一项坏
    # list 字段不回传(避免误导 LLM 部分成功)
    assert "list" not in r
    # 列表原封不动
    q = store.query(UMO)
    assert q["stats"]["total"] == 1
    assert [it["title"] for it in q["list"]["items"]] == ["x"]


def test_add_batch_exceeds_max_items_returns_error(tmp_path: Path):
    """追加后总数超过 MAX_ITEMS → 全量回滚。"""
    store = _new_store(tmp_path)
    # 先造一个几乎满的 list(MAX_ITEMS - 1 = 99 条)
    seed_items = [{"title": f"t{i}"} for i in range(1, todo_list.MAX_ITEMS)]
    store.create(UMO, items=seed_items)
    q = store.query(UMO)
    assert q["stats"]["total"] == todo_list.MAX_ITEMS - 1

    # 一次加 2 条会超 100
    r = store.add(UMO, [{"title": "x"}, {"title": "y"}])
    assert r["ok"] is False
    assert "MAX_ITEMS" in r["error"].upper() or "上限" in r["error"]
    # 数据原封不动
    q2 = store.query(UMO)
    assert q2["stats"]["total"] == todo_list.MAX_ITEMS - 1


def test_add_batch_when_no_list_returns_proposal(tmp_path: Path):
    """没有 list 时批量 add 同样返回 proposal(与单条 add 一致)。"""
    store = _new_store(tmp_path)
    r = store.add(UMO, [{"title": "a"}, {"title": "b"}])
    assert r["ok"] is False
    assert "proposal" in r
    assert "list" not in r


def test_add_with_string_item_returns_error(tmp_path: Path):
    """item='...' 直接拒绝(单条 / 批量列表里都该拒绝)。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])
    r = store.add(UMO, "not a dict")  # type: ignore[arg-type]
    assert r["ok"] is False
    assert "error" in r


def test_add_batch_with_mixed_types_in_list_rolls_back(tmp_path: Path):
    """list 含非 dict 元素 → 报错,不动数据。"""
    store = _new_store(tmp_path)
    store.create(UMO, items=[{"title": "x"}])

    r = store.add(
        UMO,
        [{"title": "a"}, "bad", {"title": "c"}],  # type: ignore[list-item]
    )
    assert r["ok"] is False
    assert "error" in r
    q = store.query(UMO)
    assert q["stats"]["total"] == 1


# ── build_filename 边界覆盖(回填 Task 1.1 review 删掉的测试) ──


def test_filename_uses_minute_precision(tmp_path: Path):
    """Filenames use %Y%m%d%H%M (12-digit timestamp), not %Y%m%d (8-digit).

    给定固定 datetime(带秒=30),验证文件名:
    1. 拼成 {umo_清洗后}_{ts}.md 格式(`:` 全部替换为 `_`,v2.11)
    2. 秒被截断 → 仅 12 位 YYYYMMDDhhmm
    """
    fixed_dt = datetime(2026, 6, 7, 15, 45, 30)
    fname = todo_list.build_filename("webchat:astrbot", when=fixed_dt)
    # Expect pattern: webchat_astrbot_202606071545.md
    assert fname == "webchat_astrbot_202606071545.md", (
        f"Expected minute-precision filename, got {fname!r}"
    )
    # The timestamp portion is 12 digits (YYYYMMDDhhmm)
    ts_part = fname.split("_")[-1].removesuffix(".md")
    assert len(ts_part) == 12, f"Expected 12-digit timestamp, got {ts_part!r}"
    assert ts_part == "202606071545"


def test_build_filename_sha256_fallback_for_long_umo(tmp_path: Path):
    """umo 拼出 > MAX_FILENAME_LEN(200) → 回退到 sha256[:16]_{ts}.md。

    v2.11: 输入从 sender_key 切到 umo 后,`:` 全部替换为 `_` 再拼 ts。
    超长 umo 常见于 session id + uuid 拼接;若仍然试图用 plaintext 拼文件名,
    会超出 200 字符限制,触发 sha256 fallback。
    """
    fixed_dt = datetime(2026, 6, 13, 12, 1)
    long_sid = "x" * 250
    umo = f"webchat:PrivateMessage:{long_sid}"
    fname = todo_list.build_filename(umo, when=fixed_dt)

    expected_hash = hashlib.sha256(umo.encode("utf-8")).hexdigest()[:16]
    expected = f"{expected_hash}_202606131201.md"
    assert fname == expected, f"Expected sha256 fallback for long umo, got {fname!r}"
    # Sanity: fallback 路径应 < MAX_FILENAME_LEN
    assert len(fname) <= todo_list.MAX_FILENAME_LEN
    # Sanity: 时间戳部分仍在末尾
    assert fname.endswith("_202606131201.md")


def test_build_filename_sha256_fallback_for_unsafe_chars(tmp_path: Path):
    """umo 含 OS 非法字符(如 <>"/\\|?*)→ 回退到 sha256[:16]_{ts}.md。

    v2.11: umo 的 `:` 全部替换为 `_`,但若清洗后仍含 <>"|?* 等字符,
    ILLEGAL_FILENAME_CHARS.search 仍会命中 → 触发 fallback。
    """
    fixed_dt = datetime(2026, 6, 13, 12, 1)
    # 用带 < > 的 umo,确保 ILLEGAL_FILENAME_CHARS 命中
    umo = "webchat:<user:bad>"
    fname = todo_list.build_filename(umo, when=fixed_dt)

    expected_hash = hashlib.sha256(umo.encode("utf-8")).hexdigest()[:16]
    expected = f"{expected_hash}_202606131201.md"
    assert fname == expected, (
        f"Expected sha256 fallback for unsafe chars, got {fname!r}"
    )
    # Fallback 后文件名不含任何 OS 非法字符
    assert todo_list.ILLEGAL_FILENAME_CHARS.search(fname) is None
    # 时间戳部分仍在末尾
    assert fname.endswith("_202606131201.md")


# ── v2.2.0 Task 1.2: add() 参数重命名 item→items, 移除单条兼容字段 ──


def test_add_accepts_items_param_not_item(tmp_path: Path):
    """v2.2.0: add() 签名应为 items(不再是 item)。"""
    import inspect

    store = _new_store(tmp_path)
    sig = inspect.signature(store.add)
    assert "items" in sig.parameters, (
        f"add() should have 'items' param, got {list(sig.parameters)}"
    )
    assert "item" not in sig.parameters, (
        f"add() should not have 'item' param, got {list(sig.parameters)}"
    )


def test_add_result_has_no_legacy_item_id_field(tmp_path: Path):
    """v2.2.0: add 返回 dict 不应含单条兼容字段 item_id (int) 或 item (dict)。

    单条时仍只返回 list 形式 item_ids + items,前端统一按 list 处理。
    """
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}])
    r = store.add(UMO, [{"title": "b"}])
    assert r["ok"] is True
    assert "item_id" not in r, (
        f"add should not return legacy item_id, got keys={list(r.keys())}"
    )
    assert "item" not in r, (
        f"add should not return legacy item, got keys={list(r.keys())}"
    )
    # 仍应有 list 形式
    assert "item_ids" in r
    assert "items" in r
    assert r["item_ids"] == [2]
    assert r["items"][0]["title"] == "b"


# ── v2.2.0 Task 1.3: update() 参数重命名 item_id→item_ids, 移除单条兼容字段 ──


def test_update_accepts_item_ids_param_not_item_id(tmp_path: Path):
    """v2.2.0: update() 签名应为 item_ids(不再是 item_id)。"""
    import inspect

    store = _new_store(tmp_path)
    sig = inspect.signature(store.update)
    assert "item_ids" in sig.parameters, (
        f"update() should have 'item_ids' param, got {list(sig.parameters)}"
    )
    assert "item_id" not in sig.parameters, (
        f"update() should not have 'item_id' param, got {list(sig.parameters)}"
    )


def test_update_result_has_no_legacy_item_id_field(tmp_path: Path):
    """v2.2.0: update 返回 dict 不应含单条兼容字段 item_id (int) 或 item (dict)。

    单条时仍只返回 list 形式 item_ids + items,前端统一按 list 处理。
    """
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}, {"title": "b"}])
    r = store.update(UMO, 1, status="done")  # 单条
    assert r["ok"] is True
    assert "item_id" not in r, (
        f"update should not return legacy item_id, got keys={list(r.keys())}"
    )
    assert "item" not in r, (
        f"update should not return legacy item, got keys={list(r.keys())}"
    )
    # 仍应有 list 形式
    assert "item_ids" in r
    assert "items" in r
    assert r["item_ids"] == [1]
    assert r["items"][0]["status"] == "done"


# ── v2.2.0 Task 2.2: TodoStore.modify() 三 mode 分发 ──


def test_modify_add_mode_appends_items(tmp_path: Path):
    """modify(mode='add') 等价于 add(items=...)。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "existing"}])
    r = store.modify(UMO, mode="add", items=[{"title": "new1"}, {"title": "new2"}])
    assert r["ok"] is True
    assert r["item_count"] == 3
    assert r["item_ids"] == [2, 3]


def test_modify_update_mode_modifies_items(tmp_path: Path):
    """modify(mode='update') 等价于 update(item_ids=..., status=..., notes=...)。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}, {"title": "b"}])
    r = store.modify(UMO, mode="update", item_ids=[1, 2], status="done")
    assert r["ok"] is True
    assert r["item_ids"] == [1, 2]


def test_modify_delete_mode_removes_items(tmp_path: Path):
    """modify(mode='delete') 等价于 delete(item_ids=...)。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}, {"title": "b"}, {"title": "c"}])
    r = store.modify(UMO, mode="delete", item_ids=[1, 3])
    assert r["ok"] is True
    assert r["deleted"] == 2
    assert r["item_count"] == 1


def test_modify_unknown_mode_returns_error(tmp_path: Path):
    """modify 收到未知 mode 返回错误而非崩溃。"""
    store = _new_store(tmp_path)
    r = store.modify(UMO, mode="bogus")
    assert r["ok"] is False
    assert "bogus" in r["error"]
    assert "add/update/delete" in r["proposal"]


# impl_t2_2 标记: 4 个 modify 测试在 tests/test_todo_list.py 末尾追加, 时间 2026-06-13 12:42 (CST)


# ── v2.2.0 Task 2.3: modify() notes 三态 None 桥接 ──


def test_modify_update_with_none_notes_keeps_existing(tmp_path: Path):
    """modify update 模式 notes=None → 保留旧值。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a", "notes": "原始备注"}])
    r = store.modify(UMO, mode="update", item_ids=1, status="done", notes=None)
    assert r["ok"] is True
    q = store.query(UMO)
    assert q["list"]["items"][0]["notes"] == "原始备注"


def test_modify_update_with_empty_string_notes_clears(tmp_path: Path):
    """modify update 模式 notes='' → 清空 notes。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a", "notes": "原始"}])
    r = store.modify(UMO, mode="update", item_ids=1, notes="")
    assert r["ok"] is True
    q = store.query(UMO)
    assert q["list"]["items"][0]["notes"] == ""


def test_modify_update_with_new_notes_overwrites(tmp_path: Path):
    """modify update 模式 notes='xxx' → 覆盖。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a", "notes": "原始"}])
    r = store.modify(UMO, mode="update", item_ids=1, notes="新内容")
    assert r["ok"] is True
    q = store.query(UMO)
    assert q["list"]["items"][0]["notes"] == "新内容"


# ── v2.2.0 Task 4.1: 空值拒绝测试 ──


def test_create_rejects_empty_items(tmp_path: Path):
    """create(items=[]) 必须返回错误。"""
    store = _new_store(tmp_path)
    r = store.create(UMO, title="t", items=[])
    assert r["ok"] is False
    assert "items" in r["error"].lower()


def test_modify_add_rejects_empty_items(tmp_path: Path):
    """modify(mode='add') items=[] 拒绝。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}])
    r = store.modify(UMO, mode="add", items=[])
    assert r["ok"] is False
    assert "items" in r["error"].lower() or "add" in r["error"].lower()


def test_modify_add_rejects_none_items(tmp_path: Path):
    """modify(mode='add') items=None 拒绝。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}])
    r = store.modify(UMO, mode="add", items=None)
    assert r["ok"] is False


def test_modify_update_rejects_none_item_ids(tmp_path: Path):
    """modify(mode='update') item_ids=None 拒绝。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}])
    r = store.modify(UMO, mode="update", item_ids=None, status="done")
    assert r["ok"] is False
    assert "item_ids" in r["error"]


def test_modify_delete_rejects_none_item_ids(tmp_path: Path):
    """modify(mode='delete') item_ids=None 拒绝。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}])
    r = store.modify(UMO, mode="delete", item_ids=None)
    assert r["ok"] is False
    assert "item_ids" in r["error"]


# ── v2.2.0 Task 4.2: MAX_ITEMS 临界值测试 ──


def test_modify_add_at_max_items_boundary(tmp_path: Path):
    """add 触发 100 上限临界值测试。

    - 99 + 1 add = 100 → 成功
    - 100 + 1 add → 失败,总数保持 100
    """
    store = _new_store(tmp_path)
    items_99 = [{"title": f"t{i}"} for i in range(99)]
    r1 = store.create(UMO, title="t", items=items_99)
    assert r1["ok"] is True
    assert r1["item_count"] == 99

    # add 1 个 → 成功
    r2 = store.modify(UMO, mode="add", items=[{"title": "t99"}])
    assert r2["ok"] is True
    assert r2["item_count"] == 100

    # 再 add 1 个 → 失败
    r3 = store.modify(UMO, mode="add", items=[{"title": "t100"}])
    assert r3["ok"] is False
    assert r3["item_count"] == 100  # 不变


def test_modify_add_rollback_at_max_boundary(tmp_path: Path):
    """add 触发 100 上限时,已存在数据保持不变。"""
    store = _new_store(tmp_path)
    items_99 = [{"title": f"t{i}"} for i in range(99)]
    store.create(UMO, title="t", items=items_99)

    # 尝试加 2 个(99+2=101 > 100)→ 失败,文件状态不变
    r = store.modify(UMO, mode="add", items=[{"title": "x"}, {"title": "y"}])
    assert r["ok"] is False
    q = store.query(UMO)
    assert len(q["list"]["items"]) == 99  # 已存在数据未被修改


# ── v2.2.0 Task 4.3: 回滚一致性测试 ──


def test_modify_update_rollback_on_missing_id(tmp_path: Path):
    """update 模式中,任一 item_ids 不存在 → 全量回滚。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}, {"title": "b"}])
    # item_ids=[1, 999],其中 999 不存在
    r = store.modify(UMO, mode="update", item_ids=[1, 999], status="done")
    assert r["ok"] is False
    # id=1 的状态应保持原状(pending),不被部分更新
    q = store.query(UMO)
    assert q["list"]["items"][0]["status"] == "pending"


def test_modify_delete_rollback_on_missing_id(tmp_path: Path):
    """delete 模式中,任一 item_ids 不存在 → 全量回滚。"""
    store = _new_store(tmp_path)
    store.create(UMO, title="t", items=[{"title": "a"}, {"title": "b"}])
    r = store.modify(UMO, mode="delete", item_ids=[1, 999])
    assert r["ok"] is False
    # 列表应保持 2 项
    q = store.query(UMO)
    assert len(q["list"]["items"]) == 2


# ── v2.2.0 Task 4.4: 已完成标记 ──
# 旧测试清理已在前面 subagent 完成:
# - test_create_with_from_file_*      → 替换为 test_create_no_longer_accepts_from_file
# - test_update_clear_notes_*          → 替换为 test_update_batch_clear_notes
# - test_delete_with_zero_item_id_*    → 替换为 test_delete_no_longer_handles_zero_sentinel
# impl_t4 标记: 9 个新测试追加, 时间 2026-06-13 15:05 (CST)


# ── v2.9 import_from_path (新增) ──────────────────────


def _write_md(tmp_path: Path, content: str, name: str = "plan.md") -> Path:
    """Helper: 写一个 .md 文件到 tmp_path,返回绝对路径。"""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


_SAMPLE_PLAN_MD = """\
---
sender_key: webchat:astrbot
platform: webchat
sender_id: "astrbot"
title: 测试计划
created_at: 2026-06-18T10:00:00
updated_at: 2026-06-18T10:00:00
---

# 测试计划

- [ ] **(1)** 设计 API
- [x] **(2)** 写文档 *(已完成 README)*
- [~] **(3)** 实现功能 *(进行中)*
- [-] **(4)** 旧功能 *(已取消)*
"""


def test_import_from_path_happy_path(tmp_path: Path):
    """正常 .md 文件 → 返回 4 个 items + title + 空 error."""
    p = _write_md(tmp_path, _SAMPLE_PLAN_MD)
    items, title, err = todo_list.import_from_path(str(p))
    assert err == "", f"unexpected error: {err}"
    assert title == "测试计划"
    assert len(items) == 4
    # 状态全部解析正确
    statuses = [it["status"] for it in items]
    assert statuses == ["pending", "done", "in_progress", "cancelled"]
    # notes 解析正确
    assert items[1]["notes"] == "已完成 README"
    assert items[2]["notes"] == "进行中"


def test_import_from_path_none_returns_error(tmp_path: Path):
    items, title, err = todo_list.import_from_path(None)  # type: ignore[arg-type]
    assert items == []
    assert title == ""
    assert "from_path 必须是字符串路径" in err


def test_import_from_path_relative_path_returns_error(tmp_path: Path):
    items, _, err = todo_list.import_from_path("plans/sprint.md")
    assert items == []
    assert "路径必须是绝对路径" in err


def test_import_from_path_not_exists(tmp_path: Path):
    items, _, err = todo_list.import_from_path(str(tmp_path / "no_such.md"))
    assert items == []
    assert "文件不存在" in err


def test_import_from_path_directory_not_file(tmp_path: Path):
    d = tmp_path / "subdir"
    d.mkdir()
    items, _, err = todo_list.import_from_path(str(d))
    assert items == []
    assert "不是文件" in err


def test_import_from_path_oversize(tmp_path: Path):
    """1.1 MB 文件 → 拒绝(MAX_IMPORT_BYTES = 1MB)。"""
    big = tmp_path / "big.md"
    big.write_bytes(b"- [ ] x\n" * (1024 * 1024 // 6 + 1))  # 略大于 1MB
    items, _, err = todo_list.import_from_path(str(big))
    assert items == []
    assert "文件过大" in err


def test_import_from_path_frontmatter_only_no_items(tmp_path: Path):
    """只有 frontmatter 没有 checkbox item → '未找到任何 item'。"""
    p = _write_md(tmp_path, "---\nsender_key: x:1\ntitle: empty\n---\n\n# empty\n")
    items, _, err = todo_list.import_from_path(str(p))
    assert items == []
    assert "未找到任何 item" in err


def test_import_from_path_h1_only_no_items(tmp_path: Path):
    """只有 H1 标题,没有任何 checkbox。"""
    p = _write_md(tmp_path, "# 仅标题\n\n没有 checkbox 列表。\n")
    items, _, err = todo_list.import_from_path(str(p))
    assert items == []
    assert "未找到任何 item" in err


def test_import_from_path_empty_file(tmp_path: Path):
    """0 字节文件 → '未找到任何 item'(不是 size error)。"""
    p = _write_md(tmp_path, "")
    items, _, err = todo_list.import_from_path(str(p))
    assert items == []
    assert "未找到任何 item" in err


def test_import_from_path_non_utf8_bytes(tmp_path: Path):
    """非 UTF-8 字节 → 读取失败。"""
    p = tmp_path / "binary.md"
    p.write_bytes(b"\xff\xfe\xfd  bad encoding")
    items, _, err = todo_list.import_from_path(str(p))
    assert items == []
    assert "读取失败" in err


def test_import_from_path_symlink_to_valid_md(tmp_path: Path):
    """软链接指向合法 .md → 正常 adopt(证明 read-only 契约对 symlink 目标成立)。

    Windows: 软链接需 admin/Developer Mode,在普通用户下不可用 → skip。
    POSIX: 应正常通过。
    """
    if sys.platform == "win32":
        import pytest

        pytest.skip("Windows symlink requires admin / Developer Mode")
    real = _write_md(tmp_path, _SAMPLE_PLAN_MD, name="real.md")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    items, title, err = todo_list.import_from_path(str(link))
    assert err == ""
    assert title == "测试计划"
    assert len(items) == 4


def test_import_from_path_no_frontmatter_no_h1_uses_untitled(tmp_path: Path):
    """无 frontmatter 无 H1,只有 checkbox → title == 'Untitled' (parse_md 默认值)。"""
    p = _write_md(tmp_path, "- [ ] **(1)** 一个任务\n- [x] **(2)** 另一个 *(完成)*\n")
    items, title, err = todo_list.import_from_path(str(p))
    assert err == ""
    assert title == "Untitled"
    assert len(items) == 2


def test_import_from_path_preserves_parsed_ids(tmp_path: Path):
    """有 ID 缺口的 items (1, 2, 5) — 透传原始 ID(不重排),重排由 create() 完成。"""
    md = "- [ ] **(1)** a\n- [ ] **(2)** b\n- [ ] **(5)** gap\n"
    p = _write_md(tmp_path, md)
    items, _, err = todo_list.import_from_path(str(p))
    assert err == ""
    assert [it["id"] for it in items] == [1, 2, 5]


# ── adopt contract: 锁住 TodoStore.create() 的领养语义 ──
# 未来如果 create() 改了重排 ID / 写当前 sender_key / now() 时间戳的行为,
# 这些测试会失败,提醒同步更新本 spec.


def test_store_adopt_contract_renumbers_ids_from_1(tmp_path: Path):
    """create() 必须丢弃入参 items 的 id,重排从 1 开始。"""
    store = _new_store(tmp_path)
    r = store.create(
        UMO,
        title="t",
        items=[{"id": 99, "title": "x", "status": "pending"}],
    )
    assert r["ok"] is True
    # 读回落盘文件确认 id 被重排(r["file"] 是 str,需包成 Path)
    parsed = todo_list.parse_md(Path(r["file"]).read_text(encoding="utf-8"))
    assert parsed["items"][0]["id"] == 1


def test_store_adopt_contract_overwrites_umo(tmp_path: Path):
    """create() 入参的 umo 必须写入新文件(覆盖原文件 / 入参 items 的 umo)。"""
    store = _new_store(tmp_path)
    r = store.create(UMO, title="t", items=[{"title": "x"}])
    assert r["ok"] is True
    parsed = todo_list.parse_md(Path(r["file"]).read_text(encoding="utf-8"))
    assert parsed["umo"] == UMO


def test_store_adopt_contract_writes_now_timestamp(tmp_path: Path):
    """create() 写 created_at / updated_at = now() (秒级精度)。"""
    store = _new_store(tmp_path)
    before = datetime.now().isoformat(timespec="seconds")
    r = store.create(UMO, title="t", items=[{"title": "x"}])
    after = datetime.now().isoformat(timespec="seconds")
    parsed = todo_list.parse_md(Path(r["file"]).read_text(encoding="utf-8"))
    assert before <= parsed["created_at"] <= after
    assert before <= parsed["updated_at"] <= after


# ── TodoCreateTool 工具层 from_path 测试 (v2.9 新增) ────────
# 工具类本身在 main.py 里,这里通过 mock ContextWrapper 验证 call() 的 XOR 路径。
# mock 模板来自 tests/test_inta_shell.py:7 (AsyncMock + MagicMock 模式)。


def _mock_context(
    umo: str = "qq_official:PrivateMessage:787D6B0A323239BE9CAFB7F5CE7EFFDD",
):
    """Helper: 构造一个 mock ContextWrapper[AstrAgentContext]。

    v2.11: todo_list 的隔离键已切到 umo,所以这里只 mock ``event.unified_msg_origin``,
    不再 mock ``get_platform_name`` / ``get_sender_id``(extract_umo 不再调用它们)。
    """
    event = MagicMock()
    event.unified_msg_origin = umo
    inner = MagicMock()
    inner.event = event
    ctx = MagicMock()
    ctx.context = inner
    return ctx


def test_todo_create_tool_from_path_happy_path(tmp_path: Path, monkeypatch):
    """from_path 正常 → 落盘文件存在,文件名前缀是 mock umo 清洗后的字符串。"""
    # 把 StarTools.get_data_dir 引导到 tmp_path,避免污染真实 data_dir
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    # 写 fixture
    src = _write_md(tmp_path, _SAMPLE_PLAN_MD, name="src.md")
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(tool.call(ctx, items=None, from_path=str(src)))
    assert '"ok": true' in result
    # 落盘文件存在,前缀是 mock umo 清洗后的字符串(`:` → `_`)
    files = list((tmp_path / "todos").glob("*.md"))
    assert any(
        "qq_official_PrivateMessage_787D6B0A323239BE9CAFB7F5CE7EFFDD" in f.name
        for f in files
    ), f"expected file not found, got {files}"


def test_todo_create_tool_from_path_and_items_both_returns_xor_error(
    tmp_path: Path, monkeypatch
):
    """from_path + items 同时传 → XOR error。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    src = _write_md(tmp_path, _SAMPLE_PLAN_MD)
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(tool.call(ctx, items=[{"title": "x"}], from_path=str(src)))
    assert '"ok": false' in result
    assert "必须二选一" in result
    assert "不能同时传" in result


def test_todo_create_tool_neither_from_path_nor_items_returns_xor_error(
    tmp_path: Path, monkeypatch
):
    """from_path 与 items 都缺(None) → XOR error。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(tool.call(ctx, items=None, from_path=""))
    assert '"ok": false' in result
    assert "必须二选一" in result
    assert "不能都不传" in result


def test_todo_create_tool_from_path_title_override(tmp_path: Path, monkeypatch):
    """from_path + title="X" → 落盘 title == "X" (覆盖文件中的 title)。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    src = _write_md(tmp_path, _SAMPLE_PLAN_MD)
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(
        tool.call(ctx, items=None, from_path=str(src), title="自定义标题")
    )
    assert '"ok": true' in result
    # 读回确认
    files = list((tmp_path / "todos").glob("qq_official_*.md"))
    assert files
    parsed = todo_list.parse_md(files[0].read_text(encoding="utf-8"))
    assert parsed["title"] == "自定义标题"


def test_todo_create_tool_from_path_title_passthrough(tmp_path: Path, monkeypatch):
    """from_path 无 title → 保留文件中的 title。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    src = _write_md(tmp_path, _SAMPLE_PLAN_MD)
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(tool.call(ctx, items=None, from_path=str(src)))
    assert '"ok": true' in result
    files = list((tmp_path / "todos").glob("qq_official_*.md"))
    parsed = todo_list.parse_md(files[0].read_text(encoding="utf-8"))
    assert parsed["title"] == "测试计划"


def test_todo_create_tool_from_path_cross_user_adopt(tmp_path: Path, monkeypatch):
    """跨用户: 导入 webchat fixture → 新文件前缀是 mock 用户的 qq_official。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    # 直接写 fixture 内容到一个临时位置
    fixture_path = tmp_path / "src.md"
    fixture_path.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    asyncio.run(tool.call(ctx, items=None, from_path=str(fixture_path)))
    # 验证没有 webchat_ 前缀的文件,只有 mock 用户的 qq_official_ 前缀
    files = list((tmp_path / "todos").glob("*.md"))
    assert not any("webchat_" in f.name for f in files)
    assert any("qq_official_" in f.name for f in files)


def test_todo_create_tool_from_path_file_not_exists(tmp_path: Path, monkeypatch):
    """from_path 指向不存在的文件 → ok=False + error 含 '文件不存在'。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(
        tool.call(ctx, items=None, from_path=str(tmp_path / "no_such.md"))
    )
    assert '"ok": false' in result
    assert "文件不存在" in result


def test_todo_create_tool_from_path_end_to_end_preserves_status(
    tmp_path: Path, monkeypatch
):
    """端到端: 导入 fixture → IDs 从 1 重排,status 保留(cancelled 仍是 cancelled)。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    fixture_path = tmp_path / "src.md"
    fixture_path.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    asyncio.run(tool.call(ctx, items=None, from_path=str(fixture_path)))
    # 读回新落盘文件
    files = list((tmp_path / "todos").glob("qq_official_*.md"))
    assert files
    parsed = todo_list.parse_md(files[0].read_text(encoding="utf-8"))
    # IDs 从 1 重排,共 6 个
    assert [it["id"] for it in parsed["items"]] == [1, 2, 3, 4, 5, 6]
    # status 保留
    statuses = [it["status"] for it in parsed["items"]]
    assert statuses == [
        "done",
        "done",
        "in_progress",
        "pending",
        "pending",
        "cancelled",
    ]


def test_todo_create_tool_from_path_none_returns_xor_error(tmp_path: Path, monkeypatch):
    """from_path=None (LLM 传 JSON null) → XOR 防御性触发,error 含 '必须二选一'。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(
        tool.call(ctx, items=None, from_path=None)  # type: ignore[arg-type]
    )
    assert '"ok": false' in result
    assert "必须二选一" in result


def test_todo_create_tool_from_path_relative_path_rejected(tmp_path: Path, monkeypatch):
    """from_path 相对路径 → ok=False + error 含 '路径必须是绝对路径'。"""
    monkeypatch.setattr(
        main_mod.StarTools,
        "get_data_dir",
        classmethod(lambda cls, plugin_name=None: str(tmp_path)),
    )
    tool = main_mod.TodoCreateTool()
    ctx = _mock_context()
    result = asyncio.run(tool.call(ctx, items=None, from_path="plans/sprint.md"))
    assert '"ok": false' in result
    assert "路径必须是绝对路径" in result
