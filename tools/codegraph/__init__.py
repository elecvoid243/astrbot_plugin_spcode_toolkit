"""codegraph 命令管理子系统(从 main.py 提取,PR-6 2026-06-23)。

公开 API:
    state:           模块级状态(task 引用 + per-dir lock)
    CodegraphManager:  业务 manager(/codegraph init/uninit/set handler)
    bootstrap_mcp:   启动 codegraph MCP server(fire-and-forget)
    shutdown_mcp:    关闭 codegraph MCP server(terminate)
    build_mcp_cfg:   构造 MCP 启动配置(供 bootstrap_mcp + manager 重启复用)
"""

from __future__ import annotations

from .bootstrap import bootstrap_mcp, build_mcp_cfg, shutdown_mcp
from .manager import CodegraphManager
from . import state

__all__ = [
    "CodegraphManager",
    "bootstrap_mcp",
    "build_mcp_cfg",
    "shutdown_mcp",
    "state",
]
