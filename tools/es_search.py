"""
es_search — 文件名搜索封装。
- Windows: Everything CLI (es.exe) 毫秒级搜索
- Linux/macOS: locate → fd → Python os.walk 三层 fallback
"""

from __future__ import annotations

import csv
import fnmatch
import io
import os
import shutil
from pathlib import Path

from ._helpers import proposal_reply, run_cmd

# Linux/macOS 跳过目录
_POSIX_SKIP_DIRS = {
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/snap",
    "/var/lib/lxcfs",
    "/var/lib/docker",
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
}

# Python fallback 扫描文件数上限
_MAX_POSIX_FILES = 10000

# 排序映射（Everything CLI 接受的排序参数）
_SORT_MAP = {
    "name": "name",
    "path": "path",
    "size": "size",
    "ext": "extension",
    "date_created": "date-created",
    "date_modified": "date-modified",
    "date_accessed": "date-accessed",
    "run_count": "run-count",
}


def _get_es_path() -> str | None:
    """获取 es.exe 路径（Everything CLI 工具）。返回 None 表示未找到。

    注意：需要 es.exe（CLI 工具），不是 Everything.exe（GUI 主程序）。
    二者参数语义不同：es.exe 用位置参数传 query，Everything.exe 需要 -s/-p 等标志。
    如果 PATH 中只有 Everything.exe 而没有 es.exe，必须单独下载 es.exe。

    查找顺序：
    1. ES_PATH 环境变量（用户显式覆盖）
    2. shutil.which("es.exe")（显式 .exe）
    3. shutil.which("es")（带 PATHEXT 扩展名）
    4. Everything 默认安装路径
    """
    # 1. 环境变量
    env_path = os.environ.get("ES_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return str(p)

    # 2-3. PATH 中查找（先 .exe 再无后缀，Windows PATHEXT 实际会自动处理）
    for name in ("es.exe", "es"):
        found = shutil.which(name)
        if found and Path(found).exists():
            return found

    # 4. Everything 默认安装路径
    for candidate in (
        Path("C:/Program Files/Everything/es.exe"),
        Path("C:/Program Files (x86)/Everything/es.exe"),
    ):
        if candidate.exists():
            return str(candidate)

    return None


def _is_everything_gui(es_path: str) -> bool:
    """检测路径是否指向 Everything.exe（GUI）而非 es.exe（CLI）。"""
    return Path(es_path).name.lower() == "everything.exe"


def search(
    query: str,
    path: str | None = None,
    max_results: int = 100,
    regex: bool = False,
    case_sensitive: bool = False,
    whole_word: bool = False,
    file_type: str = "all",
    sort_by: str | None = None,
    ext: str | None = None,
) -> dict:
    """Everything 文件名搜索。

    Args:
        query: 搜索关键词（支持 Everything 搜索语法如 *.py）
        path: 限定搜索路径，None 表示全盘搜索
        max_results: 最大结果数，默认 100
        regex: 使用正则表达式
        case_sensitive: 区分大小写
        whole_word: 全词匹配
        file_type: "file" / "folder" / "all"
        sort_by: name/path/size/ext/date_created/date_modified/date_accessed/run_count
        ext: 文件扩展名过滤，如 "py" "xlsx" "exe"
    """
    es_path = _get_es_path()
    # 没找到 es.exe
    if not es_path:
        return proposal_reply(
            False,
            "找不到 es.exe（Everything CLI 工具）。请单独下载 es.exe 并放在 PATH 中，"
            "或设置 ES_PATH 环境变量指向 es.exe 完整路径。",
            error="es.exe 未找到",
            evidence={
                "searched": [
                    "ES_PATH 环境变量",
                    "shutil.which('es.exe')",
                    "shutil.which('es')",
                    "C:/Program Files/Everything/es.exe",
                ]
            },
            options=[
                "下载 es.exe: https://www.voidtools.com/support/everything/command_line_interface/",
                "设置 ES_PATH 环境变量指向 es.exe",
                "在 WebUI 的 es_path 字段填入 es.exe 完整路径",
            ],
        )
    # 误把 Everything.exe（GUI）当成 es.exe（CLI）—— 这是最常见的坑
    if _is_everything_gui(es_path):
        return proposal_reply(
            False,
            "es_path 指向了 Everything.exe（GUI 主程序），不是 es.exe（CLI 工具）。"
            "两个工具参数格式不同：Everything.exe 会把 query 当成要打开的文件名，导致弹窗。",
            error="es_path 指向 Everything.exe（GUI）",
            evidence={"detected": es_path, "filename": Path(es_path).name},
            options=[
                "下载 es.exe: https://www.voidtools.com/support/everything/command_line_interface/",
                "将 es_path 改为 es.exe 完整路径（不是 Everything.exe 同目录的 Everything.exe）",
                "把 es.exe 放到 Everything.exe 同目录即可自动找到",
            ],
        )
    # Windows 走 es.exe（CLI 工具）
    return _windows_search(
        es_path,
        query,
        path,
        max_results,
        regex,
        case_sensitive,
        whole_word,
        file_type,
        sort_by,
        ext,
    )


# ── Windows: Everything CLI ─────────────────────────


def _windows_search(
    es_path: str,
    query: str,
    path: str | None,
    max_results: int,
    regex: bool,
    case_sensitive: bool,
    whole_word: bool,
    file_type: str,
    sort_by: str | None,
    ext: str | None,
) -> dict:
    """调用 es.exe，返回结构化结果。"""
    if query.startswith(("/", "-")) and not regex:
        return {
            "ok": False,
            "error": "query 不能以 / 或 - 开头（会被 es.exe 解释为选项）。正则搜索请用 regex=True。",
        }

    args: list[str] = [es_path]
    if regex:
        args.extend(["-r", query])
    else:
        args.append(query)
        if ext:
            args[-1] = f"{args[-1]} *.{ext}"

    if path:
        args.extend(["-path", path])
    if file_type == "file":
        args.append("/a-d")
    elif file_type == "folder":
        args.append("/ad")
    if case_sensitive:
        args.append("-case")
    if whole_word:
        args.append("-w")
    if sort_by and sort_by in _SORT_MAP:
        args.extend(["-sort", _SORT_MAP[sort_by]])
    if max_results > 0:
        args.extend(["-n", str(max_results)])
    args.extend(
        [
            "-csv",
            "-name",
            "-path-column",
            "-size",
            "-size-format",
            "1",
            "-date-modified",
        ]
    )

    proc = run_cmd(args, timeout=15)
    if not proc["ok"]:
        err_msg = proc.get("error", "")
        if "超时" in err_msg:
            return proposal_reply(
                False,
                "Everything 搜索超时 (15s)——尝试缩小搜索范围",
                error="es.exe 搜索超时（15s）",
                evidence={"query": query, "timeout": 15},
                options=["缩小 path 范围", "简化 query 通配符"],
            )
        if "未安装" in err_msg or "不存在" in err_msg:
            return proposal_reply(
                False,
                "es.exe 未找到",
                error=err_msg,
                evidence={"query": query},
                options=["检查 es_path 配置", "安装 Everything + es.exe"],
            )
        return {
            "ok": False,
            "error": proc.get("stderr", "") or f"es.exe 返回码 {proc.get('code')}",
        }

    # 解析 CSV 输出
    reader = csv.DictReader(io.StringIO(proc["stdout"]))
    items: list[dict] = []
    total_size = 0
    for row in reader:
        name = row.get("Name", "").strip('"')
        fpath = row.get("Path", "").strip('"')
        size_str = row.get("Size", "0").strip('"')
        date_mod = row.get("Date Modified", "").strip('"')
        try:
            size = int(size_str)
        except (ValueError, TypeError):
            size = 0
        total_size += size
        items.append(
            {
                "name": name,
                "path": fpath,
                "full": str(Path(fpath) / name) if fpath else name,
                "size": size,
                "date_modified": date_mod,
            }
        )

    result = {
        "ok": True,
        "count": len(items),
        "total_size": total_size,
        "items": items,
    }
    if len(items) == 0:
        result["proposal"] = f"搜索无结果 (query: {query})——尝试放宽条件或移除过滤"
    return result


# ── POSIX: locate → fd → os.walk ───────────────────


def _posix_search(
    query: str,
    path: str | None,
    max_results: int,
    case_sensitive: bool,
    file_type: str,
    ext: str | None,
) -> dict:
    """Linux/macOS 三层 fallback 搜索。"""
    search_root = path or "/"

    # Layer 1: locate
    locate_path = shutil.which("locate")
    if locate_path:
        try:
            loc_query = query.replace("*", "").replace("?", "") or query
            args = [locate_path, "-l", str(max_results)]
            if not case_sensitive:
                args.append("-i")
            args.append(loc_query)
            r = run_cmd(args, timeout=10)
            if r["ok"] and r["stdout"]:
                items = _parse_locate_output(
                    r["stdout"],
                    max_results,
                    search_root,
                    file_type,
                    ext,
                    case_sensitive,
                )
                if items:
                    return {
                        "ok": True,
                        "count": len(items),
                        "total_size": 0,
                        "items": items,
                        "engine": "locate",
                    }
        except Exception:
            pass

    # Layer 2: fd
    fd_path = shutil.which("fd")
    if fd_path:
        try:
            args = [
                fd_path,
                "--max-results",
                str(max_results),
                "--type",
                "f" if file_type == "file" else ("d" if file_type == "folder" else "f"),
            ]
            if ext:
                args.extend(["-e", ext])
            if case_sensitive:
                args.append("--case-sensitive")
            args.append(query)
            if search_root != "/":
                args.append(search_root)
            r = run_cmd(args, timeout=15)
            if r["ok"] and r["stdout"]:
                items = _parse_locate_output(
                    r["stdout"],
                    max_results,
                    search_root,
                    file_type,
                    ext,
                    case_sensitive,
                )
                if items:
                    return {
                        "ok": True,
                        "count": len(items),
                        "total_size": 0,
                        "items": items,
                        "engine": "fd",
                        "note": "Linux 搜索模式：搜索语法有限（不支持 ext: folder: 等 Everything 语法）",
                    }
        except Exception:
            pass

    # Layer 3: Python os.walk 兜底
    return _python_fallback(
        query, search_root, max_results, case_sensitive, file_type, ext
    )


def _parse_locate_output(
    stdout: str,
    max_results: int,
    search_root: str,
    file_type: str,
    ext: str | None,
    case_sensitive: bool,
) -> list[dict]:
    """解析 locate/fd 输出（每行一个绝对路径）。"""
    items: list[dict] = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        # 限定搜索目录
        if search_root != "/" and not str(p.resolve()).startswith(
            str(Path(search_root).resolve())
        ):
            continue
        try:
            if file_type == "file" and not p.is_file():
                continue
            if file_type == "folder" and not p.is_dir():
                continue
        except OSError:
            continue
        if ext and p.suffix.lstrip(".") != ext.lstrip("."):
            continue
        try:
            st = p.stat()
            size = st.st_size
            date_mod = str(st.st_mtime)
        except OSError:
            size = 0
            date_mod = ""
        items.append(
            {
                "name": p.name,
                "path": str(p.parent),
                "full": str(p),
                "size": size,
                "date_modified": date_mod,
            }
        )
        if len(items) >= max_results:
            break
    return items


def _python_fallback(
    query: str,
    search_root: str,
    max_results: int,
    case_sensitive: bool,
    file_type: str,
    ext: str | None,
) -> dict:
    """纯 Python os.walk 兜底搜索。"""
    items: list[dict] = []
    files_scanned = 0
    try:
        for root, dirs, files in os.walk(search_root):
            dirs[:] = [
                d for d in dirs if d not in _POSIX_SKIP_DIRS and not d.startswith(".")
            ]
            if files_scanned >= _MAX_POSIX_FILES:
                break
            entries: list[str] = []
            if file_type == "folder":
                entries = dirs
            elif file_type == "file":
                entries = files
            else:
                entries = files + dirs
            for entry in entries:
                files_scanned += 1
                ep = Path(root) / entry
                try:
                    st = ep.stat()
                except OSError:
                    continue
                if ext and ep.suffix.lstrip(".") != ext.lstrip("."):
                    continue
                name = ep.name if case_sensitive else ep.name.lower()
                q = query if case_sensitive else query.lower()
                if q not in name and not fnmatch.fnmatch(name, q):
                    continue
                items.append(
                    {
                        "name": ep.name,
                        "path": str(ep.parent),
                        "full": str(ep),
                        "size": st.st_size,
                        "date_modified": str(st.st_mtime),
                    }
                )
                if len(items) >= max_results:
                    break
            if len(items) >= max_results:
                break
    except PermissionError:
        pass

    return {
        "ok": True,
        "count": len(items),
        "total_size": 0,
        "items": items,
        "engine": "python",
        "note": "Linux 搜索模式（Python 扫描较慢）。建议安装 locate 或 fd 加速。",
    }
