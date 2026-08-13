"""POST /spcode/git-commit-amend — 修改当前 HEAD 提交信息。

Spec: docs/superpowers/specs/2026-08-13-git-commit-amend-design.md
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _detect_conflict_operation,
    _get_staged_files,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)
from .git_commit import (
    COMMIT_TRUNCATE_BYTES,
    MAX_MESSAGE_LENGTH,
    _build_git_env,
    _classify_commit_error,
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
    """POST /spcode/git-commit-amend handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            amended=False,
            **fields,
        )

    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    message = body.get("message")
    if not isinstance(message, str):
        return _failure(ReasonCode.INVALID_MESSAGE)
    if len(message) == 0 or len(message) > MAX_MESSAGE_LENGTH:
        return _failure(ReasonCode.INVALID_MESSAGE)

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("amended", False)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 1. 空仓库检查 ──
    head_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not head_result.get("ok"):
        stderr = head_result.get("stderr", "") or head_result.get("error", "")
        if "needed a single revision" in stderr.lower() or "ambiguous" in stderr.lower():
            return _failure(
                ReasonCode.EMPTY_REPOSITORY,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=stderr[:COMMIT_TRUNCATE_BYTES],
            )
        return _failure(
            ReasonCode.GIT_ERROR,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:COMMIT_TRUNCATE_BYTES],
        )

    # ── 2. 冲突操作进行中 ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _failure(
            ReasonCode.OPERATION_IN_PROGRESS,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 3. merge commit 检查 ──
    parents_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-list", "--parents", "-n", "1", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    if parents_result.get("ok"):
        parent_fields = parents_result.get("stdout", "").split()
        if len(parent_fields) > 2:
            return _failure(
                ReasonCode.CANNOT_AMEND_MERGE_COMMIT,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            )

    # ── 4. staged 改动检查 ──
    staged = await _get_staged_files(git_bin, directory)
    if staged:
        return _failure(
            ReasonCode.STAGED_CHANGES_PRESENT,
            staged_files=staged,
            staged_count=len(staged),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 5. 记录 before SHA 并执行 amend ──
    before_sha = head_result.get("stdout", "").strip()
    args = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "commit",
        "--amend",
        "-F",
        "-",
    ]
    result = await _run_git_async(
        args,
        encoding="utf-8",
        input_text=message,
        env=_build_git_env(),
        timeout=30.0,
    )

    if not result.get("ok"):
        stderr = result.get("stderr", "") or result.get("error", "")
        reason = _classify_commit_error(stderr, result.get("code", -1))
        if reason == ReasonCode.GIT_ERROR:
            reason = ReasonCode.AMEND_FAILED
        return _failure(
            reason,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:COMMIT_TRUNCATE_BYTES],
        )

    # ── 6. 回读 after SHA / subject / full message ──
    after_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    after_sha = after_result.get("stdout", "").strip() if after_result.get("ok") else ""

    subject_result = await _run_git_async(
        [git_bin, "-C", directory, "log", "-1", "--pretty=%s"],
        encoding="utf-8",
        timeout=5.0,
    )
    subject = subject_result.get("stdout", "").strip()

    message_result = await _run_git_async(
        [git_bin, "-C", directory, "log", "-1", "--pretty=%B"],
        encoding="utf-8",
        timeout=5.0,
    )
    full_message = (
        message_result.get("stdout", "").rstrip("\n")
        if message_result.get("ok")
        else message
    )

    logger.info(
        "git-commit-amend: %s -> %s (umo=%s)",
        before_sha[:12],
        after_sha[:12],
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            amended=True,
            before_sha=before_sha,
            after_sha=after_sha,
            subject=subject,
            message=full_message,
            files_changed=[],
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
