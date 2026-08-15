"""GET /spcode/home-directory — 返回宿主账户 home 目录（目录选择器起始位置）。

Spec: D:\\AstrbotWorkSpace\\spcode-目录选择器设计方案.md §4.1
Author: elecvoid243, 2026-08-15

Dashboard 的 ProjectDirectoryBrowser（应用内目录选择器）需要一个合法的
起始目录：file-browser 端点要求绝对路径，而浏览器侧并不知道宿主账户的
home 在哪。本端点把它暴露出来，前端 ``goHome()`` 先调本端点拿 home，
再用它调 ``GET /spcode/file-browser?path=...`` 列目录。

该端点只读、无参数、无 umo 依赖——不涉及已加载项目，也不触碰路径安全
黑名单（home 是宿主账户目录，天然在用户自己的地盘）。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(plugin: SPCodeToolkit) -> dict:
    """Web API handler for ``GET /spcode/home-directory``.

    Response envelope::

        {"status": "ok", "data": {"home": "C:/Users/xxx" | "/home/xxx"}}

    ``home`` 是 ``os.path.expanduser("~")`` 的结果——Windows 返回用户目录
    （如 ``C:/Users/foo``），POSIX 返回 ``/home/foo`` 或 ``/root`` 等。
    它总是绝对路径，可直接作为 file-browser 的 ``path`` 参数。
    """
    home = os.path.expanduser("~")
    return {"status": "ok", "data": {"home": home}}
