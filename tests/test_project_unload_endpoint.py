"""tests/test_project_unload_endpoint.py — POST /spcode/project-unload 测试。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from tools import operation_progress as prog
from tools.project import state as _state
from tools.webapi.project_unload import handle


@pytest.fixture(autouse=True)
def _clean() -> None:
    prog._records.clear()
    _state._loaded_projects.clear()


def _make_plugin(*, silent_result: dict) -> MagicMock:
    plugin = MagicMock()
    plugin.project.unload_impl_silent = AsyncMock(return_value=silent_result)
    return plugin


def test_missing_umo_rejected():
    plugin = _make_plugin(silent_result={})
    result = asyncio.run(handle(plugin, umo=None, body={"umo": None}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


def test_concurrent_operation_rejected():
    plugin = _make_plugin(silent_result={})
    prog.begin("u1", "project_load")  # 占用 running
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "operation_in_progress"


def test_success():
    plugin = _make_plugin(
        silent_result={
            "ok": True,
            "directory": "C:/proj",
            "substep_messages": ["✅ 项目已卸载: C:/proj"],
            "reason": None,
        }
    )
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is True
    assert result["data"]["unloaded"] is True
    assert result["data"]["directory"] == "C:/proj"
    assert result["data"]["substep_messages"] == ["✅ 项目已卸载: C:/proj"]


def test_no_project_loaded_maps_reason():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "",
            "substep_messages": [],
            "reason": "no_project_loaded",
        }
    )
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "no_project_loaded"


def test_feature_disabled_maps_reason():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "",
            "substep_messages": [],
            "reason": "feature_disabled",
        }
    )
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "feature_disabled"


def test_unhandled_exception_gives_internal_error_and_finish():
    plugin = MagicMock()
    plugin.project.unload_impl_silent = AsyncMock(side_effect=RuntimeError("boom"))
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "internal_error"
    rec = prog.query("u1")
    assert rec["status"] == "failed"
    assert rec["reason"] == "internal_error"


class TestUnloadImplSilent:
    """ProjectManager.unload_impl_silent 本体(不走 endpoint)。"""

    def _make_event(self, umo: str) -> MagicMock:
        event = MagicMock()
        event.unified_msg_origin = umo
        event.plain_result = lambda text: text
        return event

    def test_not_loaded_returns_reason(self):
        from tools.project.manager import ProjectManager

        plugin = MagicMock()
        plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
        mgr = ProjectManager(plugin)
        result = asyncio.run(mgr.unload_impl_silent(self._make_event("u-x")))
        assert result["ok"] is False
        assert result["reason"] == "no_project_loaded"

    def test_not_loaded_finishes_progress_as_done(self):
        """2026-08-07:卸载的幂等空转也写 done(目标状态=未加载,已达成)。"""
        from tools.project.manager import ProjectManager

        plugin = MagicMock()
        plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
        mgr = ProjectManager(plugin)
        prog.begin("u-x", "project_unload")
        result = asyncio.run(mgr.unload_impl_silent(self._make_event("u-x")))
        assert result["ok"] is False
        assert result["reason"] == "no_project_loaded"
        rec = prog.query("u-x")
        assert rec["status"] == "done"
        assert rec["reason"] is None

    def test_feature_disabled(self):
        from tools.project.manager import ProjectManager

        plugin = MagicMock()
        plugin._config = {"agentsmd_enabled": False, "codegraph_enabled": True}
        mgr = ProjectManager(plugin)
        result = asyncio.run(mgr.unload_impl_silent(self._make_event("u-y")))
        assert result["ok"] is False
        assert result["reason"] == "feature_disabled"

    def test_success_collects_messages_and_pops_state(self):
        from tools.project.manager import ProjectManager

        _state.put("u-z", {"directory": "C:/proj", "loaded_at": 1.0})
        plugin = MagicMock()
        plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
        plugin.agentsmd.unload = MagicMock(return_value="✅ AGENTS.md 已移除")
        # codegraph_project 未配置 → 走"跳过 set"分支
        plugin._config["codegraph_project"] = ""
        mgr = ProjectManager(plugin)
        result = asyncio.run(mgr.unload_impl_silent(self._make_event("u-z")))
        assert result["ok"] is True
        assert result["directory"] == "C:/proj"
        assert _state.get("u-z") is None  # state 已清
