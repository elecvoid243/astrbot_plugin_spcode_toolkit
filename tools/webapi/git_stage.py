"""POST /spcode/git-stage — git add(指定文件 or all)。

Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §B
PR-3 of git workflow endpoints design.
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _get_staged_files,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    _validate_repo_relative_file,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ── 端点常量 ──
MAX_FILES_PER_REQUEST = 100
STAGE_TRUNCATE_BYTES = 1024  # git stderr 截断字节数


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-stage handler。

    Body (JSON, 必传): ``{"files": ["a.py","b.py"]}`` 或 ``{"all": true}``。
    两者互斥。

    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §B
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            staged=False,
            files=[],
            staged_count=0,
            umo=umo,
            worktree=worktree,
        )

    files = body.get("files")
    all_flag = body.get("all")

    # ── 2. files / all 互斥校验 ──
    has_files = files is not None
    has_all = all_flag is not None
    if has_files and has_all:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_FILES,
            elapsed_ms=_elapsed(),
            staged=False,
            files=[],
            staged_count=0,
            umo=umo,
            worktree=worktree,
        )
    if not has_files and not has_all:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_FILES,
            elapsed_ms=_elapsed(),
            staged=False,
            files=[],
            staged_count=0,
            umo=umo,
            worktree=worktree,
        )

    if has_files:
        # 必须是 list[str],且非空,且 ≤ 100
        if not isinstance(files, list) or len(files) == 0:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_FILES,
                elapsed_ms=_elapsed(),
                staged=False,
                files=[],
                staged_count=0,
                umo=umo,
                worktree=worktree,
            )
        if len(files) > MAX_FILES_PER_REQUEST:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_FILES,
                elapsed_ms=_elapsed(),
                staged=False,
                files=[],
                staged_count=0,
                umo=umo,
                worktree=worktree,
            )
        if not all(isinstance(f, str) for f in files):
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_FILES,
                elapsed_ms=_elapsed(),
                staged=False,
                files=[],
                staged_count=0,
                umo=umo,
                worktree=worktree,
            )
    else:
        # all 必须是 bool
        if not isinstance(all_flag, bool):
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_FILES,
                elapsed_ms=_elapsed(),
                staged=False,
                files=[],
                staged_count=0,
                umo=umo,
                worktree=worktree,
            )

    # ── 3. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("staged", False)
        err["data"].setdefault("files", [])
        err["data"].setdefault("staged_count", 0)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # ── 4. file 4 步防御(仅在 has_files 路径) ──
    if has_files:
        for f in files:
            _, path_err = _validate_repo_relative_file(f, Path(directory))
            if path_err is not None:
                return _make_envelope(
                    success=False,
                    reason=ReasonCode.PATH_UNSAFE,
                    elapsed_ms=_elapsed(),
                    staged=False,
                    files=[],
                    staged_count=0,
                    directory=directory,
                    umo=effective_umo,
                    worktree=directory,
                )

    # ── 5. git add ──
    # files 模式用 -f 强制暂存被 .gitignore 匹配的文件:用户在 UI 上逐个
    # 点击 = 显式意图,git 默认会拒绝更新"tracked-but-ignored"的文件
    # (例如 dashboard/src/assets/mdi-subset/*.css,既在 .gitignore 又在
    # index 里),必须 -f 才能让工作区的新版本进入 index。all=true 不加
    # -f:批量操作应保守,避免把未被用户注意到的 ignored 文件一并暂存。
    git_bin = plugin._git_binary()
    args: list[str]
    if has_files:
        args = [
            git_bin,
            "-C",
            directory,
            "-c",
            "color.ui=never",
            "add",
            "-f",
            "--",
        ]
        args.extend(files)
    else:
        args = [
            git_bin,
            "-C",
            directory,
            "-c",
            "color.ui=never",
            "add",
            "-A",
        ]

    result = await _run_git_async(args, encoding="utf-8")
    if not result["ok"]:
        stderr = result.get("stderr", "") or result.get("error", "")
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            staged=False,
            files=[],
            staged_count=0,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:STAGE_TRUNCATE_BYTES],
        )

    # ── 6. 列出当前 staged 文件 ──
    staged_files = await _get_staged_files(git_bin, directory)
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            staged=True,
            files=staged_files,
            staged_count=len(staged_files),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
