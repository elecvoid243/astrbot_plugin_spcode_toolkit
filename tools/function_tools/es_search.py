"""EsSearchTool — 文件名极速搜索 (Everything / fallback)。"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ._common import record_and_run


@dataclass
class EsSearchTool(FunctionTool):
    name: str = "es_search"
    description: str = (
        "Fast FILENAME search (does not search file contents). "
        "Prefer this over reading whole directory trees to locate a file. "
        "Supports wildcards, regex, extension and path filters, case/whole-word"
        "toggles, and size/date sorting."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Filename or pattern. Examples: '*.py', 'main', 'config.json'. "
                        "On Windows, supports Everything syntax (ext:py, path:C:\\src). "
                        "On POSIX, basic wildcards only. "
                        "Must NOT start with '/' or '-' unless regex=true."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Limit search to this directory. Omit to search the whole system."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 100,
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat query as a regular expression (Windows only).",
                    "default": False,
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Case-sensitive matching.",
                    "default": False,
                },
                "whole_word": {
                    "type": "boolean",
                    "description": "Match whole words only (Windows only).",
                    "default": False,
                },
                "file_type": {
                    "type": "string",
                    "enum": ["all", "file", "folder"],
                    "description": "Restrict result type to files, folders, or both.",
                    "default": "all",
                },
                "sort_by": {
                    "type": "string",
                    "enum": [
                        "name",
                        "path",
                        "size",
                        "ext",
                        "date_modified",
                        "date_created",
                        "date_accessed",
                        "run_count",
                    ],
                    "description": (
                        "Sort field. Most options work on Windows; "
                        "POSIX backends only support name/path/size/date_modified."
                    ),
                },
                "ext": {
                    "type": "string",
                    "description": (
                        "Filter by file extension WITHOUT the leading dot, e.g. 'py', "
                        "'xlsx', 'exe'."
                    ),
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        query: str,
        path: str | None = None,
        max_results: int = 100,
        regex: bool = False,
        case_sensitive: bool = False,
        whole_word: bool = False,
        file_type: str = "all",
        sort_by: str | None = None,
        ext: str | None = None,
        **kwargs,
    ) -> ToolExecResult:
        from .. import es_search

        return await record_and_run(
            self.name,
            es_search.search,
            query,
            path,
            max_results,
            regex,
            case_sensitive,
            whole_word,
            file_type,
            sort_by,
            ext,
            err_prefix="es_search",
        )
