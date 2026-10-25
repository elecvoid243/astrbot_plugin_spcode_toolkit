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
# NOTE(2026-07-18, elecvoid243): 本文件内的纯同步解析器测试也统一写成
# ``async def``,以满足 strict 模式下的全局 mark(消除 PytestWarning)。
# 参照 test_git_path_schema_field_exists 的既有约定。

# main.py 用相对导入,需把项目父目录加到 sys.path 然后以包形式导入。
# 同时让 `from tools import _helpers` 和 `import main` 这种绝对导入也能工作
# (与 tests/test_project_subcommand.py 相同的 sys.path 设置)。
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent  # F:\github
_PROJECT_DIR = (
    Path(__file__).resolve().parent.parent
)  # F:\github\astrbot_plugin_spcode_toolkit
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

# v3.4: git-diff handler 已从 main.py 搬到 tools/webapi/git_diff.py
# (Task 4.1 — webapi extraction chunk 5)。所有 helper (_parse_X,
# _build_stat_text, _compute_diff_etag, _DIFF_ETAG_CACHE/TL, _run_git_async)
# 都从 main._X 改到 _gd._X(handler 入口也从 plugin.handle_get_git_diff()
# 改为 _gd.handle(plugin))。
# SPCodeToolkit 类本身仍来自 main(用于测试中 _make_plugin 构造)
from astrbot_plugin_spcode_toolkit import main as _main_mod  # noqa: E402
from tools.project import state as _proj_state  # noqa: E402
from tools.webapi import git_diff as _gd  # noqa: E402

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
    """Inject a project into tools.project.state bypassing /project load.

    PR-7 (2026-06-23): 数据源迁到 ``tools.project.state`` 模块级单例后,
    测试也必须直接写入 state(handler 读 ``plugin.get_loaded_project(umo)``,
    内部委托给 ``state.get(umo)``)。
    """
    _proj_state.put(
        umo,
        {
            "directory": str(directory),
            "loaded_at": time.time(),
        },
    )


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
    # v2.32.0 渐进式加载:元数据端点不再携带 patch 文本
    assert "diff" not in data
    assert data["files_changed"] == []
    assert data["total_files"] == 0
    assert data["has_more"] is False
    assert data["etag"]
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


# --- T7-T11: gating (no project, umo mismatch, fallback, feature flag) -


async def test_handle_git_diff_no_project_loaded(plugin):
    _proj_state.reset()
    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "no_project_loaded"


async def test_handle_git_diff_umo_not_in_loaded(plugin, tmp_path):
    # do NOT load any project; query a specific umo
    _proj_state.reset()
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
    _proj_state.reset()
    _proj_state.put("umo:1", {"directory": str(a), "loaded_at": 1.0})
    _proj_state.put("umo:2", {"directory": str(b), "loaded_at": 2.0})
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
    _proj_state.put(
        "test:umo",
        {
            "directory": "/nonexistent/path/that/does/not/exist",
            "loaded_at": time.time(),
        },
    )
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
        "loaded",
        "directory",
        "umo",
        "scope",
        "etag",
        "files_changed",
        "stat",
        "total_files",
        "total_additions",
        "total_deletions",
        "offset",
        "limit",
        "has_more",
        "elapsed_ms",
        "reason",
    }
    assert expected_keys.issubset(data.keys())
    # v2.32.0 渐进式加载:patch 文本不再出现在元数据端点
    assert "diff" not in data
    assert "truncated" not in data


async def test_handle_git_diff_umo_query_param_hits_loaded_umo(
    plugin, tmp_path, monkeypatch
):
    _init_git_repo(tmp_path)
    _load_project(plugin, "target:umo", str(tmp_path))

    # Mock web.request.query to return "target:umo" only for the "umo" key.
    # v3.1 之前用 MagicMock(return_value="target:umo") 对所有 key 返回同值,
    # 但新增的 ?scope= 校验会把它误判为非法 scope 并提前返回。这里用
    # make_web_request_mock 让 umo / worktree / scope 三个 key 各自查表。
    from astrbot.api import web

    monkeypatch.setattr(web, "request", make_web_request_mock({"umo": "target:umo"}))

    result = await _gd.handle(plugin)
    assert result["data"]["umo"] == "target:umo"


