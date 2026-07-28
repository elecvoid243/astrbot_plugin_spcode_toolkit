"""POST /spcode/git-merge — merge a ref into current HEAD.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §3
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .._helpers import _is_valid_ref_name
from ._helpers import (
    ReasonCode,
    _classify_merge_stderr,
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


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-merge handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            merged=False,
            source="",
        )

    source = body.get("source")
    message = body.get("message", "")
    no_ff = bool(body.get("no_ff", False))
    ff_only = bool(body.get("ff_only", False))
    squash = bool(body.get("squash", False))

    if not isinstance(source, str) or not source.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            merged=False,
            source="",
        )
    source = source.strip()

    if not isinstance(message, str) or len(message) > MAX_MESSAGE_LENGTH:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            merged=False,
            source=source,
        )

    # Cross-field mutual exclusion
    if (no_ff and ff_only) or (squash and no_ff) or (squash and ff_only):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            merged=False,
            source=source,
            stderr="no_ff/ff_only/squash mutual exclusion violated",
        )

    # ── 2. ref-format validation ──
    if not _is_valid_ref_name(source):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BRANCH,
            elapsed_ms=_elapsed(),
            merged=False,
            source=source,
        )

    # ── 3. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("merged", False)
        err["data"].setdefault("source", source)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 4. conflict state pre-check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(),
            merged=False,
            source=source,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"operation already in progress: {operation}",
        )

    # ── 5. worktree dirty check (squash exempt) ──
    if not squash:
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
                merged=False,
                source=source,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr="working tree has uncommitted changes",
            )

    # ── 6. execute git merge ──
    args: list[str] = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "merge",
        "--no-edit",
    ]
    if no_ff:
        args.append("--no-ff")
    if ff_only:
        args.append("--ff-only")
    if squash:
        args.append("--squash")
    if message:
        args.extend(["-m", message])
    args.append(source)

    result = await _run_git_async(args, encoding="utf-8", timeout=30.0)

    # ── 7. classify result ──
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    if result["ok"]:
        # Check "Already up to date" (returncode=0 but no merge happened)
        if "already up to date" in stdout.lower() or "already up-to-date" in stdout.lower():
            return _make_envelope(
                success=False,
                reason=ReasonCode.MERGE_ALREADY_UP_TO_DATE,
                elapsed_ms=_elapsed(),
                merged=False,
                source=source,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            )
        # Squash: staged but not committed
        if squash:
            files_result = await _run_git_async(
                [git_bin, "-C", directory, "diff", "--cached", "--name-only"],
                encoding="utf-8",
                timeout=5.0,
            )
            files_touched = (
                [f for f in files_result.get("stdout", "").splitlines() if f.strip()]
                if files_result.get("ok")
                else []
            )
            return _JSONResponseCompat(
                _make_envelope(
                    success=True,
                    elapsed_ms=_elapsed(),
                    merged=False,
                    source=source,
                    squash=True,
                    files_touched=files_touched,
                    directory=directory,
                    umo=effective_umo,
                    worktree=directory,
                ),
                status_code=200,
            )
        # Normal success (fast-forward or merge commit)
        fast_forward = "fast-forward" in stdout.lower()
        sha_result = await _run_git_async(
            [git_bin, "-C", directory, "rev-parse", "HEAD"],
            encoding="utf-8",
            timeout=5.0,
        )
        merge_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""
        msg_result = await _run_git_async(
            [git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"],
            encoding="utf-8",
            timeout=5.0,
        )
        merge_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""
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
            "git-merge: %s → %s (ff=%s, umo=%s)",
            source,
            merge_sha[:12],
            fast_forward,
            effective_umo,
        )
        return _JSONResponseCompat(
            _make_envelope(
                success=True,
                elapsed_ms=_elapsed(),
                merged=True,
                source=source,
                merge_sha=merge_sha,
                merge_message=merge_message,
                fast_forward=fast_forward,
                squash=False,
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

    # Failure path
    reason = _classify_merge_stderr(stderr, stdout)
    conflicted_files = []
    if reason == ReasonCode.MERGE_CONFLICT:
        conflicted_files = await _list_conflicted_files(git_bin, directory)

    logger.info("git-merge: failed %s (%s): %s", source, reason, stderr[:200])
    return _make_envelope(
        success=False,
        reason=reason,
        elapsed_ms=_elapsed(),
        merged=False,
        source=source,
        conflict=(reason == ReasonCode.MERGE_CONFLICT),
        operation="merge",
        conflicted_files=conflicted_files,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        stderr=stderr[:4096],
    )
