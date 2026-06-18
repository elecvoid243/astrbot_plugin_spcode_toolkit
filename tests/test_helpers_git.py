"""Tests for git-related helpers in tools/_helpers.py.

Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.3
Author: elecvoid243 @ 2026-06-18
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Match the sys.path pattern used by test_git_diff.py so we can import
# from tools._helpers in the package layout.
import sys
_PROJECT_PARENT = Path(__file__).resolve().parent.parent.parent  # F:\github
_PROJECT_DIR = Path(__file__).resolve().parent.parent  # F:\github\astrbot_plugin_spcode_toolkit
if str(_PROJECT_PARENT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_PARENT))
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from tools._helpers import _resolve_git_common_dir, _parse_git_worktree_porcelain  # noqa: E402


def _make_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "x@x"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "x"], check=True)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


@pytest.fixture
def two_repos():
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        a = _make_repo(parent, "repoA")
        b = _make_repo(parent, "repoB")
        yield a, b


# ─── _resolve_git_common_dir tests (Task 1.1) ───────────────────────────

def test_resolve_returns_absolute_path(two_repos):
    a, _ = two_repos
    result = _resolve_git_common_dir("git", str(a))
    assert os.path.isabs(result)


def test_resolve_different_repos_differ(two_repos):
    """CRITICAL: prevents cross-repo bypass (spec §2.3)."""
    a, b = two_repos
    assert _resolve_git_common_dir("git", str(a)) != _resolve_git_common_dir("git", str(b))


def test_resolve_same_repo_two_worktrees_match():
    """Two worktrees of the same repo share a common dir."""
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        main = _make_repo(parent, "main")
        wt = parent / "wt"
        subprocess.run(
            ["git", "-C", str(main), "worktree", "add", str(wt), "-b", "wt2"],
            check=True,
        )
        assert _resolve_git_common_dir("git", str(main)) == _resolve_git_common_dir(
            "git", str(wt)
        )


def test_resolve_case_insensitive_on_windows(two_repos):
    a, _ = two_repos
    # normcase on Windows lowercases; on macOS/Linux, case is preserved but
    # the test still passes because both sides see the same case.
    r1 = _resolve_git_common_dir("git", str(a))
    r2 = _resolve_git_common_dir("git", str(a).upper())
    assert r1 == r2


# ─── _parse_git_worktree_porcelain tests (Task 1.2) ─────────────────────

def test_parse_single_main_worktree():
    text = "worktree /r/main\nHEAD abc1234\nbranch refs/heads/main\n"
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 1
    assert result[0] == {
        "path": "/r/main",
        "branch": "main",
        "head_sha": "abc1234",
        "is_main": True,
    }


def test_parse_multiple_worktrees():
    text = (
        "worktree /r/main\n"
        "HEAD 1111111\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /r/feat\n"
        "HEAD 2222222\n"
        "branch refs/heads/feat/x\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 2
    assert result[0]["path"] == "/r/main"
    assert result[0]["is_main"] is True
    assert result[1]["path"] == "/r/feat"
    assert result[1]["branch"] == "feat/x"
    assert result[1]["is_main"] is False


def test_parse_detached_worktree():
    text = (
        "worktree /r/main\n"
        "HEAD 1111111\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /r/detached\n"
        "HEAD 2222222\n"
        "detached\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert result[1]["branch"] is None
    assert result[1]["is_main"] is False


def test_parse_empty_returns_empty_list():
    assert _parse_git_worktree_porcelain("") == []


def test_parse_malformed_raises():
    """Unrecognized records should raise rather than silently corrupt."""
    with pytest.raises(ValueError, match="Unknown porcelain record"):
        _parse_git_worktree_porcelain("worktree /r/main\nWAT abc\n")
