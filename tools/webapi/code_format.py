"""POST /spcode/code-format — manual single-file formatting.

Spec: docs/superpowers/specs/2026-08-12-code-check-format-webapi-design.md
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ..code_format import format as _format_source_file
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


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


def _plugin_format_style(plugin: SPCodeToolkit) -> str:
    return str(plugin._config.get("default_style") or "allman")


def _plugin_format_indent(plugin: SPCodeToolkit) -> int:
    try:
        return int(plugin._config.get("default_indent") or 4)
    except (TypeError, ValueError):
        return 4


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/code-format handler."""
    t0 = _time.time()

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(t0),
            formatted=False,
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

    check = body.get("check", False)
    if type(check) is not bool:
        return _failure(ReasonCode.INVALID_BODY)

    err, ctx, target = await _resolve_code_target(
        plugin,
        path=path,
        umo=umo,
        worktree=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("formatted", False)
        return err
    assert ctx is not None and target is not None
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    result = await _run_code_tool(
        _format_source_file,
        str(target),
        "auto",
        check=check,
        style=_plugin_format_style(plugin),
        indent=_plugin_format_indent(plugin),
    )

    if not result.get("ok"):
        reason, error = _tool_failure(result, ReasonCode.FORMAT_FAILED)
        return _failure(
            reason,
            path=path,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=error[:4096],
            details=_tool_details(result),
        )

    changed = bool(result.get("changed"))
    logger.info(
        "[code-format] %s formatter=%s check=%s changed=%s (umo=%s)",
        path,
        result.get("formatter", ""),
        check,
        changed,
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(t0),
            formatted=not check,
            path=path,
            formatter=result.get("formatter"),
            formatter_options=result.get("formatter_options"),
            check=check,
            changed=changed,
            file_size_before=result.get("file_size_before"),
            file_size_after=result.get("file_size_after"),
            diff_summary=result.get("diff_summary"),
            proposal=result.get("proposal"),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