async def test_handle_git_diff_paging_defaults(plugin, tmp_path):
    """v2.32.0: 不传 offset/limit 时默认 offset=0, limit=200, has_more=False。"""
    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["offset"] == 0
    assert data["limit"] == 200
    assert data["has_more"] is False
    assert data["total_files"] == 0


async def test_handle_git_diff_git_calls_run_serial(plugin, tmp_path, monkeypatch):
    """v3.5 (2026-06-30): 全部 git 调用按既定顺序串行执行(无 asyncio.gather)。

    v3.3 时代这个测试名叫 ``test_handle_git_diff_git_calls_run_concurrently``,
    验证 probe + diff/numstat/asyncio.gather 4 路并发。
    v3.4 (2026-06-21) P2 perf 改回串行(diff → numstat,走 page cache 命中),
    旧测试目标已过时。
    v3.5 (2026-06-30) ETag 修复后,git 调用数从 3 (probe + diff + numstat) 增到
    7 (4 ETag 探测 + 1 probe + 1 diff + 1 numstat),全部按既定顺序串行。

    v2.32.0 (2026-10-25) 渐进式加载:完整 patch 渲染从轮询路径移除,
    git 调用从 7 降到 6 (4 ETag + probe + numstat)。

    新测试目标:
      - 顺序:Etag 4 个 → probe → numstat
      - 计数:6 个 _run_git_async 调用
      - 全量 patch 渲染调用(``diff`` 无 --numstat)不再出现
    """
    from tools.webapi import git_diff as main_mod
    from tools.webapi import _helpers as _h

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    real_run = main_mod._run_git_async
    call_log: list[list[str]] = []

    async def _recording_run(cmd_args, *args, **kwargs):
        call_log.append(list(cmd_args))
        return await real_run(cmd_args, *args, **kwargs)

    monkeypatch.setattr(main_mod, "_run_git_async", _recording_run)
    monkeypatch.setattr(_h, "_run_git_async", _recording_run)

    await _gd.handle(plugin)

    # 验证调用顺序:ETag 4 个 → probe → numstat
    assert len(call_log) == 6, (
        f"Expected 6 git calls (4 ETag + probe + numstat), got {len(call_log)}"
    )

    # ETag 4 个:rev-parse HEAD + diff-files + diff-index + ls-files
    etag_cmds = [cmd[2:] for cmd in call_log[:4]]  # strip "git -C dir"
    assert "rev-parse" in etag_cmds[0] and "HEAD" in etag_cmds[0], (
        f"First ETag call should be rev-parse HEAD, got {etag_cmds[0]}"
    )
    assert "diff-files" in etag_cmds[1], (
        f"Second ETag call should be diff-files, got {etag_cmds[1]}"
    )
    assert "diff-index" in etag_cmds[2] and "--cached" in etag_cmds[2], (
        f"Third ETag call should be diff-index --cached, got {etag_cmds[2]}"
    )
    assert "ls-files" in etag_cmds[3] and "--others" in etag_cmds[3], (
        f"Fourth ETag call should be ls-files --others, got {etag_cmds[3]}"
    )

    # 后续 2 个:probe + numstat(raw diff 渲染已从轮询路径移除)
    assert "--is-inside-work-tree" in call_log[4], (
        f"Fifth call should be probe, got {call_log[4]}"
    )
    assert "diff" in call_log[5] and "--numstat" in call_log[5], (
        f"Sixth call should be diff --numstat, got {call_log[5]}"
    )
    raw_diff_calls = [
        c for c in call_log if "diff" in c and "--numstat" not in c and "raw" not in c
        and "diff-files" not in c and "diff-index" not in c
    ]
    assert len(raw_diff_calls) == 0, (
        f"metadata endpoint must NOT render the full patch; got {raw_diff_calls}"
    )


