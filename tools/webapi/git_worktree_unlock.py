"""POST /spcode/git-worktree-unlock — unlock a git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.4
PR-D (v2.14.0, 2026-06-26).

5-layer defense chain:
  L1: body type guard (non-dict → invalid_body)
  L2: _git_endpoint_preflight (5-step)
  L3: _resolve_target_worktree (format + list lookup → path_unsafe / worktree_not_found)
  L4: git worktree unlock <path> + stderr → not_locked / worktree_not_found / git_error
  L5: _list_worktrees_safe refresh on success

Note on L4 design (vs LOCK handler):
  UNLOCK does NOT have an explicit "not_locked" business gate. Reason:
  - git itself returns "fatal: '/target' is not locked" for already-unlocked
    worktrees, which we map to not_locked via stderr parser.
  - Skipping the explicit check makes UNLOCK symmetric with LOCK's design
    philosophy ("no business gate on main worktree; let git decide") and
    avoids redundant git list lookup overhead.
  - All non-zero git exit codes still produce well-defined ReasonCodes
    (worktree_not_found / not_locked / git_error) via stderr mapping.
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


def _map_unlock_stderr_to_reason(stderr: str) -> str:
    """Map `git worktree unlock` stderr to ReasonCode.

    Spec §5.2 UNLOCK mapping table. Order matters: most specific first.

    Real git stderr formats observed:
      - "fatal: '/target' is not a working tree"     → worktree_not_found
      - "fatal: '/target' is not locked"              → not_locked
      - "fatal: The main working tree cannot be locked or unlocked" → git_error
        (no special reason code; falls through to default)
    """
    s = (stderr or "").lower()
    if "is not a working tree" in s:
        return "worktree_not_found"
    if "not locked" in s:
        return "not_locked"
    return "git_error"


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-worktree-unlock handler.

    Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.4

    Body: {"path": "/abs/path/to/worktree"}

    Note: UNLOCK has **no business restriction** on main worktree
    (mirror of LOCK policy: main can be unlocked; git self-handles).
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

    # ── L4: git worktree unlock <path> ──────────────────────────────
    # No explicit "not_locked" business gate: git returns
    # "fatal: '/target' is not locked" → mapped to not_locked below.
    args = [git_bin, "-C", directory, "worktree", "unlock", target_wt["path"]]
    result = await _run_git_async(args, encoding="utf-8", timeout=10.0)
    if not result["ok"]:
        reason = _map_unlock_stderr_to_reason(result.get("stderr", ""))
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

    # ── L5: 刷新 worktree list ──────────────────────────────────────
    worktrees = await _list_worktrees_safe(git_bin, directory)
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=target_wt["path"],
        locked=False,
        worktrees=worktrees,
    )
