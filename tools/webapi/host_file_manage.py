"""Authenticated, single-file host management without Agent permission changes."""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import sys
from pathlib import Path
from typing import Any

from .._recycle import trash_file
from .file_rename import _validate_new_name

logger = logging.getLogger(__name__)


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
            if sys.platform == "win32":
                # MoveFileEx without REPLACE_EXISTING, as used by os.rename on Windows.
                target.rename(destination)
            else:
                # Bind directly to an exclusive native rename, never a replace-capable fallback.
                native = ctypes.CDLL(None, use_errno=True)
                if sys.platform.startswith("linux"):
                    rename = getattr(native, "renameat2", None)
                    arguments = (
                        -100,
                        os.fsencode(target),
                        -100,
                        os.fsencode(destination),
                        1,
                    )
                    signature = [
                        ctypes.c_int,
                        ctypes.c_char_p,
                        ctypes.c_int,
                        ctypes.c_char_p,
                        ctypes.c_uint,
                    ]
                elif sys.platform == "darwin":
                    rename = getattr(native, "renamex_np", None)
                    arguments = (os.fsencode(target), os.fsencode(destination), 4)
                    signature = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
                else:
                    rename = None
                if rename is None:
                    raise OSError(errno.ENOTSUP, "Exclusive rename is unavailable")
                rename.argtypes = signature
                rename.restype = ctypes.c_int
                if rename(*arguments) != 0:
                    code = ctypes.get_errno()
                    raise OSError(code, os.strerror(code), str(destination))
            result.update(renamed=True, new_path=str(destination))
            logger.info("[host-file-rename] %s -> %s", target, destination)
        else:
            trash_file(target)
            result.update(deleted=True, trashed=True)
        result["reason"] = None
    except FileExistsError:
        result["reason"] = "file_exists"
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
