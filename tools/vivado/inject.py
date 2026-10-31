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
    """Inject guidance only for an enabled, connected Vivado server.

    Args:
        plugin: Plugin owning the configuration and MCP runtime.
        req: LLM request receiving the system guidance.
    """
    if not plugin._config.get("vivado_enabled", True):
        return
    try:
        mgr = plugin.context.get_llm_tool_manager()
        runtime = mgr.mcp_server_runtime.get("vivado")
        if runtime is None:
            return
        client = runtime.client
        if not client.active or client.session is None or not client.tools:
            return
    except Exception as e:
        logger.debug("Unable to inspect Vivado MCP runtime: %s", e)
        return

    prompt = getattr(req, "system_prompt", None) or ""
    if VIVADO_INJECTION_MARKER in prompt:
        return

    text = VIVADO_GUIDANCE_TEMPLATE.format(
        marker=VIVADO_INJECTION_MARKER,
        tool_count=len(client.tools),
        session_default="default",
        write_tool_count=len(VIVADO_WRITE_TOOLS),
        write_tools_sample=", ".join(VIVADO_WRITE_TOOLS[:5]) + "...",
    )
    req.system_prompt = (prompt + "\n\n" + text) if prompt else text
