"""POST /spcode/code-check — manual single-file syntax/style check.

Spec: docs/superpowers/specs/2026-08-12-code-check-format-webapi-design.md
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ..code_check import check as _check_source_file
from ._code_tools import (
    _resolve_code_target,
    _run_code_tool,
    _tool_details,
    _tool_failure,
    _validate_code_path,
)
from ._helpers import (
    ReasonCode,
    _JSONResponseCompat,
    _make_envelope,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_LINTERS = frozenset({"auto", "ruff", "cpplint", "cppcheck"})


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/code-check handler."""
    t0 = _time.time()

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(t0),
            checked=False,
            fixed=False,
            **fields,
        )

    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    path = body.get("path", "")
    path_reason = _validate_code_path(path)
    if path_reason is not None:
        return _failure(path_reason)
    assert isinstance(path, str)
    path = path.strip()

    linter = body.get("linter", "auto")
    if not isinstance(linter, str) or linter not in _LINTERS:
        return _failure(ReasonCode.INVALID_PARAM)

    fix = body.get("fix", False)
    if type(fix) is not bool:
        return _failure(ReasonCode.INVALID_BODY)

    err, ctx, target = await _resolve_code_target(
        plugin,
        path=path,
        umo=umo,
        worktree=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("checked", False)
        return err
    assert ctx is not None and target is not None
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    cfg = plugin._config
    raw_enable = cfg.get("cppcheck_enable")
    cppcheck_enable = raw_enable if isinstance(raw_enable, (list, tuple)) else []
    raw_mode = cfg.get("cppcheck_shortcircuit")
    cppcheck_shortcircuit = raw_mode if isinstance(raw_mode, str) else None

    result = await _run_code_tool(
        _check_source_file,
        str(target),
        linter,
        fix=fix,
        cppcheck_enable=list(cppcheck_enable),
        cppcheck_shortcircuit=cppcheck_shortcircuit,
    )

    if not result.get("ok"):
        reason, error = _tool_failure(result, ReasonCode.CHECK_FAILED)
        return _failure(
            reason,
            path=path,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=error[:4096],
            details=_tool_details(result),
        )

    issues = result.get("issues", [])
    count = result.get("count")
    linters = result.get("linters")
    if count is None and isinstance(linters, dict):
        count = sum(block.get("count", 0) for block in linters.values())
    count = int(count or 0)

    logger.info(
        "[code-check] %s linter=%s issues=%d (umo=%s)",
        path,
        result.get("linter", linter),
        count,
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(t0),
            checked=True,
            path=path,
            linter=result.get("linter", linter),
            issues=issues,
            count=count,
            linters=linters,
            fixed=bool(result.get("fixed", False)),
            fixed_count=result.get("fixed_count"),
            proposal=result.get("proposal"),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
