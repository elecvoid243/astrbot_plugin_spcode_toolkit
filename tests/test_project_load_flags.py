"""/project load 的加载语义测试(2026-11-07 重设计)。

新契约:
- 只登记工作目录;
- 目录内已有 AGENTS.md 时注入 system_prompt,不存在时不生成;
- 不自动执行 codegraph init;
- replace=True 时旧项目只在新路径校验 + 注入完成后才被替换。

Author: elecvoid243
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402
from tools.project import state as _proj_state  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


def _make_event(umo: str = "test:flag:umo:001"):
    event = MagicMock()
    event.unified_msg_origin = umo
    event.plain_result = lambda x: x
    return event


def _make_plugin(
    *,
    agentsmd_enabled: bool = True,
    codegraph_enabled: bool = True,
):
    """Minimal plugin instance with the real agentsmd subsystem."""
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    plugin._config = {
        "agentsmd_enabled": agentsmd_enabled,
        "codegraph_enabled": codegraph_enabled,
        "codegraph_project": "",
        "file_remove_blacklist": None,
    }
    from tools.agentsmd import AgentsmdSubsystem

    plugin.agentsmd = AgentsmdSubsystem(
        plugin=plugin,
        is_path_safe=lambda *a, **kw: (True, ""),
    )
    from tools.project import ProjectManager

    plugin.project = ProjectManager(plugin)
    return plugin


def _count_substeps(plugin) -> dict[str, int]:
    """Replace the legacy sub-steps with call counters.

    ``init`` must stay at zero for every load; ``codegraph.init`` must stay
    at zero because codegraph indexing is manual now.
    """
    counts = {
        "agentsmd.init": 0,
        "agentsmd.load": 0,
        "codegraph.init": 0,
        "codegraph.set_project": 0,
    }

    def _factory(name: str):
        def _make(*args, **kwargs):
            counts[name] += 1

            async def _gen():
                yield "mock-substep-ok"

            return _gen()

        return _make

    for name in counts:
        subsystem_name, method_name = name.split(".", 1)
        setattr(
            getattr(plugin, subsystem_name),
            method_name,
            _factory(name),
        )
    return counts


def _run(coro):
    return asyncio.run(coro)


async def _drive(plugin, event, sub_command, *args):
    msgs = []
    async for msg in plugin.project.handle_subcommand(event, sub_command, *args):
        msgs.append(msg)
    return msgs


def _load(plugin, event, directory, *flags):
    return "".join(_run(_drive(plugin, event, "load", directory, *flags)))


def test_missing_agentsmd_loads_project_without_generating(tmp_path):
    plugin = _make_plugin()
    counts = _count_substeps(plugin)
    event = _make_event("test:no-agentsmd:001")

    text = _load(plugin, event, str(tmp_path))

    assert "✅ 项目已加载" in text
    assert "未找到 AGENTS.md" in text
    assert "不会自动生成" in text
    assert "codegraph 索引未自动初始化" in text
    assert counts == {
        "agentsmd.init": 0,
        "agentsmd.load": 0,
        "codegraph.init": 0,
        "codegraph.set_project": 0,
    }

    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["directory"] == str(tmp_path)
    assert info["agents_md"] is False
    assert info["skipped_substeps"] == {"agentsmd"}


def test_existing_agentsmd_is_loaded_but_not_initialized(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    counts = _count_substeps(plugin)
    event = _make_event("test:with-agentsmd:001")

    text = _load(plugin, event, str(tmp_path))

    assert "✅ 项目已加载" in text
    assert "AGENTS.md 注入到 system_prompt" in text
    assert counts["agentsmd.init"] == 0
    assert counts["agentsmd.load"] == 1
    assert counts["codegraph.init"] == 0

    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["agents_md"] is True
    assert info["skipped_substeps"] == set()


def test_no_agentsmd_skips_injection_even_when_file_exists(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    counts = _count_substeps(plugin)
    event = _make_event("test:no-agentsmd-flag:001")

    text = _load(plugin, event, str(tmp_path), "no_agentsmd")

    assert "✅ 项目已加载" in text
    assert "注入已跳过" in text
    assert counts["agentsmd.init"] == 0
    assert counts["agentsmd.load"] == 0
    assert _proj_state.get(event.unified_msg_origin)["skipped_substeps"] == {"agentsmd"}


def test_no_codegraph_is_a_backward_compatible_noop(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin()
    counts = _count_substeps(plugin)
    event = _make_event("test:no-codegraph:001")

    text = _load(plugin, event, str(tmp_path), "no_codegraph")

    assert "✅ 项目已加载" in text
    assert counts["agentsmd.load"] == 1
    assert counts["codegraph.init"] == 0


def test_agentsmd_disabled_skips_injection_but_still_loads(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# project\n", encoding="utf-8")
    plugin = _make_plugin(agentsmd_enabled=False)
    counts = _count_substeps(plugin)
    event = _make_event("test:agentsmd-disabled:001")

    text = _load(plugin, event, str(tmp_path))

    assert "✅ 项目已加载" in text
    assert "功能未启用" in text
    assert counts["agentsmd.load"] == 0
    assert _proj_state.get(event.unified_msg_origin)["agents_md"] is False


def test_missing_directory_fails_without_registering_state(tmp_path):
    plugin = _make_plugin()
    event = _make_event("test:missing-dir:001")
    missing = tmp_path / "does-not-exist"

    text = _load(plugin, event, str(missing))

    assert "❌ 目录不存在或不是目录" in text
    assert _proj_state.get(event.unified_msg_origin) is None


def test_replace_swaps_only_after_new_project_is_ready(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "AGENTS.md").write_text("# second\n", encoding="utf-8")

    plugin = _make_plugin()
    event = _make_event("test:replace:001")
    _load(plugin, event, str(first))
    assert _proj_state.get(event.unified_msg_origin)["directory"] == str(first)

    text = _load(plugin, event, str(second), "replace")

    assert "✅ 已替换旧项目" in text
    info = _proj_state.get(event.unified_msg_origin)
    assert info["directory"] == str(second)
    assert info["agents_md"] is True
    assert plugin.agentsmd.state.get(event.unified_msg_origin).directory == str(second)


def test_failed_replace_keeps_the_old_project(tmp_path):
    first = tmp_path / "first"
    first.mkdir()

    plugin = _make_plugin()
    event = _make_event("test:replace-fail:001")
    _load(plugin, event, str(first))

    text = _load(plugin, event, str(tmp_path / "missing"), "replace")

    assert "❌ 目录不存在或不是目录" in text
    info = _proj_state.get(event.unified_msg_origin)
    assert info is not None
    assert info["directory"] == str(first)


def test_create_flag_still_works(tmp_path):
    plugin = _make_plugin()
    event = _make_event("test:create:001")
    target = tmp_path / "new-project"

    text = _load(plugin, event, str(target), "create")

    assert "✅ 目录已创建" in text
    assert "✅ 项目已加载" in text
    assert target.is_dir()
    assert _proj_state.get(event.unified_msg_origin)["directory"] == str(target)


def test_duplicate_load_is_rejected_without_replace(tmp_path):
    plugin = _make_plugin()
    event = _make_event("test:duplicate:001")
    _load(plugin, event, str(tmp_path))

    text = _load(plugin, event, str(tmp_path))

    assert "当前会话已加载项目" in text
    assert _proj_state.get(event.unified_msg_origin)["directory"] == str(tmp_path)


def test_flags_are_parsed_in_any_order(tmp_path):
    plugin = _make_plugin()
    event = _make_event("test:flag-order:001")

    text = _load(plugin, event, "no_agentsmd", str(tmp_path))

    assert "✅ 项目已加载" in text
    assert _proj_state.get(event.unified_msg_origin) is not None


def test_only_flags_without_directory_reports_error():
    plugin = _make_plugin()
    event = _make_event("test:only-flags:001")

    text = _load(plugin, event, "no_agentsmd", "no_codegraph")

    assert "❌" in text
    assert _proj_state.get(event.unified_msg_origin) is None


# ── codegraph guidance hook (unchanged behavior) ──────────────────────


def _make_provider_request(system_prompt: str = ""):
    req = MagicMock()
    req.system_prompt = system_prompt
    return req


def test_guidance_hook_no_inject_when_codegraph_skipped():
    plugin = _make_plugin()
    umo = "test:hook:no_codegraph:001"
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
        assert (req.system_prompt or "") == ""
    finally:
        _proj_state.reset()


def test_guidance_hook_injects_when_codegraph_not_skipped():
    plugin = _make_plugin()
    umo = "test:hook:default:001"
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
        assert "Use Codegraph" in (req.system_prompt or "")
    finally:
        _proj_state.reset()
