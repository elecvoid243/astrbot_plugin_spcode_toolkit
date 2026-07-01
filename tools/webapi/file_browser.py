# tools/webapi/file_browser.py
"""GET /spcode/file-browser — 浏览/读取文件/目录/符号链接。

Spec: docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md
"""

from __future__ import annotations
import logging
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import _JSONResponseCompat

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# ── file-browser 端点常量(v3.2) ──
# 单一真相源,handler 与测试共享;详情见
# docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md §5
FILE_BROWSER_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB 文件大小硬上限
FILE_BROWSER_MAX_ENTRIES: int = 1000  # 单层目录最大返回项数
FILE_BROWSER_SNIFF_BYTES: int = 8192  # 8 KB 二进制探测窗口
_TYPE_ORDER: dict[str, int] = {"directory": 0, "file": 1, "symlink": 2}


def _compute_file_etag(path: Path, st: os.stat_result | None = None) -> str | None:
    """为单个文件计算弱 ETag(mtime_ns + size),失败返回 ``None``。

    Args:
        path: 文件 / 目录路径。
        st: 可选 — caller 已有的 ``os.stat_result``,提供时复用(避免重复 lstat);
            为 None 时函数内部 ``path.lstat()`` 一次。

    Returns:
        弱 ETag 字符串(如 ``W/"1234567890-100"``);失败 / lstat 错误 → ``None``。
    """
    if st is None:
        try:
            st = path.lstat()
        except OSError:
            return None
    return f'W/"{st.st_mtime_ns}-{st.st_size}"'


def _common_cache_headers(etag: str | None) -> dict[str, str]:
    """构造 HTTP 缓存响应头。

    Args:
        etag: 弱 ETag 字符串(如 ``W/"abc-123"``);为 None 时不带 ETag。

    Returns:
        ``{ETag, Cache-Control, Vary}`` dict(供 JSONResponse 注入)。
    """
    headers: dict[str, str] = {
        "Cache-Control": "private, must-revalidate",
        "Vary": "Cookie",
    }
    if etag:
        headers["ETag"] = etag
    return headers


def _get_if_none_match() -> str:
    """从当前 web request 取 ``If-None-Match`` 头,失败返回空串。"""
    try:
        from astrbot.api import web

        return web.request.headers.get("If-None-Match", "") or ""
    except Exception:
        return ""


def _make_304_response(headers: dict[str, str]) -> _JSONResponseCompat:
    """构造 304 Not Modified 响应(空 body + 缓存头)。"""
    return _JSONResponseCompat({}, status_code=304, headers=headers)


def _build_error_response(path: str | Path, reason: str) -> dict:
    """构造错误响应(3 字段:type=null, path, reason)。

    详见 docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md §6.4。
    """
    return {"type": None, "path": str(path), "reason": reason}


def _classify_entry(p: Path) -> tuple[str, os.stat_result | None]:
    """不跟随 symlink 的类型分类 + 返回 stat 结果供 caller 复用。

    Returns:
        ``(entry_type, st)`` 元组:
        - ``entry_type``: ``directory`` / ``file`` / ``symlink`` / ``special`` (FIFO/socket/device)
        - ``st``: ``os.stat_result`` 或 ``None``(symlink / 失败)

    WHY: Python 3.12 build 缺 ``Path.is_dir(follow_symlinks=)`` 参数(仅在
    Python ≥ 3.13 才正式支持);改用 ``Path.lstat()`` + ``stat.S_ISDIR`` 走底层
    ``stat.S_IFMT`` 判定,与 spec §7.3 "用 lstat 不跟随" 等价。

    v3.4 (2026-06-21) P2 perf: 返回 ``st`` 而不仅是 type,让 caller 复用
    这次 ``Path.lstat()`` 结果算 ETag(避免后续 ``_compute_file_etag`` 重复 lstat),
    整个 handle 目录 / 文件路径 lstat 次数从 2 → 1(directory)/ 3 → 1(file)。
    """
    try:
        st = p.lstat()
    except OSError:
        return "special", None
    if stat.S_ISLNK(st.st_mode):
        return "symlink", None
    mode = stat.S_IFMT(st.st_mode)
    if mode == stat.S_IFDIR:
        return "directory", st
    if mode == stat.S_IFREG:
        return "file", st
    return "special", None


