"""Integration tests for cmd sessions: GBK world roundtrip.

cmd.exe on a Chinese Windows parses stdin bytes with the ANSI code page
(cp936/gbk) and emits builtin output (dir etc.) as GBK bytes. The
terminal manager must therefore:
  - NOT inject PYTHONUTF8 / PYTHONIOENCODING for cmd sessions (otherwise
    python.exe children would emit UTF-8 and break the mixed stream);
  - encode input as GBK when writing to a cmd session;
  - decode output with a stateful GB18030 decoder — with a UTF-8
    fallback, because node/npm-family children write UTF-8 to a pipe
    regardless of the console code page (2026-09-17, see the
    ``test_cmd_decode_*`` cases below).

A real cmd.exe is not used (test suite convention: ``exe_override`` with
``python -i``); the python REPL honours the same locale-based encodings
on a piped stdin/stdout, making it a faithful stand-in for the byte
world behavior.

Author: elecvoid243 · 2026-09-07 (updated 2026-09-17: UTF-8 fallback)
"""

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

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
    # 2026-09-15 fix: 固定 1s 单次窗口在本机 ConPTY 节奏下取不到 print
    # 输出(只见 banner),改为有界重试。消费式 poll 正是本用例要验证的
    # "主 decoder 状态已推进" 前提。
    deadline = time.monotonic() + 10.0
    while True:
        consumed = await manager.poll(
            owner_id="umo:cmd3", session_id=sid, yield_time_ms=1000
        )
        if "快照中文" in consumed["stdout"] or time.monotonic() >= deadline:
            break
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


# ── 2026-09-17:UTF-8 子进程输出容错 ──
# node / npm / vite 对**管道** stdout 恒写 UTF-8(与控制台代码页无关)。
# ``npm run build`` 里 ``node scripts/subset-mdi-font.mjs`` 打印的
# "✅ Found 395 unique mdi-* icons" 就是 UTF-8 的 E2 9C 85:严格 GB18030
# 解不下来(E2 9C 是合法 GBK 对,余下的 0x85 撞上空格 → illegal multibyte)。
# 抛出的 UnicodeDecodeError 是 ValueError 的子类,会被 webapi 层的
# ``except ValueError`` 误报成 "session not found" 并终止 SSE 流
# (实测用户会话日志 offset 186),而 cmd 进程其实还活着。

_UTF8_ICON_LINE = b"\xe2\x9c\x85 Found 395 unique mdi-* icons"


def _decode(raw: bytes, advance: bool = True, session=None):
    """Call the manager's cmd decoder with a minimal session stand-in."""
    if session is None:
        session = SimpleNamespace(gbk_decoder=None)
    return TerminalSessionManager._decode_cmd_output(session, raw, advance)


def test_cmd_decode_tolerates_utf8_child_output():
    """ASCII + UTF-8 chunk:must decode AND keep the emoji intact."""
    text = _decode(b"npm run build\r\n\r\n" + _UTF8_ICON_LINE + b"\n")
    assert "\u2705 Found 395 unique mdi-* icons" in text


def test_cmd_decode_recovers_gbk_after_utf8_chunk():
    """After a UTF-8 chunk the decoder must re-sync for later GBK output."""
    session = SimpleNamespace(gbk_decoder=None)
    _decode(_UTF8_ICON_LINE + b"\n", session=session)
    assert _decode("目录".encode("gbk"), session=session) == "目录"


def test_cmd_decode_peek_survives_mixed_chunk():
    """advance=False (status snapshot reads from byte 0) must not raise.

    A chunk mixing GBK and UTF-8 bytes is inherently lossy — only the
    ASCII payload is guaranteed; the point is that the snapshot call
    never explodes into a bogus "session not found".
    """
    raw = b"banner\r\n" + _UTF8_ICON_LINE + b"\n" + "目录".encode("gbk")
    text = _decode(raw, advance=False)
    assert "Found 395 unique mdi-* icons" in text


@pytest.mark.asyncio
async def test_cmd_session_survives_utf8_child_output(manager, tmp_cwd):
    """A cmd session whose child writes UTF-8 must stay alive and readable."""
    started = await manager.start(
        owner_id="umo:cmd5",
        shell="cmd",
        cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    # ASCII-only source line (write() encodes it as GBK) that pushes raw
    # UTF-8 bytes into the pipe, exactly like node does.
    command = (
        'import sys; sys.stdout.buffer.write(b"\\xe2\\x9c\\x85'
        ' Found 395 unique mdi-* icons\\n"); sys.stdout.flush()\n'
    )
    try:
        await manager.write(owner_id="umo:cmd5", session_id=sid, chars=command)
        out = ""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            res = await manager.poll(
                owner_id="umo:cmd5", session_id=sid, yield_time_ms=200
            )
            out += res["stdout"]
            if "Found 395 unique mdi-* icons" in out:
                break
        assert "Found 395 unique mdi-* icons" in out, out
        snap = await manager.poll(
            owner_id="umo:cmd5", session_id=sid, yield_time_ms=0, cursor=0,
            advance=False,
        )
        assert snap["status"] == "running"
    finally:
        await manager.terminate(owner_id="umo:cmd5", session_id=sid)
