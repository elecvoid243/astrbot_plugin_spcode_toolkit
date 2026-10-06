"""GET /spcode/git-file-diff — 单文件任意两版本比较(from→to patch + from 侧全文)。

Spec: docs/superpowers/specs/2026-10-06-git-file-range-diff-design.md
Author: elecvoid243, 2026-10-06 · v2.30.0

一次请求返回渲染「全文件 + diff 叠加」视图所需的全部材料:
``git diff <from> <to> -- <path>`` 的 unified patch + from 侧完整 blob。
"""

from __future__ import annotations

import hashlib
import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from .file_browser import (
    _common_cache_headers,
    _get_if_none_match,
    _make_304_response,
)
from ._helpers import (
    _JSONResponseCompat,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    _run_git_async_bytes,
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


def _is_full_sha(ref: str) -> bool:
    """是否为完整 40-hex SHA(决定响应可否标记 immutable)。"""
    return len(ref) == 40 and all(c in "0123456789abcdef" for c in ref.lower())


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
    # quotePath=false:非 ASCII 路径(中文文件名)在 name-status/diff 输出中
    # 不被八进制转义,否则下方按 path 匹配会失配并静默误判 unchanged
    git_prefix = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "-c",
        "core.quotePath=false",
    ]

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

    # ── 4.5 ETag:基于**解析后**的 from_sha/to_sha 计算(分支漂移在下次解析时
    # 自然反映;若基于原始输入,分支移动后 If-None-Match 会返回陈旧 304)。
    # 仅当 from/to 原始输入均为完整 40-hex SHA 时结果才天然不可变 → immutable。
    etag = 'W/"' + hashlib.sha1(
        f"{from_sha}|{to_sha}|{target_path}|v1".encode()
    ).hexdigest() + '"'
    cache_headers = _common_cache_headers(etag)
    if _is_full_sha(from_ref) and _is_full_sha(to_ref):
        cache_headers["Cache-Control"] = "private, immutable"
    else:
        cache_headers["Cache-Control"] = "private, no-cache"
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)

    # ── 5. 状态探测(全量 name-status 后按 path 匹配) ──
    # WHY 不加 pathspec: rename 检测与 pathspec 过滤的交互有版本差异,
    # 全量输出后自行匹配(新路径或旧路径命中皆可)行为最稳定。
    ns = await _run_git_async(
        git_prefix + ["diff", "--name-status", from_sha, to_sha],
        encoding="utf-8",
    )
    if not ns["ok"]:
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=ns.get("stderr", "") or ns.get("error", ""),
        )

    status = "unchanged"
    old_path: str | None = None
    for line in ns["stdout"].splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        code = parts[0]
        if code.startswith(("R", "C")):
            if len(parts) >= 3 and parts[2] == target_path:
                # 改名为当前查询路径 → renamed,基准 blob 按旧路径取
                status = "renamed"
                old_path = parts[1]
                break
            if len(parts) >= 3 and parts[1] == target_path:
                # 查询路径本身被改走 → 该路径在 to 侧不存在 = deleted
                status = "deleted"
                break
        elif len(parts) >= 2 and parts[1] == target_path:
            status = {"A": "added", "D": "deleted"}.get(code[0], "modified")
            break

    # ── 6. patch 生成 ──
    patch_text = ""
    additions = 0
    deletions = 0
    if status != "unchanged":
        # rename 时 pathspec 同时给旧/新路径,确保 diff 不丢
        pathspec = [old_path, target_path] if old_path else [target_path]
        dp = await _run_git_async(
            git_prefix + ["diff", from_sha, to_sha, "--", *pathspec],
            encoding="utf-8",
        )
        if not dp["ok"]:
            return _make_envelope(
                success=False,
                reason=ReasonCode.GIT_ERROR,
                elapsed_ms=_elapsed(),
                loaded=False,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=dp.get("stderr", "") or dp.get("error", ""),
            )
        patch_text = dp["stdout"]
        for pline in patch_text.splitlines():
            if pline.startswith("+") and not pline.startswith("+++"):
                additions += 1
            elif pline.startswith("-") and not pline.startswith("---"):
                deletions += 1

    # patch 截断(与 git-diff 同语义:截断不失败,前端仍渲染可见部分)
    truncated = len(patch_text) > MAX_PATCH_BYTES
    if truncated:
        patch_text = patch_text[:MAX_PATCH_BYTES]

    # ── 7. 基准 blob(added 时 from 侧无此文件,跳过) ──
    # WHY deleted 也要读:前端叠加视图需要展示被删全文。
    # WHY bytes 变体:`_run_git_async` 会 rstrip 尾部换行(porcelain 安全设计),
    # blob 内容需要保字节级完整,且二进制 NUL 探测必须在解码前做。
    base_content = ""
    base_size = 0
    base_truncated = False
    is_binary = False
    if status != "added":
        blob_path = old_path or target_path
        show = await _run_git_async_bytes(
            git_prefix + ["show", f"{from_sha}:{blob_path}"],
        )
        if not show["ok"]:
            return _make_envelope(
                success=False,
                reason=ReasonCode.GIT_ERROR,
                elapsed_ms=_elapsed(),
                loaded=False,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=show.get("stderr", "") or show.get("error", ""),
            )
        raw_blob: bytes = show["stdout"]
        base_size = len(raw_blob)
        # 二进制:NUL 探测(与 git-file HEAD_BYTES 窗口一致)
        if b"\x00" in raw_blob[:8000]:
            is_binary = True
        # 超上限截断(与 git-file 先例一致:截断不失败,前端对齐失败时
        # 会自动降级为纯 patch 视图)
        base_truncated = len(raw_blob) > MAX_BASE_BLOB_BYTES
        if base_truncated:
            raw_blob = raw_blob[:MAX_BASE_BLOB_BYTES]
        base_content = raw_blob.decode("utf-8", errors="replace")

    # patch 文本二进制信号:added(无 base blob 可探)与 text→binary 的
    # modified(base 是纯文本,NUL 探测不到)都只能靠这个信号。
    # 注意 patch 以 "diff --git" 头开头,信号行在头部之后。
    if any(
        pline.startswith("Binary files") for pline in patch_text.splitlines()
    ):
        is_binary = True
    if is_binary:
        patch_text_out: str | None = None
        additions_out: int | None = None
        deletions_out: int | None = None
        base_content = ""
    else:
        patch_text_out = patch_text
        additions_out = additions
        deletions_out = deletions

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
            status=status,
            old_path=old_path,
            is_binary=is_binary,
            base_content=base_content,
            base_size=base_size,
            base_truncated=base_truncated,
            patch=patch_text_out,
            additions=additions_out,
            deletions=deletions_out,
            truncated=truncated,
            truncated_at_bytes=MAX_PATCH_BYTES if truncated else 0,
            max_bytes=MAX_PATCH_BYTES,
        ),
        status_code=200,
        headers=cache_headers,
    )
