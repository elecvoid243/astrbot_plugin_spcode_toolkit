"""FileRemoveTool — 沙箱化文件/目录删除(带回收站 + 黑名单)。"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .._helpers import err_json, run_sync, unwrap
from .._stats import _record


@dataclass
class FileRemoveTool(FunctionTool):
    name: str = "astrbot_file_remove"
    description: str = (
        "Delete an entire file or directory. Before deleting, it is necessary to ask the user. "
        "If delete fragments instead of the entire file, use `astrbot_file_edit_tool`. "
        "Deleting a DIRECTORY requires parameter 'confirm=true'. "
        "If a directory contains more than max_items files, the call returns a "
        "proposal asking for batch confirmation INSTEAD of deleting — read the "
        "proposal/options, then retry with confirm=true. "
        "Single files are deleted without confirm. "
        "Items are sent to the system recycle bin (recoverable), not permanently deleted."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Absolute path of the file or directory to remove. "
                        "Must not contain '..' segments and must not be inside a "
                        "protected system directory or the user-configured "
                        "blacklist (see plugin config 'file_remove_blacklist')."
                    ),
                },
                "confirm": {
                    "type": "boolean",
                    "description": (
                        "Set to true to confirm a directory deletion. "
                        "Required for directories; ignored for single files."
                    ),
                    "default": False,
                },
                "max_items": {
                    "type": "integer",
                    "description": (
                        "If a directory contains more than this many files, return a "
                        "proposal for batch confirmation instead of deleting. "
                        "Defaults to 50."
                    ),
                    "default": 50,
                },
            },
            "required": ["path"],
        }
    )
    # 用户自定义黑名单（从插件配置 file_remove_blacklist 注入），
    # 不暴露给 LLM 作为 function parameter——是服务端策略。
    custom_blacklist: list[str] = field(default_factory=list)

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        path: str,
        confirm: bool = False,
        max_items: int = 50,
        **kwargs,
    ) -> ToolExecResult:
        from .. import file_remove

        _record(self.name)
        try:
            result = await run_sync(
                file_remove.remove,
                path,
                confirm,
                max_items,
                list(self.custom_blacklist),
            )
            return unwrap(result)
        except Exception as e:
            return err_json(f"file_remove 失败: {e}")
