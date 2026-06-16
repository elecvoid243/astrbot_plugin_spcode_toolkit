"""_code_detect — 代码项目目录检测(v2.9 抽出共享模块)。

WHY: /agentsmd init|load 和 /codegraph init 都要求目标目录下至少存在一个
代码文件。检测逻辑(后缀白名单 + 跳过垃圾目录 + 隐藏目录)最初实现在
tools/agentsmd.py,但 codegraph 在架构上是 agentsmd 的对等模块,
不应反向依赖 agentsmd。故把检测函数抽到本内部模块,两边都用。

提供:
- _SKIP_DIRS: 目录扫描时跳过的常见垃圾目录(node_modules / __pycache__ / ...)
- CODE_FILE_EXTENSIONS: 代码文件后缀白名单(覆盖主流编程语言)
- has_code_files(dir_path) -> bool: 递归扫描 dir_path,返回是否含代码文件

调用方:
- tools/agentsmd.py: import _SKIP_DIRS(给 scan_project_context 用),
                     重导出 CODE_FILE_EXTENSIONS / has_code_files 保持向后兼容
- tools/_codegraph_mcp.py: 通过 require_code_files 参数间接调用 has_code_files
- main.py: handlers 通过 _agentsmd_mod.has_code_files / resolve_project_path 调用
"""

from __future__ import annotations

import os
from pathlib import Path

# 目录扫描时跳过的常见垃圾目录(与 scan_project_context 共享)
# 任何"遍历用户指定目录树"的工具都应使用这份清单以保持视图一致。
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        "__pycache__",
        "venv",
        ".venv",
        "dist",
        "build",
        "target",
        ".git",
        ".idea",
        ".vscode",
        "venv",
    }
)


# ── 代码文件后缀白名单(v2.9 扩展版)───────────────────────
# 按语言家族组织——覆盖主流编程语言,不含纯标记/样式/配置文件
# (html / css / scss / xml / json / yaml / toml / md 等被显式排除)。
# 添加新语言时:把扩展名加到对应家族分组下,并在测试中加一行锁住关键存在性。
#
# 注意事项:
# - 同一扩展名可能跨语言复用(.pl = Perl / Prolog,.m = ObjC / MATLAB,.fs = F#),
#   本表采用"出现任一即算代码"的语义,无需消歧。
# - 大小写不敏感(has_code_files 内部 .lower() 后查表)。

CODE_FILE_EXTENSIONS: frozenset[str] = frozenset({
    # C / C++ / Objective-C / Arduino
    "c", "h",
    "cpp", "cc", "cxx", "c++",
    "hpp", "hh", "hxx", "h++",
    "m", "mm",
    "ino",
    # JVM 系
    "java", "kt", "kts",
    "scala", "sc",
    "groovy",
    # .NET 系
    "cs", "csx",
    "fs", "fsi", "fsx",
    "vb",
    # Web: JavaScript / TypeScript / 主流前端框架
    "js", "mjs", "cjs", "jsx",
    "ts", "tsx", "mts", "cts",
    "vue", "svelte",
    # 后端 / 系统级语言
    "go", "rs",
    "php", "rb",
    "swift", "dart",
    # 脚本语言
    "py", "pyx", "pyi",
    "lua", "pl", "pm", "tcl",
    "r", "jl",
    # Shell 系
    "sh", "bash", "zsh", "fish",
    "ps1", "psm1",
    "bat", "cmd",
    # 函数式语言
    "hs", "lhs",
    "ml", "mli",
    "clj", "cljs", "cljc",
    "scm", "lisp", "lsp",
    "elm",
    "ex", "exs",
    "erl", "hrl",
    # 系统级 / 小众
    "zig", "nim", "d", "cr",
    # 硬件描述语言(HDL)
    "v", "sv", "svh", "vhdl",
})


def has_code_files(dir_path: Path) -> bool:
    """递归扫描 dir_path,返回是否存在后缀在 CODE_FILE_EXTENSIONS 中的代码文件。

    行为约定:
    - 跳过 _SKIP_DIRS 列出的常见垃圾目录(node_modules / __pycache__ / venv /
      dist / build / target / .git / .idea / .vscode / .venv),即使里面有
      .py/.cpp 也不算
    - 跳过以 "." 开头的隐藏目录(与 scan_project_context 行为一致)
    - 扩展名大小写不敏感(Main.PY 与 main.py 同等)
    - 无扩展名的文件(README / Makefile / LICENSE)不算
    - 目录不存在或路径指向文件而非目录 → 返回 False,不抛异常

    该函数是 /agentsmd init|load 和 /codegraph init 的前置守卫之一;
    返回 False 时上层 handler 应直接 yield 错误消息并 return。
    """
    try:
        # os.walk 对不存在的路径会 yield 一次 root=ghost(取决于 Python 版本),
        # 对文件路径会抛 NotADirectoryError。这里 try 包裹统一兜底。
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [
                d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS
            ]
            for filename in files:
                if "." not in filename:
                    continue
                ext = filename.rsplit(".", 1)[-1].lower()
                if ext in CODE_FILE_EXTENSIONS:
                    return True
    except (OSError, ValueError):
        return False
    return False
