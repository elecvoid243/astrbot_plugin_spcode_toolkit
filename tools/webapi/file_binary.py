# Author: elecvoid243, 2026-07-22
# Spec: docs/superpowers/specs/2026-07-22-binary-preview-design.md §4
"""GET /spcode/file-binary — stream raw file bytes for browser-side rendering.

Returns the raw bytes of a whitelisted file (PDF / DOCX / XLSX / CSV / MD)
with a precise Content-Type so the browser (or in-tab library like
pdfjs / mammoth / exceljs / papaparse) can render it directly.

Rejecting non-whitelisted extensions is intentional — the endpoint is
*not* a generic file-download proxy, it is the read pipe for
<BinaryPreview> in the dashboard.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from starlette.responses import Response as _RawResponse

from ._helpers import (
    FILE_BINARY_MAX_BYTES,
    MIME_BY_EXT,
    ReasonCode,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async_bytes,
    _validate_repo_relative_file,
)

# Success responses use raw bytes via Starlette ``Response``;
# error envelopes use ``_JSONResponseCompat`` (dict-like for tests).
ResponseLike = _RawResponse | _JSONResponseCompat

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _get_path_param() -> str:
    """Read the ?path= query param from the current web request."""
    from astrbot.api import web

    return (web.request.query.get("path", "") or "").strip()


def _get_ref_param() -> str | None:
    """Read the ?ref= query param; None when absent."""
    from astrbot.api import web

    raw = (web.request.query.get("ref", "") or "").strip()
    return raw or None


def _get_if_none_match() -> str:
    from astrbot.api import web

    return web.request.headers.get("If-None-Match", "") or ""


def _build_error(
    status: int,
    reason: str,
    path: str,
    elapsed_ms: int = 0,
    **fields: object,
) -> _JSONResponseCompat:
    envelope = _make_envelope(
        success=False,
        reason=reason,
        elapsed_ms=elapsed_ms,
        path=path,
        **fields,
    )
    return _JSONResponseCompat(
        envelope,
        status_code=status,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def _build_304(etag: str) -> _RawResponse:
    """304 Not Modified with cache headers; empty body."""
    return _RawResponse(
        b"",
        status_code=304,
        headers=_common_cache_headers(etag),
    )


def _build_binary_response(
    data: bytes, mime: str, etag: str, filename: str
) -> _RawResponse:
    """200 with raw bytes + Content-Type / Content-Disposition / ETag."""
    return _RawResponse(
        data,
        status_code=200,
        headers={
            "Content-Type": mime,
            "Content-Disposition": _content_disposition(filename),
            "Content-Length": str(len(data)),
            **_common_cache_headers(etag),
        },
    )


def _classify_oserror(exc: OSError) -> tuple[int, str]:
    """Map OSError → (http_status, reason)."""
    if isinstance(exc, PermissionError) or exc.errno == 13:
        return 403, ReasonCode.PERMISSION_DENIED
    return 404, ReasonCode.PATH_NOT_FOUND


def _compute_binary_etag(st: os.stat_result) -> str:
    """Build weak ETag from mtime_ns + size; spec §4.6."""
    return f'W/"{st.st_mtime_ns}-{st.st_size}"'


def _content_disposition(name: str) -> str:
    """RFC 5987 encoded filename for unicode-safe Content-Disposition."""
    safe_ascii = name.encode("ascii", "replace").decode("ascii")
    utf8 = quote(name, safe="")
    return f"inline; filename=\"{safe_ascii}\"; filename*=UTF-8''{utf8}"


def _common_cache_headers(etag: str) -> dict[str, str]:
    return {
        "ETag": etag,
        "Cache-Control": "private, must-revalidate",
        "Vary": "Cookie",
    }


async def _resolve_preflight(
    plugin: SPCodeToolkit,
    umo: str | None,
    worktree_param: str | None,
) -> tuple[dict | None, dict | None]:
    """Wrap _git_endpoint_preflight (always returns envelope, not Response)."""
    return await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree_param,
    )


async def _serve_workspace_path(
    plugin: SPCodeToolkit,
    rel_path: str,
    umo: str | None,
) -> ResponseLike:
    """Read a workspace file and stream its bytes (workspace-path branch)."""
    err, ctx = await _resolve_preflight(plugin, umo, None)
    if err is not None:
        return _JSONResponseCompat(
            err, headers={"Content-Type": "application/json; charset=utf-8"}
        )
    directory = ctx["directory"]

    target, path_err = _validate_repo_relative_file(rel_path, Path(directory))
    if path_err is not None:
        return _build_error(400, ReasonCode.PATH_UNSAFE, rel_path)

    try:
        file_st = target.lstat()
    except (PermissionError, OSError) as exc:
        status, reason = _classify_oserror(exc)
        return _build_error(status, reason, rel_path)

    if stat.S_ISLNK(file_st.st_mode) or not stat.S_ISREG(file_st.st_mode):
        return _build_error(415, ReasonCode.SPECIAL_FILE, rel_path)
    if file_st.st_size > FILE_BINARY_MAX_BYTES:
        return _build_error(413, ReasonCode.FILE_TOO_LARGE, rel_path)

    etag = _compute_binary_etag(file_st)
    if _get_if_none_match() == etag:
        return _build_304(etag)

    try:
        with target.open("rb") as f:
            data = f.read()
    except OSError as exc:
        status, reason = _classify_oserror(exc)
        return _build_error(status, reason, rel_path)

    suffix = target.suffix.lower()
    return _build_binary_response(data, MIME_BY_EXT[suffix], etag, target.name)


async def _serve_ref_path(
    plugin: SPCodeToolkit,
    rel_path: str,
    ref: str,
    umo: str | None,
) -> ResponseLike:
    """Read a blob at a given git ref (ref-path branch).

    Note: the git ref is consumed via ``git show <ref>:<path>`` *after*
    preflight; it is NOT a worktree path. We pass ``None`` as the
    worktree_param so preflight stays scoped to the loaded project root.
    """
    err, ctx = await _resolve_preflight(plugin, umo, None)
    if err is not None:
        return _JSONResponseCompat(
            err, headers={"Content-Type": "application/json; charset=utf-8"}
        )
    directory = ctx["directory"]

    target, path_err = _validate_repo_relative_file(rel_path, Path(directory))
    if path_err is not None:
        return _build_error(400, ReasonCode.PATH_UNSAFE, rel_path)

    git_bin = plugin._git_binary()  # type: ignore[attr-defined]
    result = await _run_git_async_bytes(
        [git_bin, "-C", directory, "-c", "color.ui=never", "show", f"{ref}:{rel_path}"],
        timeout=15.0,
    )
    if not result["ok"]:
        stderr = (result.get("stderr") or "").lower()
        error = (result.get("error") or "").lower()
        combined = stderr + " " + error
        if (
            "invalid reference" in combined
            or "unknown revision" in combined
            or "not a valid ref" in combined
            or "bad revision" in combined
            or "bad object" in combined
            or "not a commit" in combined
            or "invalid object name" in combined
            or "ambiguous" in combined
        ):
            return _build_error(404, ReasonCode.REF_NOT_FOUND, rel_path)
        if (
            "exists on disk, but not in" in combined
            or "does not exist in" in combined
            or "path not in" in combined
            or "does not exist" in combined
        ):
            return _build_error(422, ReasonCode.FILE_MISSING_AT_REF, rel_path)
        return _build_error(500, ReasonCode.GIT_ERROR, rel_path)

    data: bytes = result["stdout"]
    if len(data) > FILE_BINARY_MAX_BYTES:
        return _build_error(413, ReasonCode.FILE_TOO_LARGE, rel_path)

    # ETag: use worktree file mtime+size so 304 short-circuits when workspace
    # edits happen. Fallback to content hash when worktree file is absent.
    try:
        wt_st = target.lstat()
        etag = _compute_binary_etag(wt_st)
    except OSError:
        etag = 'W/"' + hashlib.sha1(data).hexdigest()[:16] + '"'

    if _get_if_none_match() == etag:
        return _build_304(etag)

    suffix = Path(rel_path).suffix.lower()
    return _build_binary_response(
        data,
        MIME_BY_EXT[suffix],
        etag,
        Path(rel_path).name,
    )


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> ResponseLike:
    """GET /spcode/file-binary — main entry point.

    Query params:
      - ``path``: repo-relative file path (required).
      - ``ref``: git ref; when absent, serve from workspace.

    Returns:
        Binary bytes for whitelisted extensions; JSON error envelope
        otherwise.
    """
    # ``worktree`` parameter from _wrap is actually the git ref in our case
    # — we use the dedicated ?ref= query param to disambiguate.
    del worktree  # unused; ref is read directly from query below

    rel_path = _get_path_param()
    if not rel_path:
        return _build_error(400, ReasonCode.PATH_UNSAFE, "")
    # Reject obvious path-traversal before checking extension, so that
    # ``../../etc/passwd`` is reported as PATH_UNSAFE rather than
    # UNSUPPORTED_MEDIA_TYPE (its suffix ``.passwd`` is not whitelisted
    # but the security failure should dominate the response reason).
    if ".." in rel_path.replace("\\", "/").split("/"):
        return _build_error(400, ReasonCode.PATH_UNSAFE, rel_path)
    suffix = Path(rel_path).suffix.lower()
    if suffix not in MIME_BY_EXT:
        return _build_error(415, ReasonCode.UNSUPPORTED_MEDIA_TYPE, rel_path)
    ref = _get_ref_param()
    if ref is None:
        return await _serve_workspace_path(plugin, rel_path, umo)
    return await _serve_ref_path(plugin, rel_path, ref, umo)
