"""Default paths for external tools bundled with AstrBot.

Author: elecvoid243
Date: 2026-07-24
"""

from pathlib import Path  # noqa: I001

from astrbot.core.utils.astrbot_path import get_astrbot_plugin_path, get_astrbot_root


_EXTERNAL_TOOL_RELATIVE_PATHS = {
    "es_path": Path("external_tools") / "Everything" / "es.exe",
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
    """Fill fixed tool defaults; codegraph directories are detected at runtime."""
    merged = dict(config)
    for key, value in external_tool_defaults().items():
        if merged.get(key) in (None, "", []):
            merged[key] = value
    return merged


def codegraph_install_dirs(install_dir: str | None = None) -> list[Path]:
    """Return codegraph directories in configured, tc_memory, bundled order."""
    directories: list[Path] = []
    if install_dir and install_dir.strip():
        directories.append(Path(install_dir.strip()))
    directories.extend(
        [
            Path(get_astrbot_plugin_path())
            / "astrbot_plugin_tc_memory"
            / "standalone"
            / "codegraph-win32-x64",
            Path(get_astrbot_root()) / "external_tools" / "codegraph-win32-x64",
        ]
    )
    return directories
