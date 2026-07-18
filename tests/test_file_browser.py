"""Smoke test for webapi/file_browser.handle + helpers.

Handler 从 main.py 搬出,行为不变。
"""

from __future__ import annotations

import pytest
from tools.webapi import file_browser

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


async def test_build_error_response_has_required_fields():
    """_build_error_response 返回 type=None + path + reason。"""
    resp = file_browser._build_error_response("/tmp/x", "not_found")
    assert resp["type"] is None
    assert resp["path"] == "/tmp/x"
    assert resp["reason"] == "not_found"


async def test_make_304_response_is_json_response_compat():
    """_make_304_response 返回 _JSONResponseCompat 且 status_code == 304。"""
    from tools.webapi._helpers import _JSONResponseCompat

    resp = file_browser._make_304_response({"ETag": "abc"})
    assert isinstance(resp, _JSONResponseCompat)
    assert resp.status_code == 304
    # body 是空 dict,dict-like get 返回 None(不 raise)
    assert resp.get("status_code") is None


async def test_common_cache_headers_includes_etag():
    """_common_cache_headers 返回的 dict 含 ETag 字段。"""
    headers = file_browser._common_cache_headers("etag123")
    assert "ETag" in headers
    assert "Cache-Control" in headers


async def test_classify_entry_handles_missing_path():
    """_classify_entry 必须能处理 missing path(返回某种 result)。"""
    from pathlib import Path

    # 缺失的 path 不存在 → 应返回某种字符串分类
    result = file_browser._classify_entry(Path("Z:/definitely-not-exist-12345"))
    assert isinstance(result, tuple)
    assert len(result) == 2


# ── Encoding-aware text decoding (PR: ANSI/GBK support) ───────
# Spec: docs/superpowers/specs/2026-06-20-file-browser-endpoint-design.md
# §3.6 (new section) — Encoding detection strategy.
#
# Background: pre-PR, _build_file_response only tried ``raw.decode("utf-8")``
# and treated any UnicodeDecodeError as binary. This made every GBK/GB2312/
# GB18030 file (common on Chinese Windows) un-previewable. After PR, the
# function should:
#   1. Sniff NUL bytes for true binary (unchanged).
#   2. Try UTF-8 strict first (fast path, byte-identical output).
#   3. Try UTF-8 BOM and strip it (returns encoding="utf-8-sig").
#   4. Fall back to GBK/GB18030 chain (returns encoding="cp936"|"gbk"|...).
#   5. Latin-1 last-resort (returns encoding="latin-1", no longer binary).
#
# These tests assert the post-PR behavior. They are intentionally tight:
#   - exact ``encoding`` string (regression guard against renames)
#   - exact content round-trip
#   - explicit ``is_binary=False`` for text in any non-UTF-8 encoding
#
# See tests/test_helpers_encoding.py for the lower-level safe_decode_bytes
# unit tests; the tests here are the file_browser integration layer.


async def test_file_response_utf8_chinese(tmp_path):
    """纯 UTF-8 中文文件应被正确解码,encoding='utf-8', is_binary=False。"""

    p = tmp_path / "utf8_中文.py"
    payload = "# 中文注释\nprint('你好,世界')\n"
    p.write_bytes(payload.encode("utf-8"))

    resp = file_browser._build_file_response(p)

    assert resp["type"] == "file"
    assert resp["encoding"] == "utf-8"
    assert resp["is_binary"] is False
    assert resp["content"] == payload
    assert resp["reason"] is None


async def test_file_response_gbk_chinese(tmp_path):
    """GBK 中文文件(中文 Windows ANSI 默认)应被正确解码,encoding='cp936'。

    这是 PR 的核心修复点:旧版会把 GBK 文件错判为 binary_file。
    """

    p = tmp_path / "gbk_指南.cpp"
    payload = "// 中文注释:你好,世界\nint main() { return 0; }\n"
    p.write_bytes(payload.encode("gbk"))

    resp = file_browser._build_file_response(p)

    assert resp["type"] == "file"
    assert resp["is_binary"] is False, f"GBK 文件不应被判为 binary, got: {resp!r}"
    assert resp["content"] == payload, (
        f"GBK 解码内容不一致: {resp['content']!r} != {payload!r}"
    )
    # 实际命中的编码应是 cp936(gbk 的超集,中文 Windows 主用)
    assert resp["encoding"] in ("cp936", "gbk"), (
        f"GBK 文件应被识别为 cp936/gbk, got: {resp['encoding']!r}"
    )
    assert resp["reason"] is None


