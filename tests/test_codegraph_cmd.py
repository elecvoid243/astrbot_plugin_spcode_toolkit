"""/codegraph init|uninit 命令测试。"""

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


def _make_plugin(blacklist=None, project="", install_dir="/fake/install/dir"):
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    context = MagicMock()
    plugin.context = context
    plugin._config = {
        "codegraph_enabled": True,
        "codegraph_install_dir": install_dir,
        "codegraph_project": project,
        "file_remove_blacklist": blacklist or [],
    }
    plugin._codegraph_dir_locks = {}
    return plugin


def _collect_async_gen(agen):
    """把 async generator 收集为 list。"""
    return asyncio.run(_drain(agen))


async def _drain(agen):
    out = []
    async for msg in agen:
        out.append(msg)
    return out


def _make_event():
    event = MagicMock()
    event.plain_result = lambda x: f"RESULT: {x}"
    return event


# ── 路径校验 ──────────────────────────────────────


def test_init_rejects_dotdot(tmp_path):
    plugin = _make_plugin()
    event = _make_event()
    msgs = _collect_async_gen(
        plugin._codegraph_init_or_uninit(event, "../etc", init=True)
    )
    assert any(".." in m for m in msgs)


def test_init_rejects_system_dir():
    plugin = _make_plugin()
    event = _make_event()
    msgs = _collect_async_gen(
        plugin._codegraph_init_or_uninit(event, "C:/Windows/System32", init=True)
    )
    assert any("黑名单" in m for m in msgs)


def test_init_rejects_user_blacklist(tmp_path):
    target = tmp_path / "secret"
    target.mkdir()
    plugin = _make_plugin(blacklist=[str(tmp_path)])
    event = _make_event()
    msgs = _collect_async_gen(
        plugin._codegraph_init_or_uninit(event, str(target), init=True)
    )
    assert any("黑名单" in m for m in msgs)


def test_init_rejects_nonexistent(tmp_path):
    plugin = _make_plugin()
    event = _make_event()
    msgs = _collect_async_gen(
        plugin._codegraph_init_or_uninit(event, str(tmp_path / "nope"), init=True)
    )
    assert any("不存在" in m for m in msgs)


def test_uninit_allows_nonexistent(tmp_path):
    plugin = _make_plugin()
    event = _make_event()
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=None):
        msgs = _collect_async_gen(
            plugin._codegraph_init_or_uninit(event, str(tmp_path / "nope"), init=False)
        )
    # 实现里 uninit + codegraph 未安装 → "找不到 codegraph CLI"
    assert any("codegraph" in m and "找不到" in m for m in msgs)


def test_init_expanduser_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir()
    plugin = _make_plugin()
    event = _make_event()
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=None):
        msgs = _collect_async_gen(
            plugin._codegraph_init_or_uninit(event, "~/proj", init=True)
        )
    assert not any("不存在" in m for m in msgs)


# ── codegraph CLI 查找 ────────────────────────────


def test_init_errors_when_codegraph_not_found(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=None):
        msgs = _collect_async_gen(
            plugin._codegraph_init_or_uninit(event, str(p), init=True)
        )
    assert any("找不到 codegraph CLI" in m for m in msgs)


# ── Windows bundled launcher ──────────────────────


def test_init_windows_uses_bundled_node(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mcp_cfg = {
        "type": "stdio",
        "command": "C:/path/node.exe",
        "args": ["--liftoff-only", "C:/path/codegraph.js", "serve", "--mcp"],
    }

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    spawn_mock = AsyncMock(return_value=proc)
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg):
        with patch("asyncio.create_subprocess_exec", new=spawn_mock):
            _collect_async_gen(
                plugin._codegraph_init_or_uninit(event, str(p), init=True)
            )

    spawn_args = spawn_mock.call_args.args
    cmd_list = list(spawn_args)
    assert "serve" not in cmd_list
    assert "--mcp" not in cmd_list
    assert "init" in cmd_list
    assert str(p) in cmd_list


# ── uninit 传 --force ─────────────────────────────


def test_uninit_passes_force_flag(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mcp_cfg = {
        "type": "stdio",
        "command": "/usr/bin/codegraph",
        "args": ["serve", "--mcp"],
    }
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    spawn_mock = AsyncMock(return_value=proc)
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg):
        with patch("asyncio.create_subprocess_exec", new=spawn_mock):
            _collect_async_gen(
                plugin._codegraph_init_or_uninit(event, str(p), init=False)
            )

    cmd_list = list(spawn_mock.call_args.args)
    assert "--force" in cmd_list


# ── 超时 ─────────────────────────────────────────


def test_init_timeout_kills_process(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mcp_cfg = {"type": "stdio", "command": "codegraph", "args": []}

    proc = MagicMock()

    async def hang(*a, **kw):
        await asyncio.sleep(100)

    proc.communicate = hang
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    spawn_mock = AsyncMock(return_value=proc)
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg):
        with patch("asyncio.create_subprocess_exec", new=spawn_mock):
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                msgs = _collect_async_gen(
                    plugin._codegraph_init_or_uninit(event, str(p), init=True)
                )

    proc.kill.assert_called_once()
    assert any("超时" in m for m in msgs)


