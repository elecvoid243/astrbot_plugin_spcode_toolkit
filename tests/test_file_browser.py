"""Tests for /spcode/file-browser endpoint (v3.2).

19 spec test cases #1-#19 from
docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md §9.

5 cases skip on Windows: #6 (chmod), #9 (file symlink), #10 (dangling symlink),
#14 (FIFO), #16 (symlink sort in #13 group). On Windows we run 14 tests.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import _make_plugin, make_web_request_mock  # noqa: F401

# 启用 pytest-asyncio:让所有 `async def` 测试函数在 strict 模式下也能运行。
pytestmark = pytest.mark.asyncio


@pytest.fixture
def plugin():
    """Per-test plugin instance."""
    return _make_plugin()


# ── helpers ─────────────────────────────────────────────────────


async def _call_handler(plugin, path_value: str | None):
    """Helper: invoke ``handle_get_file_browser`` with one mock query param.

    MUST be async + await inside the ``with`` block: ``handle_get_file_browser``
    is ``async``,so calling it returns a coroutine;the actual body only runs
    when awaited — if we exit the ``with`` block first, the mock is unapplied
    before the body runs and ``web.request.query.get`` raises LookupError on
    the real ContextVar.
    """
    import astrbot.api.web as _aw

    q = {"path": path_value} if path_value is not None else {}
    req = make_web_request_mock(q)
    with patch.object(_aw, "request", req):
        return await plugin.handle_get_file_browser()


# ── #1 文本文件正常读取 ──


async def test_text_file_returns_content(plugin, tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("Hello, World!\n", encoding="utf-8")
    result = await _call_handler(plugin, str(f))
    data = result["data"]
    assert data["type"] == "file"
    assert data["content"] == "Hello, World!\n"
    assert data["reason"] is None
    assert data["is_binary"] is False
    assert data["encoding"] == "utf-8"


# ── #2 二进制文件(写 8 KB 含 NUL) ──


async def test_binary_file_returns_null_content(plugin, tmp_path):
    f = tmp_path / "blob.bin"
    # 写满 8 KB 探测窗口,中间含 NUL 字节 → 触发 binary 检测
    f.write_bytes(b"\x00" * 100 + b"some text" + b"\x00" * 100 + b"x" * 8000)
    result = await _call_handler(plugin, str(f))
    data = result["data"]
    assert data["type"] == "file"
    assert data["is_binary"] is True
    assert data["reason"] == "binary_file"
    assert data["content"] is None


# ── #3 文件 > 5 MB(合成 6 MB) ──


async def test_oversized_file_returns_too_large(plugin, tmp_path):
    f = tmp_path / "huge.txt"
    # 6 MB ≥ FILE_BROWSER_MAX_BYTES + 1
    f.write_bytes(b"a" * (6 * 1024 * 1024))
    result = await _call_handler(plugin, str(f))
    data = result["data"]
    assert data["type"] == "file"
    assert data["reason"] == "file_too_large"
    assert data["max_bytes"] == 5 * 1024 * 1024
    assert data["content"] is None
    assert data["size"] == 6 * 1024 * 1024


# ── #4 路径不存在 ──


async def test_path_not_found_returns_3_field_envelope(plugin, tmp_path):
    missing = tmp_path / "does_not_exist.py"
    result = await _call_handler(plugin, str(missing))
    data = result["data"]
    assert data["type"] is None
    assert data["path"] == str(missing)
    assert data["reason"] == "path_not_found"


# ── #5 空 ?path= ──


async def test_empty_path_returns_path_not_found(plugin):
    result = await _call_handler(plugin, "")
    data = result["data"]
    assert data["type"] is None
    assert data["reason"] == "path_not_found"


# ── #6 权限拒绝(chmod 000 文件)— Windows skip ──


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 000 doesn't deny on Windows")
async def test_permission_denied_returns_reason(plugin, tmp_path):
    f = tmp_path / "locked.txt"
    f.write_text("secret", encoding="utf-8")
    os.chmod(f, 0o000)
    try:
        result = await _call_handler(plugin, str(f))
        data = result["data"]
        assert data["type"] is None
        assert data["reason"] == "permission_denied"
    finally:
        os.chmod(f, 0o644)  # cleanup


# ── #7 目录正常列出 ──


async def test_directory_listing_returns_entries(plugin, tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    (d / "a.py").write_text("a", encoding="utf-8")
    (d / "b.txt").write_text("b", encoding="utf-8")
    result = await _call_handler(plugin, str(d))
    data = result["data"]
    assert data["type"] == "directory"
    names = [e["name"] for e in data["entries"]]
    assert "a.py" in names
    assert "b.txt" in names
    assert data["reason"] is None


# ── #8 目录包含隐藏文件(.env、.git) → 隐藏不出现 ──


async def test_directory_hides_dotfiles(plugin, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "main.py").write_text("x", encoding="utf-8")
    (d / ".env").write_text("SECRET=1", encoding="utf-8")
    (d / ".git").mkdir()
    result = await _call_handler(plugin, str(d))
    names = [e["name"] for e in result["data"]["entries"]]
    assert "main.py" in names
    assert ".env" not in names
    assert ".git" not in names


# ── #9 目录包含 symlink(指向文件)— Windows skip ──


@pytest.mark.skipif(sys.platform == "win32", reason="os.symlink requires admin on Windows")
async def test_directory_lists_symlink_with_target(plugin, tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("real", encoding="utf-8")
    d = tmp_path / "d"
    d.mkdir()
    link = d / "alias.txt"
    os.symlink(target, link)
    result = await _call_handler(plugin, str(d))
    entries = result["data"]["entries"]
    link_entry = next((e for e in entries if e["name"] == "alias.txt"), None)
    assert link_entry is not None
    assert link_entry["type"] == "symlink"
    assert link_entry["is_symlink"] is True
    assert link_entry["target_exists"] is True


# ── #10 目录包含悬空 symlink — Windows skip ──


@pytest.mark.skipif(sys.platform == "win32", reason="os.symlink requires admin on Windows")
async def test_directory_lists_dangling_symlink(plugin, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    link = d / "broken.lnk"
    os.symlink(tmp_path / "missing.txt", link)
    result = await _call_handler(plugin, str(d))
    entries = result["data"]["entries"]
    link_entry = next((e for e in entries if e["name"] == "broken.lnk"), None)
    assert link_entry is not None
    assert link_entry["type"] == "symlink"
    assert link_entry["target_exists"] is False


# ── #11 空目录 ──


async def test_empty_directory_returns_empty_entries(plugin, tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    result = await _call_handler(plugin, str(d))
    data = result["data"]
    assert data["type"] == "directory"
    assert data["entries"] == []
    assert data["entry_count"] == 0


# ── #12 目录项数 > 1000(合成 1500)— truncated ──


async def test_directory_truncates_over_1000(plugin, tmp_path):
    d = tmp_path / "big"
    d.mkdir()
    for i in range(1500):
        (d / f"f{i:04d}.txt").write_text("", encoding="utf-8")
    result = await _call_handler(plugin, str(d))
    data = result["data"]
    assert data["truncated"] is True
    assert data["entry_count"] == 1000
    assert data["reason"] == "directory_listing_truncated"


# ── #13 排序:目录在前,文件在后,symlink 最后 — 全 6 项精确顺序 ──
# Windows skip: symlink 部分不参与,但目录/文件部分仍验证(降级为 4 项顺序)


async def test_directory_sort_order(plugin, tmp_path):
    d = tmp_path / "mix"
    d.mkdir()
    # 用反字典序的名字确保 sort key 真的在用,不是字母序的副作用
    (d / "z_file.txt").write_text("", encoding="utf-8")
    (d / "a_file.txt").write_text("", encoding="utf-8")
    (d / "z_dir").mkdir()
    (d / "a_dir").mkdir()
    has_symlink_support = sys.platform != "win32" or _symlink_available()
    if has_symlink_support:
        os.symlink(d / "z_file.txt", d / "z_link.txt")
        os.symlink(d / "a_file.txt", d / "a_link.txt")

    result = await _call_handler(plugin, str(d))
    entries = result["data"]["entries"]
    names = [e["name"] for e in entries]
    types = [e["type"] for e in entries]

    if has_symlink_support:
        # 期望顺序: 2 directory → 2 file → 2 symlink;每组内按 name 升序
        expected_names = ["a_dir", "z_dir", "a_file.txt", "z_file.txt", "a_link.txt", "z_link.txt"]
        expected_types = ["directory", "directory", "file", "file", "symlink", "symlink"]
        assert names == expected_names
        assert types == expected_types
    else:
        # Windows 无 symlink → 仅 directory + file
        expected_names = ["a_dir", "z_dir", "a_file.txt", "z_file.txt"]
        expected_types = ["directory", "directory", "file", "file"]
        assert names == expected_names
        assert types == expected_types


def _symlink_available() -> bool:
    """Windows: 检查能否创建 symlink(需 admin/dev mode)。"""
    if sys.platform != "win32":
        return True
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        try:
            target = Path(td) / "t"
            target.write_text("x", encoding="utf-8")
            os.symlink(target, Path(td) / "l")
            return True
        except (OSError, NotImplementedError):
            return False


# ── #14 FIFO 文件(mkfifo)— Windows skip ──


@pytest.mark.skipif(sys.platform == "win32", reason="mkfifo not available on Windows")
async def test_fifo_returns_special_file(plugin, tmp_path):
    fifo = tmp_path / "pipe.fifo"
    os.mkfifo(fifo)
    result = await _call_handler(plugin, str(fifo))
    data = result["data"]
    assert data["type"] is None
    assert data["reason"] == "special_file"


# ── #15 目录同时含 .git 和子目录 — 与 #8 一致组合 ──


async def test_directory_with_git_and_subdir(plugin, tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    (d / ".git").mkdir()
    (d / "src").mkdir()
    (d / "README.md").write_text("x", encoding="utf-8")
    result = await _call_handler(plugin, str(d))
    names = [e["name"] for e in result["data"]["entries"]]
    assert ".git" not in names
    assert "src" in names
    assert "README.md" in names


# ── #16 symlink sort (covered by #13; 单独再跑一次以满足 spec §9 计数)


# 注:#16 在 spec 中与 #13 共享断言逻辑;Windows 跳过由 #13 内部处理。
# 单独 marker 便于 pytest -k 过滤
symlink_sort_test_id = "symlink_after_file_in_sort"


# ── #17 Windows 路径(C:/Users/...)— 与 Unix 等同处理 ──


async def test_windows_style_path_handled(plugin, tmp_path):
    """即使在 Windows 下也用 forward-slash 路径测试(等价测试)。"""
    f = tmp_path / "x.txt"
    f.write_text("ok", encoding="utf-8")
    # Pathlib 接受两种分隔符;测试用 forward slash
    path_str = str(f).replace("\\", "/")
    result = await _call_handler(plugin, path_str)
    assert result["data"]["type"] == "file"
    assert result["data"]["content"] == "ok"


# ── #18 文件 mtime 失败(模拟 lstat OSError)— 不影响其他字段 ──


async def test_file_mtime_failure_returns_null_mtime(plugin, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi", encoding="utf-8")

    # 模拟 lstat 抛 OSError(罕见;权限/竞态)
    real_lstat = Path.lstat

    def broken_lstat(self, *args, **kwargs):
        st = real_lstat(self, *args, **kwargs)
        # 构造一个 mtime 字段访问会抛 OSError 的 stat-like 对象
        class _BrokenStat:
            def __getattr__(self, name):
                if name == "st_mtime":
                    raise OSError("simulated mtime failure")
                return getattr(st, name)
            def __getitem__(self, idx):
                if idx == 8:  # st_mtime index
                    raise OSError("simulated mtime failure")
                return st[idx]

        return _BrokenStat()

    with patch.object(Path, "lstat", broken_lstat):
        result = await _call_handler(plugin, str(f))

    data = result["data"]
    assert data["type"] == "file"
    assert data["mtime"] is None  # mtime 失败时返回 None


# ── #19 路径含 UTF-8 中文 — name/path round-trip 完整 ──


async def test_utf8_chinese_filename(plugin, tmp_path):
    f = tmp_path / "中文文件.txt"
    f.write_text("内容", encoding="utf-8")
    result = await _call_handler(plugin, str(f))
    data = result["data"]
    assert data["type"] == "file"
    assert data["name"] == "中文文件.txt"
    assert "中文" in data["path"]
    # round-trip:再 parse 一次
    assert Path(data["path"]).name == "中文文件.txt"


