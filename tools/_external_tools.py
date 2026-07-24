"""Default paths for external tools bundled with AstrBot.

Author: elecvoid243
Date: 2026-07-24
"""

from pathlib import Path  # noqa: I001

from astrbot.core.utils.astrbot_path import get_astrbot_root


_EXTERNAL_TOOL_RELATIVE_PATHS = {
    "es_path": Path("external_tools") / "Everything" / "es.exe",
    "codegraph_install_dir": Path("external_tools") / "codegraph-win32-x64",
    "cppcheck_path": Path("external_tools") / "cppcheck" / "cppcheck.exe",
}


def external_tool_defaults() -> dict[str, str]:
    """Return bundled external-tool paths relative to the AstrBot root."""
    root = Path(get_astrbot_root())
    return {
        key: str(root / relative_path)
        for key, relative_path in _EXTERNAL_TOOL_RELATIVE_PATHS.items()
    }


def merge_external_tool_defaults(config: dict) -> dict:
    """Fill empty external-tool settings without overriding explicit values."""
    merged = dict(config)
    for key, value in external_tool_defaults().items():
        if merged.get(key) in (None, "", []):
            merged[key] = value
    return merged