def _safe_lstat_mtime(
    path: Path,
    st: os.stat_result | None = None,
) -> float | None:
    """安全读 mtime: lstat 失败 / st_mtime 访问失败 → None。

    不抛异常;与 spec §6 "文件 mtime 失败" 边界一致。

    Args:
        path: 路径(``st=None`` 时用于内部 lstat)。
        st: 可选 — caller 已有的 stat 结果;提供时直接读 ``st_mtime``,省 1 次 lstat。
    """
    if st is None:
        try:
            st = path.lstat()
        except OSError:
            return None
    try:
        return float(st.st_mtime)
    except (OSError, ValueError):
        return None


def _make_entry(p: Path) -> dict:
    """构造单个 entry dict(目录列表项)。

    不跟随 symlink (lstat)。
    性能优化 (v3.3,2026-06-21): 之前对每个 entry 调 5-7 次 stat / follow syscalls
    (lstat + is_symlink + os.stat(follow=False) + lstat + is_symlink + is_symlink + readlink + exists);
    现版 1 次 lstat 拿全部 mode/size/mtime 信息,symlink 额外 1 次 readlink + 1 次
    target_exists 探测。1000 项目录从 ~5000-7000 syscalls 降到 ~1000-2000。
    """
    try:
        st = p.lstat()  # 唯一一次主 syscall
    except OSError:
        raise
    mode = st.st_mode
    is_sym = stat.S_ISLNK(mode)
    if is_sym:
        etype = "symlink"
    elif stat.S_ISDIR(mode):
        etype = "directory"
    elif stat.S_ISREG(mode):
        etype = "file"
    else:
        etype = "special"
    # mtime: 极个别 FS(网络盘 / 特殊 FUSE)在 lstat 成功但 st_mtime
    # 访问时仍会抛 OSError,沿用旧 _safe_lstat_mtime 的兜底语义。
    try:
        mtime: float | None = float(st.st_mtime)
    except (OSError, ValueError):
        mtime = None
    entry: dict = {
        "path": str(p),
        "name": p.name,
        "type": etype,
        "size": st.st_size,
        "mtime": mtime,
        "is_symlink": is_sym,
    }
    if is_sym:
        entry["target"] = os.readlink(p)
        # target_exists: 相对 symlink 应相对 symlink 父目录解析
        # (旧实现用 Path(target).exists() 实际是相对 CWD,有 bug;此处修正)
        target = entry["target"]
        if not os.path.isabs(target):
            target = os.path.join(str(p.parent), target)
        try:
            entry["target_exists"] = os.path.exists(target)
        except OSError:
            entry["target_exists"] = False
    return entry


def _build_file_response(
    path: Path,
    file_st: os.stat_result | None = None,
) -> dict:
    """构造文件响应。三种 reason 路径(成功 / file_too_large / binary_file)。

    P1 perf (v3.4, 2026-06-21): 单次 ``open("rb")`` 合并 binary sniff + 全文读。
    旧实现走 ``_is_binary(path)`` + ``path.read_text(...)``,共 2 次 open + 2 次 close。
    新实现:1 次 open → sniff 8K → f.read() → utf-8 decode,合并 1 次 open + 1 次 close。

    P2 perf (v3.4, 2026-06-21): 接受可选 ``file_st`` — caller 已 lstat 时复用,
    内部不再 ``path.lstat()``(省 1 次 syscall)+ ``_safe_lstat_mtime`` 也不再 lstat。
    总 file 路径 lstat 从 3 → 1。

    行为兼容:
    - binary 判定:sniff 8K 含 NUL → binary(同旧 sniff)
    - utf-8 decode 失败 → binary(同旧 read_text UnicodeDecodeError 路径)
    - OSError 不可读 → binary(同旧 _is_binary 异常路径)
    """
    if file_st is None:
        try:
            file_st = path.lstat()
        except OSError as exc:
            return _build_error_response(path, _classify_oserror(exc))
    base: dict = {
        "type": "file",
        "path": str(path),
        "name": path.name,
        "size": file_st.st_size,
        "mtime": _safe_lstat_mtime(path, st=file_st),
        "max_bytes": FILE_BROWSER_MAX_BYTES,
    }
    if file_st.st_size > FILE_BROWSER_MAX_BYTES:
        return {
            **base,
            "encoding": None,
            "is_binary": False,
            "content": None,
            "reason": "file_too_large",
        }
    # 单次 open("rb"):先 sniff 8K,再读全文,最后 utf-8 decode。
    try:
        with path.open("rb") as f:
            sniff = f.read(FILE_BROWSER_SNIFF_BYTES)
            if b"\x00" in sniff:
                return {
                    **base,
                    "encoding": None,
                    "is_binary": True,
                    "content": None,
                    "reason": "binary_file",
                }
            # 读剩余(已经在缓存中的 sniff 数据不必重新读)
            f.seek(0)
            raw = f.read()
    except OSError:
        return {
            **base,
            "encoding": None,
            "is_binary": True,
            "content": None,
            "reason": "binary_file",
        }
    # utf-8 decode:失败等同旧 read_text(UnicodeDecodeError)路径
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            **base,
            "encoding": None,
            "is_binary": True,
            "content": None,
            "reason": "binary_file",
        }
    # 模拟 ``Path.read_text(encoding="utf-8")`` 的 universal newlines 行为:
    # 旧实现在 Windows 上把 ``\r\n`` / ``\r`` 统一为 ``\n``,我们用 ``open("rb")``
    # 拿到的是原始字节,需要手工做这个翻译,保持返回 content 与 v3.3 行为一致。
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    return {
        **base,
        "encoding": "utf-8",
        "is_binary": False,
        "content": content,
        "reason": None,
    }


