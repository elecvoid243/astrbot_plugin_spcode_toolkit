"""CodeCrapTool — CRAP 指标检测(Python → crap4py;C/C++ → lizard)。

只读工具,plan 模式可用(不在 plan_mode_blocked_tools 中)。
lcov 可选:缺省时按覆盖率 0% 计最坏情况分值,coverage=null 标注。
Spec: docs/superpowers/specs/2026-08-30-code-crap-tool-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ._common import record_and_run


@dataclass
class CodeCrapTool(FunctionTool):
    name: str = "code_crap"
    description: str = (
        "CRAP (Change Risk Anti-Patterns) risk analysis for a single Python or "
        "C/C++ source file. CRAP = CC^2 * (1-coverage)^3 + CC per function "
        "combines cyclomatic complexity with test coverage (30 = warning "
        "threshold). Engines by extension: .py -> crap4py (branch coverage "
        "from LCOV); .c/.cpp/.cc/.cxx/.h/.hpp/.hxx/.hh -> lizard (line "
        "coverage from LCOV). Both return the same schema: per-function "
        "name/lines/cc/coverage/crap/risk/suggestion sorted worst-first, "
        "plus a summary. Coverage is OPTIONAL: pass `lcov` = absolute path to "
        "an LCOV file (pytest --cov --cov-report=lcov for Python, gcovr "
        "--lcov / lcov for C/C++). Without it, coverage=null and crap is the "
        "WORST-CASE value (coverage assumed 0%) - run tests and pass lcov "
        "for real scores. NOTE: CC counting conventions differ between the "
        "two engines; do NOT compare scores across languages. Read-only, "
        "safe in plan mode. IMPORTANT: `filepath` MUST be an absolute path "
        "(e.g. `F:/repo/src/main.py`); relative paths resolve against the "
        "AstrBot root directory and will likely fail."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": (
                        "ABSOLUTE path to the source file (MUST be absolute, "
                        "e.g. `F:/repo/src/main.py`). Extension determines "
                        "the engine: .py -> crap4py; "
                        ".c/.cpp/.cc/.cxx/.h/.hpp/.hxx/.hh -> lizard."
                    ),
                },
                "lcov": {
                    "type": "string",
                    "description": (
                        "Optional ABSOLUTE path to an LCOV coverage file. "
                        "Python: pytest --cov --cov-branch --cov-report=lcov; "
                        "C/C++: gcovr --lcov (or lcov). Omit -> worst-case "
                        "scores (coverage assumed 0%)."
                    ),
                    "default": None,
                },
                "max_crap": {
                    "type": "number",
                    "description": (
                        "Optional warning threshold (risk tiers: <30 ok, "
                        "30-60 moderate, >60 high). Defaults to the plugin "
                        "config value (30)."
                    ),
                    "default": 30,
                },
            },
            "required": ["filepath"],
        }
    )
    # 插件配置注入的默认阈值(LLM 不可见;调用参数 max_crap 显式传值时优先)
    max_crap_threshold: float = 30.0

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        filepath: str,
        lcov: str | None = None,
        max_crap: float | None = None,
        **kwargs,
    ) -> ToolExecResult:
        from .. import code_crap

        threshold = (
            float(max_crap) if max_crap is not None else float(self.max_crap_threshold)
        )
        return await record_and_run(
            self.name,
            code_crap.check,
            filepath,
            lcov,
            max_crap=threshold,
            err_prefix="code_crap",
        )
