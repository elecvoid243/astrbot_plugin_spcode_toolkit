"""Verify source patches survive rebuilds without touching the host runtime."""

import ast
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts._runtime_patches import (
    _HELPER,
    EXPECTED_CALLS,
    apply_runtime_patches,
    patch_source,
)


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_generated_window_kwargs_are_platform_specific(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    namespace = {}
    exec(_HELPER, namespace)  # noqa: S102 - execute only the builder's fixed helper
    assert namespace["NO_WINDOW"] == (
        {"creationflags": 0x08000000} if platform == "win32" else {}
    )


def test_source_patch_preserves_unicode_comments_and_future_imports() -> None:
    source = '''#!/usr/bin/env python
"""说明。"""
from __future__ import annotations
import subprocess as sp
from asyncio import create_subprocess_exec as spawn

# 保留上游的注释
sp.run(["中文"], check=False,)
async def start():
    return await spawn("工具", env={})
'''
    patched, count = patch_source(source)
    assert count == 2
    assert "# 保留上游的注释" in patched
    assert '["中文"]' in patched
    assert patched.index("from __future__") < patched.index("from _spcode_no_window")
    compile(patched, "test", "exec")
    assert patch_source(patched) == (patched, count)
    calls = [
        node for node in ast.walk(ast.parse(patched)) if isinstance(node, ast.Call)
    ]
    assert all(node.keywords[-1].value.id == "_SPCODE_NO_WINDOW" for node in calls)


@pytest.mark.parametrize(
    "source",
    [
        "import os; os.system('tool')",
        "import subprocess; subprocess.run([], creationflags=0)",
    ],
)
def test_upstream_window_policy_changes_require_review(source: str) -> None:
    with pytest.raises(ValueError):
        patch_source(source)


def test_no_argument_call_and_non_spawn_source() -> None:
    patched, count = patch_source("import subprocess\nsubprocess.run()\n")
    assert "subprocess.run(**_SPCODE_NO_WINDOW)" in patched
    assert count == 1
    assert patch_source("# untouched\nx = 1\n") == ("# untouched\nx = 1\n", 0)


def test_wheel_patch_manifest_is_repeatable_and_rejects_new_sites(
    tmp_path: Path,
) -> None:
    site = tmp_path / "Lib/site-packages"
    for relative, count in EXPECTED_CALLS.items():
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "import subprocess\n" + "subprocess.run([])\n" * count, encoding="utf-8"
        )
    manifest = apply_runtime_patches(tmp_path)
    assert apply_runtime_patches(tmp_path) == manifest
    assert sum(item["spawn_calls"] for item in manifest["files"].values()) == 24
    for relative, info in manifest["files"].items():
        assert (
            hashlib.sha256((site / relative).read_bytes()).hexdigest() == info["sha256"]
        )
    unexpected = site / "vivado_mcp/new_launcher.py"
    unexpected.write_text("import subprocess\nsubprocess.run([])\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Upstream subprocess sites changed"):
        apply_runtime_patches(tmp_path)
