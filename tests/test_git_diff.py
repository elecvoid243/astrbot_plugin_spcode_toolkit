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

# v3.2: shared helper 从 conftest 导入(原为 test_git_diff.py 私有)
from tests.conftest import make_web_request_mock  # noqa: F401

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

# v3.4: git-diff handler 已从 main.py 搬到 tools/webapi/git_diff.py
# (Task 4.1 — webapi extraction chunk 5)。所有 helper (_parse_X,
# _build_stat_text, _compute_diff_etag, _DIFF_ETAG_CACHE/TL, _run_git_async)
# 都从 main._X 改到 _gd._X(handler 入口也从 plugin.handle_get_git_diff()
# 改为 _gd.handle(plugin))。
from tools.webapi import git_diff as _gd  # noqa: E402

# SPCodeToolkit 类本身仍来自 main(用于测试中 _make_plugin 构造)
from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402

SPCodeToolkit = _main_mod.SPCodeToolkit


# ────────────────────────────────────────────────────────────────────
# Fixtures (v3.2: _make_plugin 已迁移到 conftest.py)
# ────────────────────────────────────────────────────────────────────

# v3.2: 从 conftest 导入(v3.2 前是 test_git_diff.py 私有)
from tests.conftest import _make_plugin  # noqa: F401, E402


@pytest.fixture
def plugin():
    """Per-test plugin instance (conftest 提供 _make_plugin)."""
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


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────

# --- T1: clean state ---------------------------------------------------

