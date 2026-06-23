# tools/webapi/git_worktrees.py
"""GET /spcode/git-worktrees — 列出当前已加载项目的 git worktree 列表。

Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.2
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .._helpers import _parse_git_worktree_porcelain, run_cmd
from ..project import state as _proj_state

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from main import SPCodeToolkit


def _make_git_worktrees_empty_envelope(
    *,
    umo: str | None = None,
    directory: str | None = None,
    reason: str,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造 /spcode/git-worktrees 未载入 / 失败路径的响应骨架。

    Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.2
    """
    return {
        "status": "ok",
        "data": {
            "loaded": False,
            "directory": directory,
            "umo": umo,
            "worktrees": None,
            "reason": reason,
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
        },
    }


async def handle(
    plugin: SPCodeToolkit,
) -> dict:
    """Web API handler for ``GET /spcode/git-worktrees``.

    返回当前已加载项目主目录的 git worktree 列表。
    第一个 worktree 是主 worktree（is_main: true）,
    其余是同仓库的链接 worktree。

    Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.2
    """
    import time as _time

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # 1. umo 解析（与 git-diff handler 保持一致）
    umo: str | None = None
    try:
        from astrbot.api import web

        umo = web.request.query.get("umo") or None
    except Exception:
        umo = None

    # 2. 解析已加载项目
    # PR-7 (2026-06-23): 数据源从 ``plugin._loaded_projects`` 迁移到
    # ``tools.project.state`` 模块级单例 + ``plugin.get_loaded_project()``。
    if umo:
        info = plugin.get_loaded_project(umo)
    else:
        all_items = _proj_state.items()
        if not all_items:
            info = None
            umo = None
        else:
            # 退回到最近加载的项目。注意 ``state.items()`` 返回浅拷贝
            # (dict comprehension),所以不能 ``v is info`` 反查 key —
            # 直接从 max() 取出的元组中保留 umo。
            umo, info = max(
                all_items.items(),
                key=lambda kv: kv[1].get("loaded_at", 0.0),
            )

    if info is None:
        return _make_git_worktrees_empty_envelope(
            umo=umo, reason="no_project_loaded", elapsed_ms=_elapsed()
        )

    directory = info.get("directory", "")

    # 3. Feature flag 校验
    if not (
        plugin._config.get("agentsmd_enabled", True)
        and plugin._config.get("codegraph_enabled", True)
    ):
        return _make_git_worktrees_empty_envelope(
            umo=umo,
            directory=directory,
            reason="feature_disabled",
            elapsed_ms=_elapsed(),
        )

    # 4. 目录存在性
    if not Path(directory).is_dir():
        return _make_git_worktrees_empty_envelope(
            umo=umo,
            directory=directory,
            reason="directory_missing",
            elapsed_ms=_elapsed(),
        )

    # 5. Git 仓库探测
    git_bin = plugin._git_binary()
    probe = run_cmd(
        [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            return _make_git_worktrees_empty_envelope(
                umo=umo,
                directory=directory,
                reason="not_a_git_repo",
                elapsed_ms=_elapsed(),
            )
        if "未安装" in probe.get("error", ""):
            return _make_git_worktrees_empty_envelope(
                umo=umo,
                directory=directory,
                reason="git_unavailable",
                elapsed_ms=_elapsed(),
            )
        return _make_git_worktrees_empty_envelope(
            umo=umo,
            directory=directory,
            reason="git_error",
            stderr=probe.get("stderr", "") or probe.get("error", ""),
            elapsed_ms=_elapsed(),
        )

    # 6. 列出 worktrees
    list_result = run_cmd(
        [git_bin, "-C", directory, "worktree", "list", "--porcelain"],
        encoding="utf-8",
    )
    if not list_result["ok"]:
        return _make_git_worktrees_empty_envelope(
            umo=umo,
            directory=directory,
            reason="git_error",
            stderr=list_result.get("stderr", "") or list_result.get("error", ""),
            elapsed_ms=_elapsed(),
        )

    try:
        worktrees = _parse_git_worktree_porcelain(list_result["stdout"])
    except ValueError as e:
        logger.warning(f"[git-worktrees] porcelain parse failed: {e}")
        return _make_git_worktrees_empty_envelope(
            umo=umo,
            directory=directory,
            reason="git_error",
            stderr=str(e),
            elapsed_ms=_elapsed(),
        )

    elapsed = _elapsed()
    logger.debug(
        f"[git-worktrees] listed {len(worktrees)} worktrees in {elapsed}ms"
    )
    return {
        "status": "ok",
        "data": {
            "loaded": True,
            "directory": directory,
            "umo": umo,
            "worktrees": worktrees,
            "reason": None,
            "stderr": "",
            "elapsed_ms": elapsed,
        },
    }

