"""tests/test_auth_guard_mcp.py — 覆盖 _auth_guard 对 mcp_<server>__ 工具的过滤。

Phase 3 PR-4:
    新增 admin_only_mcp_servers L1 鉴权配置。
    当工具名形如 ``mcp_<X>__<action>`` 且 X ∈ admin_only_mcp_servers,
    非管理员看不到这些工具。

设计说明:
    本测试不直接 ``from main import SPCodeToolkit`` —— 当前环境
    (D:\\anaconda3\\python.exe 3.11.5) 下 site-packages 的 astrbot
    安装不完整(缺失 astrbot.api.web),main.py 的导入链会失败。

    替代方案: 用 ``ast`` 从 main.py 源码动态提取 _auth_guard 方法
    定义, exec 到独立的 _IsolatedSPCodeToolkit 类上。这样测试运行的是
    main.py 里的真实代码,不依赖 main.py 能成功打包。

    关于 admin/non-admin 切换: _auth_guard 内部调用
    ``check_is_admin(event)``(main.py 顶层 import 的名字)。exec 时
    这个名字固定绑到 tools.security.admin.check_is_admin 的真实函数。
    直接 patch ``tools.security.admin.check_is_admin`` 不会影响
    已绑定到类 globals 的引用;改用 ``event.is_admin.return_value`` 控制
    真实函数的返回值(真实 check_is_admin 内部调用 ``event.is_admin()``)。
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── 动态从 main.py 提取 _auth_guard 方法源码 ─────────────


def _extract_auth_guard_source() -> str:
    """从 main.py AST 提取 _auth_guard method body (含 @filter.on_llm_request)。"""
    main_path = Path(__file__).resolve().parent.parent / "main.py"
    source = main_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_auth_guard":
            return ast.unparse(node)
    raise RuntimeError("未找到 _auth_guard 方法")


# ── 构造隔离的最小 SPCodeToolkit ─────────────────────────────


def _build_isolated_plugin_class():
    """构建含 _auth_guard(来自 main.py)的最小 plugin 类。

    关键:
        - _auth_guard 内部用 ``check_is_admin``(main.py 顶层 import 的名字);
          把真实函数绑到 ns['check_is_admin'] 让 exec 后的 method 可调用。
        - ``filter.on_llm_request`` 装饰器被替换为 identity,避免依赖真实 AstrBot。
        - logger 替换为 MagicMock;真实 try/except 失败时会调用 logger.warning。
    """
    method_src = _extract_auth_guard_source()
    fake_filter = MagicMock()
    fake_filter.on_llm_request = lambda *a, **kw: (lambda fn: fn)
    fake_logger = MagicMock()
    from tools.security.admin import check_is_admin as _cia
    ns: dict = {
        "filter": fake_filter,
        "logger": fake_logger,
        "ProviderRequest": MagicMock(),
        "check_is_admin": _cia,
    }
    class_body = textwrap.indent(method_src, "    ")
    full_src = f"class _IsolatedSPCodeToolkit:\n{class_body}"
    exec(full_src, ns)  # noqa: S102  (test-infra dynamic class build)
    return ns["_IsolatedSPCodeToolkit"]


# ── 测试辅助 ─────────────────────────────────────────────


def _make_event(umo: str = "test:umo", *, is_admin: bool = False):
    """Make a MagicMock event whose is_admin() returns the given value.

    Why is_admin default False: 默认模拟非管理员场景;admin test 显式覆盖。
    """
    e = MagicMock()
    e.unified_msg_origin = umo
    e.is_admin = MagicMock(return_value=is_admin)
    return e


def _make_req(tool_names: list[str]):
    """Make a ProviderRequest-like object whose func_tool.tools mirrors tool_names.

    Uses real astrbot.core.agent.tool.ToolSet so the replace-in-place
    logic in _auth_guard (which constructs a new ToolSet) is exercised.
    """
    from astrbot.core.agent.tool import ToolSet

    ts = ToolSet()
    for n in tool_names:
        tool = MagicMock()
        tool.name = n
        ts.add_tool(tool)
    req = MagicMock()
    req.func_tool = ts
    return req


def _kept_names(req) -> list[str]:
    return [t.name for t in req.func_tool.tools]


@pytest.fixture
def base_cfg():
    return {
        "admin_only_mcp_servers": ["codegraph", "vivado"],
        "allowed_ids": "",
    }


@pytest.fixture
def PluginCls():
    """Per-test fixture that builds a fresh isolated plugin class."""
    return _build_isolated_plugin_class()


# ── 测试用例 ─────────────────────────────────────────────


class TestAuthGuardMcpFiltering:
    @pytest.mark.asyncio
    async def test_admin_sees_all_mcp_tools(self, base_cfg, PluginCls):
        plugin = PluginCls.__new__(PluginCls)
        plugin._config = base_cfg
        plugin._tool_names = []
        req = _make_req([
            "mcp_vivado__program_device",
            "mcp_codegraph__explore",
            "astrbot_x",
        ])
        await plugin._auth_guard(_make_event(is_admin=True), req)
        kept = _kept_names(req)
        assert "mcp_vivado__program_device" in kept
        assert "mcp_codegraph__explore" in kept
        assert "astrbot_x" in kept

    @pytest.mark.asyncio
    async def test_non_admin_vivado_mcp_filtered(self, base_cfg, PluginCls):
        plugin = PluginCls.__new__(PluginCls)
        plugin._config = base_cfg
        plugin._tool_names = []
        req = _make_req([
            "mcp_vivado__program_device",
            "mcp_vivado__list_sessions",
        ])
        await plugin._auth_guard(_make_event(is_admin=False), req)
        kept = _kept_names(req)
        assert "mcp_vivado__program_device" not in kept
        assert "mcp_vivado__list_sessions" not in kept

    @pytest.mark.asyncio
    async def test_non_admin_codegraph_mcp_filtered(self, base_cfg, PluginCls):
        plugin = PluginCls.__new__(PluginCls)
        plugin._config = base_cfg
        plugin._tool_names = []
        req = _make_req([
            "mcp_codegraph__explore",
            "mcp_codegraph__search",
        ])
        await plugin._auth_guard(_make_event(is_admin=False), req)
        kept = _kept_names(req)
        assert "mcp_codegraph__explore" not in kept
        assert "mcp_codegraph__search" not in kept

    @pytest.mark.asyncio
    async def test_non_admin_unrelated_mcp_not_filtered(self, base_cfg, PluginCls):
        plugin = PluginCls.__new__(PluginCls)
        plugin._config = base_cfg
        plugin._tool_names = []
        req = _make_req(["mcp_other__foo", "mcp_thirdparty__bar"])
        await plugin._auth_guard(_make_event(is_admin=False), req)
        kept = _kept_names(req)
        assert "mcp_other__foo" in kept
        assert "mcp_thirdparty__bar" in kept

    @pytest.mark.asyncio
    async def test_non_admin_existing_spcode_filter_still_works(self, base_cfg, PluginCls):
        plugin = PluginCls.__new__(PluginCls)
        plugin._config = base_cfg
        plugin._tool_names = ["astrbot_x"]
        req = _make_req(["astrbot_x", "mcp_vivado__foo"])
        await plugin._auth_guard(_make_event(is_admin=False), req)
        kept = _kept_names(req)
        # 原有 _tool_names 行为:astrbot_x 被剔除
        assert "astrbot_x" not in kept
        # 新增 mcp_* 行为:mcp_vivado__foo 被剔除
        assert "mcp_vivado__foo" not in kept

    @pytest.mark.asyncio
    async def test_missing_config_graceful(self, PluginCls):
        """admin_only_mcp_servers 缺失 → 默认空 set, 不崩, 任何 mcp_* 都不被过滤。"""
        cfg = {"allowed_ids": ""}  # no admin_only_mcp_servers key
        plugin = PluginCls.__new__(PluginCls)
        plugin._config = cfg
        plugin._tool_names = []
        req = _make_req(["mcp_vivado__foo", "mcp_other__bar"])
        await plugin._auth_guard(_make_event(is_admin=False), req)
        # 由于 admin_only_mcp_servers 默认空集, vivado 也不在白名单 → 不过滤
        kept = _kept_names(req)
        assert "mcp_vivado__foo" in kept
        assert "mcp_other__bar" in kept
