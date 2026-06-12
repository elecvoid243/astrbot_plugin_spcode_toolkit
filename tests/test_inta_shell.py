"""inta_shell LLM 工具测试(v2.5 集成)。"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.inta_shell import tools as _sh  # noqa: E402
from tools.inta_shell.component import LocalInteractiveShellComponent  # noqa: E402
from tools.inta_shell.paths import normalize_umo_for_workspace, workspace_root  # noqa: E402
from tools.inta_shell.session_models import (  # noqa: E402
    InteractiveSession,
    InteractiveSessionState,
    session_to_dict,
)


# ── paths ───────────────────────────────────────


def test_normalize_umo_replaces_special_chars():
    result = normalize_umo_for_workspace("webchat:user@123")
    assert "@" not in result
    assert ":" not in result


def test_normalize_umo_empty_returns_unknown():
    assert normalize_umo_for_workspace("   ") == "unknown"


def test_workspace_root_returns_path():
    p = workspace_root("test:session")
    assert str(p).endswith("test_session") or p.name == "test_session"


# ── session_models ─────────────────────────────


def test_session_to_dict_serializable():
    s = InteractiveSession(
        session_id="s1",
        command="python -i",
        pid=12345,
        state=InteractiveSessionState.RUNNING,
    )
    d = session_to_dict(s)
    assert d["session_id"] == "s1"
    assert d["command"] == "python -i"
    assert d["state"] == "running"


# ── _deny / _ok ────────────────────────────────


def test_deny_returns_json_error():
    r = _sh._deny("test error")
    d = json.loads(r)
    assert d["success"] is False
    assert d["error"] == "test error"


def test_ok_returns_json_success():
    r = _sh._ok(custom="value")
    d = json.loads(r)
    assert d["success"] is True
    assert d["custom"] == "value"


def test_ok_merges_multiple_fields():
    r = _sh._ok(a=1, b=2, c="three")
    d = json.loads(r)
    assert d["success"] is True
    assert d["a"] == 1
    assert d["b"] == 2
    assert d["c"] == "three"


# ── start (basic flow) ────────────────────────


def test_start_basic():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.read = AsyncMock(return_value="Python 3.12.0 >>> ")
    session = InteractiveSession(
        session_id="s1",
        command="python -i",
        pid=12345,
        state=InteractiveSessionState.RUNNING,
    )
    comp.start = AsyncMock(return_value=session)

    r = asyncio.run(_sh.start(comp, "test:umo", "python -i"))
    d = json.loads(r)
    assert d["success"] is True
    assert d["session"]["session_id"] == "s1"
    assert d["initial_output"] == "Python 3.12.0 >>> "


def test_start_handles_exception():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.start = AsyncMock(side_effect=Exception("boom"))

    r = asyncio.run(_sh.start(comp, "test:umo", "cmd"))
    d = json.loads(r)
    assert d["success"] is False
    assert "Failed" in d["error"]


def test_start_permission_error():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.start = AsyncMock(side_effect=PermissionError("blocked"))

    r = asyncio.run(_sh.start(comp, "test:umo", "sudo rm -rf /"))
    d = json.loads(r)
    assert d["success"] is False


# ── send ───────────────────────────────────────


def test_send_ok():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.send = AsyncMock()

    r = asyncio.run(_sh.send(comp, "s1", "y"))
    d = json.loads(r)
    assert d["success"] is True


def test_send_not_found():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.send = AsyncMock(side_effect=ValueError("not found"))

    r = asyncio.run(_sh.send(comp, "s999", "x"))
    d = json.loads(r)
    assert d["success"] is False
    assert "not found" in d["error"]


# ── read ───────────────────────────────────────


def test_read_ok():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.read = AsyncMock(return_value="output")
    comp.get_session = AsyncMock(return_value=None)

    r = asyncio.run(_sh.read(comp, "s1"))
    d = json.loads(r)
    assert d["success"] is True
    assert d["output"] == "output"


# ── stop ───────────────────────────────────────


def test_stop_ok():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    session = InteractiveSession(
        session_id="s1",
        command="python -i",
        pid=12345,
        state=InteractiveSessionState.TERMINATED,
    )
    comp.terminate = AsyncMock(return_value=session)

    r = asyncio.run(_sh.stop(comp, "s1"))
    d = json.loads(r)
    assert d["success"] is True


def test_stop_not_found():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.terminate = AsyncMock(side_effect=ValueError("not found"))

    r = asyncio.run(_sh.stop(comp, "s999"))
    d = json.loads(r)
    assert d["success"] is False


# ── list_sessions ──────────────────────────────


def test_list_sessions_ok():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.list_sessions = AsyncMock(return_value=[])

    r = asyncio.run(_sh.list_sessions(comp))
    d = json.loads(r)
    assert d["success"] is True
    assert d["count"] == 0
    assert d["sessions"] == []


def test_list_sessions_error():
    comp = MagicMock(spec=LocalInteractiveShellComponent)
    comp.list_sessions = AsyncMock(side_effect=Exception("term"))

    r = asyncio.run(_sh.list_sessions(comp))
    d = json.loads(r)
    assert d["success"] is False
