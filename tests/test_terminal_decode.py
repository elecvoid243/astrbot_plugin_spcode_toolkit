"""tests/test_terminal_decode.py — decode_bytes_with_fallback 单元测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: E402

from tools.terminal.decode import decode_bytes_with_fallback  # noqa: E402


def test_none_returns_empty_string():
    assert decode_bytes_with_fallback(None) == ""


def test_utf8_decodes():
    raw = "hello 世界".encode()
    assert decode_bytes_with_fallback(raw) == "hello 世界"


def test_empty_bytes_returns_empty():
    assert decode_bytes_with_fallback(b"") == ""


def test_gbk_fallback_on_windows_chain():
    raw = "中文编码".encode("gbk")
    # gbk 字节在 utf-8 下必然失败 → 回退链必须命中 cp936/gbk
    assert decode_bytes_with_fallback(raw) == "中文编码"


def test_invalid_bytes_never_raises():
    assert decode_bytes_with_fallback(b"\xff\xfe\x00\x01garbage") is not None
