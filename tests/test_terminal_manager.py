"""tests/test_terminal_manager.py 鈥?TerminalSessionManager 鍗曞厓娴嬭瘯銆?

鐢?``python -i`` 浣滀负 exe_override 妯℃嫙鎸佷箙浜や簰寮?REPL:
stdin 绠￠亾淇濇寔鎵撳紑,閫愯鎵ц骞跺洖鏄?``>>>`` prompt 涓庣粨鏋溿€?

杩愯鏂瑰紡(鍚屾彃浠朵粨搴撳叾浣欐祴璇?:闇€瑕佽兘瀵煎叆鐪熷疄 astrbot 鐨勭幆澧?
(寮€鍙戞満浣跨敤涓婚」鐩?venv),瑙?ledger 澶囨敞銆?
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
    return tempfile.mkdtemp(prefix="term_test_")


async def _wait_for_output(mgr, owner, sid, needle, timeout_s=10.0):
    """Poll repeatedly until output contains `needle` (or timeout)."""
    deadline = time.monotonic() + timeout_s
    out = ""
    try:
        while time.monotonic() < deadline:
            res = await mgr.poll(
                owner_id=owner, session_id=sid, yield_time_ms=200
            )
            out += res["stdout"]
            if needle in out:
                return res
        raise TimeoutError(f"output {needle!r} not seen in {out!r}")
    finally:
        await mgr.terminate(owner_id=owner, session_id=sid)


@pytest.mark.asyncio
async def test_start_returns_session(manager, tmp_cwd):
    res = await manager.start(
        owner_id="umo:1",
        shell="powershell",
        cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    assert res["session_id"].startswith("term_")
    assert res["pid"] > 0
    assert res["status"] == "running"
    await manager.terminate(owner_id="umo:1", session_id=res["session_id"])


@pytest.mark.asyncio
async def test_write_and_poll_roundtrip(manager, tmp_cwd):
    started = await manager.start(
        owner_id="umo:1", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    await manager.write(owner_id="umo:1", session_id=sid, chars="print(6*7, flush=True)\n")
    result = await _wait_for_output(manager, "umo:1", sid, "42")
    assert "42" in result["stdout"]


@pytest.mark.asyncio
async def test_poll_cursor_is_incremental(manager, tmp_cwd):
    started = await manager.start(
        owner_id="umo:1", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    await manager.write(owner_id="umo:1", session_id=sid, chars="print('A'*200, flush=True)\n")
    # 绗竴娆?poll 鐢ㄥ皬 max 闄愬埗,纭 cursor 鍓嶈繘浣嗚緭鍑轰笉閲嶅
    r1 = await manager.poll(
        owner_id="umo:1", session_id=sid, yield_time_ms=1000, max_output_chars=64
    )
    assert "A" * 200 not in r1["stdout"] or r1["has_more"] is True
    r2 = await manager.poll(owner_id="umo:1", session_id=sid, yield_time_ms=1000)
    joined = r1["stdout"] + r2["stdout"]
    assert "A" * 200 in joined
    assert r2["cursor"] >= r1["cursor"]
    await manager.terminate(owner_id="umo:1", session_id=sid)


@pytest.mark.asyncio
async def test_poll_rejects_other_owner(manager, tmp_cwd):
    started = await manager.start(
        owner_id="umo:1", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    with pytest.raises(ValueError):
        await manager.poll(owner_id="umo:2", session_id=sid, yield_time_ms=0)
    with pytest.raises(ValueError):
        await manager.write(owner_id="umo:2", session_id=sid, chars="x\n")
    await manager.terminate(owner_id="umo:1", session_id=sid)


@pytest.mark.asyncio
async def test_terminate_removes_session(manager, tmp_cwd):
    started = await manager.start(
        owner_id="umo:1", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    res = await manager.terminate(owner_id="umo:1", session_id=sid)
    assert res["status"] == "terminated"
    with pytest.raises(ValueError):
        await manager.poll(owner_id="umo:1", session_id=sid, yield_time_ms=0)


@pytest.mark.asyncio
async def test_single_session_per_owner(manager, tmp_cwd):
    first = await manager.start(
        owner_id="umo:1", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    second = await manager.start(
        owner_id="umo:1", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    assert first["session_id"] != second["session_id"]
    # 鏃т細璇濆凡琚浛鎹㈠苟缁堟
    with pytest.raises(ValueError):
        await manager.poll(
            owner_id="umo:1", session_id=first["session_id"], yield_time_ms=0
        )
    await manager.terminate(owner_id="umo:1", session_id=second["session_id"])


@pytest.mark.asyncio
async def test_owner_session_query(manager, tmp_cwd):
    assert await manager.owner_session("umo:9") is None
    started = await manager.start(
        owner_id="umo:9", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    info = await manager.owner_session("umo:9")
    assert info is not None
    assert info["session_id"] == started["session_id"]
    assert await manager.owner_session("umo:8") is None
    await manager.terminate(owner_id="umo:9", session_id=started["session_id"])


@pytest.mark.asyncio
async def test_poll_without_advance_keeps_cursor(manager, tmp_cwd):
    started = await manager.start(
        owner_id="umo:1", shell="powershell", cwd=tmp_cwd,
        exe_override=[sys.executable, "-i"],
    )
    sid = started["session_id"]
    await manager.write(owner_id="umo:1", session_id=sid, chars="print('CUR', flush=True)\n")
    snap = await manager.poll(
        owner_id="umo:1", session_id=sid, yield_time_ms=1000, advance=False
    )
    assert "CUR" in snap["stdout"]
    # advance=False 涓嶆帹杩涗細璇濇父鏍?鍐嶆 poll 浠?0 寮€濮嬩粛鑳界湅鍒?CUR
    again = await manager.poll(
        owner_id="umo:1", session_id=sid, yield_time_ms=0, advance=False
    )
    assert "CUR" in again["stdout"]
    await manager.terminate(owner_id="umo:1", session_id=sid)

