"""GET/POST /spcode/git-stash + POST /spcode/git-stash-pop — git stash 管理。

2026-08-21 (elecvoid243): 供 Dashboard GitDiffSidebar 的 stash 入口。

  * GET  /spcode/git-stash      — 列出 stash 条目(默认最近 50 条),每条带
                                  文件级 numstat 明细(tracked 改动来自
                                  ``git diff <sha>^ <sha>``,``-u`` 贮藏的
                                  untracked 文件来自 ``stash^3`` 提交树)。
  * POST /spcode/git-stash      — ``git stash push -u -m <message>``
                                  (含未跟踪文件;message 可选)。
  * POST /spcode/git-stash-pop  — ``git stash pop stash@{index}``(应用回
                                  工作区并删除该条;冲突时条目保留)。
  * POST /spcode/git-stash-drop — ``git stash drop stash@{index}``(删除
                                  该条,不触碰工作区)。

失败仍返回 HTTP 200,业务结果看 ``data.success`` / ``data.reason``。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ── 端点常量 ──
MAX_STASHES = 50  # GET 列表条数上限(超出即 truncated)
MAX_MESSAGE_LENGTH = 8192  # 与 git-commit 一致
STASH_PUSH_TIMEOUT_SECONDS = 30.0
STDERR_TRUNCATE_BYTES = 4096

# stash list 行格式: %gd=stash@{N} | %H=sha | %ct=unix 时间戳 | %s=主题
STASH_LIST_FORMAT = "%gd%x00%H%x00%ct%x00%s"


def _parse_numstat_lines(stdout: str, *, untracked: bool) -> list[dict[str, object]]:
    """解析 numstat 输出为 ``[{path, additions, deletions, untracked}]``。

    每行 ``<add>\\t<del>\\t<path>``;binary 行为 ``-\\t-\\t<path>``,
    计数记为 None。rename 的 "old => new" 记法整体落在 path 字段。

    Args:
        stdout: ``git diff --numstat`` / ``git diff-tree --numstat`` 输出。
        untracked: True 表示这些文件来自 stash^3(全部为新增)。

    Returns:
        文件条目列表,顺序与 git 输出一致。
    """
    files: list[dict[str, object]] = []
    for line in stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3 or not parts[2]:
            continue
        add_raw, del_raw, path = parts
        additions = int(add_raw) if add_raw.isdigit() else None
        deletions = int(del_raw) if del_raw.isdigit() else None
        files.append(
            {
                "path": path,
                "additions": additions,
                "deletions": deletions,
                "untracked": untracked,
            }
        )
    return files


async def _list_stash_files(
    git_bin: str, directory: str, sha: str
) -> list[dict[str, object]]:
    """列出一个 stash 的文件明细(tracked + untracked)。

    tracked: ``git diff --numstat <sha>^ <sha>``(stash 提交树 = 贮藏时的
    工作区状态,与 parent1(HEAD)之差即被贮藏的 tracked 改动)。
    untracked: 仅 ``git stash push -u`` 产生 parent3,其树即 untracked
    文件;无 parent3 时命令失败,直接跳过。
    """
    files: list[dict[str, object]] = []

    tracked = await _run_git_async(
        [git_bin, "-C", directory, "diff", "--numstat", f"{sha}^", sha],
        encoding="utf-8",
        timeout=5.0,
    )
    if tracked.get("ok"):
        files.extend(_parse_numstat_lines(tracked.get("stdout", ""), untracked=False))

    untracked_result = await _run_git_async(
        [
            git_bin,
            "-C",
            directory,
            "diff-tree",
            "--root",
            "-r",
            "--numstat",
            "--format=",
            f"{sha}^3",
        ],
        encoding="utf-8",
        timeout=5.0,
    )
    if untracked_result.get("ok"):
        files.extend(
            _parse_numstat_lines(untracked_result.get("stdout", ""), untracked=True)
        )

    return files


def _parse_stash_list(stdout: str) -> list[dict[str, object]]:
    """解析 ``git stash list --format=...`` 输出(不含文件明细)。"""
    entries: list[dict[str, object]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\x00", 3)
        if len(fields) < 4:
            continue
        ref, sha, ts, message = fields
        try:
            timestamp = int(ts)
        except ValueError:
            timestamp = 0
        entries.append(
            {
                "index": 0,  # 占位,下方按序回填
                "ref": ref,
                "sha": sha,
                "timestamp": timestamp,
                "message": message,
            }
        )
    for i, entry in enumerate(entries):
        entry["index"] = i
    return entries


async def handle_list(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> _JSONResponseCompat:
    """GET /spcode/git-stash handler — 列出 stash 及文件明细。"""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("stashes", [])
        err["data"].setdefault("count", 0)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    result = await _run_git_async(
        [
            git_bin,
            "-C",
            directory,
            "stash",
            "list",
            f"--format={STASH_LIST_FORMAT}",
            "-n",
            str(MAX_STASHES + 1),
        ],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        stderr = result.get("stderr", "") or result.get("error", "")
        return _JSONResponseCompat(
            _make_envelope(
                success=False,
                reason=ReasonCode.GIT_ERROR,
                elapsed_ms=_elapsed(),
                stashes=[],
                count=0,
                truncated=False,
                max_stashes=MAX_STASHES,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=stderr[:STDERR_TRUNCATE_BYTES],
            ),
            status_code=200,
        )

    entries = _parse_stash_list(result.get("stdout", ""))
    truncated = len(entries) > MAX_STASHES
    if truncated:
        entries = entries[:MAX_STASHES]

    stashes: list[dict[str, object]] = []
    for entry in entries:
        files = await _list_stash_files(git_bin, directory, str(entry["sha"]))
        stashes.append({**entry, "files": files, "file_count": len(files)})

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            reason=None,
            elapsed_ms=_elapsed(),
            stashes=stashes,
            count=len(stashes),
            truncated=truncated,
            max_stashes=MAX_STASHES,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )


async def handle_push(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-stash handler。

    Body (JSON, 可为空对象): ``{"message": "可选说明"}``。
    执行 ``git stash push -u``(tracked + untracked,不含 ignored)。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            stashed=False,
            **fields,
        )

    # ── 1. body 校验(message 可选,给则必须合法) ──
    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    message = body.get("message")
    if message is not None:
        if not isinstance(message, str):
            return _failure(ReasonCode.INVALID_MESSAGE)
        message = message.strip()
        if not message or len(message) > MAX_MESSAGE_LENGTH:
            return _failure(ReasonCode.INVALID_MESSAGE)

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("stashed", False)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 冲突进行中禁用(与 git-pull 一致的守卫) ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _failure(
            ReasonCode.OPERATION_IN_PROGRESS,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 4. 无本地改动 → nothing_to_stash(porcelain 预检比依赖
    #       stash push 的退出码可靠:旧版 git 无改动时退出码为 0) ──
    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not status_result.get("ok"):
        stderr = status_result.get("stderr", "") or status_result.get("error", "")
        return _failure(
            ReasonCode.GIT_ERROR,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:STDERR_TRUNCATE_BYTES],
        )
    if not status_result.get("stdout", "").strip():
        return _failure(
            ReasonCode.NOTHING_TO_STASH,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 5. git stash push -u ──
    args = [
        git_bin,
        "-C",
        directory,
        "-c",
        "color.ui=never",
        "stash",
        "push",
        "-u",
    ]
    if message is not None:
        args.extend(["-m", message])

    result = await _run_git_async(
        args, encoding="utf-8", timeout=STASH_PUSH_TIMEOUT_SECONDS
    )
    if not result.get("ok"):
        stderr = result.get("stderr", "") or result.get("error", "")
        combined = f"{stderr}".lower()
        if "no local changes to save" in combined:
            reason = ReasonCode.NOTHING_TO_STASH
        else:
            reason = ReasonCode.STASH_FAILED
        return _failure(
            reason,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:STDERR_TRUNCATE_BYTES],
        )

    # ── 6. 读回新 stash@{0} 的元数据 + 文件明细 + 总数 ──
    list_result = await _run_git_async(
        [
            git_bin,
            "-C",
            directory,
            "stash",
            "list",
            f"--format={STASH_LIST_FORMAT}",
            "-n",
            "1",
        ],
        encoding="utf-8",
        timeout=5.0,
    )
    entries = (
        _parse_stash_list(list_result.get("stdout", ""))
        if list_result.get("ok")
        else []
    )
    if entries:
        new_stash = entries[0]
        files = await _list_stash_files(git_bin, directory, str(new_stash["sha"]))
    else:
        # 理论不可达(push 成功后 stash@{0} 必存在);兜底为空明细。
        new_stash = {
            "index": 0,
            "ref": "stash@{0}",
            "sha": "",
            "timestamp": 0,
            "message": message or "",
        }
        files = []

    count_result = await _run_git_async(
        [git_bin, "-C", directory, "stash", "list", "--format=%H"],
        encoding="utf-8",
        timeout=5.0,
    )
    stash_count = (
        len([ln for ln in count_result.get("stdout", "").splitlines() if ln.strip()])
        if count_result.get("ok")
        else len(entries)
    )

    logger.info(
        "git-stash: pushed stash@{0} (%d files, umo=%s)", len(files), effective_umo
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            reason=None,
            elapsed_ms=_elapsed(),
            stashed=True,
            ref=new_stash["ref"],
            sha=new_stash["sha"],
            message=new_stash["message"],
            timestamp=new_stash["timestamp"],
            files=files,
            file_count=len(files),
            stash_count=stash_count,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )


async def handle_pop(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-stash-pop handler。

    Body (JSON, 可为空对象): ``{"index": 0}``(stash@{index},默认 0)。
    执行 ``git stash pop stash@{index}``:改动应用回工作区并删除该条;
    应用冲突时 git 保留该条目(不 drop),reason=stash_conflict。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            popped=False,
            **fields,
        )

    # ── 1. body / index 校验 ──
    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    index = body.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return _failure(ReasonCode.INVALID_PARAM)
    stash_ref = f"stash@{{{index}}}"

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("popped", False)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 冲突进行中禁用(与 git-pull 一致的守卫) ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _failure(
            ReasonCode.OPERATION_IN_PROGRESS,
            operation=operation,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 4. 干净工作区前置(git-pull 同款守卫):pop 会写工作区,
    #       脏树下应用一半冲突会把现场弄乱。 ──
    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not status_result.get("ok"):
        stderr = status_result.get("stderr", "") or status_result.get("error", "")
        return _failure(
            ReasonCode.GIT_ERROR,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:STDERR_TRUNCATE_BYTES],
        )
    if status_result.get("stdout", "").strip():
        return _failure(
            ReasonCode.WORKTREE_DIRTY,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )

    # ── 5. 解析 stash@{index} → sha;不存在 → stash_not_found ──
    verify = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", "--quiet", stash_ref],
        encoding="utf-8",
        timeout=5.0,
    )
    if not verify.get("ok"):
        return _failure(
            ReasonCode.STASH_NOT_FOUND,
            ref=stash_ref,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )
    sha = verify.get("stdout", "").strip()

    # ── 6. 先取该条的文件明细(pop 之后条目即被 drop,无从回读) ──
    files = await _list_stash_files(git_bin, directory, sha)

    # ── 7. git stash pop ──
    result = await _run_git_async(
        [
            git_bin,
            "-C",
            directory,
            "-c",
            "color.ui=never",
            "stash",
            "pop",
            stash_ref,
        ],
        encoding="utf-8",
        timeout=STASH_PUSH_TIMEOUT_SECONDS,
    )
    if not result.get("ok"):
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "") or result.get("error", "")
        # 冲突标记(CONFLICT ...)走 stdout,refs 报错走 stderr,合并判定。
        combined = f"{stdout} {stderr}".lower()
        if (
            "is not a valid reference" in combined
            or "too many revisions" in combined
            or "is not a stash reference" in combined
        ):
            reason = ReasonCode.STASH_NOT_FOUND
        elif "conflict" in combined or "could not restore untracked" in combined:
            reason = ReasonCode.STASH_CONFLICT
        else:
            reason = ReasonCode.STASH_FAILED
        return _failure(
            reason,
            ref=stash_ref,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:STDERR_TRUNCATE_BYTES],
        )

    # ── 8. 剩余条目数 ──
    count_result = await _run_git_async(
        [git_bin, "-C", directory, "stash", "list", "--format=%H"],
        encoding="utf-8",
        timeout=5.0,
    )
    stash_count = (
        len([ln for ln in count_result.get("stdout", "").splitlines() if ln.strip()])
        if count_result.get("ok")
        else 0
    )

    logger.info(
        "git-stash: popped %s (%d files, umo=%s)", stash_ref, len(files), effective_umo
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            reason=None,
            elapsed_ms=_elapsed(),
            popped=True,
            ref=stash_ref,
            sha=sha,
            files=files,
            file_count=len(files),
            stash_count=stash_count,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )


async def handle_drop(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-stash-drop handler。

    Body (JSON, 可为空对象): ``{"index": 0}``(stash@{index},默认 0)。
    执行 ``git stash drop stash@{index}``:删除该贮藏条目,不触碰工作区。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    def _failure(reason: str, **fields: object) -> dict:
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            dropped=False,
            **fields,
        )

    # ── 1. body / index 校验 ──
    if not isinstance(body, dict):
        return _failure(ReasonCode.INVALID_BODY)

    index = body.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        return _failure(ReasonCode.INVALID_PARAM)
    stash_ref = f"stash@{{{index}}}"

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("dropped", False)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 解析 stash@{index} → sha;不存在 → stash_not_found ──
    verify = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", "--quiet", stash_ref],
        encoding="utf-8",
        timeout=5.0,
    )
    if not verify.get("ok"):
        return _failure(
            ReasonCode.STASH_NOT_FOUND,
            ref=stash_ref,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        )
    sha = verify.get("stdout", "").strip()

    # ── 4. git stash drop ──
    result = await _run_git_async(
        [git_bin, "-C", directory, "-c", "color.ui=never", "stash", "drop", stash_ref],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        stderr = result.get("stderr", "") or result.get("error", "")
        combined = f"{result.get('stdout', '')} {stderr}".lower()
        if (
            "is not a valid reference" in combined
            or "too many revisions" in combined
            or "is not a stash reference" in combined
        ):
            reason = ReasonCode.STASH_NOT_FOUND
        else:
            reason = ReasonCode.STASH_DROP_FAILED
        return _failure(
            reason,
            ref=stash_ref,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr[:STDERR_TRUNCATE_BYTES],
        )

    # ── 5. 剩余条目数 ──
    count_result = await _run_git_async(
        [git_bin, "-C", directory, "stash", "list", "--format=%H"],
        encoding="utf-8",
        timeout=5.0,
    )
    stash_count = (
        len([ln for ln in count_result.get("stdout", "").splitlines() if ln.strip()])
        if count_result.get("ok")
        else 0
    )

    logger.info("git-stash: dropped %s (umo=%s)", stash_ref, effective_umo)
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            reason=None,
            elapsed_ms=_elapsed(),
            dropped=True,
            ref=stash_ref,
            sha=sha,
            stash_count=stash_count,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
