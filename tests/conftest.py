"""Pytest config — path setup + module skips.

Path setup:
    自动定位插件包根目录(含 main.py 的目录)并加入 ``sys.path``,
    使得 ``from astrbot_plugin_spcode_toolkit import main`` 在以下两种环境
    中都能解析(且 import 的是当前 worktree 的 main.py,而非主项目):
      - 主项目根: F:\\github\\astrbot_plugin_spcode_toolkit\\
      - Worktree:   F:\\github\\astrbot_plugin_spcode_toolkit\\.worktrees\\<branch>\\
    原实现查找 ``parent/astrbot_plugin_spcode_toolkit/main.py``(假设 monorepo
    结构),但本项目是单包结构(package IS the directory),worktree 场景下
    conftest 会一路走到 F:\\github\\,导入到主项目的 main.py 而非 worktree 的,
    导致 worktree 重构验证失效。PR-1 (2026-06-23) 修复为"向上查找最近的 main.py",
    确保 import 命中当前目录树的 main.py。

Module skips:
    - ``test_codegraph_cmd`` / ``test_codegraph_mcp`` 跳过(依赖外部 codegraph
      MCP 服务,当前开发环境未启动;按用户 2026-06-18 决定:codegraph 相关测试
      暂不跑)。
    - PR-0 (2026-06-23) 删除了 ``test_codegraph_cpp`` / ``test_codegraph_lifecycle``
      (旧 codegraph 引擎测试)以及其对应的 tree-sitter-cpp skip hook。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# ── Path setup ─────────────────────────────────────────────
#
# WHY: 测试模块用 ``from astrbot_plugin_spcode_toolkit import main`` 直接
# 导入插件包(因 main.py 顶层用了相对导入 ``from .tools import ...``,
# 不能用裸路径 ``import main``)。我们必须在 pytest 收集阶段就把
# 插件包目录塞进 ``sys.path``,早于任何测试模块被 import。
#
# 策略:从 conftest.py 自身出发向上走,寻找最近的"含 main.py 的祖先目录"。
# 这能同时适配主项目与 worktree——前者找到 F:\\github\\astrbot_plugin_spcode_toolkit\\,
# 后者找到 .worktrees/<branch>\\,确保 ``from astrbot_plugin_spcode_toolkit import main``
# 命中当前 worktree 的 main.py(而非主项目的)。
_PACKAGE_DIR_NAME = "astrbot_plugin_spcode_toolkit"


def _find_package_root() -> Path | None:
    p = Path(__file__).resolve()
    for parent in (p, *p.parents):
        if (parent / "main.py").exists():
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
# codegraph 相关测试依赖外部 MCP 服务,本开发环境不跑。
# 用 collect_ignore 让 pytest 在 collect 阶段就跳过这些文件,
# 避免 import 错误(它们的 import 路径假设 codegraph_* 工具已注册)。
# PR-0 (2026-06-23) 删除 test_codegraph_cpp / test_codegraph_lifecycle
# (旧 codegraph 引擎测试);tree-sitter-cpp skip hook 也已删除。
collect_ignore_glob = [
    "test_codegraph_cmd.py",
    "test_codegraph_mcp.py",
]


# ── 共享 autouse fixture ──────────────────────────────────
# WHY: tools.project.state 是模块级单例,test 之间的状态会相互污染。
# 每个 test 前 reset(),保证隔离。同理 tools.agentsmd.state。
# PR-7 (2026-06-23): 新增 — 把 project/agentsmd state reset 收口到 conftest。
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_module_state():
    """每个 test 前 reset 共享 module-level state(防相互污染)。"""
    # 惰性导入 — conftest 在 import 时机要早于 tools.*,直接 import 会触发
    # 包级 ``from .xxx import`` 链,worktree 下 sys.path 还未就绪会报
    # ImportError。改在 fixture 体内 import。
    try:
        from tools.project import state as _proj_state

        _proj_state.reset()
    except ImportError:
        pass
    try:
        from tools.agentsmd import state as _ag_state

        _ag_state.reset()
    except ImportError:
        pass
    yield


# ── 共享 test helper ────────────────────────────────────────
# WHY: 多个 test_*.py 需要构造 mock 替换 ``astrbot.api.web.request``,
# 原 _make_web_request_mock 在 test_git_diff.py 私有;v3.2 抽出到 conftest
# 供 test_file_browser.py 复用。设计见 docs/superpowers/specs/
# 2026-06-20-file-browser-endpoint-design.md §13 实施检查清单。


def make_web_request_mock(
    query: dict[str, str | None] | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """构造 mock 替换 ``astrbot.api.web.request``。

    Args:
        query: 模拟 query string,如 ``{"scope": "staged", "umo": "x:y"}``。
               key 不存在时返回 None(对齐真实 ``QueryDict.get`` 语义)。
        headers: 模拟 HTTP 请求头,如 ``{"If-None-Match": "W/abc"}``。
                 key 不存在时返回 None(对齐真实 ``Headers.get`` 语义)。
                 v3.3 (2026-06-21) HTTP 缓存支持新增。

    Returns:
        ``MagicMock`` — ``mock.query.get(key[, default])`` 按 query dict 查表;
        ``mock.headers.get(key[, default])`` 按 headers dict 查表。
        接受 1 或 2 个位置参数(对应 ``QueryDict.get`` / ``Headers.get`` 的
        两种签名),1-arg 旧 caller 与 2-arg 新 caller 都能复用同一个 mock。
    """
    mock = MagicMock()
    mock.query.get = MagicMock(
        side_effect=lambda *args: (query or {}).get(args[0]) if args else None
    )
    # v3.3 HTTP 缓存:支持 If-None-Match 等头读取
    # 关键:_get_if_none_match() 走 web.request.headers.get(...)
    mock.headers.get = MagicMock(
        side_effect=lambda *args, **kwargs: (
            (headers or {}).get(args[0], kwargs.get("default")) if args else None
        )
    )
    return mock


def _make_plugin() -> Any:
    """Build a minimal SPCodeToolkit instance for unit testing.

    Bypasses __init__ (which would require a real star.Context) and sets up
    the bare attributes the git-diff handler will touch. Matches the pattern
    used by tests/test_project_subcommand.py.

    v3.2: 从 test_git_diff.py 迁移到 conftest.py,供 test_file_browser.py 复用。
    PR-7 (2026-06-23): 把 ``plugin.get_loaded_project`` 直接绑定到
    ``tools.project.state.get``。生产 ``__init__`` 会设 ``self.project =
    ProjectManager(self)``,但这里 ``__new__`` 绕过了 ``__init__``,所以
    ``self.project`` 仍是类级 ``@filter.command_group`` 方法对象,
    ``self.project.get_loaded_project(umo)`` 会 AttributeError。
    直接绑方法跳过该断点,测试与生产语义等价。
    """
    # 惰性导入避免模块级循环(与 test_git_diff.py 原 import 行为一致)
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit
    from tools.project import state as _proj_state

    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    # PR-7 (2026-06-23): 兼容老代码残留的 ``plugin._loaded_projects``
    # 读取,实际生效走 state。
    plugin._loaded_projects = {}
    plugin._loaded_agents = {}
    plugin._codegraph_projects = {}
    # Permissive default config so feature-flag checks pass.
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": None,
        "git_path": "",
    }
    # PR-7 (2026-06-23): 详见 docstring — 直接绑 state.get 替代
    # ``self.project.get_loaded_project`` 链路。
    plugin.get_loaded_project = _proj_state.get
    return plugin
