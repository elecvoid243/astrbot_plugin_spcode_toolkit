# code_check / code_format Web API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing single-file `code_check` and `code_format` capabilities through two safe Dashboard Web API endpoints.

**Architecture:** Add two atomic POST endpoints that reuse the established business functions. A shared internal Web API helper handles path/body validation, target resolution, serialization, and failure mapping. `tools/code_check.py` gains keyword-only cppcheck configuration overrides so the Web API can use plugin config without mutating global environment state.

**Tech Stack:** Python 3.10+, AstrBot Web API, pytest, ruff, cpplint/cppcheck, AStyle.

**Spec:** `docs/superpowers/specs/2026-08-12-code-check-format-webapi-design.md`
**Author:** elecvoid243 @ 2026-08-12 16:55 CST

## Global Constraints

- Follow `docs/superpowers/specs/2026-08-12-code-check-format-webapi-design.md` exactly.
- Only repo-relative `path` values are accepted.
- Reuse `_git_endpoint_preflight`, `_validate_repo_relative_file`, `_make_envelope`, `_JSONResponseCompat`, `ReasonCode`, and `run_sync`.
- Serialize check/format calls through a shared `asyncio.Lock`.
- `code_format` defaults to write mode; `check=true` must never write.
- `code_format` uses `plugin._config["default_style"]` and `["default_indent"]`.
- `code_check` uses `plugin._config["cppcheck_enable"]` and `["cppcheck_shortcircuit"]` through keyword-only overrides; LLM behavior remains unchanged.
- Run TDD: write failing tests first, verify the expected failure, then implement.
- Do not modify `README.md`, `AGENTS.md`, or `metadata.yaml`.
- Commit only task-owned files. No push and no PR.

---

### Task 1: Create isolated worktree and verify baseline

**Files:**
- No source files are modified.

**Interfaces:**
- Produces: clean isolated worktree for implementation.

- [ ] **Step 1: Detect isolation and ignored worktree directory**

```powershell
$gitDir = (Resolve-Path (git rev-parse --git-dir)).Path
$gitCommon = (Resolve-Path (git rev-parse --git-common-dir)).Path
git branch --show-current
git check-ignore -v .worktrees
```

Expected: current repository is a normal checkout on `main`; `.worktrees` is ignored.

- [ ] **Step 2: Create worktree**

```powershell
git worktree add .worktrees/code-tools-webapi -b feat/code-tools-webapi
```

Expected: branch `feat/code-tools-webapi` at the current `main` HEAD, including design commit `6dabaed`.

- [ ] **Step 3: Run baseline focused tests**

```powershell
Set-Location .worktrees/code-tools-webapi
$env:PYTHONPATH = 'F:\github\Astrbot'
D:\anaconda3\python.exe -m pytest tests/test_code_format.py tests/test_cppcheck_enable_config.py tests/test_reason_code.py tests/test_webapi_end_to_end.py -q
```

Expected: all selected tests pass. Restore any previous `PYTHONPATH` after the command.

- [ ] **Step 4: Confirm clean baseline**

```powershell
git status --short --branch
```

Expected: only `## feat/code-tools-webapi`.

---

### Task 2: Add cppcheck configuration overrides to `tools/code_check.py`

**Files:**
- Modify: `tools/code_check.py`
- Test: `tests/test_code_check_config_overrides.py`

**Interfaces:**
- Consumes: existing `_get_cppcheck_enable()` and `_get_shortcircuit_mode()`.
- Produces:
  - `check(filepath, linter="auto", *, cppcheck_enable=None, cppcheck_shortcircuit=None) -> dict`
  - `_run_cppcheck(p, *, cppcheck_enable=None) -> dict | None`
  - `_run_cppcheck_only(p, *, cppcheck_enable=None) -> dict`
  - `_run_cpplint(p, *, cppcheck_enable=None, shortcircuit_mode=None) -> dict`

