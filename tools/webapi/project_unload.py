"""POST /spcode/project-unload — 静默卸载项目(dashboard 调用,不在聊天框 yield)。

Author: elecvoid243 @ 2026-08-06

仿照 ``project_load.py`` 的静默模式:不调 ``unload_impl``(async generator,
yield 用户消息),改调 :meth:`ProjectManager.unload_impl_silent`(返回结构化
dict)。body 仅需 ``umo``(由 _wrap 从 JSON body 注入)。
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

# silent 层 reason → envelope reason 码
_SILENT_REASON_TO_ENVELOPE: dict[str, str] = {
    "feature_disabled": ReasonCode.FEATURE_DISABLED,
    "no_project_loaded": ReasonCode.NO_PROJECT_LOADED,
}


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,  # noqa: ARG001  # 对齐 _wrap 注入
    body: dict | None = None,  # noqa: ARG001  # umo 已被 _wrap 单独注入
) -> dict:
    """POST /spcode/project-unload handler — 静默卸载当前会话的项目。

    Returns:
        成功: ``data{unloaded: True, directory, umo, substep_messages}``
        失败: ``success=False + reason``(invalid_body / operation_in_progress /
              feature_disabled / no_project_loaded / internal_error)
    """
    t0 = _time.time()
    if not umo:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
        )
    if not _progress_begin(umo, "project_unload"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
            umo=umo,
        )

    from unittest.mock import MagicMock  # 延迟到 runtime

    silent_event = MagicMock()
    silent_event.unified_msg_origin = umo
    silent_event.plain_result = lambda text: text  # noqa: ARG005

    try:
        result = await plugin.project.unload_impl_silent(silent_event)
    except Exception as exc:
        logger.exception("project-unload: 未捕获异常 (umo=%s)", umo)
        _progress_finish(umo, ok=False, reason="internal_error")
        return _make_envelope(
            success=False,
            reason=ReasonCode.INTERNAL_ERROR,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
            umo=umo,
            substep_messages=[str(exc)],
        )

    if not result.get("ok"):
        silent_reason = result.get("reason") or ""
        envelope_reason = _SILENT_REASON_TO_ENVELOPE.get(
            silent_reason, ReasonCode.GIT_ERROR
        )
        logger.info("project-unload: 失败 (umo=%s, reason=%s)", umo, silent_reason)
        return _make_envelope(
            success=False,
            reason=envelope_reason,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
            umo=umo,
            substep_messages=result.get("substep_messages", []),
            silent_reason=silent_reason,
        )

    logger.info(
        "project-unload: 成功 (umo=%s, dir=%s)", umo, result.get("directory", "")
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        unloaded=True,
        directory=result.get("directory", ""),
        umo=umo,
        substep_messages=result.get("substep_messages", []),
    )
