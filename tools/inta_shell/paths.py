"""
AstrBot 路径与会话工作区辅助函数。

本文件中的函数与 AstrBot 内核 ``astrbot/core/tools/computer_tools/util.py``
保持一致；将其独立为插件模块，避免插件对内核的 computer_tools 包产生循环依赖。

Author: AstrBot Agent Harness Expert
Date: 2026-06-06
"""

from __future__ import annotations

import re
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path


def normalize_umo_for_workspace(umo: str) -> str:
    """将 unified_msg_origin 规范化为可用于目录名的形式。"""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", umo.strip())
    return normalized or "unknown"


def workspace_root(umo: str) -> Path:
    """获取当前会话的 workspace 根目录。"""
    normalized_umo = normalize_umo_for_workspace(umo)
    return (Path(get_astrbot_workspaces_path()) / normalized_umo).resolve(strict=False)
