"""/project 加载状态(模块级 dict,以 umo 为 key,PR-7 2026-06-23)。

每个 entry 形如:
    {
        "directory": str,    # 项目绝对路径
        "loaded_at": float,  # 加载时间戳(time.time())
    }

设计意图:从 main.py 的 self._loaded_projects 改为模块级单例。
- plugin 单例本身,模块级与实例级在功能上等价(都是进程内唯一)
- 子系统代码不应回写 main.py 实例属性 — 模块级更解耦
- 与 tools.codegraph.state / tools.inta_shell.runtime 一致
"""
from __future__ import annotations

_loaded_projects: dict[str, dict] = {}


def get(umo: str) -> dict | None:
    """取指定 umo 的已加载项目信息(没有则 None)。"""
    return _loaded_projects.get(umo)


def put(umo: str, info: dict) -> None:
    """登记/覆盖指定 umo 的已加载项目信息。"""
    _loaded_projects[umo] = info


def pop(umo: str) -> dict | None:
    """弹出并返回指定 umo 的已加载项目信息(没有则 None)。"""
    return _loaded_projects.pop(umo, None)


def items() -> dict[str, dict]:
    """返回所有已加载项目的浅拷贝(便于外部迭代)。"""
    return {k: dict(v) for k, v in _loaded_projects.items()}


def reset() -> None:
    """清空所有状态(供测试 fixture)。"""
    _loaded_projects.clear()


def __contains__(umo: str) -> bool:
    return umo in _loaded_projects


# 同时支持 ``umo in state``(模块本身) 和 ``umo in state._loaded_projects``(显式)
# 两种 in 检查形式。Python 自动走模块 __contains__ → 委托给 ``in _loaded_projects``。
