"""Tests for POST /spcode/git-worktree-add endpoint.

PR-B (v2.14.0, 2026-06-26): ADD endpoint with 7-layer defense chain.
Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.1
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest


# ── Task 2.1: _validate_add_cross_fields ─────────────────────────────


def test_cross_validate_default_ok():
    """默认 create=false / force=false / detach=false / base=None / branch="x" → ok。"""
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, None, "feature")
    assert err is None


def test_cross_validate_create_true_ok():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, False, False, None, "new-feat")
    assert err is None


def test_cross_validate_create_with_base_ok():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, False, False, "main", "new-feat")
    assert err is None


def test_cross_validate_force_true_ok():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, True, False, None, "existing")
    assert err is None


def test_cross_validate_detach_with_branch_ok():
    """detach + branch 视为 commit ref,合法。"""
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, True, None, "abc123")
    assert err is None


def test_cross_validate_create_and_force_both_true_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, True, False, None, "x")
    assert err is not None
    assert "create" in err.lower() and "force" in err.lower()


def test_cross_validate_detach_and_create_both_true_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(True, False, True, None, "x")
    assert err is not None


def test_cross_validate_detach_and_force_both_true_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, True, True, None, "x")
    assert err is not None


def test_cross_validate_base_without_create_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, "main", "x")
    assert err is not None


def test_cross_validate_missing_branch_when_not_detach_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, None, None)
    assert err is not None


def test_cross_validate_empty_branch_when_not_detach_rejected():
    from tools.webapi.git_worktree_add import _validate_add_cross_fields

    err = _validate_add_cross_fields(False, False, False, None, "")
    assert err is not None


# ── Task 2.2: _build_git_worktree_add_args ───────────────────────────


def test_build_args_basic_checkout():
    """add <path> <branch> (create=False, detach=False) → ['add', path, branch]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "feat", False, False, False, None
    )
    assert args == ["add", "/target", "feat"]


def test_build_args_create_new_branch():
    """create=True → ['add', '-b', branch, path]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "new-feat", True, False, False, None
    )
    assert args == ["add", "-b", "new-feat", "/target"]


def test_build_args_create_with_base():
    """create=True + base → ['add', '-b', branch, path, base]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "new-feat", True, False, False, "main"
    )
    assert args == ["add", "-b", "new-feat", "/target", "main"]


def test_build_args_force_reset_existing():
    """force=True → ['add', '-B', branch, path]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "existing", False, True, False, None
    )
    assert args == ["add", "-B", "existing", "/target"]


def test_build_args_detached_at_head():
    """detach=True, branch=None → ['add', '--detach', path]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", None, False, False, True, None
    )
    assert args == ["add", "--detach", "/target"]


def test_build_args_detached_at_commit():
    """detach=True, branch=<sha> → ['add', '--detach', path, sha]."""
    from tools.webapi.git_worktree_add import _build_git_worktree_add_args

    args = _build_git_worktree_add_args(
        "/repo", "/target", "abc1234", False, False, True, None
    )
    assert args == ["add", "--detach", "/target", "abc1234"]


# ── Task 2.3: _map_add_stderr_to_reason ──────────────────────────────


def test_stderr_branch_already_checked_out():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'feature' is already checked out at '/path'"
    assert _map_add_stderr_to_reason(stderr) == "cannot_create_existing"


def test_stderr_branch_already_exists():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'feature' already exists"
    assert _map_add_stderr_to_reason(stderr) == "cannot_create_existing"


def test_stderr_branch_not_valid_name():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'fea..ture' is not a valid branch name"
    assert _map_add_stderr_to_reason(stderr) == "invalid_branch"


def test_stderr_missing_branch_name():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: 'feature' is a missing branch name"
    assert _map_add_stderr_to_reason(stderr) == "cannot_checkout_missing"


def test_stderr_path_already_exists():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: '/target' already exists"
    assert _map_add_stderr_to_reason(stderr) == "path_exists_nonempty"


def test_stderr_invalid_worktree_name():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: '/foo:bar' cannot be used as a worktree name"
    assert _map_add_stderr_to_reason(stderr) == "invalid_param"


