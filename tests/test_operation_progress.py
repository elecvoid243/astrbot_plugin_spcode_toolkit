"""tests/test_operation_progress.py — 进度存储模块单测。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import time

import pytest
from tools import operation_progress as prog


@pytest.fixture(autouse=True)
def _clean_records() -> None:
    """每个用例前后清空模块级进度存储(class 用例不吃 setup_function)。"""
    prog._records.clear()


class TestBegin:
    def test_first_begin_returns_true(self):
        assert prog.begin("u1", "project_load") is True
        rec = prog.query("u1")
        assert rec is not None
        assert rec["status"] == "running"
        assert rec["operation"] == "project_load"
        assert rec["messages"] == []
        assert rec["current_step"] == ""
        assert rec["finished_at"] is None
        assert rec["reason"] is None

    def test_begin_rejected_while_running(self):
        assert prog.begin("u1", "project_load") is True
        assert prog.begin("u1", "project_unload") is False
        # 原记录不被覆盖
        assert prog.query("u1")["operation"] == "project_load"

    def test_begin_allowed_after_finish(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=True)
        assert prog.begin("u1", "project_unload") is True
        assert prog.query("u1")["operation"] == "project_unload"

    def test_begin_independent_per_umo(self):
        prog.begin("u1", "project_load")
        assert prog.begin("u2", "project_load") is True


class TestAppend:
    def test_append_collects_messages(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "⏳ [1/3] AGENTS.md 初始化")
        prog.append("u1", "✅ 项目已加载: /x")
        rec = prog.query("u1")
        assert rec["messages"] == ["⏳ [1/3] AGENTS.md 初始化", "✅ 项目已加载: /x"]

    def test_append_updates_current_step_on_step_prefix(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "ℹ️ 信息行")
        assert prog.query("u1")["current_step"] == ""
        prog.append("u1", "⏳ [2/3] codegraph init: /x")
        assert prog.query("u1")["current_step"] == "⏳ [2/3] codegraph init: /x"
        prog.append("u1", "🔄 正在重启 codegraph MCP...")
        assert prog.query("u1")["current_step"] == "🔄 正在重启 codegraph MCP..."

    def test_current_step_takes_first_line_only(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "⏳ 步骤标题\n第二行详情")
        assert prog.query("u1")["current_step"] == "⏳ 步骤标题"

    def test_append_unknown_umo_is_noop(self):
        prog.append("ghost", "⏳ x")  # 不抛异常


class TestFinish:
    def test_finish_done(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=True)
        rec = prog.query("u1")
        assert rec["status"] == "done"
        assert rec["finished_at"] is not None
        assert rec["reason"] is None

    def test_finish_failed_with_reason(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=False, reason="path_unsafe")
        rec = prog.query("u1")
        assert rec["status"] == "failed"
        assert rec["reason"] == "path_unsafe"

    def test_finish_only_transitions_from_running(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=False, reason="path_unsafe")
        prog.finish("u1", ok=True)  # 已终态,不再覆盖
        assert prog.query("u1")["status"] == "failed"

    def test_finish_unknown_umo_is_noop(self):
        prog.finish("ghost", ok=True)  # 不抛异常


class TestQuery:
    def test_query_unknown_returns_none(self):
        assert prog.query("ghost") is None

    def test_query_returns_copy(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "⏳ x")
        rec = prog.query("u1")
        rec["messages"].append("污染")
        assert prog.query("u1")["messages"] == ["⏳ x"]


class TestTtl:
    def test_finished_record_purged_after_ttl(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=True)
        prog._records["u1"]["finished_at"] = time.time() - 301
        # 下一次 begin 触发惰性清理
        assert prog.begin("u1", "project_unload") is True
        assert prog.query("u1")["operation"] == "project_unload"

    def test_running_record_never_purged(self):
        prog.begin("u1", "project_load")
        prog._records["u1"]["started_at"] = time.time() - 99999
        assert prog.begin("u2", "project_load") is True
        assert prog.query("u1") is not None


class TestProgressList:
    def test_append_mirrors_to_store(self):
        prog.begin("u1", "project_load")
        messages = prog.ProgressList("u1")
        messages.append("⏳ [1/3] init")
        messages.append("✅ done")
        rec = prog.query("u1")
        assert rec["messages"] == ["⏳ [1/3] init", "✅ done"]
        assert rec["current_step"] == "⏳ [1/3] init"
        # 仍是普通 list 行为
        assert list(messages) == ["⏳ [1/3] init", "✅ done"]

    def test_unknown_umo_noop(self):
        messages = prog.ProgressList("ghost")
        messages.append("x")  # 不抛异常
        assert list(messages) == ["x"]