def _classify_oserror(exc: OSError) -> str:
    """把 OSError 映射到 reason 字符串(EACCES/PermissionError → permission_denied)。

    其他 OSError 视为 path_not_found(资源不可用)。
    """
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if exc.errno == 13:  # EACCES
        return "permission_denied"
    return "path_not_found"


def _build_directory_response(
    path: Path,
    parent_st: os.stat_result | None = None,
) -> dict:
    """构造目录响应。

    - 过滤以 ``.`` 开头的隐藏项
    - 按 ``(_TYPE_ORDER[type], name)`` 排序:directory → file → symlink
    - 超过 ``FILE_BROWSER_MAX_ENTRIES`` 截断
    - 内部算 ETag(可复用 caller 传入的 ``parent_st``,省 1 次 lstat)

    Args:
        path: 目录路径。
        parent_st: 可选 — caller 已经 lstat 过的 stat 结果(用于算 ETag);
            缺省则内部自己 lstat。
    """
    raw_entries: list[dict] = []
    for child in path.iterdir():
        if child.name.startswith("."):
            continue
        try:
            raw_entries.append(_make_entry(child))
        except OSError:
            continue  # lstat 失败(罕见;权限/竞态)— 跳过
    raw_entries.sort(key=lambda e: (_TYPE_ORDER[e["type"]], e["name"]))
    truncated = len(raw_entries) > FILE_BROWSER_MAX_ENTRIES
    entries = raw_entries[:FILE_BROWSER_MAX_ENTRIES]
    # P2 perf (v3.4, 2026-06-21): 复用 caller 的 lstat 结果(若提供)算 ETag,
    # 省 1 次 lstat(path)。旧实现 handle 在 build 后单独调
    # ``_compute_file_etag(path)`` → 重复 lstat。新实现 caller 把 st 传进来,
    # 总 lstat(path) 次数:从 2 (classify_entry + compute_file_etag) → 1。
    dir_etag = _compute_file_etag(path, st=parent_st)
    return {
        "type": "directory",
        "path": str(path),
        "entry_count": len(entries),
        "truncated": truncated,
        "max_entries": FILE_BROWSER_MAX_ENTRIES,
        "entries": entries,
        "etag": dir_etag,  # 新增;handle 复用,避免再 lstat
        "reason": "directory_listing_truncated" if truncated else None,
    }


def _build_symlink_response(path: Path) -> dict:
    """构造顶层 symlink 响应。

    symlink → 文件: target_exists=True(若 target 存在)
    悬空 symlink: target_exists=False
    """
    st = path.lstat()
    target = os.readlink(path)
    return {
        "type": "symlink",
        "path": str(path),
        "name": path.name,
        "size": st.st_size,
        "mtime": _safe_lstat_mtime(path),
        "is_symlink": True,
        "target": target,
        "target_exists": Path(target).exists(),
        "reason": None,
    }


