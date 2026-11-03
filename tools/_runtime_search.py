"""Keep ripgrep's Python interface while running it outside the AstrBot host."""

from __future__ import annotations

from typing import Any

from ._tool_runtime import invoke


def _call(operation: str, parameters: dict[str, Any]) -> list[str]:
    """Return search output or an error understood by the existing endpoints."""
    result = invoke(operation, parameters, timeout=4)
    if result.get("reason") == "timeout":
        raise TimeoutError(result["error"])
    if not result.get("ok"):
        raise ImportError(result.get("error", "Bundled ripgrep unavailable"))
    return result["lines"]


def search(**kwargs: Any) -> list[str]:
    """Execute python_ripgrep.search in the standalone runtime."""
    return _call("ripgrep_search", kwargs)


def files(**kwargs: Any) -> list[str]:
    """Execute python_ripgrep.files in the standalone runtime."""
    return _call("ripgrep_files", kwargs)
