"""GET /spcode/git-file — 给定 ref 下某文件的完整内容(blob)。

Spec: docs/superpowers/specs/2026-07-11-document-manager-backend-design.md §3.1
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    _JSONResponseCompat,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    _validate_repo_relative_file,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_FILE_BLOB_BYTES = 1 * 1024 * 1024  # 1 MB(spec §3.1)
MAX_PARAM_LENGTH = 512
HEAD_BYTES = 8000  # binary 探测窗口(spec §4.1)
_NO_STORE = {"Cache-Control": "no-store"}


def _qget(query: object, key: str, default: str | None = None) -> str | None:
    try:
        v = query.get(key)  # type: ignore[attr-defined]
        return v if v else default
    except Exception:
        return default


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-file handler。

    Query 参数:
      - ``ref``(默认 ``HEAD``):任意 git ref(commit / branch / tag / HEAD~n)
      - ``path``:仓库相对路径(必填)

    Returns:
        ``{"status": "ok", "data": {loaded, directory, umo, worktree, ref,
        resolved_sha, path, content, is_binary, size, truncated, max_bytes,
        elapsed_ms, reason, stderr}}``
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    from astrbot.api import web

    query = web.request.query if hasattr(web, "request") else {}

    ref = (_qget(query, "ref") or "HEAD").strip()
    if not ref or len(ref) > MAX_PARAM_LENGTH:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False,
            umo=umo, worktree=worktree,
        )

    target_path = _qget(query, "path")
    if not target_path or not isinstance(target_path, str):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False,
            umo=umo, worktree=worktree,
        )
    target_path = target_path.strip()
    if len(target_path) > MAX_PARAM_LENGTH:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False,
            umo=umo, worktree=worktree,
        )
    if "\n" in target_path or "\r" in target_path or "\x00" in target_path:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False,
            umo=umo, worktree=worktree,
        )

    # preflight
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # path 4 步防御
    target, path_err = _validate_repo_relative_file(target_path, Path(directory))
    if path_err is not None:
        return _make_envelope(
            success=False, reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
        )

    git_bin = plugin._git_binary()  # type: ignore[attr-defined]
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # 1. 解析 ref → sha
    resolve = await _run_git_async(
        git_prefix + ["rev-parse", f"{ref}" + "^{commit}"],
        encoding="utf-8",
    )
    if not resolve["ok"] or not resolve["stdout"]:
        stderr = (
            resolve.get("stderr", "") or resolve.get("error", "")
        ).lower()
        if (
            "bad revision" in stderr
            or "unknown revision" in stderr
            or "bad object" in stderr
            or "not a commit" in stderr
        ):
            reason = ReasonCode.REF_NOT_FOUND
        elif "does not have any commits" in stderr or "ambiguous" in stderr:
            reason = ReasonCode.EMPTY_REPOSITORY
        else:
            reason = ReasonCode.GIT_ERROR
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=resolve.get("stderr", "") or resolve.get("error", ""),
        )

    resolved_sha = resolve["stdout"]

    # 2. 读 blob
    show = await _run_git_async(
        git_prefix + ["show", f"{resolved_sha}:{target_path}"],
        encoding="utf-8",
    )
    if not show["ok"]:
        stderr_lower = (
            show.get("stderr", "") or show.get("error", "")
        ).lower()
        if (
            "exists on disk, but not in" in stderr_lower
            or "does not exist in" in stderr_lower
            or "path not in" in stderr_lower
        ):
            return _make_envelope(
                success=False, reason=ReasonCode.FILE_MISSING_AT_REF,
                elapsed_ms=_elapsed(), loaded=False,
                directory=directory, umo=effective_umo, worktree=directory,
                ref=ref, resolved_sha=resolved_sha, path=target_path,
                stderr=show.get("stderr", "") or show.get("error", ""),
            )
        if "bad revision" in stderr_lower or "bad object" in stderr_lower:
            return _make_envelope(
                success=False, reason=ReasonCode.REF_NOT_FOUND,
                elapsed_ms=_elapsed(), loaded=False,
                directory=directory, umo=effective_umo, worktree=directory,
                stderr=show.get("stderr", ""),
            )
        return _make_envelope(
            success=False, reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=show.get("stderr", "") or show.get("error", ""),
        )

    # 3. binary 判定 + 截断
    raw = show["stdout"]
    raw_bytes = raw.encode("utf-8")
    is_binary = b"\x00" in raw_bytes[:HEAD_BYTES]
    truncated = False
    content = raw
    if is_binary:
        content = ""
    elif len(raw_bytes) > MAX_FILE_BLOB_BYTES:
        truncated = True
        content = raw_bytes[:MAX_FILE_BLOB_BYTES].decode(
            "utf-8", errors="replace"
        )
    size = len(content.encode("utf-8"))

    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory, umo=effective_umo, worktree=directory,
            ref=ref, resolved_sha=resolved_sha, path=target_path,
            content=content, is_binary=is_binary, size=size,
            truncated=truncated, max_bytes=MAX_FILE_BLOB_BYTES,
        ),
        status_code=200, headers=_NO_STORE,
    )
