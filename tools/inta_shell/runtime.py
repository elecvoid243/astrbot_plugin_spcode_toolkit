"""inta_shell 模块级运行时状态(组件实例 + 默认 cwd)。

历史:这 2 个状态原本在 main.py 顶层定义,_inta_component / _inta_default_cwd,
在 SPCodeToolkit.initialize() 中赋值。PR-2 (2026-06-23) 提取到本模块,
让 5 个 IntaShell*Tool 类(已迁移到 tools/function_tools/) 也能引用,
无需再依赖 main.py 的局部符号。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .component import LocalInteractiveShellComponent

# v2.5: 由 SPCodeToolkit.initialize() 赋值
# 在 main.py 启动时是 None;初始化后变成 LocalInteractiveShellComponent 实例
component: "LocalInteractiveShellComponent | None" = None

# 默认 cwd 字符串(空 = 自动)
default_cwd: str = ""
