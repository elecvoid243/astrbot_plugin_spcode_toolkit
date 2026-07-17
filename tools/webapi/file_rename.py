"""POST /spcode/file-rename — 重命名 repo 文件(同目录,任意文本文件)。

作者: elecvoid243, 2026-07-18

设计约束(与 PATCH /spcode/docs 对齐,但去掉 .md 限制):
  - 仅支持**同目录**重命名(body 传 ``path`` + ``new_name``,不允许
    跨目录移动 — 前端编辑工具栏只需要改名,移动留给未来显式需求);
  - ``new_name`` 是纯文件名: 非空、不含 ``/`` ``\\``、不含换行/NUL、
    不为 ``.`` / ``..``、长度 ≤ 255;
  - 路径安全: ``path`` 与拼接后的目标路径都过
    ``_validate_repo_relative_file`` 4 步防御;
  - 目标已存在 → file_exists(不覆盖);源不存在/不是文件 → file_not_found;
  - 纯文件系统 ``os.replace``,git 后续自行识别 rename(与 docs 一致)。

统一 envelope 由 ``_helpers._make_envelope`` 生成。
"""

from __future__ import annotations

import os
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING, Any

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


def _validate_new_name(new_name: Any) -> str | None:
    """校验 new_name 为合法纯文件名;通过返回 None,否则返回 reason 码。"""
    if not isinstance(new_name, str) or not new_name.strip():
        return ReasonCode.INVALID_PARAM
    if new_name != new_name.strip():
        return ReasonCode.INVALID_PARAM
    if "/" in new_name or "\\" in new_name or "\n" in new_name or "\x00" in new_name:
        return ReasonCode.PATH_UNSAFE
    if new_name in (".", ".."):
        return ReasonCode.PATH_UNSAFE
    if len(new_name) > 255:
        return ReasonCode.INVALID_PARAM
    return None


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/file-rename handler — 同目录重命名文本文件。"""
    t0 = _time.time()
    if body is None or not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    path = body.get("path", "")
    new_name = body.get("new_name", "")
    if not isinstance(path, str) or not path.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
        )
    name_err = _validate_new_name(new_name)
    if name_err is not None:
        return _make_envelope(
            success=False,
            reason=name_err,
            elapsed_ms=_elapsed(t0),
            path=path,
        )

    # 同目录拼接(POSIX 分隔符,与 _validate_repo_relative_file 归一化一致)。
    norm_path = path.replace("\\", "/")
    if "/" in norm_path:
        parent_rel, old_name = norm_path.rsplit("/", 1)
        new_rel = f"{parent_rel}/{new_name}"
    else:
        old_name = norm_path
        new_rel = new_name
    if new_name == old_name:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            path=path,
            stderr="new_name equals current name",
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

    old_target, err1 = _validate_repo_relative_file(path, Path(directory))
    new_target, err2 = _validate_repo_relative_file(new_rel, Path(directory))
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

    if not old_target.is_file():
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
    if new_target.exists():
        return _make_envelope(
            success=False,
            reason=ReasonCode.FILE_EXISTS,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=new_rel,
        )

    os.replace(old_target, new_target)
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        renamed=True,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        path=norm_path,
        new_path=new_rel,
    )