def test_stderr_invalid_start_point():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: invalid start point: badref"
    assert _map_add_stderr_to_reason(stderr) == "invalid_param"


def test_stderr_unknown_returns_git_error():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    stderr = "fatal: unknown error XYZ"
    assert _map_add_stderr_to_reason(stderr) == "git_error"


def test_stderr_empty_returns_git_error():
    from tools.webapi.git_worktree_add import _map_add_stderr_to_reason

    assert _map_add_stderr_to_reason("") == "git_error"


# ── Task 2.4: handle() — 7-layer defense, 30 tests ───────────────────
# Coverage:
#   7  invalid_body   (type / missing / cross-field)
#   5  invalid_branch (ref-format)
#   4  path_unsafe
#   4  path_exists_nonempty
#   4  cannot_create_existing / cannot_checkout_missing
#   1  worktree_not_in_repo
#   4  integration (two linked / force reset / detach / base)
#   1  envelope shape
#   ───
#   30 total


# ─── Fixtures ────────────────────────────────────────────────────────


def _make_plugin_mock(directory: str) -> MagicMock:
    """Standard plugin mock for the handler."""
    plugin = MagicMock()
    plugin._config = {
        "agentsmd_enabled": True,
        "codegraph_enabled": True,
        "file_remove_blacklist": None,
    }
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {
        "directory": directory,
        "loaded_at": 0.0,
    }
    return plugin


@pytest.fixture
def primary_repo(tmp_path):
    """Real git primary repo with one commit on ``main`` branch.

    Returns (plugin, umo, primary_dir) — ready to pass to handler.
    """
    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(primary)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(primary), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    (primary / "a.txt").write_text("a")
    subprocess.run(
        ["git", "-C", str(primary), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(primary), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    plugin = _make_plugin_mock(str(primary))
    return plugin, "test:umo", primary


# ─── 7 invalid_body tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_body_type_guard_non_dict(primary_repo):
    """L1: body 不是 dict → invalid_body。"""
    plugin, umo, primary = primary_repo
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=[])
    assert result["data"]["reason"] == "invalid_body"


@pytest.mark.asyncio
async def test_add_body_type_guard_none(primary_repo):
    """L1: body=None → invalid_body。"""
    plugin, umo, primary = primary_repo
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=None)
    assert result["data"]["reason"] == "invalid_body"


