"""GET /spcode/git-remotes — list configured Git remotes with their URLs.

2026-08-16 (elecvoid243): 供 Dashboard "设置 Remote URL" 对话框展示
已有远端 (name + url), 支持查看与后续删除。与 git-branches 的
``remotes`` 名称列表不同, 本端点返回完整 URL (``git remote -v``)。

Spec: docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md
"""

from __future__ import annotations

import logging
import re
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_REMOTE_URL_SUFFIX_RE = re.compile(r"\s+\((?:fetch|push)\)$")


def _parse_remote_verbose(stdout: str) -> list[dict[str, str]]:
    """Parse ``git remote -v`` output into ``[{name, url}]``.

    Each line is ``name<TAB>url (fetch|push)``; the same remote appears
    twice (fetch + push). The fetch URL wins; push is kept only when a
    remote has no fetch row. Names are sorted for stable output.
    """
    by_name: dict[str, str] = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        url_part = parts[1].strip()
        if not name or not url_part:
            continue
        url = _REMOTE_URL_SUFFIX_RE.sub("", url_part)
        if not url:
            continue
        is_fetch = "(fetch)" in url_part
        if name in by_name:
            # 同名多行: fetch 行覆盖 push 行; 已存 fetch 值则跳过。
            if is_fetch:
                by_name[name] = url
        else:
            by_name[name] = url
    return [{"name": n, "url": by_name[n]} for n in sorted(by_name)]


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> _JSONResponseCompat:
    """GET /spcode/git-remotes handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("remotes", [])
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    result = await _run_git_async(
        [git_bin, "-C", directory, "remote", "-v"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        stderr = result.get("stderr", "") or result.get("error", "")
        return _JSONResponseCompat(
            _make_envelope(
                success=False,
                reason="git_error",
                elapsed_ms=_elapsed(),
                remotes=[],
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=stderr[:4096],
            ),
            status_code=200,
        )

    remotes = _parse_remote_verbose(result.get("stdout", ""))
    logger.info("git-remotes: %d remotes (umo=%s)", len(remotes), effective_umo)
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            reason=None,
            elapsed_ms=_elapsed(),
            remotes=remotes,
            total=len(remotes),
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
