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
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
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
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
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
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
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
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
    event = _make_mock_event()
    msgs = _run(plugin.build(event))
    text = "".join(str(m) for m in msgs)
    assert "已在 build 模式" in text


# ── 3. 状态隔离 ────────────────────────────────────────────


def test_plan_state_isolated_per_umo():
    """不同 umo 的 plan 状态互相隔离。"""
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
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
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
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
            "plan_mode_reminder": "Plan mode active. Blocked: {blocked}"
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
            "plan_mode_reminder": "Plan mode active."
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
            "plan_mode_reminder": "Plan mode active."
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
        {
            "plan_mode_blocked_tools": ["astrbot_file_remove"],
            "plan_mode_reminder": ""
        }
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
            "plan_mode_reminder": "Plan mode active. Blocked: {blocked}"
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
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
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
            "plan_mode_reminder": "Plan mode active."
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

    actual = (
        schema["plan_mode"]["items"]["plan_mode_blocked_tools"]["default"]
    )
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
    plugin = _make_plugin(
        {"plan_mode_blocked_tools": ["astrbot_file_remove"]}
    )
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
            "plan_mode_reminder": "Plan mode active."
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
