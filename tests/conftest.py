"""Pytest config — path setup + module skips.

Path setup:
    自动定位 ``astrbot_plugin_spcode_toolkit`` 包的父目录并加入 ``sys.path``,
    使得 ``from astrbot_plugin_spcode_toolkit import main`` 在以下两种环境
    中都能解析:
      - 主项目根: F:\\github\\astrbot_plugin_spcode_toolkit\\
      - Worktree:   F:\\github\\astrbot_plugin_spcode_toolkit\\.worktrees\\<branch>\\
    原实现固定 ``Path(__file__).parent.parent.parent``(假设 3 层到主项目),
    在 worktree 中会多出 .worktrees/<branch> 一级,导致找不到包。
    这里改用 "向上查找直到遇到含 main.py 的包目录" 的策略。

Module skips:
    - ``test_codegraph_*`` 全部跳过(依赖外部 codegraph MCP 服务,
      当前开发环境未启动;按用户 2026-06-18 决定:codegraph 相关测试暂不跑)
    - ``test_codegraph_cpp`` 还要在 tree-sitter-cpp 缺失时跳过
      (由 ``pytest_collection_modifyitems`` 处理)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── Path setup ─────────────────────────────────────────────
#
# WHY: 测试模块用 ``from astrbot_plugin_spcode_toolkit import main`` 直接
# 导入插件包(因 main.py 顶层用了相对导入 ``from .tools import ...``,
# 不能用裸路径 ``import main``)。我们必须在 pytest 收集阶段就把
# 插件包父目录塞进 ``sys.path``,早于任何测试模块被 import。
#
# 策略:从 conftest.py 自身出发向上走,寻找同时含
#   ``astrbot_plugin_spcode_toolkit/main.py``
# 的最近祖先目录。这能同时适配主项目与 worktree(在后者里会多走一层)。
_PACKAGE_DIR_NAME = "astrbot_plugin_spcode_toolkit"


def _find_package_root() -> Path | None:
    p = Path(__file__).resolve()
    for parent in (p, *p.parents):
        if (parent / _PACKAGE_DIR_NAME / "main.py").exists():
            return parent
    return None


_root = _find_package_root()
if _root is None:
    raise RuntimeError(
        "conftest.py 无法定位 astrbot_plugin_spcode_toolkit 包父目录。"
        "请检查目录结构是否被破坏。"
    )
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


# ── Module skips ───────────────────────────────────────────
#
# 1. codegraph 相关测试依赖外部 MCP 服务,本开发环境不跑。
#    用 collect_ignore 让 pytest 在 collect 阶段就跳过这些文件,
#    避免 import 错误(它们的 import 路径假设 codegraph_* 工具已注册)。
# 2. tree-sitter-cpp 缺失时再单独跳过 test_codegraph_cpp(见下方 hook)。
collect_ignore_glob = [
    "test_codegraph_cmd.py",
    "test_codegraph_lifecycle.py",
    "test_codegraph_mcp.py",
    "test_codegraph_cpp.py",
]


# ── tree-sitter-cpp 依赖探测 ───────────────────────────────
try:
    import tree_sitter  # noqa: F401
    import tree_sitter_cpp  # noqa: F401

    _HAS_CPP = True
except ImportError:
    _HAS_CPP = False


def pytest_collection_modifyitems(config, items):
    if _HAS_CPP:
        return
    skip = pytest.mark.skip(reason="tree-sitter-cpp not installed")
    for item in items:
        if "test_codegraph_cpp" in item.nodeid:
            item.add_marker(skip)
