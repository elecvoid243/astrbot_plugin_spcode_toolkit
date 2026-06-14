"""tools/agentsmd.py 单元测试(v2.4 合并自独立插件)。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agentsmd import (  # noqa: E402
    DEFAULT_AGENTS_MD,
    DEFAULT_INIT_TEMPLATE,
    DEFAULT_INJECTION_HEADER,
    INJECTION_MARKER,
    build_injection,
    generate_agents_md_via_llm,
    resolve_init_template,
    scan_project_context,
    strip_code_fence,
)


# ── 常量 ────────────────────────────────────────────


def test_constants_not_empty():
    assert len(DEFAULT_INIT_TEMPLATE) > 50
    assert len(DEFAULT_AGENTS_MD) > 50
    assert INJECTION_MARKER in DEFAULT_INJECTION_HEADER


# ── strip_code_fence ───────────────────────────────


def test_strip_code_fence_no_fence():
    s = "plain markdown content"
    assert strip_code_fence(s) == s


def test_strip_code_fence_with_fenced_block():
    text = "```markdown\n# Title\n\nbody\n```"
    assert strip_code_fence(text) == "# Title\n\nbody"


def test_strip_code_fence_with_lang_prefix():
    text = "```python\nprint('hi')\n```"
    assert strip_code_fence(text) == "print('hi')"


def test_strip_code_fence_partial_match_not_stripped():
    """只在 text 完全被一对 ``` 包裹时才剥离。"""
    text = "```\npartial content without closing fence"
    assert strip_code_fence(text) == text


def test_strip_code_fence_multiline_preserved():
    text = "```\nline1\nline2\nline3\n```"
    assert strip_code_fence(text) == "line1\nline2\nline3"


# ── scan_project_context ───────────────────────────


def test_scan_basic_directory(tmp_path):
    """扫描基本目录结构。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')")
    (tmp_path / "README.md").write_text("# Project")
    (tmp_path / "package.json").write_text("{}")

    ctx = scan_project_context(tmp_path)
    assert "项目根目录" in ctx
    assert "src" in ctx
    assert "main.py" in ctx
    assert "package.json" in ctx
    assert "{}" in ctx  # 文件内容被包含


def test_scan_skips_git_and_node_modules(tmp_path):
    """.git / node_modules / __pycache__ 等被跳过。"""
    for skip in [".git", "node_modules", "__pycache__", "venv", "dist", "build"]:
        d = tmp_path / skip
        d.mkdir()
        (d / "noise.txt").write_text("x")

    ctx = scan_project_context(tmp_path)
    # 这些目录名不应出现在输出中
    for skip in [".git", "node_modules", "__pycache__", "venv"]:
        assert skip not in ctx, f"{skip} 应被跳过但出现在 ctx 中"


def test_scan_respects_max_depth(tmp_path):
    """深度超过 max_depth 的子目录名不出现(直接 continue,不输出)。"""
    deep = tmp_path / "L0dir" / "L1dir" / "L2dir" / "L3dir" / "L4dir"
    deep.mkdir(parents=True)
    (deep / "L4_leaf.txt").write_text("x")

    ctx = scan_project_context(tmp_path, max_depth=1)
    # depth 0 (./) 和 depth 1 (L0dir/) 出现
    assert "./" in ctx
    assert "L0dir/" in ctx
    # depth ≥ 2 不出现
    assert "L1dir" not in ctx
    assert "L2dir" not in ctx
    assert "L3dir" not in ctx
    assert "L4dir" not in ctx
    assert "L4_leaf.txt" not in ctx


def test_scan_includes_key_files(tmp_path):
    """常见的项目配置文件被识别并附加内容。"""
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\naddopts = '-v'")
    (tmp_path / "README.md").write_text("# My Project\n\nDescription.")
    (tmp_path / "Makefile").write_text("test:\n\tpytest")

    ctx = scan_project_context(tmp_path)
    assert "pyproject.toml" in ctx
    assert "Makefile" in ctx
    assert "README.md" in ctx
    assert "[tool.pytest]" in ctx


def test_scan_truncates_long_files(tmp_path):
    """超过 50 行的关键文件被截断。"""
    long_content = "\n".join(f"line {i}" for i in range(100))
    (tmp_path / "README.md").write_text(long_content)

    ctx = scan_project_context(tmp_path)
    assert "已截断" in ctx
    assert "line 99" not in ctx  # 第 100 行被截断


def test_scan_caps_subdirs_per_parent(tmp_path):
    """单个目录下子目录超过 10 个时,只显示前 10 个。"""
    for i in range(15):
        (tmp_path / f"sub{i:02d}").mkdir()

    ctx = scan_project_context(tmp_path)
    assert "还有" in ctx  # 显示截断提示
    assert "个子目录被忽略" in ctx


# ── generate_agents_md_via_llm ───────────────────────


def test_generate_returns_default_when_provider_none():
    """provider=None 时返回默认内容(走兜底)。"""
    out = asyncio.run(generate_agents_md_via_llm(None, Path("/tmp"), umo="test:umo"))
    assert out == DEFAULT_AGENTS_MD


def test_generate_strips_code_fence_in_response(tmp_path):
    """LLM 返回 ``` 包裹时,自动剥离。"""
    provider = MagicMock()
    llm_resp = MagicMock()
    llm_resp.completion_text = "```markdown\n# Title\n\nbody\n```"
    provider.text_chat = AsyncMock(return_value=llm_resp)

    out = asyncio.run(generate_agents_md_via_llm(provider, tmp_path, umo="test:umo"))
    assert out == "# Title\n\nbody"
    assert not out.startswith("```")


