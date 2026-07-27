"""项目路径注入(tools/project/inject.py)单元测试。

v2.22 (2026-07-27): 项目路径注入与 /agentsmd 解耦。
此前路径前缀("你正在处理的项目工作路径为: ...")由 agentsmd 子系统的
``build_injection(content, directory=...)`` 附带注入;解耦后改由 project
子系统独立注入 — 只要 ``/project load`` 成功(含 ``no_agentsmd`` 空壳
load),每次 LLM 请求前都会把项目工作路径注入 system_prompt 末尾。

覆盖:
- tools/_guidance_text.py 的 PROJECT_PATH_* 常量
- tools/project/inject.py 的 inject_project_path()
- main.py 的 _project_inject_path 钩子(结构性 / 注册性 / 委托性)

Author: elecvoid243, 2026-07-27
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools._guidance_text import (  # noqa: E402
    PROJECT_PATH_GUIDANCE_TEMPLATE,
    PROJECT_PATH_MARKER,
)
from tools.project import state as _proj_state  # noqa: E402
from tools.project.inject import inject_project_path  # noqa: E402

UMO = "test:umo:path"


# ── 工厂 / 辅助函数 ─────────────────────────────────


def _make_event(umo: str = UMO) -> MagicMock:
    ev = MagicMock()
    ev.unified_msg_origin = umo
    return ev


def _make_req(sp: str | None = "") -> MagicMock:
    req = MagicMock()
    req.system_prompt = sp
    return req


def _put(directory: str = "/proj/demo", umo: str = UMO, **extra) -> None:
    """登记一个已加载项目到 tools.project.state 单例。"""
    info = {"directory": directory, "loaded_at": 0.0}
    info.update(extra)
    _proj_state.put(umo, info)


# ── guidance 常量 ──────────────────────────────────


def test_guidance_template_renders_path_and_worktree_hint():
    """模板渲染后必须含路径声明 + git worktree 指引,且含 marker。"""
    text = PROJECT_PATH_GUIDANCE_TEMPLATE.format(directory="/proj/x")
    assert "你正在处理的项目工作路径为: /proj/x" in text
    assert "优先使用git worktree" in text
    assert PROJECT_PATH_MARKER in text


# ── inject_project_path: 状态门控 ──────────────────


def test_no_project_loaded_returns_false():
    """未加载项目 → 返回 False,system_prompt 不变。"""
    req = _make_req("orig")
    assert inject_project_path(_make_event(), req) is False
    assert req.system_prompt == "orig"


def test_loaded_project_injects_path():
    """已加载项目 → 返回 True,system_prompt 含路径 + worktree 指引。"""
    _put("/proj/demo")
    req = _make_req("orig")
    assert inject_project_path(_make_event(), req) is True
    assert "你正在处理的项目工作路径为: /proj/demo" in req.system_prompt
    assert "优先使用git worktree" in req.system_prompt


def test_inject_is_idempotent_via_marker():
    """重复调用 → 第二次返回 False,路径只出现一次(marker 防重复)。"""
    _put("/proj/demo")
    req = _make_req("")
    assert inject_project_path(_make_event(), req) is True
    assert inject_project_path(_make_event(), req) is False
    assert req.system_prompt.count("你正在处理的项目工作路径为: /proj/demo") == 1


def test_none_system_prompt_handled():
    """system_prompt 为 None → 注入后不以换行开头,且含路径。"""
    _put("/proj/demo")
    req = _make_req(None)
    assert inject_project_path(_make_event(), req) is True
    assert req.system_prompt is not None
    assert not req.system_prompt.startswith("\n")
    assert "你正在处理的项目工作路径为: /proj/demo" in req.system_prompt


def test_no_agentsmd_skip_still_injects_path():
    """核心需求: /project load <dir> no_agentsmd 后仍注入项目路径。

    skipped_substeps={"agentsmd"} 只应抑制 AGENTS.md 内容注入
    (由 agentsmd 子系统自己判断),不影响 project 子系统的路径注入。
    """
    _put("/proj/demo", skipped_substeps={"agentsmd"})
    req = _make_req("")
    assert inject_project_path(_make_event(), req) is True
    assert "你正在处理的项目工作路径为: /proj/demo" in req.system_prompt


def test_empty_directory_returns_false():
    """state 中 directory 为空字符串(防御旧状态) → 不注入空路径。"""
    _put("")
    req = _make_req("orig")
    assert inject_project_path(_make_event(), req) is False
    assert req.system_prompt == "orig"


def test_after_unload_no_injection():
    """/project unload(state.pop)后 → 不再注入。"""
    _put("/proj/demo")
    _proj_state.pop(UMO)
    req = _make_req("orig")
    assert inject_project_path(_make_event(), req) is False
    assert req.system_prompt == "orig"


def test_existing_system_prompt_preserved_as_prefix():
    """已有 system_prompt → 路径追加到末尾,原内容保留在前。"""
    _put("/proj/demo")
    req = _make_req("original system")
    assert inject_project_path(_make_event(), req) is True
    assert req.system_prompt.startswith("original system")
    assert "你正在处理的项目工作路径为: /proj/demo" in req.system_prompt


# ── main.py 钩子:结构性 / 注册性 / 委托性 ──────────
# 模式与 tests/test_agentsmd_injection.py 一致(PR-5 漏接钩子回归测试)。


def test_main_hook_exists_and_is_coroutine():
    """SPCodeToolkit._project_inject_path 必须存在且为 async 方法。"""
    from astrbot_plugin_spcode_toolkit import main as _main_mod

    assert hasattr(_main_mod.SPCodeToolkit, "_project_inject_path"), (
        "SPCodeToolkit 缺少 _project_inject_path 方法 — "
        "路径注入钩子未挂到插件类上,AstrBot 框架不会调用它。"
    )
    method = getattr(_main_mod.SPCodeToolkit, "_project_inject_path")
    assert asyncio.iscoroutinefunction(method), (
        "_project_inject_path 必须是 async 函数,"
        "AstrBot 框架要求 on_llm_request 钩子为 async。"
    )


def test_main_hook_registered_to_on_llm_request_event():
    """钩子必须被 @filter.on_llm_request() 注册到 OnLLMRequestEvent。"""
    from astrbot_plugin_spcode_toolkit import main as _main_mod

    from astrbot.core.star.star_handler import (
        EventType,
        star_handlers_registry,
    )

    method = _main_mod.SPCodeToolkit._project_inject_path
    expected_full_name = f"{method.__module__}_{method.__name__}"
    handler = star_handlers_registry.get_handler_by_full_name(expected_full_name)

    assert handler is not None, (
        f"未找到 full_name={expected_full_name!r} 的 handler — "
        "@filter.on_llm_request() 装饰器未对该方法生效。"
    )
    assert handler.event_type == EventType.OnLLMRequestEvent, (
        f"handler.event_type 应为 OnLLMRequestEvent,实际为 {handler.event_type}"
    )


def test_main_hook_delegates_to_inject_project_path(monkeypatch):
    """钩子应把 (event, req) 委托给 tools.project.inject.inject_project_path。"""
    from astrbot_plugin_spcode_toolkit import main as _main_mod

    called: list[tuple] = []

    def _fake_inject(event, req):
        called.append((event, req))
        return True

    monkeypatch.setattr(_main_mod, "inject_project_path", _fake_inject)

    plugin = _main_mod.SPCodeToolkit.__new__(_main_mod.SPCodeToolkit)
    ev = _make_event("umo-delegate")
    req = _make_req("")
    asyncio.run(plugin._project_inject_path(ev, req))

    assert called == [(ev, req)], (
        "_project_inject_path 应原样委托 (event, req) 给 inject_project_path"
    )
