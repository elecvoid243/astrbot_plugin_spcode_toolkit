"""tools/code_format + tools/function_tools/code_format 单元测试。

覆盖(13 用例):
  1-3:  Python + ruff format (write / check / already-formatted)
  4-6:  C/C++ + astyle  (write / check / not-installed)
  7:    auto 路由 (.py → ruff, .cpp → astyle)
  8:    文件不存在
  9:    不支持扩展名
  10:   幂等 (连续两次格式化)
  11:   astyle 不安装 → proposal
  12:   ruff 不安装 → proposal
  13:   FunctionTool 包装 (record_and_run 模板)

测试策略:
  - ruff 真实可用 → 大部分用例走真实 subprocess
  - astyle 不一定安装 → 通过 monkeypatch 注入 mock
  - 边界场景 (subprocess 行为) → mock subprocess.run
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import code_format  # noqa: E402


# ── Fixtures ──────────────────────────────────────────


@pytest.fixture
def unformatted_py(tmp_path: Path) -> Path:
    """一个需要 ruff format 处理的 .py 文件。"""
    f = tmp_path / "unformatted.py"
    f.write_text(
        "x=1;y=2\n"
        "def foo(a,b):\n"
        "  return a+b\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def unformatted_cpp(tmp_path: Path) -> Path:
    """一个需要 astyle 处理的 .cpp 文件。"""
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
        "x = 1\n"
        "y = 2\n"
        "\n"
        "\n"
        "def foo(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def no_ruff(monkeypatch):
    """强制 ruff 不可用。"""
    monkeypatch.setattr(code_format, "_find_ruff", lambda: [])


@pytest.fixture
def no_astyle(monkeypatch):
    """强制 astyle 不可用。"""
    monkeypatch.setattr(code_format, "_find_astyle", lambda: [])


@pytest.fixture
def fake_astyle_run(monkeypatch):
    """把 subprocess.run 替换为可控 mock,默认模拟 astyle 简单缩进。"""
    calls: list[dict] = []
    state = {"stdout": "", "returncode": 0, "stderr": ""}

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "input": kwargs.get("input", b"")})
        cp = subprocess.CompletedProcess(
            args=cmd, returncode=state["returncode"],
            stdout=state["stdout"], stderr=state["stderr"],
        )
        return cp

    monkeypatch.setattr(
        code_format.subprocess, "run", fake_run,
    )
    monkeypatch.setattr(code_format, "_find_astyle", lambda: ["AStyle"])
    return {"calls": calls, "state": state}


# ── 1. Python: ruff format 写回 ─────────────────────


def test_format_python_ruff_writes(unformatted_py: Path):
    """未格式化的 .py → ruff 改写文件 + changed=True。"""
    r = code_format.format(str(unformatted_py))
    assert r["ok"] is True
    assert r["formatter"] == "ruff"
    assert r["changed"] is True
    # ruff 实际改写后,内容必然与原始不同
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
    # 文件**未**被改写
    assert unformatted_py.read_text(encoding="utf-8") == original


# ── 3. Python: 已格式化文件 → changed=False ──────────


def test_format_python_already_formatted(already_formatted_py: Path):
    """已经符合 ruff 风格的 .py → changed=False,文件不变。"""
    r = code_format.format(str(already_formatted_py))
    assert r["ok"] is True
    assert r["changed"] is False
    assert r["proposal"]


# ── 4. C++: astyle 写回 ────────────────────────────


def test_format_cpp_astyle_writes(unformatted_cpp: Path, fake_astyle_run):
    """未格式化的 .cpp → astyle 改写文件 + changed=True。"""
    fake_astyle_run["state"]["stdout"] = (
        "int main()\n"
        "{\n"
        "    int x=1;\n"
        "    int y=2;\n"
        "    if (x==y) {\n"
        "        return 0;\n"
        "    }\n"
        "}\n"
    )
    r = code_format.format(str(unformatted_cpp), style="allman", indent=4)
    assert r["ok"] is True
    assert r["formatter"] == "astyle"
    assert r["formatter_options"] == {"style": "allman", "indent": 4}
    assert r["changed"] is True
    # 文件被改写
    after = unformatted_cpp.read_text(encoding="utf-8")
    assert after == fake_astyle_run["state"]["stdout"]
    # astyle 必然被以 stdin/stdout 方式调用
    assert len(fake_astyle_run["calls"]) == 1
    cmd = fake_astyle_run["calls"][0]["cmd"]
    assert cmd[0] == "AStyle"
    assert "--style=allman" in cmd
    assert "--indent=spaces=4" in cmd


# ── 5. C++: astyle check 模式不写回 ───────────────


def test_format_cpp_astyle_check_no_write(unformatted_cpp: Path, fake_astyle_run):
    """check=True → 不写回原文件 + changed=True。"""
    fake_astyle_run["state"]["stdout"] = "FORMATTED CONTENT\n"
    original = unformatted_cpp.read_text(encoding="utf-8")
    r = code_format.format(str(unformatted_cpp), check=True)
    assert r["ok"] is True
    assert r["check"] is True
    assert r["changed"] is True
    # 原文件未变
    assert unformatted_cpp.read_text(encoding="utf-8") == original


# ── 6. astyle 不可用 → 友好错误 ───────────────────


def test_format_cpp_astyle_not_installed(unformatted_cpp: Path, no_astyle):
    """AStyle 不可用 → ok=False + proposal + options。"""
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    assert r["error"] == "astyle 未安装"
    assert "proposal" in r
    assert r["options"]  # 非空,给 LLM 决策建议
    # 文件**未**被改写
    assert unformatted_cpp.read_text(encoding="utf-8").startswith("int main(){")


# ── 7. auto 路由 ──────────────────────────────────


def test_format_auto_routes_by_extension(
    unformatted_py: Path, unformatted_cpp: Path, fake_astyle_run
):
    """auto 模式:.py → ruff, .cpp → astyle。"""
    fake_astyle_run["state"]["stdout"] = unformatted_cpp.read_text()  # noop
    r_py = code_format.format(str(unformatted_py))
    r_cpp = code_format.format(str(unformatted_cpp))
    assert r_py["formatter"] == "ruff"
    assert r_cpp["formatter"] == "astyle"


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
    assert r["supported_extensions"]  # 非空,列出 .py + astyle 全部


# ── 10. 幂等 (连续两次格式化) ────────────────────


def test_format_is_idempotent(unformatted_cpp: Path, fake_astyle_run):
    """第二次格式化同一文件 → changed=False。"""
    # 第一次:mock 返回与原文件不同
    formatted = (
        "int main()\n{\n    int x=1;\n    int y=2;\n    if (x==y) {\n        return 0;\n    }\n}\n"
    )
    fake_astyle_run["state"]["stdout"] = formatted
    r1 = code_format.format(str(unformatted_cpp))
    assert r1["changed"] is True

    # 第二次:mock 返回与现文件相同(已格式化)
    fake_astyle_run["state"]["stdout"] = unformatted_cpp.read_text()
    r2 = code_format.format(str(unformatted_cpp))
    assert r2["changed"] is False
    assert r2["ok"] is True


# ── 11. astyle 不安装 → proposal 含安装提示 ──────


def test_format_astyle_not_installed_suggests_install(unformatted_cpp: Path, no_astyle):
    """astyle 不可用时,proposal 应提示 pip install astyle。"""
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    assert r["proposal"]
    assert "pip install astyle" in r["proposal"]


# ── 12. ruff 不安装 → proposal 含安装提示 ────────


def test_format_ruff_not_installed(unformatted_py: Path, no_ruff):
    """ruff 不可用 → ok=False + 提示 pip install ruff。"""
    r = code_format.format(str(unformatted_py))
    assert r["ok"] is False
    assert r["error"] == "ruff 未安装"
    assert "pip install ruff" in r["proposal"]


# ── 13. FunctionTool 包装: record_and_run 模板 ───


def test_function_tool_code_format_runs(tmp_path: Path):
    """CodeFormatTool.call 走 record_and_run 模板,真实 ruff 调用。

    v2.14.1 简化:LLM schema 只暴露 filepath + check。
    formatter/style/indent 由 main.py 从配置注入到实例属性。
    """
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()
    # name + schema 基础断言
    assert tool.name == "code_format"
    assert "filepath" in tool.parameters["properties"]
    assert "check" in tool.parameters["properties"]
    assert tool.parameters["required"] == ["filepath"]

    # call() 经 record_and_run 异步返回 JSON 字符串
    import asyncio
    f = tmp_path / "sample.py"
    f.write_text("x=1\n", encoding="utf-8")
    ctx = MagicMock()
    result = asyncio.run(tool.call(ctx, filepath=str(f)))
    # record_and_run → unwrap → JSON 字符串
    import json as _json
    payload = _json.loads(result)
    assert payload["ok"] is True
    # 注:format() 返回的 dict 含 proposal 字段,unwrap 透传不包 data。
    # 这是 unwrap() 的设计:含 proposal/options/evidence/next_call 任意一个就原样序列化。
    assert payload["formatter"] == "ruff"
    assert payload["changed"] is True
    assert "proposal" in payload
    # 不显式 unlink:pytest tmp_path 自动清理;Windows 下 ruff 进程对文件句柄
    # 的释放有短暂延迟,显式 unlink 会偶发 PermissionError,留给 tmp_path GC。


# ── 14. (bonus) FunctionTool ALL_TOOL_CLASSES 注册 ─


def test_code_format_registered_in_all_tool_classes():
    """ALL_TOOL_CLASSES 必须包含 CodeFormatTool。"""
    from tools.function_tools import ALL_TOOL_CLASSES
    from tools.function_tools.code_format import CodeFormatTool

    assert CodeFormatTool in ALL_TOOL_CLASSES


def test_code_format_in_all_tool_names():
    """ALL_TOOL_NAMES 必须包含 'code_format'(供 enabled_tools 校验)。"""
    from tools._config_filter import ALL_TOOL_NAMES

    assert "code_format" in ALL_TOOL_NAMES


# ── 15. v2.14.1 API 简化: LLM schema 不再暴露 formatter/style/indent ──


def test_function_tool_schema_excludes_internal_params():
    """v2.14.1:formatter/style/indent 从 LLM schema 移除(LLM 不应关心实现细节)。

    LLM 只暴露 2 个参数:filepath (必填) + check (optional, dry-run 开关)。
    其它实现细节(formatter 选择 + style/indent)由配置层控制。
    """
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()
    exposed = set(tool.parameters["properties"].keys())
    # filepath + check 是 LLM 该看到的
    assert "filepath" in exposed
    assert "check" in exposed
    # formatter/style/indent 是实现细节,不应暴露
    assert "formatter" not in exposed, (
        "v2.14.1: formatter 已从 LLM schema 移除,后缀名自动路由"
    )
    assert "style" not in exposed, (
        "v2.14.1: style 已从 LLM schema 移除,使用配置默认"
    )
    assert "indent" not in exposed, (
        "v2.14.1: indent 已从 LLM schema 移除,使用配置默认"
    )


def test_function_tool_uses_injected_defaults(tmp_path: Path, fake_astyle_run):
    """v2.14.1:CodeFormatTool 实例属性 default_style/default_indent 由 main.py 注入,
    call() 时透传给底层 format()。
    """
    from tools.function_tools.code_format import CodeFormatTool

    # 模拟 main.py 注入配置(从 _conf_schema.json 读出)
    tool = CodeFormatTool()
    tool.default_style = "google"   # 非默认值,验证注入生效
    tool.default_indent = 2         # 非默认值,验证注入生效

    # 构造一个 .cpp 文件 + 让 astyle mock 返回不同内容
    f = tmp_path / "test.cpp"
    f.write_text("int main(){return 0;}", encoding="utf-8")
    fake_astyle_run["state"]["stdout"] = "int main() {\n  return 0;\n}\n"

    import asyncio
    import json as _json

    ctx = MagicMock()
    result_str = asyncio.run(tool.call(ctx, filepath=str(f)))
    payload = _json.loads(result_str)

    # 验证 astyle 用了我们注入的 google + 2 空格
    assert payload["ok"] is True
    astyle_call = fake_astyle_run["calls"][-1]
    cmd = astyle_call["cmd"]
    assert "--style=google" in cmd, f"应该用 google 风格,实际: {cmd}"
    assert "--indent=spaces=2" in cmd, f"应该用 2 空格,实际: {cmd}"
    # returned formatter_options 应回显注入值
    assert payload["formatter_options"] == {"style": "google", "indent": 2}


def test_function_tool_default_values_when_not_injected(tmp_path: Path, fake_astyle_run):
    """未注入配置时,CodeFormatTool 用内置默认:allman + 4 空格。"""
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()  # 不注入
    f = tmp_path / "test.cpp"
    f.write_text("int main(){return 0;}", encoding="utf-8")
    fake_astyle_run["state"]["stdout"] = "int main()\n{\n    return 0;\n}\n"

    import asyncio
    import json as _json
    ctx = MagicMock()
    result_str = asyncio.run(tool.call(ctx, filepath=str(f)))
    payload = _json.loads(result_str)

    assert payload["ok"] is True
    astyle_call = fake_astyle_run["calls"][-1]
    cmd = astyle_call["cmd"]
    assert "--style=allman" in cmd
    assert "--indent=spaces=4" in cmd
    assert payload["formatter_options"] == {"style": "allman", "indent": 4}
