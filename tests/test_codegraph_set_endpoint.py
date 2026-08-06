"""tests/test_codegraph_set_endpoint.py — POST /spcode/codegraph-set 测试。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools import operation_progress as prog
from tools.webapi.codegraph_set import handle


@pytest.fixture(autouse=True)
def _clean() -> None:
    prog._records.clear()


def _make_plugin(*, silent_result: dict) -> MagicMock:
    plugin = MagicMock()
    plugin.codegraph.set_project_silent = AsyncMock(return_value=silent_result)
    return plugin


def test_missing_umo_rejected():
    result = asyncio.run(handle(_make_plugin(silent_result={}), umo=None, body={}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


def test_missing_directory_rejected():
    result = asyncio.run(
        handle(_make_plugin(silent_result={}), umo="u1", body={"umo": "u1"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_param"


def test_concurrent_operation_rejected():
    prog.begin("u1", "project_load")
    result = asyncio.run(
        handle(
            _make_plugin(silent_result={}),
            umo="u1",
            body={"umo": "u1", "directory": "C:/x"},
        )
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "operation_in_progress"


def test_success():
    plugin = _make_plugin(
        silent_result={
            "ok": True,
            "directory": "C:/proj",
            "substep_messages": ["✅ codegraph 已切换到新项目: C:/proj"],
            "reason": None,
            "mcp_restarted": True,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is True
    assert result["data"]["set"] is True
    assert result["data"]["mcp_restarted"] is True


def test_path_invalid_maps_to_path_unsafe():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/nope",
            "substep_messages": ["❌ 目录不存在: C:/nope"],
            "reason": "path_invalid",
            "mcp_restarted": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/nope"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "path_unsafe"


def test_mcp_restart_failed_maps_to_git_error():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/proj",
            "substep_messages": ["❌ codegraph MCP 重启失败: boom"],
            "reason": "mcp_restart_failed",
            "mcp_restarted": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "git_error"


class TestSetProjectSilent:
    """CodegraphManager.set_project_silent 本体。"""

    def _run_silent(self, yielded: list[str]) -> dict:
        from tools.codegraph.manager import CodegraphManager

        plugin = MagicMock()
        mgr = CodegraphManager(plugin)

        async def fake_set_project(event, directory):
            for m in yielded:
                yield m

        mgr.set_project = fake_set_project
        event = MagicMock()
        event.unified_msg_origin = "u-cg"
        return asyncio.run(mgr.set_project_silent(event, "C:/proj"))

    def test_success_ok(self):
        result = self._run_silent(["✅ codegraph 已切换到新项目: C:/proj"])
        assert result["ok"] is True
        assert result["mcp_restarted"] is True

    def test_first_message_failure_is_path_invalid(self):
        result = self._run_silent(["❌ 目录不存在: C:/nope"])
        assert result["ok"] is False
        assert result["reason"] == "path_invalid"

    def test_late_failure_is_mcp_restart_failed(self):
        result = self._run_silent(
            ["🔄 正在重启 codegraph MCP...", "❌ codegraph MCP 重启失败: boom"]
        )
        assert result["ok"] is False
        assert result["reason"] == "mcp_restart_failed"
