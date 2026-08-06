# tools/operation_progress.py
"""Per-umo 静默操作进度存储(内存级,2026-08-06 引入)。

供 webapi 静默端点(project-load / project-unload / codegraph-set)在执行
流水线时写入实时进度,``GET /spcode/operation-progress`` 读取并供 dashboard
轮询渲染 chip loading 态。

设计要点:
- 模块级 dict 按 umo 键控,每个 umo 只保留**最近一次**操作的记录。
- ``begin`` 拒绝并发(同 umo 已有 running 记录时返回 False)。
- ``finish`` 只从 running 转换(幂等),端点层的异常兜底 finish 不会
  覆盖 silent 方法已写入的终态。
- finished 记录保留 ``_FINISHED_TTL_SECONDS``(300s)供前端最后一次轮询
  拿终态;``begin`` / ``query`` 时惰性清理,不引入后台线程。
- ``current_step`` 取最后一条以 ⏳/🔄 开头的消息的首行——与
  ``project_load_step`` 的步骤文案约定耦合,若流水线改前缀需同步修改
  ``_STEP_PREFIXES``。
"""

from __future__ import annotations

import time

_FINISHED_TTL_SECONDS = 300.0
_STEP_PREFIXES = ("⏳", "🔄")

_records: dict[str, dict] = {}


def _purge_expired(now: float) -> None:
    """惰性删除已过 TTL 的 finished 记录(running 记录永不清理)。"""
    expired = [
        umo
        for umo, rec in _records.items()
        if rec["finished_at"] is not None
        and now - rec["finished_at"] > _FINISHED_TTL_SECONDS
    ]
    for umo in expired:
        del _records[umo]


def begin(umo: str, operation: str) -> bool:
    """开始一个操作。同 umo 有 running 记录时返回 False(拒绝并发)。"""
    now = time.time()
    _purge_expired(now)
    existing = _records.get(umo)
    if existing is not None and existing["status"] == "running":
        return False
    _records[umo] = {
        "operation": operation,
        "status": "running",
        "messages": [],
        "current_step": "",
        "started_at": now,
        "finished_at": None,
        "reason": None,
    }
    return True


def append(umo: str, message: str) -> None:
    """追加一条子步骤消息;⏳/🔄 开头的消息更新 current_step(取首行)。"""
    rec = _records.get(umo)
    if rec is None:
        return
    rec["messages"].append(message)
    if message.startswith(_STEP_PREFIXES):
        rec["current_step"] = message.splitlines()[0]


def finish(umo: str, ok: bool, reason: str | None = None) -> None:
    """写入终态。仅从 running 转换,重复调用不覆盖已有终态。"""
    rec = _records.get(umo)
    if rec is None or rec["status"] != "running":
        return
    rec["status"] = "done" if ok else "failed"
    rec["finished_at"] = time.time()
    rec["reason"] = reason


def query(umo: str) -> dict | None:
    """返回记录副本(messages 深拷贝一层);无记录返回 None。"""
    _purge_expired(time.time())
    rec = _records.get(umo)
    if rec is None:
        return None
    return {**rec, "messages": list(rec["messages"])}


class ProgressList(list):
    """list 子类:append 时自动镜像到进度存储。

    用途:silent 流水线里大量既有 ``messages.append(...)`` 调用点无需逐个
    修改,把 ``messages`` 初始化为 ``ProgressList(umo)`` 即可自动上报进度。
    """

    def __init__(self, umo: str) -> None:
        super().__init__()
        self._umo = umo

    def append(self, item: object) -> None:
        super().append(item)
        append(self._umo, str(item))
