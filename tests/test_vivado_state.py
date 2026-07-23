"""tests/test_vivado_state.py - 覆盖 tools.vivado.state.VivadoState。"""

from __future__ import annotations

import asyncio
import time

import pytest

from tools.vivado import state as state_module
from tools.vivado.state import VivadoState


class TestVivadoStateBasics:
    def test_default_state(self):
        s = VivadoState()
        assert s.bootstrap_task is None
        assert s.mcp_running is False
        assert s.mcp_started_at == 0.0
        assert s.sessions_cached == []
        assert s.sessions_cached_at == 0.0
        assert s.sessions_stale is False

    def test_mark_running(self):
        s = VivadoState()
        s.mark_running(True, started_at=1000.0)
        assert s.mcp_running is True
        assert s.mcp_started_at == 1000.0

    def test_clear_resets_all(self):
        s = VivadoState()
        s.mark_running(True, started_at=1000.0)
        s.update_sessions([{"id": "x"}])
        s.clear()
        assert s.mcp_running is False
        assert s.mcp_started_at == 0.0
        assert s.sessions_cached == []


class TestVivadoStateSessionsCache:
    def test_update_sessions_stores(self):
        s = VivadoState()
        s.update_sessions([{"id": "default", "state": "running"}])
        assert s.get_sessions() == [{"id": "default", "state": "running"}]

    def test_get_sessions_within_5s_returns_cache(self):
        s = VivadoState()
        s.update_sessions([{"id": "default"}])
        assert s.get_sessions() == [{"id": "default"}]

    def test_get_sessions_after_5s_returns_empty_with_stale(self, monkeypatch):
        s = VivadoState()
        s.update_sessions([{"id": "default"}])
        # Capture the real time.time BEFORE patching; the lambda must not call
        # the monkeypatched ``time.time`` directly (would recurse infinitely
        # because state_module.time IS the time module in this process).
        real_time = time.time
        monkeypatch.setattr(state_module.time, "time", lambda: real_time() + 6)
        result = s.get_sessions()
        assert result == []
        assert s.sessions_stale is True


class TestVivadoStateTask:
    @pytest.mark.asyncio
    async def test_set_get_task(self):
        s = VivadoState()
        async def coro():
            await asyncio.sleep(0)
            return "ok"
        task = asyncio.create_task(coro())
        s.set_task(task)
        assert s.get_task() is task
        await task


class TestVivadoStateModuleLevel:
    def test_module_singleton(self):
        from tools.vivado import state as sm
        sm.reset_state()
        s1 = sm.get_state()
        s2 = sm.get_state()
        assert s1 is s2
