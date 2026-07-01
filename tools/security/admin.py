"""auth helpers — L1 管理员校验(全局 AstrBot 管理员 + 用户配置额外 ID)。"""

from __future__ import annotations


def build_allowed_ids(config: dict) -> set[str]:
    """从配置 + AstrBot 全局管理员构建允许列表。

    配置键 ``allowed_ids`` 接受逗号分隔字符串(支持中英文逗号)。
    返回: 仅包含配置额外 ID 的集合 —— AstrBot 全局管理员由调用方另行
    合并(本函数纯函数,不依赖 context)。

    历史: 原 main.py._build_allowed_ids(context, config) — 2026-06-23
    PR-3 提取时把 context 参数去掉(从未被使用),改为纯函数。
    """
    allowed: set[str] = set()
    extra = config.get("allowed_ids", "")
    if extra:
        for x in extra.replace("，", ",").split(","):
            x = x.strip()
            if x:
                allowed.add(x)
    return allowed


def check_is_admin(event) -> bool:
    """检查 event 对应用户是否为 AstrBot 全局管理员。

    防御性: 老版本 AstrBot 可能没有 ``is_admin()`` 方法或抛异常,
    此时视为非管理员(spcode_toolkit 默认拒绝)。
    """
    try:
        return bool(event.is_admin())
    except Exception:
        return False
