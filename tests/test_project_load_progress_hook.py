"""tests/test_project_load_progress_hook.py — load_impl_silent 进度钩子测试。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from tools import operation_progress as prog
from tools.project import state as _state
from tools.project.manager import ProjectManager


@pytest.fixture(autouse=True)
def _clean() -> None:
    prog._records.clear()
    _state._loaded_projects.clear()


def _make_event(umo: str = "u1") -> MagicMock:
    event = MagicMock()
    event.unified_msg_origin = umo
    event.plain_result = lambda text: text
    return event


def _make_manager(*, agentsmd_on: bool = True) -> ProjectManager:
    plugin = MagicMock()
    plugin._config = {
        "agentsmd_enabled": agentsmd_on,
        "codegraph_enabled": True,
    }
    return ProjectManager(plugin)


def test_progress_written_on_early_failure():
    """feature flag 关闭的早期返回也要写 failed 终态。"""
    mgr = _make_manager(agentsmd_on=False)
    prog.begin("u1", "project_load")
    result = asyncio.run(
        mgr.load_impl_silent(
            _make_event(), "C:/x", no_agentsmd=False, no_codegraph=True
        )
    )
    assert result["ok"] is False
    rec = prog.query("u1")
    assert rec["status"] == "failed"
    assert rec["reason"] == "agentsmd_disabled"


def test_progress_finish_without_begin_is_noop():
    """未经 begin 的直接调用(如旧测试)不因进度钩子崩溃。"""
    mgr = _make_manager(agentsmd_on=False)
    result = asyncio.run(
        mgr.load_impl_silent(
            _make_event(), "C:/x", no_agentsmd=False, no_codegraph=True
        )
    )
    assert result["ok"] is False
    assert prog.query("u1") is None


def test_silent_reason_classification_agentsmd_init():
    """A 类修复:agentsmd.init 失败 → reason=agentsmd_init_failed(非兜底值)。"""

    async def _fail_init(event, directory):
        yield "❌ 模拟 init 失败"

    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin.agentsmd.init = _fail_init
    mgr = ProjectManager(plugin)

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        # td 下无 AGENTS.md → 走 init 分支
        result = asyncio.run(mgr.load_impl_silent(_make_event(), td, no_codegraph=True))
    assert result["ok"] is False
    assert result["reason"] == "agentsmd_init_failed"


def test_silent_reason_classification_codegraph_init():
    """A 类修复:codegraph.init 失败 → reason=codegraph_init_failed。"""

    async def _ok(event, directory):
        yield "✅ ok"

    async def _fail_init(event, directory):
        yield "❌ 模拟 codegraph init 失败"

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        # 预置 AGENTS.md 使 agentsmd 分支跳过 init,直接 load 成功
        (open(f"{td}/AGENTS.md", "w", encoding="utf-8")).write("# agents")
        plugin = MagicMock()
        plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
        plugin.agentsmd.load = _ok
        plugin.codegraph.init = _fail_init
        mgr = ProjectManager(plugin)
        result = asyncio.run(mgr.load_impl_silent(_make_event(), td))
    assert result["ok"] is False
    assert result["reason"] == "codegraph_init_failed"


def test_already_loaded_finishes_progress_as_done_not_failed():
    """2026-08-07 bugfix:重复进入已挂载会话时,幂等拒绝必须写 done。

    前端 silentLoad 把 no_project_loaded + 同目录视为幂等成功,但进度
    存储若写 failed(且 messages 为空——早退在任何 append 之前),chip
    会显示"加载失败"且失败详情为空,尽管后端实际保持已加载状态。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        _state.put("u1", {"directory": td, "loaded_at": 1.0})
        mgr = _make_manager()
        prog.begin("u1", "project_load")
        result = asyncio.run(
            mgr.load_impl_silent(_make_event(), td, no_codegraph=True)
        )
    # 端点层仍拿到"未重复加载"的原始结果(契约不变)
    assert result["ok"] is False
    assert result["reason"] == "project_already_loaded"
    # 但进度存储必须收敛为 done(目标状态已达成)
    rec = prog.query("u1")
    assert rec["status"] == "done"
    assert rec["reason"] is None
