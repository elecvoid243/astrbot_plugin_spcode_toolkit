"""POST /spcode/git-conflict-resolve — resolve conflicted files.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §6
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from .._helpers import (
    _DEFAULT_TEXT_FILE_FORMAT,
    _decode_text_bytes,
    _detect_text_format,
    _encode_content,
)
from ._helpers import (
    ConflictHunk,
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _parse_conflict_hunks,
    _run_git_async,
    _validate_repo_relative_file,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB


def _rebuild_file_from_hunks(
    original_lines: list[str],
    hunks: list[ConflictHunk],
    choices: dict[int, str],
) -> str | None:
    """Rebuild file content by replacing conflict regions per choices.

    Returns None if any hunk lacks a choice (partial resolution).
    """
    # Check all hunks have choices
    for h in hunks:
        if h.index not in choices:
            return None

    # Build output by walking lines, replacing conflict regions
    output_parts: list[str] = []
    line_idx = 0  # 0-based index into original_lines
    sorted_hunks = sorted(hunks, key=lambda h: h.start_line)

    for hunk in sorted_hunks:
        # Lines before this hunk (1-based start_line → 0-based = start_line - 1)
        hunk_start_0 = hunk.start_line - 1
        hunk_end_0 = hunk.end_line  # end_line is 1-based inclusive → 0-based exclusive

        # Append non-conflict lines before this hunk
        while line_idx < hunk_start_0 and line_idx < len(original_lines):
            output_parts.append(original_lines[line_idx] + "\n")
            line_idx += 1

        # Replace conflict region with chosen content
        choice = choices[hunk.index]
        if choice == "ours":
            output_parts.append(hunk.ours)
        elif choice == "theirs":
            output_parts.append(hunk.theirs)
        elif choice == "base" and hunk.base is not None:
            output_parts.append(hunk.base)

        # Skip past the conflict region
        line_idx = hunk_end_0

    # Append remaining lines after last hunk
    while line_idx < len(original_lines):
        output_parts.append(original_lines[line_idx] + "\n")
        line_idx += 1

    result = "".join(output_parts)
    # Remove trailing extra newline if original didn't end with one
    if result.endswith("\n") and original_lines and original_lines[-1] == "":
        result = result[:-1]
    return result


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-conflict-resolve handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
        )

    file_field = body.get("file")
    all_flag = bool(body.get("all", False))
    hunks_field = body.get("hunks")
    resolution = body.get("resolution")
    content = body.get("content")

    # file/all mutual exclusion
    has_file = isinstance(file_field, str) and file_field.strip()
    if has_file and all_flag:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="file and all are mutually exclusive",
        )
    if not has_file and not all_flag:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="either file or all must be provided",
        )

    # hunks/resolution mutual exclusion
    if hunks_field is not None and resolution is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="hunks and resolution are mutually exclusive",
        )

    # all + custom/hunks forbidden
    if all_flag and resolution == "custom":
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="all=true does not support custom resolution",
        )
    if all_flag and hunks_field is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="all=true does not support per-hunk resolution",
        )

    # custom requires content
    if resolution == "custom" and (not isinstance(content, str) or not content):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="resolution=custom requires non-empty content",
        )

    # content size limit
    if isinstance(content, str) and len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="content exceeds 1 MB limit",
        )

    # resolution enum check
    if resolution is not None and resolution not in ("ours", "theirs", "custom"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            resolved=False,
            stderr="resolution must be ours/theirs/custom",
        )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("resolved", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. conflict state check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.NO_CONFLICT_IN_PROGRESS,
            elapsed_ms=_elapsed(),
            resolved=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 4. resolve ──
    if all_flag:
        # Batch mode: resolve all conflicted files with ours/theirs
        conflicted = await _list_conflicted_files(git_bin, directory)
        for cf in conflicted:
            checkout_side = "--ours" if resolution == "ours" else "--theirs"
            await _run_git_async(
                [git_bin, "-C", directory, "checkout", checkout_side, "--", cf["path"]],
                encoding="utf-8",
                timeout=10.0,
            )
        await _run_git_async(
            [git_bin, "-C", directory, "add", "--all"],
            encoding="utf-8",
            timeout=10.0,
        )
        remaining = await _list_conflicted_files(git_bin, directory)
        return _JSONResponseCompat(
            _make_envelope(
                success=True,
                elapsed_ms=_elapsed(),
                resolved=True,
                file=None,
                mode="all",
                resolution=resolution,
                files_resolved=len(conflicted),
                remaining_conflicts=remaining,
                all_resolved=(len(remaining) == 0),
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            ),
            status_code=200,
        )

    # Single file mode
    file = file_field.strip()
    target, path_err = _validate_repo_relative_file(file, Path(directory))
    if path_err is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(),
            resolved=False,
            file=file,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # Check file is actually conflicted
    conflicted = await _list_conflicted_files(git_bin, directory)
    conflicted_paths = {c["path"] for c in conflicted}
    if file not in conflicted_paths:
        return _make_envelope(
            success=False,
            reason=ReasonCode.FILE_NOT_CONFLICTED,
            elapsed_ms=_elapsed(),
            resolved=False,
            file=file,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    if hunks_field is not None:
        # Mode 1: per-hunk resolution
        if not isinstance(hunks_field, list):
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_BODY,
                elapsed_ms=_elapsed(),
                resolved=False,
                file=file,
            )
        # Binary check
        raw = target.read_bytes() if target.exists() else b""
        if b"\x00" in raw:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                resolved=False,
                file=file,
                stderr="binary file does not support hunk resolution",
            )

        # v2.23.2: 用统一解码链探测原编码(不再 utf-8 errors=replace,
        # 避免 GBK/cp936 源文件被误读成乱码)
        file_content, file_encoding = _decode_text_bytes(raw)
        parsed_hunks = _parse_conflict_hunks(file_content)
        choices: dict[int, str] = {}
        for item in hunks_field:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            choice = item.get("choice")
            if isinstance(idx, int) and choice in ("ours", "theirs", "base"):
                # Validate base choice requires diff3
                if choice == "base":
                    matching = [h for h in parsed_hunks if h.index == idx]
                    if matching and matching[0].base is None:
                        return _make_envelope(
                            success=False,
                            reason=ReasonCode.INVALID_PARAM,
                            elapsed_ms=_elapsed(),
                            resolved=False,
                            file=file,
                            stderr=f"hunk {idx} has no base (not diff3 format)",
                        )
                choices[idx] = choice

        # Validate indices in range
        valid_indices = {h.index for h in parsed_hunks}
        for idx in choices:
            if idx not in valid_indices:
                return _make_envelope(
                    success=False,
                    reason=ReasonCode.INVALID_PARAM,
                    elapsed_ms=_elapsed(),
                    resolved=False,
                    file=file,
                    stderr=f"hunk index {idx} out of range",
                )

        lines = file_content.split("\n")
        rebuilt = _rebuild_file_from_hunks(lines, parsed_hunks, choices)

        if rebuilt is None:
            # Partial resolution
            unresolved = [h for h in parsed_hunks if h.index not in choices]
            remaining = await _list_conflicted_files(git_bin, directory)
            return _JSONResponseCompat(
                _make_envelope(
                    success=True,
                    elapsed_ms=_elapsed(),
                    resolved=False,
                    file=file,
                    mode="hunks",
                    hunks_resolved=len(choices),
                    hunks_total=len(parsed_hunks),
                    partial=True,
                    unresolved_hunks=[
                        {
                            "index": h.index,
                            "start_line": h.start_line,
                            "end_line": h.end_line,
                        }
                        for h in unresolved
                    ],
                    remaining_conflicts=remaining,
                    all_resolved=False,
                    directory=directory,
                    umo=effective_umo,
                    worktree=directory,
                ),
                status_code=200,
            )

        # Write rebuilt content — 保持原编码(v2.23.2;换行由 rebuild 的
        # line+"\n" 拼接自然保持,无需额外归一化)
        target.write_bytes(rebuilt.encode(file_encoding))
        await _run_git_async(
            [git_bin, "-C", directory, "add", "--", file],
            encoding="utf-8",
            timeout=10.0,
        )

    elif resolution in ("ours", "theirs"):
        # Mode 2: whole-file ours/theirs
        checkout_side = "--ours" if resolution == "ours" else "--theirs"
        await _run_git_async(
            [git_bin, "-C", directory, "checkout", checkout_side, "--", file],
            encoding="utf-8",
            timeout=10.0,
        )
        await _run_git_async(
            [git_bin, "-C", directory, "add", "--", file],
            encoding="utf-8",
            timeout=10.0,
        )

    elif resolution == "custom":
        # Mode 3: custom content
        # v2.23.2: 与 file-write 统一 —— 检测冲突文件原编码 + 主导换行,
        # 按原格式写回(不再固定 UTF-8)。
        raw = target.read_bytes() if target.exists() else b""
        file_format = _detect_text_format(raw) if raw else _DEFAULT_TEXT_FILE_FORMAT
        try:
            output_bytes = _encode_content(content, file_format)
        except (UnicodeEncodeError, LookupError) as exc:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                resolved=False,
                file=file,
                stderr=f"content cannot be encoded as {file_format.encoding}: {exc}",
            )
        target.write_bytes(output_bytes)
        await _run_git_async(
            [git_bin, "-C", directory, "add", "--", file],
            encoding="utf-8",
            timeout=10.0,
        )

    # ── 5. readback ──
    remaining = await _list_conflicted_files(git_bin, directory)
    mode = (
        "hunks"
        if hunks_field is not None
        else ("custom" if resolution == "custom" else "whole_file")
    )

    logger.info(
        "git-conflict-resolve: file=%s mode=%s remaining=%d",
        file,
        mode,
        len(remaining),
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            resolved=True,
            file=file,
            mode=mode,
            hunks_resolved=len(choices) if hunks_field else None,
            hunks_total=len(parsed_hunks) if hunks_field else None,
            partial=False,
            remaining_conflicts=remaining,
            all_resolved=(len(remaining) == 0),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
