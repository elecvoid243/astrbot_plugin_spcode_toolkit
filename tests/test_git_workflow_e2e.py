"""End-to-end smoke test for the v3.7 git workflow loop.

This test exercises the **full closed loop** that an LLM agent would
execute when editing code through the Dashboard:

    1. /spcode/git-stage     →  ``git add`` specific files
    2. /spcode/git-commit    →  ``git commit`` with message
    3. /spcode/git-unstage   →  ``git reset HEAD`` (after re-staging)
    4. /spcode/git-stage     →  ``git add`` again
    5. /spcode/git-commit    →  (verify final state with ``git`` CLI)

Also covers:
- /spcode/git-stage with ``all=true``
- /spcode/git-unstage with ``all=true``
- /spcode/git-commit with pre-commit hook failure (error classification)

Note: ``git_log`` reads its params from ``web.request.query`` (AstrBot
framework dep); this e2e test verifies the write-loop + uses raw
``git`` CLI to verify history state, since ``git_log`` is unit-tested
separately in ``test_git_log.py``.

Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §5
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi import git_commit as _gc
from tools.webapi import git_stage as _gs
from tools.webapi import git_unstage as _gu

pytestmark = pytest.mark.asyncio


# ──────────────────────────────────────────────────────────────────────
# Fixtures & helpers
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def plugin():
    return _make_plugin()


def _git_log_count(repo: Path) -> int:
    """Raw ``git rev-list --count HEAD`` — verify history length."""
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(r.stdout.strip())


def _git_last_message(repo: Path) -> str:
    """Raw ``git log -1 --format=%B`` — verify last commit message."""
    r = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%B"],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.rstrip("\r\n")


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=path, check=True)


def _load_project(plugin: Any, umo: str, directory: str) -> None:
    _proj_state.put(umo, {"directory": directory, "loaded_at": time.time()})


# ──────────────────────────────────────────────────────────────────────
# Closed-loop e2e
# ──────────────────────────────────────────────────────────────────────


async def test_full_git_workflow_loop(plugin, tmp_path: Path):
    """Stage → commit → unstage → diff: complete agent workflow."""
    _init_git_repo(tmp_path)
    (tmp_path / "feature.py").write_text("def f(): return 1\n", encoding="utf-8")
    (tmp_path / "test.py").write_text("assert f() == 1\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    # 1. Stage specific files
    stage_result = await _gs.handle(
        plugin,
        umo="u:m",
        body={"files": ["feature.py", "test.py"]},
    )
    assert stage_result["data"]["staged"] is True
    assert stage_result["data"]["staged_count"] == 2

    # 2. Verify history still has only "init" (sanity)
    assert _git_log_count(tmp_path) == 1

    # 3. Commit with multi-line message
    msg = "feat: add feature + test\n\n- implement f()\n- assert correctness"
    commit_result = await _gc.handle(plugin, umo="u:m", body={"message": msg})
    assert commit_result["data"]["committed"] is True
    sha = commit_result["data"]["sha"]
    assert len(sha) == 40
    assert commit_result["data"]["committed_count"] == 2

    # 4. Verify history now has 2 commits AND new commit message preserved
    assert _git_log_count(tmp_path) == 2
    assert _git_last_message(tmp_path) == msg

    # 5. Make another change + stage all
    (tmp_path / "extra.py").write_text("x = 42\n", encoding="utf-8")
    stage_all = await _gs.handle(plugin, umo="u:m", body={"all": True})
    assert stage_all["data"]["staged"] is True
    assert "extra.py" in stage_all["data"]["files"]

    # 6. Unstage all
    unstage_all = await _gu.handle(plugin, umo="u:m", body={"all": True})
    assert unstage_all["data"]["unstaged"] is True
    assert unstage_all["data"]["staged_count"] == 0

    # 7. Verify extra.py is no longer in index (git status shows it as untracked
    #    since it was never committed, and reset HEAD brings it back out of index)
    status_result = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    # untracked files show with "??" prefix
    assert "?? extra.py" in status_result.stdout


async def test_stage_specific_then_unstage_specific(plugin, tmp_path: Path):
    """Stage 3 files then unstage 1: only 2 should remain staged."""
    _init_git_repo(tmp_path)
    files = ["a.py", "b.py", "c.py"]
    for f in files:
        (tmp_path / f).write_text(f"# {f}\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    # Stage all 3
    s1 = await _gs.handle(plugin, umo="u:m", body={"files": files})
    assert s1["data"]["staged_count"] == 3

    # Unstage just a.py
    u1 = await _gu.handle(plugin, umo="u:m", body={"files": ["a.py"]})
    assert u1["data"]["unstaged"] is True
    assert u1["data"]["staged_count"] == 2
    assert sorted(u1["data"]["files"]) == ["b.py", "c.py"]


async def test_commit_message_preserved_through_log(plugin, tmp_path: Path):
    """Message written via /git-commit appears verbatim in raw git log."""
    _init_git_repo(tmp_path)
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    msg = "fix: handle edge case\n\n- bug #123\n- regression test added"
    await _gs.handle(plugin, umo="u:m", body={"files": ["x.py"]})
    await _gc.handle(plugin, umo="u:m", body={"message": msg})

    assert _git_log_count(tmp_path) == 2
    assert _git_last_message(tmp_path) == msg


async def test_commit_with_pre_commit_hook_failure(plugin, tmp_path: Path):
    """Hook rejection: commit returns ``hook_rejected`` and no new commit."""
    _init_git_repo(tmp_path)
    (tmp_path / "blocked.py").write_text("x = 1\n", encoding="utf-8")
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\necho 'pre-commit hook failed: lint error' >&2\nexit 1\n",
        encoding="utf-8",
    )
    _load_project(plugin, "u:m", str(tmp_path))

    await _gs.handle(plugin, umo="u:m", body={"files": ["blocked.py"]})

    result = await _gc.handle(plugin, umo="u:m", body={"message": "should fail"})
    assert result["data"]["committed"] is False
    assert result["data"]["reason"] == "hook_rejected"

    # git log should still show only "init"
    assert _git_log_count(tmp_path) == 1


async def test_unstage_then_restore_via_stage(plugin, tmp_path: Path):
    """Unstage → stage again → commit: full undo + redo."""
    _init_git_repo(tmp_path)
    (tmp_path / "y.py").write_text("y = 2\n", encoding="utf-8")
    _load_project(plugin, "u:m", str(tmp_path))

    # Stage then unstage
    await _gs.handle(plugin, umo="u:m", body={"files": ["y.py"]})
    u = await _gu.handle(plugin, umo="u:m", body={"files": ["y.py"]})
    assert u["data"]["staged_count"] == 0

    # Restage and commit
    await _gs.handle(plugin, umo="u:m", body={"files": ["y.py"]})
    c = await _gc.handle(plugin, umo="u:m", body={"message": "stage again"})
    assert c["data"]["committed"] is True

    assert _git_log_count(tmp_path) == 2
    assert _git_last_message(tmp_path) == "stage again"
