"""GET /spcode/git-branches — 列出本地和远程分支(v2.17.0 PR-C).

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.2
Author: elecvoid243
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

from ._helpers import (
    ReasonCode,
    _compute_git_etag,
    _JSONResponseCompat,
    _make_envelope,
    _parse_for_each_ref,
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

    reason, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if reason is not None:
        return _JSONResponseCompat(reason, status_code=200)
    assert ctx is not None
    repo_dir = ctx["directory"]

    git_bin = plugin._git_binary()  # type: ignore[attr-defined]

    # ── ETag ───────────────────────────────────────────────
    # 与 git_status 一致:取 HEAD SHA + porcelain 作为 ETag 输入。
    # v3.6 (2026-07-24): 额外把 ``%(upstream:track)`` 拼入 ETag,
    # 修复 ``git fetch / pull / push`` 后上游跟踪计数(↑N / ↓N)
    # 缓存命中的 staleness bug —— 这些操作不动工作树, porcelain
    # 不变, 但 upstream_track 会变。再跑一次极轻量的 for-each-ref
    # 只取 track 字段, ref 数量 < 1000 时开销 < 5ms。
    extra_etag_inputs: tuple[str, ...] = ()
    try:
        track_result = await _run_git_async(
            [
                git_bin,
                "-C",
                str(repo_dir),
                "for-each-ref",
                "--format=%(upstream:track)",
                "refs/heads/",
                "refs/remotes/",
            ],
        )
        if track_result.get("ok", False):
            # 单行 track 字符串(末尾可能含 \n)→ 整体作为单一 ETag 因子。
            # 任何 track 字符串变化都会让 hash 不同, 命中 304 失效。
            track_blob = (track_result.get("stdout") or "").strip()
            extra_etag_inputs = (track_blob,) if track_blob else ()
    except Exception:
        # for-each-ref 失败不阻塞主流程: 返回的 etag 退回到 v3.5 行为,
        # 即不感知 upstream_track 变化 (保留旧行为作为兜底)。
        pass

    etag = await _compute_git_etag(git_bin, repo_dir, extra_inputs=extra_etag_inputs)
    if etag and if_none_match and etag == if_none_match:
        content = _make_envelope(
            success=True,
            reason=None,
            data="(not modified)",
            elapsed_ms=round((_time.monotonic() - t0) * 1000, 2),
        )
        return _JSONResponseCompat(content, status_code=304)

    # ── git for-each-ref ───────────────────────────────────
    from ._helpers import _FOR_EACH_REF_FORMAT  # noqa: PLC0415

    try:
        result = await _run_git_async(
            [
                git_bin,
                "-C",
                str(repo_dir),
                "for-each-ref",
                f"--format={_FOR_EACH_REF_FORMAT}",
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

    # ── 解析(与 _read_post_mutation_branch_state 共享解析器) ──
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