- [ ] **Step 1: Write failing override tests**

Create `tests/test_code_check_config_overrides.py`:

```python
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
        return subprocess.CompletedProcess(args, 0, stdout="", stderr=cppcheck_stderr)

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
```

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_check_config_overrides.py -q
```

Expected: first test fails with `TypeError: check() got an unexpected keyword argument 'cppcheck_enable'`.

- [ ] **Step 3: Implement normalization helpers**

In `tools/code_check.py`, add:

```python
def _normalize_cppcheck_enable(values: object) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if isinstance(value, str) and value in _VALID_CPPCHECK_ENABLE and value not in seen:
            seen.add(value)
            out.append(value)
    return out
```

Refactor `_get_cppcheck_enable()` so each source delegates to `_normalize_cppcheck_enable(...)`.

Add:

```python
def _normalize_shortcircuit_mode(value: object, default: str = _SHORTCIRCUIT_ERROR) -> str:
    return value if isinstance(value, str) and value in _VALID_SHORTCIRCUIT_MODES else default
```

- [ ] **Step 4: Thread overrides through the call chain**

Change public signature:

```python
def check(
    filepath: str,
    linter: str = "auto",
    *,
    cppcheck_enable: list[str] | None = None,
    cppcheck_shortcircuit: str | None = None,
) -> dict:
```

For auto C/C++ dispatch use:

```python
runner = lambda p: _run_cpplint(
    p,
    cppcheck_enable=cppcheck_enable,
    shortcircuit_mode=cppcheck_shortcircuit,
)
```

For explicit cppcheck use:

```python
"cppcheck": lambda p: _run_cppcheck_only(
    p,
    cppcheck_enable=cppcheck_enable,
),
```

Change:

```python
def _run_cppcheck(p: Path, *, cppcheck_enable: list[str] | None = None) -> dict | None:
    enable_categories = (
        _get_cppcheck_enable()
        if cppcheck_enable is None
        else _normalize_cppcheck_enable(cppcheck_enable)
    )
```

Change `_run_cppcheck_only` and `_run_cpplint` to accept and forward the overrides. In `_run_cpplint`, use:

```python
mode = (
    _get_shortcircuit_mode()
    if shortcircuit_mode is None
    else _normalize_shortcircuit_mode(shortcircuit_mode)
)
```

- [ ] **Step 5: Verify GREEN and lint**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_check_config_overrides.py tests/test_cppcheck_enable_config.py -q
```

Expected: all tests pass.

Use `code_check` on:

- `tools/code_check.py`
- `tests/test_code_check_config_overrides.py`

Expected: no issues.

- [ ] **Step 6: Commit**

```powershell
git add tools/code_check.py tests/test_code_check_config_overrides.py
git commit -m "feat(code-check): add cppcheck config overrides"
```

---

### Task 3: Add shared Web API helper and ReasonCodes

**Files:**
- Create: `tools/webapi/_code_tools.py`
- Modify: `tools/webapi/_helpers.py`
- Test: `tests/test_reason_code.py`
- Test: `tests/test_code_tools_webapi_helpers.py`

**Interfaces:**
- Produces:
  - `_CODE_TOOLS_LOCK`
  - `_validate_code_path(path: object) -> str | None`
  - `_resolve_code_target(plugin, *, path, umo, worktree) -> tuple[dict | None, dict | None, Path | None]`
  - `_run_code_tool(func, *args, **kwargs) -> Any`
  - `_tool_failure(result: dict, fallback: str) -> tuple[str, str]`
  - `_tool_details(result: dict) -> dict`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_code_tools_webapi_helpers.py`:

```python
"""Tests for shared code-check/code-format Web API helpers."""

from pathlib import Path

from tools.webapi._helpers import ReasonCode


