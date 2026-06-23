"""FileDiffTool — 结构化文件差异比较(统一 diff + 计数)。"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ._common import record_and_run


@dataclass
class FileDiffTool(FunctionTool):
    name: str = "astrbot_file_compare"
    description: str = (
        "Compares two text files and returns a structured diff: counts of added and "
        "removed lines, plus a unified diff. Files larger than 50MB are rejected. "
        "Reads as UTF-8 with a GBK fallback for Windows-encoded Chinese text. "
        "Use this to review the impact of an edit or to compare candidate alternatives."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "file_a": {
                    "type": "string",
                    "description": "Path of the first (baseline) file.",
                },
                "file_b": {
                    "type": "string",
                    "description": "Path of the second (modified) file.",
                },
            },
            "required": ["file_a", "file_b"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        file_a: str,
        file_b: str,
        **kwargs,
    ) -> ToolExecResult:
        from .. import file_compare

        return await record_and_run(
            self.name, file_compare.compare, file_a, file_b, err_prefix="file_compare"
        )
