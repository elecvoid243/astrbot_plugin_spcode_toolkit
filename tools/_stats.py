"""模块级工具调用统计(defaultdict + time.time())。

设计:与现有 ``_inta_component`` / ``_inta_default_cwd`` 模块级单例风格一致。
所有 FunctionTool.call() 通过 ``_record(self.name)`` 上报调用次数与时间戳,
供调试 / Dashboard 展示。

历史:
    v2.x 之前直接定义在 main.py(第 93-101 行),PR-1 (2026-06-23) 提取。
"""

from __future__ import annotations

import time
from collections import defaultdict

_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "last": 0.0})


def _record(name: str) -> None:
    """记录工具调用一次(name 必须为字符串)。

    任何内部异常都被吞掉 — 这是统计代码,不能影响主流程。
    """
    try:
        entry = _stats[name]
        entry["count"] += 1
        entry["last"] = time.time()
    except Exception:
        pass


def get_stats() -> dict:
    """返回当前统计快照(浅拷贝 dict,防止外部 mutate)。"""
    return {k: dict(v) for k, v in _stats.items()}


def reset() -> None:
    """清空统计(用于测试)。"""
    _stats.clear()
