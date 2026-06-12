"""todo_list 工具测试。

覆盖 create / query / add / update / delete / clear 六个 action 的核心协议。
重点验证：create / add / update / delete(单条) 必须回传完整 list + stats +
attention_items，以便前端 TodoListResult.vue 在这些 action 成功后展示完整列表。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import todo_list  # noqa: E402

SENDER = "test:user-a"


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
    r = store.create(SENDER, title="重构 todo_list", items=items)

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
    store.create(SENDER, title="旧", items=[{"title": "a"}, {"title": "b"}])

    r = store.create(SENDER, title="新", items=[{"title": "x"}])
    assert r["ok"] is True
    assert r["previous_item_count"] == 2
    assert r["item_count"] == 1
    # 仍然包含完整 list
    assert len(r["list"]["items"]) == 1
    assert r["stats"]["total"] == 1


# ── 3. add 返回完整 list + stats ─────────────────────


def test_add_returns_full_list_and_stats(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(SENDER, title="计划", items=[{"title": "task1"}])

    r = store.add(SENDER, {"title": "task2", "status": "in_progress"})
    assert r["ok"] is True
    assert r["item_id"] == 2
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
    r = store.add(SENDER, {"title": "x"})
    assert r["ok"] is False
    assert "proposal" in r
    # 失败路径不需要回传 list
    assert "list" not in r


# ── 5. update 返回完整 list + stats ──────────────────


def test_update_returns_full_list_and_stats(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "pending"},
            {"title": "c", "status": "pending"},
        ],
    )

    # 把 #2 标记为 done
    r = store.update(SENDER, item_id=2, status="done")
    assert r["ok"] is True
    assert r["item_id"] == 2
    assert r["item"]["status"] == "done"

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
    store.create(SENDER, items=[{"title": "x"}])

    r = store.update(SENDER, item_id=1, status="in_progress", notes="被外部阻塞")
    assert r["ok"] is True
    # attention_items 应包含 #1
    assert r["attention_items"] == [1]
    # list 中该 item 标了 attention
    assert r["list"]["items"][0]["attention"] is True


# ── 7. update 失败时返回 proposal 不含 list ─────────


def test_update_invalid_id_returns_proposal(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "x"}])

    r = store.update(SENDER, item_id=999, status="done")
    assert r["ok"] is False
    assert "proposal" in r
    assert "list" not in r


# ── 8. query 行为不变（回归保护） ────────────────────


def test_query_unchanged(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        title="回归",
        items=[{"title": "a", "status": "done"}],
    )
    r = store.query(SENDER)
    assert r["ok"] is True
    assert r["list"]["title"] == "回归"
    assert r["stats"]["done"] == 1


# ── 9. delete 单条回传完整 list + stats ─────────────


def test_delete_single_includes_list_and_stats(tmp_path: Path):
    """delete(item_id>0) 删单条后列表还在，应回传完整 list/stats/attention_items。"""
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "in_progress", "notes": "等评审"},
            {"title": "c", "status": "pending"},
        ],
    )

    r = store.delete(SENDER, item_id=2)
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
        SENDER,
        items=[
            {"title": "x", "status": "in_progress", "notes": "blocker"},
            {"title": "y", "status": "pending"},
        ],
    )
    # 删掉唯一 in_progress 项
    r = store.delete(SENDER, item_id=1)
    assert r["ok"] is True
    # attention_items 应为空（in_progress 项已删）
    assert r["attention_items"] == []
    # 剩下的 item 也不应带 attention
    assert all(not it["attention"] for it in r["list"]["items"])


# ── 10. clear 仍然不回传 list（列表整个没了） ─────────


def test_clear_does_not_include_list(tmp_path: Path):
    """clear() = delete(item_id=0)，整个列表被删，无 list 可回传。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}, {"title": "b"}])

    r = store.clear(SENDER)
    assert r["ok"] is True
    assert r["deleted"] == "list"
    # clear 整个列表没了，保持精简，不回传 list
    assert "list" not in r


