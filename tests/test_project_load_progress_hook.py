"""tests/test_project_load_progress_hook.py — load_impl_silent 进度钩子测试。

Author: elecvoid243 @ 2026-11-07
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


def _make_manager() -> ProjectManager:
    plugin = MagicMock()
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "file_remove_blacklist": None,
    }
    return ProjectManager(plugin)


def test_progress_written_on_early_failure(tmp_path):
    """目录不存在等早期失败也要写 failed 终态。"""
    mgr = _make_manager()
    prog.begin("u1", "project_load")
    result = asyncio.run(mgr.load_impl_silent(_make_event(), str(tmp_path / "missing")))
    assert result["ok"] is False
    assert result["reason"] == "directory_missing"
    rec = prog.query("u1")
    assert rec["status"] == "failed"
    assert rec["reason"] == "directory_missing"


def test_progress_finish_without_begin_is_noop(tmp_path):
    """未经 begin 的直接调用(如旧测试)不因进度钩子崩溃。"""
    mgr = _make_manager()
    result = asyncio.run(mgr.load_impl_silent(_make_event(), str(tmp_path / "missing")))
    assert result["ok"] is False
    assert prog.query("u1") is None


def test_silent_reason_classification_agentsmd_load(tmp_path):
    """AGENTS.md 读取失败 → reason=agentsmd_load_failed(非兜底值)。"""
    (tmp_path / "AGENTS.md").write_text("# agents\n", encoding="utf-8")

    async def _fail_load(event, directory):
        yield "❌ 模拟 AGENTS.md 读取失败"

    from tools.agentsmd import AgentsmdSubsystem

    plugin = MagicMock()
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "file_remove_blacklist": None,
    }
    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **kw: (True, ""),
    )
    plugin.agentsmd.load = _fail_load
    mgr = ProjectManager(plugin)

    result = asyncio.run(mgr.load_impl_silent(_make_event(), str(tmp_path)))
    assert result["ok"] is False
    assert result["reason"] == "agentsmd_load_failed"


def test_load_never_invokes_codegraph(tmp_path):
    """加载流水线不调用 codegraph.init / set_project。"""
    plugin = MagicMock()
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "file_remove_blacklist": None,
    }
    from tools.agentsmd import AgentsmdSubsystem

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **kw: (True, ""),
    )
    plugin.agentsmd.init = MagicMock()
    mgr = ProjectManager(plugin)

    result = asyncio.run(mgr.load_impl_silent(_make_event(), str(tmp_path)))
    assert result["ok"] is True
    plugin.codegraph.init.assert_not_called()
    plugin.codegraph.set_project.assert_not_called()
    plugin.agentsmd.init.assert_not_called()


def test_already_loaded_finishes_progress_as_done_not_failed(tmp_path):
    """2026-08-07 bugfix:重复进入已挂载会话时,幂等拒绝必须写 done。

    前端 silentLoad 把 no_project_loaded + 同目录视为幂等成功,但进度
    存储若写 failed(且 messages 为空——早退在任何 append 之前),chip
    会显示"加载失败"且失败详情为空,尽管后端实际保持已加载状态。
    """
    _state.put("u1", {"directory": str(tmp_path), "loaded_at": 1.0})
    mgr = _make_manager()
    prog.begin("u1", "project_load")
    result = asyncio.run(mgr.load_impl_silent(_make_event(), str(tmp_path)))

    # 端点层仍拿到"未重复加载"的原始结果(契约不变)
    assert result["ok"] is False
    assert result["reason"] == "project_already_loaded"
    # 但进度存储必须收敛为 done(目标状态已达成)
    rec = prog.query("u1")
    assert rec["status"] == "done"
    assert rec["reason"] is None
