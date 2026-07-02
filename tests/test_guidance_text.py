"""测试 tools/_guidance_text.py 导出所有 marker + guidance 文本常量(目前 5 对)。

5 对分别是:
- PROJECT_CODEGRAPH_GUIDANCE (project load 后注入)
- FILE_REMOVE_GUIDANCE      (astrbot_file_remove 启用时注入)
- TODO_GUIDANCE             (任一 todo_* 工具启用时注入)
- CODE_CHECK_GUIDANCE       (code_check 工具启用时注入,本 PR 新增)
- CODE_FORMAT_GUIDANCE      (code_format 工具启用时注入,本 PR 新增)
"""

from tools._guidance_text import (
    CODE_CHECK_GUIDANCE,
    CODE_CHECK_GUIDANCE_MARKER,
    CODE_FORMAT_GUIDANCE,
    CODE_FORMAT_GUIDANCE_MARKER,
    FILE_REMOVE_GUIDANCE,
    FILE_REMOVE_GUIDANCE_MARKER,
    PROJECT_CODEGRAPH_GUIDANCE,
    PROJECT_GUIDANCE_MARKER,
    TODO_GUIDANCE,
    TODO_GUIDANCE_MARKER,
)

# (marker, guidance, 必须提到的工具名/子串) 三元组列表
_ALL_PAIRS = [
    (PROJECT_GUIDANCE_MARKER, PROJECT_CODEGRAPH_GUIDANCE, "codegraph_explore"),
    (FILE_REMOVE_GUIDANCE_MARKER, FILE_REMOVE_GUIDANCE, "astrbot_file_remove"),
    (TODO_GUIDANCE_MARKER, TODO_GUIDANCE, "todo_"),
    (CODE_CHECK_GUIDANCE_MARKER, CODE_CHECK_GUIDANCE, "code_check"),
    (CODE_FORMAT_GUIDANCE_MARKER, CODE_FORMAT_GUIDANCE, "code_format"),
]


def test_markers_are_unique():
    """所有 marker 必须两两不同(防重复注入检查,marker 撞车会导致误判)。"""
    markers = {marker for marker, _, _ in _ALL_PAIRS}
    assert len(markers) == len(_ALL_PAIRS), "marker 之间不能重复"


def test_each_guidance_contains_its_marker():
    """每个 GUIDANCE 文本必须包含自己的 MARKER(inject_guidance 依赖此特性防重复)。"""
    for marker, guidance, _ in _ALL_PAIRS:
        assert marker in guidance, f"GUIDANCE 文本必须包含自己的 MARKER ({marker!r})"


def test_each_guidance_mentions_target_tool():
    """每个 GUIDANCE 文本必须提到对应的目标工具名,否则 LLM 不知道怎么用。"""
    for marker, guidance, tool_name in _ALL_PAIRS:
        assert tool_name in guidance, f"{marker!r} 引导文本必须提到 {tool_name!r}"


def test_markers_are_short_strings():
    """marker 应是短字符串(防止注入开销过大 + 在 system_prompt 里显眼)。"""
    for marker, _, _ in _ALL_PAIRS:
        assert len(marker) < 50, f"marker {marker!r} 太长"


def test_code_check_guidance_warns_against_subprocess():
    """code_check 引导应明确禁止 LLM 用 subprocess / shell 调外部 linter。

    背景:防止 agent 在 AstrBot pythonw 启动模式下,自己写
    ``subprocess.run([sys.executable, "-m", "ruff", ...])`` 导致弹 cmd 黑框
    (实测每次一闪即逝)。内置 code_check 走 Python API 不弹,所以应当引导优先。
    """
    assert "subprocess" in CODE_CHECK_GUIDANCE
    assert "ruff" in CODE_CHECK_GUIDANCE
    assert "cpplint" in CODE_CHECK_GUIDANCE


def test_code_format_guidance_warns_against_subprocess():
    """code_format 引导应明确禁止 LLM 用 subprocess / shell 调外部 formatter。

    同 test_code_check_guidance_warns_against_subprocess 背景:
    内置 code_format 走 ruff + AStyle 包内调用,不弹黑框;外部 spawn 会弹。
    """
    assert "subprocess" in CODE_FORMAT_GUIDANCE
    assert "ruff" in CODE_FORMAT_GUIDANCE
    assert "astyle" in CODE_FORMAT_GUIDANCE


def test_codegraph_guidance_mentions_fallback_tool():
    """PROJECT_CODEGRAPH_GUIDANCE 应提到 astrbot_file_grep_tool 作为 codegraph 不可用时的兜底。"""
    assert "astrbot_file_grep_tool" in PROJECT_CODEGRAPH_GUIDANCE
