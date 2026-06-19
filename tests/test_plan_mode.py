"""v2.8 /plan 和 /build 命令测试。

覆盖:
- /plan 命令(简化 API:无子命令,直接激活 plan 模式)
  - 首次激活、重复激活、blocked_tools 为空时的告警
- /build 命令(简化 API:无子命令,直接退出 plan 模式回到默认)
  - 退出活跃 plan、未激活时幂等
- per-umo 状态隔离
- _filter_func_tool 辅助方法(过滤逻辑 + 新建 ToolSet 防止 in-place 修改)
- _plan_filter_tools 钩子(过滤工具 + reminder 注入规则)
  - build 模式(默认)下钩子 no-op
  - plan 模式激活时按 blocked_tools 过滤
  - reminder 仅在第一轮注入(后续轮次不重复)
  - reminder 放 user message 末尾(前缀 cache 友好)
  - 缺省 blocked_tools 时记 warning 但不报错
  - 缺省 reminder 模板时不注入

__author__: AstrBot Agent Harness 开发专家
__created__: 2026-06-14
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock


# main.py 用相对导入 (from .tools import ...),需把项目父目录加到 sys.path
# 然后以包形式导入 main。这样 `from .tools import` 才能 resolve。
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ── Fixtures ───────────────────────────────────────────────


def _make_plugin(config_overrides: dict | None = None) -> SPCodeToolkit:
    """构造一个最小可用的 SPCodeToolkit 实例用于测试。

    不走 __init__,直接 __new__ 后手动注入必要字段。
    """
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    base_config = {
        "codegraph_enabled": False,
        "codegraph_install_dir": "",
        "codegraph_project": "",
        "allowed_ids": "",
        "agentsmd_enabled": False,
    }
    if config_overrides:
        base_config.update(config_overrides)
    plugin._config = base_config
    plugin._plan_mode = {}
    plugin._plan_reminded = {}
    return plugin


def _make_mock_tool(name: str) -> MagicMock:
    """构造一个 mock 的 FunctionTool(只关心 .name 字段)。"""
    t = MagicMock()
    t.name = name
    t.description = f"mock tool {name}"
    t.parameters = {"type": "object", "properties": {}}
    t.handler = MagicMock()
    return t


def _make_mock_toolset(tool_names: list[str]) -> MagicMock:
    """构造一个 mock 的 ToolSet,包含指定名字的工具。"""
    tools = [_make_mock_tool(n) for n in tool_names]
    ts = MagicMock()
    ts.tools = tools
    return ts


class _ResultStub:
    """plain_result 的测试替身——只保留文本,str() 直接给原文。"""

    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:  # pragma: no cover
        return self.text

    def __repr__(self) -> str:  # pragma: no cover
        return self.text


class _EventMock:
    """AstrMessageEvent 的轻量替身,用于命令 handler 测试。

    让 plain_result(text) 捕获文本,这样 `str(msg)` 在测试断言中能拿到原文。
    """

    def __init__(self, umo: str) -> None:
        self.unified_msg_origin = umo
        self._results: list[str] = []

    def plain_result(self, text: str) -> _ResultStub:
        self._results.append(text)
        return _ResultStub(text)

    def message(self, *args, **kwargs):  # pragma: no cover
        return MagicMock()


def _make_mock_event(umo: str = "test:umo:1") -> _EventMock:
    return _EventMock(umo)


def _make_mock_request(
    tool_names: list[str] | None = None,
    contexts: list[dict] | None = None,
    system_prompt: str = "",
) -> MagicMock:
    """构造一个 mock 的 ProviderRequest。"""
    req = MagicMock()
    req.system_prompt = system_prompt
    req.func_tool = _make_mock_toolset(tool_names or [])
    req.contexts = contexts if contexts is not None else []
    return req


async def _collect_async_gen(agen) -> list:
    """异步生成器 → list。

    WHY: 命令 handler 是 `async def` + `yield`,返回的是 AsyncGenerator,
    不能直接 `asyncio.run(agen)`(asyncio.run 只接 Coroutine)。
    """
    items: list = []
    async for item in agen:
        items.append(item)
    return items


def _run(agen) -> list:
    """便利函数:同步跑一个 async generator 并返回 list。"""
    return asyncio.run(_collect_async_gen(agen))


# ── 1. /plan 命令行为(简化 API) ───────────────────────────


def test_plan_activates_mode():
    """plan 激活模式 + 重置 reminder 标记。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    event = _make_mock_event()
    msgs = _run(plugin.plan(event))
    text = "".join(str(m) for m in msgs)
    assert "已激活" in text
    assert "astrbot_file_remove" in text
    assert "build" in text.lower() or "/build" in text  # 提示退出方式
    assert plugin._plan_mode[event.unified_msg_origin] is True
    assert plugin._plan_reminded.get(event.unified_msg_origin) is None


