"""GET /spcode/git-show — 单 commit 详情 + 修改文件列表。

Spec: docs/superpowers/specs/2026-06-25-git-show-design.md
v3.8 (2026-06-25): 新增端点。返回给定 ref 的 commit 元数据 + 修改的文件列表
(name-status + numstat)。
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
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


# ── 端点常量 ──
MAX_PARAM_LENGTH = 512
MAX_SHOW_BYTES = 1 * 1024 * 1024  # 1 MB 硬上限(numstat)
# v3.9 (2026-06-25): 单文件 patch 视图(?path=)硬上限,防极端大文件
MAX_SHOW_FILE_BYTES = 256 * 1024  # 256 KB 单文件 patch 字节上限
MAX_FILES_DEFAULT = 500
MAX_FILES_LIMIT = 2000

# 8 字段 NUL 分隔 pretty=format 模板(比 git-log 少 committer / body_full 字段)
# %H  full sha | %P parents (space-separated) | %an author name | %ae author email
# %aI author date ISO | %s subject | %b body | @@SPREC_END@@ sentinel
SHOW_FORMAT = "%H%x00%P%x00%an%x00%ae%x00%aI%x00%s%x00%b%x00@@SPREC_END@@"
_SENTINEL_END = "@@SPREC_END@@"

# git-show 端点专用正则
# name-status 行形态:
#   M\t<path>
#   A\t<path>
#   D\t<path>
#   R<sim>\t<old_path>\t<new_path>
#   C<sim>\t<old_path>\t<new_path>
#   T\t<path>          (type change,git show 默认就报,这里做 "M" 处理)
_NAME_STATUS_RE = re.compile(
    r"^(?P<status>[AMDRCT])"
    r"(?P<sim>\d{1,3})?"  # rename/copy 的相似度 0-100
    r"\t"
    r"(?P<old>[^\t\n]+)"  # 旧路径(R/C)或新路径(M/A/D/T)
    r"(?:\t(?P<new>[^\t\n]+))?"  # 仅 R/C 有新路径
    r"$"
)
# numstat 行形态:
#   <add>\t<del>\t<path>
#   -\t-\t<path>     (binary,git 无法统计)
#   -\t-\t<old>\t<new>  (rename/copy binary)
_NUMSTAT_RE = re.compile(
    r"^(?P<add>\d+|-)\t(?P<del>\d+|-)"
    r"\t"
    r"(?P<old>[^\t\n]+)"
    r"(?:\t(?P<new>[^\t\n]+))?"
    r"$"
)
# 哨兵前的 format 段切分:8 字段 = 1 commit 元数据块
_FMT_FIELDS_COUNT = 8
_SHA_ANCHOR_RE = re.compile(r"^[0-9a-f]{40}\x00")

# ── git-show ETag in-memory 缓存 ──
# 复用 git-log / git-diff 模式:HEAD SHA + worktree mtime + .git/index mtime,
# 1.5s TTL。rebase / amend 改写 HEAD → ETag 自动失效。
_SHOW_ETAG_TTL: float = 1.5
_SHOW_ETAG_CACHE_MAX = 64
_SHOW_ETAG_CACHE: OrderedDict[str, tuple[str, float]] = OrderedDict()


# ──────────────────────────────────────────────────────────
# 解析器
# ──────────────────────────────────────────────────────────


def _parse_name_status_lines(block: str) -> list[dict]:
    """解析 ``git show --name-status`` 输出块(去掉尾部空行)。

    Args:
        block: name-status 块,每行格式 ``<status>\\t<old>[\\t<new>]``。

    Returns:
        list of dict:
          - ``{path, status, old_path?, similarity?}``
          - M/A/D/T:``path`` = 实际路径,``status`` = 单字母
          - R/C:``path`` = 新路径,``old_path`` = 旧路径,``similarity`` = int 0-100
    """
    entries: list[dict] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _NAME_STATUS_RE.match(line)
        if not m:
            # 防御:解析失败跳过该行(不破坏整体)
            continue
        status = m.group("status")
        sim_raw = m.group("sim")
        sim = int(sim_raw) if sim_raw is not None else 100  # M/A/D/T sim 缺省视为 100
        old = m.group("old")
        new = m.group("new")
        if status in ("R", "C"):
            entries.append(
                {
                    "path": new or old,
                    "status": status,
                    "old_path": old,
                    "similarity": sim,
                }
            )
        else:
            # M/A/D/T
            entries.append(
                {
                    "path": old,
                    "status": status if status != "T" else "M",  # T 归为 M
                }
            )
    return entries


def _parse_numstat_lines(block: str) -> dict[str, tuple[int, int]]:
    """解析 ``git show --numstat`` 输出块,按 **新路径** 索引 (add, del)。

    Args:
        block: numstat 块,每行 ``<add>\\t<del>\\t<old>[\\t<new>]``。

    Returns:
        ``{path: (add, del)}``,key 为新路径(R/C 用 new,其它用 old);
        ``-`` 计为 0(binary 文件无法统计)。
    """
    out: dict[str, tuple[int, int]] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _NUMSTAT_RE.match(line)
        if not m:
            continue
        add_raw = m.group("add")
        del_raw = m.group("del")
        add = int(add_raw) if add_raw.isdigit() else 0
        delete = int(del_raw) if del_raw.isdigit() else 0
        new = m.group("new")
        # key 选新路径(R/C)或旧路径(其它);name-status 也是按新路径排,
        # 合并时按"新路径"键对齐才不出错。
        key = new if new is not None else m.group("old")
        out[key] = (add, delete)
    return out


def _is_target_diff_line(line: str, target_path: str) -> bool:
    """``diff --git a/PATH b/PATH`` 行是否对应 target_path(新路径)。

    ``git show <ref> -- <path>`` 的 diff --git 头 b/ 段是新路径;
    R/C 时与 a/ 不同。M/A/D/T 时 a/ == b/。
    """
    parts = line.split(" ")
    if len(parts) < 4:
        return False
    b_path = parts[3]
    if b_path.startswith("b/"):
        return b_path[2:] == target_path
    return b_path == target_path


def _parse_single_file_patch(raw: str, target_path: str) -> dict:
    """解析 ``git show <ref> -- <path> --no-color --no-ext-diff`` 输出。

    v3.9 (2026-06-25): 单文件 patch 视图(扩展 git-show 端点,加 ?path=)。
    仅提取目标文件的 unified diff 段(忽略 commit header / Author / Date
    / 其它文件的 diff)。``git show`` 默认会输出完整 commit header,故需要
    状态机式逐行扫描定位目标文件。

    Args:
        raw: ``git show <ref> -- <path> --no-color --no-ext-diff`` 的完整 stdout。
        target_path: 仓库相对路径,需与 ``diff --git`` 行的 b/ 段严格匹配。

    Returns:
        dict 包含:
          - ``path``: 实际新路径(R/C 时为 new_path; 其它 = target_path)
          - ``old_path``: 仅 R/C 有值
          - ``status``: "M" / "A" / "D" / "R" / "C" / "unknown"
          - ``additions`` / ``deletions``: 从 hunk body 累加
          - ``is_binary``: 看到 ``Binary files ... differ`` 时 True
          - ``patch``: 完整 unified diff 文本(含 diff --git / --- / +++ / hunks);
            binary 或 path 不匹配时为 None(让前端渲染 fallback 占位)
    """
    result: dict = {
        "path": target_path,
        "old_path": None,
        "status": "unknown",
        "additions": 0,
        "deletions": 0,
        "is_binary": False,
        "patch": None,
    }
    if not raw or not target_path:
        return result

    in_target_diff = False
    in_hunk = False
    patch_lines: list[str] = []

    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip("\n").rstrip("\r")

        if stripped.startswith("diff --git "):
            # 切换到下一个 diff 段(单文件场景通常只 1 段;多段则严格匹配 target)
            in_target_diff = _is_target_diff_line(stripped, target_path)
            in_hunk = False
            if in_target_diff:
                # 默认 M,被后续 new/deleted/rename/binary 头覆盖
                result["status"] = "M"
                patch_lines.append(line)
            continue

        if not in_target_diff:
            continue

        # 状态头解析
        if stripped.startswith("new file mode"):
            result["status"] = "A"
            patch_lines.append(line)
            continue
        if stripped.startswith("deleted file mode"):
            result["status"] = "D"
            patch_lines.append(line)
            continue
        if stripped.startswith("rename from "):
            result["old_path"] = stripped[len("rename from ") :]
            result["status"] = "R"
            patch_lines.append(line)
            continue
        if stripped.startswith("rename to "):
            result["path"] = stripped[len("rename to ") :]
            patch_lines.append(line)
            continue
        if stripped.startswith("copy from "):
            result["old_path"] = stripped[len("copy from ") :]
            result["status"] = "C"
            patch_lines.append(line)
            continue
        if stripped.startswith("copy to "):
            result["path"] = stripped[len("copy to ") :]
            patch_lines.append(line)
            continue
        if stripped.startswith("Binary files "):
            result["is_binary"] = True
            result["status"] = "M"
            patch_lines.append(line)
            # binary 后续无 hunk,提前结束(无意义继续扫)
            break

        # hunk 头
        if stripped.startswith("@@"):
            in_hunk = True
            patch_lines.append(line)
            continue

        if in_hunk:
            # 累加 +/-(排除文件头 --- / +++ 的"伪"前缀)
            if stripped.startswith("+") and not stripped.startswith("+++"):
                result["additions"] += 1
            elif stripped.startswith("-") and not stripped.startswith("---"):
                result["deletions"] += 1
            patch_lines.append(line)
        else:
            # diff 头部(index / --- / +++)
            patch_lines.append(line)

    if result["is_binary"]:
        # binary 无可显示文本,patch 置 None 让前端渲染 binaryFile 占位
        result["patch"] = None
    elif patch_lines:
        result["patch"] = "".join(patch_lines)
    # else: path 不匹配 / 空输入,保持 patch=None

    return result


def _parse_format_block(format_text: str) -> dict | None:
    """解析哨兵前的 format 段(8 字段 NUL 分隔)。

    关键陷阱:哨兵前的 format 段末尾通常有 1~2 个 trailing NUL(由模板
    ``%H\\x00%P\\x00...%b\\x00@@SPREC_END@@`` 决定)。**不**主动 pop
    trailing 空段,直接用 sha 验证 + 字段数 ≥ 8 双重防御,避免误删。
    """
    # 先 strip 所有换行(哨兵后到 name-status 段之间有 \\n)
    fmt_clean = format_text.replace("\n", "").replace("\r", "")
    parts = fmt_clean.split("\x00")
    # 字段数兜底:trailing 空段计入,只要 >= 8 就 OK
    if len(parts) < _FMT_FIELDS_COUNT:
        return None
    sha = parts[0]
    # sha 验证(40 hex)→ 不通过直接 None,挡掉非 commit 数据片段
    if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
        return None
    # 即使中间有空字段(parents 缺失)也按位置取,空值用空串兜底
    parents_raw = parts[1] if len(parts) > 1 else ""
    an = parts[2] if len(parts) > 2 else ""
    ae = parts[3] if len(parts) > 3 else ""
    aI = parts[4] if len(parts) > 4 else ""
    subject = parts[5] if len(parts) > 5 else ""
    body = parts[6] if len(parts) > 6 else ""
    parents = parents_raw.split() if parents_raw.strip() else []
    return {
        "sha": sha,
        "parents": parents,
        "author": {"name": an, "email": ae},
        "date": aI,
        "subject": subject,
        "body": body if body else None,
    }


# ──────────────────────────────────────────────────────────
# ETag 计算
# ──────────────────────────────────────────────────────────


async def _compute_show_etag(git_bin: str, directory: str) -> str:
    """为 git-show 端点计算弱 ETag。"""
    now = _time.monotonic()
    cached = _SHOW_ETAG_CACHE.get(directory)
    if cached is not None and (now - cached[1]) < _SHOW_ETAG_TTL:
        _SHOW_ETAG_CACHE.move_to_end(directory)
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

    etag = f'W/"{head_sha}-{wt_mtime}-{idx_mtime}"'

    _SHOW_ETAG_CACHE[directory] = (etag, now)
    while len(_SHOW_ETAG_CACHE) > _SHOW_ETAG_CACHE_MAX:
        _SHOW_ETAG_CACHE.popitem(last=False)
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
    """GET /spcode/git-show handler。

    Spec: docs/superpowers/specs/2026-06-25-git-show-design.md

    Query 参数:
      - ``ref`` (默认 ``HEAD``):任意 git ref(commit SHA / branch / tag / HEAD~3)
      - ``max_files`` (默认 500, max 2000):返回文件数硬上限

    Returns:
        ``{"status": "ok", "data": {loaded, directory, umo, worktree, ref,
        resolved_sha, files, count, truncated, max_files, elapsed_ms,
        reason, stderr}}``
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. Query 参数解析 ──
    from astrbot.api import web

    query = web.request.query if hasattr(web, "request") else {}

    def _qget(key: str, default: str | None = None) -> str | None:
        try:
            v = query.get(key)
            return v if v else default
        except Exception:
            return default

    ref = _qget("ref") or "HEAD"

    # max_files 解析(1 ~ MAX_FILES_LIMIT)
    max_files_raw = _qget("max_files")
    if max_files_raw is not None:
        try:
            max_files = int(max_files_raw)
        except ValueError:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                loaded=False,
                umo=umo,
                worktree=worktree,
            )
        max_files = max(1, min(max_files, MAX_FILES_LIMIT))
    else:
        max_files = MAX_FILES_DEFAULT

    # ref 长度校验(防注入超长)
    if len(ref) > MAX_PARAM_LENGTH:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            loaded=False,
            umo=umo,
            worktree=worktree,
        )

    # ── 1b. 可选 ?path= 单文件 patch 视图(v3.9, 2026-06-25) ──
    # 不传时走 v3.8 现有逻辑(响应 schema 不变);传了则在最终响应里加
    # ``file: {path, patch, additions, deletions, is_binary, old_path, status}``。
    # path 必须是仓库相对路径(与 git diff name-status 一致),不能跨工作树逃逸。
    target_path = _qget("path")
    if target_path is not None:
        target_path = target_path.strip()
        if not target_path or len(target_path) > MAX_PARAM_LENGTH:
            return _make_envelope(
                success=False,
                reason=ReasonCode.INVALID_PARAM,
                elapsed_ms=_elapsed(),
                loaded=False,
                umo=umo,
                worktree=worktree,
            )
        # 拒绝包含换行 / NUL 的 path(防止注入到 git args)
        if "\n" in target_path or "\r" in target_path or "\x00" in target_path:
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
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    git_bin = plugin._git_binary()
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # ── 3. ETag 检查 ──
    etag = await _compute_show_etag(git_bin, directory)
    cache_headers = _common_cache_headers(etag)
    if _get_if_none_match() == etag:
        return _make_304_response(cache_headers)

    # ── 4. 第一次 git show: --name-status + format ──
    # WHY 不用 ``--name-status --numstat`` 同时:git 不支持,会忽略后者。
    # 改为两次调用,按"新路径"键合并 (add, del) 数字。
    ns_args = list(git_prefix) + [
        "show",
        f"--pretty=format:{SHOW_FORMAT}",
        "--name-status",
        ref,
    ]
    ns_result = await _run_git_async(ns_args, encoding="utf-8")
    if not ns_result["ok"]:
        stderr = ns_result.get("stderr", "")
        stderr_lower = stderr.lower()
        # git 在不同版本/不同 ref 形式下 stderr 略有差异,覆盖主要 case:
        #   - `git show 000000...` → "fatal: bad object 000000..."
        #   - `git show nonexistent-branch` → "fatal: unknown revision ..."
        #   - `git show HEAD` in empty repo → "fatal: ambiguous argument 'HEAD'..."
        #   - `git show HEAD~999` → "fatal: bad revision ..."
        if (
            "bad object" in stderr_lower
            or "unknown revision" in stderr_lower
            or "bad revision" in stderr_lower
            or "not a commit" in stderr_lower
        ):
            reason = ReasonCode.REF_NOT_FOUND
        elif "does not have any commits" in stderr or "ambiguous" in stderr_lower:
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

    # ── 5. 解析 format + name-status ──
    raw_ns = ns_result["stdout"]
    if _SENTINEL_END not in raw_ns:
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr="git show 输出缺少哨兵 @@SPREC_END@@",
        )

    format_text, _, after_sentinel = raw_ns.partition(_SENTINEL_END)
    # after_sentinel = "\n" + name-status 块(可能为空)
    name_status_block = after_sentinel.lstrip("\n").lstrip("\r")

    commit_meta = _parse_format_block(format_text)
    if commit_meta is None:
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            loaded=False,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
            stderr="无法解析 git show format 段",
        )

    files = _parse_name_status_lines(name_status_block)
    if not files:
        # merge commit 也可能有文件改动;root commit 一定有内容;
        # 空 files 列表是真实状态(无文件修改 — 极少见,允许)
        return _JSONResponseCompat(
            _make_envelope(
                success=True,
                elapsed_ms=_elapsed(),
                loaded=True,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                ref=ref,
                resolved_sha=commit_meta["sha"],
                files=[],
                count=0,
                truncated=False,
                max_files=max_files,
                **{
                    k: commit_meta[k]
                    for k in (
                        "parents",
                        "author",
                        "date",
                        "subject",
                        "body",
                    )
                },
            ),
            status_code=200,
            headers=cache_headers,
        )

    # ── 6. 第二次 git show: --numstat 取 (add, del) ──
    ns_args2 = list(git_prefix) + [
        "show",
        "--pretty=format:",
        "--numstat",
        ref,
    ]
    ns_result2 = await _run_git_async(ns_args2, encoding="utf-8")
    numstat_map: dict[str, tuple[int, int]] = {}
    if ns_result2["ok"]:
        # 字节数硬上限检查(防 1 commit 改 10000 文件的极端 case)
        stdout2 = ns_result2["stdout"]
        if len(stdout2) > MAX_SHOW_BYTES:
            return _make_envelope(
                success=False,
                reason=ReasonCode.COMMIT_TOO_LARGE,
                elapsed_ms=_elapsed(),
                loaded=False,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=f"numstat 输出 {len(stdout2)} 字节超过 {MAX_SHOW_BYTES} 字节上限",
            )
        numstat_map = _parse_numstat_lines(stdout2)
    else:
        # numstat 失败不致命(可能 binary / ref 被 rewind),fallback 0/0
        logger.warning(
            "git show --numstat 失败,fallback 0/0: %s", ns_result2.get("stderr", "")
        )

    # ── 7. 合并 name-status + numstat,按"新路径"键 ──
    for entry in files:
        key = entry["path"]
        add, delete = numstat_map.get(key, (0, 0))
        entry["additions"] = add
        entry["deletions"] = delete

    # ── 8. 应用 max_files 截断 ──
    truncated = len(files) > max_files
    if truncated:
        files = files[:max_files]

    # ── 8b. 可选:单文件 patch 视图(?path= 时, v3.9) ──
    # 复用 v3.8 已解析的 commit meta(不重复跑 git),额外调一次
    # ``git show <ref> -- <path> --no-color --no-ext-diff`` 取单文件 patch。
    # 若 target_path 不在该 ref 的 files 列表里(防御:caller 给了错的 path),
    # 解析器返回 status="unknown" + patch=None,前端据此渲染占位;
    # 不会让整个 commit 元数据请求失败(与 git show name-status 解耦)。
    file_view: dict | None = None
    if target_path is not None:
        patch_args = list(git_prefix) + [
            "show",
            "--no-color",
            "--no-ext-diff",
            ref,
            "--",
            target_path,
        ]
        patch_result = await _run_git_async(patch_args, encoding="utf-8")
        if patch_result["ok"]:
            stdout_p = patch_result["stdout"]
            if len(stdout_p) > MAX_SHOW_FILE_BYTES:
                return _make_envelope(
                    success=False,
                    reason=ReasonCode.COMMIT_TOO_LARGE,
                    elapsed_ms=_elapsed(),
                    loaded=False,
                    directory=directory,
                    umo=effective_umo,
                    worktree=directory,
                    stderr=(
                        f"单文件 patch 输出 {len(stdout_p)} 字节超过"
                        f" {MAX_SHOW_FILE_BYTES} 字节上限"
                    ),
                )
            file_view = _parse_single_file_patch(stdout_p, target_path)
        else:
            # 单文件 git show 失败(可能 ref 与 path 不匹配):不阻塞整个 commit
            # 视图,记 warning 并让 file_view=None,前端渲染占位。
            logger.warning(
                "git show %s -- %s 失败: %s",
                ref,
                target_path,
                patch_result.get("stderr", ""),
            )
            file_view = None

    # ── 9. 构造响应 ──
    response_data = _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=directory,
        ref=ref,
        resolved_sha=commit_meta["sha"],
        files=files,
        count=len(files),
        truncated=truncated,
        max_files=max_files,
        **{
            k: commit_meta[k]
            for k in (
                "parents",
                "author",
                "date",
                "subject",
                "body",
            )
        },
    )
    # v3.9: 透传单文件 patch 视图(仅在 ?path= 给出时附加;不传时 100% 向后兼容)
    if file_view is not None:
        # file 字段加在 data 子 dict 里,符合 envelope 协议
        response_data["data"]["file"] = file_view
    return _JSONResponseCompat(
        response_data,
        status_code=200,
        headers=cache_headers,
    )
