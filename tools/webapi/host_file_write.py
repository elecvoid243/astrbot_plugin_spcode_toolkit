"""Authenticated manual text saves, independent of Agent/project permissions."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

from .._helpers import (
    _TEXT_ENCODING_CANDIDATES,
    _TextFileFormat,
    _decode_text_bytes,
    _detect_text_format,
    _encode_content,
)
from .file_browser import FILE_BROWSER_MAX_BYTES

logger = logging.getLogger(__name__)


async def handle(plugin: SPCodeToolkit, *, body: dict | None = None) -> dict:
    """Replace an existing host text file only against its confirmed revision.

    Args:
        plugin: Plugin instance; the WebUI extension authenticates the caller.
        body: Absolute path, text content and SHA256 base_revision.

    Returns:
        Explicit saved/revision fields, or a failure preserving disk contents.
        Conflicts include the newly observed revision for explicit confirmation.
        The final recheck cannot lock out arbitrary external editors between
        that check and replacement; this is optimistic concurrency control.
    """
    result = {"saved": False}
    temporary: Path | None = None
    try:
        if not isinstance(body, dict) or any(
            not isinstance(body.get(key), str)
            for key in ("path", "content", "base_revision")
        ):
            return {"status": "ok", "data": {**result, "reason": "invalid_body"}}
        target = Path(body["path"])
        base = body["base_revision"]
        if not target.is_absolute() or len(base) != 64:
            return {"status": "ok", "data": {**result, "reason": "invalid_param"}}
        if body.get("worktree") or body.get("ref"):
            return {"status": "ok", "data": {**result, "reason": "invalid_param"}}
        target = target.resolve(strict=True)
        result["path"] = str(target)
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode):
            return {"status": "ok", "data": {**result, "reason": "not_a_file"}}
        # Opening without truncation also checks the file's own write permission,
        # rather than relying solely on permission to replace its directory entry.
        with target.open("r+b") as source:
            raw = source.read(FILE_BROWSER_MAX_BYTES + 1)
        if len(raw) > FILE_BROWSER_MAX_BYTES:
            raise ValueError("file_too_large")
        revision = hashlib.sha256(raw).hexdigest()
        if revision != base:
            return {
                "status": "ok",
                "data": {**result, "reason": "conflict", "revision": revision},
            }
        if b"\x00" in raw[:8192]:
            raise ValueError("binary_file")
        file_format = _detect_text_format(raw)
        encoding, newline = file_format.encoding, file_format.newline
        # An ASCII-only intermediate save cannot identify the original encoding.
        # Honor the editing session's hint only when it decodes the actual bytes
        # to exactly the same source text as the normal detection chain.
        hint = body.get("encoding")
        text, _ = _decode_text_bytes(raw)
        if hint in (*_TEXT_ENCODING_CANDIDATES, "utf-8-sig"):
            try:
                if raw.decode(hint) == text:
                    encoding = hint
            except UnicodeError:
                pass
        if (
            "\r" not in text
            and "\n" not in text
            and body.get("newline") in ("\r\n", "\n", "\r")
        ):
            newline = body["newline"]
        output = _encode_content(body["content"], _TextFileFormat(encoding, newline))
        if len(output) > FILE_BROWSER_MAX_BYTES:
            raise ValueError("file_too_large")
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".spcode-save-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(stat.S_IMODE(metadata.st_mode))
        # Keep the old file intact on staging failures and recheck after staging.
        with target.open("rb") as source:
            current = source.read(FILE_BROWSER_MAX_BYTES + 1)
        revision = hashlib.sha256(current).hexdigest()
        if revision != base:
            return {
                "status": "ok",
                "data": {**result, "reason": "conflict", "revision": revision},
            }
        if target.stat().st_ino != metadata.st_ino:
            raise ValueError("target_changed")
        if os.name == "nt":
            # ReplaceFile preserves the original DACL and file attributes.
            import ctypes
            from ctypes import wintypes

            replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
            replace.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.LPVOID,
            ]
            replace.restype = wintypes.BOOL
            if not replace(str(target), str(temporary), None, 0, None, None):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.replace(temporary, target)
        temporary = None
        logger.info("[host-file-write] saved %s (%d bytes)", target, len(output))
        result.update(
            saved=True,
            revision=hashlib.sha256(output).hexdigest(),
            encoding=encoding,
            newline=newline,
            reason=None,
        )
    except PermissionError:
        result["reason"] = "permission_denied"
    except FileNotFoundError:
        result["reason"] = "path_not_found"
    except (UnicodeError, LookupError):
        result["reason"] = "encoding_error"
    except ValueError as exc:
        result["reason"] = str(exc)
    except (OSError, RuntimeError):
        logger.exception("[host-file-write] save failed")
        result["reason"] = "write_failed"
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("[host-file-write] failed to remove staging file")
    return {"status": "ok", "data": result}