def test_plan_warns_when_blocked_empty():
    """plan 激活但 blocked_tools 为空时给警告。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": []})
    event = _make_mock_event()
    msgs = _run(plugin.plan(event))
    text = "".join(str(m) for m in msgs)
    assert "plan 模式已激活" in text
    assert "为空" in text or "⚠️" in text
    # 即便 blocked 为空,模式仍激活(用户显式开了)
    assert plugin._plan_mode[event.unified_msg_origin] is True


def test_plan_reactivation_resets_reminded():
    """plan 模式已激活时再次输入,重置 reminded 标记。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    plugin._plan_reminded[event.unified_msg_origin] = True
    msgs = _run(plugin.plan(event))
    text = "".join(str(m) for m in msgs)
    assert "仍激活" in text
    assert "重置" in text
    # reminded 被重置(下次 LLM 调用时重新注入)
    assert event.unified_msg_origin not in plugin._plan_reminded


# ── 2. /build 命令行为(简化 API) ──────────────────────────


def test_build_clears_plan_when_active():
    """build 在 plan 激活时清除状态(等价于"退出 plan 模式")。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    plugin._plan_reminded[event.unified_msg_origin] = True
    msgs = _run(plugin.build(event))
    text = "".join(str(m) for m in msgs)
    assert "已关闭" in text
    assert event.unified_msg_origin not in plugin._plan_mode
    assert event.unified_msg_origin not in plugin._plan_reminded


def test_build_idempotent_when_not_in_plan():
    """build 在 build 模式(默认)下给提示但不报错。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    event = _make_mock_event()
    msgs = _run(plugin.build(event))
    text = "".join(str(m) for m in msgs)
    assert "已在 build 模式" in text


# ── 3. 状态隔离 ────────────────────────────────────────────