# ── 错误信息识别 ──────────────────────────────────


def test_init_handles_already_initialized_auto_retries(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mcp_cfg = {"type": "stdio", "command": "codegraph", "args": []}

    proc_fail = MagicMock()
    proc_fail.communicate = AsyncMock(return_value=(b"", b"already initialized"))
    proc_fail.returncode = 1

    proc_ok = MagicMock()
    proc_ok.communicate = AsyncMock(return_value=(b"", b""))
    proc_ok.returncode = 0

    spawn_mock = AsyncMock(side_effect=[proc_fail, proc_ok])
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg):
        with patch("asyncio.create_subprocess_exec", new=spawn_mock):
            msgs = _collect_async_gen(
                plugin._codegraph_init_or_uninit(event, str(p), init=True)
            )

    assert spawn_mock.call_count == 2
    cmd_list2 = list(spawn_mock.call_args_list[1].args)
    assert "--force" in cmd_list2
    assert any("重新初始化完成" in m for m in msgs)


def test_uninit_handles_not_initialized(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mcp_cfg = {"type": "stdio", "command": "codegraph", "args": []}

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b"not initialized"))
    proc.returncode = 1

    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg):
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            msgs = _collect_async_gen(
                plugin._codegraph_init_or_uninit(event, str(p), init=False)
            )

    assert any("未初始化" in m for m in msgs)


# ── 并发锁 ───────────────────────────────────────


