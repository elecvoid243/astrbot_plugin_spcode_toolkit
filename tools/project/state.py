"""/project 加载状态(模块级 dict,以 umo 为 key,PR-7 2026-06-23)。

每个 entry 形如:
    {
        "directory": str,             # 项目绝对路径
        "loaded_at": float,           # 加载时间戳(time.time())
        "skipped_substeps": set[str], # (2026-07-25) 用户显式 opt-out 跳过的
                                       #   子步骤名集合。可选,默认空 set。
                                       #   当前可能值:
                                       #     - "agentsmd"  → /project load 时带 no_agentsmd
                                       #     - "codegraph"  → /project load 时带 no_codegraph
                                       #   用途: 让 on_llm_request 钩子能据此决定
                                       #   是否注入对应子系统的提示文本,避免
                                       #   LLM 拿到「请用 codegraph_*」但工具
                                       #   实际未启用的不一致。
    }

设计意图:从 main.py 的 self._loaded_projects 改为模块级单例。
- plugin 单例本身,模块级与实例级在功能上等价(都是进程内唯一)
- 子系统代码不应回写 main.py 实例属性 — 模块级更解耦
- 与 tools.codegraph.state / tools.inta_shell.runtime 一致

向后兼容:
- 旧调用方只读 ``directory`` / ``loaded_at`` 仍正常。
- 旧状态(无 ``skipped_substeps`` 字段)在钩子里用
  ``.get("skipped_substeps", set())`` 兜底,行为退化为「不跳过任何子步骤」。
- 旧 test 若对 state 做严格字段断言,需补 ``skipped_substeps=set()``
  字段(PR-7 之前未引入 set 概念,本字段 2026-07-25 才有)。
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