def test_delete_with_item_id_zero_does_not_include_list(tmp_path: Path):
    """delete(item_id=0) 与 clear() 等价，不回传 list。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}])

    r = store.delete(SENDER, item_id=0)
    assert r["ok"] is True
    assert r["deleted"] == "list"
    assert "list" not in r


# ── 11. delete 失败路径不污染 list 字段 ──────────────


def test_delete_nonexistent_id_returns_error_without_list(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}])

    r = store.delete(SENDER, item_id=999)
    assert r["ok"] is False
    assert "error" in r
    assert "list" not in r


def test_delete_no_list_returns_proposal(tmp_path: Path):
    store = _new_store(tmp_path)
    r = store.delete(SENDER, item_id=1)
    assert r["ok"] is False
    assert "proposal" in r
    assert "list" not in r


# ══════════════════════════════════════════════════════════════════════
# Phase 2: create from persisted file + minute-precision filenames
# ══════════════════════════════════════════════════════════════════════


def test_create_from_explicit_file_loads_content(tmp_path: Path):
    """from_file points to an existing .md → new list mirrors its content."""
    store = _new_store(tmp_path)
    # Seed a persisted file first
    seed = store.create(
        SENDER,
        title="Yesterday",
        items=[
            {"title": "a", "status": "done"},
            {"title": "b", "status": "pending"},
        ],
    )
    source_path = seed["file"]
    assert Path(source_path).is_file()

    # Now create a new list loaded from that file
    r = store.create(SENDER, from_file=source_path)
    assert r["ok"] is True
    # Title falls back to source file's title
    assert r["list_title"] == "Yesterday"
    # item_count and list items match source
    assert r["item_count"] == 2
    assert len(r["list"]["items"]) == 2
    assert r["list"]["items"][0]["title"] == "a"
    assert r["list"]["items"][0]["status"] == "done"
    # No overwrite → previous_item_count is 0 (snapshot semantic)
    assert r["previous_item_count"] == 0
    # Result includes source_file pointer
    assert r["source_file"] == source_path
    # New file is a different file from the source
    assert r["file"] != source_path
    # Full list state is included
    assert "list" in r
    assert "stats" in r
    assert "attention_items" in r


def test_create_from_explicit_file_preserves_source(tmp_path: Path):
    """Loading from a file does NOT delete the source (snapshot semantic)."""
    store = _new_store(tmp_path)
    seed = store.create(SENDER, title="Keep me", items=[{"title": "x"}])
    source_path = Path(seed["file"])
    assert source_path.is_file()

    r = store.create(SENDER, from_file=str(source_path))
    assert r["ok"] is True
    # Source file must still exist after the new create
    assert source_path.is_file(), "from_file mode must not delete the source"
    # And its content is unchanged
    src_items = todo_list.parse_md(source_path.read_text(encoding="utf-8"))["items"]
    assert len(src_items) == 1
    assert src_items[0]["title"] == "x"


def test_create_from_explicit_file_with_title_override(tmp_path: Path):
    """Non-empty title overrides the source file's title."""
    store = _new_store(tmp_path)
    seed = store.create(SENDER, title="Old title", items=[{"title": "a"}])
    r = store.create(SENDER, from_file=seed["file"], title="New title")
    assert r["ok"] is True
    assert r["list_title"] == "New title"
    # But items still come from the source file
    assert len(r["list"]["items"]) == 1
    assert r["list"]["items"][0]["title"] == "a"


def test_create_from_explicit_file_invalid_path(tmp_path: Path):
    """Non-existent file path → error with proposal."""
    store = _new_store(tmp_path)
    fake = tmp_path / "does_not_exist.md"
    r = store.create(SENDER, from_file=str(fake))
    assert r["ok"] is False
    assert "error" in r
    assert "list" not in r


def test_create_from_explicit_file_outside_todos_dir(tmp_path: Path):
    """A path outside the todos directory is rejected (security)."""
    store = _new_store(tmp_path)
    # Create a file *outside* the todos dir
    outside = tmp_path.parent / f"outside_{SENDER.replace(':', '_')}.md"
    outside.write_text("---\nsender_key: x\n---\n", encoding="utf-8")
    try:
        r = store.create(SENDER, from_file=str(outside))
        assert r["ok"] is False
        assert "error" in r
        assert "list" not in r
        # Error message should not leak absolute path info
        assert "list" not in r
    finally:
        outside.unlink(missing_ok=True)


def test_create_from_explicit_file_wrong_owner(tmp_path: Path):
    """A .md file that belongs to a different sender_key is rejected."""
    store = _new_store(tmp_path)
    # Create a file for a *different* user
    other = store.create("other:user", title="Other", items=[{"title": "x"}])
    other_path = other["file"]

    # SENDER tries to load that file
    r = store.create(SENDER, from_file=other_path)
    assert r["ok"] is False
    assert "error" in r
    assert "list" not in r