def test_init_concurrent_same_dir_serialized(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mcp_cfg = {"type": "stdio", "command": "codegraph", "args": []}

    proc = MagicMock()
    first_started = asyncio.Event()
    second_can_proceed = asyncio.Event()
    call_count = 0

    async def slow_communicate(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await second_can_proceed.wait()
        return (b"", b"")

    proc.communicate = slow_communicate

    async def drain(agen):
        out = []
        async for msg in agen:
            out.append(msg)
        return out

    async def run_both():
        t1 = asyncio.create_task(
            drain(plugin._codegraph_init_or_uninit(event, str(p), init=True))
        )
        await asyncio.sleep(0.02)
        t2 = asyncio.create_task(
            drain(plugin._codegraph_init_or_uninit(event, str(p), init=True))
        )
        await first_started.wait()
        second_can_proceed.set()
        return await asyncio.gather(t1, t2)

    spawn_mock = AsyncMock(return_value=proc)
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg):
        with patch("asyncio.create_subprocess_exec", new=spawn_mock):
            r1, r2 = asyncio.run(run_both())

    assert call_count == 1
    rejected = [m for m in r2 if "已有 codegraph 操作在跑" in m]
    assert len(rejected) >= 1, f"第二次调用应被拒收,但 messages={r2}"


# ── 命令组注册(v2.3 修正: 命令是插件类方法)────


def test_codegraph_command_group_is_class_method():
    """验证 /codegraph 命令组是 SPCodeToolkit 的类方法(参考项目模式)。"""
    from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit
    from astrbot.core.star.register.star_handler import RegisteringCommandable

    # 命令组装饰器把方法替换为 RegisteringCommandable 实例(framework 通过它调度)
    assert isinstance(SPCodeToolkit.codegraph, RegisteringCommandable)
    # 子命令方法保留为可调用的 async 函数
    assert callable(SPCodeToolkit.codegraph_init)
    assert callable(SPCodeToolkit.codegraph_uninit)
    assert callable(SPCodeToolkit.codegraph_set)

    # 实例上也存在
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    assert isinstance(plugin.codegraph, RegisteringCommandable)
    assert callable(plugin.codegraph_init)


def test_codegraph_subcommands_dispatch_via_instance():
    """通过 plugin 实例调子命令方法,确认 self 正确转发(plugin 自己)。"""
    plugin = _make_plugin()
    # 子命令方法的 __self__ 应该是 plugin(framework 通过它知道调用哪个实例)
    assert plugin.codegraph_init.__self__ is plugin
    assert plugin.codegraph_uninit.__self__ is plugin
    assert plugin.codegraph_set.__self__ is plugin


def test_codegraph_init_method_dispatches_to_inner(tmp_path):
    """codegraph_init 类方法 → 调用 self._codegraph_init_or_uninit。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mcp_cfg = {"type": "stdio", "command": "codegraph", "args": []}
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    spawn_mock = AsyncMock(return_value=proc)
    with patch(f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg):
        with patch("asyncio.create_subprocess_exec", new=spawn_mock):
            msgs = []

            async def collect():
                async for m in plugin.codegraph_init(event, str(p)):
                    msgs.append(m)

            asyncio.run(collect())

    assert any("✅" in m for m in msgs)


def test_codegraph_set_method_dispatches_to_inner(tmp_path):
    """codegraph_set 类方法 → 调用 self._codegraph_set_project。"""
    p = tmp_path / "newproj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.mcp_server_runtime = {}  # MCP 未运行

    msgs = []

    async def collect():
        async for m in plugin.codegraph_set(event, str(p)):
            msgs.append(m)

    asyncio.run(collect())

    # _config 已更新
    assert str(p) in plugin._config["codegraph_project"] or plugin._config[
        "codegraph_project"
    ] == str(p)
    # 用户收到成功消息
    assert any("已更新" in m for m in msgs)


# ── v2.1: codegraph_install_dir 透传 ─────────────


def test_init_passes_install_dir_to_detect(tmp_path):
    """plugin 配了 install_dir → init 命令把这个 install_dir 传给 detect。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin(install_dir="D:/custom/codegraph")
    event = _make_event()
    mcp_cfg = {
        "type": "stdio",
        "command": "D:/custom/codegraph/node.exe",
        "args": ["D:/custom/codegraph/codegraph.js", "serve", "--mcp"],
    }
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    with patch(
        f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg
    ) as mock_detect:
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            _collect_async_gen(
                plugin._codegraph_init_or_uninit(event, str(p), init=True)
            )

    # 验证 detect 收到了 install_dir
    mock_detect.assert_called_once_with(install_dir="D:/custom/codegraph")


def test_init_falls_back_to_auto_detect_when_install_dir_empty(tmp_path):
    """install_dir 未配置 → detect 收到 None(走 auto-detect)。"""
    p = tmp_path / "proj"
    p.mkdir()
    plugin = _make_plugin(install_dir="")
    event = _make_event()
    mcp_cfg = {"type": "stdio", "command": "/usr/bin/codegraph", "args": []}
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    with patch(
        f"{_MAIN_MODULE_PATH}.detect_codegraph_launcher", return_value=mcp_cfg
    ) as mock_detect:
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            _collect_async_gen(
                plugin._codegraph_init_or_uninit(event, str(p), init=True)
            )

    # 验证 detect 收到 None (走 auto-detect 分支)
    mock_detect.assert_called_once_with(install_dir=None)


# ── v2.2: /codegraph set 命令 ─────────────


def test_set_rejects_nonexistent_dir(tmp_path):
    """set 必须有已存在的目录。"""
    plugin = _make_plugin()
    event = _make_event()
    msgs = _collect_async_gen(
        plugin._codegraph_set_project(event, str(tmp_path / "nope"))
    )
    assert any("不存在" in m for m in msgs)


def test_set_rejects_dotdot():
    """set 拒绝 .. 路径。"""
    plugin = _make_plugin()
    event = _make_event()
    msgs = _collect_async_gen(plugin._codegraph_set_project(event, "../etc"))
    assert any(".." in m for m in msgs)


def test_set_updates_config_when_mcp_not_running(tmp_path):
    """MCP 未运行:仅更新 _config,不调 disable/enable。"""
    p = tmp_path / "newproj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.mcp_server_runtime = {}  # MCP 未运行
    mgr.disable_mcp_server = AsyncMock()
    mgr.enable_mcp_server = AsyncMock()

    msgs = _collect_async_gen(plugin._codegraph_set_project(event, str(p)))

    # 验证 _config 已更新(Windows 上 Path.resolve() 会返回反斜杠)
    assert str(p) in plugin._config["codegraph_project"] or plugin._config[
        "codegraph_project"
    ] == str(p)
    # MCP 未运行时不应调 disable/enable
    mgr.disable_mcp_server.assert_not_called()
    mgr.enable_mcp_server.assert_not_called()
    # 给用户提示
    assert any("已更新" in m for m in msgs)


def test_set_restarts_mcp_when_running(tmp_path):
    """MCP 在跑:disable + enable 以应用新 --path。"""
    p = tmp_path / "newproj"
    p.mkdir()
    plugin = _make_plugin()
    event = _make_event()
    mgr = plugin.context.get_llm_tool_manager.return_value
    mgr.mcp_server_runtime = {"codegraph": MagicMock()}  # MCP 在跑
    mgr.disable_mcp_server = AsyncMock()
    mgr.enable_mcp_server = AsyncMock()

    with patch(f"{_MAIN_MODULE_PATH}.ensure_stdio_allowlist"):
        with patch.object(
            plugin,
            "_build_mcp_cfg",
            return_value={
                "type": "stdio",
                "command": "node.exe",
                "args": ["entry.js", "serve", "--mcp", "--path", str(p)],
            },
        ):
            msgs = _collect_async_gen(plugin._codegraph_set_project(event, str(p)))

    # 验证 disable + enable 都被调
    mgr.disable_mcp_server.assert_awaited_once()
    mgr.enable_mcp_server.assert_awaited_once()
    # 验证传给 enable 的 config 含新 --path
    call = mgr.enable_mcp_server.call_args
    assert "--path" in call.kwargs["config"]["args"]
    assert str(p) in call.kwargs["config"]["args"]
    # 用户收到成功消息
    assert any("✅" in m and "codegraph" in m for m in msgs)