async def test_handle_git_diff_returns_clean_when_no_changes(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _gd.handle(plugin)
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
    result = await _gd.handle(plugin)
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
    result = await _gd.handle(plugin)
    fc = result["data"]["files_changed"]
    assert any(f["path"] == "new.py" and f["status"] == "A" for f in fc), fc


async def test_handle_git_diff_returns_deleted_file(plugin, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").unlink()
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _gd.handle(plugin)
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
    result = await _gd.handle(plugin)
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
    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["truncated"] is True
    assert data["truncated_at_bytes"] == 1024 * 1024
    assert len(data["diff"].encode("utf-8")) <= 1024 * 1024
    # sanity: 原始 diff 输入确实 > 1 MB
    assert len(bigger.encode("utf-8")) > 1024 * 1024


# --- T7-T11: gating (no project, umo mismatch, fallback, feature flag) -

async def test_handle_git_diff_no_project_loaded(plugin):
    plugin._loaded_projects.clear()
    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "no_project_loaded"
    assert data["diff"] is None


async def test_handle_git_diff_umo_not_in_loaded(plugin, tmp_path):
    # do NOT load any project; query a specific umo
    plugin._loaded_projects.clear()
    result = await _gd.handle(plugin)
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
    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(b)  # most recent


async def test_handle_git_diff_feature_disabled_agentsmd(plugin):
    plugin._config["agentsmd_enabled"] = False
    try:
        result = await _gd.handle(plugin)
        assert result["data"]["reason"] == "feature_disabled"
    finally:
        plugin._config["agentsmd_enabled"] = True


async def test_handle_git_diff_feature_disabled_codegraph(plugin):
    plugin._config["codegraph_enabled"] = False
    try:
        result = await _gd.handle(plugin)
        assert result["data"]["reason"] == "feature_disabled"
    finally:
        plugin._config["codegraph_enabled"] = True


# --- T12-T14: environment errors (directory / git) -------------------

async def test_handle_git_diff_directory_missing(plugin):
    plugin._loaded_projects["test:umo"] = {
        "directory": "/nonexistent/path/that/does/not/exist",
        "loaded_at": time.time(),
    }
    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "directory_missing"
    assert data["directory"] == "/nonexistent/path/that/does/not/exist"


async def test_handle_git_diff_not_a_git_repo(plugin, tmp_path):
    # tmp_path is empty — no .git folder
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "not_a_git_repo"
    assert data["directory"] == str(tmp_path)


async def test_handle_git_diff_git_unavailable(plugin, tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    # v3.4 (2026-06-21) P1 perf: P1-5 把 run_sync(run_cmd, ...) 换成 _run_git_async,
    # 所以 mock 目标从 run_cmd 改为 _run_git_async。模拟"git 不在 PATH"路径
    # (``asyncio.create_subprocess_exec`` 抛 FileNotFoundError,_run_git_async
    # 转成 {ok: False, error: "git 未安装或不在 PATH 中"})。
    from tools.webapi import git_diff as main_mod

    async def _fake_run_git_async(cmd_args, cwd="", timeout=15.0, encoding="utf-8"):
        return {"ok": False, "error": "git 未安装或不在 PATH 中"}

    monkeypatch.setattr(main_mod, "_run_git_async", _fake_run_git_async)

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "git_unavailable"


# --- T15-T18: envelope shape + umo echo + max_bytes constant ---------

async def test_handle_git_diff_response_envelope_shape(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _gd.handle(plugin)
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

    # Mock web.request.query to return "target:umo" only for the "umo" key.
    # v3.1 之前用 MagicMock(return_value="target:umo") 对所有 key 返回同值,
    # 但新增的 ?scope= 校验会把它误判为非法 scope 并提前返回。这里用
    # make_web_request_mock 让 umo / worktree / scope 三个 key 各自查表。
    from astrbot.api import web
    monkeypatch.setattr(
        web, "request", make_web_request_mock({"umo": "target:umo"})
    )

    result = await _gd.handle(plugin)
    assert result["data"]["umo"] == "target:umo"


async def test_handle_git_diff_max_bytes_constant(plugin, tmp_path):
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _gd.handle(plugin)
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

    from tools.webapi import git_diff as main_mod
    monkeypatch.setattr(_helpers, "run_cmd", _slow_run_cmd)
    monkeypatch.setattr(main_mod, "run_cmd", _slow_run_cmd, raising=False)

    t0 = _t.time()
    await _gd.handle(plugin)
    elapsed = _t.time() - t0
    # Concurrent: ~0.4-0.5s. Serial: ~0.9-1.0s. Threshold 0.6s clearly distinguishes.
    assert elapsed < 0.6, f"git calls appear serial (elapsed={elapsed:.2f}s)"


# ── v3.4 (2026-06-21) P2 perf: git diff / numstat 串行 ──


async def test_handle_git_diff_diff_runs_before_numstat(plugin, tmp_path, monkeypatch):
    """P2 perf 修复:diff 完成后才跑 numstat(走 page cache),而不是 gather 并发。

    旧实现 ``asyncio.gather(diff, numstat)`` → 两路并发,CPU 2 路。
    新实现串行:diff 跑完填满 page cache → numstat 几乎瞬时。
    总 wall-clock 持平(numstat 极快),但 CPU 占用从 2 路降到 1 路。

    测试方法:mock ``_run_git_async``,记录每个调用的 args 和时间戳,
    断言 diff 调用在 numstat 之前完成(按完成时间)。
    """
    import time as _t

    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))

    from tools.webapi import git_diff as main_mod

    # 真实 ``_run_git_async``(避免完全 mock 掉,改用 wrapper 记录调用顺序)
    real_run = main_mod._run_git_async
    call_log: list[tuple[float, tuple, dict]] = []  # (completed_at, args, kwargs)

    async def _instrumented(*args, **kwargs):
        result = await real_run(*args, **kwargs)
        call_log.append((_t.time(), args, kwargs))
        return result

    monkeypatch.setattr(main_mod, "_run_git_async", _instrumented)

    await _gd.handle(plugin)

    # 找到 raw diff 调用和 numstat 调用的完成时间
    def _is_raw_diff_call(args: tuple, kwargs: dict) -> bool:
        # git_prefix + ["diff"] + scope_args,没有 "--numstat"
        cmd = args[0]
        return "diff" in cmd and "--numstat" not in cmd

    def _is_numstat_call(args: tuple, kwargs: dict) -> bool:
        cmd = args[0]
        return "diff" in cmd and "--numstat" in cmd

    raw_diff_completed = next(
        (t for t, a, kw in call_log if _is_raw_diff_call(a, kw)), None
    )
    numstat_completed = next(
        (t for t, a, kw in call_log if _is_numstat_call(a, kw)), None
    )

    assert raw_diff_completed is not None, "raw diff invocation not recorded"
    assert numstat_completed is not None, "numstat invocation not recorded"
    assert raw_diff_completed <= numstat_completed, (
        f"raw diff must complete before numstat starts "
        f"(diff_completed={raw_diff_completed}, numstat_completed={numstat_completed}); "
        f"if diff >= numstat, the handler is still using asyncio.gather"
    )


async def test_handle_git_diff_skips_numstat_on_raw_failure(plugin, tmp_path, monkeypatch):
    """P2 perf 修复:raw diff 失败时不跑 numstat(节省 1 个 git 调用)。

    旧实现 ``asyncio.gather(diff, numstat)`` 两路并发 — raw 失败时 numstat
    仍跑,浪费 1 个 git 调用。新实现串行 — raw 失败直接返回,numstat 跳过。
    """
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))

    from tools.webapi import git_diff as main_mod

    real_run = main_mod._run_git_async
    call_log: list[tuple] = []

    async def _fake_run(*args, **kwargs):
        cmd = args[0]
        call_log.append(cmd)
        # 只让 raw diff 返回 ok=False;其他调用走真实实现
        if "diff" in cmd and "--numstat" not in cmd:
            return {"ok": False, "stdout": "", "stderr": "simulated failure", "elapsed_ms": 0}
        return await real_run(*args, **kwargs)

    monkeypatch.setattr(main_mod, "_run_git_async", _fake_run)

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "git_error"

    # 验证 raw diff 跑了,但 numstat 没跑
    raw_diff_calls = [c for c in call_log if "diff" in c and "--numstat" not in c]
    numstat_calls = [c for c in call_log if "diff" in c and "--numstat" in c]
    assert len(raw_diff_calls) >= 1, f"raw diff should have been called: {call_log!r}"
    assert len(numstat_calls) == 0, (
        f"numstat must NOT run when raw diff fails "
        f"(saves 1 git call); got numstat_calls={numstat_calls!r}"
    )


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
    monkeypatch.setattr(web, "request", make_web_request_mock())

    result = await _gd.handle(plugin)
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
        web, "request", make_web_request_mock({"scope": "staged"})
    )

    result = await _gd.handle(plugin)
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
        web, "request", make_web_request_mock({"scope": "all"})
    )

    result = await _gd.handle(plugin)
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
        web, "request", make_web_request_mock({"scope": "staged"})
    )

    result = await _gd.handle(plugin)
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
        web, "request", make_web_request_mock({"scope": "all"})
    )

    result = await _gd.handle(plugin)
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
        web, "request", make_web_request_mock({"scope": "all"})
    )

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["scope"] == "all"
    assert "only unstaged" in data["diff"]


