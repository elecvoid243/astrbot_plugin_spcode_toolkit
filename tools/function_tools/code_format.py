"""CodeFormatTool — 源代码自动格式化(Python/C·C++/Java/JS·TS/C#)。

v2.14 (2026-06-25) 引入。
v2.14.1 (2026-06-25) 简化 LLM 暴露面:
  - 移除 formatter 参数:后缀名自动路由(.py → ruff, 其他 → astyle)
  - 移除 style/indent 参数:从插件配置注入(``code_format.default_style`` /
    ``code_format.default_indent``),LLM 不再关心 astyle 风格细节

仿照 file_remove.custom_blacklist 模式:FunctionTool 实例属性
``default_style`` / ``default_indent`` 由 main.py 在初始化时从 _config 注入。

- Python: ruff format
- C/C++/Java/JS/TS/C#: AStyle(通过 stdin/stdout 调用)

Author: elecvoid243, 2026-06-25
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ._common import record_and_run


@dataclass
class CodeFormatTool(FunctionTool):
    name: str = "code_format"
    description: str = (
        "Auto-format a single source file in-place. "
        "Set check=true for dry-run (detect changes without writing the file). "
        "Supported extensions: .py (ruff format); "
        ".c/.cpp/.h/.hpp/.cc/.cxx/.hxx/.hh/.java/.js/.jsx/.mjs/.cjs/.cs "
        "(AStyle). Other extensions are rejected. "
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": (
                        "Path of the source file. Extension determines the formatter "
                        "(.py → ruff; other supported extensions → AStyle)."
                    ),
                },
                "check": {
                    "type": "boolean",
                    "description": (
                        "if true means Dry-run: detect changes WITHOUT writing to the file. "
                        "Returns changed=true if the file would be modified."
                    ),
                    "default": False,
                },
            },
            "required": ["filepath"],
        }
    )

    default_style: str = "linux"
    default_indent: int = 4

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        filepath: str,
        check: bool = False,
        **kwargs,
    ) -> ToolExecResult:
        from .. import code_format

        # LLM 不再传 formatter/style/indent,全部从实例属性读
        return await record_and_run(
            self.name, code_format.format, filepath, "auto",
            check=check,
            style=self.default_style,
            indent=self.default_indent,
            err_prefix="code_format",
        )
