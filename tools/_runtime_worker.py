"""Known tool operations over JSON; imported solely by the bundled interpreter."""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

OPERATIONS = {
    "code_check": ("code_check", "check"),
    "code_format": ("code_format", "format"),
    "code_crap": ("code_crap", "check"),
    "file_remove": ("file_remove", "remove"),
}


def execute(request: dict[str, Any]) -> dict:
    """Dispatch a known operation without importing the AstrBot host."""
    from tools import _tool_runtime

    _tool_runtime._IN_WORKER = True
    operation = request.get("operation")
    parameters = request.get("parameters", {})
    if not isinstance(parameters, dict):
        raise TypeError("Worker parameters must be an object")
    if operation == "find_vivado":
        from vivado_mcp.config import find_vivado

        return {"ok": True, "path": find_vivado()}
    if operation in {"ripgrep_search", "ripgrep_files"}:
        import python_ripgrep

        function = (
            python_ripgrep.search
            if operation == "ripgrep_search"
            else python_ripgrep.files
        )
        return {"ok": True, "lines": function(**parameters)}
    if operation == "trash_file":
        from tools._recycle import trash_file

        trash_file(Path(parameters["path"]))
        return {"ok": True}
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown worker operation: {operation!r}")
    module_name, function_name = OPERATIONS[operation]
    module = importlib.import_module(f"tools.{module_name}")
    return getattr(module, function_name)(**parameters)


def main() -> None:
    """Keep stdout exclusively for the JSON response, even on tool failures."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise TypeError("Worker request must be an object")
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(request)
    except Exception as exc:
        logging.getLogger(__name__).exception("Tool worker failed")
        result = {"ok": False, "error": str(exc)}
    json.dump(result, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
