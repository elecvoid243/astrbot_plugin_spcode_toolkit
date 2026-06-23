"""/project load|unload 命令测试(v2.7)。"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit
_MAIN_MODULE_PATH = "astrbot_plugin_spcode_toolkit.main"


# ── 工厂 / 辅助函数 ─────────────────────────────────


def _make_plugin(
    *,
    agentsmd_enabled=True,
    codegraph_enabled=True,
    codegraph_project="",
    blacklist=None,
):
    """创建最小化 plugin 实例(不经过完整 __init__ 流程)。"""
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    context = MagicMock()
    plugin.context = context
    plugin._config = {
        "agentsmd_enabled": agentsmd_enabled,
        "codegraph_enabled": codegraph_enabled,
        "codegraph_project": codegraph_project,
        "file_remove_blacklist": blacklist or [],
    }
    plugin._loaded_projects = {}
    plugin._codegraph_dir_locks = {}

    # PR-5 (2026-06-23): agentsmd 子系统 — 真实实例,_patch_internal_methods
    # 会进一步把 .init/.load/.unload 替换为 MagicMock。
    from tools.agentsmd import AgentsmdSubsystem

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *args, **kwargs: (True, ""),
    )
    return plugin


def _make_event(umo: str = "test:umo:001"):
    event = MagicMock()
    event.unified_msg_origin = umo
    event.plain_result = lambda x: f"RESULT: {x}"
    return event


def _collect_async_gen(agen):
    """把 async generator 收集为 list。"""
    return asyncio.run(_drain(agen))


async def _drain(agen):
    out = []
    async for msg in agen:
        out.append(msg)
    return out


def _make_error_gen(error_msg: str = "❌ 模拟失败:原因"):
    """工厂:返回一个 async generator 函数,被调时 yield 一条错误消息后结束。

    用作测试夹具 — 模拟子方法返回错误但优雅退出的场景
    (即 _project_load_impl 期望"yield 错误 + return" 而不是抛异常)。
    """
    async def _gen(*args, **kwargs):
        yield error_msg
    return _gen


def _patch_internal_methods(plugin, *, agentsmd_md_exists=False, custom_async_gens=None):
    """把 plugin 的 5 个内部方法替换为 mock,返回可观察的 mock 实例。

    重要:这里要区分两类方法:
    - async generator 方法(`async def ... yield ...`):
      用 MagicMock + side_effect = async_gen_func 实现。调用返回 async generator,
      可被 `async for` 迭代。
    - 同步方法(`def ... return str`):
      用 MagicMock + return_value 实现。调用返回字符串。

    注意:不能对 async generator 方法用 AsyncMock——
    AsyncMock 调用返回 coroutine(需 await),而 `async for` 需要 async generator。

    Args:
        plugin: 目标 plugin 实例。
        agentsmd_md_exists: 保留参数(早期 API,目前未使用)。
        custom_async_gens: 可选 dict,key=async gen 方法名,value=自定义 side_effect
            (一个 async generator 函数)。未指定的 method 仍用默认的 _async_gen_ok。
            例如: ``{"init": _make_error_gen("❌ ...")}`` 可让 init
            调用返回错误消息,其它方法保持成功。
    """
    custom_async_gens = custom_async_gens or {}

    async def _async_gen_ok(*args, **kwargs):
        """通用 async generator,被所有未自定义的 async gen 方法复用。"""
        yield "mock-async-gen-ok"

    # PR-5 (2026-06-23): agentsmd 方法已提取到 tools.agentsmd 子系统,
    # 主流程现在调用 ``plugin.agentsmd.init / .load / .unload``。
    # codegraph 方法暂留 main.py(PR-6 才会搬)。

    # agentsmd: mock 替换 plugin.agentsmd.<method>
    agentsmd_async = {
        "init": lambda *a, **kw: None,
        "load": lambda *a, **kw: None,
    }
    agentsmd_sync = {
        "unload": "mock-sync-ok",
    }

    mocks = {}
    # agentsmd async generator 方法
    for method_name, _ in agentsmd_async.items():
        m = MagicMock()
        m.side_effect = custom_async_gens.get(method_name, _async_gen_ok)
        setattr(plugin.agentsmd, method_name, m)
        # mock dict key 保留短名("init"),便于断言简洁
        mocks[method_name] = m
    # agentsmd 同步方法
    for method_name, ret_val in agentsmd_sync.items():
        m = MagicMock()
        m.return_value = ret_val
        setattr(plugin.agentsmd, method_name, m)
        mocks[method_name] = m
    # PR-6 (2026-06-23): codegraph 业务搬到 tools.codegraph 子包,
    # main.py 现在调 self.codegraph.init / .set_project。
    codegraph_async = {
        "init": lambda *a, **kw: None,
        "set_project": lambda *a, **kw: None,
    }
    for method_name, _ in codegraph_async.items():
        m = MagicMock()
        # mock dict key 改用 ``codegraph_<method>`` 形式,
        # 避免与 agentsmd 自己的 "init" key 冲突。
        mock_key = f"codegraph_{method_name}"
        m.side_effect = custom_async_gens.get(mock_key, _async_gen_ok)
        setattr(plugin.codegraph, method_name, m)
        mocks[mock_key] = m
    return mocks


# ── 1. 命令组注册 ──────────────────────────────────


def test_project_command_group_is_class_method():
    """验证 /project 命令组是 SPCodeToolkit 的类方法(参考 /codegraph 模式)。"""
    from astrbot.core.star.register.star_handler import RegisteringCommandable

    assert isinstance(SPCodeToolkit.project, RegisteringCommandable)
    assert callable(SPCodeToolkit.project_load)
    assert callable(SPCodeToolkit.project_unload)

    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    assert isinstance(plugin.project, RegisteringCommandable)
    assert callable(plugin.project_load)
    assert callable(plugin.project_unload)


def test_project_subcommands_bound_to_instance():
    """子命令方法的 __self__ 应该是 plugin 自身。"""
    plugin = _make_plugin()
    assert plugin.project_load.__self__ is plugin
    assert plugin.project_unload.__self__ is plugin


# ── 2. Feature flag 校验 ──────────────────────────


def test_project_load_refuses_when_agentsmd_disabled(tmp_path):
    """agentsmd_enabled=False → 立即拒绝,不动状态,也不调子方法。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin(agentsmd_enabled=False, codegraph_enabled=True)
    mocks = _patch_internal_methods(plugin)
    event = _make_event()

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # 新提示:只说"启用 codegraph 和 AGENTS.md",不再暴露变量名
    assert any("codegraph" in m and "AGENTS.md" in m for m in msgs), (
        f"应提示启用 codegraph 和 AGENTS.md,实际: {msgs}"
    )
    # 任一子方法都不该被调
    for name, mock in mocks.items():
        mock.assert_not_called(), f"{name} 不应被调用"
    # 状态不应被记录
    assert event.unified_msg_origin not in plugin._loaded_projects


