"""Codegraph directory priority and MCP startup without external services."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools import _codegraph_mcp, _external_tools
from tools.codegraph import CodegraphManager, bootstrap
from tools.codegraph import manager as manager_module

Installations = tuple[Path, Path, Path]


@pytest.fixture
def installations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Installations:
    """Use an isolated AstrBot root and a separately located plugin directory."""
    root = tmp_path / "astrbot"
    plugins = tmp_path / "plugin-store"
    monkeypatch.setattr(_external_tools, "get_astrbot_root", lambda: root)
    monkeypatch.setattr(_external_tools, "get_astrbot_plugin_path", lambda: plugins)
    monkeypatch.setattr(_codegraph_mcp.sys, "platform", "win32")
    return (
        tmp_path / "configured",
        plugins / "astrbot_plugin_tc_memory" / "standalone" / "codegraph-win32-x64",
        root / "external_tools" / "codegraph-win32-x64",
    )


def _deploy(directory: Path, *, node_name: str = "node.exe", flat: bool = False) -> Path:
    """Create the package layout without executing Node or codegraph."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / node_name).write_bytes(b"")
    entry = directory / "codegraph.js" if flat else directory / "lib/dist/bin/codegraph.js"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("", encoding="utf-8")
    return entry


def test_configured_directory_has_highest_priority(installations: Installations) -> None:
    configured, memory, bundled = installations
    entry = _deploy(configured)
    _deploy(memory)
    _deploy(bundled)

    cfg = _codegraph_mcp.detect_codegraph_launcher(str(configured))

    assert cfg == {
        "type": "stdio",
        "command": str(configured / "node.exe"),
        "args": ["--liftoff-only", str(entry), "serve", "--mcp"],
    }


@pytest.mark.parametrize("install_dir", [None, "", "   "])
def test_memory_directory_precedes_bundled(
    installations: Installations, install_dir: str | None
) -> None:
    _, memory, bundled = installations
    _deploy(memory)
    _deploy(bundled)

    cfg = _codegraph_mcp.detect_codegraph_launcher(install_dir)

    assert cfg is not None
    assert cfg["command"] == str(memory / "node.exe")


def test_invalid_configured_directory_falls_back_to_memory(
    installations: Installations,
) -> None:
    configured, memory, bundled = installations
    _deploy(memory)
    _deploy(bundled)

    cfg = _codegraph_mcp.detect_codegraph_launcher(str(configured))

    assert cfg is not None
    assert cfg["command"] == str(memory / "node.exe")


@pytest.mark.parametrize(
    "layout", ["missing", "empty", "node_only", "script_only", "node_dir", "script_dir"]
)
def test_unavailable_memory_directory_falls_back_to_bundled(
    installations: Installations, layout: str
) -> None:
    _, memory, bundled = installations
    if layout != "missing":
        memory.mkdir(parents=True)
    if layout in {"node_only", "node_dir", "script_dir"}:
        if layout == "node_dir":
            (memory / "node.exe").mkdir()
        else:
            (memory / "node.exe").write_bytes(b"")
    if layout in {"script_only", "node_dir", "script_dir"}:
        entry = memory / "lib/dist/bin/codegraph.js"
        entry.parent.mkdir(parents=True)
        if layout == "script_dir":
            entry.mkdir()
        else:
            entry.write_text("", encoding="utf-8")
    _deploy(bundled)

    cfg = _codegraph_mcp.detect_codegraph_launcher()

    assert cfg is not None
    assert cfg["command"] == str(bundled / "node.exe")


def test_incomplete_configured_directory_falls_back_to_bundled(
    installations: Installations,
) -> None:
    configured, _, bundled = installations
    configured.mkdir(parents=True)
    (configured / "node.exe").write_bytes(b"")
    _deploy(bundled)

    cfg = _codegraph_mcp.detect_codegraph_launcher(str(configured))

    assert cfg is not None
    assert cfg["command"] == str(bundled / "node.exe")


