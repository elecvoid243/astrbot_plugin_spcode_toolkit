"""CodeCheckTool — Python/C/C++ 语法+风格合并检查。"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ._common import record_and_run


@dataclass
class CodeCheckTool(FunctionTool):
    name: str = "code_check"
    description: str = (
        "Unified syntax + style check for a single Python or C/C++ source file. "
        "For both languages a single linter covers BOTH syntax errors and style "
        "issues, so one call replaces a 'syntax check then lint' workflow. "
        "Auto-detects the linter from the file extension: "
        ".py → ruff (PEP 8 + common lint rules); "
        ".c/.cpp/.cc/.cxx/.h/.hpp/.hxx → cpplint (Google C++ Style Guide). "
        "Returns a structured list of issues; the first 5 include surrounding "
        "source-context lines (→ marks the offending line). "
        "Other extensions (e.g. .js/.ts/.go/.nim) are NOT supported by this "
        "tool. Requires the linter to be installed: "
        "pip install ruff (Python) or pip install cpplint (C/C++)."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": (
                        "Path to the source file. Extension determines the linter: "
                        ".py → ruff; .c/.cpp/.cc/.cxx/.h/.hpp/.hxx → cpplint."
                    ),
                },
                "linter": {
                    "type": "string",
                    "enum": ["auto", "ruff", "cpplint"],
                    "description": (
                        "Override the linter. 'auto' (default) picks by extension."
                    ),
                    "default": "auto",
                },
            },
            "required": ["filepath"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        filepath: str,
        linter: str = "auto",
        **kwargs,
    ) -> ToolExecResult:
        from .. import code_check

        return await record_and_run(
            self.name, code_check.check, filepath, linter, err_prefix="code_check"
        )