def test_plan_state_isolated_per_umo():
    """不同 umo 的 plan 状态互相隔离。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    plugin._plan_mode["umo:A"] = True
    plugin._plan_mode["umo:B"] = False
    assert plugin._plan_mode["umo:A"] is True
    assert plugin._plan_mode["umo:B"] is False
    # 关闭 A 不影响 B
    event_a = _make_mock_event("umo:A")
    _run(plugin.build(event_a))
    assert "umo:A" not in plugin._plan_mode
    assert plugin._plan_mode["umo:B"] is False


# ── 4. _filter_func_tool 辅助方法 ──────────────────────────


def test_filter_func_tool_no_func_tool():
    """func_tool 为 None 时直接返回 0,不做任何事。"""
    plugin = _make_plugin()
    req = MagicMock()
    req.func_tool = None
    n = plugin._filter_func_tool(req, {"foo"})
    assert n == 0


def test_filter_func_tool_empty_blocked():
    """blocked 为空集时不做任何事。"""
    plugin = _make_plugin()
    req = _make_mock_request(["a", "b", "c"])
    original_tools = list(req.func_tool.tools)
    n = plugin._filter_func_tool(req, set())
    assert n == 0
    assert req.func_tool.tools == original_tools


def test_filter_func_tool_no_match():
    """blocked 集合里的名字都不在 tools 中,无变化。"""
    plugin = _make_plugin()
    req = _make_mock_request(["a", "b", "c"])
    original_count = len(req.func_tool.tools)
    n = plugin._filter_func_tool(req, {"not_exist"})
    assert n == 0
    assert len(req.func_tool.tools) == original_count


def test_filter_func_tool_removes_blocked():
    """blocked 集合里的名字被过滤掉。"""
    plugin = _make_plugin()
    req = _make_mock_request(["a", "b", "c", "d"])
    n = plugin._filter_func_tool(req, {"b", "d"})
    assert n == 2
    remaining_names = [t.name for t in req.func_tool.tools]
    assert "b" not in remaining_names
    assert "d" not in remaining_names
    assert "a" in remaining_names
    assert "c" in remaining_names


def test_filter_func_tool_creates_new_toolset():
    """过滤后 req.func_tool 是新的 ToolSet 实例(非 in-place 修改)。

    WHY: 防止共享引用污染(多 session 共享 func_tool 时)。
    """
    plugin = _make_plugin()
    req = _make_mock_request(["a", "b", "c"])
    original_toolset = req.func_tool
    plugin._filter_func_tool(req, {"b"})
    assert req.func_tool is not original_toolset
    # 原始 ToolSet 的 tools 列表应未被修改
    assert len(original_toolset.tools) == 3


def test_filter_func_tool_all_filtered_yields_empty_toolset():
    """所有工具都被过滤,得到一个空 ToolSet。"""
    plugin = _make_plugin()
    req = _make_mock_request(["a", "b"])
    n = plugin._filter_func_tool(req, {"a", "b"})
    assert n == 2
    assert req.func_tool.tools == []


# ── 5. _plan_filter_tools 钩子 ─────────────────────────────


def test_plan_hook_noop_when_build_mode():
    """build 模式(默认)下钩子完全 no-op,不动 func_tool 和 contexts。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    event = _make_mock_event()
    req = _make_mock_request(
        ["astrbot_file_remove", "read"],
        contexts=[{"role": "user", "content": "hello"}],
    )
    original_toolset = req.func_tool
    original_contexts = list(req.contexts)
    asyncio.run(plugin._plan_filter_tools(event, req))
    # 工具列表完全没动
    assert req.func_tool is original_toolset
    assert [t.name for t in req.func_tool.tools] == ["astrbot_file_remove", "read"]
    # contexts 没动
    assert req.contexts == original_contexts


def test_plan_hook_filters_tools_when_active():
    """plan 模式激活时按配置过滤写工具。"""
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove", "todo_modify"]}
    )
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = _make_mock_request(
        ["astrbot_file_remove", "read", "todo_modify", "grep"],
        contexts=[{"role": "user", "content": "analyze"}],
    )
    asyncio.run(plugin._plan_filter_tools(event, req))
    remaining = [t.name for t in req.func_tool.tools]
    assert "astrbot_file_remove" not in remaining
    assert "todo_modify" not in remaining
    assert "read" in remaining
    assert "grep" in remaining


def test_plan_hook_injects_reminder_on_first_call():
    """plan 模式第一轮 LLM 调用时,reminder 注入到最后一条 user message。"""
    plugin = _make_plugin(
        {
            "plan_mode_blocked_tools": ["astrbot_file_remove"],
            "plan_mode_reminder": "Plan mode active. Blocked: {blocked}",
        }
    )
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = _make_mock_request(
        ["astrbot_file_remove", "read"],
        contexts=[
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "analyze"},
        ],
    )
    asyncio.run(plugin._plan_filter_tools(event, req))
    # 最后一条 user message 应被追加 reminder
    last_user = req.contexts[-1]
    assert "analyze" in last_user["content"]
    assert "Plan mode active" in last_user["content"]
    assert "astrbot_file_remove" in last_user["content"]  # {blocked} 被替换
    # reminder 已注入标记
    assert plugin._plan_reminded[event.unified_msg_origin] is True


