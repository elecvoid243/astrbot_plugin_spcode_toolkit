"""POST /spcode/git-commit — git commit(严格最小)。

Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §E
PR-5 of git workflow endpoints design.

**重要**:严格只接 ``message``,不支持 ``--amend`` / 签名 / 多 author 等扩展,
以最小化 L1 hook surface。错误分类:把 git commit 失败按 stderr 关键字符串
映射为 ReasonCode(``hook_rejected`` / ``identity_not_set`` / ``nothing_to_commit``
/ ``git_error``)。
"""

from __future__ import annotations
import logging
import os
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _JSONResponseCompat,
    _get_staged_files,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)

# git commit 作者/提交者身份的环境变量。
# 如果调用者(测试或集成方)有显式传入 GIT_AUTHOR_*,我们透传给 git 子进程。
_GIT_AUTHOR_ENV_KEYS = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
)


def _build_git_env() -> dict[str, str] | None:
    """从 os.environ 提取 GIT_AUTHOR_*/GIT_COMMITTER_* 子集。

    Returns:
        若任一变量被显式设置,返回该子集 dict(供 _run_git_async env= 用);
        否则返回 None(让 git 子进程继承默认 env)。
    """
    env_subset: dict[str, str] = {}
    for k in _GIT_AUTHOR_ENV_KEYS:
        if k in os.environ:
            env_subset[k] = os.environ[k]
    return env_subset or None


if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ── 端点常量 ──
MAX_MESSAGE_LENGTH = 8192  # 8 KB message 硬上限
COMMIT_TRUNCATE_BYTES = 4096  # stderr 截断字节数


def _classify_commit_error(stderr: str, returncode: int) -> str:
    """根据 stderr 文本对 git commit 失败进行分类。

    Returns:
        ReasonCode 字符串:
        - ``hook_rejected`` (pre-commit / commit-msg 失败)
        - ``identity_not_set`` (没有 user.name / user.email)
        - ``nothing_to_commit`` (没有 staged 改动)
        - ``git_error`` (其他 git 错误)
    """
    s = stderr.lower()
    # 1. Hook 拒绝 — 关键词覆盖 git 输出的常见变体
    if any(
        kw in s
        for kw in (
            "pre-commit hook",
            "commit-msg hook",
            "hook declined",
            "hook script",
            "hook failed",
            "rejected by hook",
        )
    ):
        return ReasonCode.HOOK_REJECTED

    # 2. Identity 未设置(commit author 无法决定)
    if any(
        kw in s
        for kw in (
            "please tell me who you are",
            "author identity unknown",
            "please set your name and email",
            "'git config user.email'",
            "empty ident name",
            "empty ident",
        )
    ):
        return ReasonCode.IDENTITY_NOT_SET

    # 3. Nothing to commit
    if any(
        kw in s
        for kw in (
            "nothing to commit",
            "no changes added to commit",
            "your branch is up to date",
        )
    ):
        return ReasonCode.NOTHING_TO_COMMIT

    return ReasonCode.GIT_ERROR


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-commit handler。

    Body (JSON, 必传): ``{"message": "..."}``。
    严格最小 — 不接 amend / signoff / author 等参数。

    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §E
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
            committed=False,
            sha="",
            files=[],
            committed_count=0,
            staged_count=0,
            umo=umo,
            worktree=worktree,
        )

    message = body.get("message")
    # ── 2. message 校验 ──
    if not isinstance(message, str):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_MESSAGE,
            elapsed_ms=_elapsed(),
            committed=False,
            sha="",
            files=[],
            committed_count=0,
            staged_count=0,
            umo=umo,
            worktree=worktree,
        )
    if len(message) == 0 or len(message) > MAX_MESSAGE_LENGTH:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_MESSAGE,
            elapsed_ms=_elapsed(),
            committed=False,
            sha="",
            files=[],
            committed_count=0,
            staged_count=0,
            umo=umo,
            worktree=worktree,
        )

    # ── 3. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("committed", False)
        err["data"].setdefault("sha", "")
        err["data"].setdefault("files", [])
        err["data"].setdefault("committed_count", 0)
        err["data"].setdefault("staged_count", 0)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # ── 4. 提前读取 staged 文件列表(commit 前 + commit 后) ──
    git_bin = plugin._git_binary()
    pre_staged = await _get_staged_files(git_bin, directory)
    if not pre_staged:
        # 0 个 staged → nothing_to_commit(早返回,避免触发 hook)
        return _make_envelope(
            success=False,
            reason=ReasonCode.NOTHING_TO_COMMIT,
            elapsed_ms=_elapsed(),
            committed=False,
            sha="",
            files=[],
            committed_count=0,
            staged_count=0,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 5. git commit ──
    # 使用 ``-F -`` 从 stdin 读取 message(支持多行)。
    # **不**带 ``--no-verify``:我们要把 hook 失败的 stderr 抓回来,
    # 由 ``_classify_commit_error`` 分类为 ``hook_rejected``。
    args = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "commit",
        "-F",
        "-",  # 从 stdin 读取 message
    ]

    git_env = _build_git_env()
    result = await _run_git_async(
        args,
        encoding="utf-8",
        input_text=message,
        env=git_env,
    )

    if not result["ok"]:
        stderr = result.get("stderr", "") or result.get("error", "")
        reason = _classify_commit_error(stderr, result.get("returncode", -1))
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            committed=False,
            sha="",
            files=pre_staged,
            committed_count=0,
            staged_count=len(pre_staged),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:COMMIT_TRUNCATE_BYTES],
        )

    # ── 6. 提取新 commit SHA ──
    # git commit stdout 形如 ``[master abc1234] message``,其中 ``abc1234`` 是
    # **短** SHA(7 字符)。必须 fallback 到 ``git rev-parse HEAD`` 获取完整 40 字符。
    sha = ""
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
    )
    if sha_result.get("ok") and sha_result.get("stdout"):
        sha = sha_result["stdout"].strip()

    # ── 7. 读取 commit 后 staged(应为 0) ──
    post_staged = await _get_staged_files(git_bin, directory)

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            committed=True,
            sha=sha,
            files=pre_staged,
            committed_count=len(pre_staged),
            staged_count=len(post_staged),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