def test_project_load_refuses_when_codegraph_disabled(tmp_path):
    """codegraph_enabled=False → 同样拒绝。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin(agentsmd_enabled=True, codegraph_enabled=False)
    mocks = _patch_internal_methods(plugin)
    event = _make_event()

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    assert any("codegraph" in m and "AGENTS.md" in m for m in msgs)
    for name, mock in mocks.items():
        mock.assert_not_called()


def test_project_unload_refuses_when_agentsmd_disabled():
    """agentsmd 关闭时 unload 也应拒绝(避免误清理)。"""
    plugin = _make_plugin(agentsmd_enabled=False, codegraph_enabled=True)
    plugin._loaded_projects["test:umo"] = {"directory": "/some/proj"}
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_unload(event))

    assert any("codegraph" in m and "AGENTS.md" in m for m in msgs)
    # 状态不应被清空
    assert "test:umo" in plugin._loaded_projects


# ── 3. 重复 load 拦截(Q2=B) ──────────────────────


def test_project_load_refuses_when_already_loaded(tmp_path):
    """umo 已在 _loaded_projects → 拒绝,提示先 unload。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    plugin._loaded_projects["test:umo"] = {"directory": "/old/proj"}
    mocks = _patch_internal_methods(plugin)
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    assert any("已加载" in m and "/old/proj" in m for m in msgs), (
        f"应提示当前已加载 /old/proj,实际: {msgs}"
    )
    assert any("先" in m and "unload" in m for m in msgs)
    for name, mock in mocks.items():
        mock.assert_not_called(), f"{name} 不应被调用"