def test_validate_code_path_rejects_control_chars():
    from tools.webapi._code_tools import _validate_code_path

    assert _validate_code_path("src/a.py") is None
    assert _validate_code_path("") == ReasonCode.INVALID_PARAM
    assert _validate_code_path("a\nb.py") == ReasonCode.INVALID_PARAM
    assert _validate_code_path("x" * 513) == ReasonCode.INVALID_PARAM
    assert _validate_code_path(None) == ReasonCode.INVALID_BODY


def test_tool_details_omit_ok():
    from tools.webapi._code_tools import _tool_details

    assert _tool_details({"ok": False, "error": "x", "proposal": "p"}) == {
        "error": "x",
        "proposal": "p",
    }


def test_tool_failure_maps_common_backend_errors():
    from tools.webapi._code_tools import _tool_failure

    assert _tool_failure({"error": "文件不存在: a.py"}, ReasonCode.CHECK_FAILED) == (
        ReasonCode.FILE_NOT_FOUND,
        "文件不存在: a.py",
    )
    assert _tool_failure({"error": "不支持的扩展名: .txt"}, ReasonCode.CHECK_FAILED)[0] == ReasonCode.UNSUPPORTED_MEDIA_TYPE
    assert _tool_failure({"error": "ruff 未安装"}, ReasonCode.CHECK_FAILED)[0] == ReasonCode.TOOL_UNAVAILABLE
    assert _tool_failure({"error": "unknown"}, ReasonCode.CHECK_FAILED)[0] == ReasonCode.CHECK_FAILED
```

Append to `tests/test_reason_code.py`:

```python
def test_code_tools_reason_codes_defined():
    assert ReasonCode.TOOL_UNAVAILABLE == "tool_unavailable"
    assert ReasonCode.CHECK_FAILED == "check_failed"
    assert ReasonCode.FORMAT_FAILED == "format_failed"
```

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_tools_webapi_helpers.py tests/test_reason_code.py -q
```

Expected: `ModuleNotFoundError` for `_code_tools` and `AttributeError` for new ReasonCodes.

- [ ] **Step 3: Implement helper and ReasonCodes**

Create `tools/webapi/_code_tools.py` with:

```python
"""Shared helpers for code-check/code-format Web API endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .._helpers import run_sync
from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _validate_repo_relative_file,
)

_CODE_TOOLS_LOCK = asyncio.Lock()
MAX_CODE_PATH_LENGTH = 512


def _validate_code_path(path: object) -> str | None:
    if not isinstance(path, str):
        return ReasonCode.INVALID_BODY
    value = path.strip()
    if not value or len(value) > MAX_CODE_PATH_LENGTH:
        return ReasonCode.INVALID_PARAM
    if "\n" in value or "\r" in value or "\x00" in value:
        return ReasonCode.INVALID_PARAM
    return None


async def _resolve_code_target(
    plugin: object,
    *,
    path: str,
    umo: str | None,
    worktree: str | None,
) -> tuple[dict | None, dict | None, Path | None]:
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        return err, None, None
    directory = ctx["directory"]
    target, path_err = _validate_repo_relative_file(path, Path(directory))
    if path_err is not None:
        return {
            "status": "ok",
            "data": {
                "success": False,
                "reason": ReasonCode.PATH_UNSAFE,
                "stderr": "",
                "elapsed_ms": 0,
            },
        }, None, None
    return None, ctx, target


async def _run_code_tool(func: Any, *args: Any, **kwargs: Any) -> Any:
    async with _CODE_TOOLS_LOCK:
        return await run_sync(func, *args, **kwargs)


def _tool_details(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "ok"}


def _tool_failure(result: dict, fallback: str) -> tuple[str, str]:
    error = str(result.get("error", ""))
    proposal = str(result.get("proposal", ""))
    text = f"{error} {proposal}".lower()
    if "文件不存在" in text or "不是普通文件" in text:
        return ReasonCode.FILE_NOT_FOUND, error
    if "不支持的扩展名" in text:
        return ReasonCode.UNSUPPORTED_MEDIA_TYPE, error
    if "文件过大" in text:
        return ReasonCode.FILE_TOO_LARGE, error
    if "未安装" in text or "不可用" in text:
        return ReasonCode.TOOL_UNAVAILABLE, error
    if "不支持" in text or "indent" in text or "formatter" in text:
        return ReasonCode.INVALID_PARAM, error
    return fallback, error
```