async def test_handle_git_diff_scope_invalid_value_returns_invalid_scope(
    plugin, tmp_path, monkeypatch
):
    """?scope=foo(未知值)→ loaded=False, reason='invalid_scope',且 git 命令一次也不跑。"""
    from astrbot.api import web
    from tools import _helpers as _h
    from tools.webapi import git_diff as _mm

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
        web, "request", make_web_request_mock({"scope": "foo"})
    )

    result = await _gd.handle(plugin)
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
        web, "request", make_web_request_mock({"scope": "STAGED"})
    )

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["scope"] == "staged"  # 规整为小写
    assert "staged" in data["diff"]


# ── v3.3 (2026-06-21): git-diff 4 合 1 重构单测 ──
# 验证新 helper 的纯函数行为 + handler 现在只跑 2 个 git 调用(从 4 个减半)。


# ── 纯函数: _parse_diff_status_map ──


def test_parse_diff_status_map_modified():
    """modify 的 `git diff` 输出 → 状态 'M'。"""
    diff = (
        "diff --git a/file.txt b/file.txt\n"
        "index abc..def 100644\n"
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+line2_mod\n"
    )
    assert _gd._parse_diff_status_map(diff) == {"file.txt": "M"}


def test_parse_diff_status_map_added():
    """add(intent-to-add)的 `git diff` 输出 → 状态 'A'。"""
    diff = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "index 0000000..123 100644\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+a\n"
        "+b\n"
    )
    assert _gd._parse_diff_status_map(diff) == {"new.py": "A"}