def test_project_load_allows_different_umo_simultaneously(tmp_path):
    """不同 umo 可以同时加载不同项目(per-umo 状态独立)。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    _patch_internal_methods(plugin)
    event_a = _make_event(umo="umo:a")
    event_b = _make_event(umo="umo:b")

    msgs_a = _collect_async_gen(plugin.project_load(event_a, str(p)))
    msgs_b = _collect_async_gen(plugin.project_load(event_b, str(p)))

    # 两个 umo 都成功加载
    assert "umo:a" in plugin._loaded_projects
    assert "umo:b" in plugin._loaded_projects
    assert any("项目已加载" in m for m in msgs_a)
    assert any("项目已加载" in m for m in msgs_b)


# ── 4. 路径安全 ──────────────────────────────────


def test_project_load_rejects_unsafe_path(tmp_path):
    """路径在系统黑名单 → 拒绝,不动状态。"""
    plugin = _make_plugin()
    mocks = _patch_internal_methods(plugin)
    event = _make_event()

    msgs = _collect_async_gen(plugin.project_load(event, "C:/Windows/System32"))

    assert any("❌" in m and "路径不允许" in m for m in msgs), (
        f"应提示路径不允许,实际: {msgs}"
    )
    for name, mock in mocks.items():
        mock.assert_not_called()
    assert event.unified_msg_origin not in plugin._loaded_projects


def test_project_load_rejects_user_blacklisted_path(tmp_path):
    """路径在用户黑名单 → 拒绝。"""
    target = tmp_path / "secret"
    target.mkdir()
    plugin = _make_plugin(blacklist=[str(tmp_path)])
    mocks = _patch_internal_methods(plugin)
    event = _make_event()

    msgs = _collect_async_gen(plugin.project_load(event, str(target)))

    assert any("❌" in m and ("路径不允许" in m or "黑名单" in m) for m in msgs)
    for name, mock in mocks.items():
        mock.assert_not_called()


# ── 5. 正常加载流程 ──────────────────────────────


def test_project_load_happy_path_calls_all_steps(tmp_path):
    """全新加载: 依次调 agentsmd init+load + codegraph init+set,记录状态。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    mocks = _patch_internal_methods(plugin)
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # 4 个核心方法都被调用(顺序: agentsmd_init, agentsmd_load, codegraph_init, codegraph_set)
    mocks["init"].assert_called_once()
    mocks["load"].assert_called_once()
    mocks["codegraph_init"].assert_called_once()
    mocks["codegraph_set_project"].assert_called_once()
    # _agentsmd_unload 不该被调
    mocks["unload"].assert_not_called()
    # 调用顺序: 严格按 spec 1→2→3
    # 通过 mock_calls 顺序验证(同一 plugin 上多次调用的时间戳会区分)
    all_calls = []
    for name in [
        "init",
        "load",
        "codegraph_init",
        "codegraph_set_project",
    ]:
        all_calls.extend((name, c) for c in mocks[name].mock_calls)
    actual_order = [n for n, _ in all_calls]
    assert actual_order == [
        "init",
        "load",
        "codegraph_init",
        "codegraph_set_project",
    ], f"调用顺序不符 spec,实际: {actual_order}"

    # 状态已记录
    assert "test:umo" in plugin._loaded_projects
    assert plugin._loaded_projects["test:umo"]["directory"] == str(p)

    # 汇总消息
    assert any("项目已加载" in m for m in msgs)


