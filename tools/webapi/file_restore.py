# tools/webapi/file_restore.py
"""POST /spcode/file-restore — 恢复/取消暂存单文件。

Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md
v3.6: scope 自动检测(基于 git status X/Y 列)。
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from .._helpers import _validate_worktree_param
from ..project import state as _proj_state
from ._helpers import _run_git_async, _validate_repo_relative_file

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _make_file_restore_empty_envelope(
    *,
    umo: str | None = None,
    file: str = "",
    directory: str | None = None,
    worktree: str | None = None,
    scope: str = "unstaged",
    reason: str,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造 ``/spcode/file-restore`` 失败路径的响应骨架。

    Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md §8
    """
    return {
        "status": "ok",
        "data": {
            "restored": False,
            "directory": directory,
            "umo": umo,
            "worktree": worktree or directory,
            "file": file,
            "scope": scope,
            "elapsed_ms": elapsed_ms,
            "stderr": stderr,
            "reason": reason,
        },
    }


def _make_file_restore_success_envelope(
    *,
    umo: str | None,
    file: str,
    directory: str,
    elapsed_ms: int,
    scope: str = "unstaged",
) -> dict:
    """构造 ``/spcode/file-restore`` 成功路径的响应骨架。

    Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md §3

    v3.6: ``scope`` 字段现在回显 handler 实际执行的 scope(``"unstaged"`` 或
    ``"staged"``),因为 handler 会基于 ``git status`` 自动检测。前端可据此
    展示 "已恢复工作区改动" vs "已取消暂存并回退到 HEAD"。
    """
    return {
        "status": "ok",
        "data": {
            "restored": True,
            "directory": directory,
            "umo": umo,
            "worktree": directory,
            "file": file,
            "scope": scope,
            "elapsed_ms": elapsed_ms,
            "stderr": "",
            "reason": None,
        },
    }


def _validate_restore_file(
    file_path: str,
    worktree: Path,
) -> tuple[Path | None, str | None]:
    """Deprecated 薄壳委托 — PR-1 起统一走 _validate_repo_relative_file。

    保留以保持与既有 from file_restore import _validate_restore_file 的
    测试代码 / 外部 import 兼容性。新代码应直接 import 共享函数。
    """
    return _validate_repo_relative_file(file_path, worktree)