async def handle(
    plugin: "SPCodeToolkit",
) -> dict:
    """Web API handler for ``GET /spcode/file-browser``.

    Query params:
      - ``path`` (必填):绝对路径;指向文件 / 目录 / 符号链接。

    Response envelope::
        {"status": "ok", "data": {<type-specific>}}

    type ∈ {file, directory, symlink, null}。null 表示错误,
    此时 data 含 ``reason`` 字段说明错误类型。
    """
    import astrbot.api.web as _aw

    raw_path = _aw.request.query.get("path", "").strip()
    if not raw_path:
        return {"status": "ok", "data": _build_error_response("", "path_not_found")}
    path = Path(raw_path)
    # 不跟随 symlink 的路径校验(spec §7.2):broken symlink 时
    # path.exists() 返回 False,所以**额外**用 path.is_symlink() 检查
    if not path.exists() and not path.is_symlink():
        return {
            "status": "ok",
            "data": _build_error_response(path, "path_not_found"),
        }
    # 分支调度(spec §7.3):
    #   1. directory → _build_directory_response(+ Cache-Control)
    #   2. file      → _build_file_response(+ ETag/304 路径)
    #   3. symlink   → _build_symlink_response(无缓存)
    #   4. else      → special_file (FIFO/socket/device,无缓存)
    try:
        # P2 perf (v3.4, 2026-06-21): 复用 ``_classify_entry`` 的 lstat 结果算 ETag。
        # 旧实现: ``_classify_entry`` 调 1 次 ``os.stat`` + 后续 ``_compute_file_etag``
        # 调 1 次 ``path.lstat()`` → 同一 path 共 2 次 syscall。
        # 新实现: ``_classify_entry`` 返回 ``st`` → 后续 ``_compute_file_etag``
        # 复用,共 1 次 syscall(directory / file 路径都适用)。
        entry_type, path_st = _classify_entry(path)
        if entry_type == "directory":
            data = _build_directory_response(path, parent_st=path_st)
            # 目录 mtime 作为 ETag(廉价;漏检 in-place 编辑,
            # 接受 1 个 poll 周期延迟,同 git-diff ETag 策略)
            dir_etag = data.get("etag")
            # max-age=2 让 5-10s polling 期间大部分请求直接不发请求
            # (浏览器命中本地缓存),2s 后再用 ETag revalidate
            cache_headers = _common_cache_headers(dir_etag)
            cache_headers["Cache-Control"] = "private, max-age=2, must-revalidate"
            if dir_etag and _get_if_none_match() == dir_etag:
                return _make_304_response(cache_headers)
            return _JSONResponseCompat(
                {"status": "ok", "data": data}, headers=cache_headers
            )
        elif entry_type == "file":
            # P0 perf (v3.4, 2026-06-21): 先算 ETag → 检查 304 → 才 build。
            # 旧顺序在 304 命中场景下白白走一次 read_text(最大 5MB IO),
            # dashboard 5-10s 轮询时,所有缓存命中的请求都重复这个浪费。
            # lstat 一次廉价,而 read_text 在 5MB 文件上 5-200ms。
            # P2 perf (v3.4, 2026-06-21): 复用 ``_classify_entry`` 的 stat,
            # 不再额外 ``path.lstat()``。
            file_etag = _compute_file_etag(path, st=path_st)
            cache_headers = _common_cache_headers(file_etag)
            if file_etag and _get_if_none_match() == file_etag:
                return _make_304_response(cache_headers)
            # P2 perf (v3.4, 2026-06-21): 复用 classify_entry 的 stat,
            # 避免 _build_file_response 再 lstat 2 次(path + mtime)。
            data = _build_file_response(path, file_st=path_st)
            return _JSONResponseCompat(
                {"status": "ok", "data": data}, headers=cache_headers
            )
        elif entry_type == "symlink":
            data = _build_symlink_response(path)
        else:
            data = _build_error_response(path, "special_file")
    except (PermissionError, OSError) as exc:
        logger.warning("file-browser: OSError on %s: %s", path, exc)
        return {
            "status": "ok",
            "data": _build_error_response(path, _classify_oserror(exc)),
        }
    # symlink / special / 其他类型:不缓存(状态不稳定或语义不明确)
    return {"status": "ok", "data": data}

    # ── /spcode/file-restore 端点(POST,v3.5;v3.6 staged 支持) ────
    # Spec: docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md
