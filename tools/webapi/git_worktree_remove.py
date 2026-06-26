"""POST /spcode/git-worktree-remove — delete a git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.2
PR-C (v2.14.0, 2026-06-26).

8-layer defense chain:
  L1: body type guard (non-dict → invalid_body)
  L2: _git_endpoint_preflight (5-step)
  L3: _resolve_target_worktree (format + list lookup → path_unsafe / worktree_not_found)
  L4: cannot_remove_main (main worktree hard-forbidden; force=true does NOT bypass)
  L5: worktree_locked (force=true does NOT bypass — unlock first)
  L6: worktree_dirty (force=true bypasses via status --porcelain check)
  L7: _run_git_async(git worktree remove [--force] <path>)
  L8: _list_worktrees_safe refresh on success
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


def _map_remove_stderr_to_reason(stderr: str) -> str:
    """Map `git worktree remove` stderr to ReasonCode.

    Spec §5.2 REMOVE mapping table. Order matters: most specific first.

    Real git stderr formats observed:
      - "fatal: '/target' is not a working tree"     → worktree_not_found
      - "fatal: '/target' is locked"                  → worktree_locked
      - "fatal: '/target' contains modified or untracked files" → worktree_dirty
    """
    s = stderr.lower()
    if "is not a working tree" in s:
        return "worktree_not_found"
    if "is locked" in s:
        return "worktree_locked"
    if "contains modified or untracked files" in s:
        return "worktree_dirty"
    return "git_error"


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-worktree-remove handler.

    Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.2

    Conservative safety gates (L4–L6):
      L4 (main):    is_main=True              → cannot_remove_main (硬禁, force=true 不绕过)
      L5 (locked):  locked=True               → worktree_locked    (force=true 不绕过)
      L6 (dirty):   status --porcelain 非空   → worktree_dirty     (force=true 跳过)
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

    # ── L4: main worktree (硬禁, force=true 不绕过) ─────────────────
    if target_wt.get("is_main"):
        return _make_envelope(
            success=False,
            reason="cannot_remove_main",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"main worktree ({target_wt['path']}) cannot be removed",
        )

    # ── L5: locked (force=true 不绕过,需 unlock 后再 remove) ─────────
    if target_wt.get("locked"):
        locked_reason = target_wt.get("locked_reason") or "<no reason>"
        return _make_envelope(
            success=False,
            reason="worktree_locked",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"worktree is locked: {locked_reason}",
        )

    force = bool(body.get("force", False))

    # ── L6: dirty check (除非 force=true) ────────────────────────────
    if not force:
        dirty_result = await _run_git_async(
            [git_bin, "-C", target_wt["path"], "status", "--porcelain"],
            encoding="utf-8",
            timeout=10.0,
        )
        if dirty_result.get("ok") and dirty_result.get("stdout", "").strip():
            return _make_envelope(
                success=False,
                reason="worktree_dirty",
                elapsed_ms=_elapsed(),
                loaded=False,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=(
                    "worktree has uncommitted changes; pass force=true to override"
                ),
            )

    # ── L7: git worktree remove [--force] <path> ────────────────────
    args = [git_bin, "-C", directory, "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(target_wt["path"])
    result = await _run_git_async(args, encoding="utf-8", timeout=30.0)
    if not result["ok"]:
        reason = _map_remove_stderr_to_reason(result.get("stderr", ""))
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

    # ── L8: 刷新 worktree list ──────────────────────────────────────
    worktrees = await _list_worktrees_safe(git_bin, directory)
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=target_wt["path"],
        removed_path=target_wt["path"],
        worktrees=worktrees,
    )
