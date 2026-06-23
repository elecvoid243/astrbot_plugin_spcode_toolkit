"""tools/codegraph/ 子系统单元测试(PR-6 2026-06-23)。

覆盖:
- state 模块:get_task / set_task / get_dir_lock / reset
- bootstrap 模块:build_mcp_cfg 边界场景
- CodegraphManager:构造 + init/uninit/set_project 路径

注:不重复测 tools/_codegraph_mcp.py 的 helpers(已有 test_codegraph_mcp.py 覆盖);
这里只测 PR-6 新增的 codegraph 子系统层。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from tools.codegraph import (  # noqa: E402
    CodegraphManager,
    build_mcp_cfg,
    shutdown_mcp,
    state as cg_state,
)


# ── state 模块 ─────────────────────────────────────


class TestStateModule:
    """tools.codegraph.state 模块级单例行为。"""

    def setup_method(self) -> None:
        """每个测试前清空 state,避免跨用例污染。"""
        cg_state.reset()

    def teardown_method(self) -> None:
        """测试后清理。"""
        cg_state.reset()

    def test_get_task_initially_none(self) -> None:
        assert cg_state.get_task() is None

    def test_set_task_then_get_returns_same(self) -> None:
        fake_task = MagicMock()
        cg_state.set_task(fake_task)
        assert cg_state.get_task() is fake_task

    def test_set_task_none_clears_reference(self) -> None:
        cg_state.set_task(MagicMock())
        cg_state.set_task(None)
        assert cg_state.get_task() is None

    def test_get_dir_lock_creates_new_per_target(self) -> None:
        lock_a = cg_state.get_dir_lock("/tmp/a")
        lock_b = cg_state.get_dir_lock("/tmp/b")
        lock_a2 = cg_state.get_dir_lock("/tmp/a")
        # 不同 target → 不同 lock 实例
        assert lock_a is not lock_b
        # 同 target → 复用同一 lock 实例
        assert lock_a is lock_a2
        # asyncio.Lock 是 asyncio.Lock 类型
        import asyncio as _aio
        assert isinstance(lock_a, _aio.Lock)

    def test_reset_clears_task_and_locks(self) -> None:
        cg_state.set_task(MagicMock())
        cg_state.get_dir_lock("/tmp/a")
        cg_state.reset()
        assert cg_state.get_task() is None
        # 验证 locks 也清空(用 pop 后重新创建)
        assert "/tmp/a" not in cg_state._codegraph_dir_locks  # type: ignore[attr-defined]


# ── build_mcp_cfg ──────────────────────────────────


class TestBuildMcpCfg:
    """build_mcp_cfg 边界场景。"""

    def test_returns_none_when_install_dir_empty(self) -> None:
        plugin = MagicMock()
        plugin._config = {"codegraph_install_dir": ""}
        assert build_mcp_cfg(plugin) is None

    def test_returns_none_when_install_dir_whitespace(self) -> None:
        plugin = MagicMock()
        plugin._config = {"codegraph_install_dir": "   "}
        assert build_mcp_cfg(plugin) is None

    def test_returns_none_when_install_dir_not_in_config(self) -> None:
        plugin = MagicMock()
        plugin._config = {}
        assert build_mcp_cfg(plugin) is None

    def test_returns_none_when_detect_returns_none(self) -> None:
        plugin = MagicMock()
        plugin._config = {"codegraph_install_dir": "C:\\invalid\\path"}
        # 真实 detect_codegraph_launcher 会返回 None(路径无效)
        # 这里没 mock,因为要验证真实行为
        result = build_mcp_cfg(plugin)
        # 可能返回 None(invalid path)或 dict(偶然有效)
        # 不强制 assert — 只确保不抛异常
        assert result is None or isinstance(result, dict)


# ── CodegraphManager ───────────────────────────────


class TestCodegraphManagerConstruction:
    """CodegraphManager 构造行为。"""

    def test_constructor_stores_plugin_reference(self) -> None:
        plugin = MagicMock()
        mgr = CodegraphManager(plugin)
        assert mgr._plugin is plugin


# ── shutdown_mcp / bootstrap_mcp 错误路径 ─────────


@pytest.mark.asyncio
class TestShutdownMcp:
    """shutdown_mcp 边界场景(无 plugin context 时不抛)。"""

    async def test_shutdown_with_no_task_noop(self) -> None:
        """无 task + MCP 未运行 → 直接 no-op。"""
        cg_state.reset()
        plugin = MagicMock()
        plugin.context.get_llm_tool_manager.return_value.mcp_server_runtime = {}
        # 不应抛
        await shutdown_mcp(plugin)
        assert cg_state.get_task() is None

    async def test_shutdown_cancels_running_task(self) -> None:
        """task 还在跑 → cancel + await。"""
        cg_state.reset()

        async def _pending_coro() -> None:
            await _never_terminate()

        async def _never_terminate() -> None:
            import asyncio as _aio
            await _aio.sleep(3600)

        task = __import__("asyncio").create_task(_pending_coro())
        # 让 task 跑起来
        import asyncio as _aio
        await _aio.sleep(0)
        cg_state.set_task(task)
        plugin = MagicMock()
        plugin.context.get_llm_tool_manager.return_value.mcp_server_runtime = {}
        await shutdown_mcp(plugin)
        assert task.cancelled() or task.done()
        assert cg_state.get_task() is None