# ── v3.4 (2026-06-21) P2 perf: git diff / numstat 串行 ──


async def test_handle_git_diff_metadata_cache_skips_numstat(
    plugin, tmp_path, monkeypatch
):
    """v2.32.0 渐进式加载:同一工作区状态下的第二次请求(etag 未变)
    从元数据缓存取记录,不再重跑 numstat——分页翻页请求因此廉价。

    注意:ETag 有自己的 1.5s TTL 缓存,第二次调用的 4 个 ETag 探测也
    全部命中缓存;probe(rev-parse --is-inside-work-tree)始终执行。
    """
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))

    from tools.webapi import git_diff as main_mod

    # 清空两级缓存,确保第一次请求真实执行
    main_mod._DIFF_ETAG_CACHE.clear()
    main_mod._META_CACHE.clear()

    real_run = main_mod._run_git_async
    call_log: list[list[str]] = []

    async def _recording_run(cmd_args, *args, **kwargs):
        call_log.append(list(cmd_args))
        return await real_run(cmd_args, *args, **kwargs)

    monkeypatch.setattr(main_mod, "_run_git_async", _recording_run)

    await _gd.handle(plugin)
    numstat_after_first = len(
        [c for c in call_log if "diff" in c and "--numstat" in c]
    )
    calls_after_first = len(call_log)

    await _gd.handle(plugin)

    numstat_after_second = len(
        [c for c in call_log if "diff" in c and "--numstat" in c]
    )
    assert numstat_after_first == 1, (
        f"first request should run numstat once, got {numstat_after_first}"
    )
    assert numstat_after_second == 1, (
        f"second request must reuse the metadata cache "
        f"(numstat ran {numstat_after_second} times total)"
    )
    # 第二次只有 probe 一个 git 调用(ETag 缓存 + 元数据缓存全部命中)
    assert len(call_log) - calls_after_first == 1, (
        f"second request should run exactly 1 git call (probe), "
        f"ran {len(call_log) - calls_after_first}"
    )


async def test_handle_git_diff_numstat_failure_maps_to_git_error(
    plugin, tmp_path, monkeypatch
):
    """v2.32.0: numstat 调用失败 → loaded=False, reason=git_error。

    元数据端点唯一的 diff 调用就是 numstat;失败时直接返回错误信封。
    """
    _init_git_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))

    from tools.webapi import git_diff as main_mod

    real_run = main_mod._run_git_async

    async def _fake_run(*args, **kwargs):
        cmd = args[0]
        if "diff" in cmd and "--numstat" in cmd:
            return {
                "ok": False,
                "stdout": "",
                "stderr": "simulated failure",
                "elapsed_ms": 0,
            }
        return await real_run(*args, **kwargs)

    monkeypatch.setattr(main_mod, "_run_git_async", _fake_run)

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is False
    assert data["reason"] == "git_error"


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
    # unstaged 改动出现在文件列表中(元数据端点不再携带 patch)
    assert any(f["path"] == "README.md" for f in data["files_changed"])


async def test_handle_git_diff_scope_staged_returns_staged_diff(
    plugin, tmp_path, monkeypatch
):
    """?scope=staged → 走 git diff --cached,只显示已暂存内容。"""
    from astrbot.api import web

    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("staged change", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    # 之后在已 staged 之上再做 unstaged 修改
    (tmp_path / "README.md").write_text("staged + extra edit", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "staged"}))

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is True
    assert data["scope"] == "staged"
    # staged 范围只含该文件;内容级断言(staged change vs extra edit)
    # 移到 /spcode/git-diff/full 的测试(patch 文本在 full 端点)
    assert any(f["path"] == "README.md" for f in data["files_changed"])


