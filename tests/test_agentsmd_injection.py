"""/agentsmd LLM 提示词注入回归测试。

WHY 存在:
    PR-5 (2026-06-23) 把 main.py 的 4 个 _agentsmd_* 命令方法 + on_llm_request
    钩子抽到 tools/agentsmd/ 子包时,**漏掉了在 main.py 写一个 @filter.on_llm_request()
    委托方法**,导致所有 system_prompt 注入失效。

    本测试同时验证三件事,确保 BUG 不再回归:
      1. 结构性:SPCodeToolkit._agentsmd_inject_to_llm_request 方法存在
      2. 注册性:该方法被装饰器注册到 star_handlers_registry
        (event_type == OnLLMRequestEvent)
      3. 行为性:实际调用时,agentsmd_enabled=false 不调委托;
        agentsmd_enabled=true 会调 self.agentsmd.on_llm_request(event, req)

Author: elecvoid243, 2026-06-23
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# worktree 兼容性:main.py 用 `from .tools import` 相对导入,需要包上下文。
# 在 sys.modules 注册一个合成包,把 main.py 作为其子模块加载,以满足
# "from .tools import" 的解析需求。生产环境(被 AstrBot 以
# `astrbot_plugin_spcode_toolkit.main` 加载)走的是同一机制的真实版本。
# (与 tests/test_file_remove_injection.py 完全一致)
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

SPCodeToolkit = _main.SPCodeToolkit


def _make_plugin(enabled: bool) -> SPCodeToolkit:
    """最小 helper:实例化 SPCodeToolkit,设置 agentsmd_enabled 配置。

    用 __new__ 跳过 __init__,避免触发 AstrBot 启动副作用(注册 filter / 加载 config)。
    """
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin._config = {"agentsmd_enabled": enabled}
    plugin.agentsmd = MagicMock()
    plugin.agentsmd.on_llm_request = AsyncMock()
    return plugin


def _make_event(umo: str = "test:umo") -> MagicMock:
    ev = MagicMock()
    ev.unified_msg_origin = umo
    return ev


# ── 1. 结构性:方法存在且为 async coroutine ────────────


def test_agentsmd_hook_method_exists_and_is_coroutine():
    """SPCodeToolkit._agentsmd_inject_to_llm_request 必须存在且为 async 方法。

    WHY: PR-5 拆分时如果忘了写这个方法,以下两个测试也会失败(属性不存在),
    但单独写这个 case 可以让错误信息更直观。
    """
    assert hasattr(SPCodeToolkit, "_agentsmd_inject_to_llm_request"), (
        "SPCodeToolkit 缺少 _agentsmd_inject_to_llm_request 方法 — "
        "PR-5 重构漏接 on_llm_request 钩子的根本原因。"
    )
    method = getattr(SPCodeToolkit, "_agentsmd_inject_to_llm_request")
    assert asyncio.iscoroutinefunction(method), (
        "_agentsmd_inject_to_llm_request 必须是 async 函数,"
        "AstrBot 框架要求 on_llm_request 钩子为 async。"
    )


# ── 2. 注册性:装饰器把钩子注册到了 star_handlers_registry ─────


def test_agentsmd_hook_registered_to_on_llm_request_event():
    """钩子必须被 @filter.on_llm_request() 装饰器注册到 OnLLMRequestEvent。

    这是本次 BUG 的核心检查:即使 main.py 中写了一个普通 async 方法,但
    没挂装饰器,AstrBot 框架就不会调用它 — 这正是 PR-5 漏接的表现。

    验证策略:
        1. 构造 handler_full_name  = f"{method.__module__}_{method.__name__}"
        2. 从 star_handlers_registry 中查询该 full_name 对应的 metadata
        3. 断言 metadata.event_type == EventType.OnLLMRequestEvent

    NOTE: 测试 _agentsmd_inject_to_llm_request,而不是 self.agentsmd.on_llm_request
    —— 后者是 AgentsmdSubsystem 普通方法,装饰器不会注册它。
    """
    from astrbot.core.star.star_handler import (
        EventType,
        star_handlers_registry,
    )

    method = SPCodeToolkit._agentsmd_inject_to_llm_request
    expected_full_name = f"{method.__module__}_{method.__name__}"
    handler = star_handlers_registry.get_handler_by_full_name(expected_full_name)

    assert handler is not None, (
        f"未找到 full_name={expected_full_name!r} 的 handler — "
        "@filter.on_llm_request() 装饰器未对该方法生效。"
        "(PR-5 重构漏接 on_llm_request 钩子,或装饰器在 main.py import 时未执行)"
    )
    assert handler.event_type == EventType.OnLLMRequestEvent, (
        f"handler.event_type 应为 OnLLMRequestEvent,"
        f"实际为 {handler.event_type} — 钩子可能被错误地用其他装饰器装饰。"
    )


# ── 3. 行为性:agentsmd_enabled=false 时早返回 ──────────


def test_agentsmd_hook_disabled_returns_early():
    """agentsmd_enabled=false 时,钩子应早返回,不调 self.agentsmd.on_llm_request。"""
    plugin = _make_plugin(enabled=False)
    req = MagicMock()

    asyncio.run(plugin._agentsmd_inject_to_llm_request(_make_event(), req))

    plugin.agentsmd.on_llm_request.assert_not_called()


# ── 4. 行为性:agentsmd_enabled=true 时正确委托 ─────────


def test_agentsmd_hook_delegates_to_subsystem():
    """agentsmd_enabled=true 时,钩子应把 (event, req) 委托给 self.agentsmd.on_llm_request。"""
    plugin = _make_plugin(enabled=True)
    ev = _make_event("umo-xyz")
    req = MagicMock()

    asyncio.run(plugin._agentsmd_inject_to_llm_request(ev, req))

    plugin.agentsmd.on_llm_request.assert_awaited_once_with(ev, req)
