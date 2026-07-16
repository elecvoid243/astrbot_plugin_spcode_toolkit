"""GET /spcode/git-branches — 列出本地和远程分支(v2.17.0 PR-C).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.2
Author: elecvoid243
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit

from ._helpers import (
    ReasonCode,
    _JSONResponseCompat,
    _compute_git_etag,
    _make_envelope,
    _run_git_async,
)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    if_none_match: str | None = None,
) -> _JSONResponseCompat:
    """列出当前项目的本地和远程分支。

    Query params:
        umo: 会话 ID(为空返回 feature_disabled)
        worktree: 可选 worktree 路径
        if_none_match: 弱 ETag(304 缓存)
    """
    t0 = _time.monotonic()

    # ── preflight ──────────────────────────────────────────
    from ._helpers import _git_endpoint_preflight

    reason, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if reason is not None:
        return _JSONResponseCompat(reason, status_code=200)
    assert ctx is not None
    repo_dir = ctx["directory"]

    git_bin = plugin._git_binary()  # type: ignore[attr-defined]

    # ── ETag ───────────────────────────────────────────────
    # 与 git_status 一致:取 HEAD SHA + porcelain 作为 ETag 输入
    etag = await _compute_git_etag(git_bin, repo_dir)
    if etag and if_none_match and etag == if_none_match:
        content = _make_envelope(
            success=True,
            reason=None,
            data="(not modified)",
            elapsed_ms=round((_time.monotonic() - t0) * 1000, 2),
        )
        return _JSONResponseCompat(content, status_code=304)

    # ── git for-each-ref ───────────────────────────────────
    try:
        result = await _run_git_async(
            [
                git_bin, "-C", str(repo_dir),
                "for-each-ref",
                "--format=%(if)%(HEAD)%(then)*%(else) %(end)%(refname:short)%09%(objectname:short)%09%(upstream:short)%09%(upstream:track)",
                "refs/heads/",
                "refs/remotes/",
            ],
        )
        if not result.get("ok", False):
            return _JSONResponseCompat(
                _make_envelope(
                    success=False,
                    reason=ReasonCode.GIT_ERROR,
                    stderr=result.get("stderr", ""),
                    elapsed_ms=round((_time.monotonic() - t0) * 1000, 2),
                ),
                status_code=200,
            )
    except Exception:
        return _JSONResponseCompat(
            _make_envelope(
                success=False,
                reason=ReasonCode.GIT_ERROR,
                elapsed_ms=round((_time.monotonic() - t0) * 1000, 2),
            ),
            status_code=200,
        )

    # ── 解析 ───────────────────────────────────────────────
    branches = _parse_for_each_ref(result.get("stdout", ""))

    # 提取 refs/heads/* 和 refs/remotes/*(标记 remote)
    # _parse_for_each_ref 已完整处理,直接可用

    # ── 确定 current ───────────────────────────────────────
    detached = False
    current_name: str | None = None
    for b in branches:
        if b["current"]:
            current_name = b["name"]
            break
    if current_name is None:
        # 检查是否 detached HEAD
        try:
            head_result = await _run_git_async(
                [git_bin, "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            )
            if head_result.get("ok", False):
                head_ref = head_result.get("stdout", "").strip()
                if head_ref == "HEAD":
                    detached = True
        except Exception:
            pass

    content = _make_envelope(
        success=True,
        reason=None,
        branches=branches,
        total=len(branches),
        current=current_name,
        detached=detached,
        elapsed_ms=round((_time.monotonic() - t0) * 1000, 2),
    )

    resp = _JSONResponseCompat(content, status_code=200)
    if etag and resp.headers is not None:
        resp.headers["ETag"] = etag  # type: ignore[index]
    return resp


# ── 解析器(公开供测试) ────────────────────────────────────


def _parse_for_each_ref(raw: str) -> list[dict[str, Any]]:
    """解析 ``git for-each-ref`` 输出为 branch 字典列表。

    格式(每行 tab 分隔):
      %(if)%(HEAD)%(then)*%(else) %(end)%(refname:short)\\t%(objectname:short)\\t%(upstream:short)\\t%(upstream:track)

    Returns:
        list of dicts with keys: name, sha, upstream, upstream_track, current, remote
    """
    result: list[dict[str, Any]] = []
    for line in raw.splitlines():
        # 注意: 不能 .strip() — 首字符可能是空格(标记非当前分支)
        if not line.strip():
            continue
        # 去掉行尾空白(\\r, \\n),保留行首位置
        line_clean = line.rstrip("\r\n")
        # 解析格式: [*| ]<name>\\t<sha>\\t<upstream>\\t<track>
        parts = line_clean.split("\t")
        if len(parts) < 2:
            continue

        name_part = parts[0]  # e.g. "*main" or " feature/x"
        is_current = name_part.startswith("*")
        name = name_part[1:]  # 去掉首字符(* 或 空格)
        # .strip() 只用于去掉 sha/upstream/track 的额外空白
        name = name.strip()

        sha = parts[1].strip() if len(parts) > 1 else ""
        upstream = parts[2].strip() if len(parts) > 2 else ""
        upstream_track = parts[3].strip() if len(parts) > 3 else ""

        result.append({
            "name": name,
            "sha": sha,
            "upstream": upstream,
            "upstream_track": upstream_track,
            "current": is_current,
            "remote": name.startswith("origin/") or name.startswith("remotes/"),
        })

    return result