def test_plan_hook_skips_reminder_on_subsequent_calls():
    """plan 模式第二轮之后,reminder 不再注入。"""
    plugin = _make_plugin(
        {
            "plan_mode_blocked_tools": ["astrbot_file_remove"],
            "plan_mode_reminder": "Plan mode active.",
        }
    )
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    plugin._plan_reminded[event.unified_msg_origin] = True  # 标记为已注入

    req = _make_mock_request(
        ["read"],
        contexts=[{"role": "user", "content": "second message"}],
    )
    asyncio.run(plugin._plan_filter_tools(event, req))
    # 没注入 reminder
    assert "Plan mode active" not in req.contexts[-1]["content"]
    assert req.contexts[-1]["content"] == "second message"


def test_plan_hook_no_user_message_no_reminder():
    """contexts 里没有 user 消息时,reminder 静默跳过。"""
    plugin = _make_plugin(
        {
            "plan_mode_blocked_tools": ["astrbot_file_remove"],
            "plan_mode_reminder": "Plan mode active.",
        }
    )
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = _make_mock_request(
        ["read"],
        contexts=[{"role": "assistant", "content": "ok"}],  # 没 user
    )
    # 不应抛异常
    asyncio.run(plugin._plan_filter_tools(event, req))
    # reminder 没注入,但标记为已注入(避免后续轮再尝试)
    assert plugin._plan_reminded[event.unified_msg_origin] is True


def test_plan_hook_empty_reminder_skips_but_marks():
    """reminder 配置为空时,不注入但标记为已注入。"""
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"], "plan_mode_reminder": ""}
    )
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = _make_mock_request(
        ["read"],
        contexts=[{"role": "user", "content": "hi"}],
    )
    asyncio.run(plugin._plan_filter_tools(event, req))
    assert "system-reminder" not in req.contexts[-1]["content"]
    assert plugin._plan_reminded[event.unified_msg_origin] is True


def test_plan_hook_empty_blocked_warns_but_does_not_filter():
    """blocked_tools 为空时,钩子不报错,只记 warning。"""
    plugin = _make_plugin({"plan_mode_blocked_tools": []})
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = _make_mock_request(["a", "b"])
    original_toolset = req.func_tool
    # 不应抛异常
    asyncio.run(plugin._plan_filter_tools(event, req))
    # 工具列表未动
    assert req.func_tool is original_toolset
    assert len(req.func_tool.tools) == 2


def test_plan_hook_reminder_wraps_in_system_reminder_tag():
    """reminder 没带 <system-reminder> 标签时,自动包裹。"""
    plugin = _make_plugin(
        {
            "plan_mode_blocked_tools": ["astrbot_file_remove"],
            "plan_mode_reminder": "Plan mode active. Blocked: {blocked}",
        }
    )
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = _make_mock_request(
        ["read"],
        contexts=[{"role": "user", "content": "hi"}],
    )
    asyncio.run(plugin._plan_filter_tools(event, req))
    content = req.contexts[-1]["content"]
    assert "<system-reminder>" in content
    assert "</system-reminder>" in content


def test_plan_hook_skips_when_no_func_tool():
    """req.func_tool 为 None 时钩子安全返回(不抛异常,不污染 reminded 状态)。

    WHY: func_tool 为 None 时 plan 模式是 no-op,reminder 是关于"被过滤的工具"
    的提醒,没有工具被过滤时不需要提醒,也不应标记为已注入——
    下次 func_tool 变为非空时,reminder 仍可被注入(此时才有意义)。
    """
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = MagicMock()
    req.func_tool = None
    req.contexts = []
    # 不应抛异常
    asyncio.run(plugin._plan_filter_tools(event, req))
    # func_tool 为 None → 钩子提前返回,reminded 标记不变
    assert event.unified_msg_origin not in plugin._plan_reminded


