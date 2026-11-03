"""Execute plugin tool operations in a bundled, relocatable Python runtime."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ._path_safety import is_path_safe

logger = logging.getLogger(__name__)
_IN_WORKER = False
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_NO_WINDOW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


class ToolRuntimeError(RuntimeError):
    """The bundled interpreter failed or returned an invalid response."""


def runtime_root() -> Path:
    """Return the runtime adjacent to the plugin, independent of host paths."""
    return _PLUGIN_ROOT / "runtime"


def runtime_python() -> Path | None:
    """Select only the bundled interpreter; missing bundles never use the host."""
    root = runtime_root()
    python = (
        root / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    )
    if not python.is_file() or not (root / "manifest.json").is_file():
        return None
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("standalone") is not True:
        return None
    return python


def runtime_environment() -> dict[str, str]:
    """Preserve tool settings without inherited Python/conda path injection."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
        and key.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX", "CONDA_DEFAULT_ENV"}
    }


def module_command(module: str) -> list[str]:
    """Build a bundled command; Ruff directly uses its fixed Rust executable."""
    if module not in {"ruff", "cpplint", "vivado_mcp", "pip"}:
        raise ToolRuntimeError(f"Unsupported runtime module: {module}")
    if module == "ruff":
        return bundled_binary("ruff")
    python = runtime_python()
    return [str(python), "-I", "-m", module] if python else []


def bundled_binary(name: str) -> list[str]:
    """Select native package data, avoiding pip's absolute-path exe shims."""
    if name not in {"clang-format", "cppcheck", "ruff"}:
        raise ToolRuntimeError(f"Unsupported bundled binary: {name}")
    if name == "ruff":
        native = (
            runtime_root()
            / "python"
            / ("Scripts/ruff.exe" if sys.platform == "win32" else "bin/ruff")
        )
        return [str(native)] if native.is_file() else []
    package = "clang_format" if name == "clang-format" else name
    executable = name + (".exe" if sys.platform == "win32" else "")
    site = runtime_root() / "python" / "Lib" / "site-packages"
    for relative in ("data/bin", "Cppcheck", "data", "bin"):
        candidate = site / package / relative / executable
        if candidate.is_file():
            return [str(candidate)]
    return []


def invoke(operation: str, parameters: dict[str, Any], *, timeout: int = 120) -> dict:
    """Send one fixed operation over JSON stdin/stdout and preserve its result."""
    python = runtime_python()
    if python is None:
        return {
            "ok": False,
            "error": "插件运行环境不可用，请部署 runtime/ 或运行 scripts/install_tool_runtime.py",
        }
    try:
        paths = [parameters.get(name) for name in ("filepath", "lcov", "path")]
        paths.extend(parameters.get("paths") or [])
        for value in paths:
            if isinstance(value, (str, Path)) and value:
                safe, reason = is_path_safe(Path(value).resolve())
                if not safe:
                    return {"ok": False, "error": reason}
        request = json.dumps(
            {"operation": operation, "parameters": parameters}, default=str
        )
        logger.info("Executing bundled tool: %s", operation)
        result = subprocess.run(
            [str(python), "-I", str(_PLUGIN_ROOT / "tools" / "_runtime_worker.py")],
            input=request,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=runtime_environment(),
            **_NO_WINDOW,
        )
        if result.returncode:
            raise ToolRuntimeError(
                f"{operation} exited {result.returncode}: {result.stderr[-2000:]}"
            )
        response = json.loads(result.stdout)
        if not isinstance(response, dict):
            raise ToolRuntimeError(f"{operation} returned a non-object response")
        return response
    except subprocess.TimeoutExpired:
        logger.exception("Bundled tool timed out: %s", operation)
        return {"ok": False, "reason": "timeout", "error": f"{operation} 超时"}
    except (OSError, ValueError, subprocess.SubprocessError, ToolRuntimeError) as exc:
        logger.exception("Bundled tool failed: %s", operation)
        return {"ok": False, "error": f"独立运行环境执行失败: {exc}"}


def runtime_tool(operation: str) -> Callable:
    """Keep the public interface; execute its implementation inside the bundle."""

    def decorate(function: Callable) -> Callable:
        signature = inspect.signature(function)

        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> dict:
            if _IN_WORKER:
                return function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return invoke(operation, dict(bound.arguments))

        return wrapped

    return decorate