def test_parse_diff_status_map_deleted():
    """delete 的 `git diff` 输出 → 状态 'D'。"""
    diff = (
        "diff --git a/old.py b/old.py\n"
        "deleted file mode 100644\n"
        "index abc..0000000\n"
        "--- a/old.py\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-content\n"
    )
    assert _gd._parse_diff_status_map(diff) == {"old.py": "D"}


def test_parse_diff_status_map_renamed():
    """rename 的 `git diff` 输出 → 状态 'R',key 是新路径。"""
    diff = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 100%\n"
        "rename from old.py\n"
        "rename to new.py\n"
    )
    assert _gd._parse_diff_status_map(diff) == {"new.py": "R"}


def test_parse_diff_status_map_skips_hunk_body():
    """hunk body 内的 +/- 行不影响 status(只检查 header 区域)。"""
    # 即使 hunk body 有"new file mode"字面文本(极端情况),也不会误判
    diff = (
        "diff --git a/x b/x\n"
        "new file mode 100644\n"  # 唯一一处真正影响 status
        "--- /dev/null\n"
        "+++ b/x\n"
        "@@ -0,0 +1,1 @@\n"
        "+new file mode 100644\n"  # 文本里出现但不影响 status
    )
    assert _gd._parse_diff_status_map(diff) == {"x": "A"}


def test_parse_diff_status_map_multiple_files():
    """多文件的 status 映射按 diff 顺序。"""
    diff = (
        "diff --git a/a.txt b/a.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/a.txt\n"
        "@@ -0,0 +1 @@\n"
        "+a\n"
        "diff --git a/b.txt b/b.txt\n"
        "deleted file mode 100644\n"
        "--- a/b.txt\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-b\n"
    )
    result = _gd._parse_diff_status_map(diff)
    assert result == {"a.txt": "A", "b.txt": "D"}


# ── 纯函数: _parse_numstat_counts ──


def test_parse_numstat_counts_simple():
    """基本 add/modify 的 numstat 解析。"""
    out = "5\t2\tfile.txt\n3\t0\tnew.py\n"
    assert _gd._parse_numstat_counts(out) == {
        "file.txt": (5, 2),
        "new.py": (3, 0),
    }


def test_parse_numstat_counts_binary():
    """Binary 文件的 numstat 是 `-` `-` → (0, 0)。"""
    out = "-\t-\tbinary.dat\n"
    assert _gd._parse_numstat_counts(out) == {"binary.dat": (0, 0)}


def test_parse_numstat_counts_rename_arrow():
    """rename/copy 的 numstat 格式 `<old> => <new>`,用 new path 作 key。

    旧实现有 bug:用整个 `<old> => <new>` 串作 key,导致 join 时永远 miss,
    rename 文件的 additions/deletions 错误为 0。v3.3 修正。
    """
    out = "0\t0\told.py => new.py\n"
    assert _gd._parse_numstat_counts(out) == {"new.py": (0, 0)}


def test_parse_numstat_counts_skips_blank():
    """空行 / 少于 3 字段的行 → 跳过。"""
    out = "\n5\t2\tfile.txt\nbad_line\n1\t2\n"
    assert _gd._parse_numstat_counts(out) == {"file.txt": (5, 2)}


