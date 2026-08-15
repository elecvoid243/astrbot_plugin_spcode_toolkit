"""POST /spcode/git-remote-remove — remove a configured Git remote.

2026-08-16 (elecvoid243): 供 Dashboard "设置 Remote URL" 对话框删除
已有远端。先确认远端存在 (``git remote get-url``), 再执行
``git remote remove``。成功响应附带剩余远端名列表, 前端可即时刷新
推送对话框的远端下拉 (与 git-remote-set-url 的 ``remotes`` 字段一致)。

Spec: docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._git_remote import (
    _classify_remote_error,
    _get_remote_url,
    _is_valid_remote_name,
)
from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-remote-remove handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            removed=False,
        )

    remote = body.get("remote")
    if not _is_valid_remote_name(remote):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_REMOTE,
            elapsed_ms=_elapsed(),
            removed=False,
            remote=str(remote or ""),
        )
    assert isinstance(remote, str)

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("removed", False)
        err["data"].setdefault("remote", remote)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    exists, _, probe_error = await _get_remote_url(git_bin, directory, remote)
    if not exists:
        return _make_envelope(
            success=False,
            reason=ReasonCode.REMOTE_NOT_FOUND,
            elapsed_ms=_elapsed(),
            removed=False,
            remote=remote,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=probe_error[:4096],
        )

    result = await _run_git_async(
        [git_bin, "-C", directory, "remote", "remove", remote],
        encoding="utf-8",
        timeout=10.0,
    )
    if not result.get("ok"):
        stderr = result.get("stderr", "") or result.get("error", "")
        reason = _classify_remote_error(stderr, result.get("stdout", ""))
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            removed=False,
            remote=remote,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:4096],
        )

    # 剩余远端名列表, 供前端即时刷新推送对话框下拉。
    remotes: list[str] = []
    list_result = await _run_git_async(
        [git_bin, "-C", directory, "remote"],
        encoding="utf-8",
        timeout=5.0,
    )
    if list_result.get("ok"):
        remotes = [
            line.strip()
            for line in (list_result.get("stdout") or "").splitlines()
            if line.strip()
        ]

    logger.info(
        "git-remote-remove: %s (remaining=%d, umo=%s)",
        remote,
        len(remotes),
        effective_umo,
    )
    return _make_envelope(
        success=True,
        reason=None,
        elapsed_ms=_elapsed(),
        removed=True,
        remote=remote,
        remotes=remotes,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
    )
