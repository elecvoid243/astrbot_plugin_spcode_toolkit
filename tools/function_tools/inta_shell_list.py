"""IntaShellListTool — 列出所有活跃的 inta_shell 会话。"""
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
class IntaShellListTool(FunctionTool):
    name: str = "astrbot_inta_shell_list"
    description: str = (
        "List all active inta_shell sessions. Use to check what needs cleanup "
        "with inta_shell_stop before finishing a task."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        _record(self.name)
        try:
            # WHY: tools.inta_shell.tools.list_sessions() 内部已通过 _ok/_deny
            # 返回 JSON 字符串。直接透传,绕过 unwrap() 的 dict 类型校验。
            return await _inta_shell_tools.list_sessions(_inta_runtime.component)
        except Exception as e:
            return err_json(f"inta_shell_list 失败: {e}")