def test_plan_hook_reenabled_resets_reminded():
    """plan 激活 → 关闭 → 再次激活:reminded 重置,下次 LLM 调用再次注入。"""
    plugin = _make_plugin(
        {
            "plan_mode_blocked_tools": ["astrbot_file_remove"],
            "plan_mode_reminder": "Plan mode active.",
        }
    )
    event = _make_mock_event()
    umo = event.unified_msg_origin
    # 第一次激活 → 注入 reminder
    plugin._plan_mode[umo] = True
    plugin._plan_reminded[umo] = True
    # 关闭
    _run(plugin.build(event))
    assert umo not in plugin._plan_reminded
    # 再次激活 → reminded 应被重置
    _run(plugin.plan(event))
    assert umo not in plugin._plan_reminded
    # 下一次 LLM 调用应再次注入
    req = _make_mock_request(
        ["read"],
        contexts=[{"role": "user", "content": "hi"}],
    )
    asyncio.run(plugin._plan_filter_tools(event, req))
    assert "Plan mode active" in req.contexts[-1]["content"]


def test_schema_default_strict_blocked_tools():
    """回归测试:_conf_schema.json 中 plan_mode_blocked_tools 的 default 必须为 strict 模式。

    默认列表决定用户开箱体验。如果误改/回滚为 [],会破坏 v2.8 设计意图
    (plan 模式开箱即严格)。
    """
    import json
    from pathlib import Path

    schema_path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    actual = schema["plan_mode"]["items"]["plan_mode_blocked_tools"]["default"]
    expected = [
        "astrbot_file_remove",
        "todo_create",
        "todo_modify",
        "todo_clear",
        "astrbot_inta_shell_start",
        "astrbot_inta_shell_send",
        "astrbot_inta_shell_stop",
        "astrbot_file_write_tool",
        "astrbot_file_edit_tool",
    ]
    assert actual == expected, (
        f"plan_mode_blocked_tools default 不匹配 strict 模式:\n"
        f"  实际: {actual}\n"
        f"  期望: {expected}\n"
        f"  注意:astrbot_execute_shell 故意不列入(用户决策);"
        f"send_message_to_user 不列入(避免误报)。"
    )

    # 额外断言:默认不包含用户**显式**决定不 ban 的工具
    assert "astrbot_execute_shell" not in actual
    assert "send_message_to_user" not in actual


def test_config_keys_must_be_flat_not_nested():
    """回归测试:AstrBot 扁平化 _conf_schema.json 的 object 包装,config 键必须顶层。

    防止未来有人把访问改回嵌套形式 ({"plan_mode": {"blocked_tools": [...]}}),
    那种写法会让 `self._config.get("plan_mode_blocked_tools")` 永远拿不到值。
    """
    # 正确形式:顶层键 plan_mode_blocked_tools
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    assert plugin._config.get("plan_mode_blocked_tools") == ["astrbot_file_remove"]

    # 错误形式(嵌套)不会生效 —— 此测试文档化这一约束
    nested_cfg = {"plan_mode": {"blocked_tools": ["astrbot_file_remove"]}}
    plugin_bad = _make_plugin(nested_cfg)
    # 嵌套 form 中,顶层键 plan_mode_blocked_tools 不存在
    assert plugin_bad._config.get("plan_mode_blocked_tools") is None
    # 提醒:实际代码用的是扁平访问,所以嵌套 form 配置**不生效**


def test_plan_hook_does_not_modify_system_prompt():
    """关键 cache 优化:reminder 不放 system_prompt,只放 user message。"""
    plugin = _make_plugin(
        {
            "plan_mode_blocked_tools": ["astrbot_file_remove"],
            "plan_mode_reminder": "Plan mode active.",
        }
    )
    event = _make_mock_event()
    plugin._plan_mode[event.unified_msg_origin] = True
    req = _make_mock_request(
        ["read"],
        contexts=[{"role": "user", "content": "hi"}],
        system_prompt="You are a helpful assistant.",  # 原始 system_prompt
    )
    asyncio.run(plugin._plan_filter_tools(event, req))
    # system_prompt 完全没动(prefix cache 友好)
    assert req.system_prompt == "You are a helpful assistant."


