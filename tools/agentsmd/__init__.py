"""agentsmd 子系统(PR-5 2026-06-23 提取自 main.py)。

构成:
- ``_core``: 原 ``tools/agentsmd.py`` — 常量、helpers、LLM 生成函数
- ``_state``: per-umo AgentsState + manager
- ``_handlers``: 4 个 /agentsmd 子命令实现 + on_llm_request 钩子
- ``AgentsmdSubsystem``: 统一对外门面(plugin 实例化一个,委托 init/load/..)

向后兼容:
    老代码 ``from tools import agentsmd as _agentsmd_mod`` 仍可用,
    tools/agentsmd.py 已被 git mv 到 tools/agentsmd/_core.py,
    旧的 import 路径由本 __init__.py 重新导出所有原符号。
"""

from __future__ import annotations

from typing import Callable

from ._core import (
    CODE_FILE_EXTENSIONS,
    DEFAULT_AGENTS_MD,
    DEFAULT_INIT_TEMPLATE,
    DEFAULT_INJECTION_HEADER,
    INJECTION_MARKER,
    KEY_PROJECT_FILES,
    PROJECT_PATH_PREFIX_TEMPLATE,
    _SKIP_DIRS,  # noqa: F401  (向后兼容 — 老代码 from tools import agentsmd; a._SKIP_DIRS)
    build_injection,
    generate_agents_md_via_llm,
    has_code_files,
    resolve_init_template,
    scan_project_context,
    strip_code_fence,
    strip_surrounding_quotes,
)
from ._handlers import AgentsmdHandlers
from ._state import AgentsState, AgentsStateManager

__all__ = [
    # 常量
    "CODE_FILE_EXTENSIONS",
    "_SKIP_DIRS",
    "DEFAULT_AGENTS_MD",
    "DEFAULT_INIT_TEMPLATE",
    "DEFAULT_INJECTION_HEADER",
    "INJECTION_MARKER",
    "KEY_PROJECT_FILES",
    "PROJECT_PATH_PREFIX_TEMPLATE",
    # helpers
    "build_injection",
    "generate_agents_md_via_llm",
    "has_code_files",
    "resolve_init_template",
    "scan_project_context",
    "strip_code_fence",
    "strip_surrounding_quotes",
    # subsystem
    "AgentsmdSubsystem",
    "AgentsmdHandlers",
    "AgentsState",
    "AgentsStateManager",
]


class AgentsmdSubsystem:
    """/agentsmd 命令组 + LLM 注入的统一门面。

    用法(在 main.py):
        self.agentsmd = AgentsmdSubsystem(
            plugin=self,
            is_path_safe=_is_path_safe,
        )

    然后把 4 个 /agentsmd 子命令的薄壳委托到本类对应方法,
    on_llm_request 钩子同样委托。

    为什么不把 @filter.command_group 装饰直接放本类?
        AstrBot 的 @filter 装饰器期望作用在插件类的实例方法上,
        它需要 self 指向 plugin 实例(plugin 启动时被框架扫描)。
        命令注册由 main.py 持有,本类只负责"业务实现"。
    """

    def __init__(
        self,
        *,
        plugin,
        is_path_safe: Callable,
    ) -> None:
        # plugin 反向引用 — 用 lambda 包裹以兼容 hot-reload
        # (虽然 hot-reload 不会重建 plugin 实例,留作未来扩展)
        self._plugin = plugin
        self._state = AgentsStateManager()
        self._handlers = AgentsmdHandlers(
            state=self._state,
            plugin_getter=lambda: plugin,
            is_path_safe=is_path_safe,
        )

    # ── 命令薄壳(main.py 委托入口) ─────────────

    async def init(self, event, directory: str):
        async for msg in self._handlers.init(event, directory):
            yield msg

    async def load(self, event, directory: str):
        async for msg in self._handlers.load(event, directory):
            yield msg

    def unload(self, event):
        return self._handlers.unload(event)

    async def update(self, event):
        async for msg in self._handlers.update(event):
            yield msg

    async def on_llm_request(self, event, req) -> None:
        """main.py 的 @filter.on_llm_request() 装饰方法会调用本方法。"""
        await self._handlers.on_llm_request(event, req)

    # ── 状态查询(供 main.py / tests 使用) ──────

    @property
    def state(self) -> AgentsStateManager:
        return self._state

    def clear(self) -> None:
        """terminate 时清空所有 per-umo state。"""
        self._state.clear()
