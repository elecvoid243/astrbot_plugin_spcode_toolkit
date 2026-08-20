"""POST /spcode/worktree-activate — 激活/取消激活 worktree(2026-08-20)。

激活后,on_llm_request 钩子(main.py ``_worktree_activation_inject``)把
激活 worktree 信息以 ``extra_user_content_parts``(TextPart.mark_as_temp)
注入每次 LLM 请求,让 LLM 知道要在哪个 worktree 上工作。
``body.path`` 为空/缺省 → 取消激活(清除状态)。

4 层防御链(仿照 git-worktree-lock):
  L1: body type guard (non-dict → invalid_body)
  L2: _git_endpoint_preflight (feature flags + umo 回退 + 目录 + git 仓库)
  L3: path 非空时 _resolve_target_worktree (格式 + worktree 列表查证
      → path_unsafe / worktree_not_found;main worktree 允许激活)
  L4: 写 tools.worktree_activation 状态 + 刷新 worktree 列表回包
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _make_envelope,
)
from .._helpers import (
    _list_worktrees_safe,
    _resolve_target_worktree,
)
from .. import worktree_activation as _activation

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/worktree-activate handler.

    Body: {"path": "/abs/path/to/worktree"} 激活;
          {"path": null} 或缺省 → 取消激活。

    Note: 激活对 main worktree 同样合法(语义是"让 LLM 工作在主工作树"),
    与 REMOVE 的硬禁 main 策略不同。

    Returns:
        统一 envelope;成功时 ``active_worktree`` 为激活路径(取消激活为
        None),``worktrees`` 为刷新后的完整列表(前端原子换 state)。
    """
    # ── L1: body type guard ─────────────────────────────────────────
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason="invalid_body",
            elapsed_ms=0,
            loaded=False,
            directory="",
            umo=umo,
            worktree="",
            active_worktree=None,
            stderr=f"body must be a dict, got {type(body).__name__}",
        )
    body = body or {}

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── L2: preflight (feature flags + umo 回退 + 目录 + git 仓库) ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        err["data"].setdefault("active_worktree", None)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    raw_path = body.get("path")

    # ── 取消激活分支:path 空/缺省 ──────────────────────────────────
    if raw_path is None or not str(raw_path).strip():
        _activation.pop(effective_umo)
        worktrees = await _list_worktrees_safe(git_bin, directory)
        logger.debug(f"[worktree-activate] 已取消会话 {effective_umo} 的激活")
        return _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory,
            umo=effective_umo,
            worktree="",
            active_worktree=None,
            worktrees=worktrees,
        )

    # ── L3: 格式 + worktree 列表查证 ───────────────────────────────
    target_wt, lookup_err = _resolve_target_worktree(
        git_bin,
        directory,
        str(raw_path),
    )
    if lookup_err == "path_unsafe":
        return _make_envelope(
            success=False,
            reason="path_unsafe",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree="",
            active_worktree=None,
            stderr=f"path validation failed: {raw_path!r}",
        )
    if lookup_err is not None or target_wt is None:
        return _make_envelope(
            success=False,
            reason="worktree_not_found",
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree="",
            active_worktree=None,
            stderr=f"path not in worktree list: {raw_path!r}",
        )

    # ── L4: 写激活状态 + 刷新 worktree 列表回包 ────────────────────
    _activation.put(
        effective_umo,
        path=target_wt["path"],
        branch=target_wt.get("branch"),
        directory=directory,
    )
    worktrees = await _list_worktrees_safe(git_bin, directory)
    logger.debug(
        f"[worktree-activate] 会话 {effective_umo} 激活 worktree: {target_wt['path']}"
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=target_wt["path"],
        active_worktree=target_wt["path"],
        worktrees=worktrees,
    )
