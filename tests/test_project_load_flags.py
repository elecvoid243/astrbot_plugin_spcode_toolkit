"""/project load 的 no_agentsmd / no_codegraph flag 单元测试(2026-07-25)。

Author: elecvoid243

覆盖场景:
- 默认(无 flag):3 个子步骤全部调用(set_project 不参与 load)
- no_agentsmd:跳过 agentsmd.init / agentsmd.load,codegraph 子步骤仍调用
- no_codegraph:跳过 codegraph.init,agentsmd 子步骤仍调用
- 两个 flag 同时给:都跳过,但 state.put 仍发生(空壳 load)
- 路由层 (handle_subcommand) 解析 args 中的 flag
- 路由层:只有 flag 没 directory → 报错
- 路由层:flag 位置在 directory 之前/之后都能解析
- feature flag 关闭 + no_xxx flag 的解耦行为
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# 复用 test_project_subcommand 的 sys.path 注入约定
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402
from tools.project import state as _proj_state  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ── 工厂 / 辅助函数(独立实现,不依赖 test_project_subcommand 的 helper) ──


def _make_event(umo: str = "test:flag:umo:001"):
    event = MagicMock()
    event.unified_msg_origin = umo
    # 返回字符串而非 MER,方便测试做 ``"".join`` 校验
    event.plain_result = lambda x: x
    return event


def _make_plugin(
    *,
    agentsmd_enabled: bool = True,
    codegraph_enabled: bool = True,
):
    """最小化 plugin 实例 + 子系统装配。"""
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    plugin._config = {
        "agentsmd_enabled": agentsmd_enabled,
        "codegraph_enabled": codegraph_enabled,
        "codegraph_project": "",
        "file_remove_blacklist": None,
    }
    # 路径安全直接放行(本文件测的是 flag 分支,不是路径安全)
    from tools.agentsmd import AgentsmdSubsystem

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **kw: (True, ""),
    )
    from tools.project import ProjectManager

    plugin.project = ProjectManager(plugin)
    return plugin


def _patch_substeps_count(plugin) -> dict[str, int]:
    """把 3 个子步骤替换为带调用计数的成功 async gen。

    返回 ``call_counts`` 字典,key 为 ``"<子系统>.<方法>"``,value 为累计调用次数。
    测试通过 ``call_counts["agentsmd.init"] == 0`` 断言"该子步骤被跳过"。

    2026-08-15: codegraph.set_project 已不在 load 流水线中, 但保留其计数
    (恒为 0), 作为"set 永不参与 load"的不变量断言。

    设计:
    - 用 ``functools.partial`` 风格的闭包维护计数,避免 ``MagicMock`` 计数
      与 ``side_effect`` 互相覆盖导致行为不一致
    - 每次调用产生一个新的 async gen(``_ok`` 是 generator factory),保证
      ``async for msg in sub_gen:`` 拿到的是新鲜 generator
    """
    call_counts: dict[str, int] = {
        "agentsmd.init": 0,
        "agentsmd.load": 0,
        "codegraph.init": 0,
        "codegraph.set_project": 0,
    }

    def _make_counted(name: str):
        """返回 factory:每次调用 +1 计数 + 返回一个 yield 一次的 async gen。"""

        def _factory(*args, **kwargs):
            call_counts[name] += 1

            async def _gen():
                yield "mock-substep-ok"

            return _gen()

        return _factory

    setattr(plugin.agentsmd, "init", _make_counted("agentsmd.init"))
    setattr(plugin.agentsmd, "load", _make_counted("agentsmd.load"))
    setattr(plugin.codegraph, "init", _make_counted("codegraph.init"))
    setattr(plugin.codegraph, "set_project", _make_counted("codegraph.set_project"))

    return call_counts


def _run(coro):
    return asyncio.run(coro)


async def _drive(plugin, event, sub_command, *args):
    msgs = []
    async for msg in plugin.project.handle_subcommand(event, sub_command, *args):
        msgs.append(msg)
    return msgs


# ── 测试用例 ───────────────────────────────────────────


def test_default_load_calls_all_3_substeps():
    """无 flag → 3 个子步骤全部调用一次,state 登记成功。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:default:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x"))
    text = "".join(msgs)

    # 3 个子步骤都被调用 1 次;set_project 不参与 load(恒 0)
    assert counters["agentsmd.init"] == 1
    assert counters["agentsmd.load"] == 1
    assert counters["codegraph.init"] == 1
    assert counters["codegraph.set_project"] == 0

    # 总结消息包含成功标记
    assert "✅ 项目已加载" in text
    # 总结消息列出 3 个标准步骤(未跳过任何)
    assert "AGENTS.md 注入到 system_prompt" in text
    assert "载入 codegraph 索引" in text

    # state 已登记
    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["directory"] == str(Path("/tmp/x").resolve())

    _proj_state.reset()


