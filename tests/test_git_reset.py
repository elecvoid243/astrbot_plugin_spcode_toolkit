"""Tests for POST /spcode/git-reset (v2.27.0).

Spec: docs/superpowers/specs/2026-09-09-git-reset-design.md
Author: elecvoid243 @ 2026-09-09

SourceTree「Reset current branch to this commit」对应:
``git reset --soft|--mixed|--hard <commit>``。测试骨架仿 test_git_revert.py。
"""

import asyncio
import subprocess


from tests.conftest import _make_plugin  # noqa: F401
from tools.project import state as _state
from tools.webapi._helpers import ReasonCode, _classify_reset_stderr
from tools.webapi import git_reset


def _run(coro):
    """Sync wrapper for async handler calls (asyncio.run, see test_git_revert)."""
    return asyncio.run(coro)


def _rev_parse(repo, ref="HEAD") -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _add_commit(repo, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", filename], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], check=True)
    return _rev_parse(repo)


# ── preflight (3 cases) ──


def test_reset_no_umo_loaded():
    plugin = _make_plugin()
    result = _run(git_reset.handle(plugin, body={"ref": "HEAD"}))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


def test_reset_not_a_git_repo(tmp_path):
    target = tmp_path / "notgit"
    target.mkdir()
    umo = "test:reset:nogit"
    _state.put(umo, {"directory": str(target), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(git_reset.handle(plugin, umo=umo, body={"ref": "HEAD"}))
        assert result["data"]["reason"] == ReasonCode.NOT_A_GIT_REPO
    finally:
        _state.pop(umo)


def test_reset_worktree_invalid(loaded_umo, existing_repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(
            plugin,
            umo=loaded_umo,
            worktree=str(other),
            body={"ref": "HEAD"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.WORKTREE_INVALID


# ── body 校验 (4 cases) ──


def test_reset_body_none(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_reset.handle(plugin, umo=loaded_umo, body=None))
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_reset_missing_ref_rejected(loaded_umo):
    """ref 缺失 → invalid_param(无 HEAD 默认值,防 --hard 误伤)。"""
    plugin = _make_plugin()
    result = _run(git_reset.handle(plugin, umo=loaded_umo, body={"mode": "hard"}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_reset_ref_leading_dash_rejected(loaded_umo):
    """ref 以 - 开头 → invalid_param(防选项注入)。"""
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(plugin, umo=loaded_umo, body={"ref": "--hard origin/main"})
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_reset_invalid_mode_rejected(loaded_umo):
    """mode 不在三档之内 → invalid_param。"""
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(
            plugin,
            umo=loaded_umo,
            body={"ref": "HEAD", "mode": "keep"},
        )
    )
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


# ── commit_not_found (2 cases) ──


def test_reset_ref_does_not_exist(loaded_umo):
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(plugin, umo=loaded_umo, body={"ref": "nonexistent-ref"})
    )
    assert result["data"]["reason"] == ReasonCode.COMMIT_NOT_FOUND


def test_reset_ref_is_blob(loaded_umo):
    """ref=文件路径(非 commit) → commit_not_found。"""
    plugin = _make_plugin()
    result = _run(git_reset.handle(plugin, umo=loaded_umo, body={"ref": "README.md"}))
    assert result["data"]["reason"] == ReasonCode.COMMIT_NOT_FOUND


# ── 冲突状态探测 (1 case) ──


def test_reset_merge_in_progress_rejected(loaded_umo, existing_repo):
    """.git/MERGE_HEAD 哨兵存在 → operation_in_progress。

    手工放置哨兵文件模拟 merge 进行中(_detect_conflict_operation 的
    判定依据),不真正跑一次会冲突的 merge。
    """
    sha = _rev_parse(existing_repo)
    (existing_repo / ".git" / "MERGE_HEAD").write_text(sha + "\n", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(plugin, umo=loaded_umo, body={"ref": sha, "mode": "soft"})
    )
    assert result["data"]["reason"] == ReasonCode.OPERATION_IN_PROGRESS


# ── happy path:三档模式 (4 cases) ──


def test_reset_mode_defaults_to_mixed(loaded_umo):
    """mode 缺省 → mixed(SourceTree 默认一致)。"""
    plugin = _make_plugin()
    result = _run(git_reset.handle(plugin, umo=loaded_umo, body={"ref": "HEAD"}))
    assert result["data"]["reset"] is True
    assert result["data"]["mode"] == "mixed"


def test_reset_soft_moves_commits_back_to_staged(loaded_umo, existing_repo):
    """reset --soft HEAD^ → 上一条 commit 的改动回到暂存区。"""
    sha_second = _add_commit(existing_repo, "soft.txt", "v1", "add soft.txt")
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(
            plugin,
            umo=loaded_umo,
            body={"ref": "HEAD^", "mode": "soft"},
        )
    )
    assert result["data"]["reset"] is True
    assert result["data"]["mode"] == "soft"
    assert result["data"]["before_sha"] == sha_second
    # HEAD 回到上一条
    assert _rev_parse(existing_repo) == result["data"]["after_sha"]
    assert _rev_parse(existing_repo) != sha_second
    # 改动在暂存区(porcelain 第一列非空)
    status = subprocess.run(
        ["git", "-C", str(existing_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert any(line.startswith(("A", "M")) for line in status.splitlines())


def test_reset_mixed_moves_commits_back_to_worktree(loaded_umo, existing_repo):
    """reset --mixed HEAD^ → 上一条 commit 的改动回到工作区(未暂存)。"""
    sha_second = _add_commit(existing_repo, "mixed.txt", "v1", "add mixed.txt")
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(
            plugin,
            umo=loaded_umo,
            body={"ref": "HEAD^", "mode": "mixed"},
        )
    )
    assert result["data"]["reset"] is True
    assert result["data"]["mode"] == "mixed"
    assert result["data"]["before_sha"] == sha_second
    # add 型 commit 被 mixed reset 后,新文件从 index 移除 → 变 untracked(??)
    status = subprocess.run(
        ["git", "-C", str(existing_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "??" in status


def test_reset_hard_discards_commits_and_changes(loaded_umo, existing_repo):
    """reset --hard <旧 sha> → commit 消失 + 工作区文件还原。"""
    _add_commit(existing_repo, "hard.txt", "v1", "add hard.txt")
    sha_first = _rev_parse(existing_repo, "HEAD^")
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(
            plugin,
            umo=loaded_umo,
            body={"ref": sha_first, "mode": "hard"},
        )
    )
    assert result["data"]["reset"] is True
    assert result["data"]["mode"] == "hard"
    assert result["data"]["after_sha"] == sha_first
    assert not (existing_repo / "hard.txt").exists()
    status = subprocess.run(
        ["git", "-C", str(existing_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status.strip() == ""


# ── hard 模式的 untracked 破坏性语义 (1 case) ──


def test_reset_hard_deletes_untracked_file_in_the_way(loaded_umo, existing_repo):
    """reset --hard 挡路 untracked 文件 → 成功且文件内容被覆盖。

    git-reset(1) --hard 语义:挡在目标 tree 写入路径上的 untracked 文件
    **静默覆盖,不拒绝**(与 checkout/merge 报 "would be overwritten"
    不同)。此测试钉死该破坏性行为。
    """
    _add_commit(existing_repo, "blocked.txt", "v1", "add blocked.txt")
    sha_with_file = _rev_parse(existing_repo)
    subprocess.run(
        ["git", "-C", str(existing_repo), "rm", "-q", "blocked.txt"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(existing_repo), "commit", "-q", "-m", "remove blocked.txt"],
        check=True,
    )
    # 现在 HEAD 不含 blocked.txt,工作区放一个同名 untracked 文件
    (existing_repo / "blocked.txt").write_text("untracked", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(
            plugin,
            umo=loaded_umo,
            body={"ref": sha_with_file, "mode": "hard"},
        )
    )
    assert result["data"]["reset"] is True
    assert _rev_parse(existing_repo) == sha_with_file
    # 实测语义:挡路 untracked 文件不被删除也不被拒绝,而是被 checkout
    # 的目标 blob **静默覆盖**(untracked 内容丢失,替换为 tracked 内容)。
    assert (existing_repo / "blocked.txt").read_text(encoding="utf-8") == "v1"


# ── 端到端 (1 case) ──


def test_reset_then_log_shows_fewer_commits(loaded_umo, existing_repo):
    """reset --hard 到上一条后,git log 少一条。"""
    _add_commit(existing_repo, "e2e.txt", "v1", "add e2e.txt")
    sha_first = _rev_parse(existing_repo, "HEAD^")
    plugin = _make_plugin()
    result = _run(
        git_reset.handle(
            plugin,
            umo=loaded_umo,
            body={"ref": sha_first, "mode": "hard"},
        )
    )
    assert result["data"]["reset"] is True
    log = subprocess.run(
        ["git", "-C", str(existing_repo), "log", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert len(log.stdout.strip().splitlines()) == 1


# ── stderr classifier (2 cases) ──


def test_classify_reset_middle_of_merge():
    stderr = "fatal: Cannot do a hard reset in the middle of a merge."
    assert _classify_reset_stderr(stderr) == ReasonCode.OPERATION_IN_PROGRESS


def test_classify_reset_fallback_git_error():
    assert _classify_reset_stderr("some unrelated failure") == ReasonCode.GIT_ERROR
