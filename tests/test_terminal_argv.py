"""Unit tests for TerminalSessionManager._resolve_argv (UTF-8 init).

Author: elecvoid243 · 2026-09-07
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.terminal.component import (  # noqa: E402
    PS_UTF8_INIT,
    TerminalSessionManager,
)


def test_powershell_argv_forces_utf8_encodings():
    """PowerShell 5.1 must emit/consume UTF-8 so Chinese output is intact.

    Without the init command a Chinese Windows PS 5.1 writes GBK bytes
    (e.g. ``C4 BF C2 BC`` for "目录") which the UTF-8-first decoder
    mis-decodes as "\u013f\u00bc".
    """
    argv = TerminalSessionManager._resolve_argv("powershell", None)
    assert argv[0]  # resolved executable (pwsh or powershell.exe)
    assert argv[1:] == [
        "-NoLogo",
        "-NoProfile",
        "-NoExit",
        "-Command",
        PS_UTF8_INIT,
    ]


def test_ps_utf8_init_covers_all_encodings():
    """The init must set Input/Output encoding plus $OutputEncoding."""
    assert "InputEncoding" in PS_UTF8_INIT
    assert "OutputEncoding" in PS_UTF8_INIT
    assert "$OutputEncoding" in PS_UTF8_INIT


def test_cmd_argv_stays_minimal():
    assert TerminalSessionManager._resolve_argv("cmd", None) == ["cmd.exe", "/Q"]


def test_exe_override_wins():
    """Test hook: exe_override replaces argv entirely."""
    override = ["python", "-i"]
    assert TerminalSessionManager._resolve_argv("powershell", override) == override
