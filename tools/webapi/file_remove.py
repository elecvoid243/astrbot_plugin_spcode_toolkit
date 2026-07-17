"""POST /spcode/file-remove — 删除 repo 文件(任意文本文件)。

作者: elecvoid243, 2026-07-18

设计约束(与 DELETE /spcode/docs 对齐,但去掉 .md 限制):
  - 仅删除**文件**(目录一律拒绝);
  - 路径过 ``_validate_repo_relative_file`` 4 步防御(绝对路径 / ``..``
    / 越出 repo / ``.git`` 内部 / 符号链接逃逸全部拒绝);
  - 直接 ``unlink``(不进回收站 — 与 docs DELETE 一致;误删防护
    由前端确认对话框承担,git 未提交文件除外,已跟踪文件可 git 恢复)。

统一 envelope 由 ``_helpers._make_envelope`` 生成。
"""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _make_envelope,
    _validate_repo_relative_file,
)

if TYPE_CHECKING:  # pragma: no cover
    from main import SPCodeToolkit


def _elapsed(t0: float) -> int:
    """端到端耗时(毫秒),与 docs_crud 一致。"""
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/file-remove handler — 删除 repo 内单个文件。"""
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
    if not target.is_file():
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
