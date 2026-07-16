"""POST /spcode/git-init — 在已存在空目录上 git init。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.1
v2.17.0 (2026-07-15).

**关键差异**:本端点是 webapi 中**唯一**完全豁免 ``_git_endpoint_preflight`` 的
写端点 —— 因为 preflight 步骤 5 会跑 ``git rev-parse --is-inside-work-tree``,
对非仓库必然返回 ``not_a_git_repo``,导致 init 永远失败。

改走 ``_git_init_preflight``(4 步,无 umo 解析、无 git repo 探测)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from . import _helpers as _helpers_module
from ._helpers import (
    ReasonCode,
    _git_init_preflight,
    _JSONResponseCompat,
    _make_envelope,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# git init -b 在 git 2.28+ 才支持,旧版本 fallback
MIN_GIT_VERSION_FOR_INIT_B = (2, 28)


def _git_supports_init_b(git_bin: str) -> bool:
    """检查 git 是否支持 ``git init -b <branch>``(需要 2.28+)。"""
    import re
    import subprocess
    try:
        result = subprocess.run(
            [git_bin, "--version"], capture_output=True, text=True
        )
    except (FileNotFoundError, OSError):
        # git 二进制不存在或不可执行 — 走 fallback 路径
        return False
    if result.returncode != 0:
        return False
    m = re.search(r"git version (\d+)\.(\d+)", result.stdout)
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2))) >= MIN_GIT_VERSION_FOR_INIT_B


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-init handler.

    Body (JSON, 必传): ``{"path": "...", "initial_branch": "main", "bare": false}``。
    ``umo`` / ``worktree`` 字段虽在 _wrap 中注入,但本端点不使用(走独立 preflight)。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            initialized=False,
            path="",
        )

    path = body.get("path")
    initial_branch = body.get("initial_branch", "main")
    bare = body.get("bare", False)

    # ── 2. 参数类型校验 ──
    if not isinstance(path, str):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=str(path or ""),
        )
    # v2.17.0 deviation: plan verbatim says return invalid_param when path is empty,
    # but tests expect path_unsafe (an empty path cannot be safely resolved against
    # blacklists). Empty / whitespace-only path is treated as path_unsafe so the
    # caller gets a clear "this path cannot be initialized" signal.
    if not path or not path.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=path,
        )
    if not isinstance(initial_branch, str):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=path,
        )
    if not isinstance(bare, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=path,
        )

    # ── 3. init-only preflight (4 步) ──
    err, ctx = await _git_init_preflight(plugin, path=path)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("initialized", False)
        return err
    abs_path = ctx["path"]

    # ── 4. git init 子进程 ──
    # WHY: 通过 _helpers_module 间接调用 _run_git_async 而非直接 imported name,
    # 让 tests 通过 ``monkeypatch.setattr(tools.webapi._helpers,
    # "_run_git_async", ...)`` 替换实现时仍然生效(直接 imported name 在
    # module load 时就被 bind 到本地 namespace,monkeypatch 改 module attr
    # 不会反映到 imported name)。
    git_bin = plugin._git_binary()
    args: list[str] = [git_bin, "init"]
    if bare:
        args.append("--bare")
    if _git_supports_init_b(git_bin):
        args.extend(["-b", initial_branch])
    args.append(abs_path)

    result = await _helpers_module._run_git_async(args, encoding="utf-8", timeout=30.0)

    # 兜底:旧版 git 无 -b,init 完手动 symbolic-ref
    if result["ok"] and not _git_supports_init_b(git_bin):
        ref_result = await _helpers_module._run_git_async(
            [git_bin, "-C", abs_path, "symbolic-ref", "HEAD", f"refs/heads/{initial_branch}"],
            encoding="utf-8",
        )
        if not ref_result["ok"]:
            logger.warning(
                "git init fallback symbolic-ref 失败: %s", ref_result.get("stderr", "")
            )

    if not result["ok"]:
        stderr = result.get("stderr", "") or result.get("error", "")
        # 探测 git 二进制可用性
        reason = ReasonCode.INIT_FAILED
        if "未安装" in result.get("error", ""):
            reason = ReasonCode.GIT_UNAVAILABLE
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=abs_path,
            stderr=stderr[:4096],
        )

    git_dir = f"{abs_path}/.git" if not bare else f"{abs_path}"
    logger.info("git init 成功: %s (branch=%s, bare=%s)", abs_path, initial_branch, bare)
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            initialized=True,
            path=abs_path,
            initial_branch=initial_branch,
            bare=bare,
            git_dir=git_dir,
            umo=umo,
            worktree="",
        ),
        status_code=200,
    )