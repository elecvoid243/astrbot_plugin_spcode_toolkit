"""tests/test_vivado_launcher.py — 覆盖 tools/_vivado_mcp.py 纯函数工具集。"""

from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import patch

import pytest


@pytest.fixture
def vivado_mcp_module():
    return importlib.import_module("tools._vivado_mcp")


@pytest.fixture
def stub_vivado_mcp_modules(monkeypatch):
    """Inject fake ``vivado_mcp`` and ``vivado_mcp.config`` modules into sys.modules.

    WHY: ``mock.patch("vivado_mcp.config.find_vivado", create=True)`` only
    auto-creates the LAST attribute in the dotted path; the intermediate
    modules (``vivado_mcp`` / ``vivado_mcp.config``) must already be present
    for ``from vivado_mcp.config import find_vivado`` to retrieve the patched
    value. This lets the test suite run on machines where the real vivado-mcp
    PyPI package isn't installed.
    """
    monkeypatch.setitem(sys.modules, "vivado_mcp", types.ModuleType("vivado_mcp"))
    monkeypatch.setitem(
        sys.modules,
        "vivado_mcp.config",
        types.ModuleType("vivado_mcp.config"),
    )


class TestDetectVivadoLauncher:
    def test_returns_dict_when_vivado_mcp_installed(self, vivado_mcp_module):
        with patch("importlib.util.find_spec", return_value=object()):
            result = vivado_mcp_module.detect_vivado_launcher()
        assert result is not None
        assert result["type"] == "stdio"
        assert result["command"] == sys.executable
        assert result["args"] == ["-m", "vivado_mcp"]

    def test_returns_none_when_vivado_mcp_not_installed(self, vivado_mcp_module):
        with patch("importlib.util.find_spec", return_value=None):
            assert vivado_mcp_module.detect_vivado_launcher() is None


class TestNormalizePath:
    def test_windows_backslash_to_forward(self, vivado_mcp_module):
        assert vivado_mcp_module.normalize_path(
            r"D:\Xilinx\Vivado\2024.1\bin\vivado.bat"
        ) == "D:/Xilinx/Vivado/2024.1/bin/vivado.bat"

    def test_no_change_on_linux(self, vivado_mcp_module):
        assert vivado_mcp_module.normalize_path("/opt/xilinx/vivado") == \
            "/opt/xilinx/vivado"


class TestBuildEnv:
    def test_empty_when_no_executable(self, vivado_mcp_module):
        assert vivado_mcp_module.build_env(vivado_executable="") == {}

    def test_nonexistent_file_no_env(self, vivado_mcp_module, tmp_path):
        fake = str(tmp_path / "no_such_vivado.bat")
        assert vivado_mcp_module.build_env(vivado_executable=fake) == {}

    def test_existing_file_sets_vivado_path(self, vivado_mcp_module, tmp_path):
        fake = tmp_path / "vivado.bat"
        fake.write_text("@echo off")
        env = vivado_mcp_module.build_env(vivado_executable=str(fake))
        assert env.get("VIVADO_PATH") == str(fake).replace("\\", "/")
        assert "\\" not in env["VIVADO_PATH"]


class TestFindVivadoExecutable:
    def test_configured_takes_priority(self, vivado_mcp_module, tmp_path):
        fake = tmp_path / "vivado.bat"
        fake.write_text("@echo off")
        result = vivado_mcp_module.find_vivado_executable(configured=str(fake))
        assert result == str(fake).replace("\\", "/")

    def test_configured_invalid_falls_through(
        self, vivado_mcp_module, tmp_path, monkeypatch, stub_vivado_mcp_modules
    ):
        monkeypatch.delenv("VIVADO_PATH", raising=False)
        with patch(
            "vivado_mcp.config.find_vivado",
            return_value="/opt/vivado",
            create=True,
        ):
            result = vivado_mcp_module.find_vivado_executable(
                configured=str(tmp_path / "nope.bat")
            )
        assert result == "/opt/vivado"

    def test_env_vivado_path(self, vivado_mcp_module, tmp_path, monkeypatch):
        fake = tmp_path / "vivado.bat"
        fake.write_text("@echo off")
        monkeypatch.setenv("VIVADO_PATH", str(fake))
        result = vivado_mcp_module.find_vivado_executable(configured="")
        assert result == str(fake).replace("\\", "/")

    def test_all_missing_raises_not_found(
        self, vivado_mcp_module, monkeypatch, stub_vivado_mcp_modules
    ):
        monkeypatch.delenv("VIVADO_PATH", raising=False)
        with patch(
            "vivado_mcp.config.find_vivado",
            side_effect=FileNotFoundError("not found"),
            create=True,
        ):
            with pytest.raises(vivado_mcp_module.VivadoNotFoundError):
                vivado_mcp_module.find_vivado_executable(configured="")


class TestEnsureStdioAllowlist:
    def test_idempotent(self, vivado_mcp_module, monkeypatch):
        monkeypatch.delenv("ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS", raising=False)
        vivado_mcp_module.ensure_stdio_allowlist()
        first = os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"]
        vivado_mcp_module.ensure_stdio_allowlist()
        second = os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"]
        assert first == second
        items = {x.strip().lower() for x in first.split(",") if x.strip()}
        assert "python" in items
        assert "vivado_mcp" in items

    def test_preserves_existing(self, vivado_mcp_module, monkeypatch):
        monkeypatch.setenv("ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS", "codegraph,node")
        vivado_mcp_module.ensure_stdio_allowlist()
        items = {
            x.strip().lower()
            for x in os.environ["ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"].split(",")
            if x.strip()
        }
        assert {"codegraph", "node", "python", "vivado_mcp"} <= items
