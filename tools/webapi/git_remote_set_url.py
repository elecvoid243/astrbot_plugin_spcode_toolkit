"""POST /spcode/git-remote-set-url — upsert a Git remote URL.

Spec: docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._git_remote import (
    _get_remote_url,
    _is_valid_remote_name,
    _is_valid_remote_url,
    _mask_remote_url,
)
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


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-remote-set-url handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            configured=False,
        )

    remote = body.get("remote", "origin")
    url = body.get("url")
    if not _is_valid_remote_name(remote):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_REMOTE,
            elapsed_ms=_elapsed(),
            configured=False,
            remote=str(remote or ""),
        )
    if not _is_valid_remote_url(url):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_URL,
            elapsed_ms=_elapsed(),
            configured=False,
            remote=remote,
        )
    assert isinstance(remote, str)
    assert isinstance(url, str)

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("configured", False)
        err["data"].setdefault("remote", remote)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    exists, current_url, probe_error = await _get_remote_url(git_bin, directory, remote)
    if exists and current_url == url:
        action = "unchanged"
    else:
        args = [
            git_bin,
            "-C",
            directory,
            "remote",
            "add" if not exists else "set-url",
            remote,
            url,
        ]
        result = await _run_git_async(args, encoding="utf-8", timeout=15.0)
        if not result.get("ok"):
            stderr = result.get("stderr", "") or result.get("error", "") or probe_error
            return _make_envelope(
                success=False,
                reason=ReasonCode.GIT_ERROR,
                elapsed_ms=_elapsed(),
                configured=False,
                remote=remote,
                url=url,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=stderr[:4096],
            )
        action = "added" if not exists else "updated"

    # 2026-08-16 (elecvoid243): 成功响应附带当前已配置远端名列表。
    # Dashboard 推送对话框的远端下拉据此即时刷新 —— 新 `remote add`
    # 的远端在 fetch 前没有任何 refs/remotes/* 分支, 仅靠
    # git-branches 的 for-each-ref 永远看不到它。
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
        "git-remote-set-url: %s %s (%s, umo=%s)",
        remote,
        _mask_remote_url(url),
        action,
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            configured=True,
            action=action,
            remote=remote,
            url=url,
            remotes=remotes,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
