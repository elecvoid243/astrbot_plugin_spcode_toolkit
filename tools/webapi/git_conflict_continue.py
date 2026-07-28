"""POST /spcode/git-conflict-continue — complete merge/cherry-pick/revert after resolution.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §7
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .git_commit import _build_git_env, _classify_commit_error
from ._helpers import (
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _read_post_mutation_branch_state,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 8192

_CONTINUE_CMD = {
    "merge": None,  # merge uses git commit
    "cherry_pick": ["cherry-pick", "--continue", "--no-edit"],
    "revert": ["revert", "--continue", "--no-edit"],
}


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-conflict-continue handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        body = {}
    message = body.get("message", "")
    if not isinstance(message, str) or len(message) > MAX_MESSAGE_LENGTH:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            continued=False,
        )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("continued", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. conflict state check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.NO_CONFLICT_IN_PROGRESS,
            elapsed_ms=_elapsed(),
            continued=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 4. unmerged check ──
    remaining = await _list_conflicted_files(git_bin, directory)
    if remaining:
        return _make_envelope(
            success=False,
            reason=ReasonCode.UNRESOLVED_CONFLICTS_REMAIN,
            elapsed_ms=_elapsed(),
            continued=False,
            operation=operation,
            remaining_conflicts=remaining,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 5. execute continue ──
    git_env = _build_git_env()
    if operation == "merge":
        args = [git_bin, "-C", directory, "-c", "color.ui=never", "commit", "--no-edit"]
        input_text = None
        if message:
            args = [git_bin, "-C", directory, "-c", "color.ui=never", "commit", "-F", "-"]
            input_text = message
    else:
        cmd_parts = _CONTINUE_CMD[operation]
        args = [git_bin, "-C", directory, "-c", "color.ui=never"] + cmd_parts
        input_text = None

    result = await _run_git_async(
        args, encoding="utf-8", input_text=input_text, env=git_env, timeout=30.0
    )

    if not result["ok"]:
        stderr = result.get("stderr", "")
        reason = _classify_commit_error(stderr, result.get("code", -1))
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            continued=False,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:4096],
        )

    # ── 6. readback ──
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"], encoding="utf-8", timeout=5.0
    )
    commit_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""
    msg_result = await _run_git_async(
        [git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    commit_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""
    files_result = await _run_git_async(
        [git_bin, "-C", directory, "show", "--name-only", "--pretty=", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    files_touched = (
        [f for f in files_result.get("stdout", "").splitlines() if f.strip()]
        if files_result.get("ok")
        else []
    )
    post_state = await _read_post_mutation_branch_state(git_bin, directory)

    logger.info(
        "git-conflict-continue: %s → %s (umo=%s)",
        operation,
        commit_sha[:12],
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            continued=True,
            operation=operation,
            commit_sha=commit_sha,
            commit_message=commit_message,
            files_touched=files_touched,
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
