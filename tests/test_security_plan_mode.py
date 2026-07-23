"""tools.security.plan_mode 测试 — PlanModeController 状态 + 过滤 + reminder 注入。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from tools.security.plan_mode import PlanModeController

_SCHEMA_PATH = Path(__file__).parent.parent / "_conf_schema.json"


def _load_plan_mode_blocked_defaults() -> list[str]:
    data = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return data["plan_mode"]["items"]["plan_mode_blocked_tools"]["default"]


def _make_req(blocked_tools: list[str] | None = None):
    """构造最小 ProviderRequest 模拟对象。"""
    req = MagicMock()
    req.func_tool = None  # 默认空
    req.contexts = []
    return req


def _make_event(umo: str = "test:umo"):
    ev = MagicMock()
    ev.unified_msg_origin = umo
    return ev


def _make_tool_set(names: list[str]):
    """Construct a real ToolSet containing named test tools."""
    from astrbot.core.agent.tool import ToolSet

    tool_set = ToolSet()
    for name in names:
        tool = MagicMock()
        tool.name = name
        tool_set.add_tool(tool)
    return tool_set


# ── 状态查询 ─────────────────────────────────────────────


def test_is_active_unknown_umo_returns_false():
    """未注册 umo → build 模式(False)。"""
    c = PlanModeController(get_config=lambda: {})
    assert c.is_active("unknown:umo") is False


def test_is_active_none_returns_false():
    """umo=None → False(防御性)。"""
    c = PlanModeController(get_config=lambda: {})
    assert c.is_active(None) is False


def test_count_active_zero_initially():
    c = PlanModeController(get_config=lambda: {})
    assert c.count_active() == 0


def test_count_active_counts_true_entries():
    c = PlanModeController(get_config=lambda: {})
    c.activate("umo-1")
    c.activate("umo-2")
    c.deactivate("umo-1")
    # umo-1: deactivated (not counted)
    # umo-2: still active
    assert c.count_active() == 1


# ── 状态变更 ─────────────────────────────────────────────


def test_activate_sets_state_and_resets_reminded():
    c = PlanModeController(get_config=lambda: {})
    c._plan_reminded["umo-1"] = True
    c.activate("umo-1")
    assert c.is_active("umo-1") is True
    assert c.has_reminded("umo-1") is False  # reminded reset


def test_activate_already_active_resets_reminded():
    c = PlanModeController(get_config=lambda: {})
    c.activate("umo-1")
    c._plan_reminded["umo-1"] = True
    c.activate("umo-1")  # re-activate
    assert c.is_active("umo-1") is True
    assert c.has_reminded("umo-1") is False  # reminded reset


def test_deactivate_returns_was_active():
    c = PlanModeController(get_config=lambda: {})
    c.activate("umo-1")
    assert c.deactivate("umo-1") is True
    assert c.is_active("umo-1") is False


def test_deactivate_unknown_umo_returns_false():
    c = PlanModeController(get_config=lambda: {})
    assert c.deactivate("unknown") is False


def test_deactivate_clears_reminded():
    c = PlanModeController(get_config=lambda: {})
    c.activate("umo-1")
    c._plan_reminded["umo-1"] = True
    c.deactivate("umo-1")
    assert "umo-1" not in c._plan_reminded


# ── filter_request: build 模式 no-op ─────────────────────


def test_filter_request_build_mode_is_noop():
    """build 模式(默认):不做事,即使配置了 blocked_tools。"""
    cfg = {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    c = PlanModeController(get_config=lambda: cfg)
    event = _make_event()
    req = _make_req()
    req.func_tool = MagicMock()
    req.func_tool.tools = [MagicMock(name="astrbot_file_remove")]
    c.filter_request(event, req)
    # tool list 未被修改(仍是原 list)
    assert len(req.func_tool.tools) == 1


def test_filter_request_restores_tools_after_leaving_plan_mode():
    """plan → build restores the original ToolSet on the next request."""
    cfg = {"plan_mode_blocked_tools": ["todo_create"]}
    c = PlanModeController(get_config=lambda: cfg)
    event = _make_event("umo-restore")
    req = _make_req()
    original = _make_tool_set(["todo_create", "todo_query"])
    req.func_tool = original

    c.activate("umo-restore")
    c.filter_request(event, req)
    assert [tool.name for tool in req.func_tool.tools] == ["todo_query"]
    assert req.func_tool is not original

    c.deactivate("umo-restore")
    c.filter_request(event, req)
    assert req.func_tool is original
    assert [tool.name for tool in req.func_tool.tools] == [
        "todo_create",
        "todo_query",
    ]
    assert "umo-restore" not in c._original_tool_sets


def test_toolset_restore_isolated_per_umo():
    """Restoration uses the snapshot belonging to the current session only."""
    cfg = {"plan_mode_blocked_tools": ["todo_create"]}
    c = PlanModeController(get_config=lambda: cfg)
    event_a = _make_event("umo-a")
    event_b = _make_event("umo-b")
    req_a = _make_req()
    req_b = _make_req()
    original_a = _make_tool_set(["todo_create", "todo_query"])
    original_b = _make_tool_set(["todo_create", "todo_query"])
    req_a.func_tool = original_a
    req_b.func_tool = original_b

    c.activate("umo-a")
    c.filter_request(event_a, req_a)
    c.filter_request(event_b, req_b)

    c.deactivate("umo-a")
    c.filter_request(event_a, req_a)

    assert req_a.func_tool is original_a
    assert req_b.func_tool is original_b
    assert [tool.name for tool in req_b.func_tool.tools] == [
        "todo_create",
        "todo_query",
    ]


# ── filter_request: plan 模式过滤 ─────────────────────────


def test_filter_request_plan_mode_filters_tools():
    """plan 模式:从 req.func_tool 过滤 blocked_tools 集合。"""
    cfg = {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    c = PlanModeController(get_config=lambda: cfg)
    c.activate("umo-1")
    event = _make_event("umo-1")

    # 模拟 ToolSet 替换
    from astrbot.core.agent.tool import ToolSet

    ts = ToolSet()
    for name in ["astrbot_file_remove", "es_search", "code_check"]:
        t = MagicMock()
        t.name = name
        ts.add_tool(t)
    req = _make_req()
    req.func_tool = ts

    c.filter_request(event, req)
    # 被过滤后,只保留 es_search + code_check
    remaining_names = [t.name for t in req.func_tool.tools]
    assert "astrbot_file_remove" not in remaining_names
    assert "es_search" in remaining_names
    assert "code_check" in remaining_names


def test_filter_request_plan_mode_no_config_logs_warning(caplog):
    """plan 模式激活但 blocked_tools 为空 → warning 日志。"""
    cfg = {"plan_mode_blocked_tools": []}
    c = PlanModeController(get_config=lambda: cfg)
    c.activate("umo-1")
    event = _make_event("umo-1")
    req = _make_req()
    req.func_tool = MagicMock()
    req.func_tool.tools = [MagicMock(name="es_search")]

    with caplog.at_level("WARNING"):
        c.filter_request(event, req)
    assert "blocked_tools 为空" in caplog.text


# ── filter_request: reminder 注入 ─────────────────────────


def _make_req_with_toolset():
    """构造一个 func_tool 非空的 req(让 reminder 注入路径能跑通)。"""
    from astrbot.core.agent.tool import ToolSet

    req = MagicMock()
    req.contexts = []
    ts = ToolSet()
    t = MagicMock()
    t.name = "es_search"
    ts.add_tool(t)
    req.func_tool = ts
    return req


def test_filter_request_injects_reminder_first_time():
    """plan 模式首轮:在 user message 末尾追加 reminder。"""
    cfg = {
        "plan_mode_blocked_tools": ["astrbot_file_remove"],
        "plan_mode_reminder": "你处于 plan 模式,被禁用:{blocked}",
    }
    c = PlanModeController(get_config=lambda: cfg)
    c.activate("umo-1")
    event = _make_event("umo-1")
    req = _make_req_with_toolset()
    req.contexts = [{"role": "user", "content": "请调研文件结构"}]

    c.filter_request(event, req)
    content = req.contexts[0]["content"]
    assert "<system-reminder>" in content
    assert "你处于 plan 模式" in content
    assert "astrbot_file_remove" in content  # {blocked} 占位符已替换


def test_filter_request_does_not_inject_reminder_twice():
    """plan 模式第二轮:reminder 不再注入(reminded=True 后跳过)。"""
    cfg = {
        "plan_mode_blocked_tools": ["astrbot_file_remove"],
        "plan_mode_reminder": "你处于 plan 模式",
    }
    c = PlanModeController(get_config=lambda: cfg)
    c.activate("umo-1")
    c._plan_reminded["umo-1"] = True  # 标记为已注入
    event = _make_event("umo-1")
    req = _make_req_with_toolset()
    req.contexts = [{"role": "user", "content": "原始消息"}]

    c.filter_request(event, req)
    # reminder 不应被注入
    assert req.contexts[0]["content"] == "原始消息"


def test_filter_request_empty_reminder_template_marks_reminded():
    """配置中 reminder 为空 → 标记为已注入(避免每轮检查)。"""
    cfg = {"plan_mode_blocked_tools": ["x"], "plan_mode_reminder": ""}
    c = PlanModeController(get_config=lambda: cfg)
    c.activate("umo-1")
    event = _make_event("umo-1")
    req = _make_req_with_toolset()
    req.contexts = [{"role": "user", "content": "msg"}]

    c.filter_request(event, req)
    assert c.has_reminded("umo-1") is True  # 标记为已注入
    assert req.contexts[0]["content"] == "msg"  # reminder 没被注入


def test_filter_request_reminder_appended_to_last_user_message():
    """reminder 追加到最后一条 user 消息(不是 system / assistant)。"""
    cfg = {
        "plan_mode_blocked_tools": ["x"],
        "plan_mode_reminder": "PLAN_REMINDER_TEXT",
    }
    c = PlanModeController(get_config=lambda: cfg)
    c.activate("umo-1")
    event = _make_event("umo-1")
    req = _make_req_with_toolset()
    req.contexts = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2-original"},
    ]

    c.filter_request(event, req)
    # system + u1 + a1 不变
    assert req.contexts[0]["content"] == "sys"
    assert req.contexts[1]["content"] == "u1"
    assert req.contexts[2]["content"] == "a1"
    # 最后一条 user message 被追加
    assert "u2-original" in req.contexts[3]["content"]
    assert "PLAN_REMINDER_TEXT" in req.contexts[3]["content"]


class TestVivadoBlockedInPlanMode:
    """plan 模式默认应隐藏 11 个 vivado 写工具。"""

    VIVADO_WRITE_TOOLS = [
        "mcp_vivado__add_files",
        "mcp_vivado__close_project",
        "mcp_vivado__create_project",
        "mcp_vivado__generate_bitstream",
        "mcp_vivado__open_project",
        "mcp_vivado__program_device",
        "mcp_vivado__run_implementation",
        "mcp_vivado__run_synthesis",
        "mcp_vivado__run_tcl",
        "mcp_vivado__start_session",
        "mcp_vivado__stop_session",
    ]

    def test_default_blocked_includes_all_vivado_writes(self):
        defaults = _load_plan_mode_blocked_defaults()
        for tool in self.VIVADO_WRITE_TOOLS:
            assert tool in defaults, f"{tool} 应在 plan_mode_blocked_tools.default 中"

    def test_readonly_tools_not_blocked(self):
        defaults = _load_plan_mode_blocked_defaults()
        readonly = [
            "mcp_vivado__list_sessions",
            "mcp_vivado__get_status",
            "mcp_vivado__get_critical_warnings",
            "mcp_vivado__report",
        ]
        for tool in readonly:
            assert tool not in defaults

    def test_eleven_writes_are_grouped_at_end(self):
        defaults = _load_plan_mode_blocked_defaults()
        assert len(self.VIVADO_WRITE_TOOLS) == 11
        assert defaults[-11:] == self.VIVADO_WRITE_TOOLS
