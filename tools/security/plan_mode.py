"""plan_mode 控制器 — 只读(readonly)文件访问模式的插件侧禁用层。

v3.x (2026-08-20): 状态由 AstrBot 核心 ``astrbot.core.tools.fs_access``
持有(per-umo)。本控制器只做两件事:
1. 派生:plan active := 核心模式 == READONLY
2. 禁用:active 时把 plan_mode_blocked_tools 写入 req.denied_tools(工具
   schema 保持可见,执行被核心 agent runner 拒绝并返回可感知错误,
   prefix-cache 友好) + 首轮注入 reminder

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
    """plan(readonly) 模式的工具禁用钩子 + reminder 注入,状态在核心。"""

    def __init__(
        self,
        get_config: Callable[[], dict],
        get_core_config: Callable[[str | None], dict],
    ) -> None:
        self._get_config = get_config
        self._get_core_config = get_core_config
        self._plan_reminded: dict[str, bool] = {}

    # ── 状态查询 ─────────────────────────────────────

    def _default_mode(self, umo: str | None):
        fs_access = _load_fs_access()
        if fs_access is None:
            return None
        return fs_access.resolve_default(self._get_core_config(umo) or {})

    def is_active(self, umo: str | None) -> bool:
        fs_access = _load_fs_access()
        if fs_access is None or not umo:
            return False
        default = self._default_mode(umo)
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
        fs_access.restore_writable_for_umo(umo, default=self._default_mode(umo))
        self._plan_reminded.pop(umo, None)
        return was_active

    # ── 钩子主入口(由 _plan_filter_tools 装饰方法调用) ──────────

    def filter_request(self, event, req: "ProviderRequest") -> None:
        """readonly 时禁用写工具的执行 + 注入 reminder;否则 no-op。"""
        umo = event.unified_msg_origin
        if not self.is_active(umo):
            # 离开 readonly 时重置 reminder,下次进入重新注入。
            self._plan_reminded.pop(umo, None)
            return
        if not req.func_tool:
            return

        blocked_tools = self._get_config().get("plan_mode_blocked_tools") or []
        if blocked_tools:
            # 执行期拒绝而非移除 schema:工具段在 /plan <-> /build 切换前后
            # 保持字节不变,provider 侧前缀缓存不受模式切换影响;被拒调用会
            # 收到 Permission denied 的工具结果,模型能感知并改走只读路径。
            req.denied_tools = set(req.denied_tools or ()) | set(blocked_tools)
            logger.debug(
                f"[plan] 会话 {umo}: 已禁止 {len(blocked_tools)} 个写工具的执行调用"
            )
        else:
            logger.warning(
                f"[plan] 会话 {umo}: 处于 plan 模式但 plan_mode_blocked_tools 为空,"
                f"将不会禁用任何工具。请在 _conf_schema.json 配置。"
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
