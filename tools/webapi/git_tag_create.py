"""POST /spcode/git-tag-create — 为指定 commit 创建轻量 tag。

主用途:提交对话框"顺便打 tag"——前端在 git-commit 成功后携带返回的
完整 SHA 作为 ``rev`` 调用本端点,把 tag 精确锚定到刚创建的 commit 上
(避免两次请求之间 HEAD 被推进导致 tag 打错位置)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .._helpers import _is_valid_ref_name
from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# tag 名单组件长度上限(refs/tags/ 下的文件名,常见文件系统 NAME_MAX=255)。
MAX_TAG_LENGTH = 256


def _classify_tag_error(stderr: str) -> str:
    """根据 stderr 文本对 git tag 失败进行分类。

    Args:
        stderr: git 子进程 stderr(原始大小写)。

    Returns:
        ReasonCode 字符串:``tag_already_exists`` / ``invalid_param``
        (tag 名不合法)/ ``ref_not_found``(rev 解析失败)/ ``git_error``。
    """
    s = stderr.lower()
    if "already exists" in s:
        return ReasonCode.TAG_ALREADY_EXISTS
    if "not a valid tag name" in s:
        return ReasonCode.INVALID_PARAM
    if "is not a valid" in s or "failed to resolve" in s or "unknown revision" in s:
        return ReasonCode.REF_NOT_FOUND
    return ReasonCode.GIT_ERROR


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-tag-create handler。

    Body (JSON, 必传): ``{"tag": "v1.0.0", "rev": "HEAD"}``。
    ``rev`` 可选,默认 HEAD;创建的是轻量 tag(不带 -a -m)。
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
            created=False,
            tag="",
            rev="HEAD",
        )

    tag = body.get("tag")
    rev = body.get("rev", "HEAD")

    if not isinstance(tag, str) or not tag.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            created=False,
            tag=str(tag or ""),
            rev=str(rev or "HEAD"),
        )
    if not isinstance(rev, str):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            created=False,
            tag=tag,
            rev="HEAD",
        )

    # ── 2. ref-format 校验 ──
    if len(tag) > MAX_TAG_LENGTH or not _is_valid_ref_name(tag):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            created=False,
            tag=tag,
            rev=rev,
        )
    if not _is_valid_ref_name(rev):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            created=False,
            tag=tag,
            rev=rev,
        )

    # ── 3. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("created", False)
        err["data"].setdefault("tag", tag)
        err["data"].setdefault("rev", rev)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 4. git tag(rev 默认 HEAD,git 自身缺省即 HEAD,省略传参) ──
    args: list[str] = [git_bin, "-C", directory, "-c", "color.ui=never", "tag", tag]
    if rev != "HEAD":
        args.append(rev)

    result = await _run_git_async(args, encoding="utf-8", timeout=15.0)
    if not result["ok"]:
        stderr = result.get("stderr", "")
        reason = _classify_tag_error(stderr)
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            created=False,
            tag=tag,
            rev=rev,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:4096],
        )

    # ── 5. 回读 SHA ──
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", f"{tag}^{{commit}}"],
        encoding="utf-8",
    )
    sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""

    logger.info("git-tag-create: %s (rev=%s)", tag, rev)
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            created=True,
            tag=tag,
            rev=rev,
            sha=sha,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