def test_no_agentsmd_skips_agentsmd_substeps_only():
    """no_agentsmd → agentsmd 子步骤全部跳过,codegraph 子步骤仍执行。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:no_agentsmd:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x", "no_agentsmd"))
    text = "".join(msgs)

    # agentsmd 子步骤都被跳过
    assert counters["agentsmd.init"] == 0
    assert counters["agentsmd.load"] == 0
    # codegraph 子步骤仍执行(仅 init;set 不参与 load)
    assert counters["codegraph.init"] == 1
    assert counters["codegraph.set_project"] == 0

    # 跳过消息 + 成功消息
    assert "⏭️" in text
    assert "AGENTS.md 步骤已跳过" in text
    assert "✅ 项目已加载" in text
    # 总结应明确标注"已跳过 AGENTS.md 注入"
    assert "已跳过" in text
    assert "AGENTS.md" in text

    # state 仍登记
    assert _proj_state.get(event.unified_msg_origin) is not None
    _proj_state.reset()


def test_no_codegraph_skips_codegraph_substeps_only():
    """no_codegraph → codegraph 子步骤全部跳过,agentsmd 子步骤仍执行。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:no_codegraph:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x", "no_codegraph"))
    text = "".join(msgs)

    # codegraph 子步骤都被跳过(init;set 本就 0)
    assert counters["codegraph.init"] == 0
    assert counters["codegraph.set_project"] == 0
    # agentsmd 子步骤仍执行
    assert counters["agentsmd.init"] == 1
    assert counters["agentsmd.load"] == 1

    assert "⏭️" in text
    assert "codegraph 步骤已跳过" in text
    assert "✅ 项目已加载" in text
    # 总结应明确标注"已跳过 codegraph 索引"
    assert "已跳过" in text
    assert "codegraph" in text.lower()

    assert _proj_state.get(event.unified_msg_origin) is not None
    _proj_state.reset()


