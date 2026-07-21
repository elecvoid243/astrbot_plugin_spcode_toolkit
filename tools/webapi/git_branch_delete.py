"""POST /spcode/git-branch-delete — 删除分支(硬禁 current branch)。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.4
v2.17.0 (2026-07-16) PR-E.

**关键不变量**:`force=true` 不绕过 current branch 检查 — 与
``git-worktree-remove`` 硬禁 main 的设计思路一致(spec §3.4 + §8)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .._helpers import _is_valid_ref_name
from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _read_post_mutation_branch_state,
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
    """POST /spcode/git-branch-delete handler.

    Body (JSON, 必传): ``{"name": "feature/x", "force": false}``。
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
            deleted=False,
            name="",
        )

    name = body.get("name")
    force = bool(body.get("force", False))

    if not isinstance(name, str) or not name:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            deleted=False,
            name=str(name or ""),
        )
    if not _is_valid_ref_name(name):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BRANCH,
            elapsed_ms=_elapsed(),
            deleted=False,
            name=name,
        )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("deleted", False)
        err["data"].setdefault("name", name)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 探测 current branch(硬禁 spec §3.4) ──
    head_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
        encoding="utf-8",
    )
    current = head_result["stdout"].strip() if head_result.get("ok") else None
    is_current = (current == name) and current not in (None, "", "HEAD")

    if is_current:
        logger.warning(
            "git-branch-delete: 试图删 current branch '%s' (umo=%s)",
            name,
            effective_umo,
        )
        return _make_envelope(
            success=False,
            reason=ReasonCode.BRANCH_IS_CURRENT,
            elapsed_ms=_elapsed(),
            deleted=False,
            name=name,
            was_current=True,
            current=current,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"cannot delete current branch '{name}'",
        )

    # ── 4. git branch -d/-D ──
    flag = "-D" if force else "-d"
    result = await _run_git_async(
        [git_bin, "-C", directory, "branch", flag, name],
        encoding="utf-8",
        timeout=15.0,
    )
    if not result["ok"]:
        stderr_lower = result.get("stderr", "").lower()
        reason = ReasonCode.GIT_ERROR
        if "not found" in stderr_lower:
            reason = ReasonCode.BRANCH_NOT_FOUND
        elif "not fully merged" in stderr_lower:
            reason = ReasonCode.BRANCH_NOT_MERGED
        elif "checked out" in stderr_lower:
            reason = ReasonCode.BRANCH_IS_CURRENT
        logger.info(
            "git-branch-delete: failed %s (%s): %s",
            name,
            reason,
            result.get("stderr", "")[:200],
        )
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            deleted=False,
            name=name,
            was_current=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    logger.info(
        "git-branch-delete: %s deleted (force=%s, umo=%s)",
        name,
        force,
        effective_umo,
    )
    # spec §3.5 L8: 回读分支状态。delete 不会切分支,current 必是 L4 读到的
    # 原 current(已通过"试图删 current branch"硬禁保护);helper 读到的
    # post_state 反映的是 for-each-ref 列表(分支少了一个 name),直接透传。
    post_state = await _read_post_mutation_branch_state(git_bin, directory)
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            deleted=True,
            name=name,
            force=force,
            was_current=False,
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