async def test_handle_git_diff_scope_all_returns_combined_diff(
    plugin, tmp_path, monkeypatch
):
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
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "all"}))

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is True
    assert data["scope"] == "all"
    # HEAD 范围含 working tree 文件(内容级断言移到 /full 测试)
    assert any(f["path"] == "README.md" for f in data["files_changed"])


async def test_handle_git_diff_scope_staged_empty_when_no_staged_changes(
    plugin, tmp_path, monkeypatch
):
    """仅 unstaged 改动时,?scope=staged 返回空 diff。"""
    from astrbot.api import web

    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("only unstaged", encoding="utf-8")
    # 注意:不调用 git add
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "staged"}))

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["loaded"] is True
    assert data["scope"] == "staged"
    assert data["files_changed"] == []
    assert data["total_files"] == 0


async def test_handle_git_diff_scope_all_with_only_staged_changes(
    plugin, tmp_path, monkeypatch
):
    """仅 staged 改动时,?scope=all 返回与 ?scope=staged 相同 diff。"""
    from astrbot.api import web

    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("only staged", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "all"}))

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["scope"] == "all"
    # staged 改动在 all 范围内仍出现在文件列表(内容级断言移到 /full)
    assert any(f["path"] == "README.md" for f in data["files_changed"])


async def test_handle_git_diff_scope_all_with_only_unstaged_changes(
    plugin, tmp_path, monkeypatch
):
    """仅 unstaged 改动时,?scope=all 返回与 ?scope=unstaged 相同 diff。"""
    from astrbot.api import web

    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("only unstaged", encoding="utf-8")
    # 不 git add
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "all"}))

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["scope"] == "all"
    assert any(f["path"] == "README.md" for f in data["files_changed"])


async def test_handle_git_diff_scope_invalid_value_returns_invalid_scope(
    plugin, tmp_path, monkeypatch
):
    """?scope=foo(未知值)→ loaded=False, reason='invalid_scope',且 git 命令一次也不跑。"""
    from tools import _helpers as _h
    from tools.webapi import git_diff as _mm

    from astrbot.api import web

    real_run_cmd = _h.run_cmd
    call_count = {"n": 0}

    def _counted_run_cmd(*args, **kwargs):
        call_count["n"] += 1
        return real_run_cmd(*args, **kwargs)

    monkeypatch.setattr(_h, "run_cmd", _counted_run_cmd)
    monkeypatch.setattr(_mm, "run_cmd", _counted_run_cmd, raising=False)

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "foo"}))

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
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "STAGED"}))

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["scope"] == "staged"  # 规整为小写
    assert any(f["path"] == "README.md" for f in data["files_changed"])


# ── v2.32.0 (2026-10-25): 渐进式加载后 handler 只跑 1 个 diff 调用 ──


# ── 纯函数: _parse_raw_numstat_z (v2.21 canonical-paths) ──


async def test_parse_raw_numstat_z_regular_and_rename():
    """Raw and numstat records must join on canonical destination paths."""
    metadata = (
        ":100644 100644 aaaaaaa bbbbbbb M\0"
        "src/中文 文档.txt\0"
        ":100644 000000 aaaaaaa 0000000 D\0"
        "deleted.txt\0"
        ":100644 100644 aaaaaaa bbbbbbb R097\0"
        "旧 名称.txt\0"
        "新 名称.txt\0"
        "3\t1\tsrc/中文 文档.txt\0"
        "0\t2\tdeleted.txt\0"
        "4\t5\t\0"
        "旧 名称.txt\0"
        "新 名称.txt\0"
    )

    assert _gd._parse_raw_numstat_z(metadata) == [
        {
            "path": "src/中文 文档.txt",
            "status": "M",
            "additions": 3,
            "deletions": 1,
            "is_binary": False,
        },
        {
            "path": "deleted.txt",
            "status": "D",
            "additions": 0,
            "deletions": 2,
            "is_binary": False,
        },
        {
            "path": "新 名称.txt",
            "status": "R",
            "additions": 4,
            "deletions": 5,
            "is_binary": False,
        },
    ]


