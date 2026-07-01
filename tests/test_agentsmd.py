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
    CODE_FILE_EXTENSIONS,
    DEFAULT_AGENTS_MD,
    DEFAULT_INIT_TEMPLATE,
    DEFAULT_INJECTION_HEADER,
    INJECTION_MARKER,
    build_injection,
    generate_agents_md_via_llm,
    has_code_files,
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


# ── has_code_files / CODE_FILE_EXTENSIONS ─────────────────────
# WHY: /agentsmd init|load 现在要求目标目录下至少存在一个代码文件
# (.c / .cpp / .py / .v 之一),避免对空目录或纯文档目录误用。
# has_code_files 必须扫描递归子目录,但跳过垃圾目录和隐藏目录。


def test_code_file_extensions_contains_common_languages():
    """锁住关键语言家族的核心扩展名必须在白名单中。

    不再锁死 exact 集合(v2.9 扩展后变更频繁),改用白名单成员检查——
    只要新增扩展不会移除这些核心项,集合后续可以自由扩展。
    """
    must_have = {
        # C / C++ 家族(老白名单的根基)
        "c",
        "cpp",
        "h",
        "hpp",
        "cc",
        "cxx",
        # 主流语言
        "py",
        "js",
        "ts",
        "jsx",
        "tsx",
        "java",
        "go",
        "rs",
        "kt",
        "kts",
        "rb",
        "php",
        "swift",
        "cs",
        # 脚本/Shell
        "sh",
        "bash",
        "ps1",
        # 函数式
        "hs",
        "ex",
        "exs",
        "erl",
        # HDL
        "v",
        "sv",
        "vhdl",
    }
    missing = must_have - CODE_FILE_EXTENSIONS
    assert not missing, f"以下核心扩展名未包含在白名单中: {sorted(missing)}"


def test_code_file_extensions_excludes_pure_markup_and_config():
    """纯标记/样式/配置文件不应被识别为代码文件。

    这些扩展名是项目配置文件或文档格式,虽然出现在代码项目中,
    但单独一个项目只有这些文件不能算"代码项目"。
    """
    must_exclude = {
        # 标记
        "html",
        "htm",
        "xml",
        "md",
        "rst",
        "tex",
        # 样式
        "css",
        "scss",
        "less",
        "sass",
        "styl",
        # 数据/配置
        "json",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        # 模板(非代码)
        "txt",
        "log",
        # 图片/二进制
        "png",
        "jpg",
        "gif",
        "ico",
        "svg",
        # 字体
        "ttf",
        "otf",
        "woff",
        "woff2",
        # 文档/二进制
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        # 归档
        "zip",
        "tar",
        "gz",
        "7z",
        "rar",
    }
    leaked = must_exclude & CODE_FILE_EXTENSIONS
    assert not leaked, f"以下扩展名不应在白名单中: {sorted(leaked)}"


def test_code_file_extensions_is_frozenset():
    """白名单是不可变 frozenset,防止运行时被修改。"""
    # 运行时类型检查
    assert isinstance(CODE_FILE_EXTENSIONS, frozenset)
    # 不可变性验证(不应能 add)
    import pytest

    with pytest.raises(AttributeError):
        CODE_FILE_EXTENSIONS.add("newext")  # type: ignore[attr-defined]


# ── v2.9: 重导出兼容测试 ─────────────────────────────
# WHY: has_code_files / CODE_FILE_EXTENSIONS / _SKIP_DIRS 已从 agentsmd.py
# 抽到 tools._code_detect.py(共享给 codegraph 模块)。agentsmd.py 仅 re-export,
# 保证老代码 `from tools.agentsmd import has_code_files, CODE_FILE_EXTENSIONS`
# 不破。这是迁移契约的核心保证。


def test_agentsmd_reexports_code_detect_symbols():
    """agentsmd 必须 re-export _code_detect 的核心符号,确保向后兼容。"""
    from tools import agentsmd, _code_detect  # noqa: F401

    # 符号必须在 agentsmd 模块里可见
    assert hasattr(agentsmd, "has_code_files")
    assert hasattr(agentsmd, "CODE_FILE_EXTENSIONS")
    assert hasattr(agentsmd, "_SKIP_DIRS")

    # 且必须与 _code_detect 中的原始定义是同一个对象(避免漂移)
    assert agentsmd.has_code_files is _code_detect.has_code_files
    assert agentsmd.CODE_FILE_EXTENSIONS is _code_detect.CODE_FILE_EXTENSIONS
    assert agentsmd._SKIP_DIRS is _code_detect._SKIP_DIRS


def test_codetect_module_importable():
    """共享模块 tools._code_detect 必须可直接 import。"""
    from tools import _code_detect  # noqa: F401

    assert hasattr(_code_detect, "has_code_files")
    assert hasattr(_code_detect, "CODE_FILE_EXTENSIONS")
    assert hasattr(_code_detect, "_SKIP_DIRS")


