"""GET /spcode/drives — 返回宿主可用的磁盘/根目录列表（目录选择器切换盘符）。

Spec: D:\\AstrBotWorkSpace\\spcode-目录选择器设计方案.md（2026-08-15 增补：多盘符导航）
Author: elecvoid243, 2026-08-15

背景：目录选择器从 home 出发、上级止于盘根（如 ``C:\\``），Windows 用户
无法切换到其他盘。本端点提供"此电脑"视图所需的盘符枚举：

- Windows：扫描 A-Z，``os.path.exists("X:\\\\")`` 探测存在的盘符，
  返回 ``["C:\\\\", "D:\\\\", ...]``（仅一次 stat 探测，成本可忽略）；
- POSIX：单根目录模型，返回 ``["/"]``。

返回值直接可作为 file-browser 的 ``path`` 参数。只读、无 umo、不涉及
已加载项目，与 home-directory 同一安全边界（枚举本机盘符不暴露内容）。
"""

from __future__ import annotations

import os
import string
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(plugin: SPCodeToolkit) -> dict:
    """Web API handler for ``GET /spcode/drives``.

    Response envelope::

        {"status": "ok", "data": {"drives": ["C:\\\\", "D:\\\\"] | ["/"]}}

    ``drives`` 是绝对路径列表，每项都带尾部分隔符（Windows ``C:\\`` /
    POSIX ``/``），可直接作为 ``GET /spcode/file-browser?path=...`` 的参数。
    """
    if os.name == "nt":
        drives = [
            f"{letter}:\\"
            for letter in string.ascii_uppercase
            if os.path.exists(f"{letter}:\\")
        ]
    else:
        drives = ["/"]
    return {"status": "ok", "data": {"drives": drives}}
