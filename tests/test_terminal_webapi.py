"""tests/test_terminal_webapi.py — 端点 handler 测试(不用真实 HTTP 框架)。

SSE 端点通过 monkeypatch ``astrbot.api.web.request`` 提供 query 参数;
manager 单例经 monkeypatch 注入 ``tools.terminal.runtime.component``。
"""

import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.terminal import runtime as _runtime  # noqa: E402
from tools.terminal.component import TerminalSessionManager  # noqa: E402
from tools.webapi.terminal import (  # noqa: E402
    handle_input,
    handle_interrupt,
    handle_start,
    handle_status,
    handle_stop,
    handle_stream,
)


@pytest.fixture
def mgr(monkeypatch):
    manager = TerminalSessionManager()
    monkeypatch.setattr(_runtime, "component", manager)
    return manager


@pytest.fixture
def tmp_cwd():
    return tempfile.mkdtemp(prefix="term_api_test_")


def _fake_request(query: dict) -> MagicMock:
    fake = MagicMock()
    fake.query.get.side_effect = lambda k, d=None: query.get(k, d)
    return fake


async def _collect(response) -> str:
    """Join the streaming body chunks (the generator yields str frames)."""
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return "".join(chunks)


@pytest.mark.asyncio
async def test_start_valid_shell(mgr, tmp_cwd):
    res = await handle_start(None, "umo:x", {"shell": "powershell", "cwd": tmp_cwd})
    assert res["status"] == "ok"
    data = res["data"]
    assert data["session_id"].startswith("term_")
    assert data["shell"] == "powershell"
    await mgr.terminate(owner_id="umo:x", session_id=data["session_id"])


@pytest.mark.asyncio
async def test_start_invalid_shell(mgr, tmp_cwd):
    res = await handle_start(None, "umo:x", {"shell": "bash", "cwd": tmp_cwd})
    assert res["data"]["error"] == "start_failed"


@pytest.mark.asyncio
async def test_start_missing_umo(mgr, tmp_cwd):
    res = await handle_start(None, "", {"shell": "powershell", "cwd": tmp_cwd})
    assert res["data"]["error"] == "missing_umo"


@pytest.mark.asyncio
async def test_start_missing_cwd(mgr):
    res = await handle_start(None, "umo:x", {"shell": "powershell", "cwd": ""})
    assert res["data"]["error"] == "missing_cwd"


@pytest.mark.asyncio
async def test_input_unknown_session(mgr):
    res = await handle_input(
        None, "umo:x", {"session_id": "term_nope", "chars": "ls\n"}
    )
    assert res["data"]["error"] == "session_not_found"


@pytest.mark.asyncio
async def test_status_without_session(mgr):
    with patch("astrbot.api.web.request", _fake_request({})):
        res = await handle_status(None, "umo:none")
    assert res["data"]["error"] == "no_session"


@pytest.mark.asyncio
async def test_stop_and_status_roundtrip(mgr, tmp_cwd):
    started = await handle_start(None, "umo:y", {"shell": "powershell", "cwd": tmp_cwd})
    sid = started["data"]["session_id"]
    stopped = await handle_stop(None, "umo:y", {"session_id": sid})
    assert stopped["data"]["status"] == "terminated"
    # 会话已清理 → status 无会话
    with patch("astrbot.api.web.request", _fake_request({})):
        after = await handle_status(None, "umo:y")
    assert after["data"]["error"] == "no_session"


@pytest.mark.asyncio
async def test_stream_emits_exit_after_process_exits(mgr, tmp_cwd):
    started = await mgr.start(
        owner_id="umo:z", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    await mgr.write(owner_id="umo:z", session_id=sid, chars="import os; os._exit(0)\n")
    await asyncio.sleep(1.0)  # give the REPL time to exit

    query = {"session_id": sid, "cursor": "0"}
    with patch("astrbot.api.web.request", _fake_request(query)):
        response = await handle_stream(None, "umo:z")

    body = await _collect(response)
    assert '"type":"exit"' in body
    assert '"type":"error"' not in body


@pytest.mark.asyncio
async def test_stream_error_when_session_missing(mgr):
    query = {"session_id": "term_ghost", "cursor": "0"}
    with patch("astrbot.api.web.request", _fake_request(query)):
        response = await handle_stream(None, "umo:ghost")

    body = await _collect(response)
    assert '"type":"error"' in body


@pytest.mark.asyncio
async def test_interrupt_unknown_session(mgr):
    res = await handle_interrupt(None, "umo:x", {"session_id": "term_nope"})
    assert res["data"]["error"] == "session_not_found"


@pytest.mark.asyncio
async def test_interrupt_live_session(mgr, tmp_cwd):
    started = await handle_start(None, "umo:w", {"shell": "powershell", "cwd": tmp_cwd})
    sid = started["data"]["session_id"]
    res = await handle_interrupt(None, "umo:w", {"session_id": sid})
    assert res["status"] == "ok"
    assert res["data"]["session_id"] == sid
    assert res["data"]["interrupted"] is False  # idle shell: nothing ran
    await mgr.terminate(owner_id="umo:w", session_id=sid)


@pytest.mark.asyncio
async def test_interrupt_kills_running_child(mgr, tmp_cwd):
    """A running external command (child process) is terminated while the
    shell host survives."""
    started = await mgr.start(
        owner_id="umo:v", shell="powershell", cwd=tmp_cwd,
    )
    sid = started["session_id"]
    await asyncio.sleep(0.5)
    # ping -n 60 spawns a child process and blocks for ~60s.
    await mgr.write(owner_id="umo:v", session_id=sid, chars="ping -n 60 127.0.0.1\r\n")
    await asyncio.sleep(2.0)
    res = await mgr.interrupt(owner_id="umo:v", session_id=sid)
    assert res["interrupted"] is True
    assert res["status"] == "running"  # shell host still alive
    await asyncio.sleep(1.0)
    st = await mgr.poll(owner_id="umo:v", session_id=sid, yield_time_ms=0, advance=False)
    assert st["status"] == "running"  # prompt will come back (pool test side-effect-free)
    await mgr.terminate(owner_id="umo:v", session_id=sid)


@pytest.mark.asyncio
async def test_interrupt_manager_unavailable(monkeypatch):
    monkeypatch.setattr(_runtime, "component", None)
    res = await handle_interrupt(None, "umo:x", {"session_id": "term_x"})
    assert res["data"]["error"] == "manager_unavailable"
