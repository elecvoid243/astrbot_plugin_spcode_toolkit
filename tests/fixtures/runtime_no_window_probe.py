"""Exercise patched wheel entry points under pythonw without starting Vivado."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import subprocess
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from _spcode_no_window import NO_WINDOW


def assert_flags(mock: Mock) -> None:
    """Assert flags reached the actual dependency subprocess API."""
    assert mock.call_count == 1, mock.call_count
    assert mock.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


async def probe(directory: Path) -> dict:
    """Check all five Vivado spawns and the other CLI wrappers."""
    import clang_format
    import cppcheck
    import ruff.__main__ as ruff_main
    from vivado_mcp.analysis import verilog_compile_check as verilog
    from vivado_mcp.vivado.gui_session import GuiSession
    from vivado_mcp.vivado.session import SubprocessSession

    for session in (SubprocessSession("fake-vivado"), GuiSession("fake-vivado")):
        spawn = AsyncMock(side_effect=OSError("expected test spawn failure"))
        with (
            patch("asyncio.create_subprocess_exec", spawn),
            patch(
                "vivado_mcp.vivado.gui_session._new_startup_log_path",
                return_value=directory / "startup.log",
            ),
        ):
            try:
                await session.start()
            except RuntimeError as exc:
                assert "expected test spawn failure" in str(exc)
            else:
                raise AssertionError("Mock spawn should fail")
        assert_flags(spawn)
        # NamedTemporaryFile belongs to the dependency and is normally cleaned on stop.
        if isinstance(session, GuiSession) and session._tmp_script:
            Path(session._tmp_script).unlink(missing_ok=True)

    cleanup = GuiSession("fake-vivado")
    cleanup._proc = SimpleNamespace(
        pid=12345, returncode=None, wait=AsyncMock(return_value=0)
    )
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    with patch("subprocess.run", run):
        await cleanup._cleanup_failed_spawn()
    assert_flags(run)
    assert run.call_args.args[0][0] == "taskkill"

    stop = GuiSession("fake-vivado")
    stop._proc = SimpleNamespace(
        pid=12345, returncode=None, wait=AsyncMock(side_effect=[TimeoutError(), 0])
    )
    with patch("subprocess.run", run := Mock()), patch("glob.glob", return_value=[]):
        await stop.stop()
    assert_flags(run)
    assert run.call_args.args[0][0] == "taskkill"

    result = subprocess.CompletedProcess([], 0, "", "")
    with (
        patch.object(verilog, "_detect_tool", return_value=("iverilog", "")),
        patch.object(verilog.shutil, "which", return_value="fake-iverilog.exe"),
        patch("subprocess.run", run := Mock(return_value=result)),
    ):
        verilog.compile_check(["test.v"])
    assert_flags(run)

    with patch("subprocess.call", call := Mock(return_value=0)):
        clang_format._run("clang-format")
    assert_flags(call)
    with patch("subprocess.call", call := Mock(return_value=0)):
        cppcheck._program("cppcheck", ["--version"])
    assert_flags(call)
    with (
        patch.object(sys, "argv", ["cppcheck", "--version"]),
        patch("subprocess.run", run := Mock(return_value=result)),
    ):
        cppcheck.cppcheck()
    assert_flags(run)
    with (
        patch.object(ruff_main, "find_ruff_bin", return_value="fake-ruff.exe"),
        patch("subprocess.run", run := Mock(return_value=result)),
    ):
        try:
            ruff_main._run()
        except SystemExit as exc:
            assert exc.code == 0
    assert_flags(run)

    # A real CUI child of this pythonw process must have no console window.
    child = await asyncio.to_thread(
        subprocess.run,
        [
            str(Path(sys.executable).with_name("python.exe")),
            "-I",
            "-c",
            "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        **NO_WINDOW,
    )
    assert child.stdout.strip() == "0", child.stdout
    return {"vivado_spawn_sites": 5, "cli_wrappers": 4, "child_console_window": 0}


if __name__ == "__main__":
    report = Path(sys.argv[1])
    try:
        # MCP normally gives its server pipe streams; pythonw alone has None.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            status = asyncio.run(probe(report.parent))
    except Exception:
        report.write_text(
            json.dumps({"error": traceback.format_exc()}), encoding="utf-8"
        )
        raise
    report.write_text(json.dumps(status), encoding="utf-8")