def test_both_flags_loaded_as_empty_shell():
    """两个 flag 都给 → 空壳 load,仅做路径校验 + state 登记。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:both:001")
    msgs = _run(
        _drive(
            plugin,
            event,
            "load",
            "/tmp/x",
            "no_agentsmd",
            "no_codegraph",
        )
    )
    text = "".join(msgs)

    # 3 个子步骤全部 0 次
    assert counters["agentsmd.init"] == 0
    assert counters["agentsmd.load"] == 0
    assert counters["codegraph.init"] == 0
    assert counters["codegraph.set_project"] == 0

    # 两条跳过消息 + 成功消息
    assert text.count("⏭️") >= 2
    assert "✅ 项目已加载" in text
    # 空壳 load 的总结应说明"无子步骤"
    assert "无子步骤" in text

    # state 仍登记(空壳也是有状态的)
    assert _proj_state.get(event.unified_msg_origin) is not None
    _proj_state.reset()


def test_no_agentsmd_overrides_disabled_agentsmd_feature_flag():
    """agentsmd_enabled=False + no_agentsmd=True → 不再被 feature flag 阻拦。

    设计意图:用户显式 opt-out 跳过该步骤,即视为不依赖该功能,
    不应该被全局开关"该功能未启用"错误阻挡。
    """
    plugin = _make_plugin(agentsmd_enabled=False)
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:no_agentsmd:override:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x", "no_agentsmd"))
    text = "".join(msgs)

    # 不应该被 feature flag 拦下
    assert "未启用" not in text
    assert "✅ 项目已加载" in text
    # codegraph 子步骤仍执行
    assert counters["codegraph.init"] == 1
    _proj_state.reset()


def test_no_codegraph_overrides_disabled_codegraph_feature_flag():
    """codegraph_enabled=False + no_codegraph=True → 不再被 feature flag 阻拦。"""
    plugin = _make_plugin(codegraph_enabled=False)
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:no_codegraph:override:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x", "no_codegraph"))
    text = "".join(msgs)

    assert "未启用" not in text
    assert "✅ 项目已加载" in text
    # agentsmd 子步骤仍执行
    assert counters["agentsmd.init"] == 1
    _proj_state.reset()


def test_both_features_disabled_without_flags_is_blocked():
    """agentsmd_enabled=False + codegraph_enabled=False + 不传 flag → 仍应报错。

    这是与"显式 opt-out 跳过"的关键区别:用户没表态,就该被全局开关拦下。
    """
    plugin = _make_plugin(agentsmd_enabled=False, codegraph_enabled=False)
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:both_disabled:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x"))
    text = "".join(msgs)

    # 拒绝 + 没有任何子步骤被调用
    assert "❌" in text
    assert "未启用" in text
    assert counters["agentsmd.init"] == 0
    assert counters["codegraph.init"] == 0
    # state 也不应登记(被 feature flag 拦下,未进入后续流程)
    assert _proj_state.get(event.unified_msg_origin) is None
    _proj_state.reset()


def test_flag_before_directory_still_resolves():
    """路由层:flag 放在 directory 之前也能正确解析(只看集合,不看顺序)。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:order:001")
    msgs = _run(_drive(plugin, event, "load", "no_agentsmd", "/tmp/x"))
    text = "".join(msgs)

    assert "✅ 项目已加载" in text
    assert counters["agentsmd.init"] == 0
    assert counters["codegraph.init"] == 1
    assert _proj_state.get(event.unified_msg_origin) is not None
    _proj_state.reset()


def test_only_flags_without_directory_reports_error():
    """路由层:只传 flag 不传 directory → 报错,不进入 load 流程。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:only_flags:001")
    msgs = _run(
        _drive(
            plugin,
            event,
            "load",
            "no_agentsmd",
            "no_codegraph",
        )
    )
    text = "".join(msgs)

    assert "❌" in text
    assert "directory" in text.lower() or "参数" in text
    # 不应触发任何子步骤
    assert counters["agentsmd.init"] == 0
    assert counters["agentsmd.load"] == 0
    assert counters["codegraph.init"] == 0
    assert counters["codegraph.set_project"] == 0
    # state 不应登记
    assert _proj_state.get(event.unified_msg_origin) is None
    _proj_state.reset()


def test_duplicate_flag_is_idempotent():
    """重复传同一个 flag → 行为不变(set 去重,无害)。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:dup:001")
    msgs = _run(
        _drive(
            plugin,
            event,
            "load",
            "/tmp/x",
            "no_agentsmd",
            "no_agentsmd",
        )
    )
    text = "".join(msgs)

    assert "✅ 项目已加载" in text
    assert counters["agentsmd.init"] == 0
    assert counters["codegraph.init"] == 1
    _proj_state.reset()


