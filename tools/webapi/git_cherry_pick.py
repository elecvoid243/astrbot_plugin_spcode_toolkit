"""POST /spcode/git-cherry-pick — cherry-pick a single commit onto current HEAD.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §4
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .._helpers import _is_commit_ref
from ._helpers import (
    ReasonCode,
    _classify_cherry_pick_stderr,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _run_git_async,
)

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
    """POST /spcode/git-cherry-pick handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            picked=False,
            ref="",
        )

    ref = body.get("ref")
    mainline = body.get("mainline")

    if not isinstance(ref, str) or not ref.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            picked=False,
            ref="",
        )
    ref = ref.strip()

    if mainline is not None:
        if not isinstance(mainline, int) or isinstance(mainline, bool) or mainline < 1:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                picked=False,
                ref=ref,
                stderr="mainline must be a positive integer or null",
            )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("picked", False)
        err["data"].setdefault("ref", ref)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. conflict state pre-check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(),
            picked=False,
            ref=ref,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"operation already in progress: {operation}",
        )

    # ── 4. commit ref validation ──
    if not _is_commit_ref(git_bin, directory, ref):
        return _make_envelope(
            success=False,
            reason=ReasonCode.COMMIT_NOT_FOUND,
            elapsed_ms=_elapsed(),
            picked=False,
            ref=ref,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"ref does not resolve to a commit: {ref}",
        )

    # ── 5. worktree dirty check ──
    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
        timeout=5.0,
    )
    if status_result.get("ok") and status_result["stdout"].strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.WORKTREE_DIRTY,
            elapsed_ms=_elapsed(),
            picked=False,
            ref=ref,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr="working tree has uncommitted changes",
        )

    # ── 6. execute git cherry-pick ──
    args: list[str] = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "cherry-pick",
        "--no-edit",
    ]
    if mainline is not None:
        args.extend(["-m", str(mainline)])
    args.append(ref)

    result = await _run_git_async(args, encoding="utf-8", timeout=30.0)

    # ── 7. classify + readback ──
    if result["ok"]:
        sha_result = await _run_git_async(
            [git_bin, "-C", directory, "rev-parse", "HEAD"],
            encoding="utf-8",
            timeout=5.0,
        )
        new_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""
        msg_result = await _run_git_async(
            [git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"],
            encoding="utf-8",
            timeout=5.0,
        )
        original_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""
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

        logger.info(
            "git-cherry-pick: %s → %s (umo=%s)", ref, new_sha[:12], effective_umo
        )
        return _JSONResponseCompat(
            _make_envelope(
                success=True,
                elapsed_ms=_elapsed(),
                picked=True,
                ref=ref,
                new_sha=new_sha,
                original_message=original_message,
                files_touched=files_touched,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            ),
            status_code=200,
        )

    stderr = result.get("stderr", "")
    reason = _classify_cherry_pick_stderr(stderr)
    conflicted_files = []
    if reason == ReasonCode.CHERRY_PICK_CONFLICT:
        conflicted_files = await _list_conflicted_files(git_bin, directory)

    logger.info("git-cherry-pick: failed %s (%s): %s", ref, reason, stderr[:200])
    return _make_envelope(
        success=False,
        reason=reason,
        elapsed_ms=_elapsed(),
        picked=False,
        ref=ref,
        conflict=(reason == ReasonCode.CHERRY_PICK_CONFLICT),
        operation="cherry_pick",
        conflicted_files=conflicted_files,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        stderr=stderr[:4096],
    )
