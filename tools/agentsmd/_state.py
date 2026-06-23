"""Per-session AGENTS.md loading state.

PR-5 (2026-06-23): 从 main.py._loaded_agents (dict[str, dict]) 抽出,
改为 @dataclass + 显式 manager API。

设计:
- ``AgentsState``: 单个会话的 AGENTS.md 加载快照
- ``AgentsStateManager``: per-umo 增删查改
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentsState:
    """单个会话的 AGENTS.md 加载快照。

    字段:
        path:       AGENTS.md 文件绝对路径
        directory:  所在项目根目录(用于注入到 system_prompt)
        last_content: 上次读取的全文(缓存,供 mtime 未变时复用)
        mtime:      上次读取时的文件 mtime(用于检测变更)
    """

    path: str
    directory: str
    last_content: str
    mtime: float

    def update_content(self, new_content: str, new_mtime: float) -> None:
        """就地刷新缓存内容(检测到文件变更时由 hook 调用)。"""
        self.last_content = new_content
        self.mtime = new_mtime


class AgentsStateManager:
    """per-umo AgentsState 容器。

    原 main.py 的 self._loaded_agents: dict[str, dict] 直接换成
    ``AgentsStateManager`` 实例,API 一一对应:
        mgr.get(umo)        -> AgentsState | None
        mgr.set(umo, st)    -> None
        mgr.pop(umo)        -> AgentsState | None
        mgr.contains(umo)   -> bool
        mgr.iter_umos()     -> Iterable[str]

    之所以独立 manager 而非裸 dict:
        1. 显式类型(AgentsState)取代 dict[str, dict],IDE 可补全
        2. 未来要做 mtime 全局广播 / 持久化等只改 manager 即可
    """

    def __init__(self) -> None:
        self._store: dict[str, AgentsState] = {}

    def get(self, umo: str) -> Optional[AgentsState]:
        return self._store.get(umo)

    def set(self, umo: str, state: AgentsState) -> None:
        self._store[umo] = state

    def pop(self, umo: str) -> Optional[AgentsState]:
        return self._store.pop(umo, None)

    def contains(self, umo: str) -> bool:
        return umo in self._store

    def __contains__(self, umo: str) -> bool:
        """支持 ``umo in mgr`` 语法。"""
        return self.contains(umo)

    def iter_umos(self):
        """返回所有已加载 AGENTS.md 的 umo 集合(测试 / 调试用)。"""
        return self._store.keys()

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        """清空全部状态(terminate 时调用)。"""
        self._store.clear()