async def test_parse_raw_numstat_z_binary():
    """Binary numstat markers must remain zero counts."""
    metadata = ":100644 100644 aaaaaaa bbbbbbb M\0image.bin\0-\t-\timage.bin\0"

    assert _gd._parse_raw_numstat_z(metadata) == [
        {
            "path": "image.bin",
            "status": "M",
            "additions": 0,
            "deletions": 0,
            "is_binary": True,
        }
    ]


async def test_parse_raw_numstat_z_preserves_tabs_and_newlines():
    """NUL boundaries must preserve whitespace inside the path."""
    path = "dir/tab\tline\nname.txt"
    nul = "\0"
    metadata = (
        ":100644 100644 aaaaaaa bbbbbbb M" + nul + path + nul + "1\t2\t" + path + nul
    )

    assert _gd._parse_raw_numstat_z(metadata)[0]["path"] == path


async def test_parse_raw_numstat_z_rejects_malformed_raw_header():
    """Malformed raw metadata must fail instead of producing wrong paths."""
    import pytest

    with pytest.raises(ValueError, match="raw metadata header"):
        _gd._parse_raw_numstat_z(":broken\0file.txt\0")


async def test_parse_raw_numstat_z_rejects_malformed_numstat():
    """Malformed numstat metadata must fail instead of dropping file data."""
    import pytest

    metadata = ":100644 100644 aaaaaaa bbbbbbb M\0file.txt\0not-numstat\0"

    with pytest.raises(ValueError, match="numstat record"):
        _gd._parse_raw_numstat_z(metadata)


# ── 纯函数: _build_stat_text ──


async def test_build_stat_text_empty():
    """空列表 → 空字符串。"""
    assert _gd._build_stat_text([]) == ""


async def test_build_stat_text_single_file():
    """单文件 summary 用单数 '1 file changed';insertions/deletions 永远复数。"""
    files = [{"path": "x.py", "status": "M", "additions": 3, "deletions": 1}]
    text = _gd._build_stat_text(files)
    assert "x.py" in text
    assert "1 file changed" in text
    assert "3 insertions(+)" in text
    # git 自身输出总是 "N deletions(-)"(复数);复刻该习惯
    assert "1 deletions(-)" in text


async def test_build_stat_text_multiple_files():
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


