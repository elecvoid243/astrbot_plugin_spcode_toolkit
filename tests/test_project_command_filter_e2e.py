"""End-to-end test for /project command filters under real AstrBot CommandFilter.

WHY this file exists (2026-07-26):
- v2.21 之前 ``@project.command("load") def project_load(self, event, directory, *flags)``
  的 ``*flags`` 在 AstrBot ``CommandFilter.validate_and_convert_params`` 行为不友好:
  framework 会抛 ``TypeError: _empty() takes no arguments``,framework 看到
  ValueError 后会把整个 handler 跳过,waking_check 阶段把消息发给用户:
  "参数 flags 类型错误"。
- 本次 PR 改用 compact 插件的方案:子指令 handler 仅接 ``(self, event)``,
  内部从 ``event.get_message_str()`` 自己剥前缀、解析剩余 tokens。
- 本文件用 **真** ``astrbot.core.star.filter.command.CommandFilter``
  跑 init_handler_md + validate_and_convert_params 全套,确认:
  1. handler 签名 ``(self, event)`` 时,handler_params = {} (无 params 需要 parse)
  2. framework 不会抛任何异常
  3. parse 后 parsed_params = {}
  4. 子 filter 注册到 framework 后,framework 看到 handler_params = {},
     调 handler 时不传任何 keyword args
"""
from __future__ import annotations

import inspect
import sys
from unittest.mock import MagicMock

_PROJECT_PARENT = Path = __import__("pathlib").Path
_PROJECT_ROOT = _PROJECT_PARENT(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ── 直接 import 真实 CommandFilter(不走 conftest.py 的 stub)───
import importlib  # noqa: E402
import importlib.util  # noqa: E402


def _import_real_command_filter_module():
    """直接 import ``astrbot.core.star.filter.command``,绕开 conftest.py 的 stub。

    conftest.py 用 sys.modules hook 替换 ``astrbot.api.event.filter`` 等顶层
    namespace,但不会替换 ``astrbot.core.star.filter.command.CommandFilter``
    自身。这里直接 import 真实源文件。
    """
    # 先把 conftest 可能 stub 过的命名空间卸了,让真实 module 能 import
    for mod_name in (
        "astrbot",
        "astrbot.core",
        "astrbot.core.star",
        "astrbot.core.star.filter",
        "astrbot.core.star.filter.command",
    ):
        sys.modules.pop(mod_name, None)
    # 找源文件绝对路径
    from pathlib import Path as _P
    real_module = _P(r"F:\github\Astrbot\astrbot\core\star\filter\command.py")
    spec = importlib.util.spec_from_file_location(
        "astrbot.core.star.filter.command",
        str(real_module),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["astrbot.core.star.filter.command"] = module
    spec.loader.exec_module(module)
    return module


_command_module = _import_real_command_filter_module()
CommandFilter = _command_module.CommandFilter


# ── helper ─────────────────────────────────────────────────────


class _FakeStarHandlerMetadata:
    """``CommandFilter.init_handler_md`` 只读 ``.handler`` 属性。"""

    def __init__(self, handler):
        self.handler = handler


def _make_event(umo: str = "test:umo:e2e:1", message_str: str = ""):
    event = MagicMock()
    event.unified_msg_origin = umo
    event.plain_result = lambda x: f"RESULT: {x}"
    event.get_message_str = lambda: message_str
    event.is_at_or_wake_command = True
    event.set_extra = lambda k, v: event.__dict__.setdefault("extras", {}).__setitem__(k, v)
    event.get_extra = lambda k, default=None: event.__dict__.get("extras", {}).get(k, default)
    return event


# ── 真实 CommandFilter 路径测试 ──────────────────────────────────


class TestProjectLoadCommandFilterE2E:
    """真实 CommandFilter.init_handler_md + validate_and_convert_params:

    关键:验证 framework 不会抛 `TypeError: _empty() takes no arguments`
    (这是 v2.21 之前 bug 的 framework 报错点)。
    """

    def test_project_load_handler_has_empty_handler_params(self) -> None:
        """`project_load(self, event)` 的 handler_params 必须 = {},
        framework 才能成功 parse 否则会抛 TypeError。"""
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("load", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_load))

        # handler_params 必须空(否则 framework 会要求 ls[0] 是 directory 类型)
        assert cmd_filter.handler_params == {}, (
            f"project_load(self, event) 的 handler_params 应为空 dict,"
            f"实际: {dict(cmd_filter.handler_params)!r}"
        )

    def test_project_unload_handler_has_empty_handler_params(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("unload", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_unload))

        assert cmd_filter.handler_params == {}

    def test_project_status_handler_has_empty_handler_params(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("status", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_status))

        assert cmd_filter.handler_params == {}

    def test_validate_and_convert_returns_empty_for_no_remaining_tokens(self) -> None:
        """完整命令后无 token(例: '/project load')→ validate 不报错,
        返回空 dict。"""
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("load", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_load))

        # ls 是 framework 剥掉 "/project load" 前缀后剩下的部分
        result = cmd_filter.validate_and_convert_params([], cmd_filter.handler_params)
        assert result == {}

    def test_validate_and_convert_returns_empty_even_with_remaining_tokens(self) -> None:
        """关键回归 (v2.21 bug):即使 ls 里有剩余 token (如 ['/tmp/foo', 'no_agentsmd']),
        因为 handler_params = {}, framework 必须把整个 ls 吞掉、不报错。
        旧版会因 *flags (VAR_POSITIONAL) 进 handler_params 然后抛 TypeError。"""
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("load", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_load))

        # v2.21 bug: validate 会抛 TypeError("参数 flags 类型错误")
        # v2.21 fix: handler_params = {} 整个 ls 被跳过,返回 {}
        result = cmd_filter.validate_and_convert_params(
            ["/tmp/foo", "no_agentsmd"],
            cmd_filter.handler_params,
        )
        assert result == {}


