"""tools/code_format + tools/function_tools/code_format 单元测试。

2026-08-14 重写:astyle → clang-format 迁移。
Spec: docs/superpowers/specs/2026-08-14-clang-format-unify-design.md

覆盖:
  1-3:  Python + ruff format (write / check / already-formatted)
  4-6:  C/C++ + clang-format (write / check / not-installed)
  7:    auto 路由 (.py → ruff, .cpp → clang-format)
  8:    文件不存在
  9:    不支持扩展名
  10:   幂等 (连续两次格式化)
  11:   clang-format 不安装 → proposal
  12:   ruff 不安装 → proposal
  13:   FunctionTool 包装 (record_and_run 模板)
  14:   ALL_TOOL_CLASSES / ALL_TOOL_NAMES 注册
  15:   LLM schema 不暴露 formatter/style/indent
  16:   注入的 default_style/default_indent 透传到 fallback-style
  17:   未注入时默认值 llvm + 4
  18:   clang-format subprocess 参数链(--assume-filename/--style=file/--fallback-style)
  19:   subprocess 异常捕获
  20:   CRLF 源文件字节级保真(clang-format 原生处理,无 astyle 双空行 bug)
  21:   ruff 走 python -m
  22:   GBK / UTF-8 BOM 编码字节级保真
  23:   _resolve_clang_format_style legacy 映射
  24:   (集成,需真实 clang-format)fallback-style / .clang-format 发现 / idempotent

测试策略:
  - ruff 真实可用 → 大部分用例走真实 subprocess
  - clang-format 行为 → mock _find_clang_format + subprocess.run(二进制管道)
  - 集成用例 → skipif(shutil.which("clang-format") is None)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import code_format  # noqa: E402

CLANG_FORMAT_AVAILABLE = shutil.which("clang-format") is not None
needs_clang_format = pytest.mark.skipif(
    not CLANG_FORMAT_AVAILABLE, reason="clang-format 未安装"
)


# ── Fixtures ──────────────────────────────────────────


@pytest.fixture
def unformatted_py(tmp_path: Path) -> Path:
    """一个需要 ruff format 处理的 .py 文件。"""
    f = tmp_path / "unformatted.py"
    f.write_text(
        "x=1;y=2\ndef foo(a,b):\n  return a+b\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def unformatted_cpp(tmp_path: Path) -> Path:
    """一个需要 clang-format 处理的 .cpp 文件。"""
    f = tmp_path / "unformatted.cpp"
    f.write_text(
        "int main(){int x=1;int y=2;if(x==y){return 0;}}\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def already_formatted_py(tmp_path: Path) -> Path:
    """一个已经符合 ruff 风格的 .py 文件。"""
    f = tmp_path / "ok.py"
    f.write_text(
        "x = 1\ny = 2\n\n\ndef foo(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def no_ruff(monkeypatch):
    """强制 ruff 不可用:让 ``subprocess.run`` 抛 ``FileNotFoundError``。"""

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("No module named 'ruff'(mocked by no_ruff fixture)")

    monkeypatch.setattr(code_format.subprocess, "run", fake_run)


@pytest.fixture
def no_clang_format(monkeypatch):
    """强制 clang-format 不可用:_find_clang_format() 返回空列表。"""
    monkeypatch.setattr(code_format, "_find_clang_format", lambda: [])


@pytest.fixture
def fake_clang_format_run(monkeypatch):
    """把对 clang-format 的 ``subprocess.run`` 调用替换为可控 mock(默认 noop)。

    2026-08-14:clang-format 走二进制 stdin/stdout:
      - input 是 bytes(原文件原始字节)
      - mock 返回的 stdout 也是 bytes

    用法:
        fake_clang_format_run["state"]["formatted"] = b"FORMATTED BYTES"
        → 下次 clang-format 调用返回该字节串作为 stdout

        fake_clang_format_run["state"]["raise"] = SomeError("xxx")
        → 下次 clang-format 调用抛该异常
    """
    calls: list[dict] = []
    state = {"formatted": None, "raise": None, "returncode": 0}

    monkeypatch.setattr(
        code_format, "_find_clang_format", lambda: ["fake-clang-format"]
    )

    real_run = code_format.subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd and isinstance(cmd, list) and len(cmd) > 0:
            cmd0 = str(cmd[0])
            if "clang-format" in cmd0.lower():
                calls.append(
                    {
                        "cmd": list(cmd),
                        "input": kwargs.get("input"),
                        "kwargs_keys": sorted(kwargs.keys()),
                    }
                )
                if state["raise"] is not None:
                    raise state["raise"]
                formatted = (
                    state["formatted"]
                    if state["formatted"] is not None
                    else kwargs.get("input", b"")
                )
                return code_format.subprocess.CompletedProcess(
                    args=cmd,
                    returncode=state["returncode"],
                    stdout=formatted,
                    stderr=b"",
                )
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(code_format.subprocess, "run", fake_run)
    return {"calls": calls, "state": state}


# ── 1. Python: ruff format 写回 ─────────────────────


def test_format_python_ruff_writes(unformatted_py: Path):
    """未格式化的 .py → ruff 改写文件 + changed=True。"""
    r = code_format.format(str(unformatted_py))
    assert r["ok"] is True
    assert r["formatter"] == "ruff"
    assert r["changed"] is True
    after = unformatted_py.read_text(encoding="utf-8")
    assert after != "x=1;y=2\ndef foo(a,b):\n  return a+b\n"


# ── 2. Python: ruff format --check 不写回 ─────────


def test_format_python_ruff_check_no_write(unformatted_py: Path):
    """check=True → 不写回 + changed=True(只读检测)。"""
    original = unformatted_py.read_text(encoding="utf-8")
    r = code_format.format(str(unformatted_py), check=True)
    assert r["ok"] is True
    assert r["formatter"] == "ruff"
    assert r["check"] is True
    assert r["changed"] is True
    assert unformatted_py.read_text(encoding="utf-8") == original


# ── 3. Python: 已格式化文件 → changed=False ──────────


def test_format_python_already_formatted(already_formatted_py: Path):
    """已经符合 ruff 风格的 .py → changed=False,文件不变。"""
    r = code_format.format(str(already_formatted_py))
    assert r["ok"] is True
    assert r["changed"] is False
    assert r["proposal"]


# ── 4. C++: clang-format 写回 ──────────────────────


def test_format_cpp_clang_format_writes(unformatted_cpp: Path, fake_clang_format_run):
    """未格式化的 .cpp → clang-format 改写文件 + changed=True。

    验证:
      - 文件被改写(字节级写回 mock 的 stdout)
      - clang-format 的 subprocess.run 被以正确参数调用 1 次
      - 入参 input 等于文件原始字节(二进制管道)
      - kwargs 包含 ``**_NO_WINDOW_KWARGS``(防黑框)
    """
    formatted = (
        b"int main() {\n"
        b"    int x = 1;\n"
        b"    int y = 2;\n"
        b"    if (x == y) {\n"
        b"        return 0;\n"
        b"    }\n"
        b"}\n"
    )
    fake_clang_format_run["state"]["formatted"] = formatted
    original_bytes = unformatted_cpp.read_bytes()
    r = code_format.format(str(unformatted_cpp), style="llvm", indent=4)
    assert r["ok"] is True
    assert r["formatter"] == "clang-format"
    assert r["formatter_options"] == {"style": "llvm", "indent": 4}
    assert r["changed"] is True
    # 字节级写回:clang-format 输出什么就写什么
    assert unformatted_cpp.read_bytes() == formatted
    # subprocess.run 必然被调 1 次,且参数正确
    assert len(fake_clang_format_run["calls"]) == 1
    call = fake_clang_format_run["calls"][0]
    cmd = call["cmd"]
    assert "clang-format" in cmd[0].lower()
    assert any(a.startswith("--assume-filename=") for a in cmd)
    assert any(a.startswith("--style=") for a in cmd)
    # input 是原始字节(二进制管道,不重编码)
    assert call["input"] == original_bytes
    assert isinstance(call["input"], bytes)
    # kwargs 包含 creationflags(CREATE_NO_WINDOW 防黑框)
    assert "creationflags" in call["kwargs_keys"]


# ── 5. C++: clang-format check 模式不写回 ──────────


def test_format_cpp_clang_format_check_no_write(
    unformatted_cpp: Path, fake_clang_format_run
):
    """check=True → 不写回原文件 + changed=True。"""
    fake_clang_format_run["state"]["formatted"] = b"FORMATTED CONTENT\n"
    original = unformatted_cpp.read_bytes()
    r = code_format.format(str(unformatted_cpp), check=True)
    assert r["ok"] is True
    assert r["check"] is True
    assert r["changed"] is True
    assert unformatted_cpp.read_bytes() == original


# ── 6. clang-format 不可用 → 友好错误 ──────────────


def test_format_cpp_clang_format_not_installed(unformatted_cpp: Path, no_clang_format):
    """clang-format 不可用 → ok=False + proposal + options。"""
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    assert r["error"] == "clang-format 未安装"
    assert "proposal" in r
    assert r["options"]
    assert unformatted_cpp.read_text(encoding="utf-8").startswith("int main(){")


# ── 7. auto 路由 ──────────────────────────────────


def test_format_auto_routes_by_extension(
    unformatted_py: Path, unformatted_cpp: Path, fake_clang_format_run
):
    """auto 模式:.py → ruff, .cpp → clang-format。"""
    r_py = code_format.format(str(unformatted_py))
    r_cpp = code_format.format(str(unformatted_cpp))
    assert r_py["formatter"] == "ruff"
    assert r_cpp["formatter"] == "clang-format"


# ── 8. 文件不存在 ─────────────────────────────────


def test_format_file_not_exists(tmp_path: Path):
    """文件不存在 → ok=False + 错误信息明确。"""
    ghost = tmp_path / "ghost.py"
    r = code_format.format(str(ghost))
    assert r["ok"] is False
    assert "不存在" in r["error"]


# ── 9. 不支持扩展名 ──────────────────────────────


def test_format_unsupported_extension(tmp_path: Path):
    """.md / .txt 等 → ok=False + supported_extensions。"""
    f = tmp_path / "README.md"
    f.write_text("# hi\n", encoding="utf-8")
    r = code_format.format(str(f))
    assert r["ok"] is False
    assert r["supported_extensions"]


# ── 10. 幂等 (连续两次格式化) ────────────────────


def test_format_is_idempotent(unformatted_cpp: Path, fake_clang_format_run):
    """第二次格式化同一文件 → changed=False。"""
    formatted = b"int main() {\n    int x = 1;\n    return 0;\n}\n"
    fake_clang_format_run["state"]["formatted"] = formatted
    r1 = code_format.format(str(unformatted_cpp))
    assert r1["changed"] is True

    fake_clang_format_run["state"]["formatted"] = unformatted_cpp.read_bytes()
    r2 = code_format.format(str(unformatted_cpp))
    assert r2["changed"] is False
    assert r2["ok"] is True


# ── 11. clang-format 不安装 → proposal 含安装提示 ──


def test_format_clang_format_not_installed_suggests_install(
    unformatted_cpp: Path, no_clang_format
):
    """clang-format 不可用时,proposal 应提示 pip install clang-format。"""
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    assert r["proposal"]
    assert "pip install clang-format" in r["proposal"]


# ── 12. ruff 不安装 → proposal 含安装提示 ────────


def test_format_ruff_not_installed(unformatted_py: Path, no_ruff):
    """ruff 不可用 → ok=False + 提示 pip install ruff。"""
    r = code_format.format(str(unformatted_py))
    assert r["ok"] is False
    assert r["error"] == "ruff 未安装"
    assert "pip install ruff" in r["proposal"]


# ── 13. FunctionTool 包装: record_and_run 模板 ───


def test_function_tool_code_format_runs(tmp_path: Path):
    """CodeFormatTool.call 走 record_and_run 模板,真实 ruff 调用。"""
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()
    assert tool.name == "code_format"
    assert "filepath" in tool.parameters["properties"]
    assert "check" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["filepath"]

    import asyncio

    f = tmp_path / "sample.py"
    f.write_text("x=1\n", encoding="utf-8")
    ctx = MagicMock()
    result = asyncio.run(tool.call(ctx, filepath=str(f)))
    import json as _json

    payload = _json.loads(result)
    assert payload["ok"] is True
    assert payload["formatter"] == "ruff"
    assert payload["changed"] is True
    assert "proposal" in payload


# ── 14. FunctionTool ALL_TOOL_CLASSES 注册 ─


def test_code_format_registered_in_all_tool_classes():
    """ALL_TOOL_CLASSES 必须包含 CodeFormatTool。"""
    from tools.function_tools import ALL_TOOL_CLASSES
    from tools.function_tools.code_format import CodeFormatTool

    assert CodeFormatTool in ALL_TOOL_CLASSES


def test_code_format_in_all_tool_names():
    """ALL_TOOL_NAMES 必须包含 'code_format'(供 enabled_tools 校验)。"""
    from tools._config_filter import ALL_TOOL_NAMES

    assert "code_format" in ALL_TOOL_NAMES


# ── 15. LLM schema 不暴露 formatter/style/indent ──


def test_function_tool_schema_excludes_internal_params():
    """formatter/style/indent 从 LLM schema 移除(实现细节由配置层控制)。"""
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()
    exposed = set(tool.parameters["properties"].keys())
    assert "filepath" in exposed
    assert "check" in exposed
    assert "formatter" not in exposed
    assert "style" not in exposed
    assert "indent" not in exposed


# ── 16. 注入的 default_style/default_indent 透传 ──


def test_function_tool_uses_injected_defaults(tmp_path: Path, fake_clang_format_run):
    """CodeFormatTool 实例属性 default_style/default_indent 透传到 fallback-style。"""
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()
    tool.default_style = "google"
    tool.default_indent = 2

    f = tmp_path / "test.cpp"
    f.write_text("int main(){return 0;}", encoding="utf-8")
    fake_clang_format_run["state"]["formatted"] = b"int main() {\n  return 0;\n}\n"

    import asyncio
    import json as _json

    ctx = MagicMock()
    result_str = asyncio.run(tool.call(ctx, filepath=str(f)))
    payload = _json.loads(result_str)

    assert payload["ok"] is True
    call = fake_clang_format_run["calls"][-1]
    cmd = call["cmd"]
    style_arg = next(a for a in cmd if a.startswith("--style="))
    assert "BasedOnStyle: google" in style_arg, f"应该用 google 预设,实际: {cmd}"
    assert "IndentWidth: 2" in style_arg, f"应该用 2 空格,实际: {cmd}"
    assert payload["formatter_options"] == {"style": "google", "indent": 2}


# ── 17. 未注入时默认值 llvm + 4 ──


def test_function_tool_default_values_when_not_injected(
    tmp_path: Path, fake_clang_format_run
):
    """未注入配置时,CodeFormatTool 用内置默认:llvm + 4 空格。"""
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()  # 不注入
    f = tmp_path / "test.cpp"
    f.write_text("int main(){return 0;}", encoding="utf-8")
    fake_clang_format_run["state"]["formatted"] = b"int main() {\n    return 0;\n}\n"

    import asyncio
    import json as _json

    ctx = MagicMock()
    result_str = asyncio.run(tool.call(ctx, filepath=str(f)))
    payload = _json.loads(result_str)

    assert payload["ok"] is True
    call = fake_clang_format_run["calls"][-1]
    cmd = call["cmd"]
    style_arg = next(a for a in cmd if a.startswith("--style="))
    assert "BasedOnStyle: llvm" in style_arg
    assert "IndentWidth: 4" in style_arg
    assert payload["formatter_options"] == {"style": "llvm", "indent": 4}


# ── 18. clang-format subprocess 参数链 ──


def test_format_clang_format_passes_correct_args_to_subprocess(
    unformatted_cpp: Path, fake_clang_format_run
):
    """subprocess.run 调用 clang-format 必须收到完整参数链。

    参数链(与 code_check 同源):
      --assume-filename=<绝对路径>      语言检测 + .clang-format 向上发现
      --style=file                     项目内 .clang-format 存在时
      --style={BasedOnStyle: X, ...}   无 .clang-format 时内联兜底
    """
    fake_clang_format_run["state"]["formatted"] = b"FORMATTED"
    code_format.format(str(unformatted_cpp), style="google", indent=2)
    assert len(fake_clang_format_run["calls"]) == 1
    call = fake_clang_format_run["calls"][0]
    cmd = call["cmd"]
    assert "clang-format" in cmd[0].lower()
    assert f"--assume-filename={unformatted_cpp}" in cmd
    # tmp_path 无 .clang-format → 内联 style 兜底
    style_arg = next(a for a in cmd if a.startswith("--style="))
    assert style_arg == "--style={BasedOnStyle: google, IndentWidth: 2}"
    if sys.platform == "win32":
        assert "creationflags" in call["kwargs_keys"]


# ── 19. subprocess 抛异常 → 捕获并返回 ok=False ──


def test_format_clang_format_subprocess_exception_caught(
    unformatted_cpp: Path, fake_clang_format_run
):
    """subprocess.run 抛任何异常 → ok=False + error 信息。"""
    fake_clang_format_run["state"]["raise"] = RuntimeError("simulated crash")
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    assert "clang-format 调用失败" in r["error"] or "simulated crash" in r["error"]
    assert unformatted_cpp.read_text(encoding="utf-8").startswith("int main(){")


def test_format_clang_format_nonzero_exit(unformatted_cpp: Path, fake_clang_format_run):
    """clang-format 非零退出 → ok=False + stderr 摘要。"""
    fake_clang_format_run["state"]["returncode"] = 1
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    assert "退出码" in r["error"]
    # 文件未被改写
    assert unformatted_cpp.read_text(encoding="utf-8").startswith("int main(){")


# ── 20. CRLF 源文件字节级保真 ──
#
# 2026-08-14:astyle 时代的 CRLF 双空行 bug 随 astyle 一起移除。
# clang-format 原生 DeriveLineEnding 保留主导行尾;我们以二进制管道透传,
# stdin 收原始字节(含 CRLF),stdout 字节原样写回,不做任何归一化。


def test_format_crlf_source_binary_passthrough(tmp_path: Path, fake_clang_format_run):
    """CRLF 源文件:stdin 收到原始 CRLF 字节,写回保持 mock 输出字节。"""
    f = tmp_path / "crlf.cpp"
    original = b"int main(){return 0;}\r\n\r\n"
    f.write_bytes(original)

    formatted = b"int main() {\r\n    return 0;\r\n}\r\n\r\n"
    fake_clang_format_run["state"]["formatted"] = formatted

    r = code_format.format(str(f), style="llvm", indent=4)
    assert r["ok"] is True

    assert len(fake_clang_format_run["calls"]) == 1
    stdin_bytes = fake_clang_format_run["calls"][0]["input"]
    # stdin 必须是原始字节(不做 astyle 时代的 CRLF→LF 归一化)
    assert stdin_bytes == original
    # 写回必须是 mock 输出字节(CRLF 保持)
    assert f.read_bytes() == formatted


# ── 21. ruff 调用走 python -m,不走路径检测 ──────


def test_format_ruff_uses_python_m_directly(monkeypatch, unformatted_py: Path):
    """ruff 调用必须用 ``[sys.executable, "-m", "ruff", "format", ...]``。"""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(code_format.subprocess, "run", fake_run)

    code_format.format(str(unformatted_py))

    cmd = captured["cmd"]
    assert cmd[0] == sys.executable, f"应走 sys.executable,实际: {cmd[0]}"
    assert "-m" in cmd
    assert "ruff" in cmd
    assert "format" in cmd
    assert not any(part.endswith("ruff.exe") for part in cmd)


# ── 22. 编码字节级保真 (GBK / UTF-8 BOM) ──
#
# 2026-08-14:二进制管道后,编码保真由"写回 clang-format 原始输出字节"
# 天然保证——clang-format 不重编码 stdin 字节流。


def test_format_clang_format_preserves_gbk_bytes(tmp_path: Path, fake_clang_format_run):
    """GBK 编码 .cpp:stdin 收到 GBK 原始字节,写回保持 GBK 输出字节。"""
    f = tmp_path / "gbk.cpp"
    original = "// 中文注释\nint main(){return 0;}\n".encode("gbk")
    f.write_bytes(original)
    formatted = "// 中文注释\nint main() {\n    return 0;\n}\n".encode("gbk")
    fake_clang_format_run["state"]["formatted"] = formatted

    r = code_format.format(str(f), style="llvm", indent=4)
    assert r["ok"] is True
    assert r["changed"] is True

    assert fake_clang_format_run["calls"][0]["input"] == original
    raw = f.read_bytes()
    assert raw == formatted, "文件应保持 GBK 字节(不被强转 UTF-8)"
    assert "中文注释" in raw.decode("gbk")


def test_format_clang_format_preserves_utf8_bom(tmp_path: Path, fake_clang_format_run):
    """UTF-8 BOM 文件格式化后仍保留 BOM(字节级写回)。"""
    f = tmp_path / "bom.cpp"
    f.write_bytes(b"\xef\xbb\xbfint main(){return 0;}\n")
    formatted = b"\xef\xbb\xbfint main() {\n    return 0;\n}\n"
    fake_clang_format_run["state"]["formatted"] = formatted

    r = code_format.format(str(f), style="llvm", indent=4)
    assert r["ok"] is True
    assert r["changed"] is True

    raw = f.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM 丢失"
    assert raw == formatted


# ── 23. _resolve_clang_format_style legacy 映射 ──


def test_resolve_style_presets():
    """clang-format 预设 → {BasedOnStyle: X, IndentWidth: N}。"""
    assert code_format._resolve_clang_format_style("llvm", 4) == (
        "{BasedOnStyle: llvm, IndentWidth: 4}"
    )
    assert code_format._resolve_clang_format_style("google", 2) == (
        "{BasedOnStyle: google, IndentWidth: 2}"
    )


def test_resolve_style_legacy_allman():
    """legacy allman → 完整 style 串(Allman braces),indent 不叠加。"""
    resolved = code_format._resolve_clang_format_style("allman", 4)
    assert resolved == "{BasedOnStyle: llvm, BreakBeforeBraces: Allman}"


def test_resolve_style_legacy_linux():
    """legacy linux → 完整 style 串(8 空格 tab),indent 不叠加。"""
    resolved = code_format._resolve_clang_format_style("linux", 4)
    assert resolved == "{BasedOnStyle: llvm, IndentWidth: 8, UseTab: Always}"


def test_resolve_style_legacy_plain_preset():
    """legacy kr/stroustrup/java 等 → llvm 预设,indent 叠加。"""
    for legacy in ("kr", "stroustrup", "java", "whitesmith"):
        assert code_format._resolve_clang_format_style(legacy, 3) == (
            "{BasedOnStyle: llvm, IndentWidth: 3}"
        )


def test_format_rejects_unknown_style(unformatted_cpp: Path):
    """完全不认识的 style → ok=False + supported_styles + legacy_styles。"""
    r = code_format.format(str(unformatted_cpp), style="bogus-style")
    assert r["ok"] is False
    assert "supported_styles" in r
    assert "legacy_styles" in r
    assert "llvm" in r["supported_styles"]
    assert "allman" in r["legacy_styles"]


def test_format_accepts_legacy_style(unformatted_cpp: Path, fake_clang_format_run):
    """legacy astyle 风格名仍被接受(向后兼容既有配置)。"""
    fake_clang_format_run["state"]["formatted"] = b"int main()\n{\n}\n"
    r = code_format.format(str(unformatted_cpp), style="allman", indent=4)
    assert r["ok"] is True
    cmd = fake_clang_format_run["calls"][0]["cmd"]
    style_arg = next(a for a in cmd if a.startswith("--style="))
    assert "BreakBeforeBraces: Allman" in style_arg


# ── 24. 集成测试(真实 clang-format) ──


# 多语句函数体:防止 clang-format 把单行函数折叠(AllowShortFunctionsOnASingleLine)
_UNFORMATTED_CPP = "int main(){\nint x=1;\nif(x>0){return x;}\nreturn 0;\n}\n"


@needs_clang_format
def test_real_clang_format_fallback_style(tmp_path: Path):
    """无 .clang-format 时内联 style 兜底生效:llvm+2 空格缩进。"""
    f = tmp_path / "real.cpp"
    f.write_text(_UNFORMATTED_CPP, encoding="utf-8")
    r = code_format.format(str(f), style="llvm", indent=2)
    assert r["ok"] is True
    assert r["changed"] is True
    content = f.read_text(encoding="utf-8")
    assert "\n  return 0;\n" in content, f"应为 2 空格缩进,实际: {content!r}"


@needs_clang_format
def test_real_clang_format_idempotent(tmp_path: Path):
    """真实 clang-format:第二次格式化 → changed=False。"""
    f = tmp_path / "real2.cpp"
    f.write_text(_UNFORMATTED_CPP, encoding="utf-8")
    r1 = code_format.format(str(f), style="llvm", indent=4)
    assert r1["changed"] is True
    r2 = code_format.format(str(f), style="llvm", indent=4)
    assert r2["ok"] is True
    assert r2["changed"] is False


@needs_clang_format
def test_real_clang_format_project_file_wins(tmp_path: Path):
    """项目内 .clang-format 优先于插件配置(fallback-style 被忽略)。"""
    (tmp_path / ".clang-format").write_text(
        "BasedOnStyle: Google\nIndentWidth: 3\n", encoding="utf-8"
    )
    f = tmp_path / "styled.cpp"
    f.write_text(_UNFORMATTED_CPP, encoding="utf-8")
    # 配置说 llvm+4,但 .clang-format 说 IndentWidth: 3 → 必须 3 空格
    r = code_format.format(str(f), style="llvm", indent=4)
    assert r["ok"] is True
    assert r["changed"] is True
    content = f.read_text(encoding="utf-8")
    assert "\n   return 0;\n" in content, (
        f".clang-format 的 IndentWidth: 3 应优先生效,实际: {content!r}"
    )


@needs_clang_format
def test_real_clang_format_legacy_allman_braces(tmp_path: Path):
    """legacy allman 映射真实生效:花括号换行(BreakBeforeBraces: Allman)。"""
    f = tmp_path / "allman.cpp"
    f.write_text(_UNFORMATTED_CPP, encoding="utf-8")
    r = code_format.format(str(f), style="allman", indent=4)
    assert r["ok"] is True
    assert r["changed"] is True
    content = f.read_text(encoding="utf-8")
    assert "int main()\n{" in content, f"Allman 风格花括号应换行,实际: {content!r}"


@needs_clang_format
def test_real_clang_format_check_dry_run_no_write(tmp_path: Path):
    """真实 clang-format check=True:不写回 + changed=True + diff_summary。"""
    f = tmp_path / "dry.cpp"
    original = _UNFORMATTED_CPP
    f.write_text(original, encoding="utf-8")
    r = code_format.format(str(f), style="llvm", indent=4, check=True)
    assert r["ok"] is True
    assert r["changed"] is True
    assert r["diff_summary"]
    assert f.read_text(encoding="utf-8") == original


# ── 25. _clang_format_flags:.clang-format 发现 vs 内联兜底 ──


def test_clang_format_flags_inline_when_no_config(tmp_path: Path):
    """目录链上无 .clang-format → --style={BasedOnStyle...} 内联兜底。"""
    f = tmp_path / "x.cpp"
    f.write_text("int main(){return 0;}\n", encoding="utf-8")
    flags = code_format._clang_format_flags(f, style="llvm", indent=4)
    assert f"--assume-filename={f}" in flags
    assert "--style={BasedOnStyle: llvm, IndentWidth: 4}" in flags
    assert "--style=file" not in flags


def test_clang_format_flags_style_file_when_config_exists(tmp_path: Path):
    """目录链上存在 .clang-format → --style=file(配置被忽略)。"""
    (tmp_path / ".clang-format").write_text("BasedOnStyle: Google\n", encoding="utf-8")
    sub = tmp_path / "src"
    sub.mkdir()
    f = sub / "x.cpp"
    f.write_text("int main(){return 0;}\n", encoding="utf-8")
    flags = code_format._clang_format_flags(f, style="llvm", indent=4)
    assert "--style=file" in flags
    assert not any(a.startswith("--style={") for a in flags)
