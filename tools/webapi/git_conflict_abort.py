"""POST /spcode/git-conflict-abort — abort merge/cherry-pick/revert.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §8
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _read_operation_ref,
    _read_post_mutation_branch_state,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_ABORT_CMD = {
    "merge": ["merge", "--abort"],
    "cherry_pick": ["cherry-pick", "--abort"],
    "revert": ["revert", "--abort"],
    "rebase": ["rebase", "--abort"],
}


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-conflict-abort handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("aborted", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 2. conflict state check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.NO_CONFLICT_IN_PROGRESS,
            elapsed_ms=_elapsed(),
            aborted=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 3. read operation ref before abort ──
    op_sha, _ = await _read_operation_ref(git_bin, directory, operation)

    # ── 4. execute abort ──
    cmd_parts = _ABORT_CMD[operation]
    result = await _run_git_async(
        [git_bin, "-C", directory] + cmd_parts, encoding="utf-8", timeout=15.0
    )
    if not result["ok"]:
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            aborted=False,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    # ── 5. verify sentinel gone ──
    still = await _detect_conflict_operation(git_bin, directory)
    if still is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            aborted=False,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr="abort did not clear sentinel file",
        )

    # ── 6. readback ──
    post_state = await _read_post_mutation_branch_state(git_bin, directory)

    logger.info(
        "git-conflict-abort: %s (ref=%s, umo=%s)", operation, op_sha[:12], effective_umo
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            aborted=True,
            operation=operation,
            operation_ref=op_sha,
            current=post_state["current"],
            detached=post_state["detached"],
            branches=post_state["branches"],
            total=post_state["total"],
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
