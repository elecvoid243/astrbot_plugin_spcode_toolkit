"""main.py 中 codegraph 生命周期测试。"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# main.py 用相对导入 (from .tools import ...),需把项目父目录加到 sys.path
# 然后以包形式导入 main。这样 `from .tools import` 才能 resolve。
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit
_MAIN_MODULE_PATH = "astrbot_plugin_spcode_toolkit.main"


def _make_plugin(config_overrides: dict | None = None):
    """构造一个最小可用的 SPCodeToolkit 实例用于测试。"""
    # 使用 __new__ 跳过真实的 __init__,手动注入最小字段
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    config = {
        "codegraph_enabled": True,
        # v2.1: 显式提供 install_dir 才能让 bootstrap 跑通(用户原始行为)
        "codegraph_install_dir": "/fake/install/dir",
        "codegraph_project": "",
        "allowed_ids": "",
    }
    if config_overrides:
        config.update(config_overrides)
    context = MagicMock()
    context.get_llm_tool_manager.return_value = MagicMock(
        mcp_server_runtime={},
    )
    plugin.context = context
    plugin._config = config
    plugin._codegraph_task = None
    return plugin


def test_bootstrap_skips_when_disabled():
    plugin = _make_plugin({"codegraph_enabled": False})
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher") as mock_detect:
        asyncio.run(plugin._bootstrap_codegraph_mcp())
    mock_detect.assert_not_called()


def test_bootstrap_skips_when_install_dir_empty():
    """v2.1: install_dir 未配置 → 直接跳过 MCP(连 detect 都不调)。"""
    plugin = _make_plugin({"codegraph_install_dir": ""})
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher") as mock_detect:
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist") as mock_ensure:
            asyncio.run(plugin._bootstrap_codegraph_mcp())
    mock_detect.assert_not_called()
    mock_ensure.assert_not_called()
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server.assert_not_called()


def test_bootstrap_skips_when_codegraph_not_found():
    plugin = _make_plugin()
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=None):
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist") as mock_ensure:
            asyncio.run(plugin._bootstrap_codegraph_mcp())
    mock_ensure.assert_called_once()
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server.assert_not_called()


def test_bootstrap_passes_install_dir_to_detect():
    """验证 install_dir 被原样传给 detect_codegraph_launcher。"""
    plugin = _make_plugin({"codegraph_install_dir": "C:/my/codegraph"})
    cfg = {
        "type": "stdio",
        "command": "C:/my/codegraph/node.exe",
        "args": [
            "--liftoff-only",
            "C:/my/codegraph/lib/dist/bin/codegraph.js",
            "serve",
            "--mcp",
        ],
    }
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server = AsyncMock()

    with patch(
        f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=cfg
    ) as mock_detect:
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
            asyncio.run(plugin._bootstrap_codegraph_mcp())

    # 验证传给 detect 的 install_dir 与 config 一致
    mock_detect.assert_called_once_with(install_dir="C:/my/codegraph")
    mgr.enable_mcp_server.assert_called_once()


def test_bootstrap_calls_enable_when_found():
    plugin = _make_plugin()
    cfg = {"type": "stdio", "command": "/usr/bin/codegraph", "args": ["serve", "--mcp"]}
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server = AsyncMock()

    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=cfg):
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
            asyncio.run(plugin._bootstrap_codegraph_mcp())

    mgr.enable_mcp_server.assert_called_once()
    call = mgr.enable_mcp_server.call_args
    assert call.kwargs["name"] == "codegraph"
    assert call.kwargs["config"] == cfg
    assert call.kwargs["timeout"] == 180


def test_bootstrap_passes_path_when_project_set():
    plugin = _make_plugin({"codegraph_project": "/my/proj"})
    cfg = {
        "type": "stdio",
        "command": "node.exe",
        "args": ["entry.js", "serve", "--mcp"],
    }
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server = AsyncMock()

    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=cfg):
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
            asyncio.run(plugin._bootstrap_codegraph_mcp())

    args = mgr.enable_mcp_server.call_args.kwargs["config"]["args"]
    assert "--path" in args
    assert "/my/proj" in args


def test_bootstrap_rejects_path_with_shell_meta():
    plugin = _make_plugin({"codegraph_project": "/proj&injection"})
    cfg = {
        "type": "stdio",
        "command": "node.exe",
        "args": ["entry.js", "serve", "--mcp"],
    }
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server = AsyncMock()

    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=cfg):
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
            asyncio.run(plugin._bootstrap_codegraph_mcp())

    args = mgr.enable_mcp_server.call_args.kwargs["config"]["args"]
    assert "--path" not in args


def test_bootstrap_handles_timeout_gracefully():
    plugin = _make_plugin()
    cfg = {"type": "stdio", "command": "codegraph", "args": ["serve", "--mcp"]}
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server = AsyncMock(side_effect=TimeoutError("timeout"))

    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=cfg):
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
            asyncio.run(plugin._bootstrap_codegraph_mcp())


def test_bootstrap_handles_generic_exception():
    plugin = _make_plugin()
    cfg = {"type": "stdio", "command": "codegraph", "args": ["serve", "--mcp"]}
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.enable_mcp_server = AsyncMock(side_effect=Exception("boom"))

    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=cfg):
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
            asyncio.run(plugin._bootstrap_codegraph_mcp())


def test_bootstrap_disables_existing_before_re_enable():
    plugin = _make_plugin()
    cfg = {"type": "stdio", "command": "codegraph", "args": ["serve", "--mcp"]}
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.mcp_server_runtime = {"codegraph": MagicMock()}
    mgr.disable_mcp_server = AsyncMock()
    mgr.enable_mcp_server = AsyncMock()

    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=cfg):
        with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
            asyncio.run(plugin._bootstrap_codegraph_mcp())

    mgr.disable_mcp_server.assert_awaited_once()
    assert mgr.disable_mcp_server.call_args.args[0] == "codegraph"
    mgr.enable_mcp_server.assert_awaited_once()


# ── terminate ──────────────────────────────────────


def test_terminate_cancels_pending_task():
    plugin = _make_plugin()

    async def never():
        await asyncio.sleep(100)

    async def setup_and_terminate():
        plugin._codegraph_task = asyncio.create_task(never())
        mgr = plugin.context.get_llm_tool_manager.return_value
        mgr.mcp_server_runtime = {}
        await plugin.terminate()
        return plugin._codegraph_task

    task = asyncio.run(setup_and_terminate())
    assert task.cancelled() or task.done()


def test_terminate_disables_mcp_when_registered():
    plugin = _make_plugin()
    plugin._codegraph_task = None
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.mcp_server_runtime = {"codegraph": MagicMock()}
    mgr.disable_mcp_server = AsyncMock()

    asyncio.run(plugin.terminate())

    mgr.disable_mcp_server.assert_awaited_once()
    assert mgr.disable_mcp_server.call_args.args[0] == "codegraph"
    assert mgr.disable_mcp_server.call_args.kwargs.get("timeout") == 15


def test_terminate_noop_when_not_registered():
    plugin = _make_plugin()
    plugin._codegraph_task = None
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.mcp_server_runtime = {}
    mgr.disable_mcp_server = AsyncMock()

    asyncio.run(plugin.terminate())

    mgr.disable_mcp_server.assert_not_called()


def test_terminate_handles_shutdown_timeout_warning(caplog):
    import logging
    from astrbot.core.provider.func_tool_manager import MCPShutdownTimeoutError

    plugin = _make_plugin()
    plugin._codegraph_task = None
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.mcp_server_runtime = {"codegraph": MagicMock()}
    mgr.disable_mcp_server = AsyncMock(
        side_effect=MCPShutdownTimeoutError(names=["codegraph"], timeout=15)
    )

    with caplog.at_level(logging.WARNING):
        asyncio.run(plugin.terminate())


# ── 旧实现清理验证 ──────────────────────────────


def test_old_codegraph_module_removed():
    """验证 tools/codegraph.py 和 tools/codegraph_cpp.py 不存在。"""
    pkg_dir = Path(__file__).resolve().parent.parent / "tools"
    assert not (pkg_dir / "codegraph.py").exists()
    assert not (pkg_dir / "codegraph_cpp.py").exists()


def test_main_no_longer_registers_code_index():
    """验证 main.py 不再 import codegraph 模块。"""
    main_path = Path(__file__).resolve().parent.parent / "main.py"
    content = main_path.read_text(encoding="utf-8")
    assert "from .tools import codegraph" not in content
    assert "from . import codegraph" not in content
    # CodeIndexTool/CodeExploreTool 类名不应再出现
    assert "class CodeIndexTool" not in content
    assert "class CodeExploreTool" not in content


def test_main_plugins_tools_no_old_tools():
    """验证 _PLUGINS_TOOLS 不含 CodeIndexTool/CodeExploreTool 实例。"""
    from astrbot_plugin_spcode_toolkit import main

    tools = main._PLUGINS_TOOLS
    names = [t.name for t in tools]
    assert "code_index" not in names
    assert "code_explore" not in names
