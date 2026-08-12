"""Unit tests for POST /spcode/code-check."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.project import state as _proj_state
from tools.webapi.code_check import handle

from tests.conftest import _make_plugin

pytestmark = pytest.mark.asyncio


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "src").mkdir()
    (path / "src" / "a.py").write_text("x=1\n", encoding="utf-8")


def _load(path: Path) -> str:
    umo = "u:code-check"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_check_success_passes_config_and_returns_issues(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    plugin._config["cppcheck_enable"] = ["warning"]
    plugin._config["cppcheck_shortcircuit"] = "never"
    umo = _load(repo)
    captured = {}

    def fake_check(filepath, linter="auto", **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "linter": "ruff",
            "issues": [{"line": 1, "message": "x"}],
            "count": 1,
            "proposal": "ruff 发现 1 个问题",
        }

    with patch(
        "tools.webapi.code_check._check_source_file",
        side_effect=fake_check,
    ):
        result = await handle(plugin, umo=umo, body={"path": "src/a.py"})

    assert result["data"]["success"] is True
    assert result["data"]["checked"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["issues"] == [{"line": 1, "message": "x"}]
    assert captured["cppcheck_enable"] == ["warning"]
    assert captured["cppcheck_shortcircuit"] == "never"


async def test_invalid_linter():
    plugin = _make_plugin()
    result = await handle(plugin, body={"path": "a.py", "linter": "bogus"})
    assert result["data"]["reason"] == "invalid_param"


async def test_invalid_body():
    plugin = _make_plugin()
    result = await handle(plugin, body=None)
    assert result["data"]["reason"] == "invalid_body"


async def test_path_unsafe(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)
    result = await handle(plugin, umo=umo, body={"path": "../a.py"})
    assert result["data"]["reason"] == "path_unsafe"


async def test_backend_file_missing(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_check._check_source_file",
        return_value={"ok": False, "error": "文件不存在: nope.py"},
    ):
        result = await handle(plugin, umo=umo, body={"path": "src/a.py"})

    assert result["data"]["reason"] == "file_not_found"


async def test_backend_tool_unavailable(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_check._check_source_file",
        return_value={"ok": False, "error": "ruff 未安装"},
    ):
        result = await handle(plugin, umo=umo, body={"path": "src/a.py"})

    assert result["data"]["reason"] == "tool_unavailable"
    assert result["data"]["details"]["error"] == "ruff 未安装"
