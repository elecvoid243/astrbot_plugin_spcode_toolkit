"""Per-module-level state for vivado subsystem (PR-1 2026-07-23)。

参照 tools/codegraph/state.py 风格。per-umo 状态不需要 (本次 /vivado 命令全局)。

公开 API:
    VivadoState      - dataclass 持有所有可变状态
    get_state()      - 模块级单例访问
    reset_state()    - terminate/测试用
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

SESSIONS_CACHE_TTL = 5.0


@dataclass
class VivadoState:
    """vivado 子系统的运行时状态。

    字段:
        bootstrap_task:    当前正在跑的 bootstrap 协程 (None = 没在启)
        mcp_running:       "vivado" 是否在 mcp_server_runtime
        mcp_started_at:    MCP 启动时间戳 (epoch seconds, 0 = 未启)
        sessions_cached:   最近一次 list_sessions 的结果
        sessions_cached_at: 缓存时间戳
        sessions_stale:    True = get_sessions 返回了过期空集 (供 Dashboard 显 "stale")
    """

    bootstrap_task: asyncio.Task | None = None
    mcp_running: bool = False
    mcp_started_at: float = 0.0
    sessions_cached: list[dict] = field(default_factory=list)
    sessions_cached_at: float = 0.0
    sessions_stale: bool = False

    def set_task(self, task: asyncio.Task | None) -> None:
        self.bootstrap_task = task

    def get_task(self) -> asyncio.Task | None:
        return self.bootstrap_task

    def clear_task(self) -> None:
        self.bootstrap_task = None

    def mark_running(self, running: bool, started_at: float = 0.0) -> None:
        self.mcp_running = running
        if running:
            self.mcp_started_at = started_at or time.time()
        else:
            self.mcp_started_at = 0.0

    def update_sessions(self, sessions: list[dict]) -> None:
        self.sessions_cached = list(sessions)
        self.sessions_cached_at = time.time()
        self.sessions_stale = False

    def get_sessions(self) -> list[dict]:
        if not self.sessions_cached:
            return []
        age = time.time() - self.sessions_cached_at
        if age > SESSIONS_CACHE_TTL:
            self.sessions_stale = True
            return []
        return list(self.sessions_cached)

    def clear(self) -> None:
        self.clear_task()
        self.mark_running(False)
        self.sessions_cached = []
        self.sessions_cached_at = 0.0
        self.sessions_stale = False


_state: VivadoState | None = None


def get_state() -> VivadoState:
    global _state
    if _state is None:
        _state = VivadoState()
    return _state


def reset_state() -> None:
    global _state
    _state = None
