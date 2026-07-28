"""GET /spcode/git-conflict-status — query conflict state + hunk details.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §5
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _parse_conflict_hunks,
    _read_operation_ref,
    _read_three_way,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_MAX_HUNK_FILES = 20
_MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-conflict-status handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 2. detect operation ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _JSONResponseCompat(
            _make_envelope(
                success=True,
                elapsed_ms=_elapsed(),
                in_conflict=False,
                operation=None,
                operation_ref=None,
                operation_subject=None,
                conflicted_files=[],
                resolved_files=[],
                total_conflicted=0,
                total_resolved=0,
                all_resolved=True,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            ),
            status_code=200,
        )

    # ── 3. read operation ref ──
    op_sha, op_subject = await _read_operation_ref(git_bin, directory, operation)

    # ── 4. list conflicted files ──
    conflicted = await _list_conflicted_files(git_bin, directory)

    # ── 5. resolved files (staged minus unmerged) ──
    staged_result = await _run_git_async(
        [git_bin, "-C", directory, "diff", "--cached", "--name-only", "-z"],
        encoding="utf-8",
        timeout=5.0,
    )
    staged_paths: set[str] = set()
    if staged_result.get("ok"):
        staged_paths = {
            p for p in staged_result.get("stdout", "").split("\0") if p
        }
    conflicted_paths = {c["path"] for c in conflicted}
    resolved_files = sorted(staged_paths - conflicted_paths)

    # ── 6. parse hunks + three-way for each conflicted file ──
    enriched: list[dict] = []
    for i, cf in enumerate(conflicted):
        entry: dict = {
            "path": cf["path"],
            "status": cf["status"],
            "hunks": [],
            "three_way": {"base": None, "ours": None, "theirs": None},
            "binary": False,
            "truncated": False,
        }
        file_path = Path(directory) / cf["path"]

        if i < _MAX_HUNK_FILES and file_path.exists():
            try:
                raw = file_path.read_bytes()
                if b"\x00" in raw:
                    entry["binary"] = True
                elif len(raw) > _MAX_FILE_BYTES:
                    entry["truncated"] = True
                else:
                    content = raw.decode("utf-8", errors="replace")
                    hunks = _parse_conflict_hunks(content)
                    entry["hunks"] = [
                        {
                            "index": h.index,
                            "start_line": h.start_line,
                            "end_line": h.end_line,
                            "ours": h.ours,
                            "theirs": h.theirs,
                            "base": h.base,
                            "ours_label": h.ours_label,
                            "theirs_label": h.theirs_label,
                        }
                        for h in hunks
                    ]
            except OSError:
                pass

        # Three-way (always attempt, even beyond _MAX_HUNK_FILES)
        tw = await _read_three_way(git_bin, directory, cf["path"])
        entry["three_way"] = {
            "base": tw["base"],
            "ours": tw["ours"],
            "theirs": tw["theirs"],
        }
        if tw.get("truncated"):
            entry["truncated"] = True

        enriched.append(entry)

    all_resolved = len(conflicted) == 0

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            in_conflict=True,
            operation=operation,
            operation_ref=op_sha,
            operation_subject=op_subject,
            conflicted_files=enriched,
            resolved_files=resolved_files,
            total_conflicted=len(conflicted),
            total_resolved=len(resolved_files),
            all_resolved=all_resolved,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