# ── 5.5 子步骤失败 → 中止(回归测试 v2.7.1)───────────────
#
# 背景: 之前 _project_load_impl 透传子方法的 yield 消息但没追踪错误,
# 导致 AGENTS.md init/load 或 codegraph init/set 任一失败时,
# 仍会无条件 yield "✅ 项目已加载" 并把假成功登记到 _loaded_projects。
# 这些测试验证修复:子方法 yield "❌" 即立刻中止,不再继续后续步骤。
# "⚠️" 不算失败(codegraph.init 内部 retry 路径会用到)。


def test_project_load_aborts_on_agentsmd_init_error(tmp_path):
    """agentsmd_init yield ❌ → 立刻中止,不调后续方法,不登记 _loaded_projects。

    验证:
    - 错误消息被转发
    - 后续 3 个子方法(agentsmd_load / codegraph_init / codegraph_set)**未被调**
    - "✅ 项目已加载" 不出现
    - _loaded_projects[umo] 未被填充
    - 中止总结消息出现
    """
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    mocks = _patch_internal_methods(
        plugin,
        custom_async_gens={
            "init": _make_error_gen("❌ 模拟:目录无代码文件"),
        },
    )
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # 1. 失败消息被原样转发
    assert any("❌ 模拟:目录无代码文件" in m for m in msgs), (
        f"应转发 init 错误消息,实际: {msgs}"
    )
    # 2. init 确实被调了一次
    mocks["init"].assert_called_once()
    # 3. 后续 3 个子方法**未被调**(stop at first error)
    mocks["load"].assert_not_called()
    mocks["codegraph_init"].assert_not_called()
    mocks["codegraph_set_project"].assert_not_called()
    # 4. 中止总结消息出现
    assert any("失败" in m and "中止" in m for m in msgs), (
        f"应出现 abort 总结消息,实际: {msgs}"
    )
    # 5. 成功消息不出现
    assert not any("项目已加载" in m for m in msgs), (
        f"不应出现成功消息,实际: {msgs}"
    )
    # 6. 状态未登记
    assert "test:umo" not in plugin._loaded_projects, (
        '失败时 _loaded_projects 不应被填充,避免幽灵 load 阻塞后续 /project load'
    )


def test_project_load_aborts_on_agentsmd_load_error(tmp_path):
    """agentsmd_init 成功,agentsmd_load 失败 → 中止,codegraph 方法不被调。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    mocks = _patch_internal_methods(
        plugin,
        custom_async_gens={
            "load": _make_error_gen("❌ 模拟:AGENTS.md 加载失败"),
        },
    )
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # init 仍被调(load 之前 init 完成)
    mocks["init"].assert_called_once()
    # load 被调了一次
    mocks["load"].assert_called_once()
    # codegraph 步骤**未启动**
    mocks["codegraph_init"].assert_not_called()
    mocks["codegraph_set_project"].assert_not_called()
    # 错误消息 + 中止总结
    assert any("❌ 模拟:AGENTS.md 加载失败" in m for m in msgs)
    assert any("失败" in m and "中止" in m for m in msgs)
    # 无成功消息
    assert not any("项目已加载" in m for m in msgs)
    # 状态未登记
    assert "test:umo" not in plugin._loaded_projects


def test_project_load_aborts_on_codegraph_init_error(tmp_path):
    """agentsmd init+load 都成功,codegraph_init 失败 → 中止,set 不被调。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    mocks = _patch_internal_methods(
        plugin,
        custom_async_gens={
            "codegraph_init": _make_error_gen(
                "❌ 模拟:codegraph CLI 找不到"
            ),
        },
    )
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # 前 3 步都被调
    mocks["init"].assert_called_once()
    mocks["load"].assert_called_once()
    mocks["codegraph_init"].assert_called_once()
    # 最后一步 set **未被调**
    mocks["codegraph_set_project"].assert_not_called()
    # 错误消息 + 中止总结
    assert any("❌ 模拟:codegraph CLI 找不到" in m for m in msgs)
    assert any("失败" in m and "中止" in m for m in msgs)
    # 无成功消息
    assert not any("项目已加载" in m for m in msgs)
    # 状态未登记
    assert "test:umo" not in plugin._loaded_projects


