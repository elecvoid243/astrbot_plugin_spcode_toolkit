"""Shared helpers for code-check/code-format Web API endpoints.

Spec: docs/superpowers/specs/2026-08-12-code-check-format-webapi-design.md
Internal module; do not register as an AstrBot tool.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .._helpers import run_sync
from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _make_envelope,
    _validate_repo_relative_file,
)

_CODE_TOOLS_LOCK = asyncio.Lock()
MAX_CODE_PATH_LENGTH = 512


def _validate_code_path(path: object) -> str | None:
    """Validate the request path field. Returns None when valid."""
    if not isinstance(path, str):
        return ReasonCode.INVALID_BODY
    value = path.strip()
    if not value or len(value) > MAX_CODE_PATH_LENGTH:
        return ReasonCode.INVALID_PARAM
    if "\n" in value or "\r" in value or "\x00" in value:
        return ReasonCode.INVALID_PARAM
    return None


async def _resolve_code_target(
    plugin: object,
    *,
    path: str,
    umo: str | None,
    worktree: str | None,
) -> tuple[dict | None, dict | None, Path | None]:
    """Run preflight and resolve the repo-relative target path."""
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        return err, None, None
    directory = ctx["directory"]
    target, path_err = _validate_repo_relative_file(path, Path(directory))
    if path_err is not None:
        return (
            _make_envelope(
                success=False,
                reason=ReasonCode.PATH_UNSAFE,
                directory=directory,
                umo=ctx["umo"],
                worktree=directory,
                path=path,
            ),
            None,
            None,
        )
    return None, ctx, target


async def _run_code_tool(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Serialize code tool calls and run them outside the event loop."""
    async with _CODE_TOOLS_LOCK:
        return await run_sync(func, *args, **kwargs)


def _tool_details(result: dict) -> dict:
    """Return backend diagnostics without the top-level ok flag."""
    return {key: value for key, value in result.items() if key != "ok"}


def _tool_failure(result: dict, fallback: str) -> tuple[str, str]:
    """Map backend tool failure text to a Web API ReasonCode."""
    error = str(result.get("error", ""))
    proposal = str(result.get("proposal", ""))
    text = f"{error} {proposal}".lower()
    if "文件不存在" in text or "不是普通文件" in text:
        return ReasonCode.FILE_NOT_FOUND, error
    if "不支持的扩展名" in text:
        return ReasonCode.UNSUPPORTED_MEDIA_TYPE, error
    if "文件过大" in text:
        return ReasonCode.FILE_TOO_LARGE, error
    if "未安装" in text or "不可用" in text:
        return ReasonCode.TOOL_UNAVAILABLE, error
    if "不支持" in text or "indent" in text or "formatter" in text:
        return ReasonCode.INVALID_PARAM, error
    return fallback, error
