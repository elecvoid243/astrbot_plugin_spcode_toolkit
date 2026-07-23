"""tests/test_vivado_subsystem.py - 覆盖 VivadoSubsystem facade。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_plugin():
    p = MagicMock()
    p._config = {"vivado_enabled": True, "vivado_executable": ""}
    return p


class TestVivadoSubsystem:
    def test_init_creates_state_and_manager(self, mock_plugin):
        from tools.vivado import VivadoSubsystem
        from tools.vivado.state import VivadoState

        sub = VivadoSubsystem(plugin=mock_plugin)
        assert isinstance(sub._state, VivadoState)
        assert sub._plugin_getter() is mock_plugin
        assert sub._manager is not None

    @pytest.mark.asyncio
    async def test_bootstrap_delegates(self, mock_plugin):
        from tools.vivado import VivadoSubsystem

        sub = VivadoSubsystem(plugin=mock_plugin)
        with patch("tools.vivado.bootstrap_mcp", new_callable=AsyncMock) as mock_boot:
            await sub.bootstrap()
        mock_boot.assert_called_once_with(mock_plugin, state=sub._state)

    @pytest.mark.asyncio
    async def test_shutdown_delegates(self, mock_plugin):
        from tools.vivado import VivadoSubsystem

        sub = VivadoSubsystem(plugin=mock_plugin)
        with patch("tools.vivado.shutdown_mcp", new_callable=AsyncMock) as mock_shut:
            await sub.shutdown()
        mock_shut.assert_called_once_with(mock_plugin, state=sub._state)
