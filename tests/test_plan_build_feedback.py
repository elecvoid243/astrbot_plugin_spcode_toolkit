"""Tests for /plan /build command feedback gating (2026-07-27, elecvoid243).

plan_mode_command_feedback 配置项(False)关闭 /plan /build 命令 yield 的
模式切换提示文本;True(默认)保持原行为。配置异常警告(blocked 为空)
不受门控,始终提示。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit

_BLOCKED = ["astrbot_file_remove", "todo_create"]


def _make_plugin(*, feedback: bool | None, blocked: list | None = None):
    """创建最小化 plugin 实例(绕过 __init__,与 test_project_cmd 同模式)。

    feedback=None 表示配置缺失(老配置文件),应回落到默认 True。
    """
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    config = {
        "plan_mode_blocked_tools": list(_BLOCKED if blocked is None else blocked),
    }
    if feedback is not None:
        config["plan_mode_command_feedback"] = feedback
    plugin._config = config

    from tools.security import PlanModeController

    plugin._plan = PlanModeController(lambda: plugin._config)
    return plugin


def _make_event(umo: str = "test:umo:plan"):
    event = MagicMock()
    event.unified_msg_origin = umo
    event.plain_result = lambda x: x
    return event


async def _collect(agen) -> list[str]:
    return [item async for item in agen]


# ── feedback=True(默认):保持原行为 ─────────────────────────────


@pytest.mark.asyncio
async def test_plan_yields_feedback_when_enabled():
    plugin = _make_plugin(feedback=True)
    results = await _collect(plugin.plan(_make_event()))
    assert len(results) == 1
    assert "plan 模式已激活" in results[0]


@pytest.mark.asyncio
async def test_plan_yields_feedback_when_config_missing():
    """老配置文件没有该键 → 默认 True,仍提示。"""
    plugin = _make_plugin(feedback=None)
    results = await _collect(plugin.plan(_make_event()))
    assert len(results) == 1
    assert "plan 模式已激活" in results[0]


@pytest.mark.asyncio
async def test_build_yields_feedback_when_enabled():
    plugin = _make_plugin(feedback=True)
    results = await _collect(plugin.build(_make_event()))
    assert len(results) == 1
    assert "build 模式" in results[0]


# ── feedback=False:静默切换 ────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_silent_when_disabled():
    plugin = _make_plugin(feedback=False)
    event = _make_event()
    results = await _collect(plugin.plan(event))
    assert results == []
    # 静默但状态必须真的切换了
    assert plugin._plan.is_active(event.unified_msg_origin) is True
    # 必须终止事件,否则框架会把 "/plan" 当普通文本回落给 LLM
    # (astrbot/core/pipeline/process_stage/stage.py 的 LLM 回退路)
    event.stop_event.assert_called_once()


@pytest.mark.asyncio
async def test_plan_silent_when_disabled_reactivate():
    """已激活再 /plan(🔄 分支)同样静默。"""
    plugin = _make_plugin(feedback=False)
    event = _make_event()
    await _collect(plugin.plan(event))
    results = await _collect(plugin.plan(event))
    assert results == []
    assert event.stop_event.call_count == 2


@pytest.mark.asyncio
async def test_build_silent_when_disabled():
    plugin = _make_plugin(feedback=False)
    event = _make_event()
    await _collect(plugin.plan(event))  # 先进入 plan 模式
    results = await _collect(plugin.build(event))
    assert results == []
    assert plugin._plan.is_active(event.unified_msg_origin) is False
    # plan + build 各 stop 一次
    assert event.stop_event.call_count == 2


@pytest.mark.asyncio
async def test_build_silent_when_disabled_noop():
    """本来就在 build 模式(ℹ️ 分支)同样静默。"""
    plugin = _make_plugin(feedback=False)
    event = _make_event()
    results = await _collect(plugin.build(event))
    assert results == []
    event.stop_event.assert_called_once()


@pytest.mark.asyncio
async def test_feedback_enabled_does_not_stop_event():
    """feedback=True 走正常 yield 路径时,不应调用 stop_event。"""
    plugin = _make_plugin(feedback=True)
    event = _make_event()
    await _collect(plugin.plan(event))
    await _collect(plugin.build(event))
    event.stop_event.assert_not_called()


# ── 配置异常警告不受门控 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_empty_blocked_warning_always_shown():
    """blocked 为空的配置异常警告:即使 feedback=False 也必须提示。"""
    plugin = _make_plugin(feedback=False, blocked=[])
    results = await _collect(plugin.plan(_make_event()))
    assert len(results) == 1
    assert "plan_mode_blocked_tools 为空" in results[0]
