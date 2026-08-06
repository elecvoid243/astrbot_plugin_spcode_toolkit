"""POST /spcode/codegraph-set — 静默切换 codegraph 默认项目(2026-08-06)。

Author: elecvoid243 @ 2026-08-06

仿照 ``project_load.py`` 的静默模式。body: ``umo``(必传)+ ``directory``(必传)。
MCP 重启最长 180s,前端通过 ``GET /spcode/operation-progress`` 轮询进度。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ..operation_progress import begin as _progress_begin
from ..operation_progress import finish as _progress_finish
from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_SILENT_REASON_TO_ENVELOPE: dict[str, str] = {
    "path_invalid": ReasonCode.PATH_UNSAFE,
    "mcp_restart_failed": ReasonCode.GIT_ERROR,
}


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,  # noqa: ARG001
    body: dict | None = None,
) -> dict:
    """POST /spcode/codegraph-set handler — 静默切换 codegraph 默认项目。"""
    t0 = _time.time()
    if not isinstance(body, dict) or not umo:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
            set=False,
        )
    directory = body.get("directory")
    if not isinstance(directory, str) or not directory.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
        )
    if not _progress_begin(umo, "codegraph_set"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
        )

    from unittest.mock import MagicMock

    silent_event = MagicMock()
    silent_event.unified_msg_origin = umo
    silent_event.plain_result = lambda text: text  # noqa: ARG005

    try:
        result = await plugin.codegraph.set_project_silent(
            silent_event, directory.strip()
        )
    except Exception as exc:
        logger.exception("codegraph-set: 未捕获异常 (umo=%s)", umo)
        _progress_finish(umo, ok=False, reason="internal_error")
        return _make_envelope(
            success=False,
            reason=ReasonCode.INTERNAL_ERROR,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
            substep_messages=[str(exc)],
        )

    if not result.get("ok"):
        silent_reason = result.get("reason") or ""
        envelope_reason = _SILENT_REASON_TO_ENVELOPE.get(
            silent_reason, ReasonCode.GIT_ERROR
        )
        logger.info("codegraph-set: 失败 (umo=%s, reason=%s)", umo, silent_reason)
        return _make_envelope(
            success=False,
            reason=envelope_reason,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
            directory=result.get("directory", ""),
            substep_messages=result.get("substep_messages", []),
            mcp_restarted=result.get("mcp_restarted", False),
            silent_reason=silent_reason,
        )

    logger.info(
        "codegraph-set: 成功 (umo=%s, dir=%s)", umo, result.get("directory", "")
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        set=True,
        directory=result.get("directory", ""),
        umo=umo,
        substep_messages=result.get("substep_messages", []),
        mcp_restarted=result.get("mcp_restarted", False),
    )
