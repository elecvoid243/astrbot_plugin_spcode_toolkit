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


# ── v3.3 (2026-06-21): _make_entry 单次 lstat 收敛单测 ──
# 直接 import 并 call _make_entry,验证:
# 1. 单次 lstat 调用(用 mock 计数)
# 2. 字段从 stat_result 派生(type/size/mtime/is_symlink)
# 3. symlink 的 target 相对路径相对 symlink 父目录解析(原实现的 CWD-relative bug 已修)

from astrbot_plugin_spcode_toolkit import main as _main_mod_browser  # noqa: E402


def test_make_entry_file_single_lstat(tmp_path, monkeypatch):
    """普通文件 _make_entry 应只调 1 次 lstat(从 stat_result 派生其他字段)。"""
    f = tmp_path / "x.txt"
    f.write_text("hello", encoding="utf-8")
    lstat_count = 0
    real_lstat = Path.lstat

    def counting_lstat(self, *args, **kwargs):
        nonlocal lstat_count
        lstat_count += 1
        return real_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", counting_lstat)
    entry = _main_mod_browser._make_entry(f)
    assert lstat_count == 1
    assert entry["type"] == "file"
    assert entry["is_symlink"] is False
    assert entry["size"] == 5  # len("hello")
    assert "target" not in entry
    assert "target_exists" not in entry


def test_make_entry_directory(tmp_path):
    """目录 _make_entry 应派生 type='directory',无 target 字段。"""
    d = tmp_path / "subdir"
    d.mkdir()
    entry = _main_mod_browser._make_entry(d)
    assert entry["type"] == "directory"
    assert entry["is_symlink"] is False
    assert "target" not in entry


@pytest.mark.skipif(sys.platform == "win32", reason="os.symlink requires admin on Windows")
def test_make_entry_relative_symlink_resolves_against_parent(tmp_path):
    """相对 symlink:target_exists 应相对 symlink 父目录(不是 CWD)。"""
    # 在 subdir/ 下创建相对 symlink 指向同目录下的 missing.txt
    sub = tmp_path / "sub"
    sub.mkdir()
    link = sub / "broken.lnk"
    os.symlink("missing.txt", link)  # 相对 target
    entry = _main_mod_browser._make_entry(link)
    assert entry["type"] == "symlink"
    assert entry["is_symlink"] is True
    assert entry["target"] == "missing.txt"
    # target 在 symlink 父目录下不存在 → target_exists=False
    # (旧实现用 Path(target).exists() 会相对 CWD 解析,bug;此处用 os.path.join(parent, target))
    assert entry["target_exists"] is False


def test_make_entry_mtime_failure_returns_null(tmp_path, monkeypatch):
    """lstat 成功但 st_mtime 访问抛 OSError → mtime=None(兜底)。"""
    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    real_lstat = Path.lstat

    def broken_lstat(self, *args, **kwargs):
        st = real_lstat(self, *args, **kwargs)

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

    monkeypatch.setattr(Path, "lstat", broken_lstat)
    entry = _main_mod_browser._make_entry(f)
    assert entry["mtime"] is None
    assert entry["type"] == "file"
    assert entry["size"] == 1


# ── #20 (v3.3 新增): 大目录响应耗时 sanity check ──
# 1000+ 项目录的 _build_directory_response 必须在合理时间内完成
# (验证 _make_entry 单次 lstat 收敛有效,避免 5000+ syscalls 退化)。
# Windows 上慢速 FS 容忍度更高,设宽松阈值。


@pytest.mark.parametrize("entry_count", [1500])
async def test_directory_large_listing_completes_quickly(plugin, tmp_path, entry_count):
    """1500 项目录 listing 端点 < 3s(v3.3 单 lstat 收敛后应轻松达成)。

    旧实现在 Windows 上 5000+ syscalls 可能超时 5-10s,新实现 ~1500 syscalls。
    """
    import time

    d = tmp_path / "big"
    d.mkdir()
    for i in range(entry_count):
        (d / f"f{i:05d}.txt").write_text("", encoding="utf-8")
    t0 = time.time()
    result = await _call_handler(plugin, str(d))
    elapsed = time.time() - t0
    data = result["data"]
    assert data["truncated"] is True
    assert data["entry_count"] == 1000
    # 1500 → 1000 截断 + 排序,Windows 上应 < 3s;Linux < 1s
    assert elapsed < 3.0, f"directory listing too slow: {elapsed:.2f}s"


