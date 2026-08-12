"""POST /spcode/git-pull — pull from upstream with headless safety guards.

Spec: docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .._helpers import _is_valid_ref_name
from ._git_remote import (
    REMOTE_TIMEOUT_SECONDS,
    _build_remote_git_env,
    _classify_remote_error,
    _get_remote_url,
    _is_already_up_to_date,
    _is_valid_remote_name,
    _parse_upstream,
    _read_current_branch,
    _read_head_sha,
    _read_upstream,
)
from ._helpers import (
    ReasonCode,
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
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-pull handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            pulled=False,
            updated=False,
            **fields,
        )

    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    ff_only = body.get("ff_only", False)
    rebase = body.get("rebase", False)
    if type(ff_only) is not bool or type(rebase) is not bool or (ff_only and rebase):
        return _failure(ReasonCode.INVALID_BODY)

    explicit_remote = body.get("remote")
    explicit_branch = body.get("branch")
    if explicit_remote is not None and not _is_valid_remote_name(explicit_remote):
        return _failure(
            ReasonCode.INVALID_REMOTE,
            remote=str(explicit_remote or ""),
        )
    if explicit_branch is not None and not _is_valid_ref_name(explicit_branch):
        return _failure(
            ReasonCode.INVALID_BRANCH,
            branch=str(explicit_branch or ""),
        )

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("pulled", False)
        err["data"].setdefault("updated", False)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()
    mode = "ff_only" if ff_only else ("rebase" if rebase else "merge")

    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _failure(
            ReasonCode.OPERATION_IN_PROGRESS,
            mode=mode,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not status_result.get("ok"):
        stderr = status_result.get("stderr", "") or status_result.get("error", "")
        return _failure(
            ReasonCode.GIT_ERROR,
            mode=mode,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:4096],
        )
    if status_result.get("stdout", "").strip():
        return _failure(
            ReasonCode.WORKTREE_DIRTY,
            mode=mode,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr="working tree has uncommitted changes",
        )

    upstream = await _read_upstream(git_bin, directory)
    if explicit_remote is None:
        if upstream is None:
            return _failure(
                ReasonCode.NO_UPSTREAM,
                mode=mode,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            )
        remote, branch = _parse_upstream(upstream)
        pull_target_args: list[str] = []
    else:
        remote = explicit_remote
        current_branch = await _read_current_branch(git_bin, directory)
        branch = explicit_branch or current_branch
        if branch is None:
            return _failure(
                ReasonCode.DETACHED_HEAD,
                mode=mode,
                remote=remote,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            )
        exists, _, probe_error = await _get_remote_url(git_bin, directory, remote)
        if not exists:
            return _failure(
                ReasonCode.REMOTE_NOT_FOUND,
                mode=mode,
                remote=remote,
                branch=branch,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=probe_error[:4096],
            )
        pull_target_args = [remote, branch]

    before_sha = await _read_head_sha(git_bin, directory)
    args = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "pull",
        "--no-edit",
    ]
    if ff_only:
        args.append("--ff-only")
    elif rebase:
        args.append("--rebase")
    args.extend(pull_target_args)

    result = await _run_git_async(
        args,
        encoding="utf-8",
        env=_build_remote_git_env(),
        timeout=REMOTE_TIMEOUT_SECONDS,
    )
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "") or result.get("error", "")

    if not result.get("ok"):
        operation = await _detect_conflict_operation(git_bin, directory)
        if operation == "rebase":
            reason = ReasonCode.REBASE_CONFLICT
        elif operation == "merge":
            reason = ReasonCode.MERGE_CONFLICT
        else:
            reason = _classify_remote_error(stderr, stdout)
        conflicted_files = []
        if reason in (ReasonCode.MERGE_CONFLICT, ReasonCode.REBASE_CONFLICT):
            conflicted_files = await _list_conflicted_files(git_bin, directory)
        return _failure(
            reason,
            mode=mode,
            remote=remote,
            branch=branch,
            before_sha=before_sha,
            operation=operation,
            conflicted_files=conflicted_files,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:4096],
        )

    already = _is_already_up_to_date(stdout, stderr)
    after_sha = await _read_head_sha(git_bin, directory)
    files_touched: list[str] = []
    if before_sha and after_sha and before_sha != after_sha:
        files_result = await _run_git_async(
            [
                git_bin,
                "-C",
                directory,
                "diff",
                "--name-only",
                f"{before_sha}..{after_sha}",
            ],
            encoding="utf-8",
            timeout=5.0,
        )
        if files_result.get("ok"):
            files_touched = [
                line for line in files_result.get("stdout", "").splitlines() if line
            ]

    updated = bool(before_sha and after_sha and before_sha != after_sha)
    new_upstream = await _read_upstream(git_bin, directory)
    combined = f"{stdout} {stderr}".lower()
    fast_forward = "fast-forward" in combined

    logger.info(
        "git-pull: %s/%s (updated=%s, mode=%s, umo=%s)",
        remote,
        branch,
        updated,
        mode,
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            pulled=updated,
            updated=updated,
            mode=mode,
            remote=remote,
            branch=branch,
            before_sha=before_sha,
            after_sha=after_sha,
            fast_forward=fast_forward,
            files_touched=files_touched,
            upstream=new_upstream,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr="" if not already else stderr,
        ),
        status_code=200,
    )
