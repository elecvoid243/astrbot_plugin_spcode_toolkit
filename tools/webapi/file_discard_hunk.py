"""POST /spcode/file-discard-hunk — 按 hunk 丢弃工作区改动(unified diff 入参,git apply --reverse)。

Spec: docs/superpowers/specs/2026-07-06-file-discard-hunk-design.md
v2.16.0 (2026-07-06): 新增端点。镜像 /spcode/file-restore 的行为对称,但操作粒度为 hunk。
"""

from __future__ import annotations

import logging
import re
import time as _time  # noqa: F401  # used by handle() in Tasks 3-5
from dataclasses import dataclass
from pathlib import Path  # noqa: F401  # used by handle() in Tasks 3-5
from typing import TYPE_CHECKING

from .._helpers import (  # noqa: F401  # used by handle() in Tasks 3-5
    _validate_worktree_param,
)
from ._helpers import (
    X_TRULY_STAGED,  # noqa: F401  # used by handle() in Tasks 3-5
    Y_WORKTREE,  # noqa: F401  # used by handle() in Tasks 3-5
    ReasonCode,
    _run_git_async,  # noqa: F401  # used by handle() in Tasks 3-5
    _validate_repo_relative_file,  # noqa: F401  # used by handle() in Tasks 3-5
)

if TYPE_CHECKING:
    from main import SPCodeToolkit  # noqa: F401  # used by handle() in Tasks 3-5

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