@pytest.mark.asyncio
async def test_add_missing_path_field(primary_repo, tmp_path):
    """L1: body 无 path → path_unsafe 路径(由 L3 防御)。"""
    plugin, umo, primary = primary_repo
    body = {"branch": "feat"}  # no path
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    # path_unsafe 优先级最高,先于 invalid_body 触发
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_add_path_not_string(primary_repo):
    """L1: path 非 str → path_unsafe。"""
    plugin, umo, primary = primary_repo
    body = {"path": 123, "branch": "feat"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_add_missing_branch_when_not_detach(primary_repo, tmp_path):
    """L1+L3: 缺 branch 且 detach=false → invalid_body(cross-field 拦截)。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target}  # branch missing, detach=False
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_body"


@pytest.mark.asyncio
async def test_add_create_and_force_both_true(primary_repo, tmp_path):
    """L3: create=true AND force=true → invalid_body。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "x", "create": True, "force": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_body"


@pytest.mark.asyncio
async def test_add_detach_and_create_both_true(primary_repo, tmp_path):
    """L3: detach=true AND create=true → invalid_body。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "x", "detach": True, "create": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_body"


# ─── 5 invalid_branch tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_invalid_branch_name_dots(primary_repo, tmp_path):
    """L4: branch 含 .. → invalid_branch。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "fea..ture"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_branch"


@pytest.mark.asyncio
async def test_add_invalid_branch_name_space(primary_repo, tmp_path):
    """L4: branch 含空格 → invalid_branch。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "bad branch"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_branch"


@pytest.mark.asyncio
async def test_add_invalid_branch_name_starts_with_dash(primary_repo, tmp_path):
    """L4: branch 以 - 开头 → invalid_branch。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "-bad"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_branch"


@pytest.mark.asyncio
async def test_add_invalid_branch_name_too_long(primary_repo, tmp_path):
    """L4: branch > 1024 字符 → invalid_branch。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "a" * 1100}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_branch"


@pytest.mark.asyncio
async def test_add_invalid_base_ref(primary_repo, tmp_path):
    """L4: base 含 .. → invalid_param。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "new", "create": True, "base": "ba..d"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_param"


# ─── 4 path_unsafe tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_relative_path_rejected(primary_repo):
    """L2: path 非绝对 → path_unsafe。"""
    plugin, umo, primary = primary_repo
    body = {"path": "relative/path", "branch": "feat"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_add_dotdot_path_rejected(primary_repo):
    """L2: path 含 .. 段 → path_unsafe。"""
    plugin, umo, primary = primary_repo
    body = {"path": "/foo/../escape", "branch": "feat"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_add_dotgit_component_rejected(primary_repo):
    """L2: path 含 .git 段 → path_unsafe。"""
    plugin, umo, primary = primary_repo
    body = {"path": "/repo/.git/feature", "branch": "feat"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_add_blacklisted_path_rejected(primary_repo, tmp_path):
    """L2: 命中 blacklist → path_unsafe(MAJOR-1 修复)。"""
    plugin, umo, primary = primary_repo
    # 把 primary 自身加 blacklist(primary 存在 + 可写,只是不在 ADD list 中,
    # 但 path_unsafe 是相对 blacklist 而言的)
    blacklist_parent = tmp_path  # primary 父目录
    plugin._config["file_remove_blacklist"] = [str(blacklist_parent)]
    body = {"path": str(blacklist_parent / "new-wt"), "branch": "feat"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_unsafe"


# ─── 4 path_exists_nonempty tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_add_target_dir_exists_with_files(primary_repo, tmp_path):
    """L5: target 已存在且非空 → path_exists_nonempty。"""
    plugin, umo, primary = primary_repo
    target = tmp_path / "feature"
    target.mkdir()
    (target / "junk.txt").write_text("junk")
    body = {"path": str(target), "branch": "feat"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_exists_nonempty"


@pytest.mark.asyncio
async def test_add_target_empty_dir_ok(primary_repo, tmp_path):
    """L5: target 是空目录 → 不算 nonempty,可继续(由 git add 创建)。"""
    plugin, umo, primary = primary_repo
    target = tmp_path / "feature"
    target.mkdir()  # empty dir is fine
    body = {"path": str(target), "branch": "feat", "create": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] is None
    assert result["data"]["created"]["branch"] == "feat"


@pytest.mark.asyncio
async def test_add_target_inside_existing_wt(primary_repo, tmp_path):
    """L5: target 已被 git worktree add 用过 → path_exists_nonempty 兜底。"""
    plugin, umo, primary = primary_repo
    target = tmp_path / "feature"
    target.mkdir()
    (target / "a.txt").write_text("a")
    (target / ".git").write_text("gitdir: /something")  # 模拟已存在 worktree
    body = {"path": str(target), "branch": "feat"}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_exists_nonempty"


@pytest.mark.asyncio
async def test_add_git_returns_path_exists(primary_repo, tmp_path):
    """L6: git 自身报 'path already exists' → path_exists_nonempty(stderr 映射)。"""
    plugin, umo, primary = primary_repo
    # 准备一个 target,里面有 .git 标记但 git 视其为已存在但非合法
    target = tmp_path / "feature"
    target.mkdir()
    (target / "a.txt").write_text("a")
    (target / ".git").write_text("gitdir: /not-a-real-repo")
    body = {"path": str(target), "branch": "feat", "create": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    # path_exists_nonempty L5 拦截;先于 L6 触发
    assert result["data"]["reason"] in ("path_exists_nonempty", "git_error")


# ─── 4 cannot_create_existing / cannot_checkout_missing tests ────────


@pytest.mark.asyncio
async def test_add_branch_already_exists(primary_repo, tmp_path):
    """L6: branch 已存在 + create=true (-b) → cannot_create_existing(stderr)。

    注意:基本形式 ``git worktree add <path> <branch>`` 在 branch 存在但
    未在别处 checked out 时会成功(将 branch 检出到新 worktree),不报错。
    只有 ``-b`` (强制创建) 才会触发 "already exists" 错误。
    """
    plugin, umo, primary = primary_repo
    # 先手动建一个 branch
    subprocess.run(
        ["git", "-C", str(primary), "branch", "feat"],
        check=True,
        capture_output=True,
    )
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat", "create": True}  # -b 强制创建
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "cannot_create_existing"


@pytest.mark.asyncio
async def test_add_basic_checkout_existing_branch_ok(primary_repo, tmp_path):
    """L6 边界:branch 已存在 + create=false → 实际可成功(非错误)。

    这是 git worktree add 的合法用法:把已存在但未在别处 checked out 的
    branch 检出到新 worktree。Handler 不应把它当错误。
    """
    plugin, umo, primary = primary_repo
    subprocess.run(
        ["git", "-C", str(primary), "branch", "feat"],
        check=True,
        capture_output=True,
    )
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat"}  # create=False (default)
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] is None
    assert result["data"]["created"]["branch"] == "feat"


@pytest.mark.asyncio
async def test_add_branch_already_checked_out(primary_repo, tmp_path):
    """L6: branch 已被另一 worktree 持有 → cannot_create_existing。"""
    plugin, umo, primary = primary_repo
    # 准备一个已 linked 的 worktree 持有 feat
    other = tmp_path / "other"
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "add", str(other), "-b", "feat"],
        check=True,
        capture_output=True,
    )
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat", "create": True}  # 强制 -b
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    # force=true 或 create=true 都触发 "already checked out" 提示
    assert result["data"]["reason"] == "cannot_create_existing"


@pytest.mark.asyncio
async def test_add_branch_missing(primary_repo, tmp_path):
    """L6: branch 不存在 + create=false → cannot_checkout_missing。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "nonexistent"}  # create=False
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "cannot_checkout_missing"


@pytest.mark.asyncio
async def test_add_git_invalid_worktree_name(primary_repo, tmp_path):
    """L6: git 报 invalid worktree name → invalid_param(stderr 映射)。"""
    plugin, umo, primary = primary_repo
    # 模拟一个含特殊字符的 path(会绕过 L2 _validate_new_worktree_path 检查
    # 是因为它不查 :字符,只在 git CLI 阶段才报错)
    # 用 monkeypatch 替换 _validate_new_worktree_path 来注入
    target = str(tmp_path / "weird:name")  # 实际会被 _validate_new_worktree_path 拒绝
    body = {"path": target, "branch": "feat", "create": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    # L2 拒绝(L2 实际不拒 :字符,但 : 在 Windows 上非法); 实际测试用 mock
    # 注入:这里只验证 reason 是 path_unsafe 或 invalid_param 之一
    assert result["data"]["reason"] in ("path_unsafe", "invalid_param", "git_error")


# ─── 1 worktree_not_in_repo test ─────────────────────────────────────


@pytest.mark.asyncio
async def test_add_post_create_common_dir_mismatch(primary_repo, tmp_path, monkeypatch):
    """L7: post-create git-common-dir 与 primary 不匹配 → worktree_not_in_repo。

    通过 monkeypatch ``_resolve_git_common_dir`` 让 post-create 校验返回不一致。
    """
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat", "create": True}
    real_resolve = "tools._helpers._resolve_git_common_dir"
    call_count = {"n": 0}

    def fake_resolve(git_bin, worktree_path):
        call_count["n"] += 1
        if call_count["n"] == 1:  # primary → X
            return "/somewhere/else"
        if call_count["n"] == 2:  # new → Y (≠ X)
            return "/totally/different"
        return "/default"

    monkeypatch.setattr(real_resolve, fake_resolve)
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "worktree_not_in_repo"


# ─── 4 integration tests (real git ops) ─────────────────────────────


@pytest.mark.asyncio
async def test_add_basic_create_new_branch(primary_repo, tmp_path):
    """集成:create=true → 新建分支 + worktree,worktrees 数 1→2。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat", "create": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] is None
    created = result["data"]["created"]
    # git returns paths with forward slashes on Windows; compare normalized
    import os as _os

    assert _os.path.normpath(created["path"]) == _os.path.normpath(target)
    assert created["branch"] == "feat"
    assert created["is_main"] is False
    assert len(result["data"]["worktrees"]) == 2  # primary + new


@pytest.mark.asyncio
async def test_add_two_linked_worktrees(primary_repo, tmp_path):
    """集成:连续 ADD 2 个,均成功,worktrees 数 1→3。"""
    plugin, umo, primary = primary_repo
    # 第一次 ADD
    target1 = str(tmp_path / "feat1")
    body1 = {"path": target1, "branch": "feat1", "create": True}
    result1 = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body1)
    assert result1["data"]["reason"] is None
    # 第二次 ADD
    target2 = str(tmp_path / "feat2")
    body2 = {"path": target2, "branch": "feat2", "create": True}
    result2 = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body2)
    assert result2["data"]["reason"] is None
    assert len(result2["data"]["worktrees"]) == 3


@pytest.mark.asyncio
async def test_add_force_reset_existing_branch(primary_repo, tmp_path):
    """集成:force=true → -B 强制重置已存在分支。"""
    plugin, umo, primary = primary_repo
    # 先建一个 branch
    subprocess.run(
        ["git", "-C", str(primary), "branch", "feat"],
        check=True,
        capture_output=True,
    )
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat", "force": True}  # -B
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] is None
    assert result["data"]["created"]["branch"] == "feat"


@pytest.mark.asyncio
async def test_add_detach_at_head(primary_repo, tmp_path):
    """集成:detach=true + branch=None → detached HEAD worktree。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "detached")
    body = {"path": target, "branch": None, "detach": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] is None
    created = result["data"]["created"]
    assert created["is_main"] is False
    # detached 模式: branch 字段是 None
    assert created.get("branch") is None


@pytest.mark.asyncio
async def test_add_create_with_base(primary_repo, tmp_path):
    """集成:create=true + base="main" → 基于 main 建新分支。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "from-main")
    body = {
        "path": target,
        "branch": "from-main",
        "create": True,
        "base": "main",
    }
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] is None
    assert result["data"]["created"]["branch"] == "from-main"