class TestProjectLoadRealCommandFilterFullParse:
    """完整跑 CommandFilter.filter() 模拟 AstrBot framework 真实调度:
    - init_handler_md 一次
    - filter(event, cfg) 一次,模拟 waking_check 阶段 framework 调用
    - 验证 framework 不会抛 ValueError/TypeError
    - 验证 event.get_extra('parsed_params') == {}
    """

    def test_filter_does_not_raise_with_flag_in_message(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("load", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_load))

        # 模拟 framework: 完整 message_str 是 "project load /tmp/foo no_agentsmd",
        # CommandFilter.filter() 内部会剥掉 "project load" 前缀,剩 "/tmp/foo no_agentsmd"
        event = _make_event(message_str="project load /tmp/foo no_agentsmd")

        # 关键: 必须不抛任何异常(旧版会抛 ValueError("参数 flags 类型错误"))
        try:
            result = cmd_filter.filter(event, MagicMock())
        except (ValueError, TypeError) as e:
            raise AssertionError(
                f"framework 调 filter() 抛了 {type(e).__name__}: {e!r}。"
                f"这是 v2.21 之前 bug 的回归(由 *flags 触发)"
            ) from e

        # 完整命令匹配后,filter() 应返回 True
        assert result is True, "filter 应该返回 True 标记为已匹配"
        # parsed_params 是空 dict(因为 handler 没声明 params)
        assert event.get_extra("parsed_params") == {}, (
            f"parsed_params 应为 {{}},实际: {event.get_extra('parsed_params')!r}"
        )

    def test_filter_does_not_raise_with_only_directory(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("load", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_load))

        event = _make_event(message_str="project load /tmp/foo")
        result = cmd_filter.filter(event, MagicMock())

        assert result is True
        assert event.get_extra("parsed_params") == {}

    def test_filter_does_not_raise_with_no_remaining_tokens(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        cmd_filter = CommandFilter("load", parent_command_names=["project"])
        cmd_filter.init_handler_md(_FakeStarHandlerMetadata(_main_mod.SPCodeToolkit.project_load))

        event = _make_event(message_str="project load")
        result = cmd_filter.filter(event, MagicMock())

        assert result is True
        assert event.get_extra("parsed_params") == {}


class TestSignatureRegressionGuard:
    """显式 guard:project_load 的 signature 必须满足 framework 要求。

    约束:
    - 必须只有 ``self`` + ``event``(不多不少)
    - **不能**有 ``*args`` / ``*flags`` / ``**kwargs`` 之类 framework 不识别的形参
    - **不能**有无默认值的必填参数(framework 会要求 ls[i] 提供)

    如果有人未来 refactor 把 ``directory: str`` 加回签名,本测试会失败,
    提示需要采用「内部从 event 解析」方案。
    """

    def test_project_load_signature_is_just_self_event(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        sig = inspect.signature(_main_mod.SPCodeToolkit.project_load)
        param_names = list(sig.parameters.keys())
        assert param_names == ["self", "event"], (
            f"project_load 的形参必须是 ['self', 'event'],"
            f"实际: {param_names}。"
            f"添加任何形参都会让 AstrBot framework 的 CommandFilter 失败"
            f"(framework 不会识别 *args,会把 VAR_POSITIONAL 当成必填参数处理)。"
        )

    def test_project_unload_signature_is_just_self_event(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        sig = inspect.signature(_main_mod.SPCodeToolkit.project_unload)
        param_names = list(sig.parameters.keys())
        assert param_names == ["self", "event"]

    def test_project_status_signature_is_just_self_event(self) -> None:
        from astrbot_plugin_spcode_toolkit import main as _main_mod

        sig = inspect.signature(_main_mod.SPCodeToolkit.project_status)
        param_names = list(sig.parameters.keys())
        assert param_names == ["self", "event"]
