"""vivado 可用性门控 (PR 2026-07-24)。

三个独立条件:
    enabled         vivado_enabled 配置项 (默认 True)
    installed       vivado_mcp Python 包已安装
    mcp_running     "vivado" 在 mcp_server_runtime 中

三个条件都满足 → /vivado 命令可用。
任一不满足 → /vivado 命令 handler 入口 yield 提示，不走业务逻辑。

WHY 抽到独立模块:
    - tools/webapi/vivado_status.py 已实现相同 helper (_get_enabled /
      _get_install_missing / _get_mcp_running), 但只服务 webapi 端点
    - main.py 的 4 个 /vivado handler 也需要这三项检查
    - 抽到 tools/vivado/availability.py 让两边共用, 避免逻辑漂移
"""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit


def is_vivado_enabled(plugin: "SPCodeToolkit") -> bool:
    """vivado_enabled 配置项 (默认 True)。"""
    return bool(plugin._config.get("vivado_enabled", True))


def is_vivado_installed() -> bool:
    """vivado_mcp Python 包是否已安装 (无 IO, 只查 importlib)。"""
    return importlib.util.find_spec("vivado_mcp") is not None


def is_vivado_mcp_running(plugin: "SPCodeToolkit") -> bool:
    """"vivado" 是否在 LLM tool manager 的 mcp_server_runtime 中。

    启动期 mcp 尚未就绪时返回 False, 启动后由 tools/vivado/bootstrap.py
    通过 state.mark_running() 反映真实状态。
    """
    try:
        mgr = plugin.context.get_llm_tool_manager()
        return "vivado" in mgr.mcp_server_runtime
    except Exception:
        return False


def check_vivado_available(plugin: "SPCodeToolkit") -> tuple[bool, str | None]:
    """统一入口: (是否可用, 失败原因)。

    失败原因 (str) 供 handler yield 统一错误提示:
        - "disabled":      vivado_enabled=false
        - "not_installed": vivado_mcp 包缺失
        - "not_running":   mcp_server_runtime 中无 vivado

    WHY 用 tuple[str, str | None] 而非 enum:
        - handler 调用简单: (ok, reason) = check_vivado_available(plugin)
        - webapi 端点不需要这个统一入口, 用 3 个独立 helper 更直接
    """
    if not is_vivado_enabled(plugin):
        return False, "disabled"
    if not is_vivado_installed():
        return False, "not_installed"
    if not is_vivado_mcp_running(plugin):
        return False, "not_running"
    return True, None


__all__ = [
    "is_vivado_enabled",
    "is_vivado_installed",
    "is_vivado_mcp_running",
    "check_vivado_available",
]
