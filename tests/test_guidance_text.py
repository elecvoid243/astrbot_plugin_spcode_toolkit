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

    def test_vivado_template_renders_with_marker_first_line(self):
        """按 inject_vivado_guidance 的实参渲染 → 哨兵在首行,且占位符全部消解。

        哨兵必须落在渲染结果里(否则去重门失效,每轮请求重复追加);
        占位符未消解则说明 .format() 缺参,LLM 会看到花括号原文。
        """
        from tools._guidance_text import (
            VIVADO_GUIDANCE_TEMPLATE,
            VIVADO_INJECTION_MARKER,
            VIVADO_WRITE_TOOLS,
        )

        text = VIVADO_GUIDANCE_TEMPLATE.format(
            marker=VIVADO_INJECTION_MARKER,
            tool_count=21,
            session_default="default",
            write_tool_count=len(VIVADO_WRITE_TOOLS),
            write_tools_sample=", ".join(VIVADO_WRITE_TOOLS[:5]) + "...",
        )
        assert text.startswith(VIVADO_INJECTION_MARKER)
        assert "{" not in text and "}" not in text
        assert "mcp_vivado__" in text
        assert "11" in text
