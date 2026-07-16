"""GET /spcode/git-repo-check - 判断给定目录是否为 git 仓库(v2.18.0).

该端点将原先耦合在 ``_git_endpoint_preflight`` 第 5 步(以及隐含在
``git-branches`` 等端点的错误返回中)的"是否是 git 项目"判断能力解耦为一个
独立的只读端点。

**设计原则**: 一个目录是否是 git 仓库是 ``path`` 的纯函数,与 umo(会话)、
worktree、项目加载状态等运行时上下文完全无关。因此本端点直接接受 ``path``
参数(与 ``file-browser`` / ``git-init`` 一致),不经过 ``_git_endpoint_preflight``
的 umo 解析链。

判断依据:执行 ``git rev-parse --is-inside-work-tree``。
- 命令成功(stdout == "true") -> ``is_git_repo = True``
- 命令失败且 stderr 含 "not a git repository" -> ``is_git_repo = False``
- git 可执行文件不存在 -> ``git_available = False``
- 其他失败 -> ``reason`` 字段给出分类

Author: elecvoid243
Created: 2026-07-16
"""

from __future__ import annotations

import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

from ._helpers import (
    ReasonCode,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)


async def handle(
    plugin: SPCodeToolkit,
    *,
    path: str = "",
) -> _JSONResponseCompat:
    """判断给定目录是否为 git 仓库。

    Query params:
        path: 必填,要探测的目录绝对路径。

    Returns:
        成功路径 envelope::

            {
              "status": "ok",
              "data": {
                "is_git_repo": true,
                "git_available": true,
                "directory": "/path/to/project",
                "reason": null,
                "elapsed_ms": 1.23
              }
            }

        非 git 仓库时 ``is_git_repo`` 为 ``False``,``reason`` 为
        ``not_a_git_repo``;git 可执行文件缺失时 ``git_available`` 为
        ``False``,``reason`` 为 ``git_unavailable``。

        前置失败(path 为空 / 不安全 / 不是目录)直接返回对应 reason,
        此时 ``is_git_repo`` / ``git_available`` 为 ``null``。
    """
    t0 = _time.monotonic()

    def _elapsed() -> float:
        return round((_time.monotonic() - t0) * 1000, 2)

    # ── 1. 参数校验 ──
    if not path or not path.strip():
        return _JSONResponseCompat(
            _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                is_git_repo=None,
                git_available=None,
                directory=path,
            ),
            status_code=200,
        )

    # ── 2. 路径安全校验(系统黑名单 + 用户黑名单) ──
    from .._path_safety import is_path_safe as _is_path_safe

    target = Path(path).resolve()
    blacklist = plugin._config.get("file_remove_blacklist")  # type: ignore[attr-defined]
    ok, safety_reason = _is_path_safe(target, user_blacklist=blacklist)
    if not ok:
        return _JSONResponseCompat(
            _make_envelope(
                success=False,
                reason=ReasonCode.PATH_UNSAFE,
                elapsed_ms=_elapsed(),
                is_git_repo=None,
                git_available=None,
                directory=str(target),
                stderr=safety_reason,
            ),
            status_code=200,
        )

    # ── 3. 目录存在性 ──
    if not target.is_dir():
        return _JSONResponseCompat(
            _make_envelope(
                success=False,
                reason=ReasonCode.PATH_NOT_DIRECTORY,
                elapsed_ms=_elapsed(),
                is_git_repo=None,
                git_available=None,
                directory=str(target),
            ),
            status_code=200,
        )

    # ── 4. git repo probe(核心) ──
    directory = str(target)
    git_bin = plugin._git_binary()  # type: ignore[attr-defined]
    probe = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )

    if probe["ok"] and probe.get("stdout", "").strip() == "true":
        # 是 git 仓库
        return _JSONResponseCompat(
            _make_envelope(
                success=True,
                reason=None,
                elapsed_ms=_elapsed(),
                is_git_repo=True,
                git_available=True,
                directory=directory,
            ),
            status_code=200,
        )

    # 探测失败:区分 not_a_git_repo / git_unavailable / git_error
    combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
    if "未安装" in probe.get("error", "") or "not installed" in combined:
        result_reason = ReasonCode.GIT_UNAVAILABLE
        is_git_repo: bool | None = None
        git_available = False
    elif "not a git repository" in combined:
        result_reason = ReasonCode.NOT_A_GIT_REPO
        is_git_repo = False
        git_available = True
    else:
        result_reason = ReasonCode.GIT_ERROR
        is_git_repo = None
        git_available = None

    return _JSONResponseCompat(
        _make_envelope(
            success=False,
            reason=result_reason,
            elapsed_ms=_elapsed(),
            is_git_repo=is_git_repo,
            git_available=git_available,
            directory=directory,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
        ),
        status_code=200,
    )
