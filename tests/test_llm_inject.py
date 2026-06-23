"""tools.llm_inject.inject_guidance 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from tools.llm_inject import inject_guidance


def _req(system_prompt=None):
    r = MagicMock()
    r.system_prompt = system_prompt
    return r


def test_empty_text_returns_false():
    """text 为空 → False,system_prompt 不变。"""
    r = _req("hello")
    assert inject_guidance(r, "", "<MARKER>") is False
    assert r.system_prompt == "hello"


def test_marker_present_returns_false():
    """marker 已在 system_prompt → False,不追加。"""
    r = _req("hello <MARKER>")
    assert inject_guidance(r, "world <MARKER>", "<MARKER>") is False
    assert r.system_prompt == "hello <MARKER>"


def test_empty_marker_skips_check():
    """marker 为空字符串 → 跳过 marker 检查,直接追加。"""
    r = _req("hello")
    assert inject_guidance(r, "world", "") is True
    assert r.system_prompt == "helloworld"


def test_none_system_prompt_strips_leading_newline():
    """system_prompt=None → 设为 text.lstrip("\\n")。"""
    r = _req(None)
    assert inject_guidance(r, "\n\nfoo", "M") is True
    assert r.system_prompt == "foo"


def test_empty_string_system_prompt_strips_leading_newline():
    """system_prompt="" → 同 None 行为。"""
    r = _req("")
    assert inject_guidance(r, "\n\nfoo", "M") is True
    assert r.system_prompt == "foo"


def test_existing_system_prompt_appends_text():
    """system_prompt 非空 → existing + text(不 lstrip,保持原行为)。"""
    r = _req("hello")
    assert inject_guidance(r, "\nworld", "M") is True
    assert r.system_prompt == "hello\nworld"


def test_strip_leading_newline_false_keeps_leading_newlines():
    """strip_leading_newline=False → system_prompt 为空时也保留 text 开头 \\n。"""
    r = _req(None)
    assert inject_guidance(r, "\nfoo", "M", strip_leading_newline=False) is True
    assert r.system_prompt == "\nfoo"


def test_returns_true_when_injected():
    """正常注入路径 → True。"""
    r = _req("hi")
    assert inject_guidance(r, "APPEND", "<M>") is True
    assert r.system_prompt == "hiAPPEND"
