"""Tests for the GET /spcode/git-diff HTTP endpoint.

Real git is used (no subprocess mocking) via tmp_path fixtures. The handler
must be exercised through a constructed SPCodeToolkit instance using the
``plugin`` fixture (defined locally below; conftest.py has no ``plugin``
fixture, so we provide one here — same pattern as test_project_subcommand.py).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# 启用 pytest-asyncio:让所有 `async def` 测试函数在 strict 模式下也能运行。
# (项目用 strict 模式,不在 pytest 配置里改全局,避免影响其他测试文件。)
pytestmark = pytest.mark.asyncio

# main.py 用相对导入,需把项目父目录加到 sys.path 然后以包形式导入。
# 同时让 `from tools import _helpers` 和 `import main` 这种绝对导入也能工作
# (与 tests/test_project_subcommand.py 相同的 sys.path 设置)。
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent  # F:\github
_PROJECT_DIR = Path(__file__).resolve().parent.parent  # F:\github\astrbot_plugin_spcode_toolkit
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ────────────────────────────────────────────────────────────────────
# Fixtures (local helpers; not exposed to conftest)
# ────────────────────────────────────────────────────────────────────

def _make_plugin() -> Any:
    """Build a minimal SPCodeToolkit instance for unit testing.

    Bypasses __init__ (which would require a real star.Context) and sets up
    the bare attributes the git-diff handler will touch. Matches the pattern
    used by tests/test_project_subcommand.py.
    """
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin.context = MagicMock()
    plugin._loaded_projects = {}
    plugin._loaded_agents = {}
    plugin._codegraph_projects = {}
    # Permissive default config so feature-flag checks pass.
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "codegraph_project": "",
        "file_remove_blacklist": None,
        "git_path": "",
    }
    return plugin


@pytest.fixture
def plugin():
    """Per-test plugin instance (local fixture; conftest has none)."""
    return _make_plugin()


def _init_git_repo(path: Path) -> None:
    """Initialize a minimal git repo in tmp_path with one initial commit."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    """Inject a project into plugin._loaded_projects bypassing /project load."""
    plugin._loaded_projects[umo] = {
        "directory": str(directory),
        "loaded_at": time.time(),
    }


def _make_event(umo: str = "test:umo") -> MagicMock:
    """Construct a minimal event mock with unified_msg_origin."""
    event = MagicMock()
    event.unified_msg_origin = umo
    return event


def _make_web_request_mock(query: dict[str, str | None] | None = None) -> MagicMock:
    """构造一个 mock 替换 ``astrbot.api.web.request``。

    现有 v1 测试用 ``monkey_q.get = MagicMock(return_value="target:umo")`` 模式
    只能为所有 key 返回同一个值,无法按 ``umo`` / ``worktree`` / ``scope`` 各自
    设值。本辅助通过 ``side_effect`` 让每个 key 独立查表。

    Args:
        query: 模拟 query string,如 ``{"scope": "staged", "umo": "x:y"}``。
               key 不存在时返回 None(对齐真实 ``QueryDict.get`` 语义)。
    """
    mock = MagicMock()
    mock.query.get = MagicMock(side_effect=lambda key: (query or {}).get(key))
    return mock


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────

# --- T1: clean state ---------------------------------------------------

async def test_handle_git_diff_returns_clean_when_no_changes(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["diff"] == ""
    assert data["files_changed"] == []
    assert data["truncated"] is False
    assert data["reason"] is None


# --- T2-T6: status detection (M / A / D / R + truncation) -------------

async def test_handle_git_diff_returns_modified_file(plugin, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified content", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    fc = result["data"]["files_changed"]
    assert len(fc) == 1
    assert fc[0]["path"] == "README.md"
    assert fc[0]["status"] == "M"
    assert (fc[0]["additions"] + fc[0]["deletions"]) > 0


async def test_handle_git_diff_returns_added_via_intent_to_add(plugin, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "new.py"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    fc = result["data"]["files_changed"]
    assert any(f["path"] == "new.py" and f["status"] == "A" for f in fc), fc


async def test_handle_git_diff_returns_deleted_file(plugin, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").unlink()
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    fc = result["data"]["files_changed"]
    assert any(f["path"] == "README.md" and f["status"] == "D" for f in fc), fc


async def test_handle_git_diff_returns_renamed_file(plugin, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "add old", "-q"], cwd=tmp_path, check=True)
    os.replace(tmp_path / "old.py", tmp_path / "new.py")
    subprocess.run(["git", "add", "-N", "new.py"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    fc = result["data"]["files_changed"]
    assert any(f["path"] == "new.py" and f["status"] == "R" for f in fc), fc


async def test_handle_git_diff_truncates_large_output(plugin, tmp_path):
    _init_git_repo(tmp_path)
    bigger = "x" * (1024 * 1024 + 100_000)  # ~1.1 MB
    (tmp_path / "big.txt").write_text(bigger, encoding="utf-8")
    # 必须 git add -N 让 git 把 big.txt 视作意图添加(tracked),否则作为 untracked
    # 不会出现在 `git diff` 输出里,truncated 永远 False
    subprocess.run(["git", "add", "-N", "big.txt"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["truncated"] is True
    assert data["truncated_at_bytes"] == 1024 * 1024
    assert len(data["diff"].encode("utf-8")) <= 1024 * 1024
    # sanity: 原始 diff 输入确实 > 1 MB
    assert len(bigger.encode("utf-8")) > 1024 * 1024


# --- T7-T11: gating (no project, umo mismatch, fallback, feature flag) -

async def test_handle_git_diff_no_project_loaded(plugin):
    plugin._loaded_projects.clear()
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "no_project_loaded"
    assert data["diff"] is None


async def test_handle_git_diff_umo_not_in_loaded(plugin, tmp_path):
    # do NOT load any project; query a specific umo
    plugin._loaded_projects.clear()
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "no_project_loaded"


async def test_handle_git_diff_falls_back_to_most_recent(plugin, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _init_git_repo(a)
    _init_git_repo(b)
    plugin._loaded_projects.clear()
    plugin._loaded_projects["umo:1"] = {"directory": str(a), "loaded_at": 1.0}
    plugin._loaded_projects["umo:2"] = {"directory": str(b), "loaded_at": 2.0}
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(b)  # most recent


async def test_handle_git_diff_feature_disabled_agentsmd(plugin):
    plugin._config["agentsmd_enabled"] = False
    try:
        result = await plugin.handle_get_git_diff()
        assert result["data"]["reason"] == "feature_disabled"
    finally:
        plugin._config["agentsmd_enabled"] = True


async def test_handle_git_diff_feature_disabled_codegraph(plugin):
    plugin._config["codegraph_enabled"] = False
    try:
        result = await plugin.handle_get_git_diff()
        assert result["data"]["reason"] == "feature_disabled"
    finally:
        plugin._config["codegraph_enabled"] = True


# --- T12-T14: environment errors (directory / git) -------------------

async def test_handle_git_diff_directory_missing(plugin):
    plugin._loaded_projects["test:umo"] = {
        "directory": "/nonexistent/path/that/does/not/exist",
        "loaded_at": time.time(),
    }
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "directory_missing"
    assert data["directory"] == "/nonexistent/path/that/does/not/exist"


async def test_handle_git_diff_not_a_git_repo(plugin, tmp_path):
    # tmp_path is empty — no .git folder
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "not_a_git_repo"
    assert data["directory"] == str(tmp_path)


async def test_handle_git_diff_git_unavailable(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    # Patch tools._helpers.run_cmd to simulate FileNotFoundError
    from tools import _helpers

    def _fake_run_cmd(cmd_args, cwd="", timeout=15, encoding="utf-8"):
        return {"ok": False, "error": "git 未安装或不在 PATH 中"}

    monkeypatch.setattr(_helpers, "run_cmd", _fake_run_cmd)
    # also patch in main module namespace (since it's imported)
    from astrbot_plugin_spcode_toolkit import main as main_mod
    monkeypatch.setattr(main_mod, "run_cmd", _fake_run_cmd, raising=False)

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "git_unavailable"


# --- T15-T18: envelope shape + umo echo + max_bytes constant ---------

async def test_handle_git_diff_response_envelope_shape(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    assert result["status"] == "ok"
    data = result["data"]
    expected_keys = {
        "loaded", "directory", "umo", "diff", "stat", "files_changed",
        "truncated", "truncated_at_bytes", "max_bytes", "elapsed_ms", "reason",
    }
    assert expected_keys.issubset(data.keys())


async def test_handle_git_diff_umo_query_param_hits_loaded_umo(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    _load_project(plugin, "target:umo", str(tmp_path))

    # Mock web.request.query.get to return "target:umo"
    from astrbot.api import web
    monkey_q = MagicMock()
    monkey_q.get = MagicMock(return_value="target:umo")
    monkeypatch.setattr(web, "request", MagicMock(query=monkey_q))

    result = await plugin.handle_get_git_diff()
    assert result["data"]["umo"] == "target:umo"


async def test_handle_git_diff_max_bytes_constant(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await plugin.handle_get_git_diff()
    assert result["data"]["max_bytes"] == 1024 * 1024


async def test_handle_git_diff_git_calls_run_concurrently(plugin, tmp_path, monkeypatch):
    """4 git calls must run concurrently, not serially. Verify with timing.

    Each stubbed ``run_cmd`` call sleeps 0.1s + pays a real ``subprocess.run``
    overhead (~0.07s on Windows) for the underlying ``git --version``.

    Expected total wall time:
      - **Concurrent** (probe + 4 gather in parallel): ~0.17s (probe) +
        ~0.17s (4 parallel) = **~0.35s** + asyncio overhead ≈ 0.4-0.5s
      - **Serial** (probe + 4 gather in sequence): ~0.17s + 4×0.17s = **~0.85s**
        + overhead ≈ 0.9-1.0s

    Threshold 0.6s clearly distinguishes the two while tolerating CI jitter.
    """
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    import time as _t
    from tools import _helpers
    real_run_cmd = _helpers.run_cmd

    def _slow_run_cmd(*args, **kwargs):
        _t.sleep(0.1)  # each call takes 0.1s + real subprocess overhead
        return real_run_cmd(*args, **kwargs)

    from astrbot_plugin_spcode_toolkit import main as main_mod
    monkeypatch.setattr(_helpers, "run_cmd", _slow_run_cmd)
    monkeypatch.setattr(main_mod, "run_cmd", _slow_run_cmd, raising=False)

    t0 = _t.time()
    await plugin.handle_get_git_diff()
    elapsed = _t.time() - t0
    # Concurrent: ~0.4-0.5s. Serial: ~0.9-1.0s. Threshold 0.6s clearly distinguishes.
    assert elapsed < 0.6, f"git calls appear serial (elapsed={elapsed:.2f}s)"


# --- T19: git_path config --------------------------------------------

async def test_handle_git_diff_uses_configured_git_path(plugin, tmp_path):
    plugin._config["git_path"] = "C:/some/other/git.exe"
    try:
        assert plugin._git_binary() == "C:/some/other/git.exe"
    finally:
        plugin._config["git_path"] = ""


async def test_handle_git_diff_falls_back_to_git_when_path_empty(plugin):
    plugin._config["git_path"] = ""
    assert plugin._git_binary() == "git"


async def test_git_path_schema_field_exists():
    """Sync schema check wrapped in async to satisfy global pytestmark.

    The schema check itself does no I/O beyond a local file read; the async
    wrapper is purely a marker shim to avoid the PytestWarning emitted when a
    sync test sits under a global ``pytest.mark.asyncio`` mark.
    """
    import json
    from pathlib import Path as _P
    schema_path = _P(__file__).resolve().parent.parent / "_conf_schema.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    # Schema 宓屽鍦?git_diff.items.git_path(AstrBot 鍒嗙粍 + items 鍖呰)
    assert "git_diff" in schema, "git_diff group missing from schema"
    assert "git_path" in schema["git_diff"]["items"], "git_path field missing"
    field = schema["git_diff"]["items"]["git_path"]
    assert field["type"] == "string"
    assert field["default"] == ""
    assert field["description"]  # non-empty


# ────────────────────────────────────────────────────────────────────
# v3.1 scope 参数(在 v1 之上扩展,默认行为不变)
# ────────────────────────────────────────────────────────────────────

async def test_handle_git_diff_default_scope_is_unstaged(plugin, tmp_path, monkeypatch):
    """不传 ?scope= 时,行为与 v1 完全一致(走 git diff,无参数)。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", _make_web_request_mock())

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["scope"] == "unstaged"  # 默认值回显
    assert "README.md" in data["diff"]  # unstaged 改动出现在 diff 中


