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
import os
import re
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
)
from .._helpers import (
    _is_valid_ref_name,
    _list_worktrees_safe,
    _resolve_git_common_dir,
    _validate_new_worktree_path,
)

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


def _map_add_stderr_to_reason(stderr: str) -> str:
    """Map `git worktree add` stderr to ReasonCode.

    Spec §5.2 ADD mapping table.

    Disambiguation notes (CRITICAL):
      - ``<branch> already exists``  → ``cannot_create_existing``
      - ``<path>   already exists``  → ``path_exists_nonempty``
      Both contain "already exists", but **first quoted token** decides:
        - if quoted token is path-shaped (contains `:` or `/` on POSIX, `\\` on Windows, or starts with `~/`)
          → ``path_exists_nonempty``
        - else (alphanumeric/dash/underscore only) → ``cannot_create_existing``

    Real git stderr formats observed (Windows + git 2.43):
      - "fatal: 'feat' already exists"                       → cannot_create_existing
      - "fatal: a branch named 'feat' already exists"        → cannot_create_existing
      - "fatal: '/repo/.worktrees/feat' already exists"      → path_exists_nonempty
      - "fatal: 'feat' is already checked out at '/path'"    → cannot_create_existing
      - "fatal: invalid reference: nonexistent"              → cannot_checkout_missing
      - "fatal: 'fea..ture' is not a valid branch name"       → invalid_branch
      - "fatal: '/foo:bar' cannot be used as a worktree name"→ invalid_param
      - "fatal: invalid start point: badref"                 → invalid_param
    """
    s = stderr.lower()

    # Most specific patterns first (longest/most-unique match wins)
    if "is already checked out at" in s:
        return "cannot_create_existing"
    if "is not a valid branch name" in s:
        return "invalid_branch"
    if "is a missing branch name" in s:
        return "cannot_checkout_missing"
    if "invalid reference" in s:
        # git 2.20+ when add <path> <nonexistent-branch> without -b/-B
        return "cannot_checkout_missing"
    if "cannot be used as a worktree name" in s:
        return "invalid_param"
    if "invalid start point" in s:
        return "invalid_param"

    # Disambiguate "already exists": first quoted token decides
    # Try compact form first: fatal: 'X' already exists
    m = re.search(r"fatal:\s*'([^']*)'\s+already exists", stderr, re.IGNORECASE)
    if not m:
        # Verbose form: fatal: a branch named 'X' already exists
        m = re.search(
            r"branch named\s+'([^']*)'\s+already exists", stderr, re.IGNORECASE
        )
    if m:
        token = m.group(1)
        # Path-shaped tokens: contain / or \ or : or start with ~
        if "/" in token or "\\" in token or ":" in token or token.startswith("~"):
            return "path_exists_nonempty"
        return "cannot_create_existing"

    return "git_error"


def _build_git_worktree_add_args(
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
    """POST /spcode/git-worktree-add handler.

    Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.1

    7-layer defense:
      L1: body type guard
      L2: _validate_new_worktree_path (4-step format, with blacklist)
      L3: _validate_add_cross_fields (create / force / detach / base / branch)
      L4: _is_valid_ref_name (branch / base ref-format)
      L5: path-exists-nonempty preventive check
      L6: _run_git_async(git worktree add ...)
      L7: post-create git-common-dir verification

    PR-A MAJOR-1 fix: blacklist parameter is injected from
    ``plugin._config.get("file_remove_blacklist")`` here, so the
    ``_validate_new_worktree_path`` step 4 defense actually receives the
    configured blacklist (the prior module-level constant was never
    populated in production).
    """
    # ── L1: body type guard ─────────────────────────────────────────
    # NOTE: L1 fires BEFORE preflight, so ``directory`` is unknown here.
    # Per spec §5.3 the failure envelope's ``worktree`` field should
    # reflect the primary worktree. Since primary is not yet resolved,
    # we emit an empty string as a sentinel (the only field that L1
    # cannot populate). This keeps the envelope shape consistent with
    # L2-L7 which all return ``worktree=directory``.
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

    # ── Preflight (5-step) ─────────────────────────────────────────
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

    # ── L2: 4-step new-path defense (with blacklist injection) ─────
    new_path, path_err = _validate_new_worktree_path(
        body.get("path"),
        blacklist=plugin._config.get("file_remove_blacklist"),
    )
    if path_err is not None:
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

    # ── L3: cross-field validation ─────────────────────────────────
    create = bool(body.get("create", False))
    force = bool(body.get("force", False))
    detach = bool(body.get("detach", False))
    base = body.get("base")
    branch = body.get("branch")

    cross_err = _validate_add_cross_fields(create, force, detach, base, branch)
    if cross_err is not None:
        return _make_envelope(
            success=False,
            reason="invalid_body",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=cross_err,
        )

    # ── L4: ref-format checks ───────────────────────────────────────
    if branch is not None and not _is_valid_ref_name(branch):
        return _make_envelope(
            success=False,
            reason="invalid_branch",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"invalid branch name: {branch!r}",
        )
    if base is not None and not _is_valid_ref_name(base):
        return _make_envelope(
            success=False,
            reason="invalid_param",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"invalid base ref: {base!r}",
        )

    # ── L5: path-exists-nonempty preventive check ──────────────────
    if os.path.exists(new_path) and os.listdir(new_path):
        return _make_envelope(
            success=False,
            reason="path_exists_nonempty",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"target path already exists and is non-empty: {new_path}",
        )

    # ── L6: git worktree add ───────────────────────────────────────
    # NOTE: _build_git_worktree_add_args returns args starting with
    # "add" (without the "worktree" subcommand) — that's the test
    # contract for the builder function. The handler must prepend
    # "worktree" when constructing the full CLI.
    add_args = [git_bin, "-C", directory, "worktree"] + _build_git_worktree_add_args(
        new_path,
        branch,
        create,
        force,
        detach,
        base,
    )
    add_result = await _run_git_async(add_args, encoding="utf-8", timeout=30.0)
    if not add_result["ok"]:
        reason = _map_add_stderr_to_reason(add_result.get("stderr", ""))
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=add_result.get("stderr", ""),
        )

    # ── L7: post-create git-common-dir verification (防越权兜底) ────
    try:
        new_common = _resolve_git_common_dir(git_bin, new_path)
        primary_common = _resolve_git_common_dir(git_bin, directory)
    except Exception as exc:
        return _make_envelope(
            success=False,
            reason="worktree_not_in_repo",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=f"post-create git-common-dir resolve failed: {exc}",
        )
    if new_common != primary_common:
        return _make_envelope(
            success=False,
            reason="worktree_not_in_repo",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=(
                f"post-create git-common-dir mismatch: "
                f"{new_common!r} vs {primary_common!r}"
            ),
        )

    # ── 成功:返回 worktree list + created 单条 ──────────────────────
    worktrees = await _list_worktrees_safe(git_bin, directory)
    # git porcelain may emit paths with forward slashes even on Windows
    # (e.g. C:/Users/.../feature). Compare using normpath+normcase so
    # the lookup is OS-portable.
    new_path_norm = os.path.normcase(os.path.normpath(new_path))
    created = next(
        (
            wt
            for wt in worktrees
            if os.path.normcase(os.path.normpath(wt["path"])) == new_path_norm
        ),
        None,
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=new_path,
        created=created,
        worktrees=worktrees,
    )
