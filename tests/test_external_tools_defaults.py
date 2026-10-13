"""External-tool defaults and preservation of configured installation paths."""

from pathlib import Path

import pytest


def test_external_tool_defaults_use_astrbot_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import _external_tools

    monkeypatch.setattr(_external_tools, "get_astrbot_root", lambda: tmp_path)

    defaults = _external_tools.external_tool_defaults()

    assert defaults == {
        "es_path": str(tmp_path / "external_tools" / "Everything" / "es.exe"),
        "cppcheck_path": str(tmp_path / "external_tools" / "cppcheck" / "cppcheck.exe"),
    }


def test_external_tool_defaults_do_not_override_explicit_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import _external_tools

    monkeypatch.setattr(_external_tools, "get_astrbot_root", lambda: tmp_path)
    explicit = {
        "es_path": "D:/custom/es.exe",
        "codegraph_install_dir": "D:/custom/codegraph",
        "cppcheck_path": "D:/custom/cppcheck.exe",
    }

    merged = _external_tools.merge_external_tool_defaults(explicit)

    assert merged == explicit


def test_codegraph_config_remains_empty_for_runtime_detection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bundled defaults must not take priority over the tc_memory installation."""
    from tools import _external_tools

    monkeypatch.setattr(_external_tools, "get_astrbot_root", lambda: tmp_path)

    merged = _external_tools.merge_external_tool_defaults({"codegraph_install_dir": ""})

    assert merged["codegraph_install_dir"] == ""
