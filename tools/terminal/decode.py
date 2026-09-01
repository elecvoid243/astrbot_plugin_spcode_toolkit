"""Decode raw shell output bytes with a UTF-8-first fallback chain.

Ported (simplified) from AstrBot core's
``astrbot/core/computer/booters/local.py::_decode_bytes_with_fallback``
so the plugin stays free of dependencies on AstrBot internal modules.

Author: elecvoid243 · 2026-09-01
"""

from __future__ import annotations

import locale

# UTF-8 first, then Windows/legacy single/multi-byte encodings.
# ``mbcs`` raises LookupError on non-Windows platforms and is skipped.
_FALLBACK_CHAIN = ("utf-8", "utf-8-sig", "mbcs", "cp936", "gbk", "gb18030")


def decode_bytes_with_fallback(output: bytes | None) -> str:
    """Decode bytes, trying UTF-8 first then Windows/legacy encodings.

    Args:
        output: Raw bytes from a subprocess pipe; None yields "".

    Returns:
        Decoded text; on total failure, UTF-8 with replacement chars.
    """
    if not output:
        return ""
    try:
        preferred = locale.getpreferredencoding(False) or "utf-8"
    except (ValueError, LookupError):
        preferred = "utf-8"
    for encoding in (*_FALLBACK_CHAIN, preferred):
        try:
            return output.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return output.decode("utf-8", errors="replace")
