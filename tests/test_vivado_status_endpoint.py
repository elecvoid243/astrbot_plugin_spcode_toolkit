"""tests/test_vivado_status_endpoint.py — 覆盖 vivado_status.handle()。

PR-4 (2026-07-23):GET /spcode/vivado-status 快照端点。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_plugin():
    p = MagicMock()
    p._config = {"vivado_enabled": True, "vivado_executable": ""}
    return p


class TestVivadoStatusHandle:
    @pytest.mark.asyncio
    async def test_disabled(self, mock_plugin):
        from tools.webapi.vivado_status import handle

        mock_plugin._config["vivado_enabled"] = False
        with patch(
            "tools.webapi.vivado_status.importlib.util.find_spec", return_value=object()
        ):
            resp = await handle(mock_plugin)
        assert resp["status"] == "ok"
        assert resp["data"]["enabled"] is False
        assert resp["data"]["mcp_running"] is False
        assert resp["data"]["install_missing"] is False

    @pytest.mark.asyncio
    async def test_vivado_mcp_not_installed(self, mock_plugin):
        from tools.webapi.vivado_status import handle

        with patch(
            "tools.webapi.vivado_status.importlib.util.find_spec", return_value=None
        ):
            resp = await handle(mock_plugin)
        assert resp["data"]["install_missing"] is True
        assert resp["data"]["mcp_running"] is False

    @pytest.mark.asyncio
    async def test_mcp_running_with_sessions(self, mock_plugin):
        from tools.webapi.vivado_status import handle
        from tools.vivado import state as state_module

        state_module.reset_state()
        state = state_module.get_state()
        state.mark_running(True, started_at=1000.0)
        state.update_sessions(
            [
                {"id": "default", "state": "running", "started_at": 1000.0},
                {"id": "dev", "state": "idle", "started_at": 1010.0},
            ]
        )
        mock_mgr = MagicMock()
        mock_mgr.mcp_server_runtime = {"vivado": {}}
        mock_plugin.context.get_llm_tool_manager = MagicMock(return_value=mock_mgr)
        with patch(
            "tools.webapi.vivado_status.importlib.util.find_spec", return_value=object()
        ), patch(
            "tools.webapi.vivado_status.find_vivado_executable",
            return_value="D:/Xilinx/Vivado/2024.1/bin/vivado.bat",
        ):
            resp = await handle(mock_plugin)
        assert resp["data"]["mcp_running"] is True
        assert resp["data"]["vivado_path"] == "D:/Xilinx/Vivado/2024.1/bin/vivado.bat"
        sessions = resp["data"]["sessions"]
        assert {s["id"] for s in sessions} >= {"default", "dev"}
        state_module.reset_state()

    @pytest.mark.asyncio
    async def test_envelope_format(self, mock_plugin):
        from tools.webapi.vivado_status import handle

        with patch(
            "tools.webapi.vivado_status.importlib.util.find_spec", return_value=object()
        ):
            resp = await handle(mock_plugin)
        assert {"status", "data", "elapsed_ms", "reason"} <= resp.keys()
        assert resp["status"] == "ok"

    @pytest.mark.asyncio
    async def test_sessions_read_failure_degraded(self, mock_plugin):
        from tools.webapi.vivado_status import handle

        with patch(
            "tools.webapi.vivado_status.importlib.util.find_spec", return_value=object()
        ), patch(
            "tools.webapi.vivado_status._vivado_state.get_state",
            side_effect=RuntimeError("state read failed"),
        ):
            resp = await handle(mock_plugin)
        assert resp["data"]["degraded"] is True
        assert resp["data"]["mcp_running"] is False
