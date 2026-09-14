"""file_remove_restore — 从系统回收站恢复 send2trash 删除的文件/目录。

配套:
  - LLM 工具 ``astrbot_file_remove``(删除走 send2trash → 系统回收站)
  - webapi ``POST /spcode/file-remove/restore``(ChatUI 文件变更总结卡片的
    "撤销删除"按钮)

平台支持:
  - Windows: 解析 ``$Recycle.Bin/<SID>/$I*`` 元数据定位 ``$R*`` 数据文件
    (版本 2 = Vista~8.1,null 结尾 UTF-16 名称;版本 4 = Win10+,4 字节
    名称长度前缀),按嵌入的原始路径匹配后整棵搬回。
  - Linux:   XDG Trash 规范(``~/.local/share/Trash``,``info/*.trashinfo``
    的 ``Path=`` 百分号编码绝对路径),按 DeletionDate 最新优先。
  - macOS:   废纸篓不保留原始路径元数据,无法可靠匹配,明确报不支持。

``recycle_root`` 参数仅供测试注入(指向合成回收站根目录);生产调用传
None,Windows 枚举所有盘符,Linux 取 XDG 数据目录。
"""

from __future__ import annotations

import os
import shutil
import sys
import urllib.parse
from pathlib import Path

# ── 路径归一化 ────────────────────────────────────────


def _normalize(p: str) -> str:
    """统一大小写与分隔符,供回收站元数据与目标路径比较。"""
    return os.path.normcase(os.path.normpath(p))


# ── Windows:$I 元数据解析 ─────────────────────────────


def _parse_i_file(meta: Path) -> tuple[str, int] | None:
    """解析 ``$I`` 元数据文件,返回 (原始路径, 原始字节数)。

    实际存在的两种布局(UTF-16LE,均以 ``28 + 名称长度*2 == 文件总长``
    判别长度前缀式):

      A. 长度前缀式(Win10/11 与 send2trash 实际写出的格式——即使版本
         字段为 2 也带长度字段):
           - 字节 0..7   版本号(2 或 4)
           - 字节 8..15  原始文件大小
           - 字节 16..23 删除时间(FILETIME,本函数不使用)
           - 字节 24..27 名称长度(UTF-16 码元数,含结尾 null)
           - 字节 28 起  原始路径
      B. 经典 null 结尾式(Vista~8.1):
           - 字节 0..7   版本号 2
           - 字节 24 起  null 结尾的原始路径(无长度字段)

    解析失败返回 None(损坏/未知版本条目直接跳过,不中断扫描)。
    """
    try:
        data = meta.read_bytes()
    except OSError:
        return None
    if len(data) < 24:
        return None
    version = int.from_bytes(data[0:8], "little")
    size = int.from_bytes(data[8:16], "little")
    if version not in (2, 4):
        return None

    candidates: list[str] = []
    if len(data) >= 28:
        name_len = int.from_bytes(data[24:28], "little")
        # 长度字段为码元数;仅当长度与文件总长精确吻合时才采信长度
        # 前缀式布局(经典式文件的该偏移是路径首字符,几乎必然不吻合)。
        if 0 < name_len and 28 + name_len * 2 == len(data):
            candidates.append(
                data[28:].decode("utf-16-le", errors="replace").rstrip("\x00")
            )
    # 经典式兜底:null 结尾名称。
    candidates.append(data[24:].decode("utf-16-le", errors="replace").split("\x00")[0])
    for name in candidates:
        # 单字符乱码候选(布局误判时)不含路径分隔符,直接淘汰。
        if name and ("\\" in name or "/" in name):
            return name, size
    return None


def _windows_drive_roots() -> list[Path]:
    """枚举存在 ``$Recycle.Bin`` 的盘符根(A..Z,忽略无权限的)。"""
    roots: list[Path] = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        bin_dir = root / "$Recycle.Bin"
        if bin_dir.is_dir():
            roots.append(root)
    return roots


