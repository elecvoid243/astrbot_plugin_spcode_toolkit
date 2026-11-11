"""security 子包 — L1 管理员鉴权。

PR-3 (2026-06-23) 从 main.py 提取:
- admin.py: 管理员校验 (build_allowed_ids + check_is_admin)

AstrBot 约束: @filter.command* 和 @filter.on_llm_request() 必须挂在
Star 子类上,所以装饰器方法仍保留在 main.py 中,业务逻辑全部委托给
本子包。
"""

from __future__ import annotations

from .admin import build_allowed_ids, check_is_admin

__all__ = [
    "build_allowed_ids",
    "check_is_admin",
]
