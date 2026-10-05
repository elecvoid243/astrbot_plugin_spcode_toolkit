"""GET /spcode/git-file-diff — 单文件任意两版本比较(from→to patch + from 侧全文)。

Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-design.md
Author: elecvoid243, 2026-10-06 · v2.30.0

一次请求返回渲染「全文件 + diff 叠加」视图所需的全部材料:
``git diff <from> <to> -- <path>`` 的 unified patch + from 侧完整 blob。
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    _JSONResponseCompat,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    _validate_repo_relative_file,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_PARAM_LENGTH = 512
MAX_PATCH_BYTES = 1 * 1024 * 1024  # 1 MB patch 硬上限(与 git-diff 一致)
MAX_BASE_BLOB_BYTES = 1 * 1024 * 1024  # 1 MB 基准 blob 硬上限(与 git-file 一致)


def _qget(query: object, key: str, default: str | None = None) -> str | None:
    try:
        v = query.get(key)  # type: ignore[attr-defined]
        return v if v else default
    except Exception:
        return default


def _bad_ref_reason(stderr_lower: str) -> str:
    """rev-parse stderr → ReasonCode(与 git-file.py 的关键字映射保持一致)。"""
    if (
        "bad revision" in stderr_lower
        or "unknown revision" in stderr_lower
        or "bad object" in stderr_lower
        or "not a commit" in stderr_lower
    ):
        return ReasonCode.REF_NOT_FOUND
    if "does not have any commits" in stderr_lower or "ambiguous" in stderr_lower:
        return ReasonCode.EMPTY_REPOSITORY
    return ReasonCode.GIT_ERROR


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-file-diff handler。

    Query 参数:
      - ``from``(必填):基准 ref
      - ``to``(必填):目标 ref(from→to 方向,不做时间序纠正)
      - ``path``(必填):仓库相对路径
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            loaded=False,
            umo=umo,
            worktree=worktree,
            **fields,
        )

    from astrbot.api import web

    query = web.request.query if hasattr(web, "request") else {}

    # ── 1. 参数校验:from/to/path 必填、长度、控制字符 ──
    from_ref = (_qget(query, "from") or "").strip()
    to_ref = (_qget(query, "to") or "").strip()
    target_path = (_qget(query, "path") or "").strip()
    for value in (from_ref, to_ref, target_path):
        if (
            not value
            or len(value) > MAX_PARAM_LENGTH
            or "\n" in value
            or "\r" in value
            or "\x00" in value
        ):
            return _failure(ReasonCode.INVALID_PARAM)

    # ── 2. preflight(5 步:feature flag / project / worktree / dir / repo) ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # ── 3. path 4 步防御 ──
    _, path_err = _validate_repo_relative_file(target_path, Path(directory))
    if path_err is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    git_bin = plugin._git_binary()  # type: ignore[attr-defined]
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # ── 4. 双 ref 解析(from 先,to 后;failing_ref 区分是哪一侧) ──
    resolved: dict[str, str] = {}
    for side, ref in (("from", from_ref), ("to", to_ref)):
        resolve = await _run_git_async(
            git_prefix + ["rev-parse", ref + "^{commit}"],
            encoding="utf-8",
        )
        if not resolve["ok"] or not resolve["stdout"]:
            stderr = (resolve.get("stderr", "") or resolve.get("error", "")).lower()
            return _make_envelope(
                success=False,
                reason=_bad_ref_reason(stderr),
                elapsed_ms=_elapsed(),
                loaded=False,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                failing_ref=side,
                stderr=resolve.get("stderr", "") or resolve.get("error", ""),
            )
        resolved[side] = resolve["stdout"]

    from_sha = resolved["from"]
    to_sha = resolved["to"]

    # ── 5+. 状态探测 / patch / blob:Task 2 填充 ──
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            **{"from": from_ref, "to": to_ref},
            from_sha=from_sha,
            to_sha=to_sha,
            path=target_path,
        ),
        status_code=200,
    )