def test_load_impl_direct_keyword_args():
    """直接调 load_impl(关键字参数)也能正确生效(便于 main.py 之外的调用方)。"""
    plugin = _make_plugin()
    counters = _patch_substeps_count(plugin)
    event = _make_event("test:direct:001")

    async def _drive_impl():
        msgs = []
        async for msg in plugin.project.load_impl(
            event,
            "/tmp/x",
            no_agentsmd=True,
            no_codegraph=False,
        ):
            msgs.append(msg)
        return msgs

    msgs = _run(_drive_impl())
    text = "".join(msgs)

    assert "✅ 项目已加载" in text
    assert counters["agentsmd.init"] == 0
    assert counters["agentsmd.load"] == 0
    assert counters["codegraph.init"] == 1
    assert counters["codegraph.set_project"] == 0
    _proj_state.reset()


# ── state.skipped_substeps 字段(2026-07-25) ─────────


def test_state_default_load_records_empty_skipped_substeps():
    """默认 load(无 flag)→ state.skipped_substeps = set()(空集合)。"""
    plugin = _make_plugin()
    _patch_substeps_count(plugin)
    event = _make_event("test:state:default:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x"))
    assert "✅ 项目已加载" in "".join(msgs)

    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["skipped_substeps"] == set()
    _proj_state.reset()


def test_state_no_agentsmd_records_agentsmd_in_skipped_substeps():
    """no_agentsmd → state.skipped_substeps = {"agentsmd"}。"""
    plugin = _make_plugin()
    _patch_substeps_count(plugin)
    event = _make_event("test:state:no_agentsmd:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x", "no_agentsmd"))
    assert "✅ 项目已加载" in "".join(msgs)

    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["skipped_substeps"] == {"agentsmd"}
    # codegraph 不应被标记为跳过
    assert "codegraph" not in info["skipped_substeps"]
    _proj_state.reset()


def test_state_no_codegraph_records_codegraph_in_skipped_substeps():
    """no_codegraph → state.skipped_substeps = {"codegraph"}。"""
    plugin = _make_plugin()
    _patch_substeps_count(plugin)
    event = _make_event("test:state:no_codegraph:001")
    msgs = _run(_drive(plugin, event, "load", "/tmp/x", "no_codegraph"))
    assert "✅ 项目已加载" in "".join(msgs)

    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["skipped_substeps"] == {"codegraph"}
    assert "agentsmd" not in info["skipped_substeps"]
    _proj_state.reset()


def test_state_both_flags_records_both_in_skipped_substeps():
    """两个 flag 都给 → state.skipped_substeps = {"agentsmd", "codegraph"}。"""
    plugin = _make_plugin()
    _patch_substeps_count(plugin)
    event = _make_event("test:state:both:001")
    msgs = _run(
        _drive(
            plugin,
            event,
            "load",
            "/tmp/x",
            "no_agentsmd",
            "no_codegraph",
        )
    )
    assert "✅ 项目已加载" in "".join(msgs)

    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["skipped_substeps"] == {"agentsmd", "codegraph"}
    _proj_state.reset()


# ── _project_inject_codegraph_guidance 钩子行为 ────────


def _make_plugin_for_guidance_hook() -> SPCodeToolkit:
    """构造一个能调 ``_project_inject_codegraph_guidance`` 的 plugin 实例。

    该方法是 SPCodeToolkit 上的 @filter.on_llm_request 钩子,需 ``self._config``
    和 ``self.get_loaded_project``(内部走 ``self.project.get_loaded_project``)
    可用。``@filter.on_llm_request`` 装饰器由 conftest stub 化为 identity,
    所以方法本身是普通 async 函数,可直接 await。
    """
    plugin = _make_plugin()  # 复用 test_project_load_flags 的轻量 plugin
    # _make_plugin 已装配 plugin.project,所以 get_loaded_project 可用
    return plugin


def _make_provider_request(system_prompt: str = ""):
    """构造一个足够像 ProviderRequest 的对象,只关心 system_prompt 字段。"""
    req = MagicMock()
    req.system_prompt = system_prompt
    return req


