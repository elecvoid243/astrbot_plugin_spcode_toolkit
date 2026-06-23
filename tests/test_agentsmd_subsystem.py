"""tools/agentsmd/ 子系统单元测试(PR-5 2026-06-23)。

覆盖:
- AgentsState: dataclass + update_content
- AgentsStateManager: get/set/pop/contains/iter_umos/clear/__len__
- AgentsmdSubsystem: 构造 + state 属性 + clear() 方法
- AgentsmdHandlers: init/load/unload/update/on_llm_request 边界场景

注:不重复测 tools/agentsmd.py 的 helpers(已有 test_agentsmd.py 覆盖);
这里只测 PR-5 新增的 _state/_handlers/__init__ 子系统层。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from tools.agentsmd import (  # noqa: E402
    AgentsState,
    AgentsStateManager,
    AgentsmdHandlers,
    AgentsmdSubsystem,
)
from tools.agentsmd._core import (  # noqa: E402
    INJECTION_MARKER,
)


pytestmark = pytest.mark.asyncio


# ── AgentsState ─────────────────────────────────────


def test_agents_state_construction():
    st = AgentsState(
        path="/tmp/x/AGENTS.md",
        directory="/tmp/x",
        last_content="hello",
        mtime=1.23,
    )
    assert st.path == "/tmp/x/AGENTS.md"
    assert st.directory == "/tmp/x"
    assert st.last_content == "hello"
    assert st.mtime == 1.23


def test_agents_state_update_content():
    st = AgentsState(path="p", directory="d", last_content="v1", mtime=1.0)
    st.update_content("v2", 2.0)
    assert st.last_content == "v2"
    assert st.mtime == 2.0


# ── AgentsStateManager ──────────────────────────────


def test_manager_empty():
    mgr = AgentsStateManager()
    assert len(mgr) == 0
    assert mgr.get("any") is None
    assert "any" not in mgr


def test_manager_set_get_contains():
    mgr = AgentsStateManager()
    s = AgentsState(path="p", directory="d", last_content="c", mtime=1.0)
    mgr.set("umo1", s)
    assert "umo1" in mgr
    assert mgr.get("umo1") is s
    assert len(mgr) == 1


def test_manager_pop_returns_state_then_missing():
    mgr = AgentsStateManager()
    s = AgentsState(path="p", directory="d", last_content="c", mtime=1.0)
    mgr.set("umo1", s)
    assert mgr.pop("umo1") is s
    assert mgr.pop("umo1") is None  # already gone
    assert "umo1" not in mgr


def test_manager_iter_umos():
    mgr = AgentsStateManager()
    mgr.set("a", AgentsState("p", "d", "c", 1.0))
    mgr.set("b", AgentsState("p", "d", "c", 1.0))
    assert set(mgr.iter_umos()) == {"a", "b"}


def test_manager_clear():
    mgr = AgentsStateManager()
    mgr.set("a", AgentsState("p", "d", "c", 1.0))
    mgr.clear()
    assert len(mgr) == 0
    assert "a" not in mgr


# ── AgentsmdSubsystem 构造 ──────────────────────────


def test_subsystem_construction():
    """subsystem 创建时 state 空,handlers 已就绪。"""
    fake_plugin = MagicMock()
    fake_plugin._config = {}
    sub = AgentsmdSubsystem(plugin=fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    assert len(sub.state) == 0
    assert sub._handlers is not None
    assert isinstance(sub._handlers, AgentsmdHandlers)


def test_subsystem_state_property_returns_manager():
    fake_plugin = MagicMock()
    fake_plugin._config = {}
    sub = AgentsmdSubsystem(plugin=fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    assert sub.state is sub._state  # same instance


def test_subsystem_clear_empties_state():
    fake_plugin = MagicMock()
    fake_plugin._config = {}
    sub = AgentsmdSubsystem(plugin=fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    sub.state.set("a", AgentsState("p", "d", "c", 1.0))
    assert len(sub.state) == 1
    sub.clear()
    assert len(sub.state) == 0


# ── AgentsmdHandlers.unload ─────────────────────────


def _make_event(umo: str = "umo-1") -> MagicMock:
    ev = MagicMock()
    ev.unified_msg_origin = umo
    ev.plain_result = MagicMock(side_effect=lambda text: f"RESULT({text})")
    return ev


def test_unload_when_no_state_returns_info():
    fake_plugin = MagicMock()
    fake_plugin._config = {}
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event()
    result = h.unload(ev)
    assert "未加载任何 AGENTS.md" in result


def test_unload_removes_state_and_returns_path():
    fake_plugin = MagicMock()
    fake_plugin._config = {}
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    mgr.set("umo-1", AgentsState(path="/tmp/x/AGENTS.md", directory="/tmp/x",
                                  last_content="x", mtime=1.0))
    ev = _make_event()
    result = h.unload(ev)
    assert "已卸载" in result
    assert "/tmp/x/AGENTS.md" in result
    assert "umo-1" not in mgr


# ── AgentsmdHandlers.on_llm_request ──────────────────


def _make_req(sp: str | None = "") -> MagicMock:
    req = MagicMock()
    req.system_prompt = sp
    return req


async def test_on_llm_request_no_state_returns():
    fake_plugin = MagicMock()
    fake_plugin._config = {"agentsmd_enabled": True}
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    req = _make_req("orig")
    await h.on_llm_request(ev, req)
    # 未注入:system_prompt 保持不变
    assert req.system_prompt == "orig"


async def test_on_llm_request_disabled_returns():
    fake_plugin = MagicMock()
    fake_plugin._config = {"agentsmd_enabled": False}
    mgr = AgentsStateManager()
    mgr.set("umo-1", AgentsState(path="p", directory="d", last_content="C", mtime=1.0))
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    req = _make_req("orig")
    await h.on_llm_request(ev, req)
    assert req.system_prompt == "orig"


async def test_on_llm_request_injects_marker():
    fake_plugin = MagicMock()
    fake_plugin._config = {"agentsmd_enabled": True}
    mgr = AgentsStateManager()
    mgr.set("umo-1", AgentsState(path="p", directory="d", last_content="AGENTS_BODY", mtime=1.0))
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    req = _make_req("original system")
    await h.on_llm_request(ev, req)
    assert INJECTION_MARKER in req.system_prompt
    assert "AGENTS_BODY" in req.system_prompt
    # 原有 system_prompt 保留
    assert req.system_prompt.startswith("original system")


async def test_on_llm_request_idempotent_with_marker():
    fake_plugin = MagicMock()
    fake_plugin._config = {"agentsmd_enabled": True}
    mgr = AgentsStateManager()
    mgr.set("umo-1", AgentsState(path="p", directory="d", last_content="BODY", mtime=1.0))
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    req = _make_req(f"already contains {INJECTION_MARKER}")
    await h.on_llm_request(ev, req)
    # 不重复注入
    assert req.system_prompt.count(INJECTION_MARKER) == 1


async def test_on_llm_request_empty_sp_handled():
    fake_plugin = MagicMock()
    fake_plugin._config = {"agentsmd_enabled": True}
    mgr = AgentsStateManager()
    mgr.set("umo-1", AgentsState(path="p", directory="/proj", last_content="BODY", mtime=1.0))
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    req = _make_req(None)
    await h.on_llm_request(ev, req)
    # lstrip("\n") 后,不应以换行开头
    assert req.system_prompt is not None
    assert not req.system_prompt.startswith("\n")
    assert INJECTION_MARKER in req.system_prompt


async def test_on_llm_request_refreshes_on_mtime_change(tmp_path: Path):
    fake_plugin = MagicMock()
    fake_plugin._config = {"agentsmd_enabled": True}
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("OLD_CONTENT", encoding="utf-8")
    mgr = AgentsStateManager()
    mgr.set(
        "umo-1",
        AgentsState(
            path=str(agents_md),
            directory=str(tmp_path),
            last_content="OLD_CONTENT",
            mtime=agents_md.stat().st_mtime - 10,  # 故意过期
        ),
    )
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    req = _make_req("")
    # 修改文件
    await asyncio.to_thread(agents_md.write_text, "NEW_CONTENT", encoding="utf-8")
    await h.on_llm_request(ev, req)
    assert "NEW_CONTENT" in req.system_prompt
    # state.mtime 已更新
    assert abs(mgr.get("umo-1").mtime - agents_md.stat().st_mtime) < 0.01


# ── AgentsmdHandlers.init 边界 ─────────────────────


async def test_init_path_not_safe_yields_error():
    fake_plugin = MagicMock()
    fake_plugin._config = {"file_remove_blacklist": []}
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(
        state=mgr,
        plugin_getter=lambda: fake_plugin,
        is_path_safe=lambda *args, **kwargs: (False, "blocked"),
    )
    ev = _make_event()
    msgs = []
    async for msg in h.init(ev, "/anywhere"):
        msgs.append(msg)
    assert any("路径不允许" in m for m in msgs)


async def test_init_dir_not_exists(tmp_path: Path):
    fake_plugin = MagicMock()
    fake_plugin._config = {"file_remove_blacklist": []}
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(
        state=mgr,
        plugin_getter=lambda: fake_plugin,
        is_path_safe=lambda *args, **kwargs: (True, ""),
    )
    ev = _make_event()
    nonexistent = tmp_path / "nope"
    msgs = []
    async for msg in h.init(ev, str(nonexistent)):
        msgs.append(msg)
    assert any("不存在" in m for m in msgs)


async def test_init_no_code_files(tmp_path: Path):
    fake_plugin = MagicMock()
    fake_plugin._config = {"file_remove_blacklist": []}
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(
        state=mgr,
        plugin_getter=lambda: fake_plugin,
        is_path_safe=lambda *args, **kwargs: (True, ""),
    )
    # 创建空目录(无代码文件)
    empty_dir = tmp_path / "empty_proj"
    empty_dir.mkdir()
    (empty_dir / "README.txt").write_text("just docs", encoding="utf-8")
    ev = _make_event()
    msgs = []
    async for msg in h.init(ev, str(empty_dir)):
        msgs.append(msg)
    assert any("未找到代码文件" in m for m in msgs)


# ── AgentsmdHandlers.load 边界 ─────────────────────


async def test_load_already_loaded_returns_warning(tmp_path: Path):
    fake_plugin = MagicMock()
    fake_plugin._config = {"file_remove_blacklist": []}
    # 创建一个含代码文件 + AGENTS.md 的目录
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("x=1", encoding="utf-8")
    (proj / "AGENTS.md").write_text("# hi", encoding="utf-8")
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(
        state=mgr,
        plugin_getter=lambda: fake_plugin,
        is_path_safe=lambda *args, **kwargs: (True, ""),
    )
    ev = _make_event("umo-1")
    msgs = []
    async for msg in h.load(ev, str(proj)):
        msgs.append(msg)
    assert any("已加载" in m for m in msgs)
    # state 已填充
    assert "umo-1" in mgr
    assert mgr.get("umo-1").last_content == "# hi"


async def test_load_no_agents_md(tmp_path: Path):
    fake_plugin = MagicMock()
    fake_plugin._config = {"file_remove_blacklist": []}
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("x=1", encoding="utf-8")
    # 没有 AGENTS.md
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(
        state=mgr,
        plugin_getter=lambda: fake_plugin,
        is_path_safe=lambda *args, **kwargs: (True, ""),
    )
    ev = _make_event()
    msgs = []
    async for msg in h.load(ev, str(proj)):
        msgs.append(msg)
    assert any("未找到 AGENTS.md" in m for m in msgs)


# ── AgentsmdHandlers.update 边界 ───────────────────


async def test_update_no_state_yields_error():
    fake_plugin = MagicMock()
    fake_plugin._config = {}
    mgr = AgentsStateManager()
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    msgs = []
    async for msg in h.update(ev):
        msgs.append(msg)
    assert any("未加载 AGENTS.md" in m for m in msgs)


async def test_update_no_provider_yields_error(tmp_path: Path):
    fake_plugin = MagicMock()
    fake_plugin._config = {}
    fake_plugin.context.get_using_provider = MagicMock(return_value=None)
    mgr = AgentsStateManager()
    # 先创建 AGENTS.md 文件,否则 handler 会先 yield "文件不存在"
    agents_md_file = tmp_path / "AGENTS.md"
    agents_md_file.write_text("OLD", encoding="utf-8")
    mgr.set(
        "umo-1",
        AgentsState(
            path=str(agents_md_file),
            directory=str(tmp_path),
            last_content="OLD",
            mtime=agents_md_file.stat().st_mtime,
        ),
    )
    h = AgentsmdHandlers(state=mgr, plugin_getter=lambda: fake_plugin, is_path_safe=lambda *args, **kwargs: (True, ""))
    ev = _make_event("umo-1")
    msgs = []
    async for msg in h.update(ev):
        msgs.append(msg)
    assert any("未配置 LLM Provider" in m for m in msgs)


# ── 导出符号完整性 ──────────────────────────────────


def test_package_exposes_all_expected_symbols():
    """tools.agentsmd 必须对外暴露完整 API。"""
    import tools.agentsmd as pkg

    expected = {
        "CODE_FILE_EXTENSIONS", "DEFAULT_AGENTS_MD", "DEFAULT_INIT_TEMPLATE",
        "DEFAULT_INJECTION_HEADER", "INJECTION_MARKER", "KEY_PROJECT_FILES",
        "PROJECT_PATH_PREFIX_TEMPLATE", "_SKIP_DIRS",
        "build_injection", "generate_agents_md_via_llm", "has_code_files",
        "resolve_init_template", "scan_project_context", "strip_code_fence",
        "strip_surrounding_quotes",
        "AgentsmdSubsystem", "AgentsmdHandlers", "AgentsState", "AgentsStateManager",
    }
    for name in expected:
        assert hasattr(pkg, name), f"tools.agentsmd missing symbol: {name}"