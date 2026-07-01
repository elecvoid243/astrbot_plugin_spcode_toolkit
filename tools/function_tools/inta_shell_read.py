"""IntaShellReadTool — 读取活跃 inta_shell 会话的输出(阻塞等待新数据)。"""

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
class IntaShellReadTool(FunctionTool):
    name: str = "astrbot_inta_shell_read"
    description: str = (
        "Read output from an active inta_shell session. Blocks up to "
        "`timeout` seconds for new output. A prompt (e.g. `[Y/n]`, `>>>`) "
        "in the returned text usually means the program is waiting for "
        "inta_shell_send input."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID returned by inta_shell_start.",
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Max seconds to wait for output. Increase for slow programs."
                    ),
                    "default": 5.0,
                },
                "max_chars": {
                    "type": "number",
                    "description": ("Max characters to read. Caps large outputs."),
                    "default": 4096,
                },
            },
            "required": ["session_id"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        session_id: str,
        timeout: float = 5.0,
        max_chars: int = 4096,
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.read() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.read(
                _inta_runtime.component,
                session_id,
                timeout=timeout,
                max_chars=max_chars,
            )
        except Exception as e:
            return err_json(f"inta_shell_read 失败: {e}")