def test_guidance_hook_no_inject_when_codegraph_skipped():
    """codegraph 显式跳过 → 钩子不应把 # Use Codegraph 提示注入到 system_prompt。

    这是用户提出问题后的核心修复:no_codegraph 时,LLM 不应被告知
    "请用 codegraph_*"(否则会去调不存在的工具)。
    """
    plugin = _make_plugin_for_guidance_hook()
    umo = "test:hook:no_codegraph:001"
    # 模拟:用户刚执行过 no_codegraph 的 /project load,state 已被写入
    _proj_state.put(
        umo,
        {
            "directory": "/tmp/x",
            "loaded_at": 0.0,
            "skipped_substeps": {"codegraph"},
        },
    )
    event = _make_event(umo)
    req = _make_provider_request("")

    try:
        _run(plugin._project_inject_codegraph_guidance(event, req))

        # 关键断言:system_prompt 不应含 codegraph 提示的 marker
        assert "Use Codegraph" not in (req.system_prompt or "")
        # 且 inject_guidance 根本没追加任何东西
        assert (req.system_prompt or "") == ""
    finally:
        _proj_state.reset()


def test_guidance_hook_injects_when_codegraph_not_skipped():
    """默认 load(codegraph 实际启动)→ 钩子正常注入 # Use Codegraph 提示。

    这是回归测试,确保新加的 skipped_substeps 检查不会影响默认行为。
    """
    plugin = _make_plugin_for_guidance_hook()
    umo = "test:hook:default:001"
    _proj_state.put(
        umo,
        {
            "directory": "/tmp/x",
            "loaded_at": 0.0,
            "skipped_substeps": set(),  # 默认:无跳过
        },
    )
    event = _make_event(umo)
    req = _make_provider_request("")

    try:
        _run(plugin._project_inject_codegraph_guidance(event, req))

        # 默认情况应正常注入
        assert "Use Codegraph" in (req.system_prompt or "")
    finally:
        _proj_state.reset()


def test_guidance_hook_agentsmd_skip_still_injects_codegraph():
    """仅 no_agentsmd(skip 了 agentsmd) → codegraph 提示仍应注入。

    skipped_substeps 只含 "agentsmd",不影响 "codegraph" 提示。
    """
    plugin = _make_plugin_for_guidance_hook()
    umo = "test:hook:no_agentsmd:001"
    _proj_state.put(
        umo,
        {
            "directory": "/tmp/x",
            "loaded_at": 0.0,
            "skipped_substeps": {"agentsmd"},  # 只跳过 agentsmd
        },
    )
    event = _make_event(umo)
    req = _make_provider_request("")

    try:
        _run(plugin._project_inject_codegraph_guidance(event, req))

        # codegraph 仍应注入(因为没被跳过)
        assert "Use Codegraph" in (req.system_prompt or "")
    finally:
        _proj_state.reset()


def test_guidance_hook_no_inject_when_no_project_loaded():
    """umo 没加载 project → 钩子不注入(原有行为,回归测试)。"""
    plugin = _make_plugin_for_guidance_hook()
    umo = "test:hook:no_loaded:001"
    event = _make_event(umo)
    req = _make_provider_request("")

    # state 是空的(没有 put 过任何 umo)
    _run(plugin._project_inject_codegraph_guidance(event, req))

    assert (req.system_prompt or "") == ""
    _proj_state.reset()


def test_guidance_hook_no_inject_when_codegraph_feature_disabled():
    """codegraph_enabled=False → 钩子不注入(原有行为,回归测试)。"""
    plugin = _make_plugin(codegraph_enabled=False)
    umo = "test:hook:feat_disabled:001"
    _proj_state.put(
        umo,
        {
            "directory": "/tmp/x",
            "loaded_at": 0.0,
            "skipped_substeps": set(),
        },
    )
    event = _make_event(umo)
    req = _make_provider_request("")

    try:
        _run(plugin._project_inject_codegraph_guidance(event, req))

        # 全局开关关闭 → 不注入
        assert (req.system_prompt or "") == ""
    finally:
        _proj_state.reset()
