"""Web API handlers for the terminal page (``/spcode/terminal/*``).

SSE: the stream endpoint returns a ``StreamingResponse``; AstrBot's
``_coerce_view_result`` passes any Starlette/FastAPI ``Response`` through
unchanged, so no envelope wrapping happens on the stream path.

Author: elecvoid243 · 2026-09-01
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from starlette.responses import StreamingResponse

if TYPE_CHECKING:
    from main import SPCodeToolkit

    from ..terminal.component import TerminalSessionManager

from ..terminal import runtime as _runtime

_SSE_MEDIA_TYPE = "text/event-stream"
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "Pragma": "no-cache",
}


def _request_query(key: str) -> str:
    """Read a query param from the current plugin web request.

    Late import of ``astrbot.api.web``: the plugin runtime must be
    initialised first (same rationale as ``file_browser.py`` — a module
    level import would break tests that mock ``web.request`` early.
    """
    import astrbot.api.web as _web

    return _web.request.query.get(key) or ""


def _manager() -> TerminalSessionManager:
    """Resolve the module-level terminal manager singleton."""
    manager = _runtime.component
    if manager is None:
        raise RuntimeError("Terminal manager is not initialized")
    return manager


def _sse_event(event_type: str, data: Any) -> str:
    """Serialize one SSE ``data:`` frame (compact JSON)."""
    payload = json.dumps(
        {"type": event_type, "data": data},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"data: {payload}\n\n"


async def handle_start(
    plugin: SPCodeToolkit,
    umo: str | None,
    body: dict | None,
) -> dict:
    """POST /spcode/terminal/start — start (or replace) the owner session."""
    if not umo:
        return {
            "status": "ok",
            "data": {"error": "missing_umo", "reason": "umo is required"},
        }
    body = body or {}
    shell = body.get("shell") or "powershell"
    cwd = (body.get("cwd") or "").strip()
    if shell not in ("powershell", "cmd"):
        return {
            "status": "ok",
            "data": {
                "error": "start_failed",
                "reason": f"unsupported shell: {shell}",
            },
        }
    if not cwd:
        return {
            "status": "ok",
            "data": {"error": "missing_cwd", "reason": "cwd is required"},
        }
    try:
        data = await _manager().start(owner_id=umo, shell=shell, cwd=cwd)
    except ValueError as exc:
        return {
            "status": "ok",
            "data": {"error": "start_failed", "reason": str(exc)},
        }
    except RuntimeError as exc:
        return {
            "status": "ok",
            "data": {"error": "manager_unavailable", "reason": str(exc)},
        }
    return {"status": "ok", "data": data}


async def handle_stream(
    plugin: SPCodeToolkit,
    umo: str | None,
) -> StreamingResponse:
    """GET /spcode/terminal/stream — SSE output stream from a cursor.

    Query params are read synchronously here — the response generator
    runs after the request context ends, so nothing request-bound may
    be touched inside it.
    """
    if not umo:
        return _error_stream("missing umo")
    session_id = _request_query("session_id").strip()
    cursor_raw = _request_query("cursor") or "0"
    if not session_id:
        return _error_stream("missing session_id")
    try:
        cursor = max(0, int(cursor_raw))
    except ValueError:
        cursor = 0

    manager = _manager()

    async def _gen() -> Any:
        current = cursor
        while True:
            try:
                result = await manager.poll(
                    owner_id=umo,
                    session_id=session_id,
                    cursor=current,
                    yield_time_ms=1_000,
                    max_output_chars=65_536,
                )
            except ValueError:
                yield _sse_event("error", "session not found")
                return
            current = result["cursor"]
            if result["stdout"]:
                yield _sse_event("output", result["stdout"])
            if result["status"] != "running":
                yield _sse_event(
                    "exit",
                    {"code": result["exit_code"], "status": result["status"]},
                )
                return
            yield ": hb\n\n"  # heartbeat: keep proxies from closing

    return StreamingResponse(
        _gen(), media_type=_SSE_MEDIA_TYPE, headers=_SSE_HEADERS
    )


def _error_stream(message: str) -> StreamingResponse:
    """Return a one-shot SSE stream carrying an error event."""

    async def _gen() -> Any:
        yield _sse_event("error", message)

    return StreamingResponse(
        _gen(), media_type=_SSE_MEDIA_TYPE, headers=_SSE_HEADERS
    )


async def handle_input(
    plugin: SPCodeToolkit,
    umo: str | None,
    body: dict | None,
) -> dict:
    """POST /spcode/terminal/input — write chars to the session stdin."""
    body = body or {}
    session_id = str(body.get("session_id") or "").strip()
    chars = body.get("chars")
    if not umo:
        return {
            "status": "ok",
            "data": {"error": "missing_umo", "reason": "umo is required"},
        }
    if not session_id:
        return {
            "status": "ok",
            "data": {
                "error": "missing_session_id",
                "reason": "session_id is required",
            },
        }
    if not isinstance(chars, str) or not chars:
        return {
            "status": "ok",
            "data": {"error": "missing_chars", "reason": "chars is required"},
        }
    try:
        data = await _manager().write(
            owner_id=umo, session_id=session_id, chars=chars
        )
    except ValueError as exc:
        return {
            "status": "ok",
            "data": {"error": "session_not_found", "reason": str(exc)},
        }
    except RuntimeError as exc:
        return {
            "status": "ok",
            "data": {"error": "manager_unavailable", "reason": str(exc)},
        }
    return {"status": "ok", "data": data}


async def handle_interrupt(
    plugin: SPCodeToolkit,
    umo: str | None,
    body: dict | None,
) -> dict:
    """POST /spcode/terminal/interrupt — Ctrl+C semantics."""
    body = body or {}
    session_id = str(body.get("session_id") or "").strip()
    if not umo:
        return {
            "status": "ok",
            "data": {"error": "missing_umo", "reason": "umo is required"},
        }
    if not session_id:
        return {
            "status": "ok",
            "data": {
                "error": "missing_session_id",
                "reason": "session_id is required",
            },
        }
    try:
        data = await _manager().interrupt(owner_id=umo, session_id=session_id)
    except ValueError as exc:
        return {
            "status": "ok",
            "data": {"error": "session_not_found", "reason": str(exc)},
        }
    except RuntimeError as exc:
        return {
            "status": "ok",
            "data": {"error": "manager_unavailable", "reason": str(exc)},
        }
    return {"status": "ok", "data": data}


async def handle_stop(
    plugin: SPCodeToolkit,
    umo: str | None,
    body: dict | None,
) -> dict:
    """POST /spcode/terminal/stop — terminate the whole process tree."""
    body = body or {}
    session_id = str(body.get("session_id") or "").strip()
    if not umo:
        return {
            "status": "ok",
            "data": {"error": "missing_umo", "reason": "umo is required"},
        }
    if not session_id:
        return {
            "status": "ok",
            "data": {
                "error": "missing_session_id",
                "reason": "session_id is required",
            },
        }
    try:
        data = await _manager().terminate(owner_id=umo, session_id=session_id)
    except ValueError as exc:
        return {
            "status": "ok",
            "data": {"error": "session_not_found", "reason": str(exc)},
        }
    except RuntimeError as exc:
        return {
            "status": "ok",
            "data": {"error": "manager_unavailable", "reason": str(exc)},
        }
    return {"status": "ok", "data": data}


async def handle_status(
    plugin: SPCodeToolkit,
    umo: str | None,
) -> dict:
    """GET /spcode/terminal/status — snapshot + cursor (reconnect helper).

    Without ``session_id`` it returns the owner's live session if any;
    ``advance=False`` keeps the stream consumer's cursor untouched.
    """
    if not umo:
        return {
            "status": "ok",
            "data": {"error": "missing_umo", "reason": "umo is required"},
        }
    manager = _manager()
    session_id = _request_query("session_id").strip()
    try:
        if session_id:
            data = await manager.poll(
                owner_id=umo,
                session_id=session_id,
                cursor=0,
                yield_time_ms=0,
                max_output_chars=200_000,
                advance=False,
            )
        else:
            info = await manager.owner_session(umo)
            if info is None:
                return {
                    "status": "ok",
                    "data": {"error": "no_session", "reason": "no session"},
                }
            data = await manager.poll(
                owner_id=umo,
                session_id=info["session_id"],
                cursor=0,
                yield_time_ms=0,
                max_output_chars=200_000,
                advance=False,
            )
        return {"status": "ok", "data": data}
    except ValueError as exc:
        return {
            "status": "ok",
            "data": {"error": "session_not_found", "reason": str(exc)},
        }
