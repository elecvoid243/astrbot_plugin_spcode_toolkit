"""POST /spcode/file-discard-hunk — 按 hunk 丢弃工作区改动(unified diff 入参,git apply --reverse)。

Spec: docs/superpowers/specs/2026-07-06-file-discard-hunk-design.md
v2.16.0 (2026-07-06): 新增端点。镜像 /spcode/file-restore 的行为对称,但操作粒度为 hunk。
"""

from __future__ import annotations

import logging
import re
import time as _time
from dataclasses import dataclass
from pathlib import Path  # noqa: F401  # used by handle() in Tasks 4
from typing import TYPE_CHECKING, Any

from .._helpers import (  # noqa: F401  # used by handle() in Task 4
    _validate_worktree_param,
)
from ._helpers import (
    X_TRULY_STAGED,  # noqa: F401  # used by handle() in Task 5
    Y_WORKTREE,  # noqa: F401  # used by handle() in Task 5
    ReasonCode,
    _run_git_async,  # noqa: F401  # used by handle() in Task 4 (+ Task 5)
    _validate_repo_relative_file,  # noqa: F401  # used by handle() in Task 4
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ── 端点常量 ──
MAX_PATCH_BYTES = 256 * 1024  # 与 git-show?path= 的 MAX_SHOW_FILE_BYTES 对齐


# ── Patch 解析正则(纯 Python,不调 git) ──
_DIFF_HEADER_RE = re.compile(
    r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$", re.MULTILINE
)
_HUNK_HEADER_RE = re.compile(
    r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE
)
_BINARY_RE = re.compile(r"^Binary files .* differ$", re.MULTILINE)


@dataclass
class PatchMeta:
    """`_parse_patch_header` 解析后的 patch 元数据。

    Attributes:
        hunk_count: patch 中的 `@@` hunk 头数量(成功时 ≥ 1)。
        source_path: `diff --git` 头中的 `a/` 路径(成功时非空)。
        target_path: `diff --git` 头中的 `b/` 路径(成功时非空)。
        err: 失败时的 reason code;成功时为 None。
    """

    hunk_count: int = 0
    source_path: str = ""
    target_path: str = ""
    err: str | None = None


def _parse_patch_header(patch_text: str, expected_file: str) -> PatchMeta:
    """解析 patch 头部,6 步防御。

    校验项:
      1. 必须含 ≥ 1 个 `diff --git a/X b/X` 头;否则 patch_malformed
      2. 必须仅含 1 个 `diff --git` 头;否则 multi_file_patch
      3. `a/` 与 `b/` 路径不能含 `..` / 绝对 / `.git/` 段;否则 patch_unsafe_path
      4. `b/` 路径必须 = body `file` 字段(允许尾部 trim);否则 patch_file_mismatch
      5. patch 不能含 `Binary files ... differ` 行;否则 patch_binary
      6. patch 必须含 ≥ 1 个 `@@ ... @@` hunk 头;否则 patch_malformed

    Returns:
        PatchMeta: 成功时 hunk_count ≥ 1, err=None;失败时 err 是 reason code,
            hunk_count=0, path 字段可能为空。
    """
    if not patch_text or not expected_file:
        return PatchMeta(err=ReasonCode.PATCH_MALFORMED)

    # 1. 必须含 `diff --git` 头
    diff_headers = list(_DIFF_HEADER_RE.finditer(patch_text))
    if not diff_headers:
        return PatchMeta(err=ReasonCode.PATCH_MALFORMED)

    # 2. 必须仅含 1 个
    if len(diff_headers) > 1:
        return PatchMeta(err=ReasonCode.MULTI_FILE_PATCH)

    first = diff_headers[0]
    source = first.group("a")
    target = first.group("b")

    # 3. 路径安全
    for path in (source, target):
        if ".." in path.split("/"):
            return PatchMeta(err=ReasonCode.PATCH_UNSAFE_PATH)
        if path.startswith("/") or path.startswith("\\"):
            return PatchMeta(err=ReasonCode.PATCH_UNSAFE_PATH)
        if any(seg == ".git" for seg in path.split("/")):
            return PatchMeta(err=ReasonCode.PATCH_UNSAFE_PATH)

    # 4. file 字段匹配(允许尾部 trim,例如换行符)
    if target.strip() != expected_file.strip():
        return PatchMeta(err=ReasonCode.PATCH_FILE_MISMATCH)

    # 5. binary 标记
    if _BINARY_RE.search(patch_text):
        return PatchMeta(err=ReasonCode.PATCH_BINARY)

    # 6. hunk 头
    hunk_matches = list(_HUNK_HEADER_RE.finditer(patch_text))
    if not hunk_matches:
        return PatchMeta(err=ReasonCode.PATCH_MALFORMED)

    return PatchMeta(
        hunk_count=len(hunk_matches),
        source_path=source,
        target_path=target,
        err=None,
    )


# ── Envelope 工厂(对称于 file_restore) ──


def _make_file_discard_hunk_empty_envelope(
    *,
    umo: str | None = None,
    file: str = "",
    directory: str | None = None,
    worktree: str | None = None,
    scope: str = "unstaged",
    reason: str,
    stderr: str = "",
    patch_sha: str = "",
    elapsed_ms: int = 0,
) -> dict:
    return {
        "status": "ok",
        "data": {
            "discarded": False,
            "directory": directory,
            "umo": umo,
            "worktree": worktree or directory,
            "file": file,
            "scope": scope,
            "hunks_reverted": 0,
            "patch_sha256": patch_sha,
            "elapsed_ms": elapsed_ms,
            "stderr": stderr,
            "reason": reason,
        },
    }


def _make_file_discard_hunk_success_envelope(
    *,
    umo: str | None,
    file: str,
    directory: str,
    elapsed_ms: int,
    scope: str = "unstaged",
    hunks: int = 0,
    patch_sha: str = "",
) -> dict:
    return {
        "status": "ok",
        "data": {
            "discarded": True,
            "directory": directory,
            "umo": umo,
            "worktree": directory,
            "file": file,
            "scope": scope,
            "hunks_reverted": hunks,
            "patch_sha256": patch_sha,
            "elapsed_ms": elapsed_ms,
            "stderr": "",
            "reason": None,
        },
    }


# ── Handler ──


def _classify_apply_failure(stderr: str) -> str:
    """把 `git apply --check --reverse` 的 stderr 关键串映射到 reason code。

    优先级:
      1. 格式错("corrupt patch" / "unable to read") → patch_malformed
      2. context / whitespace 错 → patch_check_failed
      3. 兜底 → git_error
    """
    lower = stderr.lower()
    if "corrupt patch" in lower or "unable to read" in lower:
        return ReasonCode.PATCH_MALFORMED
    if (
        "patch does not apply" in lower
        or "context does not match" in lower
        or "whitespace" in lower
        or "trailing whitespace" in lower
        or "indent" in lower
        or "no such line" in lower
    ):
        return ReasonCode.PATCH_CHECK_FAILED
    return ReasonCode.GIT_ERROR


async def handle(
    plugin: "SPCodeToolkit",
    **kwargs: Any,
) -> dict:
    """POST /spcode/file-discard-hunk handler.

    完整流程见 spec §8。Task 3 覆盖 step 1-5(到 patch 解析前)。
    Task 4 覆盖 step 6-9(file 安全 + git probe + patch 解析)。
    Task 5 覆盖 step 10-15(scope detect + git apply + 成功路径)。
    """
    import hashlib

    from astrbot.api import web

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # Step 1: body 解析
    try:
        body = await web.request.json(default=None)
    except Exception:
        body = None
    if not isinstance(body, dict):
        return _make_file_discard_hunk_empty_envelope(
            reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed()
        )

    # Step 2: file 字段
    # NOTE: ``missing_file`` 未升格为 ``ReasonCode`` 成员(对齐 file_restore
    # 原有的字面量用法,见 tools/webapi/file_restore.py:149)。沿用字面量保持
    # 与既有 envelope reason 字串一致,便于前端 switch case。
    file_field = body.get("file", "")
    if not isinstance(file_field, str) or not file_field.strip():
        return _make_file_discard_hunk_empty_envelope(
            reason="missing_file", elapsed_ms=_elapsed()
        )
    file = file_field.strip()

    # Step 3: patch_text 字段
    patch_text = body.get("patch_text", "")
    if not isinstance(patch_text, str):
        return _make_file_discard_hunk_empty_envelope(
            reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed()
        )
    if not patch_text:
        return _make_file_discard_hunk_empty_envelope(
            file=file,
            reason=ReasonCode.PATCH_EMPTY,
            elapsed_ms=_elapsed(),
        )
    patch_bytes = patch_text.encode("utf-8")
    if len(patch_bytes) > MAX_PATCH_BYTES:
        return _make_file_discard_hunk_empty_envelope(
            file=file,
            reason=ReasonCode.PATCH_TOO_LARGE,
            elapsed_ms=_elapsed(),
        )
    patch_sha = hashlib.sha256(patch_bytes).hexdigest()[:16]

    # Step 4: umo / worktree 提取
    umo_raw = body.get("umo")
    umo = umo_raw if isinstance(umo_raw, str) and umo_raw.strip() else None
    wt_raw = body.get("worktree")
    worktree_param = wt_raw if isinstance(wt_raw, str) else None  # noqa: F841  # consumed in Task 4 (_validate_worktree_param)

    # Step 5: feature flag
    if not (
        plugin._config.get("agentsmd_enabled", True)
        and plugin._config.get("codegraph_enabled", True)
    ):
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, patch_sha=patch_sha,
            reason=ReasonCode.FEATURE_DISABLED, elapsed_ms=_elapsed(),
        )

    # Step 6: umo 解析 + 最近载入回退
    from tools.project import state as _proj_state

    if umo:
        info = plugin.get_loaded_project(umo)
    else:
        all_items = _proj_state.items()
        if not all_items:
            info = None
        else:
            _, info = max(
                all_items.items(),
                key=lambda kv: kv[1].get("loaded_at", 0),
            )
    if info is None:
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, patch_sha=patch_sha,
            reason=ReasonCode.NO_PROJECT_LOADED, elapsed_ms=_elapsed(),
        )
    directory = info.get("directory", "")

    # Step 7: worktree 6 步防御
    if worktree_param is not None and worktree_param.strip():
        validated_wt, wt_err = _validate_worktree_param(
            plugin._git_binary(), directory, worktree_param
        )
        if wt_err is not None:
            logger.warning(
                f"[file-discard-hunk] rejected worktree={worktree_param!r} "
                f"(loaded={directory!r})"
            )
            return _make_file_discard_hunk_empty_envelope(
                file=file, umo=umo, directory=directory, patch_sha=patch_sha,
                reason=wt_err, elapsed_ms=_elapsed(),
            )
        directory = validated_wt

    # Step 8: 目录存在性
    if not Path(directory).is_dir():
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, patch_sha=patch_sha,
            reason=ReasonCode.DIRECTORY_MISSING, elapsed_ms=_elapsed(),
        )

    # Step 9: git repo probe
    git_bin = plugin._git_binary()
    probe = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            return _make_file_discard_hunk_empty_envelope(
                file=file, umo=umo, directory=directory, patch_sha=patch_sha,
                reason=ReasonCode.NOT_A_GIT_REPO, elapsed_ms=_elapsed(),
            )
        if "未安装" in probe.get("error", ""):
            return _make_file_discard_hunk_empty_envelope(
                file=file, umo=umo, directory=directory, patch_sha=patch_sha,
                reason=ReasonCode.GIT_UNAVAILABLE, elapsed_ms=_elapsed(),
            )
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, patch_sha=patch_sha,
            reason=ReasonCode.GIT_ERROR,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
            elapsed_ms=_elapsed(),
        )

    # Step 10: file 路径 4 步防御
    target, path_err = _validate_repo_relative_file(file, Path(directory))
    if path_err is not None:
        logger.warning(
            f"[file-discard-hunk] rejected file={file!r} "
            f"(worktree={directory!r}): {path_err}"
        )
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, patch_sha=patch_sha,
            reason=ReasonCode.PATH_UNSAFE, elapsed_ms=_elapsed(),
        )
    if not target.exists():
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, patch_sha=patch_sha,
            reason=ReasonCode.FILE_NOT_FOUND, elapsed_ms=_elapsed(),
        )

    # Step 11: patch 解析(纯 Python,前置 fail-fast)
    patch_meta = _parse_patch_header(patch_text, expected_file=file)
    if patch_meta.err is not None:
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, patch_sha=patch_sha,
            reason=patch_meta.err, elapsed_ms=_elapsed(),
        )

    # Step 12: scope auto-detect(porcelain X/Y 列)
    status = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain", "--", file],
        encoding="utf-8",
    )
    if not status["ok"]:
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, scope="unstaged",
            patch_sha=patch_sha,
            reason=ReasonCode.GIT_ERROR,
            stderr=status.get("stderr", ""),
            elapsed_ms=_elapsed(),
        )
    porcelain = status["stdout"]
    if not porcelain.strip():
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, scope="unstaged",
            patch_sha=patch_sha,
            reason=ReasonCode.NOT_MODIFIED, elapsed_ms=_elapsed(),
        )
    first_line = porcelain.splitlines()[0]
    x_status = first_line[0] if len(first_line) >= 1 else " "
    y_status = first_line[1] if len(first_line) >= 2 else " "
    if x_status == "?" and y_status == "?":
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, scope="unstaged",
            patch_sha=patch_sha,
            reason=ReasonCode.UNTRACKED_FILE, stderr=porcelain,
            elapsed_ms=_elapsed(),
        )
    is_intent_to_add = x_status == " " and y_status == "A"
    is_truly_staged = x_status in X_TRULY_STAGED
    # intent-to-add 和 truly staged 都走 --cached
    if is_intent_to_add or is_truly_staged:
        scope = "staged"
        apply_base = ["apply", "--reverse", "--cached",
                      "--whitespace=error", "--no-unsafe-paths"]
    else:
        scope = "unstaged"
        apply_base = ["apply", "--reverse",
                      "--whitespace=error", "--no-unsafe-paths"]

    # Step 13: git apply --check --reverse(干跑)
    check_args = [git_bin, "-C", directory, "-c", "color.ui=never"] + apply_base
    # 注入 --check
    check_args = check_args[:check_args.index("apply") + 1] + ["--check"] + check_args[check_args.index("apply") + 1:]
    check = await _run_git_async(
        check_args, input_text=patch_text, encoding="utf-8",
    )
    if not check["ok"]:
        stderr = check.get("stderr", "")
        reason = _classify_apply_failure(stderr)
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, scope=scope,
            patch_sha=patch_sha, reason=reason, stderr=stderr,
            elapsed_ms=_elapsed(),
        )

    # Step 14: git apply --reverse(实际应用)
    apply_args = [git_bin, "-C", directory, "-c", "color.ui=never"] + apply_base
    apply = await _run_git_async(
        apply_args, input_text=patch_text, encoding="utf-8",
    )
    if not apply["ok"]:
        return _make_file_discard_hunk_empty_envelope(
            file=file, umo=umo, directory=directory, scope=scope,
            patch_sha=patch_sha,
            reason=ReasonCode.PATCH_APPLY_FAILED,
            stderr=apply.get("stderr", ""),
            elapsed_ms=_elapsed(),
        )

    # Step 15: 成功:审计 + success envelope
    logger.info(
        f"[file-discard-hunk] reverted: file={file!r} scope={scope} "
        f"hunks={patch_meta.hunk_count} patch_sha={patch_sha} "
        f"worktree={directory!r} umo={umo!r} elapsed_ms={_elapsed()}"
    )
    return _make_file_discard_hunk_success_envelope(
        umo=umo, file=file, directory=directory, scope=scope,
        hunks=patch_meta.hunk_count, patch_sha=patch_sha,
        elapsed_ms=_elapsed(),
    )