def test_codetect_skip_dirs_consistent():
    """_SKIP_DIRS 包含必要的垃圾目录。"""
    from tools._code_detect import _SKIP_DIRS

    must_have = {"node_modules", "__pycache__", "venv", ".git", ".venv"}
    missing = must_have - _SKIP_DIRS
    assert not missing, f"_SKIP_DIRS 缺少核心垃圾目录: {sorted(missing)}"


def test_has_code_files_empty_directory(tmp_path):
    """空目录 → False。"""
    assert has_code_files(tmp_path) is False


def test_has_code_files_only_docs(tmp_path):
    """仅包含 markdown / 文本 / 配置 → False。"""
    (tmp_path / "README.md").write_text("# hi")
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / ".gitignore").write_text("ignored")
    assert has_code_files(tmp_path) is False


def test_has_code_files_py_file(tmp_path):
    """单个 .py 文件 → True。"""
    (tmp_path / "main.py").write_text("print('hi')")
    assert has_code_files(tmp_path) is True


def test_has_code_files_c_file(tmp_path):
    """单个 .c 文件 → True。"""
    (tmp_path / "main.c").write_text("int main(){}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_cpp_file(tmp_path):
    """单个 .cpp 文件 → True。"""
    (tmp_path / "main.cpp").write_text("int main(){}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_v_file(tmp_path):
    """单个 .v (Verilog) 文件 → True。"""
    (tmp_path / "top.v").write_text("module top(); endmodule")
    assert has_code_files(tmp_path) is True


def test_has_code_files_case_insensitive_extension(tmp_path):
    """扩展名大小写不敏感。"""
    (tmp_path / "Main.PY").write_text("x = 1")
    (tmp_path / "Main.CPP").write_text("int main(){}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_nested(tmp_path):
    """子目录里的代码文件也能识别(递归)。"""
    src = tmp_path / "src" / "deep"
    src.mkdir(parents=True)
    (src / "module.py").write_text("x = 1")
    assert has_code_files(tmp_path) is True


def test_has_code_files_skips_git_and_node_modules(tmp_path):
    """.git / node_modules / __pycache__ 等跳过,即使里面有代码文件也不算。"""
    for skip in [".git", "node_modules", "__pycache__", "venv", "dist", "build"]:
        d = tmp_path / skip
        d.mkdir()
        (d / "noise.py").write_text("x = 1")  # 在垃圾目录里故意放 .py
        (d / "noise.c").write_text("int x;")
    assert has_code_files(tmp_path) is False, "垃圾目录里的代码文件不应被计入"


def test_has_code_files_skips_hidden_dirs(tmp_path):
    """以 . 开头的隐藏目录(如 .vscode, .idea)跳过。"""
    hidden = tmp_path / ".vscode"
    hidden.mkdir()
    (hidden / "settings.py").write_text("x = 1")
    assert has_code_files(tmp_path) is False


def test_has_code_files_file_without_extension(tmp_path):
    """无扩展名的文件(README, Makefile, LICENSE)不算代码文件。"""
    (tmp_path / "README").write_text("# hi")
    (tmp_path / "Makefile").write_text("all:")
    (tmp_path / "LICENSE").write_text("MIT")
    assert has_code_files(tmp_path) is False


def test_has_code_files_disallowed_extensions(tmp_path):
    """v2.9 扩展白名单:真正不在白名单的扩展名(.html/.css/.png/.json 等)不算代码文件。

    原版用 .h/.js/.go/.java 做反例,但这些已在扩展后的白名单中。
    本测试改用纯标记/样式/二进制/配置文件做反例。
    """
    for f in [
        "index.html",
        "style.css",
        "data.json",
        "config.yaml",
        "pyproject.toml",
        "logo.png",
        "doc.pdf",
        "script.sql",  # SQL 暂未纳入(数据查询语言,非通用代码)
    ]:
        (tmp_path / f).write_text("// not code")
    assert has_code_files(tmp_path) is False


# ── v2.9 白名单扩展后:各语言家族正向覆盖 ────────────────


def test_has_code_files_javascript(tmp_path):
    """JavaScript 系列 (.js / .mjs / .cjs / .jsx) → True。"""
    for ext in ["app.js", "module.mjs", "legacy.cjs", "component.jsx"]:
        (tmp_path / f"f_{ext}").write_text(f"// {ext}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_typescript(tmp_path):
    """TypeScript 系列 (.ts / .tsx / .mts / .cts) → True。"""
    for ext in ["app.ts", "component.tsx", "esm.mts", "commonjs.cts"]:
        (tmp_path / f"f_{ext}").write_text(f"// {ext}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_go_file(tmp_path):
    """Go: .go → True。"""
    (tmp_path / "main.go").write_text("package main")
    assert has_code_files(tmp_path) is True


def test_has_code_files_rust_file(tmp_path):
    """Rust: .rs → True。"""
    (tmp_path / "main.rs").write_text("fn main(){}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_java_file(tmp_path):
    """Java: .java → True。"""
    (tmp_path / "App.java").write_text("class App{}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_kotlin_files(tmp_path):
    """Kotlin: .kt / .kts → True。"""
    (tmp_path / "App.kt").write_text("// kt")
    (tmp_path / "build.kts").write_text("// kts")
    assert has_code_files(tmp_path) is True


def test_has_code_files_csharp_file(tmp_path):
    """C#: .cs → True。"""
    (tmp_path / "Program.cs").write_text("class Program{}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_ruby_file(tmp_path):
    """Ruby: .rb → True。"""
    (tmp_path / "app.rb").write_text("# ruby")
    assert has_code_files(tmp_path) is True


def test_has_code_files_php_file(tmp_path):
    """PHP: .php → True。"""
    (tmp_path / "index.php").write_text("<?php echo 'hi';")
    assert has_code_files(tmp_path) is True


def test_has_code_files_swift_file(tmp_path):
    """Swift: .swift → True。"""
    (tmp_path / "App.swift").write_text("// swift")
    assert has_code_files(tmp_path) is True


def test_has_code_files_shell_files(tmp_path):
    """Shell 系 (.sh / .bash / .ps1 / .bat) → True。"""
    for f in ["script.sh", "interactive.bash", "deploy.ps1", "run.bat"]:
        (tmp_path / f).write_text(f"# {f}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_functional_files(tmp_path):
    """函数式语言 (.hs / .ex / .exs / .erl / .ml / .clj) → True。"""
    for f in [
        "Main.hs",
        "module.ex",
        "test.exs",
        "app.erl",
        "main.ml",
        "core.clj",
    ]:
        (tmp_path / f).write_text(f"-- {f}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_hdl_files(tmp_path):
    """HDL 系列 (.v / .sv / .vhdl) → True。"""
    for f in ["top.v", "alu.sv", "regfile.vhdl"]:
        (tmp_path / f).write_text(f"// {f}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_cpp_alternate_extensions(tmp_path):
    """C++ 替代后缀 (.cc / .cxx / .hpp / .hxx) → True。"""
    for ext in ["main.cc", "main.cxx", "header.hpp", "header.hxx"]:
        (tmp_path / f"f_{ext}").write_text(f"// {ext}")
    assert has_code_files(tmp_path) is True


def test_has_code_files_frontend_frameworks(tmp_path):
    """前端框架文件 (.vue / .svelte) → True。"""
    (tmp_path / "App.vue").write_text("<template/>")
    (tmp_path / "App.svelte").write_text("<script/>")
    assert has_code_files(tmp_path) is True


def test_has_code_files_sql_excluded(tmp_path):
    """SQL 暂未纳入白名单(数据查询语言,非通用代码)。如需启用请编辑 CODE_FILE_EXTENSIONS。"""
    (tmp_path / "schema.sql").write_text("CREATE TABLE t();")
    (tmp_path / "queries.sql").write_text("SELECT 1;")
    assert has_code_files(tmp_path) is False


def test_has_code_files_nonexistent_directory(tmp_path):
    """目录不存在 → False(不抛异常)。"""
    ghost = tmp_path / "does_not_exist"
    assert has_code_files(ghost) is False


def test_has_code_files_is_directory_check(tmp_path, caplog):
    """传入文件路径而非目录 → False(不抛异常)。"""
    f = tmp_path / "single_file.py"
    f.write_text("x = 1")
    # 注意:这里直接对文件调用,应不抛异常,返回 False
    # (os.walk 对文件直接调用会 raise NotADirectoryError,需被内部捕获)
    result = has_code_files(f)
    assert result is False


def test_has_code_files_mixed_real_and_skip(tmp_path):
    """根目录有合法代码 + 垃圾目录里有噪声 → True(以真实代码为准)。"""
    (tmp_path / "main.py").write_text("x = 1")
    node = tmp_path / "node_modules"
    node.mkdir()
    (node / "dep.py").write_text("# not counted")
    assert has_code_files(tmp_path) is True


def test_has_code_files_multilevel_skip_dirs(tmp_path):
    """垃圾目录嵌套在子目录里也要跳过。"""
    nested = tmp_path / "src" / "node_modules"
    nested.mkdir(parents=True)
    (nested / "x.py").write_text("x = 1")
    (tmp_path / "src" / "real.py").write_text("x = 1")
    assert has_code_files(tmp_path) is True, "src/real.py 应被识别"
