"""IntaShellSendTool — 向活跃的 inta_shell 会话发送输入。"""

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
class IntaShellSendTool(FunctionTool):
    name: str = "astrbot_inta_shell_send"
    description: str = (
        "Send input text to an active inta_shell session to drive the program "
        "forward. Auto-appends a newline if missing. Common uses: answer "
        "prompts with 'y' / 'n' / empty (default), or type the next command. "
        "Pair with inta_shell_read to see the response."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID returned by inta_shell_start.",
                },
                "input": {
                    "type": "string",
                    "description": "Text to send to the interactive program.",
                },
                "send_eof": {
                    "type": "boolean",
                    "description": (
                        "If true, close stdin after sending (signals "
                        "end-of-input to the program)."
                    ),
                    "default": False,
                },
            },
            "required": ["session_id", "input"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        session_id: str,
        input: str,
        send_eof: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.send() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.send(
                _inta_runtime.component, session_id, input, send_eof=send_eof
            )
        except Exception as e:
            return err_json(f"inta_shell_send 失败: {e}")
