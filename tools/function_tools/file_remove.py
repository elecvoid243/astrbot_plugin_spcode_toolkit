"""FileRemoveTool — 沙箱化文件/目录删除(带回收站 + 黑名单)。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

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
        from astrbot.core.tools import fs_access

        _record(self.name)
        try:
            await fs_access.assert_writable(path, context)
        except PermissionError as exc:
            return f"Error: {exc}"
        try:
            result = await run_sync(
                file_remove.remove,
                path,
                confirm,
                max_items,
                list(self.custom_blacklist),
            )
            _record_turn_removal(context, path, result)
            return unwrap(result)
        except Exception as e:
            return err_json(f"file_remove 失败: {e}")


def _record_turn_removal(
    context: ContextWrapper[AstrAgentContext],
    raw_path: str,
    result: object,
) -> None:
    """删除成功后向 agent 上下文记录 changed_files 条目(kind=remove)。

    主仓库的 agent runner 会把 ``AstrAgentContext.extra["changed_files"]``
    聚合为 ChatUI 回合文件变更总结卡片;卡片的"撤销删除"按钮再经
    ``POST /spcode/file-remove/restore`` 把文件从回收站搬回原位。
    防御式 getattr——轻量测试上下文没有 extra 字段,直接跳过。
    """
    if not (isinstance(result, dict) and result.get("ok")):
        return
    extra = getattr(context.context, "extra", None)
    if not isinstance(extra, dict):
        return
    try:
        resolved = str(Path(raw_path).resolve())
    except OSError:
        resolved = str(raw_path).strip()
    extra.setdefault("changed_files", []).append(
        {
            "path": resolved,
            "kind": "remove",
            "runtime": "local",
            "backup_id": "",
            "ts": time.time(),
        }
    )
