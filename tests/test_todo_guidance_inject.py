"""todo_* 工具启用时 LLM 提示词注入测试。

设计依据:docs/superpowers/specs/2026-06-30-todo-llm-inject-design.md
仿照 tests/test_file_remove_injection.py 的 helper 模式(合成包 + __new__ 跳过 __init__)。
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# worktree 兼容性:main.py 用 `from .tools import` 相对导入,需要包上下文。
# 在 sys.modules 注册一个合成包,把 main.py 作为其子模块加载,以满足
# "from .tools import" 的解析需求。生产环境(被 AstrBot 以
# `astrbot_plugin_spcode_toolkit.main` 加载)走的是同一机制的真实版本。
_PKG_NAME = "_spcode_runtime_pkg"
if _PKG_NAME not in sys.modules:
    _pkg = types.ModuleType(_PKG_NAME)
    _pkg.__path__ = [str(ROOT)]
    sys.modules[_PKG_NAME] = _pkg
_spec = importlib.util.spec_from_file_location(
    f"{_PKG_NAME}.main", str(ROOT / "main.py")
)
_main = importlib.util.module_from_spec(_spec)
sys.modules[f"{_PKG_NAME}.main"] = _main
_spec.loader.exec_module(_main)
sys.modules["main"] = _main

# PR-1 (2026-06-23): 常量从 main.py 提取到 tools._guidance_text
# 2026-06-30: 新增 TODO_GUIDANCE_* 供 todo 工具注入使用
TODO_GUIDANCE_MARKER = _main.TODO_GUIDANCE_MARKER  # noqa: E402
TODO_GUIDANCE = _main.TODO_GUIDANCE  # noqa: E402
SPCodeToolkit = _main.SPCodeToolkit

# 6 个 todo_* 工具,任一启用即触发注入。与 main.py _todo_inject_guidance 一致。
_TODO_TOOL_NAMES = (
    "todo_create",
    "todo_query",
    "todo_add",
    "todo_update",
    "todo_delete",
    "todo_clear",
)


def _make_plugin(enabled_tools: set[str] | None = None) -> SPCodeToolkit:
    """最小 helper:实例化 SPCodeToolkit,设置 _tool_names 模拟 enabled_tools 过滤。

    用 __new__ 跳过 __init__,避免触发 AstrBot 启动副作用(注册 filter / 加载 config)。
    默认空集 = 无 todo 工具启用。
    """
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin._tool_names = enabled_tools if enabled_tools is not None else set()
    return plugin


def _make_event(umo: str = "test:umo") -> MagicMock:
    ev = MagicMock()
    ev.unified_msg_origin = umo
    return ev


# ── 1. todo_create 启用时注入关键句与 marker ─────────


def test_inject_when_todo_create_enabled():
    plugin = _make_plugin(enabled_tools={"todo_create"})
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._todo_inject_guidance(_make_event(), req))

    assert req.system_prompt != "base", "system_prompt 末尾应被追加"
    assert "base" in req.system_prompt, "原文应保留"
    assert TODO_GUIDANCE_MARKER in req.system_prompt, "marker 应在"
    # 关键句断言(Anthropic 强约束措辞至少出现一条)
    assert "VERY frequently" in req.system_prompt
    # v2.12 起 todo_add/todo_update/todo_delete 支持批量(item_ids 为列表),
    # 旧版 "Do NOT batch" 文案已移除;改为断言当前的防遗忘强约束句。
    assert "unacceptable" in req.system_prompt
    # 工具名提示(让 LLM 知道调哪个)
    assert "todo_create" in req.system_prompt
    assert "todo_update" in req.system_prompt


# ── 2. 任一 todo_* 工具启用都触发注入 ─────────────


def test_inject_when_any_todo_tool_enabled():
    """gate 是'任一 todo_* 工具启用'——逐个验证 6 个工具都能触发。"""
    for tool_name in _TODO_TOOL_NAMES:
        plugin = _make_plugin(enabled_tools={tool_name})
        req = MagicMock()
        req.system_prompt = ""

        asyncio.run(plugin._todo_inject_guidance(_make_event(), req))

        assert TODO_GUIDANCE_MARKER in req.system_prompt, (
            f"{tool_name} 启用时,marker 应被注入;实际: {req.system_prompt!r}"
        )


# ── 3. 全无 todo 工具时绝不注入 ─────────────────


def test_no_inject_when_no_todo_tool():
    """只启用非 todo 工具时,system_prompt 不应被污染。"""
    plugin = _make_plugin(enabled_tools={"code_check", "es_search", "file_remove"})
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._todo_inject_guidance(_make_event(), req))

    assert req.system_prompt == "base", (
        f"未启用 todo 工具时,system_prompt 应保持原样;实际: {req.system_prompt!r}"
    )
    assert TODO_GUIDANCE_MARKER not in req.system_prompt


def test_no_inject_when_tool_set_empty():
    """全 0 工具启用时(enabled_tools=[]),绝不注入。"""
    plugin = _make_plugin(enabled_tools=set())
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._todo_inject_guidance(_make_event(), req))

    assert req.system_prompt == "base"
    assert TODO_GUIDANCE_MARKER not in req.system_prompt


# ── 4. 幂等性:二次调用 no-op ──────────────────────


def test_inject_idempotent():
    """已注入 marker 后,二次调用应被 inject_guidance 防重复机制阻止。"""
    plugin = _make_plugin(enabled_tools={"todo_create"})
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._todo_inject_guidance(_make_event(), req))
    first_len = len(req.system_prompt)
    asyncio.run(plugin._todo_inject_guidance(_make_event(), req))
    second_len = len(req.system_prompt)

    assert first_len == second_len, (
        f"重复注入应被 marker 阻止,长度变化: {first_len} → {second_len}"
    )
    # marker 仍只出现一次
    assert req.system_prompt.count(TODO_GUIDANCE_MARKER) == 1


# ── 5. system_prompt = None 时正确初始化 ──────────


def test_inject_handles_none_system_prompt():
    """system_prompt 为 None 时(inject_guidance 内部用 `or ""` 兜底),正常注入。"""
    plugin = _make_plugin(enabled_tools={"todo_create"})
    req = MagicMock()
    req.system_prompt = None

    asyncio.run(plugin._todo_inject_guidance(_make_event(), req))

    assert req.system_prompt is not None
    assert TODO_GUIDANCE_MARKER in req.system_prompt


def test_inject_handles_empty_string_system_prompt():
    """system_prompt 为空字符串时,正常注入(不留前缀空行——inject_guidance 会 strip)。"""
    plugin = _make_plugin(enabled_tools={"todo_update"})
    req = MagicMock()
    req.system_prompt = ""

    asyncio.run(plugin._todo_inject_guidance(_make_event(), req))

    assert req.system_prompt
    assert TODO_GUIDANCE_MARKER in req.system_prompt
    # strip_leading_newline 行为:不以前缀空行开始
    assert not req.system_prompt.startswith("\n")
