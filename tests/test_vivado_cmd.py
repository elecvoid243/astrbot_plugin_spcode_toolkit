"""tests/test_vivado_cmd.py - 覆盖 VivadoManager 4 个 cmd handler。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.vivado import state as state_module
from tools.vivado.manager import VivadoManager


@pytest.fixture
def mock_plugin():
    p = MagicMock()
    p._config = {"vivado_enabled": True, "vivado_executable": ""}
    return p


@pytest.fixture
def mock_mgr_setup(mock_plugin):
    state_module.reset_state()
    state = state_module.get_state()
    state.mark_running(True, started_at=1000.0)
    mgr = VivadoManager(state=state, plugin_getter=lambda: mock_plugin)
    return mgr, state


async def _collect(agen):
    out = []
    async for msg in agen:
        out.append(msg)
    return out


class TestCmdPath:
    @pytest.mark.asyncio
    async def test_returns_detected_path(self, mock_mgr_setup):
        mgr, _ = mock_mgr_setup
        with patch("tools.vivado.manager.find_vivado_executable",
                   return_value="D:/Xilinx/Vivado/2024.1/bin/vivado.bat"):
            msgs = await _collect(mgr.cmd_path(event=MagicMock()))
        assert any("D:/Xilinx/Vivado" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_not_found_returns_error(self, mock_mgr_setup):
        from tools.vivado.manager import VivadoNotFoundError
        mgr, _ = mock_mgr_setup
        with patch("tools.vivado.manager.find_vivado_executable",
                   side_effect=VivadoNotFoundError("not found")):
            msgs = await _collect(mgr.cmd_path(event=MagicMock()))
        assert any("未找到 Vivado" in m for m in msgs)


class TestCmdStatus:
    @pytest.mark.asyncio
    async def test_no_sessions_message(self, mock_mgr_setup):
        mgr, state = mock_mgr_setup
        state.sessions_cached = []  # ensure empty
        msgs = await _collect(mgr.cmd_status(event=MagicMock()))
        assert any("没有活跃" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_lists_sessions(self, mock_mgr_setup):
        mgr, state = mock_mgr_setup
        state.update_sessions([
            {"id": "default", "state": "running"},
            {"id": "dev", "state": "idle"},
        ])
        msgs = await _collect(mgr.cmd_status(event=MagicMock()))
        text = "\n".join(msgs)
        assert "default" in text
        assert "dev" in text


class TestCmdStart:
    @pytest.mark.asyncio
    async def test_mcp_not_running_yields_error(self, mock_mgr_setup):
        mgr, state = mock_mgr_setup
        state.mark_running(False)
        msgs = await _collect(mgr.cmd_start(event=MagicMock(), session_id="dev"))
        assert any("未运行" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_mcp_running_yields_instruction(self, mock_mgr_setup):
        mgr, _ = mock_mgr_setup
        msgs = await _collect(mgr.cmd_start(event=MagicMock(), session_id="dev"))
        text = "\n".join(msgs)
        assert "mcp_vivado__start_session" in text
        assert "'dev'" in text


class TestCmdStop:
    @pytest.mark.asyncio
    async def test_mcp_running_yields_instruction(self, mock_mgr_setup):
        mgr, _ = mock_mgr_setup
        msgs = await _collect(mgr.cmd_stop(event=MagicMock(), session_id="dev"))
        text = "\n".join(msgs)
        assert "mcp_vivado__stop_session" in text
        assert "'dev'" in text