Add `TOOL_UNAVAILABLE`, `CHECK_FAILED`, `FORMAT_FAILED` to `ReasonCode`.

- [ ] **Step 4: Verify GREEN and lint**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_tools_webapi_helpers.py tests/test_reason_code.py -q
```

Expected: all tests pass. `code_check` both changed Python files.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/_code_tools.py tools/webapi/_helpers.py tests/test_code_tools_webapi_helpers.py tests/test_reason_code.py
git commit -m "feat(webapi): add code tool helpers"
```

---

### Task 4: Implement `POST /spcode/code-check`

**Files:**
- Create: `tools/webapi/code_check.py`
- Test: `tests/test_code_check_api.py`

**Interfaces:**
- Produces: `handle(plugin, *, umo=None, worktree=None, body=None) -> dict`.

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_code_check_api.py` with mocked business function and real temporary repo:

```python
"""Unit tests for POST /spcode/code-check."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.project import state as _proj_state
from tools.webapi.code_check import handle

from tests.conftest import _make_plugin

pytestmark = pytest.mark.asyncio


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "src").mkdir()
    (path / "src" / "a.py").write_text("x=1\n", encoding="utf-8")


def _load(path: Path) -> str:
    umo = "u:code-check"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_check_success_passes_config_and_returns_issues(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    plugin._config["cppcheck_enable"] = ["warning"]
    plugin._config["cppcheck_shortcircuit"] = "never"
    umo = _load(repo)
    captured = {}

    def fake_check(filepath, linter="auto", **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "linter": "ruff",
            "issues": [{"line": 1, "message": "x"}],
            "count": 1,
        }

    with patch("tools.webapi.code_check._check_source_file", side_effect=fake_check):
        result = await handle(plugin, umo=umo, body={"path": "src/a.py"})

    assert result["data"]["success"] is True
    assert result["data"]["checked"] is True
    assert result["data"]["count"] == 1
    assert captured["cppcheck_enable"] == ["warning"]
    assert captured["cppcheck_shortcircuit"] == "never"


async def test_invalid_linter():
    plugin = _make_plugin()
    result = await handle(plugin, body={"path": "a.py", "linter": "bogus"})
    assert result["data"]["reason"] == "invalid_param"


async def test_path_unsafe(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)
    result = await handle(plugin, umo=umo, body={"path": "../a.py"})
    assert result["data"]["reason"] == "path_unsafe"


async def test_backend_tool_unavailable(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_check._check_source_file",
        return_value={"ok": False, "error": "ruff 未安装"},
    ):
        result = await handle(plugin, umo=umo, body={"path": "src/a.py"})

    assert result["data"]["reason"] == "tool_unavailable"
    assert result["data"]["details"]["error"] == "ruff 未安装"
```

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_check_api.py -q
```

Expected: `ModuleNotFoundError` for `tools.webapi.code_check`.

- [ ] **Step 3: Implement endpoint**

In `tools/webapi/code_check.py`:

- Validate body and `linter in {"auto", "ruff", "cpplint", "cppcheck"}`.
- Use `_validate_code_path` and `_resolve_code_target`.
- Read `cppcheck_enable` / `cppcheck_shortcircuit` from `plugin._config`.
- Call:

```python
result = await _run_code_tool(
    _check_source_file,
    str(target),
    linter,
    cppcheck_enable=enable,
    cppcheck_shortcircuit=mode,
)
```

- On success return `_JSONResponseCompat` with `checked=True`, `linter`, `issues`, `count`, optional `linters`, `proposal`, path, directory, umo, worktree.
- On failure use `_tool_failure(result, ReasonCode.CHECK_FAILED)` and `_tool_details(result)`.

- [ ] **Step 4: Verify GREEN and lint**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_check_api.py -q
```

Expected: all tests pass. `code_check` endpoint and test files.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/code_check.py tests/test_code_check_api.py
git commit -m "feat(webapi): add code-check endpoint"
```

---

### Task 5: Implement `POST /spcode/code-format`

**Files:**
- Create: `tools/webapi/code_format.py`
- Test: `tests/test_code_format_api.py`

**Interfaces:**
- Produces: `handle(plugin, *, umo=None, worktree=None, body=None) -> dict`.

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_code_format_api.py` with mocked `_format_source_file`:

```python
"""Unit tests for POST /spcode/code-format."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tools.project import state as _proj_state
from tools.webapi.code_format import handle

