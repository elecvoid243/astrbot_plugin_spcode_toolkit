"""Task 5 — 优雅降级 shim 测试。

契约（详见 docs/superpowers/specs/2026-10-04-file-remove-core-integration-design.md §4.1）：
  - 核心提供内置 ``astrbot_file_remove``（registry 可查到类）→ 插件让位，
    ``FileRemoveTool`` 不出现在 ``add_llm_tools`` 注册列表，模块级布尔
    ``_CORE_PROVIDES_FILE_REMOVE`` 为 True；
  - 旧核心 registry 返回 None → 插件照常注册，布尔为 False；
  - 旧核心无 ``astrbot.core.tools.registry``（ImportError）→ 视作未提供，
    插件照常注册，布尔为 False。

测试通过真实构造 ``SPCodeToolkit``（mock context + 关闭 codegraph/vivado
异步启动）驱动 shim，观察 ``context.add_llm_tools`` 的实参。沿用
``tests/conftest.py`` 的 fake plugin fixture 模式与 ``test_fs_guard_tools.py``
的 monkeypatch 风格。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from tools.function_tools.file_remove import FileRemoveTool

TOOL_NAME = FileRemoveTool.name


def _build_plugin() -> tuple[object, list[str]]:
    """构造 SPCodeToolkit 并返回 (main 模块, 已注册工具名列表)。

    关闭 codegraph/vivado 以免 __init__ 里 ``asyncio.create_task`` 需要事件循环；
    ``enabled_tools`` 仅开启 file_remove，暴露 shim 的取舍。
    """
    from astrbot_plugin_spcode_toolkit import main as main_mod

    ctx = MagicMock()
    config = {
        "enabled_tools": [TOOL_NAME],
        "codegraph_enabled": False,
        "vivado_enabled": False,
    }
    main_mod.SPCodeToolkit(ctx, config=config)
    registered = (
        [t.name for t in ctx.add_llm_tools.call_args.args]
        if ctx.add_llm_tools.called
        else []
    )
    return main_mod, registered


def test_shim_skips_registration_when_core_provides(monkeypatch):
    """core registry 返回同名内置类 → 插件跳过 FileRemoveTool 注册。"""
    import astrbot.core.tools.registry as registry_mod

    fake_core_cls = type("_FakeCoreFileRemoveTool", (), {})
    monkeypatch.setattr(
        registry_mod, "get_builtin_tool_class", lambda name: fake_core_cls
    )

    main_mod, registered = _build_plugin()

    assert main_mod._CORE_PROVIDES_FILE_REMOVE is True
    assert TOOL_NAME not in registered


def test_shim_registers_when_core_missing(monkeypatch):
    """core registry 返回 None（旧核心无内置工具）→ 插件照常注册。"""
    import astrbot.core.tools.registry as registry_mod

    monkeypatch.setattr(registry_mod, "get_builtin_tool_class", lambda name: None)

    main_mod, registered = _build_plugin()

    assert main_mod._CORE_PROVIDES_FILE_REMOVE is False
    assert TOOL_NAME in registered


def test_shim_registers_when_registry_import_fails(monkeypatch):
    """更老核心无 ``astrbot.core.tools.registry``（ImportError）→ 视作未提供。"""
    # None in sys.modules 会让 ``from ... import ...`` 抛 ImportError。
    monkeypatch.setitem(sys.modules, "astrbot.core.tools.registry", None)

    main_mod, registered = _build_plugin()

    assert main_mod._CORE_PROVIDES_FILE_REMOVE is False
    assert TOOL_NAME in registered
