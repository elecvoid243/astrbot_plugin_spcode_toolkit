"""file_remove_restore 回收站恢复测试。

全部用例通过 ``recycle_root`` 注入合成回收站目录,不触碰真实回收站;
Windows 分支用 monkeypatch sys.platform,在任意平台可跑。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import file_remove_restore as frr
from tools.function_tools.file_remove import _record_turn_removal

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── 合成回收站工具 ────────────────────────────────────


def _make_i_bytes_v2(original: str, size: int = 5) -> bytes:
    """版本 2 元数据:null 结尾 UTF-16 名称。"""
    data = (2).to_bytes(8, "little")
    data += size.to_bytes(8, "little")
    data += (0).to_bytes(8, "little")
    data += original.encode("utf-16-le") + b"\x00\x00"
    return data


def _make_i_bytes_v4(original: str, size: int = 5) -> bytes:
    """版本 4 元数据:4 字节名称长度前缀(码元数,含结尾 null)。"""
    name = original.encode("utf-16-le") + b"\x00\x00"
    data = (4).to_bytes(8, "little")
    data += size.to_bytes(8, "little")
    data += (0).to_bytes(8, "little")
    data += (len(name) // 2).to_bytes(4, "little")
    data += name
    return data


def _make_i_bytes_v2_lengthprefixed(original: str, size: int = 5) -> bytes:
    """真实机器观察到的布局:版本字段为 2 但带 4 字节名称长度前缀
    (码元数,含结尾 null)——Win10/11 与 send2trash 的实际产物。"""
    name = original.encode("utf-16-le") + b"\x00\x00"
    data = (2).to_bytes(8, "little")
    data += size.to_bytes(8, "little")
    data += (0).to_bytes(8, "little")
    data += (len(name) // 2).to_bytes(4, "little")
    data += name
    return data


def _seed_windows_bin(
    recycle_root: Path, original: Path, content: bytes, i_bytes: bytes
) -> Path:
    sid = recycle_root / "$Recycle.Bin" / "S-1-5-18"
    sid.mkdir(parents=True)
    meta = sid / "$I1234567.txt"
    meta.write_bytes(i_bytes)
    (sid / "$R1234567.txt").write_bytes(content)
    return meta


# ── Windows 分支 ──────────────────────────────────────


@pytest.mark.parametrize(
    "make_i",
    [_make_i_bytes_v2, _make_i_bytes_v4, _make_i_bytes_v2_lengthprefixed],
)
def test_restore_windows_v2_v4(tmp_path: Path, monkeypatch, make_i):
    monkeypatch.setattr(sys, "platform", "win32")
    original = tmp_path / "workspace" / "报告.md"
    recycle_root = tmp_path / "recycle"
    meta = _seed_windows_bin(
        recycle_root, original, "数据内容".encode("utf-8"), make_i(str(original), 12)
    )
    # 原路径当前不存在(已删除状态)。
    assert not original.exists()

    r = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert r["ok"] is True
    assert Path(r["restored_path"]) == original
    assert original.read_bytes() == "数据内容".encode("utf-8")
    # 元数据与数据文件均被清理。
    assert not meta.exists()
    assert not meta.with_name("$R1234567.txt").exists()


def test_restore_windows_not_found(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    recycle_root = tmp_path / "recycle"
    recycle_root.mkdir()
    r = frr.restore_from_recycle_bin(
        str(tmp_path / "gone.txt"), recycle_root=str(recycle_root)
    )
    assert r["ok"] is False
    assert "未找到" in r["error"]


def test_restore_windows_dest_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    original = tmp_path / "workspace" / "a.txt"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"new content")  # 目标已被新文件占用。
    recycle_root = tmp_path / "recycle"
    _seed_windows_bin(recycle_root, original, b"old", _make_i_bytes_v2(str(original)))

    r = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert r["ok"] is False
    assert "已存在" in r["error"]
    # 目标文件未被覆盖。
    assert original.read_bytes() == b"new content"


def test_restore_windows_picks_newest(tmp_path: Path, monkeypatch):
    """同一路径多次删除时取最新一条记录。"""
    monkeypatch.setattr(sys, "platform", "win32")
    original = tmp_path / "workspace" / "a.txt"
    recycle_root = tmp_path / "recycle"
    sid = recycle_root / "$Recycle.Bin" / "S-1-5-18"
    sid.mkdir(parents=True)
    for i, content in enumerate((b"old", b"newest")):
        meta = sid / f"$I000000{i}.txt"
        meta.write_bytes(_make_i_bytes_v2(str(original)))
        (sid / f"$R000000{i}.txt").write_bytes(content)
        import os

        os.utime(meta, (1000 + i, 1000 + i))  # 递增 mtime。

    r = frr.restore_from_recycle_bin(str(original), recycle_root=str(recycle_root))

    assert r["ok"] is True
    assert original.read_bytes() == b"newest"


# ── Linux 分支(XDG Trash) ────────────────────────────


def test_restore_linux_trashinfo(tmp_path: Path):
    from urllib.parse import quote

    original = tmp_path / "workspace" / "b.txt"
    recycle_root = tmp_path / "recycle"
    info_dir = recycle_root / "info"
    files_dir = recycle_root / "files"
    info_dir.mkdir(parents=True)
    files_dir.mkdir()
    info = info_dir / "b.txt.trashinfo"
    info.write_text(
        f"[Trash Info]\nPath={quote(str(original))}\nDeletionDate=20260914T10:00:00\n",
        encoding="utf-8",
    )
    (files_dir / "b.txt").write_bytes(b"trashed")

    r = frr._restore_linux(str(original), str(recycle_root))

    assert r["ok"] is True
    assert original.read_bytes() == b"trashed"
    assert not info.exists()


def test_restore_linux_relative_path_skipped(tmp_path: Path):
    """挂载点回收站的相对 Path= 无法可靠还原,应视为未找到。"""
    recycle_root = tmp_path / "recycle"
    info_dir = recycle_root / "info"
    info_dir.mkdir(parents=True)
    (info_dir / "c.txt.trashinfo").write_text(
        "[Trash Info]\nPath=rel/c.txt\n", encoding="utf-8"
    )

    r = frr._restore_linux(str(tmp_path / "rel" / "c.txt"), str(recycle_root))

    assert r["ok"] is False


# ── FileRemoveTool 记录 changed_files ─────────────────


def _make_ctx(extra=None):
    return SimpleNamespace(
        context=SimpleNamespace(extra=extra if extra is not None else {})
    )


def test_record_turn_removal_ok():
    ctx = _make_ctx()
    _record_turn_removal(ctx, "D:/proj/a.txt", {"ok": True, "deleted": 1})
    entries = ctx.context.extra["changed_files"]
    assert len(entries) == 1
    assert entries[0]["kind"] == "remove"
    assert entries[0]["backup_id"] == ""
    assert entries[0]["runtime"] == "local"
    assert entries[0]["ts"] > 0
    assert entries[0]["path"].endswith("a.txt")


def test_record_turn_removal_failure_noop():
    ctx = _make_ctx()
    _record_turn_removal(ctx, "D:/proj/a.txt", {"ok": False, "error": "x"})
    assert ctx.context.extra.get("changed_files", []) == []


def test_record_turn_removal_context_without_extra():
    """轻量上下文没有 extra 字段时不炸(防御式 getattr)。"""
    ctx = SimpleNamespace(context=SimpleNamespace())
    _record_turn_removal(ctx, "D:/proj/a.txt", {"ok": True})
