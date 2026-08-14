"""POST /spcode/git-push — push the current branch and auto-set upstream.

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
    _is_everything_up_to_date,
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
    """POST /spcode/git-push handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(
        reason: str,
        **fields: object,
    ) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            pushed=False,
            **fields,
        )

    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    explicit_remote = body.get("remote")
    explicit_branch = body.get("branch")
    explicit_remote_branch = body.get("remote_branch")
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
    if explicit_remote_branch is not None and not _is_valid_ref_name(
        explicit_remote_branch
    ):
        return _failure(
            ReasonCode.INVALID_BRANCH,
            remote=str(explicit_remote or ""),
            branch=str(explicit_branch or ""),
        )

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("pushed", False)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _failure(
            ReasonCode.OPERATION_IN_PROGRESS,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    current_branch = await _read_current_branch(git_bin, directory)
    if current_branch is None:
        return _failure(
            ReasonCode.DETACHED_HEAD,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    upstream = await _read_upstream(git_bin, directory)
    base_args = [git_bin, "-C", directory, "-c", "color.ui=never"]

    if upstream and explicit_remote is None and explicit_branch is None:
        remote, branch = _parse_upstream(upstream)
        push_args = base_args + ["push"]
        set_upstream = False
    else:
        remote = (
            explicit_remote
            if explicit_remote is not None
            else (_parse_upstream(upstream)[0] if upstream else "origin")
        )
        branch = explicit_branch or current_branch
        assert isinstance(remote, str)
        assert isinstance(branch, str)

        exists, _, probe_error = await _get_remote_url(git_bin, directory, remote)
        if not exists:
            return _failure(
                ReasonCode.REMOTE_NOT_FOUND,
                remote=remote,
                branch=branch,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=probe_error[:4096],
            )

        if explicit_branch is not None:
            ref_result = await _run_git_async(
                [
                    git_bin,
                    "-C",
                    directory,
                    "rev-parse",
                    "--verify",
                    f"{branch}^{{commit}}",
                ],
                encoding="utf-8",
                timeout=5.0,
            )
            if not ref_result.get("ok"):
                return _failure(
                    ReasonCode.BRANCH_NOT_FOUND,
                    remote=remote,
                    branch=branch,
                    directory=directory,
                    umo=effective_umo,
                    worktree=directory,
                    stderr=ref_result.get("stderr", "")[:4096],
                )

        push_args = base_args + ["push"]
        set_upstream = upstream is None
        if set_upstream:
            push_args.append("-u")
        # remote_branch 显式指定且与本地分支不同名时，用 refspec `local:remote`
        # 推送到远端不同名分支；否则保持 `git push <remote> <branch>`（同名）。
        if explicit_remote_branch is not None and explicit_remote_branch != branch:
            push_args.extend([remote, f"{branch}:{explicit_remote_branch}"])
        else:
            push_args.extend([remote, branch])

    result = await _run_git_async(
        push_args,
        encoding="utf-8",
        env=_build_remote_git_env(),
        timeout=REMOTE_TIMEOUT_SECONDS,
    )
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "") or result.get("error", "")

    if not result.get("ok"):
        reason = _classify_remote_error(stderr, stdout)
        if "src refspec" in stderr.lower() or "does not match any" in stderr.lower():
            reason = ReasonCode.BRANCH_NOT_FOUND
        return _failure(
            reason,
            remote=remote,
            branch=branch,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:4096],
        )

    pushed = not _is_everything_up_to_date(stdout, stderr)
    local_sha = await _read_head_sha(git_bin, directory)
    new_upstream = await _read_upstream(git_bin, directory)
    actual_remote_branch = explicit_remote_branch or branch
    remote_branch_label = f"{remote}/{actual_remote_branch}"

    logger.info(
        "git-push: %s -> %s (pushed=%s, set_upstream=%s, umo=%s)",
        branch,
        remote_branch_label,
        pushed,
        set_upstream,
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            pushed=pushed,
            set_upstream=set_upstream,
            remote=remote,
            branch=branch,
            remote_branch=remote_branch_label,
            local_sha=local_sha,
            upstream=new_upstream,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
