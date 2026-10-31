"""Authenticated, single-file host management without Agent permission changes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .file_rename import _validate_new_name

logger = logging.getLogger(__name__)


def trash_file(target: Path) -> None:
    """Move a validated file to the system trash without a deletion fallback.

    Args:
        target: Validated absolute regular file target.

    Raises:
        ImportError: The trash dependency is unavailable.
        OSError: The system cannot move the file to its trash.
    """
    from send2trash import send2trash

    try:
        send2trash(str(target))
    except Exception:
        logger.exception("[file-trash] system recycle bin rejected %s", target)
        raise
    logger.info("[file-trash] moved to system recycle bin: %s", target)


async def handle(plugin: Any, *, body: dict | None = None) -> dict:
    """Rename a host file in its directory or move it to the system trash.

    Args:
        plugin: Plugin instance; the WebUI extension authenticates the caller.
        body: Explicit absolute path, operation, and new_name for rename.

    Returns:
        Explicit success marker and actual target paths, or a structured error.
        This path binding does not lock out unrelated filesystem writers.
    """
    result: dict[str, Any] = {"renamed": False, "deleted": False}
    try:
        if not isinstance(body, dict) or not isinstance(body.get("path"), str):
            raise ValueError("invalid_body")
        operation = body.get("operation")
        target = Path(body["path"])
        if (
            not target.is_absolute()
            or operation not in ("rename", "trash")
            or body.get("worktree")
            or body.get("ref")
        ):
            raise ValueError("invalid_param")
        target = target.resolve(strict=True)
        result["path"] = str(target)
        if not target.is_file():
            raise ValueError("not_a_file")
        if operation == "rename":
            name = body.get("new_name")
            error = _validate_new_name(name)
            if error:
                raise ValueError(error)
            if name == target.name:
                raise ValueError("invalid_param")
            destination = target.with_name(name)
            if destination.exists() or destination.is_symlink():
                raise ValueError("file_exists")
            target.rename(destination)
            result.update(renamed=True, new_path=str(destination))
            logger.info("[host-file-rename] %s -> %s", target, destination)
        else:
            trash_file(target)
            result.update(deleted=True, trashed=True)
        result["reason"] = None
    except PermissionError:
        result["reason"] = "permission_denied"
    except FileNotFoundError:
        result["reason"] = "path_not_found"
    except ValueError as exc:
        result["reason"] = str(exc)
    except Exception:
        # The OS trash implementations also raise platform-specific exceptions.
        logger.exception("[host-file-manage] operation failed")
        result["reason"] = "operation_failed"
    return {"status": "ok", "data": result}