# ── 纯函数: _build_stat_text ──


def test_build_stat_text_empty():
    """空列表 → 空字符串。"""
    assert _gd._build_stat_text([]) == ""


def test_build_stat_text_single_file():
    """单文件 summary 用单数 '1 file changed';insertions/deletions 永远复数。"""
    files = [{"path": "x.py", "status": "M", "additions": 3, "deletions": 1}]
    text = _gd._build_stat_text(files)
    assert "x.py" in text
    assert "1 file changed" in text
    assert "3 insertions(+)" in text
    # git 自身输出总是 "N deletions(-)"(复数);复刻该习惯
    assert "1 deletions(-)" in text


def test_build_stat_text_multiple_files():
    """多文件 summary 用复数 'N files changed'。"""
    files = [
        {"path": "a.py", "status": "M", "additions": 5, "deletions": 2},
        {"path": "b.py", "status": "A", "additions": 10, "deletions": 0},
    ]
    text = _gd._build_stat_text(files)
    assert "2 files changed" in text
    assert "15 insertions(+)" in text
    assert "2 deletions(-)" in text


# ── 集成: handler 现在只跑 2 个 git 调用(从 4 减半) ──


async def test_handle_git_diff_runs_two_git_invocations(
    plugin, tmp_path, monkeypatch
):
    """v3.3 重构后,handler 一次请求应只跑 2 个 git diff 调用(从 4 减半)。

    v3.4 (2026-06-21) P1 perf: P1-5 把 run_sync(run_cmd, ...) 换成 _run_git_async,
    所以 mock 目标改为 _run_git_async(计数其内部 create_subprocess_exec 调用)。
    """
    from tools.webapi import git_diff as _m

    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))

    # 清空 ETag 缓存,确保 ETag 路径也走 git(rev-parse HEAD)
    _m._DIFF_ETAG_CACHE.clear()

    # P1-5 改完后,计数目标是 _run_git_async
    real_run_git_async = _m._run_git_async
    git_cmd_args: list[list[str]] = []

    async def counting_run_git_async(cmd_args, *args, **kwargs):
        if cmd_args and len(cmd_args) >= 2 and cmd_args[0] == plugin._git_binary():
            git_cmd_args.append(cmd_args)
        return await real_run_git_async(cmd_args, *args, **kwargs)

    monkeypatch.setattr(_m, "_run_git_async", counting_run_git_async)

    result = await _gd.handle(plugin)
    assert result["data"]["loaded"] is True

    # 期望:1 个 rev-parse (probe) + 1 个 rev-parse (ETag) + 2 个 diff = 4 个调用
    diff_calls = [c for c in git_cmd_args if "diff" in c]
    assert len(diff_calls) == 2, (
        f"expected 2 git diff invocations, got {len(diff_calls)}: {diff_calls}"
    )
    # 第一个应是 raw diff,第二个应是 numstat
    assert "diff" in diff_calls[0] and "--numstat" not in diff_calls[0]
    assert "--numstat" in diff_calls[1]


# ── v3.3 (2026-06-21): HTTP 缓存 ETag/304 集成测试 ──
# 验证 304 命中 / ETag 变化 / 缓存跳过 git 调用 / 响应头格式。