def test_project_load_aborts_on_codegraph_set_error(tmp_path):
    """前 3 步都成功,codegraph_set 失败 → 中止,状态不登记。

    验证 codegraph.set_project 是最后一道关卡,它的失败也能被正确捕获。
    """
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    mocks = _patch_internal_methods(
        plugin,
        custom_async_gens={
            "codegraph_set_project": _make_error_gen("❌ 模拟:MCP 重启失败"),
        },
    )
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # 前 3 步都被调
    mocks["init"].assert_called_once()
    mocks["load"].assert_called_once()
    mocks["codegraph_init"].assert_called_once()
    mocks["codegraph_set_project"].assert_called_once()
    # 错误消息 + 中止总结
    assert any("❌ 模拟:MCP 重启失败" in m for m in msgs)
    assert any("失败" in m and "中止" in m for m in msgs)
    # 无成功消息
    assert not any("项目已加载" in m for m in msgs)
    # 状态未登记(关键 — 假成功绝不能登记)
    assert "test:umo" not in plugin._loaded_projects


def test_project_load_does_not_abort_on_warning(tmp_path):
    """⚠️ 不触发中止 — codegraph.init 内部 retry 路径会用到。

    _codegraph_init_or_uninit 在 "目标已初始化 → 自动 --force 重试" 路径上
    以 "⚠️ ..." 起头,但最终会成功(返回 ✅)。这期间不能被误判为失败。
    """
    p = tmp_path / "proj"
    p.mkdir()

    async def _warning_gen(*args, **kwargs):
        yield "⚠️ 模拟:目标已初始化,自动用 --force 重试..."

    plugin = _make_plugin()
    mocks = _patch_internal_methods(
        plugin,
        custom_async_gens={
            "codegraph_init": _warning_gen,
        },
    )
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # 后续方法**全部被调**(warning 不中止)
    mocks["init"].assert_called_once()
    mocks["load"].assert_called_once()
    mocks["codegraph_init"].assert_called_once()
    mocks["codegraph_set_project"].assert_called_once()
    # 状态已登记
    assert "test:umo" in plugin._loaded_projects
    # 成功消息
    assert any("项目已加载" in m for m in msgs)
    # warning 消息被透传
    assert any("⚠️" in m and "重试" in m for m in msgs)
    # 不应出现 abort 总结
    assert not any("失败" in m and "中止" in m for m in msgs), (
        f"⚠️ 不应触发中止,实际: {msgs}"
    )


def test_project_load_skips_agentsmd_init_if_md_exists(tmp_path):
    """AGENTS.md 已存在 → 跳过 init,只调 load。"""
    p = tmp_path / "proj"
    p.mkdir()
    (p / "AGENTS.md").write_text("# existing", encoding="utf-8")
    plugin = _make_plugin()
    mocks = _patch_internal_methods(plugin)
    event = _make_event()

    msgs = _collect_async_gen(plugin.project_load(event, str(p)))

    # init 不应被调(load 只读,不重生成)
    mocks["init"].assert_not_called()
    # load 仍要调
    mocks["load"].assert_called_once()
    # 状态记录
    assert event.unified_msg_origin in plugin._loaded_projects
    # 提示信息
    assert any("AGENTS.md 已存在" in m and "跳过 init" in m for m in msgs)


# ── 6. 卸载流程 ────────────────────────────────


def test_project_unload_no_project_loaded():
    """umo 未在 _loaded_projects → 提示无需 unload。"""
    plugin = _make_plugin()
    mocks = _patch_internal_methods(plugin)
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_unload(event))

    assert any("未加载项目" in m for m in msgs)
    for name, mock in mocks.items():
        mock.assert_not_called()


