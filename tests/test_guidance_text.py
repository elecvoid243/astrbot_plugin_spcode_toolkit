"""测试 tools/_guidance_text.py 导出 4 个常量(2 marker + 2 guidance 文本)。"""

from tools._guidance_text import (
    PROJECT_GUIDANCE_MARKER,
    PROJECT_CODEGRAPH_GUIDANCE,
    FILE_REMOVE_GUIDANCE_MARKER,
    FILE_REMOVE_GUIDANCE,
)


def test_markers_are_unique():
    """2 个 marker 必须两两不同(防重复注入检查)。"""
    markers = {
        PROJECT_GUIDANCE_MARKER,
        FILE_REMOVE_GUIDANCE_MARKER,
    }
    assert len(markers) == 2, "marker 之间不能重复"


def test_guidance_text_contains_marker():
    """PROJECT_CODEGRAPH_GUIDANCE 必须包含 PROJECT_GUIDANCE_MARKER。"""
    assert PROJECT_GUIDANCE_MARKER in PROJECT_CODEGRAPH_GUIDANCE


def test_file_remove_guidance_contains_marker():
    """FILE_REMOVE_GUIDANCE 必须包含 FILE_REMOVE_GUIDANCE_MARKER。"""
    assert FILE_REMOVE_GUIDANCE_MARKER in FILE_REMOVE_GUIDANCE


def test_guidance_text_uses_correct_tool_name():
    """FILE_REMOVE_GUIDANCE 应提到 'astrbot_file_remove'。"""
    assert "astrbot_file_remove" in FILE_REMOVE_GUIDANCE


def test_codegraph_guidance_uses_correct_tool_name():
    """PROJECT_CODEGRAPH_GUIDANCE 应提到 'codegraph_explore'。"""
    assert "codegraph_explore" in PROJECT_CODEGRAPH_GUIDANCE


def test_codegraph_guidance_mentions_fallback_tool():
    """PROJECT_CODEGRAPH_GUIDANCE 应提到 astrbot_file_grep_tool 兜底。"""
    assert "astrbot_file_grep_tool" in PROJECT_CODEGRAPH_GUIDANCE


def test_markers_are_short_strings():
    """marker 应是短字符串(防止注入开销过大)。"""
    assert len(PROJECT_GUIDANCE_MARKER) < 50
    assert len(FILE_REMOVE_GUIDANCE_MARKER) < 50
