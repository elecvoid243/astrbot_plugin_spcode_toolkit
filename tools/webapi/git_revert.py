"""POST /spcode/git-revert — 创建回滚 commit。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.6
v2.17.0 (2026-07-16) PR-G.

复用 ``tools/webapi/git_commit.py``:
- ``_build_git_env()``:透传 ``GIT_AUTHOR_*`` 环境变量
- ``_classify_commit_error()``:4 类 hook/identity/nothing/git_error 分类

``_classify_revert_stderr()`` 内部先调 commit classifier,未命中再走
revert 专属 3 类(conflict / nothing_to_revert / dirty)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _classify_revert_stderr,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .git_commit import _build_git_env
from .._helpers import _is_commit_ref

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

COMMIT_TRUNCATE_BYTES = 4096


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-revert handler.

    Body (JSON): ``{"ref": "HEAD", "no_edit": True}``。

    SPEC §3.6: ``no_edit`` 必须为 ``True``(headless 服务不开编辑器)。
    想要自定义 message 的路径是 revert 完再 ``git commit --amend``,不在本端点。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), reverted=False, ref="",
        )

    ref = body.get("ref", "HEAD")
    no_edit = body.get("no_edit", True)

    if not isinstance(ref, str) or not ref:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), reverted=False, ref=str(ref or ""),
        )
    if no_edit is not True:
        # v2.17.0 强制 no_edit=true(headless 服务不开编辑器)
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            stderr="no_edit must be true (headless service, no editor)",
        )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("reverted", False)
        err["data"].setdefault("ref", ref)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 校验 ref 是 commit ──
    if not _is_commit_ref(git_bin, directory, ref):
        return _make_envelope(
            success=False, reason=ReasonCode.COMMIT_NOT_FOUND,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"ref does not resolve to a commit: {ref}",
        )

    # ── 4. 探测 worktree 状态(必须 clean) ──
    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
    )
    if not status_result.get("ok"):
        stderr_msg = status_result.get("stderr", "") or status_result.get("error", "")
        if "does not have any commits" in stderr_msg or "no commits" in stderr_msg:
            return _make_envelope(
                success=False, reason=ReasonCode.EMPTY_REPOSITORY,
                elapsed_ms=_elapsed(), reverted=False, ref=ref,
                directory=directory, umo=effective_umo, worktree=directory,
                stderr=stderr_msg,
            )
        return _make_envelope(
            success=False, reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=stderr_msg,
        )
    if status_result["stdout"].strip():
        return _make_envelope(
            success=False, reason=ReasonCode.WORKTREE_DIRTY,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr="working tree has uncommitted changes",
        )

    # ── 5. git revert --no-edit ──
    args = [
        git_bin, "-C", directory, "-c", "color.ui=never",
        "revert", "--no-edit", ref,
    ]
    git_env = _build_git_env()
    result = await _run_git_async(
        args, encoding="utf-8", input_text="", env=git_env, timeout=30.0,
    )

    if not result["ok"]:
        stderr = result.get("stderr", "") or result.get("error", "")
        reason = _classify_revert_stderr(stderr)
        logger.info(
            "git-revert: failed ref=%s (%s): %s",
            ref, reason, stderr[:200],
        )
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=stderr[:COMMIT_TRUNCATE_BYTES],
        )

    # ── 6. 回读新 commit SHA + touched files ──
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
    )
    revert_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""

    msg_result = await _run_git_async(
        [git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"],
        encoding="utf-8",
    )
    revert_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""

    files_result = await _run_git_async(
        [git_bin, "-C", directory, "show", "--name-only", "--pretty=", "HEAD"],
        encoding="utf-8",
    )
    files_touched = [
        line.strip()
        for line in (files_result.get("stdout", "")).splitlines()
        if line.strip()
    ] if files_result.get("ok") else []

    logger.info(
        "git-revert: %s → %s (touched %d files, umo=%s)",
        ref, revert_sha[:12], len(files_touched), effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            reverted=True, ref=ref, revert_sha=revert_sha,
            revert_message=revert_message, files_touched=files_touched,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
