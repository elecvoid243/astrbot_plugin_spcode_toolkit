"""Integration tests for cmd sessions: GBK world roundtrip.

cmd.exe on a Chinese Windows parses stdin bytes with the ANSI code page
(cp936/gbk) and emits builtin output (dir etc.) as GBK bytes. The
terminal manager must therefore:
  - NOT inject PYTHONUTF8 / PYTHONIOENCODING for cmd sessions (otherwise
    python.exe children would emit UTF-8 and break the mixed stream);
  - encode input as GBK when writing to a cmd session;
  - decode output with a stateful GB18030 decoder.

A real cmd.exe is not used (test suite convention: ``exe_override`` with
``python -i``); the python REPL honours the same locale-based encodings
on a piped stdin/stdout, making it a faithful stand-in for the byte
world behavior.

Author: elecvoid243 · 2026-09-07
"""

import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.terminal.component import TerminalSessionManager  # noqa: E402


@pytest.fixture
def manager():
    return TerminalSessionManager()


@pytest.fixture
def tmp_cwd():
    return tempfile.mkdtemp(prefix="term_cmd_test_")


async def _wait_for_output(mgr, owner, sid, needle, timeout_s=10.0):
    """Poll repeatedly until output contains `needle` (or timeout)."""
    deadline = time.monotonic() + timeout_s
    out = ""
    try:
        while time.monotonic() < deadline:
            res = await mgr.poll(owner_id=owner, session_id=sid, yield_time_ms=200)
            out += res["stdout"]
            if needle in out:
                return res
        raise TimeoutError(f"output {needle!r} not seen in {out!r}")
    finally:
        await mgr.terminate(owner_id=owner, session_id=sid)


@pytest.mark.asyncio
async def test_cmd_session_chinese_roundtrip(manager, tmp_cwd):
    """Chinese text must survive input(GBK) -> REPL -> output(GB18030)."""
    started = await manager.start(
        owner_id="umo:cmd1",
        shell="cmd",
        cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    # input line contains Chinese; write() must encode it as GBK so the
    # REPL (locale cp936 on a piped stdin) decodes it back correctly.
    await manager.write(
        owner_id="umo:cmd1",
        session_id=sid,
        chars="print('中文测试', flush=True)\n",
    )
    result = await _wait_for_output(manager, "umo:cmd1", sid, "中文测试")
    assert "中文测试" in result["stdout"]


@pytest.mark.asyncio
async def test_cmd_session_has_no_python_utf8_env(manager, tmp_cwd):
    """cmd sessions must not inherit PYTHONUTF8/PYTHONIOENCODING."""
    started = await manager.start(
        owner_id="umo:cmd2",
        shell="cmd",
        cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    await manager.write(
        owner_id="umo:cmd2",
        session_id=sid,
        chars=(
            "import os; print(os.environ.get('PYTHONUTF8'),"
            " os.environ.get('PYTHONIOENCODING'), flush=True)\n"
        ),
    )
    result = await _wait_for_output(manager, "umo:cmd2", sid, "None")
    assert "None None" in result["stdout"] or (
        "None" in result["stdout"] and "utf-8" not in result["stdout"]
    )


@pytest.mark.asyncio
async def test_cmd_session_status_peek_decodes_from_zero(manager, tmp_cwd):
    """advance=False snapshot reads from byte 0: the main GB18030 decoder
    state must not corrupt the peek decode (fresh decoder per snapshot)."""
    started = await manager.start(
        owner_id="umo:cmd3",
        shell="cmd",
        cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    await manager.write(
        owner_id="umo:cmd3",
        session_id=sid,
        chars="print('快照中文', flush=True)\n",
    )
    # Consume output normally (advances the session decoder state).
    await manager.poll(owner_id="umo:cmd3", session_id=sid, yield_time_ms=1000)
    # Peek from byte 0 (same as handle_status) must still decode Chinese
    # correctly with a fresh decoder.
    snap = await manager.poll(
        owner_id="umo:cmd3",
        session_id=sid,
        yield_time_ms=0,
        cursor=0,
        advance=False,
    )
    assert "快照中文" in snap["stdout"]
    await manager.terminate(owner_id="umo:cmd3", session_id=sid)


@pytest.mark.asyncio
async def test_powershell_session_keeps_utf8_env(manager, tmp_cwd):
    """PowerShell sessions (UTF-8 world) must keep the Python env vars."""
    started = await manager.start(
        owner_id="umo:ps1",
        shell="powershell",
        cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    await manager.write(
        owner_id="umo:ps1",
        session_id=sid,
        chars=(
            "import os; print(os.environ.get('PYTHONUTF8'),"
            " os.environ.get('PYTHONIOENCODING'), flush=True)\n"
        ),
    )
    result = await _wait_for_output(manager, "umo:ps1", sid, "utf-8")
    assert "1 utf-8" in result["stdout"]