def test_no_available_installation_returns_none(installations: Installations) -> None:
    configured, _, _ = installations
    assert _codegraph_mcp.detect_codegraph_launcher(str(configured)) is None


def test_flat_script_layout_is_supported(installations: Installations) -> None:
    _, memory, _ = installations
    entry = _deploy(memory, flat=True)

    cfg = _codegraph_mcp.detect_codegraph_launcher()

    assert cfg is not None
    assert str(entry) in cfg["args"]


def test_standard_script_precedes_flat_script(installations: Installations) -> None:
    _, memory, _ = installations
    entry = _deploy(memory)
    (memory / "codegraph.js").write_text("", encoding="utf-8")

    cfg = _codegraph_mcp.detect_codegraph_launcher()

    assert cfg is not None
    assert cfg["args"][1] == str(entry)


def test_unsafe_configured_directory_falls_back_to_memory(
    installations: Installations,
) -> None:
    _, memory, _ = installations
    _deploy(memory)

    cfg = _codegraph_mcp.detect_codegraph_launcher("C:/Windows/codegraph")

    assert cfg is not None
    assert cfg["command"] == str(memory / "node.exe")


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_unix_installation_uses_node(
    installations: Installations, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    configured, _, _ = installations
    _deploy(configured, node_name="node", flat=True)
    monkeypatch.setattr(_codegraph_mcp.sys, "platform", platform)

    cfg = _codegraph_mcp.detect_codegraph_launcher(str(configured))

    assert cfg is not None
    assert cfg["command"] == str(configured / "node")


@pytest.mark.parametrize("config", [{}, {"codegraph_install_dir": ""}])
def test_build_mcp_cfg_automatically_finds_memory(
    installations: Installations, config: dict[str, str]
) -> None:
    _, memory, _ = installations
    _deploy(memory)
    plugin = SimpleNamespace(_config={**config, "codegraph_project": "G:/my project"})

    cfg = bootstrap.build_mcp_cfg(plugin)

    assert cfg is not None
    assert cfg["command"] == str(memory / "node.exe")
    assert cfg["args"][-2:] == ["--path", "G:/my project"]


@pytest.mark.asyncio
async def test_bootstrap_starts_from_memory_without_configured_directory(
    installations: Installations, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, memory, _ = installations
    _deploy(memory)
    manager = SimpleNamespace(mcp_server_runtime={}, enable_mcp_server=AsyncMock())
    plugin = SimpleNamespace(
        _config={},
        context=SimpleNamespace(get_llm_tool_manager=lambda: manager),
    )
    monkeypatch.setattr(bootstrap, "ensure_stdio_command", lambda command: True)

    await bootstrap.bootstrap_mcp(plugin)

    manager.enable_mcp_server.assert_awaited_once()
    assert manager.enable_mcp_server.call_args.kwargs["config"]["command"] == str(
        memory / "node.exe"
    )


@pytest.mark.asyncio
async def test_bootstrap_skips_when_all_directories_unavailable(
    installations: Installations,
) -> None:
    plugin = SimpleNamespace(_config={}, context=MagicMock())

    await bootstrap.bootstrap_mcp(plugin)

    plugin.context.get_llm_tool_manager.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("subcommand", ["init", "uninit"])
async def test_cli_uses_memory_installation(
    installations: Installations,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    subcommand: str,
) -> None:
    _, memory, bundled = installations
    entry = _deploy(memory)
    _deploy(bundled)
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("", encoding="utf-8")
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(manager_module.asyncio, "create_subprocess_exec", spawn)
    event = SimpleNamespace(plain_result=lambda message: message)
    manager = CodegraphManager(SimpleNamespace(_config={}))

    messages = [
        message async for message in getattr(manager, subcommand)(event, str(project))
    ]

    spawn.assert_awaited_once()
    expected = (str(memory / "node.exe"), str(entry), subcommand, str(project.resolve()))
    if subcommand == "uninit":
        expected += ("--force",)
    assert spawn.call_args.args == expected
    assert any(message.startswith("✅") for message in messages)