async def test_file_response_utf8_bom(tmp_path):
    """带 UTF-8 BOM 的文件应剥离 BOM 头部,encoding='utf-8-sig'。"""

    p = tmp_path / "bom_file.md"
    payload = "# 标题\n内容\n"
    p.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))

    resp = file_browser._build_file_response(p)

    assert resp["type"] == "file"
    assert resp["encoding"] == "utf-8-sig"
    assert resp["is_binary"] is False
    # BOM 必须被剥离(否则 Shiki / diff 渲染会出现 ﻿ 乱码)
    assert resp["content"] == payload
    assert "\ufeff" not in resp["content"]


async def test_file_response_gb18030(tmp_path):
    """GB18030 文件(超集 GBK,含扩展字符)应被解码。

    GB18030 是中国国家标准,Windows 中文系统也用它作为 ANSI 代码页。
    """

    p = tmp_path / "gb18030.txt"
    # GB18030 支持更广的字符集;这里用普通 GBK 字符测试即可
    payload = "中文测试,文件名_指南.md\n"
    p.write_bytes(payload.encode("gb18030"))

    resp = file_browser._build_file_response(p)

    assert resp["type"] == "file"
    assert resp["is_binary"] is False
    assert resp["content"] == payload
    # 实现可能在 cp936 / gbk / gb18030 任一处命中,只要解码正确即可
    assert resp["encoding"] in ("cp936", "gbk", "gb18030")


async def test_file_response_binary_nul_still_binary(tmp_path):
    """真二进制(含 NUL 字节)仍应被判为 binary,保持旧行为。"""

    p = tmp_path / "fake.png"
    # PNG 文件头 + 一些二进制数据
    p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\xff" * 100)

    resp = file_browser._build_file_response(p)

    assert resp["type"] == "file"
    assert resp["is_binary"] is True
    assert resp["content"] is None
    assert resp["reason"] == "binary_file"
    # 真二进制时 encoding 应为 None(与旧版一致)
    assert resp["encoding"] is None


async def test_file_response_latin1_not_binary(tmp_path):
    """纯 latin-1 字节(0xA0-0xFF)应被 latin-1 兜底解码,不再判为二进制。

    旧版对所有非 UTF-8 文件统一返回 binary_file,这导致许多西欧语言
    源文件(法语/德语带重音字符)也"无法预览"。latin-1 是永远可解的,
    所以这里必须返回文本。

    注意:payload 只能含 latin-1 (0x00-0xFF) 字符;em-dash / smart-quotes 等
    不在 latin-1 范围内,会触发 UnicodeEncodeError。
    """

    p = tmp_path / "latin1.txt"
    # "Café français: voilà!" - 所有字符都在 latin-1 范围内
    payload = "Café français: voilà!\n"
    p.write_bytes(payload.encode("latin-1"))

    resp = file_browser._build_file_response(p)

    assert resp["type"] == "file"
    assert resp["is_binary"] is False
    assert resp["content"] == payload
    assert resp["encoding"] == "latin-1"


async def test_file_response_gbk_line_endings_normalized(tmp_path):
    """GBK 文件的 \\r\\n 应与 UTF-8 一样被规范化为 \\n。

    行为与现有 UTF-8 路径一致(对齐 _build_file_response 的 docstring)。
    """

    p = tmp_path / "gbk_crlf.cpp"
    payload_crlf = "第一行\r\n第二行\r\n"
    expected_lf = "第一行\n第二行\n"
    p.write_bytes(payload_crlf.encode("gbk"))

    resp = file_browser._build_file_response(p)

    assert resp["content"] == expected_lf
    assert "\r" not in resp["content"]


async def test_file_response_empty(tmp_path):
    """空文件应被识别为 UTF-8 文本(空字符串),而非二进制。"""

    p = tmp_path / "empty.txt"
    p.write_bytes(b"")

    resp = file_browser._build_file_response(p)

    assert resp["type"] == "file"
    # 空文件无 NUL,所以走文本路径
    assert resp["is_binary"] is False
    assert resp["content"] == ""
    assert resp["encoding"] == "utf-8"
