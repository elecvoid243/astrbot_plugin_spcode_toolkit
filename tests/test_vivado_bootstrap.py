"""tests/test_vivado_bootstrap.py - 覆盖 tools/vivado/bootstrap.py + launcher.py。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from tools.vivado import bootstrap as bootstrap_module
from tools.vivado import state as state_module


@pytest.fixture
def fresh_state():
    state_module.reset_state()
    return state_module.get_state()


@pytest.fixture
def mock_plugin():
    p = MagicMock()
    p._config = {
        "vivado_enabled": True,
        "vivado_executable": "",
        "vivado_max_sessions": 8,
    }
    return p


class TestBuildMcpCfg:
    def test_disabled_returns_none(self, mock_plugin, fresh_state):
        mock_plugin._config["vivado_enabled"] = False
        assert bootstrap_module.build_mcp_cfg(mock_plugin, state=fresh_state) is None

    def test_launcher_unavailable_returns_none(self, mock_plugin, fresh_state):
        with patch("tools.vivado.launcher.detect_vivado_launcher", return_value=None):
            assert bootstrap_module.build_mcp_cfg(mock_plugin, state=fresh_state) is None

    def test_returns_full_cfg(self, mock_plugin, fresh_state):
        fake_launcher = {
            "type": "stdio",
            "command": "C:/Python/python.exe",
            "args": ["-m", "vivado_mcp"],
        }
        with patch("tools.vivado.launcher.detect_vivado_launcher", return_value=fake_launcher), \
             patch("tools.vivado.launcher.build_env", return_value={"VIVADO_PATH": "D:/Xilinx/Vivado"}):
            cfg = bootstrap_module.build_mcp_cfg(mock_plugin, state=fresh_state)
        assert cfg == {
            "type": "stdio",
            "command": "C:/Python/python.exe",
            "args": ["-m", "vivado_mcp"],
            "env": {"VIVADO_PATH": "D:/Xilinx/Vivado"},
        }


class TestBootstrapMcp:
    @pytest.mark.asyncio
    async def test_disabled_skips(self, mock_plugin, fresh_state):
        mock_plugin._config["vivado_enabled"] = False
        await bootstrap_module.bootstrap_mcp(mock_plugin, state=fresh_state)
        assert fresh_state.mcp_running is False

    @pytest.mark.asyncio
    async def test_launcher_unavailable_skips(self, mock_plugin, fresh_state):
        with patch("tools.vivado.launcher.detect_vivado_launcher", return_value=None):
            await bootstrap_module.bootstrap_mcp(mock_plugin, state=fresh_state)
        assert fresh_state.mcp_running is False

    @pytest.mark.asyncio
    async def test_enable_mcp_server_called(self, mock_plugin, fresh_state):
        fake_cfg = {"type": "stdio", "command": "python", "args": ["-m", "vivado_mcp"], "env": {}}
        mock_mgr = MagicMock()
        mock_mgr.mcp_server_runtime = {}

        async def fake_enable(name, config, timeout):
            mock_mgr.mcp_server_runtime[name] = config

        mock_mgr.enable_mcp_server = fake_enable
        mock_plugin.context.get_llm_tool_manager = MagicMock(return_value=mock_mgr)
        with patch("tools.vivado.bootstrap.build_vivado_launcher_cfg", return_value=fake_cfg), \
             patch("tools._vivado_mcp.ensure_stdio_allowlist") as mock_ensure:
            await bootstrap_module.bootstrap_mcp(mock_plugin, state=fresh_state)
        assert fresh_state.mcp_running is True
        assert fresh_state.mcp_started_at > 0
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_enable_failure_does_not_raise(self, mock_plugin, fresh_state):
        mock_mgr = MagicMock()
        mock_mgr.mcp_server_runtime = {}

        async def fail_enable(name, config, timeout):
            raise RuntimeError("MCP daemon died")

        mock_mgr.enable_mcp_server = fail_enable
        mock_plugin.context.get_llm_tool_manager = MagicMock(return_value=mock_mgr)
        with patch("tools.vivado.bootstrap.build_vivado_launcher_cfg",
                   return_value={"type": "stdio", "command": "p", "args": [], "env": {}}), \
             patch("tools._vivado_mcp.ensure_stdio_allowlist"):
            await bootstrap_module.bootstrap_mcp(mock_plugin, state=fresh_state)  # 不应抛
        assert fresh_state.mcp_running is False


class TestShutdownMcp:
    @pytest.mark.asyncio
    async def test_cancels_bootstrap_task(self, mock_plugin, fresh_state):
        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.create_task(long_running())
        fresh_state.set_task(task)
        mock_mgr = MagicMock()
        mock_mgr.mcp_server_runtime = {"vivado": {}}

        async def fake_disable(name, timeout):
            pass

        mock_mgr.disable_mcp_server = fake_disable
        mock_plugin.context.get_llm_tool_manager = MagicMock(return_value=mock_mgr)
        await bootstrap_module.shutdown_mcp(mock_plugin, state=fresh_state)
        assert task.cancelled() or task.done()
        assert fresh_state.get_task() is None

    @pytest.mark.asyncio
    async def test_disable_called(self, mock_plugin, fresh_state):
        fresh_state.mark_running(True)
        called_with: list = []

        async def fake_disable(name, timeout):
            called_with.append((name, timeout))

        mock_mgr = MagicMock()
        mock_mgr.mcp_server_runtime = {"vivado": {}}
        mock_mgr.disable_mcp_server = fake_disable
        mock_plugin.context.get_llm_tool_manager = MagicMock(return_value=mock_mgr)
        await bootstrap_module.shutdown_mcp(mock_plugin, state=fresh_state)
        assert called_with == [("vivado", 15)]
        assert fresh_state.mcp_running is False

    @pytest.mark.asyncio
    async def test_shutdown_failure_not_raised(self, mock_plugin, fresh_state):
        fresh_state.mark_running(True)

        async def fail_disable(name, timeout):
            raise RuntimeError("zombie")

        mock_mgr = MagicMock()
        mock_mgr.mcp_server_runtime = {"vivado": {}}
        mock_mgr.disable_mcp_server = fail_disable
        mock_plugin.context.get_llm_tool_manager = MagicMock(return_value=mock_mgr)
        await bootstrap_module.shutdown_mcp(mock_plugin, state=fresh_state)
        assert fresh_state.mcp_running is False