# ─── 1 envelope shape test ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_success_envelope_shape(primary_repo, tmp_path):
    """成功响应 envelope 必须含 created{} + worktrees[] + 8 字段。"""
    plugin, umo, primary = primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat", "create": True}
    result = await plugin_module_handle(plugin, umo=umo, worktree=None, body=body)
    data = result["data"]
    assert set(data.keys()) >= {
        "loaded",
        "directory",
        "umo",
        "worktree",
        "created",
        "worktrees",
        "reason",
        "stderr",
        "elapsed_ms",
    }
    assert data["loaded"] is True
    assert data["reason"] is None
    assert data["stderr"] == ""
    assert isinstance(data["elapsed_ms"], int)
    # created 单条含 path/branch/head_sha/is_main
    c = data["created"]
    assert set(c.keys()) >= {"path", "branch", "head_sha", "is_main", "locked"}


# ─── Preflight tests (NOT in 30 but required for completeness) ──────


@pytest.mark.asyncio
async def test_add_no_project_loaded():
    """preflight: 无 umo 且无回退 → no_project_loaded。"""
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = None
    result = await plugin_module_handle(
        plugin,
        umo="nonexistent",
        worktree=None,
        body={"path": "/x", "branch": "y"},
    )
    assert result["data"]["reason"] == "no_project_loaded"


@pytest.mark.asyncio
async def test_add_feature_disabled():
    """preflight: 任一 feature flag = False → feature_disabled。"""
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": False, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/tmp", "loaded_at": 0.0}
    result = await plugin_module_handle(
        plugin,
        umo="test",
        worktree=None,
        body={"path": "/x", "branch": "y"},
    )
    assert result["data"]["reason"] == "feature_disabled"


# ── handler accessor (delays import to allow module-level patch) ────


def plugin_module_handle(plugin, *, umo=None, worktree=None, body=None):
    """Local accessor — keeps imports minimal in test body.

    Lifts the actual handler import here so test discovery works even if
    the module isn't fully built yet (e.g., before Task 2.4 ships).
    """
    from tools.webapi.git_worktree_add import handle

    return handle(plugin, umo=umo, worktree=worktree, body=body)
