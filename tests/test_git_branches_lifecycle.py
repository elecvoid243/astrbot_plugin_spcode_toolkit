"""E2E lifecycle test for v2.17.0 git-init/branch/revert (1 large test).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §6.4
Author: elecvoid243 @ 2026-07-16
"""

import asyncio
import subprocess
import time


from tests.conftest import _make_plugin  # noqa: F401
from tools.project import state as _state
from tools.webapi import (
    git_branch_create,
    git_branch_delete,
    git_branch_switch,
    git_branches,
    git_commit,
    git_init,
    git_revert,
)


def _run(coro):
    """Sync wrapper for async handler calls.

    使用 ``asyncio.run()`` 而非 ``asyncio.get_event_loop().run_until_complete()``:
    Python 3.10+ 主线程无 loop 时会触发 RuntimeError。
    """
    return asyncio.run(coro)


def test_init_to_revert_full_flow(tmp_path):
    """完整生命周期:init → branches → commit → create → switch → revert → delete。"""
    repo = tmp_path / "lifecycle_repo"
    repo.mkdir()
    umo = "test:lifecycle:1"
    plugin = _make_plugin()

    try:
        # 1. init (PR-B 不自动配置 git user.email/user.name)
        r = _run(
            git_init.handle(
                plugin, body={"path": str(repo), "initial_branch": "main"},
            )
        )
        assert r["data"]["initialized"] is True, f"init failed: {r}"

        # 显式配置 local git user identity(commit/revert 需要)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "lifecycle@test.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Lifecycle Tester"],
            check=True,
        )

        # 把新仓库注册到 state
        _state.put(umo, {"directory": str(repo), "loaded_at": time.time()})

        # 2. branches(空仓库:for-each-ref 应空)
        r = _run(git_branches.handle(plugin, umo=umo))
        # 空仓库:无任何 commit,branches 列表为空
        assert r["data"]["reason"] is None
        assert r["data"]["branches"] == [] or all(
            b.get("current") for b in r["data"]["branches"]
        )

        # 3. shell: 写 README + commit (绕过 umo plumbing 走 git_commit handler)
        (repo / "README.md").write_text("# Lifecycle")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        r = _run(git_commit.handle(plugin, umo=umo, body={"message": "init commit"}))
        assert r["data"]["committed"] is True, f"commit failed: {r}"

        # 4. create feature branch
        r = _run(
            git_branch_create.handle(
                plugin, umo=umo, body={"name": "feature/x"},
            )
        )
        assert r["data"]["created"] is True, f"create failed: {r}"

        # 5. switch to feature
        r = _run(
            git_branch_switch.handle(
                plugin, umo=umo, body={"name": "feature/x"},
            )
        )
        assert r["data"]["switched"] is True, f"switch failed: {r}"
        assert r["data"]["previous"] == "main"

        # 6. shell: 在 feature/x 改文件 + commit
        (repo / "feature.txt").write_text("feature work")
        subprocess.run(["git", "-C", str(repo), "add", "feature.txt"], check=True)
        r = _run(
            git_commit.handle(plugin, umo=umo, body={"message": "add feature"})
        )
        assert r["data"]["committed"] is True

        # 7. switch back to main
        r = _run(
            git_branch_switch.handle(plugin, umo=umo, body={"name": "main"})
        )
        assert r["data"]["switched"] is True
        assert r["data"]["previous"] == "feature/x"

        # 8. revert main 上次 commit(init commit)
        # 用 main 自己的 commit(而非 feature/x 的 SHA — non-ancestor revert 会失败)。
        main_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        r = _run(
            git_revert.handle(plugin, umo=umo, body={"ref": main_sha})
        )
        assert r["data"]["reverted"] is True, f"revert failed: {r}"
        assert r["data"]["revert_sha"] != main_sha

        # 9. delete feature branch (用 force 因为 revert 后可能变得未合并)
        r = _run(
            git_branch_delete.handle(
                plugin, umo=umo,
                body={"name": "feature/x", "force": True},
            )
        )
        assert r["data"]["deleted"] is True, f"delete failed: {r}"

        # 10. branches 验证
        r = _run(git_branches.handle(plugin, umo=umo))
        names = [b["name"] for b in r["data"]["branches"]]
        assert "main" in names
        assert "feature/x" not in names
    finally:
        if hasattr(_state, "pop"):
            try:
                _state.pop(umo)
            except KeyError:
                pass
        elif hasattr(_state, "clear"):
            try:
                _state.clear(umo)
            except KeyError:
                pass
