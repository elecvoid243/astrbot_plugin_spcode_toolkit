"""GET /spcode/operation-progress — 查询静默操作实时进度(2026-08-06)。

Author: elecvoid243 @ 2026-08-06

dashboard 在发出静默 POST(project-load / project-unload / codegraph-set)
后以 500ms 间隔轮询本端点,直到 status 进入 done/failed。
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

from ..operation_progress import query as _progress_query
from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(
    plugin: SPCodeToolkit,  # noqa: ARG001  # 进度存储是模块级,无需 plugin
    *,
    umo: str | None = None,
) -> dict:
    """GET /spcode/operation-progress handler。

    Query params:
        umo (required): 会话 umo。

    Returns:
        无记录: ``data{status: "idle"}``
        有记录: ``data{operation, status, current_step, messages,
        started_at, finished_at, reason}``
    """
    t0 = _time.time()
    if not umo:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=int((_time.time() - t0) * 1000),
            status="idle",
        )
    rec = _progress_query(umo)
    if rec is None:
        return _make_envelope(
            success=True,
            elapsed_ms=int((_time.time() - t0) * 1000),
            status="idle",
        )
    return _make_envelope(
        success=True,
        elapsed_ms=int((_time.time() - t0) * 1000),
        **rec,
    )