# ── 6. /spcode/plan-mode web API 端点(dashboard 查询用) ───────────
#
# 覆盖:
# - _plan_mode_active 辅助方法
# - _plan_mode_active_count 辅助方法
# - handle_get_plan_mode:无 umo / 未知 umo / plan 激活 / build 默认 /
#   跨 umo 计数 / web.request query 注入等
#
# Author: AstrBot Agent Harness 开发专家
# Created: 2026-06-19


def _patch_web_request_query(monkeypatch, values: dict) -> None:
    """Mock ``astrbot.api.web.request.query.get`` to return provided values.

    Mirrors the helper used in :mod:`test_git_diff_worktree` so the
    plan-mode tests share the same contract: the handler reads umo
    via ``web.request.query.get("umo")``, so we install a ``query``
    object whose ``.get(key, default)`` returns ``values[key]`` or
    the default.
    """
    from astrbot.api import web

    mock_query = MagicMock()
    mock_query.get = lambda key, default=None: values.get(key, default)
    monkeypatch.setattr(web, "request", MagicMock(query=mock_query))


# --- 6.1 helper methods -------------------------------------------------


def test_plan_mode_active_helper_unknown_umo_returns_false():
    """未记录的 umo 应被视为 build 模式(_plan_mode.get 默认 False)。"""
    plugin = _make_plugin()
    assert plugin._plan_mode_active("never:seen:umo") is False


def test_plan_mode_active_helper_explicit_false_returns_false():
    """key 存在但显式为 False 同样视为 build(与 on_llm_request 钩子一致)。"""
    plugin = _make_plugin()
    plugin._plan_mode["umo:explicit-build"] = False
    assert plugin._plan_mode_active("umo:explicit-build") is False


def test_plan_mode_active_helper_true_returns_true():
    plugin = _make_plugin()
    plugin._plan_mode["umo:plan"] = True
    assert plugin._plan_mode_active("umo:plan") is True


def test_plan_mode_active_helper_none_or_empty_returns_false():
    """umo 为 None 或空字符串时直接返回 False(防止误把"未传参"当 plan 激活)。"""
    plugin = _make_plugin()
    plugin._plan_mode["umo:plan"] = True
    assert plugin._plan_mode_active(None) is False
    assert plugin._plan_mode_active("") is False


def test_plan_mode_active_count_only_counts_true():
    """``False`` 值不应计入激活数(避免在 build→plan 状态切换时把历史污染留下)。"""
    plugin = _make_plugin()
    plugin._plan_mode["umo:a"] = True
    plugin._plan_mode["umo:b"] = False  # 显式 build
    plugin._plan_mode["umo:c"] = True
    assert plugin._plan_mode_active_count() == 2


def test_plan_mode_active_count_empty_returns_zero():
    plugin = _make_plugin()
    assert plugin._plan_mode_active_count() == 0


# --- 6.2 handler: no umo / unknown umo ---------------------------------


def test_handle_get_plan_mode_no_umo_returns_build(monkeypatch):
    """未传 umo 时,handler 返回 active=False(默认 build)+ umo=None。

    与 /spcode/project-status 不同,我们**不**fallback 到"最近的
    plan 模式 session"——plan/build 是严格 per-session,继承其他
    session 的模式会造成用户混淆。
    """
    _patch_web_request_query(monkeypatch, {})
    plugin = _make_plugin()
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["status"] == "ok"
    assert payload["data"]["active"] is False
    assert payload["data"]["umo"] is None
    assert payload["data"]["all_active_count"] == 0


def test_handle_get_plan_mode_unknown_umo_returns_build(monkeypatch):
    """查询未在 _plan_mode 中登记的 umo → active=False。"""
    _patch_web_request_query(monkeypatch, {"umo": "ghost:umo:42"})
    plugin = _make_plugin()
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["data"]["active"] is False
    assert payload["data"]["umo"] == "ghost:umo:42"
    assert payload["data"]["all_active_count"] == 0


