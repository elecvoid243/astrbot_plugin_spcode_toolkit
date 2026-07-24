"""tests/test_vivado_availability.py — 覆盖 tools.vivado.availability helper。

PR 2026-07-24: /vivado 子命令可用性门控 (与 /project 模式对齐)。

测试覆盖:
    - is_vivado_enabled: True / False / 缺失默认 True
    - is_vivado_installed: True / False (mock find_spec)
    - is_vivado_mcp_running: True / False / 异常时 False
    - check_vivado_available: 4 种组合 (all ok + 3 种 single fail)

WHY patch 路径用 'tools.vivado.availability.importlib.util.find_spec':
    - availability.py 直接 import importlib, 所以 find_spec 在其命名空间下
    - 直接 patch importlib.util.find_spec 也可, 但路径锁在 availability 内
      更能反映"我们就是要 patch availability 用的 find_spec"语义
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_plugin():
    """默认: enabled=True, mcp_running=True, install_present."""
    p = MagicMock()
    p._config = {"vivado_enabled": True}
    mock_mgr = MagicMock()
    mock_mgr.mcp_server_runtime = {"vivado": {}}
    p.context.get_llm_tool_manager = MagicMock(return_value=mock_mgr)
    return p


# ─────────────────────────────────────────────────────────────────
# is_vivado_enabled
# ─────────────────────────────────────────────────────────────────


class TestIsVivadoEnabled:
    def test_returns_true_when_config_true(self, mock_plugin):
        from tools.vivado.availability import is_vivado_enabled

        mock_plugin._config["vivado_enabled"] = True
        assert is_vivado_enabled(mock_plugin) is True

    def test_returns_false_when_config_false(self, mock_plugin):
        from tools.vivado.availability import is_vivado_enabled

        mock_plugin._config["vivado_enabled"] = False
        assert is_vivado_enabled(mock_plugin) is False

    def test_returns_true_when_config_missing(self, mock_plugin):
        from tools.vivado.availability import is_vivado_enabled

        # 模拟用户没显式设置 vivado_enabled
        mock_plugin._config = {}
        assert is_vivado_enabled(mock_plugin) is True


# ─────────────────────────────────────────────────────────────────
# is_vivado_installed
# ─────────────────────────────────────────────────────────────────


class TestIsVivadoInstalled:
    def test_true_when_find_spec_returns_object(self):
        from tools.vivado.availability import is_vivado_installed

        with patch(
            "tools.vivado.availability.importlib.util.find_spec",
            return_value=object(),
        ):
            assert is_vivado_installed() is True

    def test_false_when_find_spec_returns_none(self):
        from tools.vivado.availability import is_vivado_installed

        with patch(
            "tools.vivado.availability.importlib.util.find_spec",
            return_value=None,
        ):
            assert is_vivado_installed() is False


# ─────────────────────────────────────────────────────────────────
# is_vivado_mcp_running
# ─────────────────────────────────────────────────────────────────


class TestIsVivadoMcpRunning:
    def test_true_when_vivado_in_runtime(self, mock_plugin):
        from tools.vivado.availability import is_vivado_mcp_running

        mock_plugin.context.get_llm_tool_manager().mcp_server_runtime = {
            "vivado": {},
        }
        assert is_vivado_mcp_running(mock_plugin) is True

    def test_false_when_vivado_not_in_runtime(self, mock_plugin):
        from tools.vivado.availability import is_vivado_mcp_running

        mock_plugin.context.get_llm_tool_manager().mcp_server_runtime = {
            "other_mcp": {},
        }
        assert is_vivado_mcp_running(mock_plugin) is False

    def test_false_when_get_llm_tool_manager_raises(self, mock_plugin):
        from tools.vivado.availability import is_vivado_mcp_running

        mock_plugin.context.get_llm_tool_manager = MagicMock(
            side_effect=RuntimeError("mgr not ready")
        )
        # 异常时不抛, 返回 False (handler 入口会 yield not_running 提示)
        assert is_vivado_mcp_running(mock_plugin) is False

    def test_false_when_runtime_empty(self, mock_plugin):
        from tools.vivado.availability import is_vivado_mcp_running

        mock_plugin.context.get_llm_tool_manager().mcp_server_runtime = {}
        assert is_vivado_mcp_running(mock_plugin) is False


# ─────────────────────────────────────────────────────────────────
# check_vivado_available — 统一入口
# ─────────────────────────────────────────────────────────────────


class TestCheckVivadoAvailable:
    def test_all_ok(self, mock_plugin):
        from tools.vivado.availability import check_vivado_available

        with patch(
            "tools.vivado.availability.importlib.util.find_spec",
            return_value=object(),
        ):
            ok, reason = check_vivado_available(mock_plugin)
        assert ok is True
        assert reason is None

    def test_disabled(self, mock_plugin):
        from tools.vivado.availability import check_vivado_available

        mock_plugin._config["vivado_enabled"] = False
        ok, reason = check_vivado_available(mock_plugin)
        assert ok is False
        assert reason == "disabled"

    def test_not_installed(self, mock_plugin):
        from tools.vivado.availability import check_vivado_available

        with patch(
            "tools.vivado.availability.importlib.util.find_spec",
            return_value=None,
        ):
            ok, reason = check_vivado_available(mock_plugin)
        assert ok is False
        assert reason == "not_installed"

    def test_not_running(self, mock_plugin):
        from tools.vivado.availability import check_vivado_available

        # vivado 不在 mcp_server_runtime
        mock_plugin.context.get_llm_tool_manager().mcp_server_runtime = {}
        with patch(
            "tools.vivado.availability.importlib.util.find_spec",
            return_value=object(),
        ):
            ok, reason = check_vivado_available(mock_plugin)
        assert ok is False
        assert reason == "not_running"

    def test_disabled_takes_precedence_over_not_installed(self, mock_plugin):
        """enabled 优先级最高: 即使包没装, 配置禁用也先报 disabled。"""
        from tools.vivado.availability import check_vivado_available

        mock_plugin._config["vivado_enabled"] = False
        with patch(
            "tools.vivado.availability.importlib.util.find_spec",
            return_value=None,
        ):
            ok, reason = check_vivado_available(mock_plugin)
        assert ok is False
        assert reason == "disabled"

    def test_not_installed_takes_precedence_over_not_running(self, mock_plugin):
        """installed 检查先于 running: 包没装就不必查 mcp 状态。"""
        from tools.vivado.availability import check_vivado_available

        with patch(
            "tools.vivado.availability.importlib.util.find_spec",
            return_value=None,
        ):
            ok, reason = check_vivado_available(mock_plugin)
        assert ok is False
        assert reason == "not_installed"


# ─────────────────────────────────────────────────────────────────
# tools.vivado package 重新导出
# ─────────────────────────────────────────────────────────────────


class TestPackageReexport:
    def test_all_helpers_reexported(self):
        from tools.vivado import (
            check_vivado_available,
            is_vivado_enabled,
            is_vivado_installed,
            is_vivado_mcp_running,
        )

        # 仅检查可导入, 不调函数
        assert callable(check_vivado_available)
        assert callable(is_vivado_enabled)
        assert callable(is_vivado_installed)
        assert callable(is_vivado_mcp_running)
