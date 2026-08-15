"""Tests for /project load create / git_init / replace flags (2026-07-30).

Author: elecvoid243

覆盖:
- create: 不存在目录被创建(含父目录); 已存在目录 no-op; 存在但是文件 → 失败
- git_init: 空目录被 init; 已是 git → no-op (git 不在 PATH 时 skip)
- create + git_init 组合
- replace: 已加载不同目录 → 原子卸载旧的 + 加载新的; 不带 replace 时拒绝
- 回归: 未知 flag 静默忽略; 不传 create 时不存在目录不会被 mkdir

create / git_init 在路径安全校验之后、agentsmd/codegraph 子步骤之前执行,
故本文件用 MagicMock 把 3 个子步骤桩为成功, 让断言聚焦于 create / git_init /
replace 的真实文件系统 / git 副作用与 state 状态机, 而非子步骤细节。
"""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import MagicMock

import pytest
from astrbot_plugin_spcode_toolkit import main as _main_mod
from tools.project import state as _proj_state

SPCodeToolkit = _main_mod.SPCodeToolkit

UMO = "webchat:webchat!u!create1"


def _make_event(umo: str = UMO):
    event = MagicMock()
    # plain_result 退化为恒等, 与 test_project_subcommand 一致。
    event.plain_result = lambda x: x
    event.unified_msg_origin = umo
    return event


def _make_plugin():
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    plugin._loaded_projects = {}
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": None,
    }
    # 用 MagicMock 代替真实子系统, 由 _patch_substeps_success 挂成功桩。
    plugin.agentsmd = MagicMock()
    plugin.codegraph = MagicMock()
    from tools.project import ProjectManager

    plugin.project = ProjectManager(plugin)
    return plugin


def _patch_substeps_success(plugin):
    """桩 3 个子步骤为成功路径, 让 load 流水线走到 state 登记。"""

    async def _ok(*args, **kwargs):
        yield "mock-substep-ok"

    for name in ("init", "load"):
        m = MagicMock()
        m.side_effect = _ok
        setattr(plugin.agentsmd, name, m)
    # agentsmd.unload 是同步方法, replace 分支会 yield 其返回值。
    plugin.agentsmd.unload = MagicMock(return_value="mock-unload-ok")
    # 2026-08-15: codegraph 只剩 init(set_project 已不在 load 流水线)。
    for name in ("init",):
        m = MagicMock()
        m.side_effect = _ok
        setattr(plugin.codegraph, name, m)


def _run(coro):
    return asyncio.run(coro)


async def _drive(plugin, event, *args):
    msgs = []
    async for msg in plugin.project.handle_subcommand(event, "load", *args):
        msgs.append(msg)
    return msgs


def _reset_state():
    if hasattr(_proj_state, "reset"):
        _proj_state.reset()


@pytest.fixture
def env(tmp_path):
    _reset_state()
    plugin = _make_plugin()
    _patch_substeps_success(plugin)
    yield plugin, tmp_path
    _reset_state()


# ── create ────────────────────────────────────────────────────────


def test_create_makes_missing_dir_with_parents(env):
    plugin, tmp_path = env
    target = tmp_path / "brand" / "new"  # 含父目录, 验证 parents=True
    event = _make_event()
    _run(_drive(plugin, event, str(target), "create", "no_agentsmd", "no_codegraph"))
    assert target.is_dir()
    assert _proj_state.get(UMO) is not None
    assert _proj_state.get(UMO)["directory"] == str(target.resolve())


def test_create_existing_dir_is_noop(env):
    plugin, tmp_path = env
    event = _make_event()
    msgs = _run(
        _drive(plugin, event, str(tmp_path), "create", "no_agentsmd", "no_codegraph")
    )
    joined = "".join(str(m) for m in msgs)
    assert "跳过创建" in joined
    assert _proj_state.get(UMO) is not None


def test_create_rejects_existing_file(env):
    plugin, tmp_path = env
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    event = _make_event()
    msgs = _run(_drive(plugin, event, str(f), "create", "no_agentsmd", "no_codegraph"))
    joined = "".join(str(m) for m in msgs)
    assert "不是目录" in joined
    assert _proj_state.get(UMO) is None  # 失败不登记 state


# ── git_init ──────────────────────────────────────────────────────


_HAS_GIT = shutil.which("git") is not None


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_git_init_creates_repo(env):
    plugin, tmp_path = env
    event = _make_event()
    _run(
        _drive(plugin, event, str(tmp_path), "git_init", "no_agentsmd", "no_codegraph")
    )
    assert (tmp_path / ".git").exists()
    assert _proj_state.get(UMO) is not None


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_git_init_existing_repo_is_noop(env):
    plugin, tmp_path = env
    (tmp_path / ".git").mkdir()  # 假装已是 git 仓库
    event = _make_event()
    msgs = _run(
        _drive(plugin, event, str(tmp_path), "git_init", "no_agentsmd", "no_codegraph")
    )
    joined = "".join(str(m) for m in msgs)
    assert "跳过 init" in joined


@pytest.mark.skipif(not _HAS_GIT, reason="git not on PATH")
def test_create_plus_git_init(env):
    plugin, tmp_path = env
    target = tmp_path / "combo"
    event = _make_event()
    _run(
        _drive(
            plugin,
            event,
            str(target),
            "create",
            "git_init",
            "no_agentsmd",
            "no_codegraph",
        )
    )
    assert target.is_dir()
    assert (target / ".git").exists()


# ── replace ───────────────────────────────────────────────────────


def test_replace_unloads_old_then_loads_new(env, tmp_path):
    plugin, _ = env
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _proj_state.put(UMO, {"directory": str(old), "loaded_at": 1.0})
    event = _make_event()
    msgs = _run(
        _drive(plugin, event, str(new), "replace", "no_agentsmd", "no_codegraph")
    )
    joined = "".join(str(m) for m in msgs)
    assert "卸载旧项目" in joined
    assert _proj_state.get(UMO)["directory"] == str(new.resolve())


def test_load_without_replace_rejects_when_loaded(env, tmp_path):
    plugin, _ = env
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _proj_state.put(UMO, {"directory": str(old), "loaded_at": 1.0})
    event = _make_event()
    msgs = _run(_drive(plugin, event, str(new), "no_agentsmd", "no_codegraph"))
    joined = "".join(str(m) for m in msgs)
    assert "请先执行 /project unload" in joined
    # 未带 replace → 旧项目保持不变
    assert _proj_state.get(UMO)["directory"] == str(old)


# ── regression ────────────────────────────────────────────────────


def test_unknown_flag_silently_ignored(env, tmp_path):
    plugin, _ = env
    event = _make_event()
    # directory 在前, 未知 flag 在后 → 未知 flag 被忽略, load 成功
    _run(
        _drive(
            plugin,
            event,
            str(tmp_path),
            "totally_unknown_flag",
            "no_agentsmd",
            "no_codegraph",
        )
    )
    assert _proj_state.get(UMO) is not None


def test_no_create_does_not_mkdir(env, tmp_path):
    plugin, _ = env
    target = tmp_path / "ghost"  # 不存在, 且不传 create
    event = _make_event()
    _run(_drive(plugin, event, str(target), "no_agentsmd", "no_codegraph"))
    # 历史行为: 不传 create 绝不 mkdir
    assert not target.exists()