# --- 6.3 handler: known umo in plan / build ----------------------------


def test_handle_get_plan_mode_known_umo_active_returns_plan(monkeypatch):
    """umo 在 _plan_mode[True] 时,handler 返回 active=True。"""
    _patch_web_request_query(monkeypatch, {"umo": "webchat:FriendMessage:u!s1"})
    plugin = _make_plugin()
    plugin._plan_mode["webchat:FriendMessage:u!s1"] = True
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["data"]["active"] is True
    assert payload["data"]["umo"] == "webchat:FriendMessage:u!s1"


def test_handle_get_plan_mode_known_umo_build_default_returns_build(monkeypatch):
    """umo 未登记时按 build 处理(与 _plan_mode_active 行为一致)。"""
    _patch_web_request_query(monkeypatch, {"umo": "webchat:FriendMessage:u!s2"})
    plugin = _make_plugin()
    # 该 umo 完全没碰过 → default
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["data"]["active"] is False


def test_handle_get_plan_mode_explicit_false_returns_build(monkeypatch):
    """umo 存在但显式设为 False 也算 build(与钩子行为一致)。"""
    _patch_web_request_query(monkeypatch, {"umo": "umo:explicit-build"})
    plugin = _make_plugin()
    plugin._plan_mode["umo:explicit-build"] = False
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["data"]["active"] is False


# --- 6.4 handler: cross-umo counts -------------------------------------


def test_handle_get_plan_mode_counts_active_across_umos(monkeypatch):
    """all_active_count 应反映全实例的 plan 激活数,与查询的 umo 无关。"""
    _patch_web_request_query(monkeypatch, {"umo": "umo:queried"})
    plugin = _make_plugin()
    plugin._plan_mode["umo:1"] = True
    plugin._plan_mode["umo:2"] = True
    plugin._plan_mode["umo:3"] = False
    plugin._plan_mode["umo:queried"] = True
    payload = asyncio.run(plugin.handle_get_plan_mode())
    # 3 个 umo 处于 plan 模式(1, 2, queried);3 是 build
    assert payload["data"]["all_active_count"] == 3
    assert payload["data"]["active"] is True  # queried 自己也算


# --- 6.5 handler: web.request unavailable ------------------------------


def test_handle_get_plan_mode_web_request_unavailable_returns_build():
    """当 ``astrbot.api.web`` 不可用(web.request 抛异常)时,handler
    应静默回退到 ``umo=None, active=False``,不向 dashboard 抛 500。

    行为对齐 :meth:`handle_get_project_status`:测试环境不挂 web 上下文
    是常见情况,handler 必须自包含。
    """
    # 不 patch web.request,直接用主 mock 后的 web 触发 import 异常
    plugin = _make_plugin()
    # 默认 _make_plugin 没碰 _plan_mode,直接调
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["status"] == "ok"
    # web.request 不存在 → except 分支 → umo 保持 None
    assert payload["data"]["umo"] is None
    assert payload["data"]["active"] is False


# --- 6.6 toggle path integration: /plan → /build round-trip ------------


def test_plan_then_build_reflected_by_handler(monkeypatch):
    """端到端:发送 /plan 后 handler 返回 active=True;/build 后回到 False。"""
    _patch_web_request_query(monkeypatch, {"umo": "test:umo:integration"})
    plugin = _make_plugin({"plan_mode_blocked_tools": ["astrbot_file_remove"]})
    event = _make_mock_event("test:umo:integration")

    # 初始:build
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["data"]["active"] is False

    # 激活 plan
    _run(plugin.plan(event))
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["data"]["active"] is True

    # 退出 plan
    _run(plugin.build(event))
    payload = asyncio.run(plugin.handle_get_plan_mode())
    assert payload["data"]["active"] is False
