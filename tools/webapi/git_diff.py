# tools/webapi/git_diff.py
"""GET /spcode/git-diff — 工作区/暂存区/HEAD 三段 diff。

Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.1
v3.4: ETag in-memory 缓存 + HTTP 缓存头(304 短路)
v3.1: scope 参数(un-staged/staged/all)
v3.3: 4-in-1 git call(优化性能)
"""

from __future__ import annotations

import logging
import time as _time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from .._helpers import _validate_worktree_param, detect_console_encoding
from ..project import state as _proj_state
from ._helpers import (
    _JSONResponseCompat,
    _compute_git_etag,
    _run_git_async,
)
from .file_browser import (
    _common_cache_headers,
    _get_if_none_match,
    _make_304_response,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# git-diff 端点专用常量(也用于 __init__ 启动期探测)
MAX_GIT_DIFF_BYTES = 1 * 1024 * 1024  # 1 MB 硬上限
_GIT_DIFF_ENCODING = detect_console_encoding()  # 进程内一次探测

# ── git-diff ETag in-memory 缓存(v3.4, 2026-06-21, P0 perf) ──
# WHY: dashboard 5-10s 轮询 git-diff 端点时,绝大多数请求 head SHA / 工作树
# / .git/index 都不变,无需每次跑 ``git rev-parse HEAD``(~10-20ms 进程启动)。
# 加 1.5s TTL 缓存可让 N 个并发轮询共享 1 个 git 调用。
# 设计:LRU + TTL(双条件),容量上限 64 个 directory,过期懒清理。
# 注意:仅在 asyncio 单事件循环上下文中使用,无 thread-safe 保护;
#      极端并发下只可能重复算 ETag,不会数据错乱。
_DIFF_ETAG_TTL: float = 1.5  # seconds
_DIFF_ETAG_CACHE_MAX = 64
_DIFF_ETAG_CACHE: OrderedDict[str, tuple[str, float]] = OrderedDict()

# ── git-diff scope 映射(v3.1) ──
# 单一真相源:scope 名 → 传给 `git diff [args]` 的位置参数列表。
# 三种 scope 与 git 原生三段 diff 语义一一对应:
#   "unstaged" (默认) → 工作区 vs index(编辑器改动;v1 行为)
#   "staged"         → index vs HEAD(`git add` 后的内容)
#   "all"            → 工作区 vs HEAD(staged + unstaged 之和)
_VALID_SCOPES: frozenset[str] = frozenset({"unstaged", "staged", "all"})
_SCOPE_GIT_ARGS: dict[str, list[str]] = {
    "unstaged": [],
    "staged": ["--cached"],
    "all": ["HEAD"],
}
DEFAULT_SCOPE: str = "unstaged"  # 与 v1 行为严格兼容

# ── file-browser 端点常量(v3.2) ──
# 单一真相源,handler 与测试共享;详情见
# docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md §5


def _parse_diff_status_map(diff_output: str) -> dict[str, str]:
    """从 ``git diff`` 原始输出解析 ``{path: status}`` 映射。

    性能优化 (v3.3,2026-06-21,git-diff 4 合 1 配套):
    替代原来的 ``git diff --name-status`` 单独调用。仅检查每个文件
    header 部分(第一个 hunk 之前的 ~10 行),跳过 hunk body 加速。

    支持的状态:
    - M (modify,默认)
    - A (add,``new file mode``)
    - D (delete,``deleted file mode``)
    - R (rename,``rename from/to`` 对;使用新路径作为 key)
    - C (copy,``copy from/to`` 对;使用新路径作为 key)

    已知限制(无测试覆盖,与 v1 行为一致):
    - 纯 mode change(``old mode`` / ``new mode``,无 content 改动)→ "M"
    - submodule 改动 → "M"
    - ``T``(type change)目前归为 "M",旧实现也是 "T" → 略退
    """
    status_by_path: dict[str, str] = {}
    current_path: str | None = None
    current_status: str = "M"
    in_hunk = False

    for line in diff_output.splitlines():
        if line.startswith("diff --git "):
            if current_path is not None:
                status_by_path[current_path] = current_status
            parts = line.split(" ")
            b_path = parts[3] if len(parts) >= 4 else ""
            current_path = b_path[2:] if b_path.startswith("b/") else b_path
            current_status = "M"
            in_hunk = False
            continue

        if current_path is None or in_hunk:
            continue

        if line.startswith("new file mode"):
            current_status = "A"
        elif line.startswith("deleted file mode"):
            current_status = "D"
        elif line.startswith("rename from "):
            current_status = "R"
        elif line.startswith("rename to "):
            current_path = line[len("rename to ") :]
        elif line.startswith("copy from "):
            current_status = "C"
        elif line.startswith("copy to "):
            current_path = line[len("copy to ") :]
        elif line.startswith("@@"):
            in_hunk = True
        # index / similarity / file header (--- / +++) / Binary files 等行:忽略

    if current_path is not None:
        status_by_path[current_path] = current_status

    return status_by_path


def _parse_numstat_counts(numstat_output: str) -> dict[str, tuple[int, int]]:
    """从 ``git diff --numstat`` 解析 ``{path: (add, del)}`` 映射。

    性能优化 (v3.3,2026-06-21,git-diff 4 合 1 配套):
    替代原 ``_parse_files_changed`` 中的 numstat 解析块,并修复了 rename/copy
    的 path 解析 bug(旧版用 ``old_path => new_path`` 整个串作 key,导致
    counts_by_path.get(new_path) 永远 miss,rename 文件的 additions/deletions
    错误地为 0)。此处用 new_path 作 key,与 _parse_diff_status_map 对齐。

    Binary 文件的 numstat 是 ``-`` ``-`` → ``(0, 0)``。
    """
    counts: dict[str, tuple[int, int]] = {}
    for line in numstat_output.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_raw, del_raw, path = parts[0], parts[1], parts[2]
        # rename/copy: numstat 输出 "<old> => <new>",取 new 部分作 key
        if " => " in path:
            path = path.split(" => ", 1)[1]
        add = 0 if add_raw == "-" else int(add_raw)
        delete = 0 if del_raw == "-" else int(del_raw)
        counts[path] = (add, delete)
    return counts


def _build_stat_text(files_changed: list[dict]) -> str:
    """根据 files_changed 构造 ``git --stat`` 风格的统计文本。

    性能优化 (v3.3,2026-06-21,git-diff 4 合 1 配套):
    替代原来的 ``git diff --stat`` 单独调用。不追求与 git 输出字节级一致
    (无测试断言比对),但格式相似(Dashboard 预览用)。
    """
    if not files_changed:
        return ""

    max_path_len = max(len(f["path"]) for f in files_changed)
    max_change_len = max(
        len(str(f["additions"] + f["deletions"])) for f in files_changed
    )
    lines: list[str] = []
    total_add = 0
    total_del = 0
    for f in files_changed:
        total = f["additions"] + f["deletions"]
        add = f["additions"]
        delete = f["deletions"]
        # git 用 50 字符的 +/- bar;空 bar 用 | 占位
        bar = "+" * min(add, 50) + "-" * min(delete, 50)
        if not bar:
            bar = "|"
        lines.append(f" {f['path']:<{max_path_len}} | {total:>{max_change_len}} {bar}")
        total_add += add
        total_del += delete

    if len(files_changed) == 1:
        summary = (
            f" 1 file changed, {total_add} insertions(+), {total_del} deletions(-)"
        )
    else:
        summary = (
            f" {len(files_changed)} files changed, "
            f"{total_add} insertions(+), {total_del} deletions(-)"
        )

    return "\n".join(lines) + "\n" + summary


async def _compute_diff_etag(git_bin: str, directory: str) -> str:
    """为 git-diff 端点计算弱 ETag(委托给共享 helper)。

    v3.5 (2026-06-30): 委托给 ``_compute_git_etag`` 统一实现,加入 3 路
    porcelain 探测解决 ETag staleness。详见
    ``tools/webapi/_helpers.py:_compute_git_etag`` docstring。

    旧 v3.4 算法只用 HEAD SHA + wt_mtime + idx_mtime,用户在 worktree 内
    编辑文件 (不 git add) 时 3 个信号都不变 → ETag 不变 → 304 stale。
    新算法额外包含 3 路 git 探测的 SHA-1 哈希,文件级真实变化立刻体现。

    v3.4 P0 perf: 1.5s TTL in-memory 缓存保留(LRU 64 容量),让 dashboard
    5-10s polling 时 N 个请求共享 1 个 ETag 计算。
    """
    # 缓存查询(命中且未过期 → 直接返回)
    now = _time.monotonic()
    cached = _DIFF_ETAG_CACHE.get(directory)
    if cached is not None and (now - cached[1]) < _DIFF_ETAG_TTL:
        # LRU:命中时移到队尾
        _DIFF_ETAG_CACHE.move_to_end(directory)
        return cached[0]

    # 缓存未命中或过期 → 委托统一 helper 计算
    etag = await _compute_git_etag(git_bin, directory)

    # 写入缓存 + LRU 驱逐
    _DIFF_ETAG_CACHE[directory] = (etag, now)
    while len(_DIFF_ETAG_CACHE) > _DIFF_ETAG_CACHE_MAX:
        _DIFF_ETAG_CACHE.popitem(last=False)
    return etag


def _make_git_diff_empty_envelope(
    umo: str | None,
    reason: str,
    directory: str | None = None,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造未载入 / 失败路径的响应骨架(对称于 handle_get_project_status 风格)。"""
    return {
        "status": "ok",
        "data": {
            "loaded": False,
            "directory": directory,
            "umo": umo,
            "reason": reason,
            "diff": None,
            "stat": None,
            "files_changed": [],
            "truncated": False,
            "truncated_at_bytes": 0,
            "max_bytes": MAX_GIT_DIFF_BYTES,
            "elapsed_ms": elapsed_ms,
        },
    }


async def handle(
    plugin: SPCodeToolkit,
) -> dict:
    """Web API handler for ``GET /spcode/git-diff``.

    Returns a JSON envelope of the form::

        {
            "status": "ok",
            "data": {
                "loaded": bool,
                "directory": str | None,
                "umo": str | None,
                "diff": str | None,
                "stat": str | None,
                "files_changed": [{"path", "status", "additions", "deletions"}],
                "truncated": bool,
                "truncated_at_bytes": int,
                "max_bytes": int,
                "elapsed_ms": int,
                "reason": str | None
            }
        }

    The endpoint is only "valid" (loaded=True) when:
      - agentsmd_enabled AND codegraph_enabled are both true
      - A project is loaded for the requested (or most-recent) umo
      - The loaded directory still exists and is a git repository
    Otherwise returns ``loaded=False`` with a structured ``reason`` code.
    """
    t0 = _time.time()
    from astrbot.api import web

    umo: str | None = None
    worktree_param: str | None = None
    try:
        umo_raw = web.request.query.get("umo")
        umo = umo_raw if umo_raw else None
        # worktree: 保留空字符串以触发 6-step 校验(不要 ``or None``)
        wt_raw = web.request.query.get("worktree")
        worktree_param = wt_raw if wt_raw is not None else None
    except Exception:
        umo = None
        worktree_param = None

    # ── scope 解析(v3.1,Q1-OOR:在 feature flag 校验之前) ──
    # 默认 "unstaged" 与 v1 行为严格一致;非法值 → invalid_scope;
    # 大小写不敏感;空字符串视同缺省(避免误报)。
    scope: str = DEFAULT_SCOPE
    try:
        scope_raw = web.request.query.get("scope")
        if scope_raw is not None:
            candidate = scope_raw.strip().lower()
            if candidate and candidate not in _VALID_SCOPES:
                # _elapsed() 尚未定义,inline 计算 elapsed_ms
                return _make_git_diff_empty_envelope(
                    umo=None,
                    reason="invalid_scope",
                    elapsed_ms=int((_time.time() - t0) * 1000),
                )
            scope = candidate or DEFAULT_SCOPE
    except Exception:
        scope = DEFAULT_SCOPE

    git_bin = plugin._git_binary()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # 1. Feature flag 校验
    if not (
        plugin._config.get("agentsmd_enabled", True)
        and plugin._config.get("codegraph_enabled", True)
    ):
        return _make_git_diff_empty_envelope(
            umo=umo, reason="feature_disabled", elapsed_ms=_elapsed()
        )

    # 2. umo 解析与回退
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
        return _make_git_diff_empty_envelope(
            umo=umo, reason="no_project_loaded", elapsed_ms=_elapsed()
        )

    directory = info.get("directory", "")

    # 2.5 ?worktree= 参数校验(6-step defense;spec §2.3)
    # Spec: trim 后空 → 视同缺省(向后兼容 v1)。
    # 其他任何值都必须通过 6 步校验,否则返回 worktree_invalid。
    if worktree_param is not None and worktree_param.strip():
        validated_wt, wt_err = _validate_worktree_param(
            git_bin, directory, worktree_param
        )
        if wt_err is not None:
            logger.warning(
                f"[git-diff] rejected ?worktree={worktree_param!r} "
                f"(loaded={directory!r})"
            )
            return _make_git_diff_empty_envelope(
                umo=umo,
                reason=wt_err,
                directory=directory,
                elapsed_ms=_elapsed(),
            )
        directory = validated_wt  # use the validated worktree path

    # 3. 目录存在性
    if not Path(directory).is_dir():
        return _make_git_diff_empty_envelope(
            umo=umo,
            reason="directory_missing",
            directory=directory,
            elapsed_ms=_elapsed(),
        )

    # 3.5 HTTP 缓存(v3.3,2026-06-21): 在跑 git probe / diff 之前
    # 先算 ETag 并检查 If-None-Match,命中直接 304 短路。Dashboard 5-10s
    # polling 时大多数请求命中缓存(无 commit/无 git add/无 add-delete),
    # 跳过 3 个 git 调用(probe + diff + numstat)从 ~1-2s 降到 ~12ms。
    etag = await _compute_diff_etag(git_bin, directory)
    cache_headers = _common_cache_headers(etag)
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)

    # 4. Git repository probe — git outputs UTF-8 on every platform,
    # so we always decode with utf-8 regardless of the Windows console
    # codepage (cp936/GBK on zh-CN systems would otherwise mojibake
    # non-ASCII characters in subsequent diff output).
    # P1 perf (v3.4, 2026-06-21): 改用 _run_git_async 释放 worker 线程。
    probe = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            return _make_git_diff_empty_envelope(
                umo=umo,
                reason="not_a_git_repo",
                directory=directory,
                elapsed_ms=_elapsed(),
            )
        if "未安装" in probe.get("error", ""):
            return _make_git_diff_empty_envelope(
                umo=umo,
                reason="git_unavailable",
                directory=directory,
                elapsed_ms=_elapsed(),
            )
        return _make_git_diff_empty_envelope(
            umo=umo,
            reason="git_error",
            directory=directory,
            stderr=probe.get("stderr", "") or probe.get("error", ""),
            elapsed_ms=_elapsed(),
        )

    # 5. Serially collect raw diff + numstat.
    # P2 perf (v3.4, 2026-06-21): 串行 — diff 完成后跑 numstat,走 page cache。
    # 旧版 (v3.3) 2 路 ``asyncio.gather(diff, numstat)`` 并发:
    # - CPU 占用 2 路(2 个 git 子进程同时跑)
    # - 2 个 git 进程互相竞争 work tree I/O
    # - raw diff 失败时 numstat 也跑(浪费 1 个 git 调用)
    # 新版串行:
    # - raw diff 跑完 → 内核 page cache 填满 → numstat 紧接着跑,几乎瞬时
    #   (numstat 不读 file body,只 walk tree;走 page cache 时 < 5ms)
    # - 总 wall-clock 持平(因为 page cache 命中)
    # - CPU 占用从 2 路降到 1 路
    # - raw diff 失败时直接返回,**numstat 跳过**(省 1 个 git 调用)
    # All git invocations output UTF-8, so we decode with utf-8 to avoid
    # mojibake on cp936 Windows hosts.
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]
    scope_args = _SCOPE_GIT_ARGS[scope]
    # P1 perf (v3.4, 2026-06-21): diff / numstat 改用 _run_git_async,
    # 事件循环直接管理两个 git 子进程,worker 占用从 2 → 0。
    # P2 perf (v3.4, 2026-06-21): 改为串行(await 而非 asyncio.gather)。
    raw_result = await _run_git_async(
        git_prefix + ["diff"] + scope_args, encoding="utf-8"
    )

    if not raw_result["ok"]:
        return _make_git_diff_empty_envelope(
            umo=umo,
            reason="git_error",
            directory=directory,
            stderr=raw_result.get("stderr", ""),
            elapsed_ms=_elapsed(),
        )

    # raw 成功后才跑 numstat(走 diff 留下的 page cache)
    numstat_result = await _run_git_async(
        git_prefix + ["diff", "--numstat"] + scope_args, encoding="utf-8"
    )

    # 6. 截断与解析(single raw diff → 3 views in Python)
    raw = raw_result["stdout"]
    truncated = len(raw) > MAX_GIT_DIFF_BYTES
    diff = raw[:MAX_GIT_DIFF_BYTES]
    status_by_path = _parse_diff_status_map(raw)
    counts_by_path = _parse_numstat_counts(numstat_result.get("stdout", ""))
    files_changed = [
        {
            "path": path,
            "status": status,
            "additions": counts_by_path.get(path, (0, 0))[0],
            "deletions": counts_by_path.get(path, (0, 0))[1],
        }
        for path, status in status_by_path.items()
    ]
    stat = _build_stat_text(files_changed)

    return _JSONResponseCompat(
        {
            "status": "ok",
            "data": {
                "loaded": True,
                "directory": directory,
                "umo": umo,
                "scope": scope,  # ← 新增 v3.1:回显 scope 解析结果
                "diff": diff,
                "stat": stat,
                "files_changed": files_changed,
                "truncated": truncated,
                "truncated_at_bytes": MAX_GIT_DIFF_BYTES if truncated else 0,
                "max_bytes": MAX_GIT_DIFF_BYTES,
                "elapsed_ms": _elapsed(),
                "reason": None,
            },
        },
        status_code=200,
        headers=cache_headers,
    )