async def test_handle_git_diff_scope_staged_returns_staged_diff(plugin, tmp_path, monkeypatch):
    """?scope=staged → 走 git diff --cached,只显示已暂存内容。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("staged change", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    # 之后在已 staged 之上再做 unstaged 修改
    (tmp_path / "README.md").write_text("staged + extra edit", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "staged"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["scope"] == "staged"
    # staged diff 显示原始 staged 内容(staged change),不含后续 edit
    assert "staged change" in data["diff"]


async def test_handle_git_diff_scope_all_returns_combined_diff(plugin, tmp_path, monkeypatch):
    """?scope=all → 走 git diff HEAD,显示 working tree vs HEAD。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("modified\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    # 再做 unstaged 改动
    (tmp_path / "README.md").write_text("modified+unstaged\n", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "all"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["scope"] == "all"
    # HEAD diff 包含 working tree 完整内容(对照 init vs modified+unstaged)
    assert "modified+unstaged" in data["diff"]


async def test_handle_git_diff_scope_staged_empty_when_no_staged_changes(
    plugin, tmp_path, monkeypatch
):
    """仅 unstaged 改动时,?scope=staged 返回空 diff。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("only unstaged", encoding="utf-8")
    # 注意:不调用 git add
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "staged"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is True
    assert data["scope"] == "staged"
    assert data["diff"] == ""
    assert data["files_changed"] == []


async def test_handle_git_diff_scope_all_with_only_staged_changes(
    plugin, tmp_path, monkeypatch
):
    """仅 staged 改动时,?scope=all 返回与 ?scope=staged 相同 diff。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("only staged", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "all"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["scope"] == "all"
    # staged 改动在工作区 + staged 之和中仍是相同内容
    assert "only staged" in data["diff"]


async def test_handle_git_diff_scope_all_with_only_unstaged_changes(
    plugin, tmp_path, monkeypatch
):
    """仅 unstaged 改动时,?scope=all 返回与 ?scope=unstaged 相同 diff。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("only unstaged", encoding="utf-8")
    # 不 git add
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "all"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["scope"] == "all"
    assert "only unstaged" in data["diff"]


async def test_handle_git_diff_scope_invalid_value_returns_invalid_scope(
    plugin, tmp_path, monkeypatch
):
    """?scope=foo(未知值)→ loaded=False, reason='invalid_scope',且 git 命令一次也不跑。"""
    from astrbot.api import web
    from tools import _helpers as _h
    from astrbot_plugin_spcode_toolkit import main as _mm

    real_run_cmd = _h.run_cmd
    call_count = {"n": 0}

    def _counted_run_cmd(*args, **kwargs):
        call_count["n"] += 1
        return real_run_cmd(*args, **kwargs)

    monkeypatch.setattr(_h, "run_cmd", _counted_run_cmd)
    monkeypatch.setattr(_mm, "run_cmd", _counted_run_cmd, raising=False)

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "foo"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "invalid_scope"
    # Q1-OOR 风格:校验顺序在所有 git 操作之前,run_cmd 不应被调用
    assert call_count["n"] == 0, f"run_cmd called {call_count['n']} times; expected 0"


async def test_handle_git_diff_scope_case_insensitive(plugin, tmp_path, monkeypatch):
    """?scope=STAGED 大写等价于 ?scope=staged。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("staged", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "STAGED"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["scope"] == "staged"  # 规整为小写
    assert "staged" in data["diff"]


async def test_handle_git_diff_scope_empty_string_defaults_to_unstaged(
    plugin, tmp_path, monkeypatch
):
    """?scope= 空字符串视同缺省 → scope=unstaged(避免误报 invalid_scope)。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("unstaged", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": ""})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["scope"] == "unstaged"
    assert "unstaged" in data["diff"]


async def test_handle_git_diff_scope_field_echoed_in_success_response(
    plugin, tmp_path, monkeypatch
):
    """成功响应里 data.scope 等于客户端请求的合法值(精确回显)。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    for requested, echoed in [
        ("unstaged", "unstaged"),
        ("staged", "staged"),
        ("all", "all"),
    ]:
        monkeypatch.setattr(
            web, "request", _make_web_request_mock({"scope": requested})
        )
        result = await plugin.handle_get_git_diff()
        assert result["data"]["scope"] == echoed, (
            f"scope={requested!r} should echo as {echoed!r}"
        )


async def test_handle_git_diff_scope_invalid_response_omits_scope_field(
    plugin, tmp_path, monkeypatch
):
    """错误响应(invalid_scope)里不包含 scope 字段(空 envelope schema 严格不变)。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "bogus"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    assert data["reason"] == "invalid_scope"
    # v1 envelope 不含 scope 字段;空响应必须严格保持
    assert "scope" not in data, (
        f"empty envelope should not have 'scope' key, got: {sorted(data.keys())}"
    )


async def test_handle_git_diff_scope_invalid_takes_precedence_over_feature_flag(
    plugin, tmp_path, monkeypatch
):
    """feature flag 关闭 + ?scope=foo → 仍返回 invalid_scope(Q1-OOR 校验顺序)。"""
    from astrbot.api import web
    plugin._config["agentsmd_enabled"] = False
    try:
        monkeypatch.setattr(
            web, "request", _make_web_request_mock({"scope": "foo"})
        )
        result = await plugin.handle_get_git_diff()
        assert result["data"]["reason"] == "invalid_scope"
    finally:
        plugin._config["agentsmd_enabled"] = True


async def test_handle_git_diff_scope_staged_handles_intent_to_add(
    plugin, tmp_path, monkeypatch
):
    """git add -N new.py → ?scope=staged 中 files_changed 含 status='A',
    且 data.scope 字段回显为 'staged'(避免与 v1 走 'git diff' 误判为绿)。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "-N", "new.py"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", _make_web_request_mock({"scope": "staged"})
    )

    result = await plugin.handle_get_git_diff()
    data = result["data"]
    # scope 字段断言:迫使 v3.1 走 git diff --cached 路径(否则 v1 走 git diff 也能 PASS)
    assert data["scope"] == "staged"
    fc = data["files_changed"]
    assert any(f["path"] == "new.py" and f["status"] == "A" for f in fc), fc


async def test_handle_git_diff_scope_combines_with_worktree_param(
    plugin, tmp_path, monkeypatch
):
    """?scope= 与 ?worktree= 正交。scope 解析通过后,worktree 校验失败应仍走
    worktree_invalid 路径(而非 invalid_scope)。"""
    from astrbot.api import web
    from tools import _helpers as _h

    # 让 _validate_worktree_param 返回 worktree_invalid(模拟 6 步防御链失败)
    def _reject_validate(*args, **kwargs):
        return None, "worktree_invalid"

    monkeypatch.setattr(_h, "_validate_worktree_param", _reject_validate)

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request",
        _make_web_request_mock({"scope": "all", "worktree": "../escape"}),
    )
    result = await plugin.handle_get_git_diff()
    # scope 解析通过后,worktree 校验失败 → worktree_invalid(而非 invalid_scope)
    assert result["data"]["reason"] == "worktree_invalid"
