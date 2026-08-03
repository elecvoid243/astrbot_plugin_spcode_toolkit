"""POST /spcode/git-squash — 压缩 HEAD 起连续 N 条 commit 为一条。

Spec: docs/superpowers/specs/2026-08-03-git-squash-design.md (Astrbot 仓库)
Author: elecvoid243 @ 2026-08-03

策略(spec decision #5): ``git reset --soft <oldest>^`` + ``git commit -F -``。
不调 rebase —— 零冲突、无 in-progress 状态机、跨平台 100% 兼容。

连续性权威校验(spec decision #6): ``git rev-list HEAD -n <count>`` 的结果
集合必须与选区集合完全相等(隐含"含 HEAD 且连续")。

复用 ``tools/webapi/git_commit.py``:
- ``_build_git_env()``:透传 ``GIT_AUTHOR_*`` 环境变量
- ``_classify_commit_error()``:hook / identity / nothing / git_error 分类
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .git_commit import _build_git_env, _classify_commit_error

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

COMMIT_TRUNCATE_BYTES = 4096
MAX_MESSAGE_LENGTH = 8192  # 与 git_commit.py 一致
MAX_SQUASH_COMMITS = 100  # 防御性上限


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-squash handler.

    Body (JSON): ``{"commits": ["<sha>", ...], "message": "..."}``。
    commits ≥ 2 条(可乱序、可 short sha);message 非空。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            squashed=False,
        )

    commits_raw = body.get("commits")
    message = body.get("message")

    if (
        not isinstance(commits_raw, list)
        or len(commits_raw) < 2
        or len(commits_raw) > MAX_SQUASH_COMMITS
        or any(not isinstance(c, str) or not c.strip() for c in commits_raw)
    ):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            squashed=False,
            stderr="commits must be a list of 2..100 non-empty sha strings",
        )
    if (
        not isinstance(message, str)
        or not message.strip()
        or len(message) > MAX_MESSAGE_LENGTH
    ):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_MESSAGE,
            elapsed_ms=_elapsed(),
            squashed=False,
            stderr="message must be a non-empty string (<= 8192 chars)",
        )

    commits = [c.strip() for c in commits_raw]

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("squashed", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    def _fail(reason: str, stderr: str = "") -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            squashed=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:COMMIT_TRUNCATE_BYTES],
        )

    # ── 3. 已有 merge/cherry-pick/revert 冲突进行中 → 拒绝 ──
    in_progress = await _detect_conflict_operation(git_bin, directory)
    if in_progress is not None:
        return _fail(
            ReasonCode.OPERATION_IN_PROGRESS,
            f"{in_progress} already in progress",
        )

    # ── 4. 工作区必须干净(spec decision #3) ──
    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
    )
    if not status_result.get("ok"):
        stderr_msg = status_result.get("stderr", "") or status_result.get("error", "")
        if "does not have any commits" in stderr_msg or "no commits" in stderr_msg:
            return _fail(ReasonCode.EMPTY_REPOSITORY, stderr_msg)
        return _fail(ReasonCode.GIT_ERROR, stderr_msg)
    if status_result["stdout"].strip():
        return _fail(ReasonCode.WORKTREE_DIRTY, "working tree has uncommitted changes")

    # ── 5. 逐个解析选区 sha → 完整 commit SHA(short sha / tag 兼容) ──
    resolved: list[str] = []
    for c in commits:
        r = await _run_git_async(
            [
                git_bin,
                "-C",
                directory,
                "rev-parse",
                "--verify",
                f"{c}^{{commit}}",
            ],
            encoding="utf-8",
        )
        if not r.get("ok"):
            return _fail(
                ReasonCode.COMMIT_NOT_FOUND,
                f"cannot resolve commit: {c}\n{r.get('stderr', '')}",
            )
        resolved.append(r["stdout"].strip())
    # 去重(保序);去重后不足 2 条 → invalid_param
    resolved = list(dict.fromkeys(resolved))
    if len(resolved) < 2:
        return _fail(ReasonCode.INVALID_PARAM, "commits contain duplicate entries")

    # ── 6. 记录旧 HEAD(响应字段 + 排障) ──
    head_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
    )
    if not head_result.get("ok"):
        return _fail(ReasonCode.GIT_ERROR, head_result.get("stderr", ""))
    old_head_sha = head_result["stdout"].strip()

    # ── 7. 连续性权威校验:选区集合 == rev-list HEAD -n N ──
    revlist = await _run_git_async(
        [git_bin, "-C", directory, "rev-list", "HEAD", "-n", str(len(resolved))],
        encoding="utf-8",
    )
    if not revlist.get("ok"):
        return _fail(ReasonCode.NOT_CONTIGUOUS, revlist.get("stderr", ""))
    top_shas = [line.strip() for line in revlist["stdout"].splitlines() if line.strip()]
    if len(top_shas) < len(resolved):
        return _fail(
            ReasonCode.NOT_CONTIGUOUS,
            "repository has fewer commits than the selection",
        )
    selected_set = set(resolved)
    if old_head_sha not in selected_set:
        return _fail(
            ReasonCode.HEAD_NOT_SELECTED,
            "HEAD must be included in the selection",
        )
    if set(top_shas) != selected_set:
        return _fail(
            ReasonCode.NOT_CONTIGUOUS,
            "selected commits are not a contiguous range from HEAD",
        )
    oldest = top_shas[-1]  # rev-list 输出新→旧,最后一行即最老

    # ── 8. 最老一条必须有 parent(root commit 无法 reset --soft <oldest>^) ──
    parent = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", f"{oldest}^"],
        encoding="utf-8",
    )
    if not parent.get("ok"):
        return _fail(
            ReasonCode.ROOT_COMMIT, "squashing the root commit is not supported"
        )
    base = parent["stdout"].strip()

    # ── 9. reset --soft:HEAD 移回 oldest^,N 条 commit 的改动回 staged ──
    reset = await _run_git_async(
        [
            git_bin,
            "-C",
            directory,
            "-c",
            "color.ui=never",
            "reset",
            "--soft",
            base,
        ],
        encoding="utf-8",
        timeout=30.0,
    )
    if not reset.get("ok"):
        return _fail(ReasonCode.GIT_ERROR, reset.get("stderr", ""))

    # ── 10. commit(stdin 传 message,透传身份,不带 --no-verify) ──
    commit = await _run_git_async(
        [git_bin, "-C", directory, "-c", "color.ui=never", "commit", "-F", "-"],
        encoding="utf-8",
        input_text=message,
        env=_build_git_env(),
        timeout=30.0,
    )
    if not commit.get("ok"):
        stderr = commit.get("stderr", "") or commit.get("error", "")
        reason = _classify_commit_error(stderr, commit.get("code", 1))
        logger.info("git-squash: commit step failed (%s): %s", reason, stderr[:200])
        # 注意:此时 HEAD 已在 oldest^、改动在暂存区(spec §5 hook 失败中间态)
        return _fail(reason, stderr)

    # ── 11. 回读新 commit ──
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
    )
    new_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""

    msg_result = await _run_git_async(
        [git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"],
        encoding="utf-8",
    )
    new_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""

    files_result = await _run_git_async(
        [git_bin, "-C", directory, "show", "--name-only", "--pretty=", "HEAD"],
        encoding="utf-8",
    )
    files_touched = (
        [
            line.strip()
            for line in (files_result.get("stdout", "")).splitlines()
            if line.strip()
        ]
        if files_result.get("ok")
        else []
    )

    logger.info(
        "git-squash: %d commits → %s (umo=%s)",
        len(resolved),
        new_sha[:12],
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            squashed=True,
            new_sha=new_sha,
            message=new_message,
            squashed_count=len(resolved),
            old_head_sha=old_head_sha,
            files_touched=files_touched,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
