"""astrbot_file_remove_tool 启用时 LLM 提示词注入测试。"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# worktree 兼容性:main.py 用 `from .tools import` 相对导入,需要包上下文。
# 在 sys.modules 注册一个合成包,把 main.py 作为其子模块加载,以满足
# "from .tools import" 的解析需求。生产环境(被 AstrBot 以
# `astrbot_plugin_spcode_toolkit.main` 加载)走的是同一机制的真实版本。
_PKG_NAME = "_spcode_runtime_pkg"
if _PKG_NAME not in sys.modules:
    _pkg = types.ModuleType(_PKG_NAME)
    _pkg.__path__ = [str(ROOT)]
    sys.modules[_PKG_NAME] = _pkg
_spec = importlib.util.spec_from_file_location(
    f"{_PKG_NAME}.main", str(ROOT / "main.py")
)
_main = importlib.util.module_from_spec(_spec)
sys.modules[f"{_PKG_NAME}.main"] = _main
_spec.loader.exec_module(_main)
sys.modules["main"] = _main
_FILE_REMOVE_GUIDANCE_MARKER = _main._FILE_REMOVE_GUIDANCE_MARKER  # noqa: E402
SPCodeToolkit = _main.SPCodeToolkit


def _make_plugin(tool_enabled: bool) -> SPCodeToolkit:
    """最小 helper:实例化 SPCodeToolkit,设置 _tool_names 反映 enabled_tools 过滤结果。

    用 __new__ 跳过 __init__,避免触发 AstrBot 启动副作用(注册 filter / 加载 config)。
    """
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin._tool_names = {"astrbot_file_remove_tool"} if tool_enabled else set()
    return plugin


def _make_event(umo: str = "test:umo") -> MagicMock:
    ev = MagicMock()
    ev.unified_msg_origin = umo
    return ev


# ── 1. 工具启用时注入 ───────────────────────────────


def test_inject_when_tool_enabled():
    plugin = _make_plugin(tool_enabled=True)
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))

    assert _FILE_REMOVE_GUIDANCE_MARKER in req.system_prompt
    assert "base" in req.system_prompt  # 原文保留


# ── 2. 工具未启用时不注入 ───────────────────────────


def test_no_inject_when_tool_disabled():
    plugin = _make_plugin(tool_enabled=False)
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))

    assert req.system_prompt == "base"
    assert _FILE_REMOVE_GUIDANCE_MARKER not in req.system_prompt


# ── 3. 幂等性 ──────────────────────────────────────


def test_inject_idempotent():
    plugin = _make_plugin(tool_enabled=True)
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))
    first_len = len(req.system_prompt)
    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))
    second_len = len(req.system_prompt)

    assert first_len == second_len, (
        f"重复注入应被 marker 阻止,长度变化: {first_len} → {second_len}"
    )


# ── 4. system_prompt = None 时正确初始化 ──────────


def test_inject_handles_none_system_prompt():
    plugin = _make_plugin(tool_enabled=True)
    req = MagicMock()
    req.system_prompt = None

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))

    assert req.system_prompt is not None
    assert _FILE_REMOVE_GUIDANCE_MARKER in req.system_prompt