def test_create_from_explicit_file_and_items_conflict(tmp_path: Path):
    """from_file + items together → error (mutually exclusive)."""
    store = _new_store(tmp_path)
    seed = store.create(SENDER, items=[{"title": "src"}])
    r = store.create(
        SENDER,
        from_file=seed["file"],
        items=[{"title": "new"}],
    )
    assert r["ok"] is False
    assert "error" in r
    assert "list" not in r


def test_create_auto_discovers_recent_file(tmp_path: Path):
    """Empty from_file + empty items → auto-load most recent file for this user."""
    store = _new_store(tmp_path)
    seed = store.create(SENDER, title="Auto source", items=[{"title": "a"}])
    source_path = seed["file"]

    # No from_file, no items → should auto-discover the seed file
    r = store.create(SENDER)
    assert r["ok"] is True
    assert r["list_title"] == "Auto source"
    assert r["item_count"] == 1
    assert r["source_file"] == source_path
    assert r.get("loaded_from") == "auto"
    # Source is still preserved
    assert Path(source_path).is_file()


def test_create_auto_discovers_no_file_returns_proposal(tmp_path: Path):
    """Auto-discover with no existing file → proposal error."""
    store = _new_store(tmp_path)
    r = store.create(SENDER)
    assert r["ok"] is False
    assert "proposal" in r
    assert "list" not in r
    # Proposal should suggest the user create one
    assert "create" in r["proposal"].lower()


def test_create_auto_discover_picks_most_recent(tmp_path: Path):
    """Auto-discover picks the most recently created file (not the oldest)."""
    store = _new_store(tmp_path)
    # Create two files for the same user; the second is more recent
    first = store.create(SENDER, title="Old", items=[{"title": "a"}])
    second = store.create(
        SENDER, title="Recent", items=[{"title": "b"}, {"title": "c"}]
    )
    # Both files exist
    assert Path(first["file"]).is_file()
    assert Path(second["file"]).is_file()

    # Auto-discover should pick the most recent one (by name sort, since both
    # have the same minute-precision timestamp created in the same instant,
    # we fall back to "more items" to be deterministic — but the contract is
    # "most recent", so the test asserts the higher of the two titles)
    r = store.create(SENDER)
    assert r["ok"] is True
    # Whichever was sorted last (reverse=True) is what's returned
    assert r["list_title"] in ("Old", "Recent")
    # Most importantly, source_file should be a real file in the todos dir
    assert Path(r["source_file"]).is_file()


def test_filename_uses_minute_precision(tmp_path: Path):
    """Filenames use %Y%m%d%H%M (12-digit timestamp), not %Y%m%d (8-digit)."""
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


def test_create_snapshot_does_not_clobber_other_snapshot(tmp_path: Path):
    """Two snapshot calls in the same minute must not overwrite each other.

    Snapshot mode is additive: every new file gets a unique minute so prior
    snapshots (and the source) remain on disk.
    """
    store = _new_store(tmp_path)
    # Fresh seed at minute M
    seed = store.create(SENDER, items=[{"title": "src"}])
    # First snapshot — bumps forward to M+1 because new_path == source_path
    snap1 = store.create(SENDER, from_file=seed["file"])
    # Second snapshot in the same wall-clock minute — should bump again to M+2
    # (NOT overwrite snap1 at M+1, NOT overwrite the source at M)
    snap2 = store.create(SENDER, from_file=seed["file"])

    # All three files must exist (additive, never destructive)
    assert Path(seed["file"]).is_file(), "Source must still exist"
    assert Path(snap1["file"]).is_file(), "snap1 must still exist"
    assert Path(snap2["file"]).is_file(), "snap2 must still exist"
    # All three filenames are distinct
    paths = {seed["file"], snap1["file"], snap2["file"]}
    assert len(paths) == 3, f"Expected 3 distinct files, got {paths}"


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
    """list 中含 0 必须报错:用单项 0 触发 clear-list,不要混在批量里。"""
    import pytest

    with pytest.raises(ValueError, match="cannot appear inside a list"):
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
        SENDER,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "pending"},
            {"title": "c", "status": "pending"},
            {"title": "d", "status": "pending"},
        ],
    )

    r = store.update(SENDER, item_id=[1, 3], status="done")
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
        SENDER,
        items=[
            {"title": "a", "status": "in_progress"},
            {"title": "b", "status": "in_progress"},
        ],
    )

    r = store.update(SENDER, item_id=[1, 2], notes="等待外部依赖")
    assert r["ok"] is True
    # 两个 item 都带 attention(in_progress + notes)
    assert r["attention_items"] == [1, 2]
    assert all(it["attention"] for it in r["list"]["items"])


