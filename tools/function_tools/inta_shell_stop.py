"""IntaShellStopTool — 终止 inta_shell 会话(发 Ctrl+C + 超时 kill)。"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .._helpers import err_json
from .._stats import _record
from ..inta_shell import tools as _inta_shell_tools
from ..inta_shell import runtime as _inta_runtime


@dataclass
class IntaShellStopTool(FunctionTool):
    name: str = "astrbot_inta_shell_stop"
    description: str = (
        "Terminate an inta_shell session. ALWAYS call this when done to free "
        "resources (each session holds a process and pipes). Default: send "
        "Ctrl+C for graceful exit, then force-kill on timeout. Use `force=true` "
        "only if the session is fully unresponsive. Use inta_shell_list first "
        "to see what's still running."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to terminate.",
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "If true, kill immediately without sending Ctrl+C. "
                        "Use only when the session is fully unresponsive."
                    ),
                    "default": False,
                },
            },
            "required": ["session_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        session_id: str,
        force: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.stop() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.stop(
                _inta_runtime.component, session_id, force=force
            )
        except Exception as e:
            return err_json(f"inta_shell_stop 失败: {e}")
