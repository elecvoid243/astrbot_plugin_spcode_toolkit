"""IntaShellStartTool — 启动长生命周期交互式 shell 会话。"""

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
class IntaShellStartTool(FunctionTool):
    name: str = "astrbot_inta_shell_start"
    description: str = (
        "Start a long-running interactive shell session for commands that need "
        "multiple rounds of input (e.g. npm init, python REPL, git add -p, "
        "interactive installers), or an always-on application (e.g. launch a server). Returns a session_id — keep using "
        "inta_shell_send / inta_shell_read to drive the session, and ALWAYS "
        "call inta_shell_stop when done. Does NOT support full TTY programs "
        "(vim, nano, less). For one-off non-interactive commands, prefer the "
        "regular `astrbot_execute_shell` tool."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Interactive command to start. If a non-interactive "
                        "form exists (e.g. `npm init -y` instead of `npm init`), "
                        "prefer that and use the regular execute_shell tool."
                    ),
                },
                "env": {
                    "type": "object",
                    "description": (
                        "Optional env-var dict. Keys and values must be strings."
                    ),
                },
            },
            "required": ["command"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        command: str,
        env: dict | None = None,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            event = context.context.event
            # WHY: tools.inta_shell.tools.start() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。如果再走 unwrap(str),会触发
            # "工具返回了非预期类型: str" 错误(虽然底层进程已被启动)。
            # 直接透传字符串结果即可。
            return await _inta_shell_tools.start(
                _inta_runtime.component,
                event.unified_msg_origin,
                command,
                env=env,
                default_cwd=_inta_runtime.default_cwd,
            )
        except Exception as e:
            return err_json(f"inta_shell_start 失败: {e}")
