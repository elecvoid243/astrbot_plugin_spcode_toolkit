"""
tests/test_helpers_encoding.py
校验 tools/_helpers.py 中的编码探测与兜底解码工具。

WHY: 修复 es_search / code_check 中文乱码后，必须确保：
  1. detect_console_encoding() 永不返回 ascii/us-ascii（避免真出现时解码失败）
  2. safe_decode_bytes() 能正确处理 GBK、UTF-8、混合输入
  3. safe_decode_bytes() 永不抛异常（最差也是 errors="replace"）
  4. es_search / code_check 实际调用子进程时用上了探测结果
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# 把项目根加到 sys.path，便于直接 `from tools.xxx import ...`
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools._helpers import detect_console_encoding, safe_decode_bytes  # noqa: E402


# ── detect_console_encoding ────────────────────────────


def test_detect_returns_nonempty_string():
    """返回值必须是非空字符串。"""
    enc = detect_console_encoding()
    assert isinstance(enc, str)
    assert enc.strip() != ""


def test_detect_never_returns_ascii():
    """永不返回 ascii/us-ascii（中文 Windows 容器可能出现），否则解码必失败。"""
    enc = detect_console_encoding().lower()
    assert enc not in ("ascii", "us-ascii", "ansi_x3.4-1968"), (
        f"detect_console_encoding() 返回 {enc!r}, 应已 fallback 到 utf-8/cp936"
    )


def test_detect_with_locale_fallback(monkeypatch):
    """locale 返回 ascii 时应 fallback。"""
    import locale as _locale

    monkeypatch.setattr(_locale, "getpreferredencoding", lambda *_a, **_k: "ascii")
    enc = detect_console_encoding()
    assert enc.lower() not in ("ascii", "us-ascii", "ansi_x3.4-1968")


def test_detect_with_locale_cp936(monkeypatch):
    """locale 返回 cp936 时透传（中文 Windows 真实场景）。"""
    import locale as _locale

    monkeypatch.setattr(_locale, "getpreferredencoding", lambda *_a, **_k: "cp936")
    assert detect_console_encoding() == "cp936"


def test_detect_with_locale_utf8(monkeypatch):
    """locale 返回 utf-8 时透传（Linux/macOS 真实场景）。"""
    import locale as _locale

    monkeypatch.setattr(_locale, "getpreferredencoding", lambda *_a, **_k: "utf-8")
    assert detect_console_encoding() == "utf-8"


def test_detect_handles_locale_exception(monkeypatch):
    """locale.getpreferredencoding 抛异常时不应崩溃。"""
    import locale as _locale

    def boom(*_a, **_k):
        raise RuntimeError("locale broken")

    monkeypatch.setattr(_locale, "getpreferredencoding", boom)
    enc = detect_console_encoding()
    assert enc.strip() != ""
    assert enc.lower() not in ("ascii", "us-ascii")


# ── safe_decode_bytes ──────────────────────────────────


def test_safe_decode_str_passthrough():
    """已是 str 时直接返回，不做无谓处理。"""
    assert safe_decode_bytes("中文 ABC") == "中文 ABC"
    assert safe_decode_bytes("") == ""
    assert safe_decode_bytes("ascii only") == "ascii only"


def test_safe_decode_gbk_bytes():
    """GBK 字节流应被正确还原为中文。"""
    text = "中文文件名_指南.md"
    gbk_bytes = text.encode("gbk")
    assert safe_decode_bytes(gbk_bytes, preferred="cp936") == text


def test_safe_decode_utf8_bytes():
    """UTF-8 字节流应被正确还原。"""
    text = "中文文件名_指南.md"
    utf8_bytes = text.encode("utf-8")
    assert safe_decode_bytes(utf8_bytes, preferred="utf-8") == text


def test_safe_decode_auto_with_gbk_bytes():
    """preferred=None (auto) 时应能正确解码当前环境的编码字节。"""
    # 中文 Windows 上 detect_console_encoding()=cp936，所以准备 cp936 字节测试
    import locale

    enc = locale.getpreferredencoding(False) or "utf-8"
    text = "中文文件名_指南.md"
    try:
        bytes_data = text.encode(enc)
    except LookupError:
        pytest_skip("当前 locale 编码不支持测试中文")
    result = safe_decode_bytes(bytes_data)
    assert result == text, f"auto 解码失败: {result!r} != {text!r}"


def test_safe_decode_wrong_preferred_falls_back():
    """preferred 错误时仍能从 fallback 链中恢复。"""
    text = "中文_指南"
    gbk_bytes = text.encode("gbk")
    # preferred=utf-8 解 GBK 字节会失败，应自动尝试 fallback (cp936/gbk)
    result = safe_decode_bytes(gbk_bytes, preferred="utf-8")
    assert result == text, f"fallback 失败: {result!r}"


def test_safe_decode_never_raises():
    """即使所有候选编码都失败，也不应抛异常（errors='replace' 兜底）。"""
    # 构造一段在所有候选编码中都部分失败的字节（用 0x80 单字节）
    bad_bytes = bytes([0x80, 0x81, 0x82, 0xFF, 0xFE])
    # 多种 preferred 都尝试一遍
    for enc in ("utf-8", "cp936", "ascii", "latin-1"):
        result = safe_decode_bytes(bad_bytes, preferred=enc)
        assert isinstance(result, str)
        assert len(result) > 0  # replace 模式下字符数 >= 输入字节数


def test_safe_decode_invalid_type():
    """非 bytes/str 输入转 str（防御性）。"""
    assert safe_decode_bytes(12345) == "12345"
    assert safe_decode_bytes(None) == "None"


def test_safe_decode_empty_bytes():
    """空字节应返回空字符串。"""
    assert safe_decode_bytes(b"") == ""
    assert safe_decode_bytes(bytearray()) == ""


def test_safe_decode_bytearray_supported():
    """bytearray 与 bytes 行为一致。"""
    text = "中文_测试"
    assert safe_decode_bytes(bytearray(text.encode("utf-8")), preferred="utf-8") == text


def test_safe_decode_invalid_encoding_name():
    """preferred 是非法编码名时不应崩溃（fallback 链接管）。"""
    text = "中文"
    bytes_data = text.encode("gbk")
    # nonexistent-encoding 会触发 LookupError，应 fallback
    result = safe_decode_bytes(bytes_data, preferred="nonexistent-encoding-xyz")
    assert isinstance(result, str)
    assert result == text  # gbk 在 fallback 链中


# ── 集成：es_search / code_check 真的用上了 detect_console_encoding ───


def test_es_search_uses_detected_encoding():
    """es_search._windows_search 调用 run_cmd 时应使用探测的编码。"""
    from tools import es_search

    captured = {}

    def fake_run_cmd(args, timeout=15, encoding=None, **kwargs):
        captured["encoding"] = encoding
        captured["args"] = args
        # 模拟 es.exe 的 CSV 输出：header + 1 行 GBK 中文
        # es.exe -csv -name -path-column -size -size-format 1 -date-modified 输出列：
        #   Name, Path, Size, Date Modified
        csv_text = (
            '"Name","Path","Size","Date Modified"\n'
            '"指南.md","D:\\test","30066","2026/06/14 01:00"\n'
        )
        # 实际 sub-process 输出是 GBK 字节流；这里模拟原样
        return {
            "ok": True,
            "stdout": csv_text,  # 假设已被正确解码为 str（run_cmd 内部完成）
            "stderr": "",
            "code": 0,
        }

    with patch.object(es_search, "run_cmd", side_effect=fake_run_cmd):
        with patch.object(es_search, "_get_es_path", return_value="C:/fake/es.exe"):
            result = es_search.search("指南")

    assert captured["encoding"], "run_cmd 必须被传 encoding 参数"
    assert captured["encoding"] == detect_console_encoding()
    assert result["ok"] is True
    assert len(result["items"]) == 1, f"应解析出 1 条记录，实际 {result.get('items')!r}"
    # 关键：name 应正确显示中文，而不是 乱码
    assert result["items"][0]["name"] == "指南.md", (
        f"中文文件名解码失败: {result['items'][0]['name']!r}"
    )


def test_code_check_cppcheck_uses_detected_encoding():
    """code_check._run_cppcheck 调用 subprocess 时应使用探测的编码。"""
    from tools import code_check

    # 准备一个 C++ 文件（即使 cppcheck 不存在，subprocess.run 仍会被调用）
    fake_file = Path(__file__)  # 用任何存在的文件即可
    captured = {}

    def fake_run(*args, **kwargs):
        captured["encoding"] = kwargs.get("encoding")
        captured["errors"] = kwargs.get("errors")
        # 构造一个 fake CompletedProcess
        import subprocess as _sp

        # 模拟 cppcheck 输出的 GBK 中文（"Mismatching allocation" 用中文写）
        gb_bytes = "src/foo.cpp:6:12: 错误: 不匹配的分配与释放 [mismatch]\n".encode("gbk")
        return _sp.CompletedProcess(
            args=args[0] if args else [],
            returncode=0,
            stdout="",
            stderr=gb_bytes.decode("gbk", errors="replace"),
        )

    with patch.object(code_check.subprocess, "run", side_effect=fake_run):
        with patch.object(code_check, "_find_cppcheck", return_value=["fake-cppcheck.exe"]):
            result = code_check._run_cppcheck(fake_file)

    assert captured.get("encoding") is not None, "_run_cppcheck 必须传 encoding"
    assert captured["encoding"] == detect_console_encoding()
    assert result["ok"] is True


def test_code_check_cpplint_uses_detected_encoding():
    """code_check._run_cpplint_only 调用 subprocess 时应使用探测的编码。"""
    from tools import code_check

    fake_file = Path(__file__)
    captured = {}

    def fake_run(*args, **kwargs):
        captured["encoding"] = kwargs.get("encoding")
        captured["errors"] = kwargs.get("errors")
        import subprocess as _sp

        # cpplint 输出格式: <path>:<lineno>:  <message>  [<category>] [<level>]
        line = "src/foo.cpp:42:  中文提示信息  [whitespace] [4]\n"
        return _sp.CompletedProcess(
            args=args[0] if args else [],
            returncode=0,
            stdout="Done processing.\nTotal errors found: 1\n",
            stderr=line,
        )

    with patch.object(code_check.subprocess, "run", side_effect=fake_run):
        with patch.object(code_check, "_find_cpplint", return_value=["fake-cpplint"]):
            result = code_check._run_cpplint_only(fake_file)

    assert captured.get("encoding") is not None, "_run_cpplint_only 必须传 encoding"
    assert captured["encoding"] == detect_console_encoding()
    assert result["ok"] is True
    assert result["count"] >= 1


# ── 辅助 ──────────────────────────────────────────────


def pytest_skip(msg: str):
    """简化版的 pytest.skip，避开顶层 import pytest。"""
    import pytest

    pytest.skip(msg)
