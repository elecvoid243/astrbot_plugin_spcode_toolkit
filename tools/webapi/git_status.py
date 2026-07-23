"""GET /spcode/git-status — git 工作区状态查询。

仿照 ``/spcode/git-log`` 与 ``/spcode/git-diff`` 的实现模式:
- 共享 ``_git_endpoint_preflight`` 5 步前置(feature flag / umo / worktree /
  目录存在 / git repo 探测)
- 共享 1.5s TTL in-memory ETag 缓存(dashboard 5-10s polling 时减少
  ``rev-parse HEAD`` 调用)
- 失败路径走 ``_make_envelope`` + ``ReasonCode``,与既有端点契约一致

git 状态查询命令(串行执行,page cache 命中):
- ``git status --porcelain`` → 文件级 X/Y 状态(替代 v3.6 file-restore 内部
  调用的同名命令,统一行为)
- ``git rev-parse --abbrev-ref HEAD`` → 当前 branch(detached HEAD 时返回
  字面量 "HEAD",需要识别)
- ``git rev-parse --abbrev-ref @{upstream}`` → upstream branch(无 upstream 时
  非零退出,跳过 ahead/behind)
- ``git rev-list --left-right --count HEAD...@{upstream}`` → ahead/behind
  计数(仅 upstream 存在时跑)

Author: elecvoid243 @ 2026-06-24
"""

from __future__ import annotations

import logging
import time as _time
from collections import OrderedDict
from typing import TYPE_CHECKING