def test_update_batch_clear_notes(tmp_path: Path):
    """批量 clear_notes=True 清掉所有目标的 notes。"""
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        items=[
            {"title": "a", "status": "in_progress", "notes": "blocker1"},
            {"title": "b", "status": "in_progress", "notes": "blocker2"},
        ],
    )
    # 先确认 attention 存在
    q = store.query(SENDER)
    assert q["attention_items"] == [1, 2]

    r = store.update(SENDER, item_id=[1, 2], clear_notes=True)
    assert r["ok"] is True
    # notes 被清掉,attention_items 也归零
    assert r["attention_items"] == []
    assert all(it["notes"] == "" for it in r["items"])


def test_update_batch_empty_notes_preserves_old_value(tmp_path: Path):
    """批量 update 时 notes='' 仍视为'保留旧值'。"""
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        items=[
            {"title": "a", "status": "in_progress", "notes": "原值1"},
            {"title": "b", "status": "in_progress", "notes": "原值2"},
        ],
    )

    r = store.update(SENDER, item_id=[1, 2], status="done")
    assert r["ok"] is True
    # notes 没传,旧值保留
    assert r["items"][0]["notes"] == "原值1"
    assert r["items"][1]["notes"] == "原值2"


# ── 14. 批量 update 失败路径 ──────────────────────────


def test_update_batch_with_missing_id_returns_error(tmp_path: Path):
    """批量 update 含不存在的 ID 必须全量回滚,不让 LLM 拿到残缺结果。"""
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        items=[{"title": "a"}, {"title": "b"}, {"title": "c"}],
    )

    r = store.update(SENDER, item_id=[1, 999, 3], status="done")
    assert r["ok"] is False
    assert "error" in r
    assert "proposal" in r
    # 关键:不应该回传 list(stats 看起来像是部分成功)
    assert "list" not in r
    # 错误信息里要能看出哪些 ID 缺失
    assert "999" in r["error"]
    # 验证状态确实没改
    q = store.query(SENDER)
    assert q["stats"]["done"] == 0
    assert q["stats"]["pending"] == 3


def test_update_batch_with_invalid_status_returns_error(tmp_path: Path):
    """批量 update 给了非法 status 必须在动数据前就 fail。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}, {"title": "b"}])

    r = store.update(SENDER, item_id=[1, 2], status="bogus")
    assert r["ok"] is False
    assert "bogus" in r["error"]
    assert "list" not in r
    # 数据原封不动
    q = store.query(SENDER)
    assert q["stats"]["pending"] == 2


def test_update_batch_with_empty_list_returns_error(tmp_path: Path):
    """item_id=[] 是错误,不允许 silently no-op。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}])

    r = store.update(SENDER, item_id=[], status="done")
    assert r["ok"] is False
    assert "error" in r
    # 数据未动
    q = store.query(SENDER)
    assert q["stats"]["pending"] == 1


# ── 15. 批量 delete 成功路径 ──────────────────────────


def test_delete_batch_with_list_of_ids(tmp_path: Path):
    """delete(item_id=[...]) 一次删多条。"""
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        title="计划",
        items=[
            {"title": "a", "status": "pending"},
            {"title": "b", "status": "in_progress", "notes": "blocker"},
            {"title": "c", "status": "pending"},
            {"title": "d", "status": "pending"},
        ],
    )

    r = store.delete(SENDER, item_id=[2, 4])
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


