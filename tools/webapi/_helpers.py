"""Shared helpers for tools/webapi/* endpoints.

Only imported by webapi/* handler modules. Do NOT import from main.py
(circular dependency) or tools/_helpers.py (cross-layer coupling).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from astrbot.api.web import JSONResponse  # _JSONResponseCompat 父类

# pythonw.exe 启动下抑制子进程弹 cmd 黑窗的统一直路常量。
# WHY: 见 tools/_helpers.py 中同名常量的定义;此处与 tools/_helpers.py
# 平行复刻,因为项目约定 webapi 层不依赖 tools._helpers。
_NO_WINDOW_KWARGS: dict[str, int] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)


# Binary file preview support (spec 2026-07-22 §4.5).
# Whitelist of extensions accepted by GET /spcode/file-binary; any
# other suffix → 415 unsupported_media_type. Keep the table in sync
# with the dashboard's <BinaryPreview> dispatcher.
MIME_BY_EXT: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}

# Hard cap for /spcode/file-binary. pdfjs / mammoth can OOM on files
# above this size in browser tabs; v1 rejects with 413.
FILE_BINARY_MAX_BYTES: int = 50 * 1024 * 1024  # 50 MB


# 从 main.py line 80-136 整体迁移,行为不变。
# 行为兼容 ``run_cmd`` 的返回 dict 格式,便于无侵入替换 ``run_sync(run_cmd, ...)``:
#   - 成功: {ok: True, stdout: str, stderr: str, code: int}
#   - 失败: {ok: False, error: str}(命令不存在 / 超时)
#   - 非零退出: {ok: False, stdout, stderr, code}(run_cmd 兼容)
async def _run_git_async(
    cmd_args: list[str],
    cwd: str = "",
    timeout: float = 15.0,
    encoding: str = "utf-8",
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    """Asyncio 真异步版本的 ``run_cmd``,用于 git 调用。

    Args:
        cmd_args: 子进程参数列表(首元素为可执行文件名)。
        cwd: 工作目录;空串则不设(用当前进程 cwd)。
        timeout: 子进程超时(秒);超时自动 kill 进程。
        encoding: stdout/stderr 解码用。
        input_text: (v3.7+) 若非空,作为 stdin 写入子进程(用于 ``git commit -F -``
            之类需要 stdin 输入的命令)。
        env: (v3.7+) 若非空,作为子进程 env 覆盖(默认继承父进程)。
            用于测试 monkeypatch GIT_AUTHOR_* 等场景。

    Returns:
        与 ``run_cmd`` 兼容的 dict。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
            env=env,
            # pythonw.exe 启动下抑制 cmd 黑窗;非 Windows 上为 {}
            **_NO_WINDOW_KWARGS,
        )
    except FileNotFoundError:
        cmd_name = cmd_args[0] if cmd_args else "command"
        return {"ok": False, "error": f"{cmd_name} 未安装或不在 PATH 中"}

    try:
        if input_text is not None:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=input_text.encode(encoding)),
                timeout=timeout,
            )
        else:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return {"ok": False, "error": f"命令超时 ({timeout}s)"}

    return {
        "ok": proc.returncode == 0,
        # 只去尾部换行(``, ``),**不**用 ``.strip()``:后者会把
        # ``git status --porcelain`` 第一列的 `` ``(X=未暂存)误删,
        # 导致下游解析 `` M`` → ``M`` 后误判为已暂存(v3.6 file-restore bug)。
        "stdout": stdout_bytes.decode(encoding, errors="replace").rstrip("\r\n"),
        "stderr": stderr_bytes.decode(encoding, errors="replace").rstrip("\r\n"),
        "code": proc.returncode,
    }


