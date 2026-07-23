"""tests/test_vivado_inject.py — 覆盖 inject_vivado_guidance。

PR-5 (2026-07-23): system_prompt 注入 vivado-mcp 工具使用说明。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_plugin():
    p = MagicMock()
    p._config = {"vivado_enabled": True, "vivado_executable": ""}
    p.context.get_llm_tool_manager = MagicMock(
        return_value=MagicMock(mcp_server_runtime={"vivado": {}})
    )
    return p


class TestInjectVivadoGuidance:
    @pytest.mark.asyncio
    async def test_disabled_skips(self, mock_plugin):
        from tools.vivado.inject import inject_vivado_guidance

        mock_plugin._config["vivado_enabled"] = False
        req = MagicMock()
        req.system_prompt = ""
        await inject_vivado_guidance(mock_plugin, req)
        assert "vivado-mcp integration" not in req.system_prompt

    @pytest.mark.asyncio
    async def test_mcp_not_running_skips(self, mock_plugin):
        from tools.vivado.inject import inject_vivado_guidance

        mock_plugin.context.get_llm_tool_manager = MagicMock(
            return_value=MagicMock(mcp_server_runtime={})
        )
        req = MagicMock()
        req.system_prompt = ""
        await inject_vivado_guidance(mock_plugin, req)
        assert "vivado-mcp integration" not in req.system_prompt

    @pytest.mark.asyncio
    async def test_idempotent(self, mock_plugin):
        from tools.vivado.inject import inject_vivado_guidance
        from tools._guidance_text import VIVADO_INJECTION_MARKER

        req = MagicMock()
        req.system_prompt = f"prefix\n{VIVADO_INJECTION_MARKER}\nbody"
        await inject_vivado_guidance(mock_plugin, req)
        assert req.system_prompt.count(VIVADO_INJECTION_MARKER) == 1

    @pytest.mark.asyncio
    async def test_injects_when_enabled(self, mock_plugin):
        from tools.vivado.inject import inject_vivado_guidance
        from tools._guidance_text import VIVADO_INJECTION_MARKER

        req = MagicMock()
        req.system_prompt = ""
        await inject_vivado_guidance(mock_plugin, req)
        assert VIVADO_INJECTION_MARKER in req.system_prompt
        assert "mcp_vivado__" in req.system_prompt
