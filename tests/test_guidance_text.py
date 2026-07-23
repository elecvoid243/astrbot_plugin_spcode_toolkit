"""tests/test_guidance_text.py — _guidance_text.py 常量与模板定义测试。

PR-5 (2026-07-23): vivado-mcp 指引常量定义验证。
"""

from __future__ import annotations


class TestVivadoGuidance:
    def test_vivado_guidance_template_exists(self):
        from tools._guidance_text import VIVADO_GUIDANCE_TEMPLATE

        assert isinstance(VIVADO_GUIDANCE_TEMPLATE, str)
        assert "{marker}" in VIVADO_GUIDANCE_TEMPLATE
        assert "{session_default}" in VIVADO_GUIDANCE_TEMPLATE

    def test_vivado_injection_marker_exists(self):
        from tools._guidance_text import VIVADO_INJECTION_MARKER

        assert isinstance(VIVADO_INJECTION_MARKER, str)
        assert len(VIVADO_INJECTION_MARKER) > 0

    def test_vivado_write_tools_has_11(self):
        from tools._guidance_text import VIVADO_WRITE_TOOLS

        assert len(VIVADO_WRITE_TOOLS) == 11
        assert "mcp_vivado__run_synthesis" in VIVADO_WRITE_TOOLS
        assert "mcp_vivado__program_device" in VIVADO_WRITE_TOOLS
        assert "mcp_vivado__run_tcl" in VIVADO_WRITE_TOOLS
