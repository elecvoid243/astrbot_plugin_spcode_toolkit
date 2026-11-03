"""Real portable-runtime checks; no mocks of package discovery or execution."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts._runtime_patches import EXPECTED_CALLS, PATCH_ID, _spawn_calls
from tools import _tool_runtime as runtime
from tools import code_check, code_crap, code_format

pytestmark = pytest.mark.skipif(
    runtime.runtime_python() is None, reason="Standalone runtime not built"
)


@pytest.fixture(autouse=True)
def no_host_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make accidental host-interpreter or host-tool lookup fail visibly."""
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "not-a-python"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "not-packages"))
    monkeypatch.setenv("CONDA_PREFIX", str(tmp_path / "not-conda"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(runtime, "_IN_WORKER", False)


def test_interpreter_and_stdlib_are_self_contained() -> None:
    result = subprocess.run(
        [
            str(runtime.runtime_python()),
            "-I",
            "-c",
            "import json,sys,sysconfig; print(json.dumps([sys.prefix,sys.base_prefix,sysconfig.get_path('stdlib'),sys.path]))",
        ],
        env=runtime.runtime_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        **runtime._NO_WINDOW,
    )
    prefix, base_prefix, stdlib, paths = json.loads(result.stdout)
    assert Path(prefix) == Path(base_prefix) == runtime.runtime_root() / "python"
    assert Path(stdlib).is_relative_to(runtime.runtime_root())
    assert all(Path(path).is_relative_to(runtime.runtime_root()) for path in paths)


def test_wheel_subprocess_patches_are_complete_and_recorded() -> None:
    root = runtime.runtime_root()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    patches = manifest["source_patches"]
    assert patches["id"] == PATCH_ID
    assert set(patches["files"]) == {*EXPECTED_CALLS, "_spcode_no_window.py"}
    site = root / "python/Lib/site-packages"
    for relative, info in patches["files"].items():
        source = (site / relative).read_bytes()
        assert hashlib.sha256(source).hexdigest() == info["sha256"]
        calls = _spawn_calls(ast.parse(source))
        assert len(calls) == info["spawn_calls"] == EXPECTED_CALLS.get(relative, 0)
        for call in calls:
            assert any(
                keyword.arg is None
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "_SPCODE_NO_WINDOW"
                for keyword in call.keywords
            )


@pytest.mark.skipif(sys.platform != "win32", reason="pythonw Windows regression")
def test_pythonw_dependency_spawns_have_no_console(tmp_path: Path) -> None:
    report = tmp_path / "probe.json"
    subprocess.run(
        [
            str(runtime.runtime_python().with_name("pythonw.exe")),
            "-I",
            str(Path(__file__).with_name("fixtures") / "runtime_no_window_probe.py"),
            str(report),
        ],
        env=runtime.runtime_environment(),
        check=True,
        timeout=30,
        **runtime._NO_WINDOW,
    )
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "vivado_spawn_sites": 5,
        "cli_wrappers": 4,
        "child_console_window": 0,
    }


@pytest.mark.parametrize("script", ["install_tool_runtime.py", "install_vivado_mcp.py"])
def test_installer_entrypoints_run_in_embedded_python(script: str) -> None:
    result = subprocess.run(
        [
            str(runtime.runtime_python()),
            "-I",
            str(runtime.runtime_root().parent / "scripts" / script),
            "--help",
        ],
        env=runtime.runtime_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        **runtime._NO_WINDOW,
    )
    assert "--requirements" in result.stdout


def test_python_format_check_and_crap_use_bundle(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def f( x ):\n return x+1\n", encoding="utf-8")
    formatted = code_format.format(str(source))
    assert formatted["ok"] is True and formatted["changed"] is True
    assert code_format.format(str(source))["changed"] is False
    assert code_check.check(str(source))["count"] == 0
    risk = code_crap.check(str(source))
    assert risk["ok"] is True and risk["engine"] == "crap4py"


def test_cpp_format_check_and_crap_use_bundle(tmp_path: Path) -> None:
    source = tmp_path / "sample.cpp"
    source.write_text("int f(int x){if(x>0){return x;}return 0;}\n", encoding="utf-8")
    assert code_format.format(str(source))["ok"] is True
    assert code_format.format(str(source))["changed"] is False
    checked = code_check.check(str(source), linter="cppcheck")
    assert checked["ok"] is True
    risk = code_crap.check(str(source))
    assert risk["ok"] is True and risk["engine"] == "lizard"


def test_cppcheck_reports_real_correctness_error(tmp_path: Path) -> None:
    source = tmp_path / "bad.cpp"
    source.write_text("int main(){int *p=nullptr; return *p;}\n", encoding="utf-8")
    result = code_check.check(str(source), linter="cppcheck")
    assert result["ok"] is True
    assert result["count"] > 0


def test_cpplint_can_run_without_host_python() -> None:
    result = subprocess.run(
        runtime.module_command("cpplint") + ["--version"],
        env=runtime.runtime_environment(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        **runtime._NO_WINDOW,
    )
    assert "2.0.2" in result.stdout + result.stderr


@pytest.mark.parametrize("name", ["clang-format", "cppcheck"])
def test_native_cpp_runtime_is_bundled_adjacent_to_executable(name: str) -> None:
    executable = Path(runtime.bundled_binary(name)[0])
    assert (executable.parent / "msvcp140.dll").is_file()
    assert (executable.parent / "vcruntime140.dll").is_file()
    assert (executable.parent / "vcruntime140_1.dll").is_file()


def test_ripgrep_search_uses_bundle(tmp_path: Path) -> None:
    from tools._runtime_search import search

    source = tmp_path / "search.txt"
    source.write_text("bundled-runtime-token\n", encoding="utf-8")
    lines = search(
        patterns=["bundled-runtime-token"],
        paths=[str(tmp_path)],
        line_number=True,
        heading=False,
    )
    assert any("search.txt:1:" in line for line in lines)


def test_mcp_server_initializes_and_lists_tools() -> None:
    script = """
import asyncio, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable, args=["-I", "-m", "vivado_mcp"], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            print(json.dumps([tool.name for tool in result.tools]))
asyncio.run(main())
"""
    result = subprocess.run(
        [str(runtime.runtime_python()), "-I", "-c", script],
        env=runtime.runtime_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=45,
        **runtime._NO_WINDOW,
    )
    assert len(json.loads(result.stdout)) > 0


def test_offline_archive_runs_after_relocation(tmp_path: Path) -> None:
    archive = runtime.runtime_root().parent / "dist/spcode-tool-runtime-win-amd64.zip"
    if not archive.is_file():
        pytest.skip("Offline archive not built")
    deployed = tmp_path / "relocated plugin with spaces"
    with zipfile.ZipFile(archive) as package:
        package.extractall(deployed)
    shutil.copytree(
        runtime.runtime_root().parent / "tools",
        deployed / "tools",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    source = deployed / "sample.py"
    source.write_text("x=1\n", encoding="utf-8")
    result = subprocess.run(
        [
            str(deployed / "runtime/python/python.exe"),
            "-I",
            str(deployed / "tools/_runtime_worker.py"),
        ],
        input=json.dumps(
            {"operation": "code_format", "parameters": {"filepath": str(source)}}
        ),
        env=runtime.runtime_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
        **runtime._NO_WINDOW,
    )
    formatted = json.loads(result.stdout)
    assert formatted["ok"] is True and formatted["changed"] is True
    assert source.read_text(encoding="utf-8") == "x = 1\n"
