"""Shared helpers for tools/webapi/* endpoints.

Only imported by webapi/* handler modules. Do NOT import from main.py
(circular dependency) or tools/_helpers.py (cross-layer coupling).
"""
from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import Any, Mapping

from astrbot.api.web import JSONResponse  # _JSONResponseCompat 父类


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
                proc.communicate(), timeout=timeout,
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
    EMPTY_MESSAGE = "empty_message"          # legacy alias for INVALID_MESSAGE
    MESSAGE_TOO_LONG = "message_too_long"    # legacy alias for INVALID_MESSAGE
    INVALID_MESSAGE = "invalid_message"      # PR-5: git-commit 统一 message 校验
    INVALID_PARAM = "invalid_param"

    # ── 文件路径 / 范围类 ──
    PATH_UNSAFE = "path_unsafe"
    NOTHING_STAGED = "nothing_staged"        # legacy alias for NOTHING_TO_COMMIT
    NOTHING_TO_COMMIT = "nothing_to_commit"  # PR-5: git-commit 无 staged 改动
    PRE_COMMIT_HOOK_FAILED = "pre_commit_hook_failed"  # legacy alias for HOOK_REJECTED
    HOOK_REJECTED = "hook_rejected"          # PR-5: pre-commit / commit-msg 拒绝
    IDENTITY_NOT_SET = "identity_not_set"    # PR-5: user.email / user.name 未设
    EMPTY_REPOSITORY = "empty_repository"
    REF_NOT_FOUND = "ref_not_found"          # v3.8 (2026-06-25): git-show ref 不存在 / ambiguous
    COMMIT_TOO_LARGE = "commit_too_large"    # v3.8 (2026-06-25): --numstat 输出超 1 MB

    # ── v2.14.0 新增(worktree-mgmt 专用) ──
    INVALID_BRANCH         = "invalid_branch"           # ADD: branch 格式非法
    PATH_EXISTS_NONEMPTY   = "path_exists_nonempty"     # ADD: target 已存在非空
    CANNOT_CREATE_EXISTING = "cannot_create_existing"   # ADD: branch 已存在 & create=false
    CANNOT_CHECKOUT_MISSING = "cannot_checkout_missing" # ADD: branch 不存在 & create=false
    WORKTREE_NOT_IN_REPO   = "worktree_not_in_repo"     # ADD: post-create 防越权兜底
    WORKTREE_NOT_FOUND     = "worktree_not_found"       # REMOVE/LOCK/UNLOCK: list 中查不到
    CANNOT_REMOVE_MAIN     = "cannot_remove_main"       # REMOVE: 硬禁止删 main
    WORKTREE_LOCKED        = "worktree_locked"          # REMOVE: target 已 locked
    WORKTREE_DIRTY         = "worktree_dirty"           # REMOVE: 工作树有改动
    ALREADY_LOCKED         = "already_locked"           # LOCK: 已 locked
    NOT_LOCKED             = "not_locked"               # UNLOCK: 未 locked


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
                success=False, reason=ReasonCode.FEATURE_DISABLED,
                elapsed_ms=0, umo=umo, worktree=worktree_param,
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
            success=False, reason=ReasonCode.NO_PROJECT_LOADED,
            elapsed_ms=0, umo=umo, worktree=worktree_param,
        ), None

    directory = info.get("directory", "")

    # 3. worktree 6 步防御
    if worktree_param is not None and worktree_param.strip():
        validated_wt, wt_err = _validate_worktree_param(
            plugin._git_binary(),  # type: ignore[attr-defined]
            directory, worktree_param,
        )
        if wt_err is not None:
            return _make_envelope(
                success=False, reason=ReasonCode.WORKTREE_INVALID,
                elapsed_ms=0, umo=umo, worktree=worktree_param, directory=directory,
            ), None
        directory = validated_wt

    # 4. 目录存在性
    if not Path(directory).is_dir():
        return _make_envelope(
            success=False, reason=ReasonCode.DIRECTORY_MISSING,
            elapsed_ms=0, umo=umo, worktree=directory, directory=directory,
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
            success=False, reason=reason,
            elapsed_ms=0, umo=umo, worktree=directory, directory=directory,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
        ), None

    return None, {
        "directory": directory,
        "umo": umo,
        "worktree": directory,
    }


def _validate_repo_relative_file(
    file_path: str,
    repo_root: Path,
) -> tuple[Path | None, str | None]:
    """4-step defense for repository-relative file paths.

    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §1.4

    4 steps:
      1. Reject absolute paths (POSIX "/" or Windows "\\") and ".." segments;
         force POSIX-style forward slashes only.
      2. Resolve relative to ``repo_root`` and ensure target stays inside.
      3. Reject paths containing a ``.git`` component.
      4. Reject symlinks whose realpath escapes ``repo_root``.

    Returns:
        (resolved_path, None) on success; (None, "path_unsafe") on rejection.

    v3.x: extracted from tools/webapi/file_restore._validate_restore_file in PR-1.
    """
    if not file_path:
        return None, "path_unsafe"

    # Step 1: format
    if file_path.startswith("/") or file_path.startswith("\\"):
        return None, "path_unsafe"
    if "\\" in file_path:  # 强制 POSIX 风格
        return None, "path_unsafe"
    if ".." in file_path.replace("\\", "/").split("/"):
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
