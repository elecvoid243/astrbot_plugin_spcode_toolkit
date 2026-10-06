"""POST /spcode/git-file-export — 导出历史版本 blob 到临时文件。

Spec: docs/superpowers/specs/2026-10-06-git-file-export-design.md
Author: elecvoid243, 2026-10-06 · v2.31.0

供 Git 历史页「导出此版本并打开」:把 ``<ref>:<path>`` 的 blob **字节**
写到 ``<plugin_root>/data/temp/git-history/<sha7>/<repo-relative path>``,
返回宿主机绝对路径;前端复用核心 ``POST /api/v1/chat/open-file`` 打开。
同 (sha, path) 重复导出 = 覆盖同一目标,不堆垃圾文件。
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
    _run_git_async_bytes,
    _validate_repo_relative_file,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_PARAM_LENGTH = 512

PLUGIN_NAME = "astrbot_plugin_spcode_toolkit"

# 导出根(2026-10-06 修订:按 AstrBot 插件约定放 plugin_data 下):
#   <astrbot data>/plugin_data/astrbot_plugin_spcode_toolkit/temp/git-history/
# 与 todo 存储同一真源(StarTools.get_data_dir)。StarTools 不可用时回退
# 插件仓库 data/temp(standalone 测试 / 异常兜底;data/ 已 gitignore)。
# 测试通过 EXPORT_ROOT_OVERRIDE 重定向到临时目录。
EXPORT_ROOT_OVERRIDE: Path | None = None
_FALLBACK_EXPORT_ROOT: Path = (
    Path(__file__).resolve().parents[2] / "data" / "temp" / "git-history"
)


def _export_root() -> Path:
    """解析导出根:plugin_data 约定优先,异常回退插件仓库 data/temp。"""
    if EXPORT_ROOT_OVERRIDE is not None:
        return EXPORT_ROOT_OVERRIDE
    try:
        from astrbot.api.star import StarTools

        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        return Path(str(data_dir)) / "temp" / "git-history"
    except Exception:  # noqa: BLE001 — standalone / 运行时缺失一律回退
        return _FALLBACK_EXPORT_ROOT


def _bad_ref_reason(stderr_lower: str) -> str:
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
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-file-export handler。

    Body:
      - ``path``(必填):仓库相对路径
      - ``ref``(必填):任意 git ref(commit / branch / tag / HEAD~n)
      - ``umo`` / ``worktree``(可选):会话 / worktree 路由(与其它写端点一致)
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(reason: str, **fields: object) -> dict:
        # umo/worktree 可被调用方覆盖(如 preflight 解析出的 effective_umo)
        base: dict[str, object] = {"umo": umo, "worktree": worktree}
        base.update(fields)
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            exported=False,
            **base,
        )

    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    raw_path = body.get("path")
    raw_ref = body.get("ref")
    target_path = raw_path.strip() if isinstance(raw_path, str) else ""
    ref = raw_ref.strip() if isinstance(raw_ref, str) else ""
    for value in (target_path, ref):
        if (
            not value
            or len(value) > MAX_PARAM_LENGTH
            or "\n" in value
            or "\r" in value
            or "\x00" in value
        ):
            return _failure(ReasonCode.INVALID_PARAM)

    # ── preflight(5 步,与只读端点同链) ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        err["data"].setdefault("exported", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # ── path 4 步防御 ──
    _, path_err = _validate_repo_relative_file(target_path, Path(directory))
    if path_err is not None:
        return _failure(
            ReasonCode.PATH_UNSAFE, directory=directory, umo=effective_umo
        )

    git_bin = plugin._git_binary()  # type: ignore[attr-defined]
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # ── ref 解析 ──
    resolve = await _run_git_async(
        git_prefix + ["rev-parse", ref + "^{commit}"],
        encoding="utf-8",
    )
    if not resolve["ok"] or not resolve["stdout"]:
        stderr = (resolve.get("stderr", "") or resolve.get("error", "")).lower()
        return _failure(
            _bad_ref_reason(stderr),
            directory=directory,
            umo=effective_umo,
            stderr=resolve.get("stderr", "") or resolve.get("error", ""),
        )
    resolved_sha = resolve["stdout"]

    # ── 读 blob(bytes 变体:二进制保真) ──
    show = await _run_git_async_bytes(
        git_prefix + ["show", f"{resolved_sha}:{target_path}"],
    )
    if not show["ok"]:
        stderr_lower = (show.get("stderr", "") or show.get("error", "")).lower()
        if (
            "exists on disk, but not in" in stderr_lower
            or "does not exist in" in stderr_lower
            or "path not in" in stderr_lower
        ):
            reason = ReasonCode.FILE_MISSING_AT_REF
        elif "bad revision" in stderr_lower or "bad object" in stderr_lower:
            reason = ReasonCode.REF_NOT_FOUND
        else:
            reason = ReasonCode.GIT_ERROR
        return _failure(
            reason,
            directory=directory,
            umo=effective_umo,
            path=target_path,
            ref=ref,
            stderr=show.get("stderr", "") or show.get("error", ""),
        )
    raw: bytes = show["stdout"]

    # ── 写临时文件(<sha7>/<repo-relative path>,同目标覆盖) ──
    sha7 = resolved_sha[:7]
    export_root = _export_root()
    target = export_root / sha7 / Path(target_path)
    export_root_resolved = export_root.resolve()
    try:
        resolved_target = target.resolve()
        if not resolved_target.is_relative_to(export_root_resolved):
            # 防御:relpath 已过 4 步校验,这里是最后兜底(符号链接等)
            return _failure(
                ReasonCode.PATH_UNSAFE, directory=directory, umo=effective_umo
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        overwritten = target.exists()
        target.write_bytes(raw)
    except OSError as exc:
        logger.error("[git-file-export] 写入失败: %s", exc)
        return _failure(
            ReasonCode.GIT_ERROR,
            directory=directory,
            umo=effective_umo,
            path=target_path,
            ref=ref,
            stderr=str(exc),
        )

    logger.info(
        "[git-file-export] %s@%s -> %s (%d bytes, overwrite=%s)",
        target_path,
        sha7,
        target,
        len(raw),
        overwritten,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            exported=True,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=target_path,
            ref=ref,
            resolved_sha=resolved_sha,
            # 正斜杠:与 dashboard 既有 openOnDisk 调用点的路径形态一致
            abs_path=str(target).replace("\\", "/"),
            size=len(raw),
            is_binary=b"\x00" in raw[:8000],
            overwritten=overwritten,
        ),
        status_code=200,
    )
