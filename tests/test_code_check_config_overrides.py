"""Tests for keyword-only cppcheck config overrides in tools.code_check."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def clear_overrides():
    from tools import code_check

    saved_enable = getattr(code_check, "CPPCHECK_ENABLE", None)
    saved_mode = getattr(code_check, "SHORTCIRCUIT_MODE", None)
    if hasattr(code_check, "CPPCHECK_ENABLE"):
        delattr(code_check, "CPPCHECK_ENABLE")
    if hasattr(code_check, "SHORTCIRCUIT_MODE"):
        delattr(code_check, "SHORTCIRCUIT_MODE")
    yield
    if hasattr(code_check, "CPPCHECK_ENABLE"):
        delattr(code_check, "CPPCHECK_ENABLE")
    if hasattr(code_check, "SHORTCIRCUIT_MODE"):
        delattr(code_check, "SHORTCIRCUIT_MODE")
    if saved_enable is not None:
        code_check.CPPCHECK_ENABLE = saved_enable
    if saved_mode is not None:
        code_check.SHORTCIRCUIT_MODE = saved_mode


def test_explicit_cppcheck_uses_enable_override(tmp_path: Path):
    from tools import code_check

    source = tmp_path / "a.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with (
        patch.object(code_check, "_find_cppcheck", return_value=["fake-cppcheck"]),
        patch.object(code_check.subprocess, "run", side_effect=fake_run),
    ):
        result = code_check.check(
            str(source),
            "cppcheck",
            cppcheck_enable=["warning", "style"],
        )

    assert result["ok"] is True
    assert "--enable=warning,style" in captured["args"]


def test_auto_cppcheck_uses_shortcircuit_override(tmp_path: Path):
    from tools import code_check

    source = tmp_path / "a.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    cppcheck_stderr = (
        f"{source}:1:5: warning: example warning  [exampleWarning]\n"
    )

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="",
            stderr=cppcheck_stderr,
        )

    with (
        patch.object(code_check, "_find_cppcheck", return_value=["fake-cppcheck"]),
        patch.object(code_check.subprocess, "run", side_effect=fake_run),
    ):
        result = code_check.check(
            str(source),
            "auto",
            cppcheck_shortcircuit="warning",
        )

    assert result["linter"] == "cppcheck"
    assert result["count"] == 1


def test_none_overrides_preserve_module_behavior(tmp_path: Path):
    from tools import code_check

    source = tmp_path / "a.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    code_check.CPPCHECK_ENABLE = ["performance"]
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with (
        patch.object(code_check, "_find_cppcheck", return_value=["fake-cppcheck"]),
        patch.object(code_check.subprocess, "run", side_effect=fake_run),
    ):
        code_check.check(str(source), "cppcheck", cppcheck_enable=None)

    assert "--enable=performance" in captured["args"]
