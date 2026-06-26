"""POST /spcode/git-worktree-add — create a new git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.1
PR-B (v2.14.0, 2026-06-26).

7-layer defense chain:
  1. body type guard (non-dict → invalid_body)
  2. _validate_new_worktree_path (4-step format defense, with blacklist)
  3. _validate_add_cross_fields (create / force / detach / base / branch)
  4. _is_valid_ref_name (branch ref-format)
  5. _is_valid_ref_name (base ref-format, when present)
  6. path-exists-nonempty preventive check
  7. _run_git_async(git worktree add ...) + post-create git-common-dir verify
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _validate_add_cross_fields(
    create: bool,
    force: bool,
    detach: bool,
    base: str | None,
    branch: str | None,
) -> str | None:
    """Validate cross-field combinations in ADD body. Returns error msg or None.

    Rules (spec §3.1.2):
      - create=true AND force=true           → reject (use -b OR -B)
      - detach=true AND create=true         → reject
      - detach=true AND force=true          → reject
      - base != None AND create=false       → reject (base only meaningful with create)
      - detach=false AND branch empty/None  → reject
    """
    if create and force:
        return "create and force are mutually exclusive (use -b OR -B)"
    if detach and create:
        return "detach mode does not support create=true"
    if detach and force:
        return "detach mode does not support force=true"
    if base is not None and not create:
        return "base is only meaningful when create=true"
    if not detach and not branch:
        return "branch is required when detach=false"
    return None


def _build_git_worktree_add_args(
    directory: str,
    new_path: str,
    branch: str | None,
    create: bool,
    force: bool,
    detach: bool,
    base: str | None,
) -> list[str]:
    """Build `git worktree add` CLI args (excluding `git -C <dir>` prefix).

    Mapping (spec §3.1.1):
      create=True,  force=False  → ['add', '-b', branch, path]            ( + [base] if base)
      create=False, force=True   → ['add', '-B', branch, path]            ( + [base] if base)
      detach=True                → ['add', '--detach', path]              ( + [branch] if branch)
      else                       → ['add', path, branch]   (basic checkout)
    """
    args = ["add"]
    if create:
        args.append("-b")
        args.append(branch)
        args.append(new_path)
        if base is not None:
            args.append(base)
    elif force:
        args.append("-B")
        args.append(branch)
        args.append(new_path)
        if base is not None:
            args.append(base)
    elif detach:
        args.append("--detach")
        args.append(new_path)
        if branch is not None:
            args.append(branch)
    else:
        # basic: add <path> <branch>
        args.append(new_path)
        args.append(branch)
    return args


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-worktree-add handler (stub — implemented in Task 2.4)."""
    raise NotImplementedError("to be implemented in Task 2.4")