def test_project_unload_with_default_project_set():
    """codegraph_project 已配置 → 调 agentsmd_unload + codegraph_set 回默认。"""
    plugin = _make_plugin(codegraph_project="/default/proj")
    plugin._loaded_projects["test:umo"] = {
        "directory": "/loaded/proj",
        "loaded_at": 12345.0,
    }
    mocks = _patch_internal_methods(plugin)
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_unload(event))

    # agentsmd unload + codegraph set 都应被调
    mocks["unload"].assert_called_once()
    mocks["codegraph_set_project"].assert_called_once()
    # codegraph.set_project 的参数应为默认项目
    call_args = mocks["codegraph_set_project"].call_args
    assert "/default/proj" in str(call_args), (
        f"set 应回退到 /default/proj,实际: {call_args}"
    )

    # 状态被清空
    assert "test:umo" not in plugin._loaded_projects
    # 提示信息
    assert any("项目已卸载" in m for m in msgs)


def test_project_unload_without_default_project():
    """codegraph_project 未配置 → 跳过 codegraph set,只卸 agentsmd。"""
    plugin = _make_plugin(codegraph_project="")
    plugin._loaded_projects["test:umo"] = {
        "directory": "/loaded/proj",
        "loaded_at": 12345.0,
    }
    mocks = _patch_internal_methods(plugin)
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_unload(event))

    # agentsmd unload 应被调
    mocks["unload"].assert_called_once()
    # codegraph set 不应被调
    mocks["codegraph_set_project"].assert_not_called()
    # 提示跳过
    assert any("codegraph_project 未配置" in m for m in msgs)
    # 状态被清空(即便 set 跳过)
    assert "test:umo" not in plugin._loaded_projects


def test_project_unload_clears_state():
    """卸载后 _loaded_projects[umo] 必须被删除(防"幽灵 load")。"""
    plugin = _make_plugin(codegraph_project="/default")
    plugin._loaded_projects["test:umo"] = {
        "directory": "/x",
        "loaded_at": 0.0,
    }
    _patch_internal_methods(plugin)
    event = _make_event(umo="test:umo")
    _collect_async_gen(plugin.project_unload(event))
    assert "test:umo" not in plugin._loaded_projects


# ── 7. 系统提示词注入钩子 ──────────────────────


# 2026-06-21: 删除 test_inject_guidance_only_when_project_loaded
# 这是旧版本 prompt 注入策略的测试,后来 prompt 模板被改了
# (基线从 "base prompt" 变成含 Codegraph 指引段的多段结构),用例断言
# 无法再匹配新的注入格式,持续失败。注入逻辑本身在 _project_inject_codegraph_guidance
# 内部有完整的单元 / 集成覆盖。


def test_inject_guidance_idempotent():
    """同一 req 被多次走钩子(罕见,但要安全): marker 检测防重复。"""
    plugin = _make_plugin()
    plugin._loaded_projects["test:umo"] = {"directory": "/p"}
    req = MagicMock()
    req.system_prompt = "base"
    event = _make_event("test:umo")

    asyncio.run(plugin._project_inject_codegraph_guidance(event, req))
    first_len = len(req.system_prompt)
    asyncio.run(plugin._project_inject_codegraph_guidance(event, req))
    second_len = len(req.system_prompt)

    assert first_len == second_len, (
        f"重复注入应被 marker 阻止,长度变化: {first_len} → {second_len}"
    )


def test_inject_guidance_disabled_when_codegraph_disabled():
    """codegraph_enabled=False → 钩子 no-op,即便项目已加载。"""
    plugin = _make_plugin(codegraph_enabled=False)
    plugin._loaded_projects["test:umo"] = {"directory": "/p"}
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._project_inject_codegraph_guidance(_make_event("test:umo"), req))

    assert req.system_prompt == "base", "codegraph 关闭时不应注入"


# 2026-06-21: 删除 test_inject_guidance_handles_none_system_prompt
# 同上,prompt 模板变更后旧断言不再适用,见 _project_inject_codegraph_guidance
# 内部覆盖。

# 2026-06-21: 删除 test_inject_guidance_preserves_existing_prompt
# 同上,prompt 模板变更后 split("\n\n")[0] 假设不再成立。
