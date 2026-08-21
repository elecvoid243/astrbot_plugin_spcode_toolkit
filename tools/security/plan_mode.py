"""plan_mode 控制器 — 只读(readonly)文件访问模式的插件侧过滤层。

v3.x (2026-08-20): 状态由 AstrBot 核心 ``astrbot.core.tools.fs_access``
持有(per-umo)。本控制器只做两件事:
1. 派生:plan active := 核心模式 == READONLY
2. 过滤:active 时按 plan_mode_blocked_tools 从 req.func_tool 移除写工具
   + 首轮注入 reminder(prefix-cache 友好)

/plan /build 命令与 /spcode/plan-mode webapi 都委托本控制器,再写核心状态。
"""

from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.provider import ProviderRequest

logger = logging.getLogger(__name__)

_FS_ACCESS_WARNING_EMITTED = False


def _load_fs_access():
    """Late-import core fs_access; None outside AstrBot (bare unit tests)."""
    global _FS_ACCESS_WARNING_EMITTED
    try:
        from astrbot.core.tools import fs_access

        return fs_access
    except Exception as exc:  # pragma: no cover - test-env only
        if not _FS_ACCESS_WARNING_EMITTED:
            _FS_ACCESS_WARNING_EMITTED = True
            logger.warning(f"plan_mode: astrbot.core.tools.fs_access 不可用: {exc}")
        return None


class PlanModeController:
    """plan(readonly) 模式的过滤钩子 + reminder 注入,状态在核心。"""

    def __init__(
        self,
        get_config: Callable[[], dict],
        get_core_config: Callable[[], dict],
    ) -> None:
        self._get_config = get_config
        self._get_core_config = get_core_config
        self._plan_reminded: dict[str, bool] = {}

    # ── 状态查询 ─────────────────────────────────────

    def _default_mode(self):
        fs_access = _load_fs_access()
        if fs_access is None:
            return None
        return fs_access.resolve_default(self._get_core_config() or {})

    def is_active(self, umo: str | None) -> bool:
        fs_access = _load_fs_access()
        if fs_access is None or not umo:
            return False
        default = self._default_mode()
        return (
            fs_access.get_mode_for_umo(umo, default=default)
            is fs_access.FileAccessMode.READONLY
        )

    def count_active(self) -> int:
        fs_access = _load_fs_access()
        if fs_access is None:
            return 0
        return fs_access.count_readonly_overrides()

    def has_reminded(self, umo: str) -> bool:
        return bool(self._plan_reminded.get(umo, False))

    # ── 状态变更(由 /plan /build 命令调用) ──────────────────

    def activate(self, umo: str) -> None:
        fs_access = _load_fs_access()
        if fs_access is None:
            return
        fs_access.set_mode_for_umo(umo, fs_access.FileAccessMode.READONLY)
        self._plan_reminded.pop(umo, None)

    def deactivate(self, umo: str) -> bool:
        fs_access = _load_fs_access()
        if fs_access is None:
            return False
        was_active = self.is_active(umo)
        fs_access.restore_writable_for_umo(umo, default=self._default_mode())
        self._plan_reminded.pop(umo, None)
        return was_active

    # ── 钩子主入口(由 _plan_filter_tools 装饰方法调用) ──────────

    def filter_request(self, event, req: "ProviderRequest") -> None:
        """readonly 时过滤写工具 + 注入 reminder;否则 no-op。"""
        umo = event.unified_msg_origin
        if not self.is_active(umo):
            # 离开 readonly 时重置 reminder,下次进入重新注入。
            self._plan_reminded.pop(umo, None)
            return
        if not req.func_tool:
            return

        blocked_tools = self._get_config().get("plan_mode_blocked_tools") or []
        if blocked_tools:
            removed_count = self._filter_func_tool(req, set(blocked_tools))
            if removed_count > 0:
                logger.debug(
                    f"[plan] 会话 {umo}: 从工具列表过滤 {removed_count} 个写工具"
                )
        else:
            logger.warning(
                f"[plan] 会话 {umo}: 处于 plan 模式但 plan_mode_blocked_tools 为空,"
                f"将不会过滤任何工具。请在 _conf_schema.json 配置。"
            )

        if self._plan_reminded.get(umo, False):
            return

        reminder_template = (self._get_config().get("plan_mode_reminder") or "").strip()
        if not reminder_template:
            self._plan_reminded[umo] = True
            return

        blocked_str = (
            ", ".join(sorted(set(blocked_tools))) if blocked_tools else "(none)"
        )
        reminder_text = reminder_template.replace("{blocked}", blocked_str)
        if not reminder_text.lstrip().startswith("<system-reminder>"):
            reminder_text = f"<system-reminder>\n{reminder_text}\n</system-reminder>"

        if isinstance(req.contexts, list) and req.contexts:
            for msg in reversed(req.contexts):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        msg["content"] = content + "\n\n" + reminder_text
                    break

        self._plan_reminded[umo] = True
        logger.debug(f"[plan] 会话 {umo}: 已注入 plan 模式 reminder 到 user message")

    @staticmethod
    def _filter_func_tool(req: "ProviderRequest", blocked: set[str]) -> int:
        """从 req.func_tool 中过滤掉 blocked 集合里的工具名,返回被过滤的数量。

        新建 ToolSet 替换原引用,避免 in-place 修改污染共享 list;
        被过滤工具的 schema 不序列化 — LLM 看不到也调不到。
        """
        if not req.func_tool or not blocked:
            return 0
        kept = [t for t in req.func_tool.tools if t.name not in blocked]
        actual_removed = len(req.func_tool.tools) - len(kept)
        if actual_removed == 0:
            return 0
        try:
            from astrbot.core.agent.tool import ToolSet

            new_set: "ToolSet" = ToolSet()
            for t in kept:
                new_set.add_tool(t)
            req.func_tool = new_set
            return actual_removed
        except Exception as exc:
            logger.warning(f"spcode_toolkit 工具过滤失败: {exc}")
            return 0
