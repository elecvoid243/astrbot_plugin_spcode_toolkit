"""Reproducible source patches for subprocesses in the pinned tool wheels.

Only files in the standalone runtime are changed. AstrBot's subprocess module
and host packages are never monkey-patched. Unexpected upstream spawn sites
stop the build so dependency updates require an explicit review.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

PATCH_ID = "windows-no-window-v1"
_HELPER_NAME = "_spcode_no_window.py"
_IMPORT = "from _spcode_no_window import NO_WINDOW as _SPCODE_NO_WINDOW\n"
_HELPER = '''"""Window policy for subprocesses in the bundled tool packages."""

import subprocess
import sys

NO_WINDOW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)
'''
_SPAWN_NAMES = {
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
}
_PACKAGES = ("vivado_mcp", "ruff", "clang_format", "cppcheck")
# WHY: review all new spawn sites when changing the locked wheel versions.
EXPECTED_CALLS = {
    "vivado_mcp/analysis/verilog_compile_check.py": 1,
    "vivado_mcp/vivado/gui_session.py": 3,
    "vivado_mcp/vivado/session.py": 1,
    "ruff/__main__.py": 1,
    "clang_format/__init__.py": 1,
    "clang_format/clang_format_diff.py": 1,
    "clang_format/git_clang_format.py": 12,
    "cppcheck/__init__.py": 2,
    "cppcheck/Cppcheck/addons/cppcheckdata.py": 1,
    "cppcheck/Cppcheck/cppcheck-htmlreport": 1,
}


def _spawn_calls(tree: ast.Module) -> list[ast.Call]:
    """Find direct and aliased subprocess APIs in a wheel module."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.asname:
                    aliases[item.asname] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            first, dot, rest = name.partition(".")
            name = aliases.get(first, first) + (dot + rest if dot else "")
            if name in {"os.system", "os.popen"}:
                raise ValueError(f"Unreviewed shell spawn: {name}")
            if name in _SPAWN_NAMES:
                calls.append(node)
    return calls


def patch_source(source: str) -> tuple[str, int]:
    """Insert window kwargs while preserving upstream comments and encoding."""
    tree = ast.parse(source)
    calls = _spawn_calls(tree)
    if not calls:
        return source, 0
    raw = source.encode("utf-8")
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    edits: list[tuple[int, bytes]] = []
    for call in calls:
        if any(
            keyword.arg is None
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "_SPCODE_NO_WINDOW"
            for keyword in call.keywords
        ):
            continue
        if any(keyword.arg == "creationflags" for keyword in call.keywords):
            raise ValueError("Upstream creationflags changed; review the window policy")
        # WHY: AST columns are UTF-8 byte offsets, including in Chinese sources.
        arguments = [*call.args, *(keyword.value for keyword in call.keywords)]
        if arguments:
            last = max(
                arguments, key=lambda node: (node.end_lineno, node.end_col_offset)
            )
            position = offsets[last.end_lineno - 1] + last.end_col_offset
            edits.append((position, b", **_SPCODE_NO_WINDOW"))
        else:
            position = offsets[call.end_lineno - 1] + call.end_col_offset - 1
            edits.append((position, b"**_SPCODE_NO_WINDOW"))
    if _IMPORT not in source:
        first = 0
        for node in tree.body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ) or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
                first = node.end_lineno
            else:
                break
        position = offsets[first] if first else offsets[tree.body[0].lineno - 1]
        edits.append((position, ("\n" + _IMPORT).encode("utf-8")))
    for position, text in sorted(edits, reverse=True):
        raw = raw[:position] + text + raw[position:]
    patched = raw.decode("utf-8")
    compile(patched, "<patched runtime dependency>", "exec")
    return patched, len(calls)


def apply_runtime_patches(python_root: Path) -> dict[str, Any]:
    """Patch all reviewed wheel call sites and return manifest provenance."""
    site = python_root / "Lib" / "site-packages"
    sources = [path for package in _PACKAGES for path in (site / package).rglob("*.py")]
    sources.append(site / "cppcheck/Cppcheck/cppcheck-htmlreport")
    counts: dict[str, int] = {}
    patched_files = {}
    for path in sorted(sources):
        source, count = patch_source(path.read_text(encoding="utf-8"))
        if count:
            relative = path.relative_to(site).as_posix()
            counts[relative] = count
            patched_files[relative] = source
    if counts != EXPECTED_CALLS:
        raise ValueError(
            f"Upstream subprocess sites changed; review runtime patches: {counts}"
        )
    patched_files[_HELPER_NAME] = _HELPER
    manifest = {}
    for relative, source in sorted(patched_files.items()):
        (site / relative).write_text(source, encoding="utf-8", newline="\n")
        manifest[relative] = {
            "sha256": hashlib.sha256((site / relative).read_bytes()).hexdigest(),
            "spawn_calls": counts.get(relative, 0),
        }
    return {"id": PATCH_ID, "files": manifest}
