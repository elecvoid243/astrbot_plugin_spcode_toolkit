"""POST /spcode/file-search — 在已加载项目内按内容搜索文件。

Spec: docs/superpowers/specs/2026-07-02-sidebar-search-design.md

后端实现:ripgrep 优先(plugin._rg_available=True);缺失则走纯 Python 兜底。
v2.15.0 (2026-07-02) — 初版 stub,业务实现在后续 Task 2-4 补齐。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/file-search handler.

    Stub v2.15.0 — returns feature_disabled so the route is registered
    and addressable. Real search logic lands in Tasks 2-4.
    """
    return _make_envelope(
        success=False,
        reason=ReasonCode.FEATURE_DISABLED,
        elapsed_ms=0,
        umo=umo,
        worktree=worktree,
    )
