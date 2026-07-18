"""GET /spcode/git-log — git log 查询(8 字段标准粒度)。

Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §D
PR-2 of git workflow endpoints design.
"""

from __future__ import annotations
import logging
import re
import time as _time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from .file_browser import (
    _common_cache_headers,
    _get_if_none_match,
    _make_304_response,
)
from ._helpers import (
    _JSONResponseCompat,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    _validate_repo_relative_file,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ── 端点常量 ──
MAX_LOG_N = 200
DEFAULT_LOG_N = 20
MAX_PARAM_LENGTH = 512
MAX_LOG_BYTES = 1 * 1024 * 1024  # 1 MB 硬上限

# 11 字段 NUL 分隔 pretty=format 模板
# %H  full sha | %h short sha | %an author name | %ae author email
# %cn committer name | %ce committer email
# %aI author date ISO | %cI committer date ISO
# %s subject | %b body | %P parents (space-separated)
#
# v3.8 (2026-06-24): 尾部追加 ``@@SPREC_END@@`` 哨兵,与 ``--shortstat``
# 单次合并调用配套 — 解析器按哨兵切分每个 commit 的 format 块,然后在每
# 块尾部查找 shortstat 行,实现 commit 与 stat 的天然对齐(关键不变量:
# merge commit 后面**没有** shortstat 行,默认 {0,0,0};regular commit
# 后必有 shortstat 行)。
# 设计依据见 docs/superpowers/specs/2026-06-24-git-log-shortstat-
# alignment-fix-design.md §3。
LOG_FORMAT = "%H%x00%h%x00%an%x00%ae%x00%cn%x00%ce%x00%aI%x00%cI%x00%s%x00%b%x00%P%x00@@SPREC_END@@"

# ── git-log ETag in-memory 缓存 ──
# 复用 git-diff 模式:HEAD SHA + worktree mtime + .git/index mtime,1.5s TTL。
_LOG_ETAG_TTL: float = 1.5
_LOG_ETAG_CACHE_MAX = 64
_LOG_ETAG_CACHE: OrderedDict[str, tuple[str, float]] = OrderedDict()


# ──────────────────────────────────────────────────────────
# 解析器
# ──────────────────────────────────────────────────────────


def _parse_log_format(raw: str) -> list[dict]:
    """解析 git log --pretty=format 11 字段 NUL 分隔输出。

    **关键陷阱**(实测):
    - ``%b`` 输出 body 时自带尾部 ``\\n``(且 body 内也可含 ``\\n``)
    - 每条 commit 之间有 ``\\n`` 分隔
    - NUL 切分时,commit 边界的 ``\\n`` 会粘到下一条 commit 的 sha 字段开头
      (如 ``\\n418bb365...``)

    实际 raw 结构(3 条 commits):
    ``<sha>\\x00...<body>\\n\\x00<parents>\\x00\\n<next_sha>\\x00...``

    解析策略:**先 strip 所有 ``\\n``** 再 split ``\\x00``,然后 11 字段一组。
    副作用:body 内真实的换行会丢失(被替换为空),但 spec 中 ``body`` 仅用于
    dashboard 预览,可以接受。
    """
    commits: list[dict] = []
    # Step 1: 移除所有换行(commit 边界 + body 尾换行)
    raw_clean = raw.replace("\n", "").replace("\r", "")
    # Step 2: 按 NUL 切
    parts = raw_clean.split("\x00")
    # Step 3: 移除恰好一个 trailing 空段(末尾 ``%x00`` 产生的)
    if parts and parts[-1] == "":
        parts.pop()
    if not parts:
        return commits

    # Step 4: 11 字段 = 1 commit
    n = len(parts) // 11
    for i in range(n):
        fields = parts[i * 11 : (i + 1) * 11]
        sha, sha_short, an, ae, cn, ce, aI, cI, subject, body, parents_raw = fields
        # 校验首字段是有效 SHA(防御:filter out 非 commit 数据片段)
        if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
            continue
        parents = parents_raw.split() if parents_raw.strip() else []
        commits.append(
            {
                "sha": sha,
                "sha_short": sha_short,
                "author": {"name": an, "email": ae},
                "committer": {"name": cn, "email": ce},
                "date": aI,
                "subject": subject,
                "body": body if body else None,
                "parents": parents,
            }
        )
    return commits


def _parse_log_shortstat(raw: str) -> list[dict]:
    """解析 git log --shortstat 输出。

    格式:`` N files changed, M insertions(+), K deletions(-)``

    v3.8 (2026-06-24) 行为变更:**只**为真正匹配 ``N files changed`` 模式
    的行 emit entry,不再为每个非空行兜底 push 一个 ``{0,0,0}`` —— 后者会
    把 commit header / Author / Date / message / ``<file>|N+-`` 行误计成
    stat 行,导致 ``shortstats[:n]`` 严重错位,大部分 commit 在 dashboard
    history 页面显示为 ``{0,0,0}``。

    现在:匹配的 stat 行 → emit 真实数字;merge commit / 非 stat 行 →
    跳过(对应 handler 走 sentinel-based 合并解析器路径)。

    解析正则同时支持:
      - `` 3 files changed, 142 insertions(+), 27 deletions(-)``  (全字段)
      - `` 1 file changed, 5 insertions(+)``                          (仅 insertions)
      - `` 2 files changed, 5 deletions(-)``                          (仅 deletions)
      - `` 0 files changed``                                          (零变更,仍 emit)
    """
    result: list[dict] = []
    pat = re.compile(
        r"\s*(\d+) files? changed(?:, (\d+) insertions?\(\+\))?"
        r"(?:, (\d+) deletions?\(-\))?"
    )
    for line in raw.splitlines():
        m = pat.match(line)
        if not m:
            continue
        result.append(
            {
                "files": int(m.group(1) or 0),
                "additions": int(m.group(2) or 0),
                "deletions": int(m.group(3) or 0),
            }
        )
    return result


# ── v3.8 (2026-06-24): 合并解析器 ───────────────────────────────
# 解决 dashboard history 页面某些 commit shortstat 全为 0 的对齐 bug。
# 详见上方模块 docstring 与 docs/superpowers/specs/2026-06-24-git-log-
# shortstat-alignment-fix-design.md §3。
#
# 背景:原实现两次 git log 调用(``--pretty=format:...`` + ``--shortstat``),
# 然后按索引硬对齐 — 但 ``git log --shortstat`` 输出每个 commit 块含
# 5~10 行(commit/Merge/Author/Date/msg/file|stat/summary),原 parser 把
# 这些行都 push 成 ``{0,0,0}``,使 shortstats 列表远长于 commits,
# ``shortstats[:n]`` 截掉后绝大多数位置是噪声行,真实 stat 行只在
# 偶发位置对齐到正确 commit 上。表现:UI 列表里部分 commit 显示正确数字,
# 其余全 0。
#
# 修复:合并为单次 ``git log --pretty=format:... @@SPREC_END@@ --shortstat``,
# 每个 commit 块以 ``@@SPREC_END@@`` 结尾,后跟可选 shortstat 块。
# merge commit 后面无 shortstat 块(默认 ``{0,0,0}``),与 git 语义一致。
_SENTINEL_END = "@@SPREC_END@@"


def _parse_combined_log_output(raw: str) -> list[dict]:
    """解析合并的 ``git log --pretty=format:<...@@SPREC_END@@> --shortstat`` 输出。

    **输出结构**(实测,关键陷阱):

    ``git log --pretty=format:<...@@SPREC_END@@> --shortstat`` 把每个 commit
    的 format 字段与 stat 块交错输出,两段之间以 ``@@SPREC_END@@`` 哨兵
    分隔:

    ```
    <format1>@@SPREC_END@@\\n<stat1>\\n\\n<format2>@@SPREC_END@@\\n<stat2>\\n\\n...
    ```

    在 sentinel 处 ``split`` 后,sentinel 消失,segment 形态交替出现:
      - segment[1] = ``<format1>\\x00``                 ← 纯 format 段
      - segment[2] = ``\\n<stat1>\\n\\n<format2>\\x00`` ← stat+format 混合段
      - segment[3] = ``\\n<stat2>\\n\\n<format3>\\x00`` ← 同上
      - segment[4] = ``\\n<stat3>``                     ← 末段纯 stat

    merge commit 没有 stat 块,所以对应位置是空(stat = ``{0,0,0}``)。

    **解析策略**:在每段中定位第一个 40-hex-字符 + NUL 的位置(``<sha>\\x00``),
    该位置之前是上一 commit 的 stat 残留,该位置之后是当前 commit 的 format
    字段。用正则定位而非 ``find('\\n')``,因为 body 字段可能含换行符(``%b``
    默认不剥换行)。

    字段格式必须与 ``LOG_FORMAT`` 11 字段 NUL 切分模板一一对应。
    """
    if not raw:
        return []

    # 锚定每个 commit format 段的起点:<40 hex chars><NUL>
    _SHA_ANCHOR_RE = re.compile(r"[0-9a-f]{40}\x00")
    _ZERO_STAT = {"files": 0, "additions": 0, "deletions": 0}

    segments = raw.split(_SENTINEL_END)
    # WHY ``segments`` 而非 ``segments[1:]``:哨兵之前的整段是**第一个
    # commit 的 format**(不是空段)。raw 结构:
    #   ``<format1>\x00@@SPREC_END@@\n<stat1>\n\n<format2>\x00@@SPREC_END@@...``
    # split 后 segments[0] = ``<format1>\x00``,segments[1] = stat1+format2,
    # 以此类推。必须从 segments[0] 开始遍历,否则丢失第一个 commit。

    commits: list[dict] = []
    pending_format: dict | None = None
    pending_stat_lines: list[str] = []

    def _finalize_current() -> None:
        """Build a commit from pending format + stat, append to commits."""
        nonlocal pending_format, pending_stat_lines
        if pending_format is None:
            return
        stat = _ZERO_STAT
        if pending_stat_lines:
            stats = _parse_log_shortstat("\n".join(pending_stat_lines))
            if stats:
                stat = stats[0]
        commits.append({**pending_format, "shortstat": stat})
        pending_format = None
        pending_stat_lines = []

    for seg in segments:
        if not seg:
            continue
        # 定位当前 commit format 起点
        m = _SHA_ANCHOR_RE.search(seg)
        if m is None:
            # 没有 SHA 锚 → 纯 stat 段(末段或合并提交的 stat 缺失)
            # 累积到当前 pending commit 的 stat 缓冲
            for line in seg.split("\n"):
                if line.strip():
                    pending_stat_lines.append(line.strip())
            continue

        fmt_start = m.start()
        stat_residue = seg[:fmt_start]  # 上一 commit 的 stat 行(可能为空)
        # 注意:**不要** ``rstrip("\\x00\\n")`` —— root commit (无 parents)
        # 的格式以 ``\\x00\\x00`` 结尾(rstrip 会把两个都剥掉,fields 变 10)。
        # 末尾 NUL 由 ``fields[:11]`` 自然丢弃。
        fmt_text = seg[fmt_start:]

        # 关键:stat_residue 是**上一 commit** 的 stat 行(位于哨兵后、
        # 下一 commit format 前)。把它们累计进 pending_stat_lines
        # 后再 finalize,否则上一 commit 会拿到空 stat(回归 bug)。
        # WHY:raw 输出形态是 ``\n<stat_prev>\n\n<format_curr>...``,
        # stat 行物理位置在 format 之前但语义属于前一个 commit。
        if stat_residue.strip():
            for line in stat_residue.split("\n"):
                if line.strip():
                    pending_stat_lines.append(line.strip())

        # 先 finalize 上一 commit
        _finalize_current()

        # 解析新 commit 的 11 个 format 字段
        fields = fmt_text.split("\x00")
        if len(fields) < 11:
            # 不完整(可能截断末段),跳过;但已有 pending 已被 finalize
            continue
        sha, sha_short, an, ae, cn, ce, aI, cI, subject, body, parents_raw = fields[:11]
        if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
            continue
        parents = parents_raw.split() if parents_raw.strip() else []
        pending_format = {
            "sha": sha,
            "sha_short": sha_short,
            "author": {"name": an, "email": ae},
            "committer": {"name": cn, "email": ce},
            "date": aI,
            "subject": subject,
            "body": body if body else None,
            "parents": parents,
        }
        # 当前 segment 的 stat_residue 是**上一** commit 的 stat,
        # 不是当前 commit 的。Stat_residue 已被 finalize 消费。
        # 这里**不要**把它当作新 commit 的 stat(常见误判)。

    _finalize_current()
    return commits


# ──────────────────────────────────────────────────────────
# ETag 计算
# ──────────────────────────────────────────────────────────


async def _compute_log_etag(
    git_bin: str,
    directory: str,
    *,
    query_fingerprint: str = "",
) -> str:
    """为 git-log 端点计算弱 ETag。

    v3.10 (2026-07-01) 修复 (Plan A): 引入 ``query_fingerprint`` 参数,
    把 query string 维度 (ref / n / path / author / since / until) 的指纹
    纳入 ETag。

    Bug 背景: 旧算法 ETag 只基于 ``(head_sha, wt_mtime, idx_mtime)``,
    不区分 query 参数。dashboard 场景:
      1. 用户搜索 author=elec → 后端返回 ETag_A + 过滤结果
      2. 用户点"重置" → URL 不再带 author → 前端可能带 ETag_A
         (或根本没缓存 default ETag)
      3. 后端 ETag_A == default ETag → 304 空 body → UI 显示空

    修复: query_fingerprint 参与 cache key 与 ETag 字符串, author / path
    / ref 等任意参数变化 → ETag 必变 → 304 误判被消除。

    Args:
        git_bin: ``git`` 可执行路径(由 caller 解析)
        directory: 工作树根目录
        query_fingerprint: 稳定的 query 字符串指纹 (由 caller 在 n/ref/path/
            author/since/until 校验通过后构造),用 ``|`` 分隔保持可读。
            为空串时行为与 v3.9 等价(无 query 维度的请求)。
    """
    cache_key = (
        f"{directory}\x00{query_fingerprint}" if query_fingerprint else directory
    )
    now = _time.monotonic()
    cached = _LOG_ETAG_CACHE.get(cache_key)
    if cached is not None and (now - cached[1]) < _LOG_ETAG_TTL:
        _LOG_ETAG_CACHE.move_to_end(cache_key)
        return cached[0]

    head_sha = "no-head"
    try:
        head_result = await _run_git_async(
            [git_bin, "-C", directory, "rev-parse", "HEAD"],
            timeout=5.0,
            encoding="utf-8",
        )
        if head_result.get("ok") and head_result.get("stdout"):
            head_sha = head_result["stdout"]
    except Exception:
        pass

    wt_mtime = 0
    try:
        wt_mtime = int(Path(directory).stat().st_mtime)
    except OSError:
        pass

    idx_mtime = 0
    try:
        idx_mtime = int((Path(directory) / ".git" / "index").stat().st_mtime)
    except OSError:
        pass

    # 拼 ETag: query_fingerprint 拼在 wt_mtime 之后(避免 head_sha / wt_mtime
    # 之间被截断的可读性下降)
    if query_fingerprint:
        etag = f'W/"{head_sha}-{wt_mtime}-{idx_mtime}-{query_fingerprint}"'
    else:
        etag = f'W/"{head_sha}-{wt_mtime}-{idx_mtime}"'

    _LOG_ETAG_CACHE[cache_key] = (etag, now)
    while len(_LOG_ETAG_CACHE) > _LOG_ETAG_CACHE_MAX:
        _LOG_ETAG_CACHE.popitem(last=False)
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
    """GET /spcode/git-log handler。

    Spec: docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md §D
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. Query 参数解析(走 web.request.query) ──
    from astrbot.api import web

    query = web.request.query if hasattr(web, "request") else {}

    def _qget(key: str, default: str | None = None) -> str | None:
        try:
            v = query.get(key)
            return v if v else default
        except Exception:
            return default

    # n 解析
    n_raw = _qget("n")
    if n_raw is not None:
        try:
            n = int(n_raw)
        except ValueError:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                loaded=False,
                umo=umo,
                worktree=worktree,
            )
        n = max(1, min(n, MAX_LOG_N))
    else:
        n = DEFAULT_LOG_N

    ref = _qget("ref") or "HEAD"
    path = _qget("path")
    author = _qget("author")
    since = _qget("since")
    until = _qget("until")

    # 长度校验
    for name, val in (("ref", ref), ("path", path), ("author", author)):
        if val and len(val) > MAX_PARAM_LENGTH:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                loaded=False,
                umo=umo,
                worktree=worktree,
            )

    # ISO date 校验
    iso_date_re = re.compile(
        r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}([+-]\d{2}:?\d{2}|Z)?)?$"
    )
    for name, val in (("since", since), ("until", until)):
        if val and not iso_date_re.match(val):
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                loaded=False,
                umo=umo,
                worktree=worktree,
            )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin,
        umo=umo,
        worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        # git-log 端点契约: 失败路径也要有 ``loaded`` 字段
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # ── 3. path 4 步防御 ──
    if path:
        target, path_err = _validate_repo_relative_file(path, Path(directory))
        if path_err is not None:
            return _make_envelope(
                success=False,
                reason=ReasonCode.PATH_UNSAFE,
                elapsed_ms=_elapsed(),
                loaded=False,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
            )

    # ── 4. ETag 检查 (v3.10 修复: query fingerprint 纳入 ETag) ──
    # 把 query string 维度 (ref / n / path / author / since / until) 序列化
    # 为 ``|`` 分隔指纹, 拼进 ETag 字符串与 cache key。author / path / ref
    # 等任意一个变化 → ETag 必变 → 重置 filter 时不会 304 空 body。
    # 用 ``|`` 而不是 ``&`` 是因为后者在 query 里是分隔符, 易混淆; ``|``
    # 是 git porcelain 风格的稳定选择。
    # WHY ``str(…)`` 显式包: type-checker 友好 + 防 None 漏处理(None
    # 在 ``f"|{None}"`` 会变字符串 ``"None"``, 这里用 ``or ""`` 兜底)。
    query_fingerprint = (
        f"{ref or 'HEAD'}|{n}|{path or ''}|{author or ''}|{since or ''}|{until or ''}"
    )
    etag = await _compute_log_etag(
        plugin._git_binary(),
        directory,
        query_fingerprint=query_fingerprint,
    )
    cache_headers = _common_cache_headers(etag)
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)

    # ── 5. 单次 git log 调用 (合并 --pretty=format + --shortstat) ──
    # v3.8 (2026-06-24) 重构:原两次调用(--pretty=format + --shortstat)
    # 按索引硬对齐会错位(详见模块 docstring 与 _parse_combined_log_output
    # docstring)。改为单次 ``--pretty=format:<...@@SPREC_END@@> --shortstat``,
    # 由 _parse_combined_log_output 按哨兵切分天然对齐 commit 与 stat。
    # 副作用:git 子进程从 2 次降到 1 次,延迟减半。
    git_bin = plugin._git_binary()
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    log_args = list(git_prefix) + [
        "log",
        f"--pretty=format:{LOG_FORMAT}",
        "--shortstat",
        f"-n{n + 1}",
    ]
    if author:
        log_args.append(f"--author={author}")
    if since:
        log_args.append(f"--since={since}")
    if until:
        log_args.append(f"--until={until}")
    if ref:
        log_args.append(ref)
    if path:
        log_args += ["--", path]

    raw_result = await _run_git_async(log_args, encoding="utf-8")
    if not raw_result["ok"]:
        stderr = raw_result.get("stderr", "")
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

    # ── 6. 解析 + 截断 ──
    raw = raw_result["stdout"]
    truncated = len(raw) > MAX_LOG_BYTES
    if truncated:
        # 在最后一个完整哨兵处截断,避免留下半个 commit 块。
        # 哨兵形态: ``\x00@@SPREC_END@@``,长度 15 字符(含 NUL 前缀)。
        # 用 rfind 找最后一个完整哨兵的**结束位置**。
        raw = raw[:MAX_LOG_BYTES]
        last_sentinel_end = raw.rfind(_SENTINEL_END)
        if last_sentinel_end != -1:
            # 哨兵前的字段切到哨兵结束(含 NUL 前缀),后续内容丢弃
            raw = raw[: last_sentinel_end + len(_SENTINEL_END)]

    commits = _parse_combined_log_output(raw)
    has_more = len(commits) > n
    if has_more:
        commits = commits[:n]  # _parse_combined_log_output 已嵌入 shortstat

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            ref=ref,
            count=len(commits),
            has_more=has_more,
            commits=commits,
            truncated=truncated,
            max_bytes=MAX_LOG_BYTES,
        ),
        status_code=200,
        headers=cache_headers,
    )
