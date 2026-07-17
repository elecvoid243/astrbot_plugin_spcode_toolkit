"""POST /spcode/file-write — 保存 repo 文本文件(通用文件编辑,upsert)。

2026-07-17: 工作区文件浏览器预览支持编辑任意文本文件。POST /spcode/docs
按设计仅接受 .md 路径(spec §4.3),不能用于代码文件,故新增本端点。

与 docs POST 的差异:
  - 不限定扩展名(任意 repo-relative 文本文件)。

相同点:
  - upsert 语义: 目标不存在则新建(自动创建父目录),已存在则覆写;
    响应带 ``created`` 标志区分两种动作(2026-07-17 修复: 原实现仅允许
    覆写已存在文件,与前端"保存后将新建"提示矛盾)。

边界: 目标已存在但不是常规文件(目录 / 特殊文件)→ file_not_found,
避免 ``write_text`` 抛 ``IsADirectoryError``。

防御链与 docs POST 一致: `_git_endpoint_preflight`(5 步) +
`_validate_repo_relative_file`(4 步);UTF-8 写入;content ≤ 2 MB。
注意: 非 UTF-8 文件(如 GBK)保存后会被转为 UTF-8(前端编辑区有提示)。
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _make_envelope,
    _validate_repo_relative_file,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_PATH_LENGTH = 512
MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB,与 docs_crud 对齐


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


def _validate_write_path(path: object) -> str | None:
    """校验 path 字段(与 docs `_validate_doc_path` 相同,但无 .md 限制)。

    返回 None 表示 OK;否则返回 reason 码(invalid_body / invalid_param)。

    顺序:
      1. 类型:必须 str
      2. 长度:≤ 512
      3. 字符:不含换行 / NUL
    """
    if not isinstance(path, str):
        return ReasonCode.INVALID_BODY
    p = path.strip()
    if not p or len(p) > MAX_PATH_LENGTH:
        return ReasonCode.INVALID_PARAM
    if "\n" in p or "\r" in p or "\x00" in p:
        return ReasonCode.INVALID_PARAM
    return None


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/file-write handler — 保存文本内容(upsert: 不存在则新建)。"""
    t0 = _time.time()
    if body is None or not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    path = body.get("path", "")
    content = body.get("content", "")
    if not isinstance(content, str):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
        )

    err_reason = _validate_write_path(path)
    if err_reason is not None:
        return _make_envelope(
            success=False,
            reason=err_reason,
            elapsed_ms=_elapsed(t0),
        )

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            stderr=(f"content bytes {len(content_bytes)} > limit {MAX_CONTENT_BYTES}"),
        )

    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    target, path_err = _validate_repo_relative_file(path, Path(directory))
    if path_err is not None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # 已存在但不是常规文件(目录 / 特殊文件)→ 拒绝,避免 write_text
    # 抛 IsADirectoryError。reason 沿用 file_not_found(语义: 无可写文件)。
    if target.exists() and not target.is_file():
        return _make_envelope(
            success=False,
            reason=ReasonCode.FILE_NOT_FOUND,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            path=path,
        )

    # upsert(2026-07-17): 不存在则新建 — 前端编辑不存在的文件(如仓库
    # 还没有 .gitignore)时提示"保存后将新建",后端必须兑现该语义。
    # 自动创建缺失的父目录,与 docs POST 行为一致。
    created = not target.exists()
    if created:
        target.parent.mkdir(parents=True, exist_ok=True)

    # newline="": 写盘不做 \n → os.linesep 转换(Windows 上默认会把 LF
    # 内容写成 CRLF)。前端 textarea 已把内容规范为 \n,按字节原样落盘,
    # 与 git 仓库主流的 LF 风格一致。
    target.write_text(content, encoding="utf-8", newline="")
    logger.info("[file-write] saved %s (%d bytes)", target, len(content_bytes))

    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        saved=True,
        created=created,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        path=path,
        size=len(content_bytes),
    )