def _iter_windows_meta_files(root: Path):
    """产出 ``root/$Recycle.Bin`` 下全部 ``$I`` 元数据文件路径。

    其他用户的 SID 子目录可能无权限读取——PermissionError 一律跳过。
    """
    bin_dir = root / "$Recycle.Bin"
    try:
        for sid in bin_dir.iterdir():
            if not sid.is_dir():
                continue
            try:
                yield from sid.glob("$I*")
            except OSError:
                continue
    except (PermissionError, OSError):
        return


def _restore_windows(raw: str, recycle_root: str | None) -> dict:
    if recycle_root is not None:
        roots = [Path(recycle_root)]
    else:
        roots = _windows_drive_roots()

    target = _normalize(raw)
    candidates: list[tuple[float, Path, str, int]] = []
    for root in roots:
        for meta in _iter_windows_meta_files(root):
            parsed = _parse_i_file(meta)
            if parsed is None:
                continue
            original, size = parsed
            if _normalize(original) != target:
                continue
            try:
                mtime = meta.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((mtime, meta, original, size))

    if not candidates:
        return {
            "ok": False,
            "error": "回收站中未找到该路径的删除记录(可能已被清空或永久删除)",
        }

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, meta, original, _size = candidates[0]
    data_file = meta.with_name("$R" + meta.name[2:])
    return _move_back(data_file, meta, original)


def _move_back(data_file: Path, meta: Path, original: str) -> dict:
    """把回收站数据文件搬回原位并清理元数据;目标被占用时报错。"""
    dest = Path(original)
    if not data_file.exists():
        return {"ok": False, "error": "回收站记录存在但数据文件已丢失"}
    if dest.exists():
        return {"ok": False, "error": f"目标位置已存在同名文件: {original}"}
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(data_file), str(dest))
    except OSError as exc:
        return {"ok": False, "error": f"恢复失败: {exc}"}
    try:
        meta.unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True, "restored_path": str(dest)}


# ── Linux:XDG Trash 规范 ──────────────────────────────


def _parse_trashinfo(info: Path) -> str | None:
    """读取 ``*.trashinfo`` 的 ``Path=`` 字段(百分号解码),非绝对路径跳过。"""
    try:
        text = info.read_text("utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("Path="):
            original = urllib.parse.unquote(line[5:])
            if original and os.path.isabs(original):
                return original
            return None
    return None


def _restore_linux(raw: str, recycle_root: str | None) -> dict:
    if recycle_root is not None:
        trash_root = Path(recycle_root)
    else:
        data_home = os.environ.get("XDG_DATA_HOME") or str(
            Path.home() / ".local" / "share"
        )
        trash_root = Path(data_home).expanduser() / "Trash"
    info_dir = trash_root / "info"
    files_dir = trash_root / "files"

    target = _normalize(raw)
    best: tuple[float, Path, str, Path] | None = None
    if info_dir.is_dir():
        for info in info_dir.glob("*.trashinfo"):
            original = _parse_trashinfo(info)
            if original is None or _normalize(original) != target:
                continue
            try:
                mtime = info.stat().st_mtime
            except OSError:
                mtime = 0.0
            trashed = files_dir / info.stem
            if best is None or mtime > best[0]:
                best = (mtime, info, original, trashed)

    if best is None:
        return {
            "ok": False,
            "error": "回收站中未找到该路径的删除记录(可能已被清空或永久删除)",
        }
    _, info, original, trashed = best
    return _move_back(trashed, info, original)


# ── 入口 ──────────────────────────────────────────────


def restore_from_recycle_bin(path: str, *, recycle_root: str | None = None) -> dict:
    """把 ``path`` 对应的最新一条回收站记录恢复到原位。

    Args:
        path: 被删除时的绝对路径。
        recycle_root: 回收站根目录;仅供测试注入,生产传 None。

    Returns:
        成功 → ``{"ok": True, "restored_path": ...}``;
        失败 → ``{"ok": False, "error": ...}``。
    """
    raw = str(path).strip()
    if not raw:
        return {"ok": False, "error": "路径为空"}
    if sys.platform == "win32":
        return _restore_windows(raw, recycle_root)
    if sys.platform.startswith("linux"):
        return _restore_linux(raw, recycle_root)
    return {"ok": False, "error": "当前平台不支持从回收站自动恢复,请从废纸篓手动恢复"}