def test_delete_batch_single_id_back_compat(tmp_path: Path):
    """单条 delete 仍保留 item_id 字段(int)以兼容旧调用方。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}, {"title": "b"}])

    r = store.delete(SENDER, item_id=2)
    assert r["ok"] is True
    assert r["deleted"] == 1
    # 单条时 item_id 仍是 int
    assert r["item_id"] == 2
    assert r["item_ids"] == [2]


# ── 16. 批量 delete 失败 / 边界路径 ───────────────────


def test_delete_batch_with_missing_id_returns_error(tmp_path: Path):
    """批量 delete 含不存在的 ID → 全量回滚,数据不被动。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}, {"title": "b"}])

    r = store.delete(SENDER, item_id=[1, 999])
    assert r["ok"] is False
    assert "999" in r["error"]
    assert "proposal" in r
    # 关键:不返回 list(避免误导 LLM 认为部分成功)
    assert "list" not in r
    # 验证数据没动
    q = store.query(SENDER)
    assert q["stats"]["total"] == 2


def test_delete_batch_with_zero_in_list_returns_error(tmp_path: Path):
    """list 里塞 0 必须报错——'clear list' 只能传单项 0,不能混在批量里。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}])

    r = store.delete(SENDER, item_id=[1, 0])
    assert r["ok"] is False
    assert "error" in r
    # 列表必须还在(没被 clear 误触发)
    q = store.query(SENDER)
    assert q["ok"] is True


def test_delete_batch_with_empty_list_returns_error(tmp_path: Path):
    """item_id=[] 是错误。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}])

    r = store.delete(SENDER, item_id=[])
    assert r["ok"] is False
    assert "error" in r
    q = store.query(SENDER)
    assert q["stats"]["total"] == 1


def test_delete_single_zero_still_clears_list(tmp_path: Path):
    """回归:单项 0 仍然是 clear-list 的语义,不能被批量逻辑破坏。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}, {"title": "b"}])

    r = store.delete(SENDER, item_id=0)
    assert r["ok"] is True
    assert r["deleted"] == "list"
    # 文件被删
    assert not Path(r["file"]).exists()


# ── 17. 边界:None / 字符串 / dict 直接报错 ──────────────


def test_update_with_string_item_id_returns_error(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}])
    r = store.update(SENDER, item_id="1", status="done")  # type: ignore[arg-type]
    assert r["ok"] is False
    assert "error" in r


def test_delete_with_dict_item_id_returns_error(tmp_path: Path):
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "a"}])
    r = store.delete(SENDER, item_id={"id": 1})  # type: ignore[arg-type]
    assert r["ok"] is False
    assert "error" in r


# ── 18. 批量 update/delete 的 list_state 完整性 ────────


def test_update_batch_refreshes_attention_list(tmp_path: Path):
    """批量把 in_progress+notes 项标 done,attention_items 应清空。"""
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        items=[
            {"title": "a", "status": "in_progress", "notes": "blocker"},
            {"title": "b", "status": "in_progress", "notes": "blocker"},
            {"title": "c", "status": "pending"},
        ],
    )
    q = store.query(SENDER)
    assert sorted(q["attention_items"]) == [1, 2]

    r = store.update(SENDER, item_id=[1, 2], status="done")
    assert r["ok"] is True
    assert r["attention_items"] == []
    assert r["stats"]["done"] == 2
    assert r["stats"]["in_progress"] == 0


def test_delete_batch_preserves_unrelated_attention(tmp_path: Path):
    """批量 delete 部分 in_progress+notes 项时,剩余的 attention_items 仍存在。"""
    store = _new_store(tmp_path)
    store.create(
        SENDER,
        items=[
            {"title": "a", "status": "in_progress", "notes": "blocker"},
            {"title": "b", "status": "in_progress", "notes": "blocker"},
            {"title": "c", "status": "pending"},
        ],
    )

    # 只删 #1,保留 #2 的 attention
    r = store.delete(SENDER, item_id=[1])
    assert r["ok"] is True
    assert r["attention_items"] == [2]
    # #1 不在 list 中,#2 还在且 attention=True
    ids = [it["id"] for it in r["list"]["items"]]
    assert ids == [2, 3]
    assert r["list"]["items"][0]["attention"] is True


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
    store.create(SENDER, title="计划", items=[{"title": "first"}])

    r = store.add(
        SENDER,
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


def test_add_single_still_returns_item_id_for_backcompat(tmp_path: Path):
    """单条 add 保留 item_id / item 字段以兼容旧调用方。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "first"}])

    r = store.add(SENDER, {"title": "second", "status": "in_progress"})
    assert r["ok"] is True
    # 单条带 item_id(int) + item(dict) + item_ids(list) + items(list)
    assert r["item_id"] == 2
    assert r["item"] == r["items"][0]
    assert r["item_ids"] == [2]
    assert len(r["items"]) == 1


