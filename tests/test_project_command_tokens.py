"""Tests for SPCodeToolkit._extract_project_command_tokens + project_load framework dispatch.

WHY this file exists (2026-07-26):
- v2.21 之前 ``@project.command("load") def project_load(self, event, directory, *flags)``
  的 ``*flags`` 在 AstrBot ``CommandFilter.validate_and_convert_params`` 行为不友好:
  framework 会抛 ``TypeError: _empty() takes no arguments``,导致带 flag 的
  ``/project load <dir> no_agentsmd`` 不能正常 dispatch。
- 本次 PR 改用 compact 插件的方案:子指令 handler 仅接 ``(self, event)``,
  内部从 ``event.get_message_str()`` 自己剥前缀、解析剩余 tokens。
  本文件测试该解析器在以下场景下:
  1. 普通 token 解析(目录 + flag)
  2. 仅目录、仅 flag、空白
  3. 唤醒前缀 + 项目名前缀剥离
  4. 直接调用 ``project_load(event)`` 走 framework 入口到 ``load_impl``,
     验证 flag 真的被传递(回归 v2.21 之前的 bug)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ── 工厂 ───────────────────────────────────────────────────────────


def _make_plugin():
    """最小化 plugin 实例(不经过完整 ``__init__`` 流程)。"""
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": [],
    }
    plugin._codegraph_dir_locks = {}

    from tools.agentsmd import AgentsmdSubsystem
    from tools.project import ProjectManager

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **k: (True, ""),
    )
    plugin.project = ProjectManager(plugin)
    return plugin


def _make_event(umo: str = "test:umo:001", message_str: str = ""):
    """event mock:让 ``get_message_str()`` 返回指定字符串。"""
    event = MagicMock()
    event.unified_msg_origin = umo
    event.plain_result = lambda x: f"RESULT: {x}"
    event.get_message_str = lambda: message_str
    return event


def _drain(agen):
    return asyncio.run(_async_drain(agen))


async def _async_drain(agen):
    out = []
    async for msg in agen:
        out.append(msg)
    return out


# ── _extract_project_command_tokens 单元测试 ──────────────────────


class TestExtractProjectCommandTokens:
    """`_extract_project_command_tokens` 必须严格按设计剥前缀 + 分词。"""

    def test_normal_load_with_one_flag(self) -> None:
        """典型场景:`/project load /tmp/foo no_agentsmd` → ['/tmp/foo', 'no_agentsmd']"""
        event = _make_event(message_str="/project load /tmp/foo no_agentsmd")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == ["/tmp/foo", "no_agentsmd"]

    def test_load_with_both_flags(self) -> None:
        event = _make_event(message_str="/project load /tmp/x no_agentsmd no_codegraph")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == ["/tmp/x", "no_agentsmd", "no_codegraph"]

    def test_load_only_directory(self) -> None:
        event = _make_event(message_str="/project load /tmp/x")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == ["/tmp/x"]

    def test_load_only_flags_no_directory(self) -> None:
        event = _make_event(message_str="/project load no_agentsmd no_codegraph")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        # tokens 全是 flag,handle_subcommand 会报错
        assert tokens == ["no_agentsmd", "no_codegraph"]

    def test_load_empty_after_prefix(self) -> None:
        """`/project load` (无任何 tokens)→ []"""
        event = _make_event(message_str="/project load")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == []

    def test_load_only_whitespace_after_prefix(self) -> None:
        event = _make_event(message_str="/project load    ")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == []

    def test_load_with_excess_whitespace_between_tokens(self) -> None:
        """多余空格 / tab / 连续空格都要被正确分词。"""
        event = _make_event(message_str="/project load   /tmp/foo   no_agentsmd")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == ["/tmp/foo", "no_agentsmd"]

    def test_load_no_wake_prefix(self) -> None:
        """无 wake_prefix(私聊 / 直接调)也要正确处理。"""
        event = _make_event(message_str="project load /tmp/foo no_agentsmd")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == ["/tmp/foo", "no_agentsmd"]

    def test_load_case_insensitive_prefix(self) -> None:
        """`PROJECT LOAD` 大写也要能识别。"""
        event = _make_event(message_str="/PROJECT LOAD /tmp/foo no_agentsmd")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == ["/tmp/foo", "no_agentsmd"]

    def test_status_returns_empty(self) -> None:
        """`/project status` 无参子命令 → []"""
        event = _make_event(message_str="/project status")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "status")
        assert tokens == []

    def test_unload_returns_empty(self) -> None:
        event = _make_event(message_str="/project unload")
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "unload")
        assert tokens == []

    def test_load_message_get_message_str_returns_none(self) -> None:
        """防御:`event.get_message_str()` 返回 None → 走 fallback → []"""
        event = _make_event()
        event.get_message_str = lambda: None
        tokens = SPCodeToolkit._extract_project_command_tokens(event, "load")
        assert tokens == []


# ── project_load framework 入口回归测试 ──────────────────────────


class TestProjectLoadFrameworkEntry:
    """验证 `project_load(event)` 装饰方法(framework 入口)真的把
    flag 传给 `load_impl` —— 这正是 v2.21 之前 bug 的根因。

    测试通过构造一个真实 event 包含 message_str,然后调
    `plugin.project_load(event)`,捕获 `load_impl` 的 keyword args,
    验证 `no_agentsmd` / `no_codegraph` 是否被正确解析。
    """

    def test_load_with_no_agentsmd_flag_passes_to_load_impl(
        self, tmp_path, monkeypatch
    ) -> None:
        """`/project load <p> no_agentsmd` → load_impl 的 no_agentsmd=True。"""
        p = tmp_path / "proj"
        p.mkdir()

        captured = {}

        async def _fake_load_impl(self, event, directory, *, no_agentsmd=False, no_codegraph=False):
            captured["directory"] = directory
            captured["no_agentsmd"] = no_agentsmd
            captured["no_codegraph"] = no_codegraph
            yield "fake done"

        # 用 monkeypatch 替换 plugin.project.load_impl
        plugin = _make_plugin()
        from tools.project import ProjectManager
        monkeypatch.setattr(ProjectManager, "load_impl", _fake_load_impl)

        event = _make_event(
            umo="test:umo:framework:1",
            message_str=f"/project load {p} no_agentsmd",
        )

        msgs = _drain(plugin.project_load(event))
        assert msgs == ["fake done"]
        assert captured["directory"] == str(p)
        assert captured["no_agentsmd"] is True
        assert captured["no_codegraph"] is False

    def test_load_with_both_flags_passes_to_load_impl(
        self, tmp_path, monkeypatch
    ) -> None:
        """`/project load <p> no_agentsmd no_codegraph` → 两个 flag 都为 True。"""
        p = tmp_path / "proj"
        p.mkdir()

        captured = {}

        async def _fake_load_impl(self, event, directory, *, no_agentsmd=False, no_codegraph=False):
            captured["directory"] = directory
            captured["no_agentsmd"] = no_agentsmd
            captured["no_codegraph"] = no_codegraph
            yield "fake done"

        plugin = _make_plugin()
        from tools.project import ProjectManager
        monkeypatch.setattr(ProjectManager, "load_impl", _fake_load_impl)

        event = _make_event(
            umo="test:umo:framework:2",
            message_str=f"/project load {p} no_agentsmd no_codegraph",
        )
        _drain(plugin.project_load(event))

        assert captured["no_agentsmd"] is True
        assert captured["no_codegraph"] is True

    def test_load_with_no_flag_passes_defaults(self, tmp_path, monkeypatch) -> None:
        """`/project load <p>` (无 flag) → 两个 flag 都为 False。"""
        p = tmp_path / "proj"
        p.mkdir()

        captured = {}

        async def _fake_load_impl(self, event, directory, *, no_agentsmd=False, no_codegraph=False):
            captured["directory"] = directory
            captured["no_agentsmd"] = no_agentsmd
            captured["no_codegraph"] = no_codegraph
            yield "fake done"

        plugin = _make_plugin()
        from tools.project import ProjectManager
        monkeypatch.setattr(ProjectManager, "load_impl", _fake_load_impl)

        event = _make_event(
            umo="test:umo:framework:3",
            message_str=f"/project load {p}",
        )
        _drain(plugin.project_load(event))

        assert captured["directory"] == str(p)
        assert captured["no_agentsmd"] is False
        assert captured["no_codegraph"] is False

    def test_load_only_flags_yields_error_and_does_not_call_load_impl(
        self, monkeypatch
    ) -> None:
        """`/project load no_agentsmd` (没 directory) → handle_subcommand
        报错,不调 load_impl。"""
        called = {"count": 0}

        async def _fake_load_impl(self, event, directory, *, no_agentsmd=False, no_codegraph=False):
            called["count"] += 1
            yield "should not happen"

        plugin = _make_plugin()
        from tools.project import ProjectManager
        monkeypatch.setattr(ProjectManager, "load_impl", _fake_load_impl)

        event = _make_event(
            umo="test:umo:framework:4",
            message_str="/project load no_agentsmd",
        )
        msgs = _drain(plugin.project_load(event))

        assert called["count"] == 0, "load_impl 不该被调(directory 缺失)"
        # handle_subcommand 的错误消息
        assert any("需要 <directory>" in m for m in msgs), f"应提示需要 directory,实际: {msgs}"

    def test_load_with_flag_in_middle_of_directory_path_is_directory_part(
        self, tmp_path, monkeypatch
    ) -> None:
        """`/project load /tmp/no_agentsmd no_codegraph` → directory 是
        '/tmp/no_agentsmd' (flag 不能匹配 directory 的中间部分)。"""
        p = tmp_path / "no_agentsmd"  # 文件名本身就叫 no_agentsmd
        p.mkdir()

        captured = {}

        async def _fake_load_impl(self, event, directory, *, no_agentsmd=False, no_codegraph=False):
            captured["directory"] = directory
            captured["no_agentsmd"] = no_agentsmd
            captured["no_codegraph"] = no_codegraph
            yield "ok"

        plugin = _make_plugin()
        from tools.project import ProjectManager
        monkeypatch.setattr(ProjectManager, "load_impl", _fake_load_impl)

        event = _make_event(
            umo="test:umo:framework:5",
            message_str=f"/project load {p} no_codegraph",
        )
        _drain(plugin.project_load(event))

        assert captured["directory"] == str(p)
        assert captured["no_agentsmd"] is False
        assert captured["no_codegraph"] is True