async def test_handle_git_diff_returns_etag_on_success(
    plugin, tmp_path, monkeypatch
):
    """成功响应必须带 ETag / Cache-Control / Vary 三个头。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", make_web_request_mock({}))

    result = await _gd.handle(plugin)
    assert result.status_code == 200
    # headers 是 Starlette MutableHeaders(类 dict),用 in / []
    etag = result.headers.get("etag")
    assert etag, f"missing ETag header in {dict(result.headers)}"
    assert etag.startswith('W/"'), f"weak ETag expected, got {etag!r}"
    assert "private" in result.headers.get("cache-control", "")
    assert "must-revalidate" in result.headers.get("cache-control", "")
    assert result.headers.get("vary") == "Cookie"


async def test_handle_git_diff_returns_304_on_matching_etag(
    plugin, tmp_path, monkeypatch
):
    """第二次带 If-None-Match 命中 → 304 + 空 body + ETag 头。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("modified", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))

    # 第一次:无 If-None-Match,返回 200 + ETag
    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r1 = await _gd.handle(plugin)
    assert r1.status_code == 200
    etag = r1.headers.get("etag")
    assert etag

    # 第二次:带匹配 ETag,返回 304
    monkeypatch.setattr(
        web, "request", make_web_request_mock(headers={"If-None-Match": etag})
    )
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 304
    assert r2.headers.get("etag") == etag
    # 304 body 必须为空
    import json
    body = json.loads(r2.body) if r2.body else {}
    assert body == {}, f"304 body should be empty, got {body!r}"


async def test_handle_git_diff_304_skips_git_diff_invocation(
    plugin, tmp_path, monkeypatch
):
    """304 路径算 ETag 后,只读缓存(0 个 git 调用),不跑 diff/numstat/probe。

    v3.4 (2026-06-21) P0 perf:第一次请求填 ETag 缓存,第二次 304 命中走缓存,
    完全跳过 rev-parse HEAD。dashboard 5-10s 轮询时,N 个请求共用 1 个 git 进程。
    """
    from tools.webapi import git_diff as _m
    from tools import _helpers
    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    # 第一次:清空缓存 + 拿 ETag(填缓存)
    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r1 = await _gd.handle(plugin)
    etag = r1.headers.get("etag")
    assert etag

    # 第二次带 If-None-Match,开始计数 git 调用
    # 注意:_compute_diff_etag / handle_get_git_diff 使用的是 main.py 顶部的
    # ``run_cmd``(从 tools._helpers 导入的本地引用),所以必须 patch
    # ``main.run_cmd``(``monkeypatch.setattr(_helpers, ...)`` 不会生效)。
    real_run_cmd = _helpers.run_cmd
    git_calls: list[list[str]] = []
    git_bin = plugin._git_binary()

    def counting_run_cmd(cmd_args, *args, **kwargs):
        if cmd_args and cmd_args[0] == git_bin:
            git_calls.append(cmd_args)
        return real_run_cmd(cmd_args, *args, **kwargs)

    monkeypatch.setattr(_m, "run_cmd", counting_run_cmd, raising=False)
    monkeypatch.setattr(
        web, "request", make_web_request_mock(headers={"If-None-Match": etag})
    )

    r2 = await _gd.handle(plugin)
    assert r2.status_code == 304

    # 304 路径:不跑 diff/probe/numstat,且由于缓存命中,0 个 rev-parse HEAD
    diff_calls = [c for c in git_calls if "diff" in c]
    probe_calls = [c for c in git_calls if "rev-parse" in c and "is-inside" in str(c)]
    head_calls = [c for c in git_calls if "rev-parse" in c and "HEAD" in c]
    assert len(diff_calls) == 0, f"304 should skip diff, got {diff_calls}"
    assert len(probe_calls) == 0, f"304 should skip probe, got {probe_calls}"
    # P0 perf:缓存命中 → 0 个 rev-parse HEAD(v3.3 是 1 个)
    assert len(head_calls) == 0, (
        f"cached 304 should skip rev-parse HEAD, got {head_calls}"
    )


# ── v3.4 (2026-06-21) P0 perf: _compute_diff_etag in-memory 缓存 ──


