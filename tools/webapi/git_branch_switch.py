"""POST /spcode/git-branch-switch — 切换当前分支(支持 create/detach/force)。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.5
v2.17.0 (2026-07-16) PR-F.

用 ``git switch`` 而非 ``git checkout``,语义更明确(项目 Git ≥ 2.30 假设,
spec §8)。8 步防御链(spec §3.5):body → name 校验 → start_point → 跨字段
→ preflight → worktree 状态探测 → git switch → 回读。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _classify_switch_stderr,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .._helpers import _is_valid_ref_name

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _validate_cross_fields(
    create: bool, force: bool, detach: bool, start_point,
) -> str | None:
    """create/detach/force/start_point 跨字段校验。返回 error msg 或 None。"""
    if create and detach:
        return "create and detach are mutually exclusive"
    if start_point is not None and not create:
        return "start_point only valid with create=true"
    return None


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-branch-switch handler.

    Body (JSON, 必传): ``{
        "name": "feature/x",
        "create": false, "start_point": "main",
        "force": false, "detach": false
    }``。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), switched=False, name="",
        )

    name = body.get("name")
    create = bool(body.get("create", False))
    start_point = body.get("start_point")
    force = bool(body.get("force", False))
    detach = bool(body.get("detach", False))

    if not isinstance(name, str) or not name:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=str(name or ""),
        )
    if not isinstance(create, bool) or not isinstance(force, bool) or not isinstance(detach, bool):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )
    if start_point is not None and not isinstance(start_point, str):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )

    # ── 2. ref-format 校验 ──
    if not _is_valid_ref_name(name):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BRANCH,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )
    if (
        create
        and start_point is not None
        and start_point != "HEAD"
        and not _is_valid_ref_name(start_point)
    ):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )

    # ── 3. 跨字段校验 ──
    cross_err = _validate_cross_fields(create, force, detach, start_point)
    if cross_err is not None:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), switched=False, name=name,
            stderr=cross_err,
        )

    # ── 4. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("switched", False)
        err["data"].setdefault("name", name)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 5. 探测 previous branch ──
    head_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
        encoding="utf-8",
    )
    head_out = head_result["stdout"].strip() if head_result.get("ok") else ""
    previous: str | None = (
        head_out if head_out and head_out != "HEAD" else None
    )

    # ── 6. worktree 状态探测(force 跳过) ──
    if not force:
        status_result = await _run_git_async(
            [git_bin, "-C", directory, "status", "--porcelain"],
            encoding="utf-8",
        )
        if status_result.get("ok") and status_result["stdout"].strip():
            return _make_envelope(
                success=False, reason=ReasonCode.WORKTREE_DIRTY,
                elapsed_ms=_elapsed(), switched=False, name=name,
                directory=directory, umo=effective_umo, worktree=directory,
                stderr="working tree has uncommitted changes",
            )

    # ── 7. git switch ──
    args: list[str] = [git_bin, "-C", directory, "switch"]
    if create:
        args.append("-c")
    if force and not create:
        args.append("-f")
    if detach:
        args.append("--detach")
    args.append(name)
    if create and start_point is not None and start_point != "HEAD":
        args.append(start_point)

    result = await _run_git_async(args, encoding="utf-8", timeout=15.0)
    if not result["ok"]:
        reason = _classify_switch_stderr(result.get("stderr", ""))
        logger.info(
            "git-branch-switch: failed %s (%s): %s",
            name, reason, result.get("stderr", "")[:200],
        )
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), switched=False, name=name,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    logger.info(
        "git-branch-switch: %s (from=%s, create=%s, force=%s, detach=%s)",
        name, previous, create, force, detach,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            switched=True, name=name, previous=previous,
            created=create, force=force, detach=detach,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
