"""codegraph 模块级状态(从 main.py:211-212 提取,PR-6 2026-06-23)。

状态:
    _codegraph_task:           asyncio.Task | None   # 当前 MCP 启动任务
    _codegraph_dir_locks:      dict[str, Lock]       # 每目录一个 lock(防并发 init 写坏 .codegraph/)
    _active_project_path:      str                   # 最后成功应用的 codegraph 项目路径(v2.14.x)

设计意图:把 main.py 的实例属性改为模块级单例。
- 原 main.py:  self._codegraph_task / self._codegraph_dir_locks(per-Plugin 实例)
- 新 tools.codegraph.state: 模块级变量(整个进程唯一)

迁移原因:
1. plugin 单例本身,模块级与实例级在功能上等价(都是进程内唯一)
2. 子系统代码不应回写 main.py 实例属性 — 模块级更解耦
3. 与 tools.inta_shell.runtime 一致(都走模块级单例)
"""

from __future__ import annotations

import asyncio
from typing import Optional

_codegraph_task: Optional[asyncio.Task] = None
_codegraph_dir_locks: dict[str, asyncio.Lock] = {}
_active_project_path: str = ""


def get_active_project_path() -> str:
    """返回最后成功生效的 codegraph 项目路径(空字符串 = 未设定)。

    由 CodegraphManager.set_project 在 MCP 重启成功后设置。
    """
    return _active_project_path


def set_active_project_path(path: str) -> None:
    """记录成功生效的 codegraph 项目路径。

    仅应在 set_project 完成 MCP 重启且确认成功后调用。
    """
    global _active_project_path
    _active_project_path = path


def get_task() -> Optional[asyncio.Task]:
    """取当前 MCP 启动 task(可空)。"""
    return _codegraph_task


def set_task(task: Optional[asyncio.Task]) -> None:
    """设置当前 MCP 启动 task(terminate 时传 None 取消引用)。"""
    global _codegraph_task
    _codegraph_task = task


def get_dir_lock(target: str) -> asyncio.Lock:
    """按目录路径取 lock(不存在则创建)。"""
    if target not in _codegraph_dir_locks:
        _codegraph_dir_locks[target] = asyncio.Lock()
    return _codegraph_dir_locks[target]


def reset() -> None:
    """清空所有状态(供测试 fixture 与 terminate 收尾)。

    WARNING:不取消 task,不释放 lock — 调用方负责在调用 reset() 之前
    先 set_task(None) 并确保所有 lock 持有者都已退出。
    """
    global _codegraph_task, _active_project_path
    _codegraph_task = None
    _active_project_path = ""
    _codegraph_dir_locks.clear()