async def _run_git_async_bytes(
    cmd_args: list[str],
    cwd: str = "",
    timeout: float = 15.0,
) -> dict:
    """Asyncio async variant of ``_run_git_async`` that returns stdout as bytes.

    Used by /spcode/file-binary for git show <ref>:<path> where the blob
    may be binary (PDF / DOCX / XLSX) and utf-8 decode would lose data.
    Shares the subprocess plumbing with ``_run_git_async`` but skips the
    stdout decode step.

    Args:
        cmd_args: Subprocess argument list.
        cwd: Working directory; empty string keeps the parent's cwd.
        timeout: Subprocess timeout in seconds.

    Returns:
        Same shape as ``_run_git_async`` but with ``stdout`` typed as bytes.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
            **_NO_WINDOW_KWARGS,
        )
    except FileNotFoundError:
        cmd_name = cmd_args[0] if cmd_args else "command"
        return {"ok": False, "error": f"{cmd_name} 未安装或不在 PATH 中"}

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return {"ok": False, "error": f"命令超时 ({timeout}s)"}

    return {
        "ok": proc.returncode == 0,
        "stdout": stdout_bytes,  # bytes, not str
        "stderr": stderr_bytes.decode("utf-8", errors="replace").rstrip("\r\n"),
        "code": proc.returncode,
    }


class _JSONResponseCompat(JSONResponse):
    """JSONResponse 子类,补回 dict-like 访问能力。

    Why: AstrBot 框架的 ``_response_from_result`` 看到 ``Response`` 实例会原样
    透传(head/status_code 都被框架消化);但项目里的 web API 单元测试大量使用
    ``result["data"]`` 这种 dict 取值写法。继承 ``JSONResponse`` 既能享受
    framework 的 status_code/headers 注入,又保持现有测试无需重写。

    v3.3 (2026-06-21): 引入以支持 HTTP 缓存 (ETag/304 + Cache-Control)。
    从 main.py 整体迁移(2026-06-23),行为不变。
    """

    def __init__(
        self,
        content: Any,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content, status_code=status_code, headers=headers)
        self._content = content

    def __getitem__(self, key: str) -> Any:
        return self._content[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._content.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self._content

    def __iter__(self) -> Any:
        return iter(self._content)


class ReasonCode:
    """集中所有 webapi 端点的 reason 码字面量。

    PR-1 of git workflow endpoints design.
    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §4

    分类:
    - 通用前置类(10 端点共用)
    - body / param 校验类(POST 端点)
    - 文件路径 / 范围类(写端点 + log path 参数)
    """

    # ── 通用前置类 ──
    FEATURE_DISABLED = "feature_disabled"
    NO_PROJECT_LOADED = "no_project_loaded"
    WORKTREE_INVALID = "worktree_invalid"
    DIRECTORY_MISSING = "directory_missing"
    NOT_A_GIT_REPO = "not_a_git_repo"
    GIT_UNAVAILABLE = "git_unavailable"
    GIT_ERROR = "git_error"

    # ── body / param 校验类 ──
    INVALID_BODY = "invalid_body"
    INVALID_FILES = "invalid_files"
    INVALID_ALL = "invalid_all"
    EMPTY_MESSAGE = "empty_message"  # legacy alias for INVALID_MESSAGE
    MESSAGE_TOO_LONG = "message_too_long"  # legacy alias for INVALID_MESSAGE
    INVALID_MESSAGE = "invalid_message"  # PR-5: git-commit 统一 message 校验
    INVALID_PARAM = "invalid_param"

    # ── 文件路径 / 范围类 ──
    PATH_UNSAFE = "path_unsafe"
    NOTHING_STAGED = "nothing_staged"  # legacy alias for NOTHING_TO_COMMIT
    NOTHING_TO_COMMIT = "nothing_to_commit"  # PR-5: git-commit 无 staged 改动
    PRE_COMMIT_HOOK_FAILED = "pre_commit_hook_failed"  # legacy alias for HOOK_REJECTED
    HOOK_REJECTED = "hook_rejected"  # PR-5: pre-commit / commit-msg 拒绝
    IDENTITY_NOT_SET = "identity_not_set"  # PR-5: user.email / user.name 未设
    EMPTY_REPOSITORY = "empty_repository"
    REF_NOT_FOUND = (
        "ref_not_found"  # v3.8 (2026-06-25): git-show ref 不存在 / ambiguous
    )
    COMMIT_TOO_LARGE = "commit_too_large"  # v3.8 (2026-06-25): --numstat 输出超 1 MB

    # ── file-search 专用(v2.15.0,2026-07-02) ──
    SEARCH_UNAVAILABLE = (
        "search_unavailable"  # python_ripgrep 库未安装或内部 rg 调用失败
    )
    SEARCH_TIMEOUT = "search_timeout"  # 5s 超时
    INVALID_PATTERN = "invalid_pattern"  # pattern 为空 / 含换行 / 正则语法错
    PATTERN_TOO_LONG = "pattern_too_long"  # > 256 chars
    PATH_UNSAFE_FILTER = "path_unsafe_filter"  # path_filter 越界

    # ── v2.14.0 新增(worktree-mgmt 专用) ──
    INVALID_BRANCH = "invalid_branch"  # ADD: branch 格式非法
    PATH_EXISTS_NONEMPTY = "path_exists_nonempty"  # ADD: target 已存在非空
    CANNOT_CREATE_EXISTING = (
        "cannot_create_existing"  # ADD: branch 已存在 & create=false
    )
    CANNOT_CHECKOUT_MISSING = (
        "cannot_checkout_missing"  # ADD: branch 不存在 & create=false
    )
    WORKTREE_NOT_IN_REPO = "worktree_not_in_repo"  # ADD: post-create 防越权兜底
    WORKTREE_NOT_FOUND = "worktree_not_found"  # REMOVE/LOCK/UNLOCK: list 中查不到
    CANNOT_REMOVE_MAIN = "cannot_remove_main"  # REMOVE: 硬禁止删 main
    WORKTREE_LOCKED = "worktree_locked"  # REMOVE: target 已 locked
    WORKTREE_DIRTY = "worktree_dirty"  # REMOVE: 工作树有改动
    ALREADY_LOCKED = "already_locked"  # LOCK: 已 locked
    NOT_LOCKED = "not_locked"  # UNLOCK: 未 locked

    # ── /spcode/file-discard-hunk(v2.16.0,2026-07-06) ──
    PATCH_EMPTY = "patch_empty"
    PATCH_TOO_LARGE = "patch_too_large"
    PATCH_MALFORMED = "patch_malformed"
    PATCH_UNSAFE_PATH = "patch_unsafe_path"
    MULTI_FILE_PATCH = "multi_file_patch"
    PATCH_FILE_MISMATCH = "patch_file_mismatch"
    PATCH_BINARY = "patch_binary"
    PATCH_CHECK_FAILED = "patch_check_failed"
    PATCH_APPLY_FAILED = "patch_apply_failed"
    FILE_NOT_FOUND = (
        "file_not_found"  # Task 4 (2026-07-06): target 文件不存在于 worktree
    )
    NOT_MODIFIED = "not_modified"  # Task 5 (2026-07-06): porcelain 为空(已落盘无改动)
    UNTRACKED_FILE = "untracked_file"  # Task 5 (2026-07-06): porcelain X/Y = ??

    # ── /spcode/git-file + /spcode/docs 专用(spec B,2026-07-11) ──
    FILE_TOO_LARGE = "file_too_large"  # git-file: blob 超过 1 MB
    FILE_MISSING_AT_REF = (
        "file_missing_at_ref"  # git-file: ref 解析成功但 path 不在 ref 下
    )
    FILE_EXISTS = "file_exists"  # PATCH /spcode/docs: new_path 已存在

    # Added by v2170impl on 2026-07-16 09:26 CST.
    # ── v2.17.0 新增:git-init / branch / revert(2026-07-15) ──
    # git-init (4)
    PATH_NOT_DIRECTORY = "path_not_directory"  # init: 路径存在但不是目录
    DIRECTORY_NOT_EMPTY = "directory_not_empty"  # init: 目录里已有文件
    ALREADY_A_GIT_REPO = "already_a_git_repo"  # init: 目录里已有 .git/
    INIT_FAILED = "init_failed"  # init: git init 自身失败
    # git-branch-create (1)
    BRANCH_EXISTS = "branch_exists"  # create/switch: 分支已存在
    # git-branch-delete (3)
    BRANCH_NOT_FOUND = "branch_not_found"  # delete/switch: ref 不存在
    BRANCH_IS_CURRENT = "branch_is_current"  # delete: 试图删当前 HEAD 所在分支
    BRANCH_NOT_MERGED = "branch_not_merged"  # delete: -d 但分支未合并
    # git-branch-switch (shared WORKTREE_DIRTY above)
    # git-revert (3)
    COMMIT_NOT_FOUND = "commit_not_found"  # revert: <ref>^{commit} 解析失败
    REVERT_CONFLICT = "revert_conflict"  # revert: 反向 patch 与 worktree 冲突
    NOTHING_TO_REVERT = "nothing_to_revert"  # revert: ref 无改动可被反

    # ── /spcode/btw(2026-07-17) ──
    NO_PROVIDER = "no_provider"  # 无可用 LLM Provider
    EMPTY_RESPONSE = "empty_response"  # LLM 返回空文本
    LLM_ERROR = "llm_error"  # LLM 调用异常(provider.text_chat 抛错)

    # ── /spcode/file-binary(spec 2026-07-22) ──
    UNSUPPORTED_MEDIA_TYPE = (
        "unsupported_media_type"  # file-binary: extension not in MIME_BY_EXT
    )
    INTERNAL_ERROR = "internal_error"  # file-binary: unhandled exception


# ── git status --porcelain X/Y 列判定(共享常量)────────────────────
# 从 tools/webapi/file_restore.py 提取(2026-07-06)。两个端点共用:
# - /spcode/file-restore: 决定 `git checkout --` vs `git checkout HEAD --` vs `git reset HEAD --`
# - /spcode/file-discard-hunk: 决定 `git apply --reverse` vs `git apply --reverse --cached`
X_TRULY_STAGED: frozenset[str] = frozenset({"M", "D", "R", "C", "T"})
Y_WORKTREE: frozenset[str] = frozenset({"M", "A", "D", "R", "C", "T"})


def _make_envelope(
    *,
    success: bool,
    reason: str | None = None,
    stderr: str = "",
    elapsed_ms: int = 0,
    **data_fields: object,
) -> dict:
    """统一 envelope 工厂。

    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §3

    Args:
        success: True → reason 强制为 None;False → reason 必传。
        reason: failure 路径的 reason 码(取 ``ReasonCode.X``)。
        stderr: 写端点 hook 失败时的截断 stderr(读端点不使用)。
        elapsed_ms: handler 端到端耗时(毫秒)。
        **data_fields: 端点特定的 data 字段(如 staged/files/committed/loaded)。

    Returns:
        ``{"status": "ok", "data": {<data_fields + reason + stderr + elapsed_ms>}}``
    """
    return {
        "status": "ok",
        "data": {
            **data_fields,
            "reason": None if success else reason,
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
        },
    }


async def _git_endpoint_preflight(
    plugin: object,
    *,
    umo: str | None,
    worktree_param: str | None,
    feature_flags: tuple[str, ...] = ("agentsmd_enabled", "codegraph_enabled"),
) -> tuple[dict | None, dict | None]:
    """Git 端点 5 步前置检查(供所有 git-* 端点共用)。

    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §2

    5 步:
      1. feature flag(任一为 false → feature_disabled)
      2. umo 解析 + 回退最近 loaded project
      3. worktree 6 步防御(若 worktree_param 非空)
      4. 目录存在性检查
      5. git 仓库探测(rev-parse --is-inside-work-tree)

    Args:
        plugin: SPCodeToolkit 实例(只需 ._config / .get_loaded_project / ._git_binary)
        umo: 请求中的 umo 参数(可能 None)
        worktree_param: 请求中的 ?worktree= 参数(可能 None / 空)
        feature_flags: feature flag 字段名元组,默认 ("agentsmd_enabled", "codegraph_enabled")

    Returns:
        (error_envelope, None)  — 前置失败
        (None, ctx)  — 前置成功;ctx = {"directory", "umo", "worktree"}
    """
    from .._helpers import _validate_worktree_param
    from ..project import state as _proj_state

    # 1. feature flag
    cfg = plugin._config  # type: ignore[attr-defined]
    for flag in feature_flags:
        if not cfg.get(flag, True):
            return _make_envelope(
                success=False,
                reason=ReasonCode.FEATURE_DISABLED,
                elapsed_ms=0,
                umo=umo,
                worktree=worktree_param,
            ), None

    # 2. umo 解析 + 回退
    info = None
    if umo:
        info = plugin.get_loaded_project(umo)  # type: ignore[attr-defined]
    else:
        items = _proj_state.items()
        if items:
            umo, info = max(items.items(), key=lambda kv: kv[1].get("loaded_at", 0.0))
    if info is None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.NO_PROJECT_LOADED,
            elapsed_ms=0,
            umo=umo,
            worktree=worktree_param,
        ), None

    directory = info.get("directory", "")

    # 3. worktree 6 步防御
    if worktree_param is not None and worktree_param.strip():
        validated_wt, wt_err = _validate_worktree_param(
            plugin._git_binary(),  # type: ignore[attr-defined]
            directory,
            worktree_param,
        )
        if wt_err is not None:
            return _make_envelope(
                success=False,
                reason=ReasonCode.WORKTREE_INVALID,
                elapsed_ms=0,
                umo=umo,
                worktree=worktree_param,
                directory=directory,
            ), None
        directory = validated_wt

    # 4. 目录存在性
    if not Path(directory).is_dir():
        return _make_envelope(
            success=False,
            reason=ReasonCode.DIRECTORY_MISSING,
            elapsed_ms=0,
            umo=umo,
            worktree=directory,
            directory=directory,
        ), None

    # 5. git repo probe
    probe = await _run_git_async(
        [plugin._git_binary(), "-C", directory, "rev-parse", "--is-inside-work-tree"],  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            reason = ReasonCode.NOT_A_GIT_REPO
        elif "未安装" in probe.get("error", ""):
            reason = ReasonCode.GIT_UNAVAILABLE
        else:
            reason = ReasonCode.GIT_ERROR
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=0,
            umo=umo,
            worktree=directory,
            directory=directory,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
        ), None

    return None, {
        "directory": directory,
        "umo": umo,
        "worktree": directory,
    }


# Added by v2170impl on 2026-07-16 09:26 CST.
async def _git_init_preflight(
    plugin: object,
    *,
    path: str,
    force: bool = False,
) -> tuple[dict | None, dict | None]:
    """Run the git-init-only path preflight.

    Unlike ``_git_endpoint_preflight``, this helper does not resolve a loaded
    project or probe for an existing Git repository.

    ``force=True`` (v2.17.1) skips the ``directory_not_empty`` check, allowing
    initialization of a non-empty directory. It does NOT bypass
    ``already_a_git_repo`` (hard-ban), ``path_not_directory``, or
    ``path_unsafe`` - those are unconditional safety guards.
    """
    from .._path_safety import is_path_safe as _is_path_safe

    if not isinstance(path, str) or not path.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=0,
            path=path or "",
        ), None

    target = Path(path).resolve()
    if not Path(path).is_absolute():
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=0,
            path=path,
        ), None

    blacklist = plugin._config.get("file_remove_blacklist")  # type: ignore[attr-defined]
    ok, safety_reason = _is_path_safe(target, user_blacklist=blacklist)
    if not ok:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=0,
            path=path,
            stderr=safety_reason,
        ), None

    if not target.exists() or not target.is_dir():
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_NOT_DIRECTORY,
            elapsed_ms=0,
            path=path,
        ), None

    # Existing repositories get their dedicated reason before non-empty.
    if (target / ".git").exists():
        return _make_envelope(
            success=False,
            reason=ReasonCode.ALREADY_A_GIT_REPO,
            elapsed_ms=0,
            path=path,
        ), None

    # v2.17.1: force=True 跳过非空检查(允许在已有代码目录 init)。
    # already_a_git_repo 检查在上面已完成,force 不绕过 hard-ban。
    if not force and any(target.iterdir()):
        return _make_envelope(
            success=False,
            reason=ReasonCode.DIRECTORY_NOT_EMPTY,
            elapsed_ms=0,
            path=path,
        ), None

    return None, {"path": str(target)}


def _classify_switch_stderr(stderr: str) -> str:
    """将 ``git switch`` 失败 stderr 映射到 ReasonCode。

    真实 git 2.30+ stdout/stderr 实测模式:
    - ``fatal: invalid reference: <name>`` (BRANCH_NOT_FOUND)
    - ``error: pathspec '<name>' did not match any file(s) known to git`` (BRANCH_NOT_FOUND)
    - ``fatal: a branch named '<name>' already exists`` (BRANCH_EXISTS)
    - ``Your local changes ... would be overwritten`` (WORKTREE_DIRTY)
    - ``fatal: '<name>' is not a valid branch name`` (INVALID_BRANCH)
    """
    s = stderr.lower()
    if "already exists" in s:
        return ReasonCode.BRANCH_EXISTS
    # 'invalid reference' / 'did not match' / 'not found' — 三种 git switch 失败模式
    if "invalid reference" in s or "did not match" in s or "not found" in s:
        return ReasonCode.BRANCH_NOT_FOUND
    if "your local changes" in s or "would be overwritten" in s:
        return ReasonCode.WORKTREE_DIRTY
    if "not a valid branch name" in s:
        return ReasonCode.INVALID_BRANCH
    return ReasonCode.GIT_ERROR


def _classify_revert_stderr(stderr: str) -> str:
    """将 ``git revert`` 失败 stderr 映射到 ReasonCode。"""
    from .git_commit import _classify_commit_error

    classified = _classify_commit_error(stderr, returncode=-1)
    if classified != ReasonCode.GIT_ERROR:
        return classified
    s = stderr.lower()
    if "merge conflict" in s or "conflict" in s:
        return ReasonCode.REVERT_CONFLICT
    if "nothing to revert" in s or "no changes" in s:
        return ReasonCode.NOTHING_TO_REVERT
    if "your local changes" in s or "would be overwritten" in s:
        return ReasonCode.WORKTREE_DIRTY
    return ReasonCode.GIT_ERROR


def _validate_repo_relative_file(
    file_path: str,
    repo_root: Path,
) -> tuple[Path | None, str | None]:
    """4-step defense for repository-relative file paths.

    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §1.4

    4 steps:
      1. Normalize Windows ``\\`` separators to POSIX ``/``; reject
         absolute paths and ".." segments.
      2. Resolve relative to ``repo_root`` and ensure target stays inside.
      3. Reject paths containing a ``.git`` component.
      4. Reject symlinks whose realpath escapes ``repo_root``.

    Returns:
        (resolved_path, None) on success; (None, "path_unsafe") on rejection.

    v3.x: extracted from tools/webapi/file_restore._validate_restore_file in PR-1.
    2026-07-09: Step 1 now NORMALIZES ``\\`` → ``/`` instead of rejecting
        backslash-bearing paths. Rationale: `str(Path(...))` on Windows
        yields backslash paths (e.g. ``dashboard\\src\\x.vue``), and
        file-browser callers legitimately pass those through. Git itself
        accepts either separator, so the previous reject-everything rule
        broke file-level history (and any other endpoint) on Windows for
        zero security gain: steps 2-4 still bound the path to ``repo_root``
        and reject ``..`` traversal after the normalization. See the
        ``test_normalizes_backslash_to_forward_slash`` regression test.
    """
    if not file_path:
        return None, "path_unsafe"

    # Step 1: format
    #
    # On Windows, ``str(Path("a/b"))`` returns ``"a\\b"`` (or vice-versa).
    # We accept either separator by normalizing, then run the rest of
    # the defense with POSIX-style paths. This keeps callers
    # cross-platform without weakening any of the security steps:
    #   - Leading ``/`` or ``\\`` is still rejected (catches ``/etc/passwd``,
    #     UNC ``\\\\server\\share``, Windows drive-rooted ``C:\\...`` that
    #     survived the slash normalization).
    #   - ``..`` segments are still rejected via the split-based check.
    #   - Steps 2-4 are path-system-agnostic and bind the resolved target
    #     to ``repo_root`` regardless of the input separator style.
    file_path = file_path.replace("\\", "/")
    if file_path.startswith("/"):
        return None, "path_unsafe"
    if ".." in file_path.split("/"):
        return None, "path_unsafe"

    # Step 2: resolve into repo_root
    root_resolved = repo_root.resolve()
    target = (root_resolved / file_path).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return None, "path_unsafe"

    # Step 3: reject .git internals
    if any(part == ".git" for part in target.parts):
        return None, "path_unsafe"

    # Step 4: symlink defense (realpath must equal target)
    real = os.path.realpath(target)
    if os.path.normcase(real) != os.path.normcase(str(target)):
        return None, "path_unsafe"

    return target, None


# ──────────────────────────────────────────────────────────
# v3.5 (2026-06-30) ETag staleness 修复: 3 路 porcelain 探测
# ──────────────────────────────────────────────────────────
# 旧 ETag 算法只用 worktree 根目录 mtime + .git/index mtime + HEAD SHA,
# 当用户在工作树里编辑文件 (不 git add) 或新建未跟踪文件时:
#   - worktree 根目录 mtime 不变
#   - .git/index mtime 不变 (没 git add)
#   - HEAD SHA 不变 (没 commit)
# → ETag 完全不变 → dashboard 持续 304 → 看到 stale 数据。
# Plan A 修复: 在 ETag 中加入 3 路 git 探测,捕获所有可能改动。
#   - git diff-files --name-only -z           未暂存改动 (worktree vs index)
#   - git diff-index --cached --name-only -z HEAD  已暂存改动 (index vs HEAD)
#   - git ls-files --others --exclude-standard -z  未跟踪文件
# Spec: docs/superpowers/specs/2026-06-30-git-etag-staleness-fix.md


async def _compute_porcelain_diffs(
    git_bin: str,
    directory: str,
) -> tuple[str, str, str]:
    """3 路 porcelain 探测,返回 (unstaged, staged, untracked) 文件名列表。

    每个输出都是 ``-z`` 分隔的文件名列表(NUL 结尾)。失败时退化为空串,
    调用方 hash 后仍然稳定。

    性能: 3 个 git 子进程串行跑,各 5-15ms,总 ~20-40ms。
    相比旧算法只跑 1 个 ``rev-parse HEAD``(~10ms),增加 ~20-30ms,
    换来 ETag 真正的"文件级"精确性(不再 staleness)。

    WHY 串行而非 ``asyncio.gather`` 并发: 3 个 git 进程互相竞争
    work tree 读 I/O,串行走 page cache 反而更快更省 CPU,
    与 git_diff.py v3.4 (2026-06-21) P2 perf 注释一致。
    """
    common_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # 1. 未暂存改动 (worktree vs index)
    r1 = await _run_git_async(
        common_prefix + ["diff-files", "--name-only", "-z"],
        timeout=5.0,
        encoding="utf-8",
    )
    unstaged = r1.get("stdout", "") if r1.get("ok") else ""

    # 2. 已暂存改动 (index vs HEAD)
    r2 = await _run_git_async(
        common_prefix + ["diff-index", "--cached", "--name-only", "-z", "HEAD"],
        timeout=5.0,
        encoding="utf-8",
    )
    staged = r2.get("stdout", "") if r2.get("ok") else ""

    # 3. 未跟踪文件
    r3 = await _run_git_async(
        common_prefix + ["ls-files", "--others", "--exclude-standard", "-z"],
        timeout=5.0,
        encoding="utf-8",
    )
    untracked = r3.get("stdout", "") if r3.get("ok") else ""

    return unstaged, staged, untracked


async def _compute_git_etag(git_bin: str, directory: str) -> str:
    """统一 ETag 计算 (git-diff / git-status / git-log 共享)。

    v3.5 (2026-06-30) 修复 ETag staleness: 在旧算法的 HEAD SHA + wt_mtime
    + idx_mtime 基础上, 加入 3 路 porcelain 探测的 SHA-1 哈希。

    组成: ``W/"<head_sha>-<wt_mtime>-<porcelain_sha>"``
    - ``head_sha``: commit 变化时变
    - ``wt_mtime``: 防御性保留,git 命令失败时仍能感知根目录时间变化
    - ``porcelain_sha``: 文件级真实变化 (3 路探测合并哈希)

    任意一项变化 → 整个 ETag 变化 → dashboard 收到 200 + 新数据。
    """
    # 1. HEAD SHA
    head_sha = "no-head"
    try:
        head_result = await _run_git_async(
            [git_bin, "-C", directory, "rev-parse", "HEAD"],
            timeout=5.0,
            encoding="utf-8",
        )
        if head_result.get("ok") and head_result.get("stdout"):
            head_sha = head_result["stdout"].strip()
    except Exception:
        pass

    # 2. worktree 根 mtime (defensive, git 命令全部失败时仍能感知)
    wt_mtime = 0
    try:
        wt_mtime = int(Path(directory).stat().st_mtime)
    except OSError:
        pass

    # 3. 3 路 porcelain 探测 → SHA-1 哈希 (核心 v3.5 修复)
    unstaged, staged, untracked = await _compute_porcelain_diffs(git_bin, directory)
    porcelain_src = f"{unstaged}\x00{staged}\x00{untracked}"
    porcelain_sha = hashlib.sha1(porcelain_src.encode("utf-8")).hexdigest()[:16]

    return f'W/"{head_sha}-{wt_mtime}-{porcelain_sha}"'


# ── git for-each-ref 解析与 mutation 后分支状态读取(共享) ───────
# v2.17.0 三个 mutating branch handler (switch / create / delete) 在成功路径
# 需要返回 refreshed branches list + current + detached(spec §3.2 前端原子
# 替换 state 所依赖)。git_branches.py (GET) 原本独占 _parse_for_each_ref;
# v2.17.0 L8 修复把它和新的 _read_post_mutation_branch_state 一起搬到本
# _helpers.py,供 3 个 mutating handler 共享。
#
# Why spec §3.5 L8 line 492 "成功后回读 current branch" 步骤在 v2.17.0
# release 漏实现,导致 dashboard 切换分支后误显示 "detached HEAD"
# (必须刷新才能看到真实状态)。修复:用 for-each-ref + %(HEAD) 标记找
# current branch;%(HEAD) 不匹配任何 branch ref 时,补 ``git rev-parse
# --abbrev-ref HEAD`` 探测 detached HEAD。

_FOR_EACH_REF_FORMAT: str = (
    "%(if)%(HEAD)%(then)*%(else) %(end)"
    "%(refname:short)%09%(objectname:short)%09"
    "%(upstream:short)%09%(upstream:track)"
)


def _parse_for_each_ref(raw: str) -> list[dict[str, Any]]:
    """解析 ``git for-each-ref`` 输出为 branch 字典列表。

    格式(每行 tab 分隔):
        [*| ]<name><TAB><sha><TAB><upstream><TAB><track>

    Args:
        raw: ``git for-each-ref --format=... refs/heads/ refs/remotes/``
            的 stdout 原文。

    Returns:
        list of dicts with keys: name, sha, upstream, upstream_track,
        current, remote。

    Note:
        行首位置字符携带 ``current`` 信息(``*`` 表示 HEAD 指向该 branch),
        解析时**不能**对整行 ``.strip()``,否则会丢失这个标记。
    """
    result: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        # 去掉行尾空白(\r, \n),保留行首位置字符
        line_clean = line.rstrip("\r\n")
        parts = line_clean.split("\t")
        if len(parts) < 2:
            continue

        name_part = parts[0]  # e.g. "*main" or " feature/x"
        is_current = name_part.startswith("*")
        # 去掉首字符(* 或 空格),然后 .strip() 处理额外空白
        name = name_part[1:].strip()

        sha = parts[1].strip() if len(parts) > 1 else ""
        upstream = parts[2].strip() if len(parts) > 2 else ""
        upstream_track = parts[3].strip() if len(parts) > 3 else ""

        result.append(
            {
                "name": name,
                "sha": sha,
                "upstream": upstream,
                "upstream_track": upstream_track,
                "current": is_current,
                "remote": name.startswith("origin/") or name.startswith("remotes/"),
            }
        )

    return result


async def _read_post_mutation_branch_state(
    git_bin: str,
    directory: str,
) -> dict[str, Any]:
    """读取 mutation 后的分支状态,供 mutating handler 附加到成功响应。

    spec §3.5 L8 "成功后回读 current branch" + spec §3.2 前端原子替换
    state 需要 mutation handler 响应含 ``branches`` / ``total`` / ``current``
    / ``detached`` 字段。本 helper 走 ``git for-each-ref`` 路径,与
    GET ``/spcode/git-branches`` 共用解析器,保证响应 shape 一致。

    Args:
        git_bin: git 可执行文件路径(``plugin._git_binary()``)。
        directory: 仓库根绝对路径。

    Returns:
        dict 含 keys:
          - ``branches`` (list[dict]): 完整分支列表(``_parse_for_each_ref`` 输出)
          - ``total`` (int): ``len(branches)``
          - ``current`` (str | None): 当前 HEAD 指向的分支名;detached 时为 None
          - ``detached`` (bool): HEAD 未指向任何 branch ref(可能在 commit 上)

    Note:
        故意不抛异常 — mutation 业务逻辑已成功(branch switch / create / delete
        完成),即便辅助读失败也必须返回可空 dict,前端能拿到 200 + 业务字段,
        后续 polling 修复后续状态。返回全空 + ``detached=False`` 是最安全的
        降级 — 前端下次 GET /spcode/git-branches 会自动同步。
    """
    branches: list[dict[str, Any]] = []
    current_name: str | None = None
    try:
        result = await _run_git_async(
            [
                git_bin,
                "-C",
                directory,
                "for-each-ref",
                f"--format={_FOR_EACH_REF_FORMAT}",
                "refs/heads/",
                "refs/remotes/",
            ],
            timeout=5.0,
            encoding="utf-8",
        )
        if result.get("ok"):
            branches = _parse_for_each_ref(result.get("stdout", ""))
            for b in branches:
                if b["current"]:
                    current_name = b["name"]
                    break
    except Exception:
        branches = []

    detached = False
    if current_name is None:
        # for-each-ref 没找到 %(HEAD) 标记的 branch ref → 可能 detached HEAD
        try:
            head_result = await _run_git_async(
                [git_bin, "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
                timeout=5.0,
                encoding="utf-8",
            )
            if head_result.get("ok"):
                head_ref = head_result.get("stdout", "").strip()
                if head_ref == "HEAD":
                    detached = True
        except Exception:
            pass

    return {
        "branches": branches,
        "total": len(branches),
        "current": current_name,
        "detached": detached,
    }
