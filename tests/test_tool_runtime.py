"""Contract tests for the standalone interpreter and fixed-operation transport."""

from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import _tool_runtime as runtime
from tools import code_check, code_format


@pytest.fixture
def bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Model the installed bundle without requiring downloads for unit tests."""
    root = tmp_path / "runtime"
    python = (
        root
        / "python"
        / ("python.exe" if runtime.sys.platform == "win32" else "bin/python3")
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake interpreter")
    ruff = (
        root
        / "python"
        / ("Scripts/ruff.exe" if runtime.sys.platform == "win32" else "bin/ruff")
    )
    ruff.parent.mkdir(parents=True, exist_ok=True)
    ruff.write_bytes(b"fake native ruff")
    (root / "manifest.json").write_text(
        '{"schema":1,"standalone":true}', encoding="utf-8"
    )
    monkeypatch.setattr(runtime, "runtime_root", lambda: root)
    monkeypatch.setattr(runtime, "_IN_WORKER", False)
    return python


def test_missing_runtime_does_not_fall_back_to_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runtime, "runtime_root", lambda: tmp_path / "missing")
    assert runtime.runtime_python() is None
    assert runtime.module_command("ruff") == []
    result = runtime.invoke("code_check", {"filepath": str(tmp_path / "x.py")})
    assert result["ok"] is False
    assert "runtime/" in result["error"]


def test_runtime_transport_uses_bundle_and_cleans_environment(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONHOME", "host python")
    monkeypatch.setenv("PYTHONPATH", "host packages")
    monkeypatch.setenv("CONDA_PREFIX", "host conda")
    monkeypatch.setenv("CLANG_FORMAT_STYLE", "google")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"ok":true}', ""))
    monkeypatch.setattr(runtime.subprocess, "run", run)

    assert runtime.invoke("code_check", {"filepath": "sample.py"}) == {"ok": True}

    (args,) = run.call_args.args
    assert args[:2] == [str(bundle), "-I"]
    assert args[-1].endswith("_runtime_worker.py")
    env = run.call_args.kwargs["env"]
    assert (
        "PYTHONHOME" not in env
        and "PYTHONPATH" not in env
        and "CONDA_PREFIX" not in env
    )
    assert env["CLANG_FORMAT_STYLE"] == "google"
    assert json.loads(run.call_args.kwargs["input"])["operation"] == "code_check"


def test_native_tools_do_not_search_host_path(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("CPPCHECK_PATH", str(tmp_path / "host-cppcheck.exe"))
    assert code_check._find_cppcheck() == []
    assert code_format._find_clang_format() == []
    assert code_check._find_ruff() == runtime.bundled_binary("ruff")


def test_missing_native_ruff_never_falls_back_to_user_install(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    native = Path(runtime.bundled_binary("ruff")[0])
    native.unlink()
    host = tmp_path / "host-tools"
    host.mkdir()
    (host / native.name).write_bytes(b"host executable")
    monkeypatch.setenv("PATH", str(host))
    monkeypatch.setattr(runtime, "_IN_WORKER", True)
    run = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    source = tmp_path / "sample.py"
    source.write_text("x=1\n", encoding="utf-8")
    assert code_check.check(str(source), linter="ruff")["ok"] is False
    assert code_format.format(str(source))["ok"] is False
    run.assert_not_called()


@pytest.mark.parametrize(
    "operation", ["code_check", "code_format", "code_crap", "file_remove"]
)
def test_public_interface_delegates_without_loading_host_dependencies(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from importlib import import_module

    module = import_module(f"tools.{operation}")
    name = (
        "format"
        if operation == "code_format"
        else "remove"
        if operation == "file_remove"
        else "check"
    )
    invoke = Mock(return_value={"ok": True, "from_bundle": True})
    monkeypatch.setattr(runtime, "invoke", invoke)
    function = getattr(module, name)
    assert function("sample.py")["from_bundle"] is True
    assert invoke.call_args.args[0] == operation
    assert (
        "path" in inspect.signature(function).parameters
        or "filepath" in inspect.signature(function).parameters
    )


def test_process_failure_is_structured(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 2, "", "broken runtime")),
    )
    result = runtime.invoke("code_check", {})
    assert result["ok"] is False and "broken runtime" in result["error"]


def test_timeout_is_structured(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("worker", 1)),
    )
    assert runtime.invoke("code_check", {}, timeout=1)["reason"] == "timeout"


def test_unsafe_path_never_starts_worker(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime, "is_path_safe", lambda path: (False, "blocked"))
    assert runtime.invoke("file_remove", {"path": "C:/Windows/file"}) == {
        "ok": False,
        "error": "blocked",
    }
    run.assert_not_called()


def test_worker_refuses_unknown_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools._runtime_worker import execute

    monkeypatch.setattr(runtime, "_IN_WORKER", False)
    with pytest.raises(ValueError, match="Unknown"):
        execute({"operation": "shell", "parameters": {"command": "anything"}})


@pytest.mark.parametrize("operation", ["code_check", "code_format"])
def test_ruff_launch_error_cannot_be_reported_as_success(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str
) -> None:
    monkeypatch.setattr(runtime, "_IN_WORKER", True)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [], 1, "", "ruff native binary missing"
            )
        ),
    )
    source = tmp_path / "sample.py"
    source.write_text("x=1\n", encoding="utf-8")
    if operation == "code_check":
        result = code_check.check(str(source), linter="ruff")
    else:
        result = code_format.format(str(source))
    assert result["ok"] is False
    assert "ruff native binary missing" in result["error"]