from ._helpers import (
    _JSONResponseCompat,
    _compute_git_etag,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .file_browser import (
    _common_cache_headers,
    _get_if_none_match,
    _make_304_response,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ── 端点常量 ──

# 上限保护:防止极端仓库(10k+ 改动)拉爆 dashboard。
# 超过 MAX_FILES 截断,前端收到 truncated=True 时显示"还有 N 项未展示"。
MAX_FILES: int = 1000

# ── git-status ETag in-memory 缓存(同 git-diff / git-log 模式) ──
# WHY: dashboard 5-10s 轮询 git-status 时,绝大多数请求 HEAD / worktree /
# .git/index 都不变,无需每次跑 ``git rev-parse HEAD``(~10-20ms 进程启动)。
_STATUS_ETAG_TTL: float = 1.5
_STATUS_ETAG_CACHE_MAX = 64
_STATUS_ETAG_CACHE: OrderedDict[str, tuple[str, float]] = OrderedDict()


# ──────────────────────────────────────────────────────────
# 解析器
# ──────────────────────────────────────────────────────────


# porcelain v1 状态码 → scope 分类
# 参考: https://git-scm.com/docs/git-status#_short_format
#
# - staged (X 列为真正暂存动作): M / A / D / R / C / T
# - intent-to-add: X=' ' Y='A' (与 file_restore 端点保持一致)
# - worktree 改动(未暂存): X=' ' Y in MADRC T
# - 未跟踪: X='?' Y='?'
# - 冲突: X/U Y/U 任意一列含 U
_STAGED_X: frozenset[str] = frozenset({"M", "A", "D", "R", "C", "T"})
_WORKTREE_Y: frozenset[str] = frozenset({"M", "A", "D", "R", "C", "T"})
_CONFLICT_CHARS: frozenset[str] = frozenset({"U", "A", "D"})


def _classify_file_scope(x_status: str, y_status: str) -> str:
    """根据 porcelain v1 的 X/Y 列,返回该文件的 scope 分类。

    Returns:
        ``"staged"`` / ``"unstaged"`` / ``"intent_to_add"`` /
        ``"untracked"`` / ``"conflict"`` / ``"modified_both"`` 之一
    """
    # 未跟踪(``?? path``)
    if x_status == "?" and y_status == "?":
        return "untracked"

    # 冲突:任一列为 U/A/D 组合(如 UU / AA / DU / UD)
    if x_status in _CONFLICT_CHARS and y_status in _CONFLICT_CHARS:
        return "conflict"

    # intent-to-add: X=' ' Y='A' (与 file_restore 端点判定一致)
    if x_status == " " and y_status == "A":
        return "intent_to_add"

    # 真正已暂存(可能 worktree 也有改动):MM / MA / M  /  AM / A  /  …
    is_staged = x_status in _STAGED_X
    is_worktree = y_status in _WORKTREE_Y

    if is_staged and is_worktree:
        return "modified_both"  # staged + worktree 都有改动
    if is_staged:
        return "staged"
    if is_worktree:
        return "unstaged"
    # fallback(理论不可达;X/Y 都是 ' ' 即 porcelain 不输出该行)
    return "unstaged"


def _parse_porcelain_v1(porcelain: str) -> list[dict]:
    """解析 ``git status --porcelain`` 输出为文件列表。

    每行格式: ``XY <path>``(rename/copy 格式稍长,本端点暂不展开);
    未跟踪文件: ``?? <path>``。

    Returns:
        ``[{"path": str, "x_status": str, "y_status": str, "scope": str}, ...]``
        按 porcelain 原序(已修改在前,未跟踪在后),截断到 ``MAX_FILES``。
    """
    files: list[dict] = []
    for line in porcelain.splitlines():
        if not line:
            continue
        # porcelain 行最少 4 字符: "XY <path>"
        if len(line) < 4:
            continue
        x_status = line[0]
        y_status = line[1]
        # rename (R) / copy (C) 在 Y 列时,后续是 "old_path -> new_path" 形式;
        # 本端点使用 ``--porcelain``(v1)非 ``-z``,所以取箭头后的新路径。
        if y_status in ("R", "C") and " -> " in line:
            path = line.split(" -> ", 1)[1].strip()
        else:
            # 跳过 "XY " 三个字符后到行尾
            path = line[3:].strip()
        if not path:
            continue
        scope = _classify_file_scope(x_status, y_status)
        files.append(
            {
                "path": path,
                "x_status": x_status,
                "y_status": y_status,
                "scope": scope,
            }
        )
        if len(files) >= MAX_FILES:
            break
    return files


def _parse_porcelain_v1_z(porcelain: str) -> list[dict]:
    """Parse NUL-delimited ``git status --porcelain=v1 -z`` output.

    Records are separated by NUL bytes. The X/Y status columns sit at the
    start of each record, and the path starts at offset 3. Rename and copy
    records carry the destination path in the first record and the source
    path in the following NUL-delimited field. The API exposes only the
    destination path.

    Args:
        porcelain: Raw decoded stdout from Git.

    Returns:
        Parsed file records in Git output order, capped at ``MAX_FILES``.
    """
    files: list[dict] = []
    records = porcelain.split("\0")
    cursor = 0
    while cursor < len(records):
        record = records[cursor]
        cursor += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            continue
        x_status = record[0]
        y_status = record[1]
        path = record[3:]
        if not path:
            continue
        # rename and copy records consume an additional NUL-delimited field
        # containing the source path. The destination (current) path is the
        # first field.
        if x_status in ("R", "C") or y_status in ("R", "C"):
            if cursor >= len(records) or not records[cursor]:
                continue
            cursor += 1
        files.append(
            {
                "path": path,
                "x_status": x_status,
                "y_status": y_status,
                "scope": _classify_file_scope(x_status, y_status),
            }
        )
        if len(files) >= MAX_FILES:
            break
    return files


def _parse_ahead_behind(rev_list_output: str) -> tuple[int, int]:
    """解析 ``git rev-list --left-right --count HEAD...@{upstream}`` 输出。

    格式: ``<ahead>\\t<behind>``(两数字以 TAB 分隔,各占一行)。

    Returns:
        ``(ahead, behind)`` 元组,解析失败时返回 ``(0, 0)``。
    """
    parts = rev_list_output.split("\t")
    if len(parts) < 2:
        return 0, 0
    try:
        return int(parts[0].strip() or "0"), int(parts[1].strip() or "0")
    except ValueError:
        return 0, 0


# ──────────────────────────────────────────────────────────
# ETag 计算
# ──────────────────────────────────────────────────────────


async def _compute_status_etag(git_bin: str, directory: str) -> str:
    """为 git-status 端点计算弱 ETag(委托给共享 helper)。

    v3.5 (2026-06-30): 委托给 ``_compute_git_etag`` 统一实现,加入 3 路
    porcelain 探测解决 ETag staleness。详见
    ``tools/webapi/_helpers.py:_compute_git_etag`` docstring。

    旧算法只用 HEAD SHA + wt_mtime + idx_mtime,用户在 worktree 内编辑
    文件 (不 git add) 时 3 个信号都不变 → ETag 不变 → 304 stale。
    新算法额外包含 3 路 git 探测的 SHA-1 哈希,文件级真实变化立刻体现。

    1.5s TTL in-memory 缓存保留(LRU 64 容量),dashboard 5-10s polling
    时 N 个请求共享 1 个 ETag 计算。
    """
    now = _time.monotonic()
    cached = _STATUS_ETAG_CACHE.get(directory)
    if cached is not None and (now - cached[1]) < _STATUS_ETAG_TTL:
        _STATUS_ETAG_CACHE.move_to_end(directory)
        return cached[0]

    etag = await _compute_git_etag(git_bin, directory)

    _STATUS_ETAG_CACHE[directory] = (etag, now)
    while len(_STATUS_ETAG_CACHE) > _STATUS_ETAG_CACHE_MAX:
        _STATUS_ETAG_CACHE.popitem(last=False)
    return etag


# ──────────────────────────────────────────────────────────
# Handler
# ──────────────────────────────────────────────────────────


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-status handler。

    仿照 ``/spcode/git-log`` 的 handler 签名 — ``umo`` / ``worktree`` 由
    ``_wrap`` 适配器从 query string 注入(POST body 不适用,本端点为 GET)。

    Returns:
        ``{"status": "ok", "data": {...}}`` envelope;成功时 ``loaded=True``
        含 ``branch`` / ``upstream`` / ``files`` / ``summary``;失败时
        ``loaded=False`` + ``reason`` 字段(由 ``_git_endpoint_preflight``
        或后续 git 调用失败填入)。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. preflight(5 步) ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        # git-status 端点契约: 失败路径也要有 ``loaded`` 字段
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    git_bin = plugin._git_binary()
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # ── 2. ETag 检查(命中缓存 → 304 短路) ──
    etag = await _compute_status_etag(git_bin, directory)
    cache_headers = _common_cache_headers(etag)
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)

    # ── 3. 串行跑 4 个 git 调用(同 git-diff 模式 — page cache 命中) ──
    # 命令 1: 必跑的 porcelain status
    # Use NUL-delimited output so paths containing spaces, quotes, tabs,
    # newlines, or non-ASCII bytes stay intact (v2.21 canonical-paths fix).
    status_result = await _run_git_async(
        git_prefix + ["status", "--porcelain=v1", "-z"], encoding="utf-8"
    )
    if not status_result["ok"]:
        stderr = status_result.get("stderr", "") or status_result.get("error", "")
        # 空仓库时 ``git status --porcelain`` 仍返回 0(只是输出空),
        # 因此本分支实际仅在 git 进程级失败时触发。
        if "does not have any commits" in stderr or "ambiguous" in stderr.lower():
            reason = ReasonCode.EMPTY_REPOSITORY
        else:
            reason = ReasonCode.GIT_ERROR
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr=stderr,
        )

    # 命令 2: 当前 branch(可能 detached HEAD)
    # ``--abbrev-ref HEAD`` 在 detached 时返回字面量 ``HEAD``,需要识别。
    branch_result = await _run_git_async(
        git_prefix + ["rev-parse", "--abbrev-ref", "HEAD"],
        encoding="utf-8",
    )
    branch: str | None = None
    if branch_result["ok"]:
        b = branch_result["stdout"].strip()
        branch = b if b and b != "HEAD" else None

    # 命令 3: upstream branch(无 upstream 时 git 进程返回非零,不要 500)
    upstream_result = await _run_git_async(
        git_prefix + ["rev-parse", "--abbrev-ref", "@{upstream}"],
        encoding="utf-8",
    )
    upstream_branch: str | None = None
    if upstream_result["ok"]:
        u = upstream_result["stdout"].strip()
        # ``@{u}`` 在新建分支未推送时也可能非零,这里再次兜底空串
        if u and u != "HEAD":
            upstream_branch = u

    # 命令 4: ahead / behind(仅 upstream 存在时跑)
    ahead = 0
    behind = 0
    if upstream_branch:
        rev_list_result = await _run_git_async(
            git_prefix
            + ["rev-list", "--left-right", "--count", f"HEAD...{upstream_branch}"],
            encoding="utf-8",
        )
        if rev_list_result["ok"]:
            ahead, behind = _parse_ahead_behind(rev_list_result["stdout"])

    # ── 4. 解析 porcelain + 汇总 ──
    files = _parse_porcelain_v1_z(status_result["stdout"])
    # truncation check operates on parsed files, not on splitlines() which
    # would mis-count paths that themselves contain newlines.
    truncated = bool(status_result["stdout"]) and len(files) >= MAX_FILES

    summary = {
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "conflicts": 0,
        "total": len(files),
    }
    for f in files:
        scope = f["scope"]
        if scope == "staged" or scope == "modified_both":
            # modified_both 视作已暂存 + 仍有 worktree 改动,但 count 计入 staged
            summary["staged"] += 1
        elif scope == "unstaged":
            summary["unstaged"] += 1
        elif scope == "untracked":
            summary["untracked"] += 1
        elif scope == "conflict":
            summary["conflicts"] += 1
        elif scope == "intent_to_add":
            # 与 file_restore 端点保持一致:intent-to-add 暂不计 staged
            summary["unstaged"] += 1

    upstream_data: dict | None = None
    if upstream_branch:
        upstream_data = {
            "branch": upstream_branch,
            "ahead": ahead,
            "behind": behind,
        }

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            branch=branch,
            upstream=upstream_data,
            files=files,
            summary=summary,
            truncated=truncated,
            max_files=MAX_FILES,
        ),
        status_code=200,
        headers=cache_headers,
    )
