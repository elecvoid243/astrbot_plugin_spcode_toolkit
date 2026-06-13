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
    plugin._loaded_agents = {}
    plugin._loaded_projects = {}
    plugin._codegraph_dir_locks = {}
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


def _patch_internal_methods(plugin, *, agentsmd_md_exists=False):
    """把 plugin 的 5 个内部方法替换为 mock,返回可观察的 mock 实例。

    重要:这里要区分两类方法:
    - async generator 方法(`async def ... yield ...`):
      用 MagicMock + side_effect = async_gen_func 实现。调用返回 async generator,
      可被 `async for` 迭代。
    - 同步方法(`def ... return str`):
      用 MagicMock + return_value 实现。调用返回字符串。

    注意:不能对 async generator 方法用 AsyncMock——
    AsyncMock 调用返回 coroutine(需 await),而 `async for` 需要 async generator。
    """

    async def _async_gen_ok(*args, **kwargs):
        """通用 async generator,被所有 async gen 方法复用。"""
        yield "mock-async-gen-ok"

    # 异步生成器方法
    async_gen_methods = [
        "_agentsmd_init",
        "_agentsmd_load",
        "_codegraph_init_or_uninit",
        "_codegraph_set_project",
    ]
    # 同步方法(返回字符串)
    sync_methods = [
        "_agentsmd_unload",
    ]

    mocks = {}
    for name in async_gen_methods:
        m = MagicMock()
        m.side_effect = _async_gen_ok  # 调用时返回 async generator
        setattr(plugin, name, m)
        mocks[name] = m
    for name in sync_methods:
        m = MagicMock()
        m.return_value = "mock-sync-ok"  # 调用时返回字符串
        setattr(plugin, name, m)
        mocks[name] = m
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

    assert any("agentsmd_enabled" in m and "codegraph_enabled" in m for m in msgs), (
        f"应提示两个 flag 都需开启,实际: {msgs}"
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

    assert any("agentsmd_enabled" in m for m in msgs)
    for name, mock in mocks.items():
        mock.assert_not_called()


def test_project_unload_refuses_when_agentsmd_disabled():
    """agentsmd 关闭时 unload 也应拒绝(避免误清理)。"""
    plugin = _make_plugin(agentsmd_enabled=False, codegraph_enabled=True)
    plugin._loaded_projects["test:umo"] = {"directory": "/some/proj"}
    event = _make_event(umo="test:umo")

    msgs = _collect_async_gen(plugin.project_unload(event))

    assert any("agentsmd_enabled" in m for m in msgs)
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
    mocks["_agentsmd_init"].assert_called_once()
    mocks["_agentsmd_load"].assert_called_once()
    mocks["_codegraph_init_or_uninit"].assert_called_once()
    mocks["_codegraph_set_project"].assert_called_once()
    # _agentsmd_unload 不该被调
    mocks["_agentsmd_unload"].assert_not_called()
    # 调用顺序: 严格按 spec 1→2→3
    # 通过 mock_calls 顺序验证(同一 plugin 上多次调用的时间戳会区分)
    all_calls = []
    for name in [
        "_agentsmd_init",
        "_agentsmd_load",
        "_codegraph_init_or_uninit",
        "_codegraph_set_project",
    ]:
        all_calls.extend((name, c) for c in mocks[name].mock_calls)
    actual_order = [n for n, _ in all_calls]
    assert actual_order == [
        "_agentsmd_init",
        "_agentsmd_load",
        "_codegraph_init_or_uninit",
        "_codegraph_set_project",
    ], f"调用顺序不符 spec,实际: {actual_order}"

    # 状态已记录
    assert "test:umo" in plugin._loaded_projects
    assert plugin._loaded_projects["test:umo"]["directory"] == str(p)

    # 汇总消息
    assert any("项目已加载" in m for m in msgs)


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
    mocks["_agentsmd_init"].assert_not_called()
    # load 仍要调
    mocks["_agentsmd_load"].assert_called_once()
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
    mocks["_agentsmd_unload"].assert_called_once()
    mocks["_codegraph_set_project"].assert_called_once()
    # _codegraph_set_project 的参数应为默认项目
    call_args = mocks["_codegraph_set_project"].call_args
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
    mocks["_agentsmd_unload"].assert_called_once()
    # codegraph set 不应被调
    mocks["_codegraph_set_project"].assert_not_called()
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


def test_inject_guidance_only_when_project_loaded():
    """on_llm_request 钩子: 项目已加载 → 注入; 未加载 → 不注入。"""
    plugin = _make_plugin()
    plugin._loaded_projects["test:umo"] = {"directory": "/p"}

    req = MagicMock()
    req.system_prompt = "base prompt"

    # 已加载: 应注入
    asyncio.run(plugin._project_inject_codegraph_guidance(_make_event("test:umo"), req))
    assert "Codegraph 优先使用指引" in req.system_prompt
    assert "base prompt" in req.system_prompt

    # 未加载: 不应注入
    req2 = MagicMock()
    req2.system_prompt = "base prompt"
    asyncio.run(
        plugin._project_inject_codegraph_guidance(_make_event("other:umo"), req2)
    )
    assert req2.system_prompt == "base prompt"


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


def test_inject_guidance_handles_none_system_prompt():
    """req.system_prompt = None 时也能正确设置(用 lstrip("\n") 避免前置空行)。"""
    plugin = _make_plugin()
    plugin._loaded_projects["test:umo"] = {"directory": "/p"}
    req = MagicMock()
    req.system_prompt = None

    asyncio.run(plugin._project_inject_codegraph_guidance(_make_event("test:umo"), req))

    assert req.system_prompt is not None
    assert "Codegraph 优先使用指引" in req.system_prompt


def test_inject_guidance_preserves_existing_prompt():
    """已有 system_prompt 时,新内容追加到末尾(不覆盖)。"""
    plugin = _make_plugin()
    plugin._loaded_projects["test:umo"] = {"directory": "/p"}
    req = MagicMock()
    req.system_prompt = "USER_CUSTOM_HERE"

    asyncio.run(plugin._project_inject_codegraph_guidance(_make_event("test:umo"), req))

    assert "USER_CUSTOM_HERE" in req.system_prompt
    assert "USER_CUSTOM_HERE" == req.system_prompt.split("\n\n")[0], (
        "原 system_prompt 应是首段,新内容追加在末尾"
    )