async def handle(
    plugin: SPCodeToolkit,
) -> dict:
    """Web API handler for ``POST /spcode/file-restore``.

    接收 JSON body ``{"file": "<repo-rel>", "umo": "...", "worktree": "..."}``,
    返回 ``{"status": "ok", "data": {"restored": bool, "reason": str|None, ...}}``。

    v3.6: scope 自动检测。原 spec 限定 ``unstaged``,导致已暂存文件被
    ``git checkout -- <file>`` 静默 no-op(返回 0 但未修改任何东西)。
    现在通过 ``git status --porcelain`` 解析 X/Y 列:

    - X 列非空(``M``/``A``/``D``/``R``/``C``/``T``)→ 已暂存 → ``git checkout HEAD -- <file>``
      (同时重置 index + worktree 到 HEAD,即"完全撤销")
    - 仅 Y 列非空 → 未暂存 → ``git checkout -- <file>``
      (重置 worktree 到 index,保留既有行为)
    - 都不空(MM / AM / …)→ 视为 staged,执行完全撤销
    - 都为空 → ``not_modified``
    - ``??``/``!!`` → ``untracked_file``
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    from astrbot.api import web

    # 1. 读取 body(POST 协议)
    # NOTE: AstrBot 的 PluginRequestProxy 不提供 Flask 风格的 get_json,
    # 正确 API 是 ``await web.request.json(default=None)``,内部已
    # 捕获 JSON 解析异常并返回 default。
    try:
        body = await web.request.json(default=None)
    except Exception:
        body = None
    if not isinstance(body, dict):
        return _make_file_restore_empty_envelope(
            reason="invalid_body", elapsed_ms=_elapsed()
        )

    # 2. 提取 file 字段
    file_field = body.get("file", "")
    if not isinstance(file_field, str) or not file_field.strip():
        return _make_file_restore_empty_envelope(
            reason="missing_file", elapsed_ms=_elapsed()
        )
    file_path = file_field.strip()

    # 3. 提取 umo / worktree(留接口位;后续 task 接入)
    umo_raw = body.get("umo")
    umo = umo_raw if isinstance(umo_raw, str) and umo_raw.strip() else None
    wt_raw = body.get("worktree")
    worktree_param = wt_raw if isinstance(wt_raw, str) else None

    # 4. Feature flag 校验
    if not (
        plugin._config.get("agentsmd_enabled", True)
        and plugin._config.get("codegraph_enabled", True)
    ):
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="feature_disabled",
            elapsed_ms=_elapsed(),
        )

    # 5. umo 解析与回退(与 git-diff 完全相同)
    # PR-7 (2026-06-23): 数据源从 ``plugin._loaded_projects`` 迁移到
    # ``tools.project.state`` 模块级单例 + ``plugin.get_loaded_project()``。
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
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="no_project_loaded",
            elapsed_ms=_elapsed(),
        )
    directory = info.get("directory", "")

    # 6. worktree 校验(6 步防御,与 git-diff 完全相同)
    if worktree_param is not None and worktree_param.strip():
        validated_wt, wt_err = _validate_worktree_param(
            plugin._git_binary(), directory, worktree_param
        )
        if wt_err is not None:
            logger.warning(
                f"[file-restore] rejected ?worktree={worktree_param!r} "
                f"(loaded={directory!r})"
            )
            return _make_file_restore_empty_envelope(
                umo=umo,
                file=file_path,
                reason=wt_err,
                directory=directory,
                elapsed_ms=_elapsed(),
            )
        directory = validated_wt

    # 7. 目录存在性
    if not Path(directory).is_dir():
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="directory_missing",
            directory=directory,
            elapsed_ms=_elapsed(),
        )

    # 8. git repo probe
    probe = await _run_git_async(
        [plugin._git_binary(), "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            return _make_file_restore_empty_envelope(
                umo=umo,
                file=file_path,
                reason="not_a_git_repo",
                directory=directory,
                elapsed_ms=_elapsed(),
            )
        if "未安装" in probe.get("error", ""):
            return _make_file_restore_empty_envelope(
                umo=umo,
                file=file_path,
                reason="git_unavailable",
                directory=directory,
                elapsed_ms=_elapsed(),
            )
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="git_error",
            directory=directory,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
            elapsed_ms=_elapsed(),
        )

    # 9. file 路径安全校验(4 步防御)
    target, path_err = _validate_restore_file(file_path, Path(directory))
    if path_err is not None:
        logger.warning(
            f"[file-restore] rejected file={file_path!r} "
            f"(worktree={directory!r}): {path_err}"
        )
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="path_unsafe",
            directory=directory,
            elapsed_ms=_elapsed(),
        )

    # 10. file 存在性
    if not target.exists():
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="file_not_found",
            directory=directory,
            elapsed_ms=_elapsed(),
        )

    # 11. git status --porcelain 预检 + scope 自动检测
    # v3.6 改造:不仅区分 not_modified / untracked_file,还按 X/Y 列
    # 决定是执行 unstaged 恢复(``git checkout --``)还是 staged 完全撤销
    # (``git checkout HEAD --``)。原 v3.5 永远跑 ``git checkout --``,
    # 对已暂存文件是 no-op,导致假成功。
    git_bin = plugin._git_binary()
    status = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain", "--", file_path],
        encoding="utf-8",
    )
    if not status["ok"]:
        stderr = status.get("stderr", "") or status.get("error", "")
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="git_error",
            directory=directory,
            stderr=stderr,
            elapsed_ms=_elapsed(),
        )

    porcelain = status["stdout"]
    if not porcelain.strip():
        # working tree 与 index 一致 → 无可恢复
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="not_modified",
            directory=directory,
            elapsed_ms=_elapsed(),
        )

    first_line = porcelain.splitlines()[0] if porcelain else ""
    # git status --porcelain 格式:"XY <path>",X=index Y=worktree
    x_status = first_line[0] if len(first_line) >= 1 else " "
    y_status = first_line[1] if len(first_line) >= 2 else " "

    # 未跟踪文件(``?? path``)
    if x_status == "?" and y_status == "?":
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="untracked_file",
            directory=directory,
            stderr=porcelain,
            elapsed_ms=_elapsed(),
        )

    # scope 判定(v3.6,基于 porcelain v1 实测语义):
    #
    # | 场景               | porcelain  示例      | X   | Y   | 处理 |
    # |--------------------|----------------------|-----|-----|------|
    # | 仅 worktree 改动    | `` M path``          | ' ' | 'M' | git checkout -- |
    # | 已暂存(无 wt 改动) | ``M  path``          | 'M' | ' ' | git checkout HEAD -- |
    # | 已暂存 + wt 改动    | ``MM path``          | 'M' | 'M' | git checkout HEAD -- |
    # | intent-to-add      | `` A path``          | ' ' | 'A' | git reset HEAD -- |
    #
    # 关键陷阱(intent-to-add):
    # - X 是 ' ' 而**不是** 'A'(因为 index 还没真暂存内容,只是记了个意图)
    # - Y 是 'A'(worktree 有新文件)
    # - 不能用 ``git checkout --``:它会把 worktree 内容置空(index 里 blob 是空的)
    # - 必须用 ``git reset HEAD`` 取消意图(worktree 内容保留,文件变 untracked)
    _TRULY_STAGED_X = frozenset({"M", "D", "R", "C", "T"})
    _WORKTREE_Y = frozenset({"M", "A", "D", "R", "C", "T"})
    is_intent_to_add = x_status == " " and y_status == "A"
    is_truly_staged = x_status in _TRULY_STAGED_X
    is_worktree_dirty = y_status in _WORKTREE_Y

    if is_intent_to_add:
        scope = "unstaged"
        restore_cmd = [
            git_bin,
            "-C",
            directory,
            "-c",
            "color.ui=never",
            "reset",
            "HEAD",
            "--",
            file_path,
        ]
    elif is_truly_staged:
        # 真正已暂存(可能 worktree 也有改动)→ 完全恢复到 HEAD
        scope = "staged"
        restore_cmd = [
            git_bin,
            "-C",
            directory,
            "-c",
            "color.ui=never",
            "checkout",
            "HEAD",
            "--",
            file_path,
        ]
    elif is_worktree_dirty:
        # 仅 worktree 与 index 不同 → 既有行为
        scope = "unstaged"
        restore_cmd = [
            git_bin,
            "-C",
            directory,
            "-c",
            "color.ui=never",
            "checkout",
            "--",
            file_path,
        ]
    else:
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason="not_modified",
            directory=directory,
            elapsed_ms=_elapsed(),
        )

    # 12. 执行 restore 命令
    result = await _run_git_async(restore_cmd, encoding="utf-8")

    if not result["ok"]:
        stderr = result.get("stderr", "")
        lower = stderr.lower()
        if "did not match any file" in lower or "unknown revision" in lower:
            reason = "untracked_file"
        else:
            reason = "git_error"
        return _make_file_restore_empty_envelope(
            umo=umo,
            file=file_path,
            reason=reason,
            scope=scope,
            directory=directory,
            stderr=stderr,
            elapsed_ms=_elapsed(),
        )

    # 13. 成功:审计日志 + success envelope(scope 回显)
    logger.info(
        f"[file-restore] restored: file={file_path!r} scope={scope} "
        f"worktree={directory!r} umo={umo!r} elapsed_ms={_elapsed()}"
    )
    return _make_file_restore_success_envelope(
        umo=umo,
        file=file_path,
        directory=directory,
        scope=scope,
        elapsed_ms=_elapsed(),
    )
