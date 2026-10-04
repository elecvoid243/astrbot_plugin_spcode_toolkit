"""POST /spcode/file-remove/restore — 从系统回收站恢复被删除的文件。

配套 LLM 工具 ``astrbot_file_remove``(删除走 send2trash → 系统回收站)。
ChatUI 回合文件变更总结卡片的"撤销删除"按钮调本端点把最新一条匹配
记录搬回原位。

不要求已加载项目:删除工具作用于任意可写绝对路径,恢复亦然;原始路径
以回收站元数据($I / .trashinfo)为准,入参 path 仅用于匹配选择。

旧核心回退路径:新核心(AstrBot 主仓库内置 astrbot_file_remove)由 dashboard
``POST /chat/file-changes/restore-removed`` 接管恢复,本端点保留供旧核心使用。

恢复核心在 ``tools.file_remove_restore.restore_from_recycle_bin``。
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

from .._helpers import run_sync
from ..file_remove_restore import restore_from_recycle_bin
from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:  # pragma: no cover
    from main import SPCodeToolkit


def _elapsed(t0: float) -> int:
    """端到端耗时(毫秒),与 file_remove 一致。"""
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: "SPCodeToolkit",
    *,
    body: dict | None = None,
    **_kwargs,
) -> dict:
    """POST /spcode/file-remove/restore handler — 恢复回收站中的文件。"""
    t0 = _time.time()
    if body is None or not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    path = body.get("path", "")
    if not isinstance(path, str) or not path.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
        )

    result = await run_sync(restore_from_recycle_bin, path.strip())
    if not result.get("ok"):
        return _make_envelope(
            success=False,
            reason="restore_failed",
            stderr=str(result.get("error", "")),
            elapsed_ms=_elapsed(t0),
            path=path,
        )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        path=path,
        restored_path=str(result.get("restored_path", "")),
    )