async def test_handle_git_diff_runs_one_diff_invocation(plugin, tmp_path, monkeypatch):
    """v2.32.0 渐进式加载后,handler 一次请求只跑 1 个 diff 调用(numstat)。

    完整 patch 渲染调用已从轮询路径移除(v2.32.0);etag 探测是另外 4 个
    调用,不归本测试计数。
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

    # 期望:numstat 1 个 diff 调用(完整 patch 渲染已移除)
    diff_calls = [c for c in git_cmd_args if "diff" in c]
    assert len(diff_calls) == 1, (
        f"expected 1 git diff invocation (numstat), got {len(diff_calls)}: {diff_calls}"
    )
    assert "--numstat" in diff_calls[0]


# ── v3.3 (2026-06-21): HTTP 缓存 ETag/304 集成测试 ──
# 验证 304 命中 / ETag 变化 / 缓存跳过 git 调用 / 响应头格式。


async def test_handle_git_diff_returns_etag_on_success(plugin, tmp_path, monkeypatch):
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
    from tools import _helpers
    from tools.webapi import git_diff as _m

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


async def test_handle_git_diff_unicode_filename_uses_canonical_path(
    plugin, tmp_path, monkeypatch
):
    """v2.21: Unicode file paths must round-trip unchanged through git-diff."""
    from astrbot.api import web

    _init_git_repo(tmp_path)
    unicode_filename = "提交文档.txt"
    (tmp_path / unicode_filename).write_text("v1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    (tmp_path / unicode_filename).write_text("v2", encoding="utf-8")
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(web, "request", make_web_request_mock({}))

    # Reset cached module-level state so the new file is picked up.
    _gd._DIFF_ETAG_CACHE.clear()

    result = await _gd.handle(plugin)

    assert result.status_code == 200
    paths = [item["path"] for item in result["data"]["files_changed"]]
    assert unicode_filename in paths


# ── v3.4 (2026-06-21) P0 perf: _compute_diff_etag in-memory 缓存 ──


async def test_compute_diff_etag_caches_across_requests(plugin, tmp_path, monkeypatch):
    """P0 perf 修复:同 directory 在 TTL 内的多次请求共用缓存,只触发 1 次 ETag 计算。

    v3.5 (2026-06-30): ETag 计算委托给 ``_compute_git_etag`` (在
    ``tools/webapi/_helpers.py`` 里),所以 mock 目标也迁移到 ``_helpers._run_git_async``。
    旧实现每次都跑 ``git rev-parse HEAD``(10-20ms 进程启动)。
    新实现:同 directory 在 1.5s TTL 内复用 ETag 缓存。
    v3.4 (2026-06-21) P1 perf: P1-5 改用 _run_git_async,计数目标迁移。
    """
    from tools.webapi import git_diff as _m
    from tools.webapi import _helpers as _h

    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    git_bin = plugin._git_binary()
    real_run_git_async = _h._run_git_async
    head_call_count = 0

    async def counting_run_git_async(cmd_args, *args, **kwargs):
        nonlocal head_call_count
        # v3.5: ETag 计算委托给 _helpers,任何 rev-parse HEAD 都计入
        if (
            cmd_args
            and cmd_args[0] == git_bin
            and "rev-parse" in cmd_args
            and "HEAD" in cmd_args
        ):
            head_call_count += 1
        return await real_run_git_async(cmd_args, *args, **kwargs)

    _m._DIFF_ETAG_CACHE.clear()
    # v3.5: 必须 patch _helpers._run_git_async,因为 _compute_git_etag 在
    # _helpers 模块命名空间里调它,不是 git_diff。
    monkeypatch.setattr(_h, "_run_git_async", counting_run_git_async)
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


async def test_compute_diff_etag_invalidates_after_ttl(plugin, tmp_path, monkeypatch):
    """P0 perf 修复:TTL 过期后重新触发 rev-parse HEAD。

    通过 ``monkeypatch.setattr`` 把 ``_DIFF_ETAG_TTL`` 设为 0 验证。
    v3.4 P1-5:计数目标改为 _run_git_async。
    v3.5:计数目标迁移到 _helpers._run_git_async(ETag 计算委托)。
    """
    from tools.webapi import git_diff as _m
    from tools.webapi import _helpers as _h

    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    git_bin = plugin._git_binary()
    real_run_git_async = _h._run_git_async
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
    monkeypatch.setattr(_h, "_run_git_async", counting_run_git_async)
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


async def test_compute_diff_etag_per_directory_cache(plugin, tmp_path, monkeypatch):
    """P0 perf 修复:不同 directory 各自独立缓存,互不污染。

    v3.4 P1-5:计数目标改为 _run_git_async。
    v3.5:计数目标迁移到 _helpers._run_git_async(ETag 计算委托)。
    """
    from tools.webapi import git_diff as _m
    from tools.webapi import _helpers as _h

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

    git_bin = plugin._git_binary()
    real_run_git_async = _h._run_git_async
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
    monkeypatch.setattr(_h, "_run_git_async", counting_run_git_async)

    # 第一次算 repo_a ETag(repo_b 的 stat 路径会先报 path 不存在但不影响计数)
    etag_a1 = await _m._compute_diff_etag(git_bin, str(repo_a))
    await _m._compute_diff_etag(git_bin, str(repo_b))  # noqa: F841 — 触发 repo_b 缓存
    etag_a2 = await _m._compute_diff_etag(git_bin, str(repo_a))

    assert etag_a1 == etag_a2, "second repo_a should hit cache"
    assert head_call_count == 2, (
        f"3 calls on 2 dirs should trigger 2 rev-parse HEAD, got {head_call_count}"
    )


# ────────────────────────────────────────────────────────────────────
# v3.5 (2026-06-30) 修复: ETag staleness 兜底
# 背景: 旧 ETag 算法只用 worktree 根目录 mtime + .git/index mtime + HEAD SHA,
#   当用户在工作树里编辑文件 (不 git add) 或新建未跟踪文件时:
#     - worktree 根目录 mtime 不变
#     - .git/index mtime 不变 (没 git add)
#     - HEAD SHA 不变 (没 commit)
#   → ETag 完全不变 → 带 If-None-Match 头持续 304 → dashboard 看到 stale diff。
# 修复 (Plan A): 在 ETag 中加入 3 个 git 探测的输出哈希
#     - git diff-files --name-only -z        (未暂存改动)
#     - git diff-index --cached --name-only -z HEAD  (已暂存改动)
#     - git ls-files --others --exclude-standard -z  (未跟踪文件)
# 详细见 docs/superpowers/specs/2026-06-30-git-etag-staleness-fix.md
# ────────────────────────────────────────────────────────────────────


async def test_handle_git_diff_etag_changes_after_unstaged_edit(
    plugin, tmp_path, monkeypatch
):
    """worktree 内文件被编辑 (不 git add) → ETag 必须变化。

    旧实现: wt_mtime + idx_mtime + HEAD SHA 都不变 → ETag 不变 → 304 stale
    新实现: 加入 ``git diff-files --name-only -z`` 探测 → 文件路径进入 ETag
    """
    from tools.webapi import git_diff as _m
    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_DIFF_ETAG_TTL", 0.0)

    # 1) 第一次: 无 diff, 拿 ETag
    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r1 = await _gd.handle(plugin)
    etag_before = r1.headers.get("etag")
    assert etag_before, "first response should have ETag"

    # 2) 编辑 README.md (不 git add)
    (tmp_path / "README.md").write_text("modified content", encoding="utf-8")

    # 3) 第二次: 带旧 ETag 头, TTL=0 强制 cache miss
    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(headers={"If-None-Match": etag_before}),
    )
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 200, (
        f"After unstaged edit, expected 200 (new ETag), got {r2.status_code} "
        f"— ETag still stale: {etag_before!r}"
    )
    etag_after = r2.headers.get("etag")
    assert etag_after != etag_before, (
        f"ETag must change after unstaged edit: "
        f"before={etag_before!r}, after={etag_after!r}"
    )


async def test_handle_git_diff_etag_changes_after_git_add(
    plugin, tmp_path, monkeypatch
):
    """git add 后 → ETag 必须变化 (即使没 commit)。"""
    from tools.webapi import git_diff as _m
    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_DIFF_ETAG_TTL", 0.0)

    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r1 = await _gd.handle(plugin)
    etag_before = r1.headers.get("etag")

    # git add 一个新文件 (不进 .git/index mtime 检查 → 改 idx_mtime)
    (tmp_path / "new_staged.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "new_staged.txt"], cwd=tmp_path, check=True)

    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(headers={"If-None-Match": etag_before}),
    )
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 200
    etag_after = r2.headers.get("etag")
    assert etag_after != etag_before, (
        f"ETag must change after git add: before={etag_before!r}, after={etag_after!r}"
    )


async def test_handle_git_diff_etag_changes_after_untracked_file(
    plugin, tmp_path, monkeypatch
):
    """新增未跟踪文件 → ETag 必须变化。

    旧实现: worktree mtime 不变 (新增文件不影响根目录 mtime 在多数 FS)
            idx_mtime 不变 (没 git add)
            HEAD SHA 不变
         → ETag 不变
    新实现: ``git ls-files --others --exclude-standard -z`` 探测进入 ETag
    """
    from tools.webapi import git_diff as _m
    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_DIFF_ETAG_TTL", 0.0)

    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r1 = await _gd.handle(plugin)
    etag_before = r1.headers.get("etag")

    # 新建未跟踪文件
    (tmp_path / "brand_new.txt").write_text("untracked", encoding="utf-8")

    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(headers={"If-None-Match": etag_before}),
    )
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 200
    etag_after = r2.headers.get("etag")
    assert etag_after != etag_before, (
        f"ETag must change after untracked file added: "
        f"before={etag_before!r}, after={etag_after!r}"
    )


async def test_handle_git_diff_stale_etag_no_longer_304(plugin, tmp_path, monkeypatch):
    """端到端: 修改文件 → 带旧 ETag 请求 → 必须 200 (不再是 304 stale)。

    这是 dashboard 实际场景的回归测试: 用户编辑文件后, dashboard 带
    上一次拿到的 ETag 来 polling, 必须收到 200 + 新 diff, 不能是 304。
    """
    from tools.webapi import git_diff as _m
    from astrbot.api import web

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))

    _m._DIFF_ETAG_CACHE.clear()
    monkeypatch.setattr(_m, "_DIFF_ETAG_TTL", 0.0)

    # 1) 首次请求
    monkeypatch.setattr(web, "request", make_web_request_mock({}))
    r1 = await _gd.handle(plugin)
    etag_before = r1.headers.get("etag")
    assert r1.status_code == 200

    # 2) 修改 README.md (不 git add)
    (tmp_path / "README.md").write_text("updated", encoding="utf-8")

    # 3) 带旧 ETag 请求 → 必须 200 + 新文件
    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock(headers={"If-None-Match": etag_before}),
    )
    r2 = await _gd.handle(plugin)
    assert r2.status_code == 200, (
        f"Stale ETag must not cause 304 after file change. "
        f"Got {r2.status_code} — ETag staleness bug regressed!"
    )
    files_after = r2["data"]["files_changed"]
    # 新 diff 应当包含 README.md
    paths = {f["path"] for f in files_after}
    assert "README.md" in paths, (
        f"After edit, files_changed should include README.md, got {paths}"
    )


async def test_handle_git_diff_etag_changes_after_commit(plugin, tmp_path, monkeypatch):
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


async def test_handle_git_diff_no_etag_on_error_envelope(plugin, monkeypatch):
    """错误响应(no_project_loaded / feature_disabled 等)不带 ETag。"""
    from astrbot.api import web

    # 无项目
    _proj_state.reset()
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
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": ""}))

    result = await _gd.handle(plugin)
    data = result["data"]
    assert data["scope"] == "unstaged"
    assert any(f["path"] == "README.md" for f in data["files_changed"])


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
        monkeypatch.setattr(web, "request", make_web_request_mock({"scope": requested}))
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
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "bogus"}))

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
        monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "foo"}))
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
    monkeypatch.setattr(web, "request", make_web_request_mock({"scope": "staged"}))

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
    from tools import _helpers as _h

    from astrbot.api import web

    # 让 _validate_worktree_param 返回 worktree_invalid(模拟 6 步防御链失败)
    def _reject_validate(*args, **kwargs):
        return None, "worktree_invalid"

    monkeypatch.setattr(_h, "_validate_worktree_param", _reject_validate)

    _init_git_repo(tmp_path)
    _load_project(plugin, "test:umo", str(tmp_path))
    monkeypatch.setattr(
        web,
        "request",
        make_web_request_mock({"scope": "all", "worktree": "../escape"}),
    )
    result = await _gd.handle(plugin)
    # scope 解析通过后,worktree 校验失败 → worktree_invalid(而非 invalid_scope)
    assert result["data"]["reason"] == "worktree_invalid"
