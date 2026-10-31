"""Authenticated, single-file host management without Agent permission changes."""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import sys
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
    if sys.platform != "win32":
        from send2trash import send2trash

        send2trash(str(target))
    else:
        # Require the modern backend explicitly; never select send2trash's legacy fallback.
        if sys.getwindowsversion()[:2] < (6, 2):
            raise OSError("Safe recycling requires Windows 8 or later")
        import pythoncom
        import win32file
        from send2trash.win.IFileOperationProgressSink import FileOperationProgressSink
        from win32com.server.exception import COMException
        from win32com.shell import shell, shellcon

        if win32file.GetDriveType(target.anchor) != win32file.DRIVE_FIXED:
            raise OSError("Safe recycling requires a local fixed drive")

        class RecycleSink(FileOperationProgressSink):
            """Reject destructive transfers and retain the native completion evidence."""

            def __init__(self) -> None:
                super().__init__()
                self.predelete = False
                self.refused = False
                self.recycled = False
                self.hr_delete = None
                self.post_flags = None

            def PreDeleteItem(self, flags: int, item: Any) -> None:
                """Abort through COM when the native transfer is not a recycle.

                Args:
                    flags: Native transfer source flags before the operation.
                    item: Native shell item about to be processed.

                Raises:
                    COMException: Recycling is not available for this transfer.
                """
                self.predelete = True
                if not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                    self.refused = True
                    # A Python return value is ignored for this HRESULT-only method.
                    raise COMException("Permanent deletion refused", scode=-2147467260)

            def PostDeleteItem(
                self, flags: int, item: Any, hr_delete: int, newly_created: Any
            ) -> None:
                """Record an actual recycled shell item, rather than a queued request.

                Args:
                    flags: Native flags used by the transfer.
                    item: Original shell item.
                    hr_delete: Result of the actual operation.
                    newly_created: Recycled item; null for permanent deletion.
                """
                self.hr_delete = hr_delete
                self.post_flags = flags
                self.recycled = (
                    not (hr_delete & 0x80000000)
                    and bool(flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE)
                    and newly_created is not None
                )

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        operation = None
        wrapped = None
        item = None
        try:
            operation = pythoncom.CoCreateInstance(
                shell.CLSID_FileOperation,
                None,
                pythoncom.CLSCTX_INPROC_SERVER,
                shell.IID_IFileOperation,
            )
            # Windows 8+ RECYCLEONDELETE mandates recycling; ALLOWUNDO alone does not.
            operation.SetOperationFlags(
                0x00080000
                | 0x20000000
                | shellcon.FOFX_EARLYFAILURE
                | shellcon.FOF_NOCONFIRMATION
                | shellcon.FOF_NOERRORUI
                | shellcon.FOF_SILENT
            )
            sink = RecycleSink()
            wrapped = pythoncom.WrapObject(sink, shell.IID_IFileOperationProgressSink)
            item = shell.SHCreateItemFromParsingName(
                str(target), None, shell.IID_IShellItem
            )
            operation.DeleteItem(item, wrapped)
            result = operation.PerformOperations()
            aborted = operation.GetAnyOperationsAborted()
            if (
                result not in (None, 0)
                or aborted
                or sink.refused
                or not sink.predelete
                or not sink.recycled
                or target.exists()
            ):
                raise OSError(
                    f"System recycle operation not confirmed: result={result}, aborted={aborted}, predelete={sink.predelete}, refused={sink.refused}, post_flags={sink.post_flags}, item_result={sink.hr_delete}, recycled={sink.recycled}"
                )
        except Exception:
            logger.exception("[file-trash] safe system recycling failed: %s", target)
            raise
        finally:
            operation = None
            wrapped = None
            item = None
            pythoncom.CoUninitialize()
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