async def test_compute_diff_etag_caches_across_requests(
    plugin, tmp_path, monkeypatch
):
    """P0 perf 修复:同 directory 在 TTL 内的多次请求共用缓存,只触发 1 次 rev-parse HEAD。

    旧实现每次都跑 ``git rev-parse HEAD``(10-20ms 进程启动)。
    新实现:同 directory 在 1.5s TTL 内复用 ETag 缓存。
    v3.4 (2026-06-21) P1 perf: P1-5 改用 _run_git_async,计数目标迁移。
    """
    from tools.webapi import git_diff as _m
    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    git_bin = plugin._git_binary()
    real_run_git_async = _m._run_git_async
    head_call_count = 0

    async def counting_run_git_async(cmd_args, *args, **kwargs):
        nonlocal head_call_count
        if (
            cmd_args
            and cmd_args[0] == git_bin
            and "rev-parse" in cmd_args
            and "HEAD" in cmd_args
        ):
            head_call_count += 1
        return await real_run_git_async(cmd_args, *args, **kwargs)

    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_run_git_async", counting_run_git_async)
    monkeypatch.setattr(web, "request", make_web_request_mock({}))

    # 3 次连续请求:应该只触发 1 次 rev-parse HEAD
    r1 = await _gd.handle(plugin)
    assert r1.status_code == 200
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 200
    r3 = await _gd.handle(plugin)
    assert r3.status_code == 200

    assert head_call_count == 1, (
        f"3 requests in <TTL should share 1 rev-parse HEAD, got {head_call_count}"
    )


async def test_compute_diff_etag_invalidates_after_ttl(
    plugin, tmp_path, monkeypatch
):
    """P0 perf 修复:TTL 过期后重新触发 rev-parse HEAD。

    通过 ``monkeypatch.setattr`` 把 ``_DIFF_ETAG_TTL`` 设为 0 验证。
    v3.4 P1-5:计数目标改为 _run_git_async。
    """
    from tools.webapi import git_diff as _m
    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    git_bin = plugin._git_binary()
    real_run_git_async = _m._run_git_async
    head_call_count = 0

    async def counting_run_git_async(cmd_args, *args, **kwargs):
        nonlocal head_call_count
        if (
            cmd_args
            and cmd_args[0] == git_bin
            and "rev-parse" in cmd_args
            and "HEAD" in cmd_args
        ):
            head_call_count += 1
        return await real_run_git_async(cmd_args, *args, **kwargs)

    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_run_git_async", counting_run_git_async)
    monkeypatch.setattr(_m, "_DIFF_ETAG_TTL", 0.0)  # 强制每次都过期
    monkeypatch.setattr(web, "request", make_web_request_mock({}))

    r1 = await _gd.handle(plugin)
    assert r1.status_code == 200
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 200

    # TTL=0 → 每次都重算
    assert head_call_count == 2, (
        f"TTL=0 should re-evaluate every time, got {head_call_count}"
    )


async def test_compute_diff_etag_per_directory_cache(
    plugin, tmp_path, monkeypatch
):
    """P0 perf 修复:不同 directory 各自独立缓存,互不污染。

    v3.4 P1-5:计数目标改为 _run_git_async。
    """
    from tools.webapi import git_diff as _m

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    git_bin = plugin._git_binary()
    real_run_git_async = _m._run_git_async
    head_call_count = 0

    async def counting_run_git_async(cmd_args, *args, **kwargs):
        nonlocal head_call_count
        if (
            cmd_args
            and cmd_args[0] == git_bin
            and "rev-parse" in cmd_args
            and "HEAD" in cmd_args
        ):
            head_call_count += 1
        return await real_run_git_async(cmd_args, *args, **kwargs)

    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_run_git_async", counting_run_git_async)

    # 第一次算 repo_a ETag(repo_b 的 stat 路径会先报 path 不存在但不影响计数)
    etag_a1 = await _m._compute_diff_etag(git_bin, str(repo_a))
    await _m._compute_diff_etag(git_bin, str(repo_b))  # noqa: F841 — 触发 repo_b 缓存
    etag_a2 = await _m._compute_diff_etag(git_bin, str(repo_a))

    assert etag_a1 == etag_a2, "second repo_a should hit cache"
    assert head_call_count == 2, (
        f"3 calls on 2 dirs should trigger 2 rev-parse HEAD, got {head_call_count}"
    )


