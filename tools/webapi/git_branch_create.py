"""POST /spcode/git-branch-create — 从当前 HEAD 拉新分支。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.3
v2.17.0 (2026-07-16) PR-D.
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .._helpers import _is_valid_ref_name

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-branch-create handler.

    Body (JSON, 必传): ``{"name": "feature/x", "start_point": "main", "force": false}``。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), created=False, name="",
        )

    name = body.get("name")
    start_point = body.get("start_point", "HEAD")
    force = bool(body.get("force", False))

    if not isinstance(name, str) or not name:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), created=False, name=str(name or ""),
        )
    if not isinstance(start_point, str):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), created=False, name=name,
        )

    # ── 2. ref-format 校验 ──
    if not _is_valid_ref_name(name):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BRANCH,
            elapsed_ms=_elapsed(), created=False, name=name,
        )
    # start_point: HEAD 是快捷别名,免校验;其他 ref 必须合法
    if start_point != "HEAD" and not _is_valid_ref_name(start_point):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), created=False, name=name,
        )

    # ── 3. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("created", False)
        err["data"].setdefault("name", name)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 4. git branch ──
    args: list[str] = [git_bin, "-C", directory, "branch"]
    if force:
        args.append("-f")
    args.append(name)
    if start_point != "HEAD":
        args.append(start_point)

    result = await _run_git_async(args, encoding="utf-8", timeout=15.0)
    if not result["ok"]:
        stderr = result.get("stderr", "").lower()
        reason = ReasonCode.GIT_ERROR
        if "already exists" in stderr:
            reason = ReasonCode.BRANCH_EXISTS
        elif "not a valid branch name" in stderr:
            reason = ReasonCode.INVALID_BRANCH
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), created=False, name=name,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    # ── 5. 回读 SHA ──
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", f"{name}^{{commit}}"],
        encoding="utf-8",
    )
    sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""

    logger.info("git-branch-create: %s (start_point=%s)", name, start_point)
    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            created=True, name=name, sha=sha,
            start_point=start_point, force=force,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