from tests.conftest import _make_plugin

pytestmark = pytest.mark.asyncio


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "a.py").write_text("x=1\n", encoding="utf-8")


def _load(path: Path) -> str:
    umo = "u:code-format"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_format_success_uses_plugin_style_and_indent(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    plugin._config["default_style"] = "google"
    plugin._config["default_indent"] = 2
    umo = _load(repo)
    captured = {}

    def fake_format(filepath, formatter="auto", **kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "formatter": "ruff",
            "formatter_options": {"style": "google", "indent": 2},
            "check": False,
            "changed": True,
            "file_size_before": 10,
            "file_size_after": 8,
            "proposal": "formatted",
        }

    with patch("tools.webapi.code_format._format_source_file", side_effect=fake_format):
        result = await handle(plugin, umo=umo, body={"path": "a.py"})

    assert result["data"]["success"] is True
    assert result["data"]["formatted"] is True
    assert captured["style"] == "google"
    assert captured["indent"] == 2


async def test_check_true_returns_dry_run(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_format._format_source_file",
        return_value={
            "ok": True,
            "formatter": "ruff",
            "check": True,
            "changed": True,
            "diff_summary": "@@",
        },
    ):
        result = await handle(plugin, umo=umo, body={"path": "a.py", "check": True})

    assert result["data"]["formatted"] is False
    assert result["data"]["diff_summary"] == "@@"


async def test_non_bool_check_rejected():
    plugin = _make_plugin()
    result = await handle(plugin, body={"path": "a.py", "check": "yes"})
    assert result["data"]["reason"] == "invalid_body"


async def test_backend_format_failure(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    with patch(
        "tools.webapi.code_format._format_source_file",
        return_value={"ok": False, "error": "写回文件失败: denied"},
    ):
        result = await handle(plugin, umo=umo, body={"path": "a.py"})

    assert result["data"]["reason"] == "format_failed"
```

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_format_api.py -q
```

Expected: `ModuleNotFoundError` for `tools.webapi.code_format`.

- [ ] **Step 3: Implement endpoint**

In `tools/webapi/code_format.py`:

- Validate body and strict boolean `check`.
- Resolve target through shared helper.
- Read and sanitize `default_style` / `default_indent` from `plugin._config`.
- Call:

```python
result = await _run_code_tool(
    _format_source_file,
    str(target),
    "auto",
    check=check,
    style=style,
    indent=indent,
)
```

- On success return `formatted=not check`, formatter, formatter_options, check, changed, sizes, diff_summary, proposal, path/directory/umo/worktree.
- On failure use `_tool_failure(result, ReasonCode.FORMAT_FAILED)`.

- [ ] **Step 4: Verify GREEN and lint**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_format_api.py -q
```

Expected: all tests pass. `code_check` endpoint and test files.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/code_format.py tests/test_code_format_api.py
git commit -m "feat(webapi): add code-format endpoint"
```

---

### Task 6: Register routes and add Dashboard API documentation

**Files:**
- Modify: `tools/webapi/__init__.py`
- Modify: `tests/test_webapi_end_to_end.py`
- Create: `docs/api/webapi-code-check-format-api.md`

**Interfaces:**
- Routes:
  - `POST /spcode/code-check`
  - `POST /spcode/code-format`
- Handler aliases:
  - `handle_post_code_check`
  - `handle_post_code_format`

- [ ] **Step 1: Write failing route tests**

Update `tests/test_webapi_end_to_end.py`:

```python
"/spcode/code-check",
"/spcode/code-format",
```

Change POST count:

```python
assert methods.count("POST") == 36
```

Change registration count:

```python
assert plugin.context.register_web_api.call_count == 55
```

Add smoke tests asserting both modules import and route paths exist.

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_webapi_end_to_end.py -q
```

Expected: route set and count assertions fail with 53 existing routes.

- [ ] **Step 3: Register routes**

In `tools/webapi/__init__.py`:

```python
from . import (
    code_check,
    code_format,
)
```

Append to `ROUTES`:

```python
(
    "/spcode/code-check",
    ["POST"],
    code_check.handle,
    "对 repo 内单文件执行 code_check（ruff/cpplint/cppcheck）",
),
(
    "/spcode/code-format",
    ["POST"],
    code_format.handle,
    "对 repo 内单文件执行 code_format（默认写回，check=true 预览）",
),
```

Append to `HANDLERS` and `__all__`; update route-count docstring to 55.

- [ ] **Step 4: Verify GREEN**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_webapi_end_to_end.py -q
```

Expected: all route tests pass.

- [ ] **Step 5: Add Dashboard API documentation**

Create `docs/api/webapi-code-check-format-api.md` with:

- endpoint overview
- common path/security contract
- request/response examples for both endpoints
- ReasonCode table
- TypeScript interfaces and fetch examples
- note that `code-format` writes by default and `check=true` is dry-run

- [ ] **Step 6: Commit**

```powershell
git add tools/webapi/__init__.py tests/test_webapi_end_to_end.py
git add -f docs/api/webapi-code-check-format-api.md
git commit -m "feat(webapi): register code tool endpoints"
```

---

### Task 7: Final verification

**Files:**
- No new source files unless verification exposes a defect.

- [ ] **Step 1: Run focused tests**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_code_check_config_overrides.py tests/test_code_tools_webapi_helpers.py tests/test_code_check_api.py tests/test_code_format_api.py tests/test_cppcheck_enable_config.py tests/test_code_format.py tests/test_reason_code.py tests/test_webapi_end_to_end.py -q
```

Expected: all pass.

- [ ] **Step 2: Lint all new/modified Python files**

Use built-in `code_check` on:

- `tools/code_check.py`
- `tools/webapi/_code_tools.py`
- `tools/webapi/_helpers.py`
- `tools/webapi/code_check.py`
- `tools/webapi/code_format.py`
- `tools/webapi/__init__.py`
- all new/modified test files

Expected: no issues except documented pre-existing issues in legacy `tests/test_webapi_end_to_end.py` if still present.

- [ ] **Step 3: Check diff and status**

```powershell
git status --short --branch
git diff --check
git log --oneline main..HEAD
```

Expected: clean worktree; no whitespace errors; all task commits present.

- [ ] **Step 4: Report evidence**

Report:

- worktree path and branch
- commit hashes
- focused test result
- lint result
- route count 55
- any pre-existing unrelated test/lint failures
- confirmation that nothing was pushed and no PR was created

---

## Self-Review

- Spec coverage: all endpoint, security, config, error mapping, route, and documentation requirements map to tasks.
- Placeholder scan: no TBD or deferred implementation step.
- Type consistency: helper and handler names match across tasks.
- Compatibility: `tools/code_check.py` defaults preserve LLM behavior.
- TDD: every production task starts with failing tests and expected failure output.
