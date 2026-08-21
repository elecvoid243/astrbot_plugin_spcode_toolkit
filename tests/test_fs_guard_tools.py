"""fs_access guards in plugin write tools (readonly / workspace).

Contract notes (verified against the real wrappers):
  - FileRemoveTool.call runs the SYNC tools.file_remove.remove via
    run_sync + unwrap; exceptions are caught and wrapped by err_json.
  - CodeFormatTool.call runs the SYNC tools.code_format.format through
    record_and_run (run_sync + unwrap); exceptions are wrapped by err_json.
  The blocking tests therefore patch the sync module functions with plain
  callables that record the attempt and raise, so an unguarded path is
  detected via the `called` flag regardless of exception wrapping.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.tools import fs_access
from tools.function_tools.code_format import CodeFormatTool
from tools.function_tools.file_remove import FileRemoveTool

UMO = "webchat:FriendMessage:webchat!tester!s1"


def _make_context() -> ContextWrapper:
    config_holder = SimpleNamespace(
        get_config=lambda umo=None: {"provider_settings": {}}
    )
    event = SimpleNamespace(
        role="admin", unified_msg_origin=UMO, get_sender_id=lambda: "tester"
    )
    astr_ctx = SimpleNamespace(context=config_holder, event=event)
    return ContextWrapper(context=astr_ctx)


@pytest.fixture(autouse=True)
def _clean_state():
    fs_access.reset()
    yield
    fs_access.reset()


@pytest.mark.asyncio
async def test_file_remove_blocked_in_readonly(tmp_path, monkeypatch):
    fs_access.set_mode_for_umo(UMO, fs_access.FileAccessMode.READONLY)
    target = tmp_path / "victim.txt"
    target.write_text("x", encoding="utf-8")

    from tools import file_remove as file_remove_mod

    called = False

    def _fail(*_a, **_k):
        # Sync: FileRemoveTool.call wraps remove() via run_sync (executor).
        nonlocal called
        called = True
        raise AssertionError("unguarded file_remove.remove was called")

    monkeypatch.setattr(file_remove_mod, "remove", _fail)
    result = await FileRemoveTool().call(_make_context(), path=str(target))
    assert not called
    assert "readonly" in str(result)
    assert target.exists()


@pytest.mark.asyncio
async def test_code_format_blocked_in_readonly(tmp_path, monkeypatch):
    fs_access.set_mode_for_umo(UMO, fs_access.FileAccessMode.READONLY)
    target = tmp_path / "victim.py"
    target.write_text("x=1\n", encoding="utf-8")

    from tools import code_format as code_format_mod

    called = False

    def _fail(*_a, **_k):
        # Sync: record_and_run runs format() via run_sync (executor);
        # an AsyncMock would just return a coroutine and hide the attempt.
        nonlocal called
        called = True
        raise AssertionError("unguarded code_format.format was called")

    monkeypatch.setattr(code_format_mod, "format", _fail)
    result = await CodeFormatTool().call(_make_context(), filepath=str(target))
    assert not called
    assert "readonly" in str(result)


@pytest.mark.asyncio
async def test_code_format_check_dry_run_allowed_in_readonly(tmp_path, monkeypatch):
    fs_access.set_mode_for_umo(UMO, fs_access.FileAccessMode.READONLY)
    target = tmp_path / "ok.py"
    target.write_text("x=1\n", encoding="utf-8")

    from tools import code_format as code_format_mod

    called = False

    def _ok(*_a, **_k):
        # Real format() is sync and returns a dict consumed by unwrap().
        nonlocal called
        called = True
        return {"ok": True, "formatter": "ruff", "check": True, "changed": False}

    monkeypatch.setattr(code_format_mod, "format", _ok)
    result = await CodeFormatTool().call(
        _make_context(), filepath=str(target), check=True
    )
    assert called  # dry-run must actually proceed past the guard
    assert "readonly" not in str(result)
    assert target.read_text(encoding="utf-8") == "x=1\n"