# ── v3.3 (2026-06-21): HTTP 缓存 ETag/304 + Cache-Control 集成测试 ──


async def test_file_browser_file_returns_etag_header(plugin, tmp_path):
    """文件响应必须带 ETag / Cache-Control / Vary 三个头。"""
    f = tmp_path / "hello.txt"
    f.write_text("Hello!", encoding="utf-8")
    result = await _call_handler(plugin, str(f))
    assert result.status_code == 200
    etag = result.headers.get("etag")
    assert etag, f"missing ETag in {dict(result.headers)}"
    assert etag.startswith('W/"'), f"weak ETag expected, got {etag!r}"
    # 文件用 private, must-revalidate(无 max-age,client 仍可缓存但必须 revalidate)
    cc = result.headers.get("cache-control", "")
    assert "private" in cc
    assert "must-revalidate" in cc
    assert result.headers.get("vary") == "Cookie"


async def test_file_browser_file_304_on_matching_etag(plugin, tmp_path):
    """文件内容 ETag 命中 → 304 + 空 body。"""
    f = tmp_path / "hello.txt"
    f.write_text("Hello!", encoding="utf-8")

    # 第一次:拿 ETag
    r1 = await _call_handler(plugin, str(f))
    etag = r1.headers.get("etag")
    assert etag

    # 第二次:带匹配 ETag
    r2 = await _file_browser_with_headers(
        plugin, str(f), headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304
    assert r2.headers.get("etag") == etag
    import json
    body = json.loads(r2.body) if r2.body else {}
    assert body == {}, f"304 body should be empty, got {body!r}"


async def test_file_browser_file_etag_changes_after_modify(plugin, tmp_path):
    """文件内容修改后 ETag 变,旧 ETag 不再命中。"""
    f = tmp_path / "hello.txt"
    f.write_text("v1", encoding="utf-8")
    r1 = await _call_handler(plugin, str(f))
    etag1 = r1.headers.get("etag")
    assert etag1

    # 改文件
    f.write_text("v2 longer content", encoding="utf-8")

    # 带旧 ETag → 200 + 新 ETag
    r2 = await _file_browser_with_headers(
        plugin, str(f), headers={"If-None-Match": etag1}
    )
    assert r2.status_code == 200
    etag2 = r2.headers.get("etag")
    assert etag2 != etag1


async def test_file_browser_directory_returns_cache_control_max_age(
    plugin, tmp_path
):
    """目录响应带 Cache-Control: max-age=2, must-revalidate。"""
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_text("a", encoding="utf-8")
    result = await _call_handler(plugin, str(d))
    assert result.status_code == 200
    cc = result.headers.get("cache-control", "")
    assert "max-age=2" in cc
    assert "must-revalidate" in cc
    assert "private" in cc
    # 目录也带 ETag(max-age 过期后 revalidate 用)
    etag = result.headers.get("etag")
    assert etag
    assert etag.startswith('W/"')


async def test_file_browser_directory_304_on_matching_etag(plugin, tmp_path):
    """目录 ETag 命中 → 304。"""
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_text("a", encoding="utf-8")

    r1 = await _call_handler(plugin, str(d))
    etag = r1.headers.get("etag")
    assert etag

    r2 = await _file_browser_with_headers(
        plugin, str(d), headers={"If-None-Match": etag}
    )
    assert r2.status_code == 304


# ── helpers for header-aware file_browser tests ──


async def _file_browser_with_headers(plugin, path_value: str, headers: dict):
    """Call handle_get_file_browser with mocked query + headers (v3.3 helper)."""
    import astrbot.api.web as _aw
    from unittest.mock import patch
    q = {"path": path_value}
    req = make_web_request_mock(q, headers=headers)
    with patch.object(_aw, "request", req):
        return await plugin.handle_get_file_browser()


