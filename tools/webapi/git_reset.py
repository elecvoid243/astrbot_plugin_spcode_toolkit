"""POST /spcode/git-reset — 重置当前分支到指定 commit。

Spec: docs/superpowers/specs/2026-09-09-git-reset-design.md
v2.27.0 (2026-09-09)。

SourceTree「Reset current branch to this commit」的 webapi 对应物:
``git reset --soft|--mixed|--hard <commit>``。三种模式都不产生新 commit、
不改写已有 commit 对象,只移动当前分支指针(+ 可选重置 index / 工作区)。

安全设计:
- ``ref`` 必须显式传入(无 HEAD 默认值)——避免 body 缺字段时意外把
  ``--hard`` 打到 HEAD 上丢弃全部工作区改动
- merge / revert / cherry-pick / rebase 进行中 → ``operation_in_progress``
  (``_detect_conflict_operation`` 前置拦截,不依赖 stderr 文案)
- ``ref`` 以 ``-`` 开头 → ``invalid_param``(防选项注入,与 git-log 同规则)
- 工作区不要求 clean:soft/mixed 的意义就是保留改动;hard 的破坏面由
  git 自身语义决定(tracked 改动全丢弃,挡路的 untracked 文件被目标
  内容静默覆盖)

复用 ``_helpers``:
- ``_git_endpoint_preflight()``:5 步共享前置校验
- ``_is_commit_ref()``(tools._helpers):ref 必须解析到 commit
- ``_classify_reset_stderr()``:失败兜底分类
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _classify_reset_stderr,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .._helpers import _is_commit_ref

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

STDERR_TRUNCATE_BYTES = 4096

VALID_MODES = ("soft", "mixed", "hard")


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-reset handler.

    Body (JSON): ``{"ref": "<sha>", "mode": "soft"|"mixed"|"hard"}``。
    ``ref`` 必填(前端固定传 log 行的完整 SHA);``mode`` 默认 ``"mixed"``
    (与 SourceTree 默认一致)。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _fail_data(**extra) -> dict:
        base = {
            "reset": False,
            "ref": str(body.get("ref", "")) if isinstance(body, dict) else "",
            "mode": str(body.get("mode", "")) if isinstance(body, dict) else "",
            "elapsed_ms": _elapsed(),
        }
        base.update(extra)
        return base

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            **_fail_data(),
        )

    ref = body.get("ref")
    mode = body.get("mode", "mixed")

    if not isinstance(ref, str) or not ref:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            stderr="ref is required (no HEAD default for a destructive op)",
            **_fail_data(),
        )
    # 防选项注入:ref 作为裸参数传给 git,以 - 开头可注入旗标。
    if ref.startswith("-"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            stderr="ref must not start with '-'",
            **_fail_data(),
        )
    if mode not in VALID_MODES:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            stderr=f"mode must be one of {VALID_MODES}",
            **_fail_data(),
        )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"].update(_fail_data())
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 校验 ref 是 commit,并解析为完整 SHA ──
    if not _is_commit_ref(git_bin, directory, ref):
        return _make_envelope(
            success=False,
            reason=ReasonCode.COMMIT_NOT_FOUND,
            stderr=f"ref does not resolve to a commit: {ref}",
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            **_fail_data(),
        )
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", f"{ref}^{{commit}}"],
        encoding="utf-8",
    )
    target_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""

    # ── 4. 冲突状态探测(merge/revert/cherry-pick/rebase 进行中拒绝) ──
    conflict_op = await _detect_conflict_operation(git_bin, directory)
    if conflict_op is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            stderr=f"a {conflict_op} operation is in progress",
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            **_fail_data(),
        )

    # ── 5. 记录重置前 HEAD(供前端提示回退了几个提交) ──
    before_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
    )
    before_sha = before_result["stdout"].strip() if before_result.get("ok") else ""

    # ── 6. git reset --<mode> <sha> ──
    args = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "reset",
        f"--{mode}",
        target_sha,
    ]
    result = await _run_git_async(
        args,
        encoding="utf-8",
        timeout=30.0,
    )

    if not result["ok"]:
        stderr = result.get("stderr", "") or result.get("error", "")
        reason = _classify_reset_stderr(stderr)
        logger.info(
            "git-reset: failed ref=%s mode=%s (%s): %s",
            ref,
            mode,
            reason,
            stderr[:200],
        )
        return _make_envelope(
            success=False,
            reason=reason,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:STDERR_TRUNCATE_BYTES],
            **_fail_data(),
        )

    logger.info(
        "git-reset: %s (%s) → %s (umo=%s)",
        before_sha[:12] or "?",
        mode,
        target_sha[:12],
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            reset=True,
            ref=ref,
            mode=mode,
            before_sha=before_sha,
            after_sha=target_sha,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
