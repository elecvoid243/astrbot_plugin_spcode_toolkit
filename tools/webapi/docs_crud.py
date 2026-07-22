"""POST / PATCH / DELETE /spcode/docs — 文档 CRUD(工作区 only,无 git 操作)。

Spec: docs/superpowers/specs/2026-07-11-document-manager-backend-design.md §3.2-§3.4
"""

from __future__ import annotations

import logging
import os
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _make_envelope,
    _validate_repo_relative_file,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_PATH_LENGTH = 512
MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


def _validate_doc_path(path: object) -> str | None:
    """校验 POST/PATCH/DELETE 共用的 path 字段。

    返回 None 表示 OK;否则返回 reason 码(invalid_body / invalid_param)。

    顺序(spec §4.3):
      1. 类型:必须 str
      2. 长度:≤ 512
      3. 字符:不含换行 / NUL
      4. 必须以 .md 结尾
    """
    if not isinstance(path, str):
        return ReasonCode.INVALID_BODY
    p = path.strip()
    if not p or len(p) > MAX_PATH_LENGTH:
        return ReasonCode.INVALID_PARAM
    if "\n" in p or "\r" in p or "\x00" in p:
        return ReasonCode.INVALID_PARAM
    if not p.endswith(".md"):
        return ReasonCode.INVALID_PARAM
    return None


# ── POST ─────────────────────────────────────────────
async def handle_post_docs(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/docs handler — 创建 / 覆盖 docs 文件(upsert)。"""
    t0 = _time.time()
    if body is None or not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    path = body.get("path", "")
    content = body.get("content", "")
    if not isinstance(content, str):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    err_reason = _validate_doc_path(path)
    if err_reason is not None:
        return _make_envelope(
            success=False,
            reason=err_reason,
            elapsed_ms=_elapsed(t0),
        )

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            stderr=(f"content bytes {len(content_bytes)} > limit {MAX_CONTENT_BYTES}"),
        )

    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    target, path_err = _validate_repo_relative_file(path, Path(directory))
    if path_err is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    created = not target.exists()
    target.write_text(content, encoding="utf-8")

    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        saved=True,
        created=created,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        path=path,
        size=len(content_bytes),
    )


# ── PATCH ───────────────────────
async def handle_patch_docs(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """PATCH /spcode/docs handler — 重命名 docs 文件(纯文件系统 mv)。"""
    t0 = _time.time()
    if body is None or not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    old_path = body.get("path", "")
    new_path = body.get("new_path", "")

    for val in (old_path, new_path):
        err_reason = _validate_doc_path(val)
        if err_reason is not None:
            return _make_envelope(
                success=False,
                reason=err_reason,
                elapsed_ms=_elapsed(t0),
            )

    if old_path == new_path:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            stderr="path and new_path are equal",
        )

    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    old_target, err1 = _validate_repo_relative_file(old_path, Path(directory))
    new_target, err2 = _validate_repo_relative_file(new_path, Path(directory))
    if err1 is not None or err2 is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    if not old_target.exists():
        return _make_envelope(
            success=False,
            reason=ReasonCode.FILE_NOT_FOUND,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=old_path,
        )
    if new_target.exists():
        return _make_envelope(
            success=False,
            reason=ReasonCode.FILE_EXISTS,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=new_path,
        )

    new_target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(old_target, new_target)

    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        renamed=True,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        path=old_path,
        new_path=new_path,
    )


# ── DELETE ─────────────────────────
async def handle_delete_docs(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """DELETE /spcode/docs handler — 从工作区删除 docs 文件(直接 unlink)。"""
    t0 = _time.time()
    if body is None or not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    path = body.get("path", "")
    err_reason = _validate_doc_path(path)
    if err_reason is not None:
        return _make_envelope(
            success=False,
            reason=err_reason,
            elapsed_ms=_elapsed(t0),
        )

    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    target, path_err = _validate_repo_relative_file(path, Path(directory))
    if path_err is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    if not target.exists():
        return _make_envelope(
            success=False,
            reason=ReasonCode.FILE_NOT_FOUND,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=path,
        )

    if target.is_dir():
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"path {path} is a directory, not a file",
        )

    target.unlink()
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        deleted=True,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        path=path,
    )
