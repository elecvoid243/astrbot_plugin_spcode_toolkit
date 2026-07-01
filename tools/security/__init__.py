"""security 子包 — L1 鉴权(auth) + plan 模式(/plan /build 命令) 控制器。

PR-3 (2026-06-23) 从 main.py 提取:
- admin.py: 管理员校验 (build_allowed_ids + check_is_admin)
- plan_mode.py: PlanModeController (per-umo 状态 + 工具过滤 + reminder 注入)

AstrBot 约束: @filter.command* 和 @filter.on_llm_request() 必须挂在
Star 子类上,所以装饰器方法仍保留在 main.py 中,业务逻辑全部委托给
本子包。
"""

from __future__ import annotations

from .admin import build_allowed_ids, check_is_admin
from .plan_mode import PlanModeController

__all__ = [
    "PlanModeController",
    "build_allowed_ids",
    "check_is_admin",
]
