"""vivado-mcp guidance injection (PR-5 2026-07-23)。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from .._guidance_text import (
    VIVADO_GUIDANCE_TEMPLATE,
    VIVADO_INJECTION_MARKER,
    VIVADO_WRITE_TOOLS,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def inject_vivado_guidance(plugin: "SPCodeToolkit", req: Any) -> None:
    """条件式注入 vivado 工具使用说明到 system_prompt。

    条件:
        1. vivado_enabled=False → 跳过
        2. 'vivado' not in mcp_server_runtime → 跳过
        3. 已含 marker → 跳过 (防重复)
        4. 注入 VIVADO_GUIDANCE_TEMPLATE
    """
    if not plugin._config.get("vivado_enabled", True):
        return
    try:
        mgr = plugin.context.get_llm_tool_manager()
        if "vivado" not in mgr.mcp_server_runtime:
            return
    except Exception as e:
        logger.debug(f"inject_vivado_guidance: 查 mcp_server_runtime 失败: {e}")
        return

    prompt = getattr(req, "system_prompt", None) or ""
    if VIVADO_INJECTION_MARKER in prompt:
        return

    text = VIVADO_GUIDANCE_TEMPLATE.format(
        marker=VIVADO_INJECTION_MARKER,
        tool_count=21,
        session_default="default",
        write_tool_count=len(VIVADO_WRITE_TOOLS),
        write_tools_sample=", ".join(VIVADO_WRITE_TOOLS[:5]) + "...",
    )
    req.system_prompt = (prompt + "\n\n" + text) if prompt else text
