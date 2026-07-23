"""vivado launcher 配置构造 (PR-1 2026-07-23)。"""

from __future__ import annotations

from typing import Any

from .._vivado_mcp import build_env, detect_vivado_launcher


def build_vivado_launcher_cfg(plugin) -> dict[str, Any] | None:
    """构造 vivado MCP 启动配置 (含 env 透传)。

    Args:
        plugin: main.py 的 SPCodeToolkit 实例, 读 _config["vivado_executable"]

    Returns:
        {"type", "command", "args", "env"} 或 None (vivado-mcp 未装)
    """
    launcher = detect_vivado_launcher()
    if launcher is None:
        return None
    cfg: dict[str, Any] = dict(launcher)
    cfg["env"] = build_env(vivado_executable=(plugin._config.get("vivado_executable") or ""))
    return cfg
