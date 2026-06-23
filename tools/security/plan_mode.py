"""plan_mode 控制器 — /plan /build 命令的底层状态 + 过滤 + reminder 注入。

历史(2026-06-23, PR-3 提取):
- 原 main.py 的 self._plan_mode / self._plan_reminded dicts
- self._plan_mode_active / _plan_mode_active_count helpers
- self._filter_func_tool / self._plan_filter_tools
- /plan /build 命令中的状态变更

AstrBot 装饰器约束:@filter.command* 和 @filter.on_llm_request() 必须挂在
Star 子类上,所以 /plan /build 命令和 _plan_filter_tools 钩子仍保留在 main.py,
但全部委托给本模块的 PlanModeController。

设计参考 opencode plan/build 模式:
- /plan → activate,过滤写工具,首轮注入 reminder
- /build → deactivate,完全不动 LLM 工具列表(默认行为)
- reminder 仅在 plan 模式**第一轮**注入,user message 末尾追加(不放
  system_prompt 以保护 prefix cache)
"""
from __future__ import annotations

import logging
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.agent.tool import ToolSet
    from astrbot.core.provider import ProviderRequest

logger = logging.getLogger(__name__)


class PlanModeController:
    """管理 plan 模式的 (umo → bool) 状态 + (umo → reminded) 标记 + 钩子逻辑。

    通过构造时传入 ``get_config`` 回调访问 _conf_schema.json 配置,避免
    紧耦合 main.py。
    """

    def __init__(self, get_config: Callable[[], dict]) -> None:
        self._get_config = get_config
        self._plan_mode: dict[str, bool] = {}
        self._plan_reminded: dict[str, bool] = {}

    # ── 状态查询 ─────────────────────────────────────

    def is_active(self, umo: str | None) -> bool:
        """Return whether the given umo is currently in plan mode.

        未注册 umo 或 None 视为 build 模式(返回 False)。
        """
        if not umo:
            return False
        return bool(self._plan_mode.get(umo, False))

    def count_active(self) -> int:
        """Count how many umos currently have plan mode active."""
        return sum(1 for active in self._plan_mode.values() if active)

    def has_reminded(self, umo: str) -> bool:
        return bool(self._plan_reminded.get(umo, False))

    # ── 状态变更(由 /plan /build 命令调用) ──────────────────

    def activate(self, umo: str) -> None:
        """激活 plan 模式(若已激活则保持,但重置 reminder)。

        设计: 再次 /plan 时重置 reminder,让 LLM 重新看到 plan 模式提示。
        """
        self._plan_mode[umo] = True
        self._plan_reminded.pop(umo, None)

    def deactivate(self, umo: str) -> bool:
        """退出 plan 模式。返回: 切换前是否处于 plan 模式。"""
        was_active = self._plan_mode.pop(umo, False)
        self._plan_reminded.pop(umo, None)
        return was_active

    # ── 钩子主入口(由 _plan_filter_tools 装饰方法调用) ──────────

    def filter_request(self, event, req: "ProviderRequest") -> None:
        """v2.8: /plan 模式钩子 — 从 LLM 工具列表过滤写工具 + 注入 reminder。

        build 模式(默认):不做事(no-op,零开销)。
        plan 模式:
          1. 过滤 req.func_tool 去掉 plan_mode_blocked_tools
          2. 首轮 LLM 调用时,在 user message 末尾注入 reminder
        """
        umo = event.unified_msg_origin
        if not self._plan_mode.get(umo, False):
            return  # build 模式(默认):不做事
        if not req.func_tool:
            return

        blocked_tools = self._get_config().get("plan_mode_blocked_tools") or []
        if blocked_tools:
            blocked_set = set(blocked_tools)
            removed_count = self._filter_func_tool(req, blocked_set)
            if removed_count > 0:
                logger.debug(
                    f"[plan] 会话 {umo}: 从工具列表过滤 {removed_count} 个写工具"
                )
        else:
            # plan 模式激活但没配置 blocked_tools = 配置错误,记 warning
            logger.warning(
                f"[plan] 会话 {umo} 处于 plan 模式但 plan_mode_blocked_tools 为空,"
                f"将不会过滤任何工具。请在 _conf_schema.json 配置。"
            )

        # plan 模式第一轮:在 user message 末尾追加 reminder(prefix cache 友好)
        if self._plan_reminded.get(umo, False):
            return

        reminder_template = (self._get_config().get("plan_mode_reminder") or "").strip()
        if not reminder_template:
            # 没配 reminder,标记为已注入(避免每轮检查)
            self._plan_reminded[umo] = True
            return

        # 替换 {blocked} 占位符
        blocked_str = (
            ", ".join(sorted(set(blocked_tools))) if blocked_tools else "(none)"
        )
        reminder_text = reminder_template.replace("{blocked}", blocked_str)
        if not reminder_text.lstrip().startswith("<system-reminder>"):
            reminder_text = f"<system-reminder>\n{reminder_text}\n</system-reminder>"

        # 追加到最后一条 user 消息(OpenAI 格式 dict)
        # WHY: 不放 system_prompt 是为了避免污染 prefix cache;
        #      user message 改动只影响本轮,后续轮 reminder 不再注入。
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

        设计要点(参考 opencode plan 模式):
        1. 新建 ToolSet 替换原引用,避免 in-place 修改原 list
           —— 防止共享引用污染其他 session(多 agent run 共享 func_tool 时)
        2. 被过滤的工具**完全从 LLM 工具列表消失**(schema 不序列化)
           —— LLM 看不到也调不到,比"调用时拒绝"更干净
        3. 不存在的工具名静默跳过——配置可写"计划中"的工具名
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
