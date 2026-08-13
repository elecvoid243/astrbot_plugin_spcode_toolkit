"""Unit tests for POST /spcode/code-format."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.project import state as _proj_state
from tools.webapi.code_format import handle

from tests.conftest import _make_plugin

pytestmark = pytest.mark.asyncio


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "a.py").write_text("x=1\n", encoding="utf-8")


def _load(path: Path) -> str:
    umo = "u:code-format"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_format_success_uses_plugin_style_and_indent(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    plugin._config["default_style"] = "google"
    plugin._config["default_indent"] = 2
    umo = _load(repo)
    captured = {}

    def fake_format(filepath, formatter="auto", **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "formatter": "ruff",
            "formatter_options": {"style": "google", "indent": 2},
            "check": False,
            "changed": True,
            "file_size_before": 10,
            "file_size_after": 8,
            "proposal": "formatted",
        }

    with patch(
        "tools.webapi.code_format._format_source_file",
        side_effect=fake_format,
    ):
        result = await handle(plugin, umo=umo, body={"path": "a.py"})

    assert result["data"]["success"] is True
    assert result["data"]["formatted"] is True
    assert result["data"]["changed"] is True
    assert captured["style"] == "google"
    assert captured["indent"] == 2
    assert captured["check"] is False


async def test_check_true_returns_dry_run(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_format._format_source_file",
        return_value={
            "ok": True,
            "formatter": "ruff",
            "check": True,
            "changed": True,
            "diff_summary": "@@",
        },
    ):
        result = await handle(plugin, umo=umo, body={"path": "a.py", "check": True})

    assert result["data"]["success"] is True
    assert result["data"]["formatted"] is False
    assert result["data"]["diff_summary"] == "@@"


async def test_non_bool_check_rejected():
    plugin = _make_plugin()
    result = await handle(plugin, body={"path": "a.py", "check": "yes"})
    assert result["data"]["reason"] == "invalid_body"


async def test_backend_unsupported_extension(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_format._format_source_file",
        return_value={
            "ok": False,
            "error": "不支持的扩展名: .txt。",
            "supported_extensions": [".py"],
        },
    ):
        result = await handle(plugin, umo=umo, body={"path": "a.py"})

    assert result["data"]["reason"] == "unsupported_media_type"


async def test_backend_format_failure(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_format._format_source_file",
        return_value={"ok": False, "error": "写回文件失败: denied"},
    ):
        result = await handle(plugin, umo=umo, body={"path": "a.py"})

    assert result["data"]["reason"] == "format_failed"
    assert result["data"]["details"]["error"] == "写回文件失败: denied"
