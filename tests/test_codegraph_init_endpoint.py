"""tests/test_codegraph_init_endpoint.py — POST /spcode/codegraph-init 测试。

Author: elecvoid243 @ 2026-09-08
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from tools import operation_progress as prog
from tools.webapi.codegraph_init import handle


@pytest.fixture(autouse=True)
def _clean() -> None:
    prog._records.clear()


def _make_plugin(*, silent_result: dict) -> MagicMock:
    plugin = MagicMock()
    plugin.codegraph.init_silent = AsyncMock(return_value=silent_result)
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
            "substep_messages": ["✅ codegraph 初始化完成: C:/proj"],
            "reason": None,
            "initialized": True,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is True
    assert result["data"]["initialized"] is True
    assert result["data"]["directory"] == "C:/proj"


def test_path_invalid_maps_to_path_unsafe():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/nope",
            "substep_messages": ["❌ 目录不存在: C:/nope"],
            "reason": "path_invalid",
            "initialized": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/nope"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "path_unsafe"


def test_cli_missing_maps_to_tool_unavailable():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/proj",
            "substep_messages": ["❌ 找不到 codegraph CLI,请先 npm install -g ..."],
            "reason": "cli_missing",
            "initialized": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "tool_unavailable"


def test_busy_maps_to_operation_in_progress():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/proj",
            "substep_messages": ["⏳ 目录 C:/proj 已有 codegraph 操作在跑,请等待完成"],
            "reason": "busy",
            "initialized": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "operation_in_progress"


def test_codegraph_error_maps_to_git_error():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/proj",
            "substep_messages": [
                "⏳ 正在 初始化 codegraph 项目 C:/proj...",
                "❌ codegraph init 失败(退出码 1): boom",
            ],
            "reason": "codegraph_error",
            "initialized": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "git_error"


class TestInitSilent:
    """CodegraphManager.init_silent 本体。"""

    def _run_silent(self, yielded: list[str]) -> dict:
        from tools.codegraph.manager import CodegraphManager

        plugin = MagicMock()
        mgr = CodegraphManager(plugin)

        async def fake_init(event, directory):
            for m in yielded:
                yield m

        mgr.init = fake_init
        event = MagicMock()
        event.unified_msg_origin = "u-cg"
        return asyncio.run(mgr.init_silent(event, "C:/proj"))

    def test_success_ok(self):
        result = self._run_silent(["✅ codegraph 初始化完成: C:/proj"])
        assert result["ok"] is True
        assert result["initialized"] is True
        assert result["reason"] is None

    def test_force_retry_success_is_ok(self):
        result = self._run_silent(
            [
                "⚠️ 目标目录已初始化 codegraph,自动用 --force 重试...",
                "✅ codegraph 重新初始化完成: C:/proj",
            ]
        )
        assert result["ok"] is True
        assert result["initialized"] is True

    def test_first_message_failure_is_path_invalid(self):
        result = self._run_silent(["❌ 目录不存在: C:/nope"])
        assert result["ok"] is False
        assert result["reason"] == "path_invalid"

    def test_cli_missing(self):
        result = self._run_silent(["❌ 找不到 codegraph CLI,请先 npm install -g ..."])
        assert result["ok"] is False
        assert result["reason"] == "cli_missing"

    def test_busy(self):
        result = self._run_silent(["⏳ 目录 C:/proj 已有 codegraph 操作在跑,请等待完成"])
        assert result["ok"] is False
        assert result["reason"] == "busy"

    def test_late_failure_is_codegraph_error(self):
        result = self._run_silent(
            [
                "⏳ 正在 初始化 codegraph 项目 C:/proj...",
                "❌ codegraph init 失败(退出码 1): boom",
            ]
        )
        assert result["ok"] is False
        assert result["reason"] == "codegraph_error"