def test_generate_falls_back_on_provider_exception(tmp_path):
    """LLM 调用异常时返回默认内容,不抛异常。"""
    provider = MagicMock()
    provider.text_chat = AsyncMock(side_effect=Exception("LLM down"))

    out = asyncio.run(generate_agents_md_via_llm(provider, tmp_path, umo="test:umo"))
    assert out == DEFAULT_AGENTS_MD


def test_generate_falls_back_on_empty_response(tmp_path):
    """LLM 返回空内容时回退到默认。"""
    provider = MagicMock()
    llm_resp = MagicMock()
    llm_resp.completion_text = ""
    provider.text_chat = AsyncMock(return_value=llm_resp)

    out = asyncio.run(generate_agents_md_via_llm(provider, tmp_path, umo="test:umo"))
    assert out == DEFAULT_AGENTS_MD


def test_generate_uses_custom_init_template(tmp_path):
    """传入 init_template 时,LLM prompt 中包含它。"""
    provider = MagicMock()
    llm_resp = MagicMock()
    llm_resp.completion_text = "# Custom"
    provider.text_chat = AsyncMock(return_value=llm_resp)

    custom = "请用 emoji 风格写 AGENTS.md"
    asyncio.run(
        generate_agents_md_via_llm(
            provider, tmp_path, umo="test:umo", init_template=custom
        )
    )

    # 验证 text_chat 收到的 prompt 含 init_template 内容
    call_kwargs = provider.text_chat.call_args.kwargs
    assert custom in call_kwargs["prompt"]


def test_generate_uses_default_template_when_empty(tmp_path):
    """init_template 为空字符串时,使用默认模板。"""
    provider = MagicMock()
    llm_resp = MagicMock()
    llm_resp.completion_text = "ok"
    provider.text_chat = AsyncMock(return_value=llm_resp)

    asyncio.run(
        generate_agents_md_via_llm(provider, tmp_path, umo="test:umo", init_template="")
    )

    call_kwargs = provider.text_chat.call_args.kwargs
    assert DEFAULT_INIT_TEMPLATE in call_kwargs["prompt"]


def test_generate_passes_session_id(tmp_path):
    """session_id 来自 umo(便于 AstrBot 会话隔离)。"""
    provider = MagicMock()
    llm_resp = MagicMock()
    llm_resp.completion_text = "ok"
    provider.text_chat = AsyncMock(return_value=llm_resp)

    asyncio.run(generate_agents_md_via_llm(provider, tmp_path, umo="test-session-123"))

    assert provider.text_chat.call_args.kwargs["session_id"] == "test-session-123"


# ── build_injection ─────────────────────────────────


def test_build_injection_contains_marker():
    s = build_injection("# My Content")
    assert INJECTION_MARKER in s
    assert "# My Content" in s


def test_build_injection_ends_with_content():
    s = build_injection("body text")
    assert s.endswith("body text")


# ── v2.8 新增: build_injection(directory=...) ────────────────


def test_build_injection_includes_directory_when_provided():
    """v2.8: 提供 directory 时,输出含项目路径声明 + git worktree 指引。

    WHY: 措辞由简短 "项目路径:" 改为更详细的 "你正在处理的项目工作路径为: ..."
    以与 main.py system_prompt 钩子 (SPCodeToolkit._agentsmd_inject_to_llm_request)
    注入到 LLM 的实际格式保持一致。
    """
    s = build_injection("# My Content", directory="/home/user/myproject")
    assert "你正在处理的项目工作路径为: /home/user/myproject" in s
    assert "优先使用git worktree" in s
    # 既有内容也保留
    assert "# My Content" in s
    assert INJECTION_MARKER in s


def test_build_injection_omits_directory_when_empty():
    """v2.8: directory 为空字符串时,不含路径块(向后兼容)。"""
    s = build_injection("body", directory="")
    assert "项目工作路径为" not in s
    assert "优先使用git worktree" not in s
    assert INJECTION_MARKER in s
    assert "body" in s


def test_build_injection_omits_directory_by_default():
    """v2.8: 不传 directory 参数时,行为与 v2.7 完全一致。"""
    s = build_injection("body")
    assert "项目工作路径为" not in s
    assert "优先使用git worktree" not in s
    assert INJECTION_MARKER in s


def test_build_injection_path_before_marker():
    """v2.8: 路径块必须放在 INJECTION_MARKER 之前。"""
    s = build_injection("# Content", directory="/proj")
    path_pos = s.find("你正在处理的项目工作路径为: /proj")
    marker_pos = s.find(INJECTION_MARKER)
    assert path_pos != -1 and marker_pos != -1
    assert path_pos < marker_pos, "路径块必须先于 marker 出现"


def test_build_injection_path_before_content():
    """v2.8: 路径块必须放在 AGENTS.md 内容之前。"""
    s = build_injection("# CONTENT_MARKER", directory="/proj")
    assert s.find("你正在处理的项目工作路径为: /proj") < s.find("# CONTENT_MARKER")


# ── resolve_init_template ───────────────────────────


def test_resolve_init_template_uses_config():
    cfg = {"init_template": "my custom"}
    assert resolve_init_template(cfg) == "my custom"


def test_resolve_init_template_falls_back_on_empty():
    cfg = {"init_template": ""}
    assert resolve_init_template(cfg) == DEFAULT_INIT_TEMPLATE


def test_resolve_init_template_falls_back_on_whitespace():
    cfg = {"init_template": "   "}
    assert resolve_init_template(cfg) == DEFAULT_INIT_TEMPLATE


def test_resolve_init_template_none_config():
    assert resolve_init_template(None) == DEFAULT_INIT_TEMPLATE


def test_resolve_init_template_strips_whitespace():
    cfg = {"init_template": "  custom template  \n"}
    assert resolve_init_template(cfg) == "custom template"
