# tools/webapi/git_worktrees.py
"""GET /spcode/git-worktrees — 列出当前已加载项目的 git worktree 列表。

Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.2
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from .. import worktree_activation as _wt_activation
from .._helpers import _parse_git_worktree_porcelain
from ..project import state as _proj_state
from ._helpers import _JSONResponseCompat, _run_git_async
from .file_browser import (
    _common_cache_headers,
    _get_if_none_match,
    _make_304_response,
)

logger = logging.getLogger(__name__)

# ── worktree 列表缓存 ──
# WHY: 一次会话切换里 dashboard 会连发两次 git-worktrees(项目状态 chip +
# git 侧栏), 5-10s 轮询也会重复跑 `git rev-parse` + `git worktree list`。
# worktree 列表只在显式 add/remove/lock 时变化, 1.5s TTL 与 git-status /
# git-diff 同一信任级别: 只合并突发请求, 不跨越轮询周期。
_WORKTREES_TTL: float = 1.5
_WORKTREES_CACHE_MAX = 64
_WORKTREES_CACHE: OrderedDict[str, tuple[list[dict], str, float]] = OrderedDict()

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
            "active_worktree": None,
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

    # 5. worktree 列表:1.5s TTL 缓存优先(命中则跳过两次 git 调用)
    now = _time.monotonic()
    cached = _WORKTREES_CACHE.get(directory)
    if cached is not None and (now - cached[2]) < _WORKTREES_TTL:
        _WORKTREES_CACHE.move_to_end(directory)
        worktrees, etag = cached[0], cached[1]
    else:
        # 6. Git 仓库探测
        git_bin = plugin._git_binary()
        # v3.10: 用真异步版本。run_cmd 是同步 subprocess.run，在 Hypercorn
        # 单事件循环里会阻塞整个进程（本机每次 git ~250ms，两次 ~500ms），
        # 期间所有响应体（/chat/configs、selection 等）都发不出去。
        probe = await _run_git_async(
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

        # 7. 列出 worktrees
        list_result = await _run_git_async(
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

        etag = (
            'W/"'
            + hashlib.sha1(
                f"{directory}\x00{list_result['stdout']}".encode()
            ).hexdigest()[:16]
            + '"'
        )
        _WORKTREES_CACHE[directory] = (worktrees, etag, now)
        while len(_WORKTREES_CACHE) > _WORKTREES_CACHE_MAX:
            _WORKTREES_CACHE.popitem(last=False)

    # 8. 解析激活 worktree(2026-08-20 worktree-activate)
    # 绑定校验:激活记录绑定的 directory 与当前 loaded project 不一致
    # (项目已切换) → 视为未激活;路径已不在当前 worktree 列表
    # (worktree 已被删除) → 同样视为未激活并顺手清理过期状态。
    activation = _wt_activation.get_for_directory(umo, directory)
    active_worktree = None
    if activation is not None:
        active_path = activation.get("path", "")
        if any(
            os.path.normcase(w.get("path", "")) == os.path.normcase(active_path)
            for w in worktrees
        ):
            active_worktree = active_path
        else:
            _wt_activation.pop(umo)

    elapsed = _elapsed()
    logger.debug(f"[git-worktrees] listed {len(worktrees)} worktrees in {elapsed}ms")
    cache_headers = _common_cache_headers(etag)
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)
    return _JSONResponseCompat(
        {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": directory,
                "umo": umo,
                "worktrees": worktrees,
                "active_worktree": active_worktree,
                "reason": None,
                "stderr": "",
                "elapsed_ms": elapsed,
            },
        },
        status_code=200,
        headers=cache_headers,
    )