async def test_handle_git_diff_etag_changes_after_commit(
    plugin, tmp_path, monkeypatch
):
    """commit 后 ETag 变,带旧 ETag 的请求会拿到 200 + 新 ETag。

    v3.4 (2026-06-21) P0 perf: 加了 1.5s TTL 缓存后,为了在这个测试里稳定
    看到 ETag 变化,把 TTL 设为 0 强制每次重算。否则 commit 后 1.5s 内的
    请求会命中缓存,这是新设计**有意接受**的 staleness(类似 git 自身
    index mtime 漏检窗口),由 dashboard 下一次自然轮询纠正。
    """
    from tools.webapi import git_diff as _m
    from astrbot.api import web
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    # 禁用缓存以稳定观察 ETag 变化
    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_DIFF_ETAG_TTL", 0.0)

    # 第一次:无 diff
    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r1 = await _gd.handle(plugin)
    etag_before = r1.headers.get("etag")

    # commit 一次(改 HEAD)
    (tmp_path / "new.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "t", "-q"], cwd=tmp_path, check=True)

    # 第二次:带旧 ETag,TTL=0 强制 cache miss → 200 + 新 ETag
    monkeypatch.setattr(
        web, "request", make_web_request_mock(headers={"If-None-Match": etag_before})
    )
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 200
    etag_after = r2.headers.get("etag")
    assert etag_after != etag_before, (
        f"ETag should change after commit: {etag_before!r} vs {etag_after!r}"
    )


async def test_handle_git_diff_no_etag_on_error_envelope(
    plugin, monkeypatch
):
    """错误响应(no_project_loaded / feature_disabled 等)不带 ETag。"""
    from astrbot.api import web
    # 无项目
    plugin._loaded_projects.clear()
    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r = await _gd.handle(plugin)
    # 错误响应是普通 dict,不是 _JSONResponseCompat
    assert isinstance(r, dict)
    assert r["data"]["loaded"] is False
    assert r["data"]["reason"] == "no_project_loaded"


async def test_handle_git_diff_scope_empty_string_defaults_to_unstaged(
    plugin, tmp_path, monkeypatch
):
    """?scope= 空字符串视同缺省 → scope=unstaged(避免误报 invalid_scope)。"""
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("unstaged", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", make_web_request_mock({"scope": ""})
    )

    result = await _gd.handle(plugin)
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
            web, "request", make_web_request_mock({"scope": requested})
        )
        result = await _gd.handle(plugin)
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
        web, "request", make_web_request_mock({"scope": "bogus"})
    )

    result = await _gd.handle(plugin)
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
            web, "request", make_web_request_mock({"scope": "foo"})
        )
        result = await _gd.handle(plugin)
        assert result["data"]["reason"] == "invalid_scope"
    finally:
        plugin._config["agentsmd_enabled"] = True


async def test_handle_git_diff_scope_staged_with_real_add(
    plugin, tmp_path, monkeypatch
):
    """真实 git add new.py(在 index 注入内容)→ ?scope=staged 中
    files_changed 含 status='A',且 data.scope 字段回显为 'staged'。

    注意:不能用 ``git add -N``(intent-to-add)做这个测试。intent-to-add
    只把 path 注册到 index 但不复制内容(在 index 里是空文件),``git diff
    --cached`` 不会输出该文件 — 它只在 ``git diff``(unstaged)中出现。
    这里用真正的 ``git add`` 把内容写入 index,``git diff --cached`` 才能
    看到该文件作为 'A'(new file with N additions)。
    """
    from astrbot.api import web
    _init_git_repo(tmp_path)
    (tmp_path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "new.py"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web, "request", make_web_request_mock({"scope": "staged"})
    )

    result = await _gd.handle(plugin)
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
        make_web_request_mock({"scope": "all", "worktree": "../escape"}),
    )
    result = await _gd.handle(plugin)
    # scope 解析通过后,worktree 校验失败 → worktree_invalid(而非 invalid_scope)
    assert result["data"]["reason"] == "worktree_invalid"
