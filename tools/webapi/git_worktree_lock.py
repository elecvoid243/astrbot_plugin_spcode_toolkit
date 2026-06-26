"""POST /spcode/git-worktree-lock — lock a git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.3
PR-D (v2.14.0, 2026-06-26).

6-layer defense chain:
  L1: body type guard (non-dict → invalid_body)
  L2: _git_endpoint_preflight (5-step)
  L3: _resolve_target_worktree (format + list lookup → path_unsafe / worktree_not_found)
  L4: already_locked (no business bypass; main 也可被 lock)
  L5: git worktree lock [--reason <text>] <path>
  L6: _list_worktrees_safe refresh on success
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
)
from .._helpers import (
    _list_worktrees_safe,
    _resolve_target_worktree,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _map_lock_stderr_to_reason(stderr: str) -> str:
    """Map `git worktree lock` stderr to ReasonCode.

    Spec §5.2 LOCK mapping table. Order matters: most specific first.

    Real git stderr formats observed:
      - "fatal: '/target' is not a working tree"     → worktree_not_found
      - "fatal: '/target' is already locked"          → already_locked
    """
    s = (stderr or "").lower()
    if "is not a working tree" in s:
        return "worktree_not_found"
    if "already locked" in s:
        return "already_locked"
    return "git_error"


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-worktree-lock handler.

    Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.3

    Body: {"path": "/abs/path/to/worktree", "reason": "optional reason (git 2.30+)"}

    Note: LOCK has **no business restriction** on main worktree (main can be
    locked; REMOVE/LOCK differ on main policy).
    """
    # ── L1: body type guard ─────────────────────────────────────────
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason="invalid_body",
            elapsed_ms=0,
            loaded=False,
            directory="",
            umo=umo,
            worktree="",
            stderr=f"body must be a dict, got {type(body).__name__}",
        )
    body = body or {}

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── L2: preflight (5-step) ──────────────────────────────────────
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── L3: format + list lookup ────────────────────────────────────
    target_wt, lookup_err = _resolve_target_worktree(
        git_bin,
        directory,
        body.get("path"),
    )
    if lookup_err == "path_unsafe":
        return _make_envelope(
            success=False,
            reason="path_unsafe",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"path validation failed: {body.get('path')!r}",
        )
    if lookup_err is not None or target_wt is None:
        return _make_envelope(
            success=False,
            reason="worktree_not_found",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"path not in worktree list: {body.get('path')!r}",
        )

    # ── L4: already_locked check (no business bypass) ───────────────
    if target_wt.get("locked"):
        locked_reason = target_wt.get("locked_reason") or "<no reason>"
        return _make_envelope(
            success=False,
            reason="already_locked",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"worktree already locked: {locked_reason}",
        )

    # ── L5: git worktree lock [--reason] <path> ─────────────────────
    reason_text = body.get("reason")
    args = [git_bin, "-C", directory, "worktree", "lock"]
    if reason_text:
        args.extend(["--reason", str(reason_text)])
    args.append(target_wt["path"])
    result = await _run_git_async(args, encoding="utf-8", timeout=10.0)
    if not result["ok"]:
        reason = _map_lock_stderr_to_reason(result.get("stderr", ""))
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=result.get("stderr", ""),
        )

    # ── L6: 刷新 worktree list ──────────────────────────────────────
    worktrees = await _list_worktrees_safe(git_bin, directory)
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=target_wt["path"],
        locked=True,
        lock_reason=reason_text,
        worktrees=worktrees,
    )