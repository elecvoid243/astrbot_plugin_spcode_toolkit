"""端到端 smoke test:模拟 dashboard 调用,验证 shortstat 对齐修复。

不在 conftest auto-reset 范围(单文件即可),用真实 git repo + merge commit
跑整套 handler,人工目视检查输出是否对齐。

运行方式:``pytest tests/test_git_log_smoke_e2e.py -v -s``
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import _make_plugin, make_web_request_mock
from tools.project import state as _proj_state
from tools.webapi import git_log as _gl


def _init_smoke_repo(path: Path) -> tuple[str, str]:
    """Init repo: 2 main commits + 1 feat + 1 main-extend + 1 merge."""
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    ib = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    (path / "a.txt").write_text("a", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: add a.txt", "-q"], cwd=path, check=True
    )

    (path / "a.txt").write_text("a\nb\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: extend a.txt", "-q"],
        cwd=path,
        check=True,
    )

    subprocess.run(["git", "checkout", "-q", "-b", "feat"], cwd=path, check=True)
    (path / "feat.txt").write_text("f1\nf2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat: new file", "-q"],
        cwd=path,
        check=True,
    )
    feat_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    subprocess.run(["git", "checkout", "-q", ib], cwd=path, check=True)
    (path / "b.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "main: add b.txt", "-q"],
        cwd=path,
        check=True,
    )

    subprocess.run(
        ["git", "merge", "--no-ff", "feat", "-m", "Merge feat", "-q"],
        cwd=path,
        check=True,
    )
    merge_sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return merge_sha, feat_sha


@pytest.mark.asyncio
async def test_git_log_smoke_e2e(monkeypatch, tmp_path: Path, capsys):
    plugin = _make_plugin()
    """E2E:含 5 commit + 1 merge 的仓库,handler 返回值人类可读。"""
    merge_sha, feat_sha = _init_smoke_repo(tmp_path)
    _proj_state.put("u:smoke", {"directory": str(tmp_path), "loaded_at": 0})

    from astrbot.api import web

    with patch.object(web, "request", make_web_request_mock(query={"n": "20"})):
        result = await _gl.handle(plugin, umo="u:smoke")

    commits = result["data"]["commits"]
    print()
    print("=" * 80)
    print(f"Total commits: {len(commits)} (expect 5)")
    print(f"{'SHA':<10} {'SUBJECT':<28} {'SHORTSTAT'}")
    print("-" * 80)
    for c in commits:
        ss = c["shortstat"]
        marker = "  [MERGE]" if "Merge" in c["subject"] else ""
        print(
            f"{c['sha_short']:<10} {c['subject'][:27]:<28} "
            f"f={ss['files']} +{ss['additions']} -{ss['deletions']}{marker}"
        )
    print("=" * 80)

    # 关键不变量:non-merge commits 不应有全 0 stat
    for c in commits:
        if "Merge" in c["subject"]:
            continue
        ss = c["shortstat"]
        assert ss != {"files": 0, "additions": 0, "deletions": 0}, (
            f"non-merge commit {c['sha_short']} ({c['subject']}) "
            f"has all-zero shortstat — alignment regression: {ss}"
        )

    assert len(commits) == 5