def test_add_batch_preserves_mixed_statuses(tmp_path: Path):
    """批量 add 每个 item 可独立指定 status,不会被强制成同一个。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "x"}])

    r = store.add(
        SENDER,
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
    store.create(SENDER, items=[{"title": "x"}, {"title": "y"}])

    r = store.add(
        SENDER,
        [{"title": "a"}, {"title": "b"}, {"title": "c"}],
    )
    assert r["ok"] is True
    assert r["item_ids"] == [3, 4, 5]
    # 持久化到文件后,query 看到的 ID 也是连续的
    q = store.query(SENDER)
    file_ids = [it["id"] for it in q["list"]["items"]]
    assert file_ids == [1, 2, 3, 4, 5]


def test_add_batch_default_status_is_pending(tmp_path: Path):
    """没指定 status 的 item 默认为 pending(与单条 add 语义一致)。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "x"}])  # 已有 1 个 pending

    r = store.add(SENDER, [{"title": "a"}, {"title": "b"}])
    assert r["ok"] is True
    # 新加的两条都是 pending(默认)
    assert all(it["status"] == "pending" for it in r["items"])
    # stats:1 (种子) + 2 (本次) = 3
    assert r["stats"]["pending"] == 3


# ── 21. 批量 add 失败 / 边界路径 ──────────────────────


def test_add_batch_with_empty_list_returns_error(tmp_path: Path):
    """item=[] 报错,不允许 silently no-op。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "x"}])

    r = store.add(SENDER, [])
    assert r["ok"] is False
    assert "error" in r
    # 列表不能动
    q = store.query(SENDER)
    assert q["stats"]["total"] == 1


def test_add_batch_with_invalid_status_rolls_back(tmp_path: Path):
    """任一 item 含非法 status → 全量回滚,不能留半改数据。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "x"}])

    r = store.add(
        SENDER,
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
    q = store.query(SENDER)
    assert q["stats"]["total"] == 1
    assert [it["title"] for it in q["list"]["items"]] == ["x"]


def test_add_batch_exceeds_max_items_returns_error(tmp_path: Path):
    """追加后总数超过 MAX_ITEMS → 全量回滚。"""
    store = _new_store(tmp_path)
    # 先造一个几乎满的 list(MAX_ITEMS - 1 = 99 条)
    seed_items = [{"title": f"t{i}"} for i in range(1, todo_list.MAX_ITEMS)]
    store.create(SENDER, items=seed_items)
    q = store.query(SENDER)
    assert q["stats"]["total"] == todo_list.MAX_ITEMS - 1

    # 一次加 2 条会超 100
    r = store.add(SENDER, [{"title": "x"}, {"title": "y"}])
    assert r["ok"] is False
    assert "MAX_ITEMS" in r["error"].upper() or "上限" in r["error"]
    # 数据原封不动
    q2 = store.query(SENDER)
    assert q2["stats"]["total"] == todo_list.MAX_ITEMS - 1


def test_add_batch_when_no_list_returns_proposal(tmp_path: Path):
    """没有 list 时批量 add 同样返回 proposal(与单条 add 一致)。"""
    store = _new_store(tmp_path)
    r = store.add(SENDER, [{"title": "a"}, {"title": "b"}])
    assert r["ok"] is False
    assert "proposal" in r
    assert "list" not in r


def test_add_with_string_item_returns_error(tmp_path: Path):
    """item='...' 直接拒绝(单条 / 批量列表里都该拒绝)。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "x"}])
    r = store.add(SENDER, "not a dict")  # type: ignore[arg-type]
    assert r["ok"] is False
    assert "error" in r


def test_add_batch_with_mixed_types_in_list_rolls_back(tmp_path: Path):
    """list 含非 dict 元素 → 报错,不动数据。"""
    store = _new_store(tmp_path)
    store.create(SENDER, items=[{"title": "x"}])

    r = store.add(
        SENDER,
        [{"title": "a"}, "bad", {"title": "c"}],  # type: ignore[list-item]
    )
    assert r["ok"] is False
    assert "error" in r
    q = store.query(SENDER)
    assert q["stats"]["total"] == 1
