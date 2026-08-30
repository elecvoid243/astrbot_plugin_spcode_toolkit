"""CodeCrapTool(FunctionTool 包装)单元测试。

Spec: docs/superpowers/specs/2026-08-30-code-crap-tool-design.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools._config_filter import ALL_TOOL_NAMES  # noqa: E402
from tools.function_tools import ALL_TOOL_CLASSES  # noqa: E402
from tools.function_tools.code_crap import CodeCrapTool  # noqa: E402

pytestmark = pytest.mark.asyncio


def test_tool_metadata():
    tool = CodeCrapTool()
    assert tool.name == "code_crap"
    assert "crap4py" in tool.description
    assert "lizard" in tool.description
    assert "absolute path" in tool.description
    assert tool.parameters["required"] == ["filepath"]
    props = tool.parameters["properties"]
    assert set(props.keys()) == {"filepath", "lcov", "max_crap"}
    assert props["lcov"]["type"] == "string"
    assert props["max_crap"]["type"] == "number"


def test_registered():
    assert CodeCrapTool in ALL_TOOL_CLASSES
    assert "code_crap" in ALL_TOOL_NAMES


def test_default_threshold():
    assert CodeCrapTool().max_crap_threshold == 30.0


async def test_call_wiring():
    tool = CodeCrapTool()
    captured = {}

    def fake_check(filepath, lcov=None, *, max_crap=30.0):
        captured.update({"filepath": filepath, "lcov": lcov, "max_crap": max_crap})
        return {"ok": True, "proposal": "fake"}

    with patch("tools.code_crap.check", side_effect=fake_check):
        out = await tool.call(None, "F:/x/a.py", "F:/x/lcov.info")
    payload = json.loads(out)
    assert payload == {"ok": True, "proposal": "fake"}  # 含 proposal → unwrap 透传
    assert captured == {
        "filepath": "F:/x/a.py",
        "lcov": "F:/x/lcov.info",
        "max_crap": 30.0,
    }


async def test_call_injected_threshold_used():
    tool = CodeCrapTool()
    tool.max_crap_threshold = 15.0
    captured = {}

    def fake_check(filepath, lcov=None, *, max_crap=30.0):
        captured["max_crap"] = max_crap
        return {"ok": True}

    with patch("tools.code_crap.check", side_effect=fake_check):
        await tool.call(None, "F:/x/a.py")
    assert captured["max_crap"] == 15.0


async def test_call_explicit_max_crap_overrides():
    tool = CodeCrapTool()
    tool.max_crap_threshold = 15.0
    captured = {}

    def fake_check(filepath, lcov=None, *, max_crap=30.0):
        captured["max_crap"] = max_crap
        return {"ok": True}

    with patch("tools.code_crap.check", side_effect=fake_check):
        await tool.call(None, "F:/x/a.py", max_crap=45)
    assert captured["max_crap"] == 45.0


async def test_call_exception_becomes_err_json():
    tool = CodeCrapTool()

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    with patch("tools.code_crap.check", side_effect=boom):
        out = await tool.call(None, "F:/x/a.py")
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "boom" in payload["error"]


def test_default_config_declares_max_crap():
    from astrbot_plugin_spcode_toolkit import main

    assert main._DEFAULT_CONFIG.get("max_crap") == 30
