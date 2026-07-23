"""vivado-mcp 集成的纯函数工具集 (PR-1 2026-07-23)。

仿 tools/_codegraph_mcp.py 设计,提供:
- detect_vivado_launcher: 检测 vivado-mcp Python 包可用性
- find_vivado_executable: 查找 Vivado 工具链可执行文件 (三层 fallback)
- build_env: 构造透传给 MCP 子进程的环境变量 (VIVADO_PATH)
- ensure_stdio_allowlist: 追加 stdio 白名单 (python + vivado_mcp)
- normalize_path: Windows 反斜杠转正斜杠 (Tcl 兼容)
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)


class VivadoNotFoundError(Exception):
    """找不到 Vivado 工具链。检查: 配置/系统 VIVADO_PATH/Vivado 默认安装路径。"""


def detect_vivado_launcher() -> dict | None:
    """跨平台检测 vivado-mcp 启动器。

    Returns:
        {"type": "stdio", "command": str, "args": list[str]} 或 None (未安装)。
        command 永远是当前 Python 解释器 (sys.executable)。
    """
    if importlib.util.find_spec("vivado_mcp") is None:
        logger.warning(
            "vivado-mcp 未安装, 请 `pip install vivado-mcp` 后重启 AstrBot"
        )
        return None
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "vivado_mcp"],
    }


def normalize_path(path: str) -> str:
    """Windows 反斜杠转正斜杠 (Tcl 兼容)。"""
    return path.replace("\\", "/")


def build_env(*, vivado_executable: str) -> dict[str, str]:
    """构造透传给 MCP 子进程的环境变量。

    Args:
        vivado_executable: spcode 配置的 Vivado 路径, 可空字符串。

    Returns:
        环境变量 dict, 为空字符串/不存在 → {} (让 vivado-mcp 自动检测);
        存在 → {"VIVADO_PATH": 正斜杠路径}
    """
    env: dict[str, str] = {}
    if vivado_executable and os.path.isfile(vivado_executable):
        env["VIVADO_PATH"] = normalize_path(vivado_executable)
    return env


def find_vivado_executable(*, configured: str = "") -> str:
    """查找 Vivado 工具链可执行文件路径 (三层 fallback)。

    优先级: configured > 系统 VIVADO_PATH env > vivado-mcp 内部 find_vivado()

    Args:
        configured: 来自 plugin._config["vivado_executable"], 可空。

    Returns:
        正斜杠格式的绝对路径。

    Raises:
        VivadoNotFoundError: 三层都找不到。
    """
    if configured and os.path.isfile(configured):
        return normalize_path(configured)
    env_path = os.environ.get("VIVADO_PATH", "")
    if env_path and os.path.isfile(env_path):
        return normalize_path(env_path)
    try:
        from vivado_mcp.config import find_vivado
        return find_vivado()
    except FileNotFoundError as e:
        raise VivadoNotFoundError(
            f"未找到 Vivado 安装。请设置 VIVADO_PATH 环境变量, "
            f"或在 spcode 配置的 vivado_executable 字段填入 Vivado 路径。"
            f" (底层错误: {e})"
        ) from e


_STDIO_ALLOWLIST_ENV = "ASTRBOT_MCP_STDIO_ALLOWED_COMMANDS"
_REQUIRED_FOR_VIVADO = frozenset({"python", "vivado_mcp"})


def ensure_stdio_allowlist() -> None:
    """把 python + vivado_mcp 追加到 stdio 白名单 (idempotent)。

    必须在 enable_mcp_server("vivado", ...) 之前调用。
    """
    current = os.environ.get(_STDIO_ALLOWLIST_ENV, "")
    items = {x.strip().lower() for x in current.split(",") if x.strip()}
    needed = _REQUIRED_FOR_VIVADO - items
    if needed:
        items |= needed
        os.environ[_STDIO_ALLOWLIST_ENV] = ",".join(sorted(items))
