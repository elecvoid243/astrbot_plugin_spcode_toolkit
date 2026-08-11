"""tools/code_format + tools/function_tools/code_format 单元测试。

覆盖(19 用例):
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
        "x=1;y=2\ndef foo(a,b):\n  return a+b\n",
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
        "x = 1\ny = 2\n\n\ndef foo(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def no_ruff(monkeypatch):
    """强制 ruff 不可用:让 ``subprocess.run`` 抛 ``FileNotFoundError``。

    v2.15(2026-07-01)后 _format_with_ruff 不再做路径检测,改用
    ``python -m ruff`` 直接调。不可用的最常见表现就是 ModuleNotFoundError
    → subprocess.run 抛 FileNotFoundError,我们在这里模拟这个错误。
    """

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("No module named 'ruff'(mocked by no_ruff fixture)")

    monkeypatch.setattr(code_format.subprocess, "run", fake_run)


@pytest.fixture
def no_astyle(monkeypatch):
    """强制 astyle 不可用:让 ``Path(<expected_path>).exists()`` 返回 ``False``。

    v2.15.1(2026-07-01)回滚:astyle 改回 subprocess.run 调用 astyle.exe。
    v2.15.1+ (用户反馈):路径查找改为从 sys.executable 推算到
    <python_dir>/Scripts/astyle.exe。所以"不可用"=这个路径不存在。
    """
    import tools.code_format as _cf

    real_path_exists = _cf.Path.exists

    def fake_path_exists(self) -> bool:
        # astyle.exe 路径 → 视为不存在(模拟未 pip install astyle)
        if "astyle" in str(self).lower() and str(self).lower().endswith(".exe"):
            return False
        return real_path_exists(self)

    monkeypatch.setattr(_cf.Path, "exists", fake_path_exists)


@pytest.fixture
def fake_astyle_run(monkeypatch):
    """把对 astyle.exe 的 ``subprocess.run`` 调用替换为可控 mock(默认 noop)。

    v2.15.1(2026-07-01)回滚:astyle 改回 subprocess.run 调用 astyle.exe。
    路径查找改为**从 sys.executable 推算**到 <python_dir>/Scripts/astyle.exe。

    本 fixture 实现:
    - mock ``tools.code_format.Path.exists`` → 对 astyle 路径返回 True
      (模拟 pip install 后真的存在 Scripts/astyle.exe)
    - mock ``tools.code_format.subprocess.run`` → 智能 spy:
        - 当 args[0] 是 astyle.exe 路径时:拦截,返回 mock 的 CompletedProcess
        - 其他 subprocess.run(例如 ruff 路径)透传,真实执行

    用法:
        fake_astyle_run["state"]["formatted"] = "FORMATTED TEXT"
        → 下次 astyle 调用返回该文本作为 stdout
        → changed = formatted != input

        fake_astyle_run["state"]["raise"] = SomeError("xxx")
        → 下次 astyle 调用抛该异常(模拟 astyle.exe 崩溃)
    """
    import tools.code_format as _cf

    calls: list[dict] = []
    state = {"formatted": None, "raise": None, "returncode": 0}

    # 1. mock Path.exists:对 astyle 路径返回 True,其他透传
    real_path_exists = _cf.Path.exists

    def fake_path_exists(self) -> bool:
        # astyle.exe 路径 → 视为存在(模拟 pip install astyle 后的真实环境)
        if "astyle" in str(self).lower() and str(self).lower().endswith(".exe"):
            return True
        return real_path_exists(self)

    monkeypatch.setattr(_cf.Path, "exists", fake_path_exists)

    # 2. mock subprocess.run:拦截 astyle 调用,其他透传
    real_run = _cf.subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd and isinstance(cmd, list) and len(cmd) > 0:
            cmd0 = str(cmd[0])
            if "astyle" in cmd0.lower():
                # 记录调用
                calls.append(
                    {
                        "cmd": list(cmd),
                        "input": kwargs.get("input"),
                        "kwargs_keys": sorted(kwargs.keys()),
                    }
                )
                # 触发模拟异常
                if state["raise"] is not None:
                    raise state["raise"]
                # 返回 mock CompletedProcess
                formatted = (
                    state["formatted"]
                    if state["formatted"] is not None
                    else kwargs.get("input", "")
                )
                return _cf.subprocess.CompletedProcess(
                    args=cmd,
                    returncode=state["returncode"],
                    stdout=formatted,
                    stderr="",
                )
        # 透传(例如 ruff 路径)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(_cf.subprocess, "run", fake_run)

    # 3. 计算 astyle 路径(供测试断言使用)
    import sys as _sys

    astyle_path = _cf.Path(_sys.executable).parent / "Scripts" / "astyle.exe"
    return {"calls": calls, "state": state, "astyle_path": str(astyle_path)}


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
    """未格式化的 .cpp → astyle 改写文件 + changed=True。

    v2.15.1(2026-07-01)回滚:astyle 改回 subprocess.run 调用 astyle.exe。
    验证:
      - 文件被改写
      - astyle.exe 的 subprocess.run 被以正确参数调用 1 次
      - 入参 input 等于文件改写前的内容
      - kwargs 包含 ``**_NO_WINDOW_KWARGS``(防黑框)
    """
    formatted = (
        "int main()\n"
        "{\n"
        "    int x=1;\n"
        "    int y=2;\n"
        "    if (x==y) {\n"
        "        return 0;\n"
        "    }\n"
        "}\n"
    )
    fake_astyle_run["state"]["formatted"] = formatted
    original_text = unformatted_cpp.read_bytes().decode("utf-8")
    r = code_format.format(str(unformatted_cpp), style="allman", indent=4)
    assert r["ok"] is True
    assert r["formatter"] == "astyle"
    assert r["formatter_options"] == {"style": "allman", "indent": 4}
    assert r["changed"] is True
    # 文件被改写 — 归一化行尾后比较(Windows 上 write_text 会 \n → \r\n)
    after = unformatted_cpp.read_bytes().decode("utf-8")
    assert after.replace("\r\n", "\n") == formatted, (
        "astyle subprocess 输出的文本应被正确写回文件"
    )
    # subprocess.run 必然被调 1 次,且参数正确
    assert len(fake_astyle_run["calls"]) == 1
    call = fake_astyle_run["calls"][0]
    cmd = call["cmd"]
    # 1. 第一个元素是 astyle.exe 路径
    assert "astyle" in cmd[0].lower()
    # 2. 包含 --style + --indent 参数
    assert "--style=allman" in cmd
    assert "--indent=spaces=4" in cmd
    # 3. input 是源码文本(2026-07-31:CRLF 源已归一为 LF 喂给 astyle)
    #    旧断言 ``call["input"] == original_text`` 在 Windows 上偶然通过
    #    是因为 fix 之前的代码确实直通 CRLF,fix 后必须用归一化后比较。
    expected_input = original_text.replace("\r\n", "\n").replace("\r", "\n")
    assert call["input"] == expected_input, (
        f"astyle stdin 应等于 CRLF→LF 归一后的源文本;实得={call['input']!r}"
    )
    assert "\r" not in call["input"], (
        f"astyle stdin 不应再含 CR (2026-07-31 防双空行 bug);实得={call['input']!r}"
    )
    # 4. kwargs 包含 creationflags(CREATE_NO_WINDOW 防黑框)
    assert "creationflags" in call["kwargs_keys"]


# ── 5. C++: astyle check 模式不写回 ───────────────


def test_format_cpp_astyle_check_no_write(unformatted_cpp: Path, fake_astyle_run):
    """check=True → 不写回原文件 + changed=True。"""
    fake_astyle_run["state"]["formatted"] = "FORMATTED CONTENT\n"
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
    # v2.15(2026-07-01):astyle 走 Python 库,mock 默认 noop (返回原文)
    # → changed=False 但 formatter 仍是 "astyle"
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
    formatted = "int main()\n{\n    int x=1;\n    int y=2;\n    if (x==y) {\n        return 0;\n    }\n}\n"
    fake_astyle_run["state"]["formatted"] = formatted
    r1 = code_format.format(str(unformatted_cpp))
    assert r1["changed"] is True

    # 第二次:mock 返回与现文件相同(已格式化)
    fake_astyle_run["state"]["formatted"] = unformatted_cpp.read_text()
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
    assert "style" not in exposed, "v2.14.1: style 已从 LLM schema 移除,使用配置默认"
    assert "indent" not in exposed, "v2.14.1: indent 已从 LLM schema 移除,使用配置默认"


def test_function_tool_uses_injected_defaults(tmp_path: Path, fake_astyle_run):
    """v2.14.1:CodeFormatTool 实例属性 default_style/default_indent 由 main.py 注入,
    call() 时透传给底层 format()。

    v2.15(2026-07-01)变更:验证 astyle.format() 调用时使用了注入的 style/indent。
    """
    from tools.function_tools.code_format import CodeFormatTool

    # 模拟 main.py 注入配置(从 _conf_schema.json 读出)
    tool = CodeFormatTool()
    tool.default_style = "google"  # 非默认值,验证注入生效
    tool.default_indent = 2  # 非默认值,验证注入生效

    # 构造一个 .cpp 文件 + 让 astyle mock 返回不同内容
    f = tmp_path / "test.cpp"
    f.write_text("int main(){return 0;}", encoding="utf-8")
    fake_astyle_run["state"]["formatted"] = "int main() {\n  return 0;\n}\n"

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


def test_function_tool_default_values_when_not_injected(
    tmp_path: Path, fake_astyle_run
):
    """未注入配置时,CodeFormatTool 用内置默认:allman + 4 空格。

    v2.15(2026-07-01)变更:验证 astyle.format() 调用时使用了默认 allman + 4 空格。
    """
    from tools.function_tools.code_format import CodeFormatTool

    tool = CodeFormatTool()  # 不注入
    f = tmp_path / "test.cpp"
    f.write_text("int main(){return 0;}", encoding="utf-8")
    fake_astyle_run["state"]["formatted"] = "int main()\n{\n    return 0;\n}\n"

    import asyncio
    import json as _json

    ctx = MagicMock()
    result_str = asyncio.run(tool.call(ctx, filepath=str(f)))
    payload = _json.loads(result_str)

    assert payload["ok"] is True
    astyle_call = fake_astyle_run["calls"][-1]
    cmd = astyle_call["cmd"]
    # CodeFormatTool 默认 default_style="linux",default_indent=4
    # (配置层可在 main.py 注入覆盖)
    assert "--style=linux" in cmd
    assert "--indent=spaces=4" in cmd
    assert payload["formatter_options"] == {"style": "linux", "indent": 4}


# ── 16-19. v2.15.1 (2026-07-01) 回滚:astyle 改回 subprocess.run 调用 astyle.exe ──
#
# WHY:
#   v2.15 错误地把 astyle 当作 Python 库用,但用户环境装的 astyle 是
#   Artistic Style 3.6.x 独立 C++ 二进制(``shutil.which("astyle")`` 找到
#   ``D:\anaconda3\Scripts\astyle.EXE``),不是 pip 可 import 的 Python 库。
#   必须改回 subprocess.run 命令行调用方式。
#
#   黑框防御链仍然保留:
#     - ``**_NO_WINDOW_KWARGS`` → 抑制父进程无 console 时 Windows 自动分配
#     - 显式 ``encoding="utf-8"`` + ``errors="replace"`` → 避免 cp936 解码失败
#     - ``shutil.which("astyle.exe")`` 优先 → 避免匹配 .bat / .cmd 触发 cmd.exe
#   Author: elecvoid243, 2026-07-01


# ── 16. astyle 不在 PATH → 友好错误 ─


def test_format_astyle_not_in_path_friendly(unformatted_cpp: Path, no_astyle):
    """shutil.which 找不到 astyle → ok=False + proposal + options。

    v2.15.1 行为变化:不再调 _find_astyle,而是直接 shutil.which。
    旧实现:no_astyle fixture 让 _find_astyle() 返回 []
    新实现:no_astyle fixture 让 ``shutil.which("astyle.exe")`` 返回 None
    两种路径下都得到 ok=False。
    """
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    assert "astyle" in r["error"].lower()
    assert "proposal" in r
    # 文件**未**被改写
    assert unformatted_cpp.read_text(encoding="utf-8").startswith("int main(){")


# ── 17. astyle subprocess.run 调用参数正确 ─────────


def test_format_astyle_passes_correct_args_to_subprocess(
    unformatted_cpp: Path, fake_astyle_run
):
    """subprocess.run 调用 astyle.exe 必须收到 --style 和 --indent 参数。

    v2.15.1 验证点:
      - cmd 第一个元素是 astyle.exe 路径
      - cmd 包含 ``--style=X`` 和 ``--indent=spaces=N``
      - kwargs 包含 ``creationflags``(CREATE_NO_WINDOW 防黑框)
    """
    fake_astyle_run["state"]["formatted"] = "FORMATTED"
    code_format.format(str(unformatted_cpp), style="google", indent=2)
    assert len(fake_astyle_run["calls"]) == 1
    call = fake_astyle_run["calls"][0]
    cmd = call["cmd"]
    # cmd 第 1 位是 astyle.exe 路径
    assert "astyle" in cmd[0].lower()
    # style + indent 参数都到位
    assert "--style=google" in cmd
    assert "--indent=spaces=2" in cmd
    # kwargs 包含 creationflags(win32 上抑制黑框)
    if sys.platform == "win32":
        assert "creationflags" in call["kwargs_keys"]


# ── 18. astyle subprocess.run 抛异常 → 捕获并返回 ok=False ─


def test_format_astyle_subprocess_exception_caught(
    unformatted_cpp: Path, fake_astyle_run
):
    """subprocess.run 抛任何异常 → ok=False + error 信息。"""
    fake_astyle_run["state"]["raise"] = RuntimeError("simulated astyle crash")
    r = code_format.format(str(unformatted_cpp))
    assert r["ok"] is False
    # 异常文本可能在 error 或 evidence 中,允许两种格式
    assert "astyle 调用失败" in r["error"] or "simulated astyle crash" in r["error"]
    # 文件**未**被改写
    assert unformatted_cpp.read_text(encoding="utf-8").startswith("int main(){")


# ── 20. (2026-07-31) CRLF 源文件不引入双空行 (bug 回归) ─────
#
# 报告:当 code_format 遇到 CRLF 时,会使得文件多出一个空行,每个 CRLF 在
# 格式化后变成了两行空行。
#
# 根因分析:
#   astyle.exe 的 stdin 模式按 \n 分行;如果直接把 "\r\n" 喂进 stdin,
#   astyle 把 "\r" 当成行内普通字符处理 → 在空行 ("\r\n\r\n") 场景下
#   会把 "\r\n\r\n" 识别成两行 ("}\r" + "\r"),输出时统一转回 \n 后
#   形成 "}\n\n\n"(原本的空行变成两个空行)。
#
# 修复 (_format_with_astyle):
#   在 stdin 喂给 astyle 之前把 CRLF/CR 归一为 LF;输出端也归一一次避免
#   astyle 输出混入 CRLF。这样 CRLF 源文件被改成 LF 文件但不出现双空行。
#
# Author: elecvoid243, 2026-07-31


def test_format_crlf_source_no_double_blank_lines(
    tmp_path: Path, fake_astyle_run, monkeypatch
):
    """CRLF 源文件格式不应产生双空行。

    复现链:
      1. 源文件 = "int main(){return 0;}\\r\\n\\r\\n"  (CRLF 行尾 + 1 个空行)
      2. 当前 (未修) 行为:把原文直接喂给 astyle stdin
         → astyle 把 "\r" 视为行内字符,把 "\r\n\r\n" 拆成 2 行 + 末尾多一个 \r
         → 输出 "int main(){return 0;}\\n\\n\\n" (LF,多一个空行)
      3. 修复后:stdin 之前归一为 LF (即 "int main(){return 0;}\\n\\n")
         → astyle 看到的就是正常的 1 个空行
         → 输出 "int main(){return 0;}\\n\\n" (仍然是 1 个空行)
    """
    # 1. 写一个 CRLF 源文件,内含 1 个空行
    f = tmp_path / "crlf.cpp"
    f.write_bytes(b"int main(){return 0;}\r\n\r\n")  # CRLF + LF-only blank line

    import tools.code_format as _cf

    # 2. mock subprocess.run:astyle 调用 → 模拟"CRLF stdin 产生双空行"bug
    #    关键:stdin 含 CRLF 时,把 \r\n → \n 后,在每个原空行(\n\n)处多注入一个 \n
    captured_stdin: list[str] = []

    def buggy_astyle_output(stdin_text: str) -> str:
        """模拟当前(未修)astyle + CRLF stdin 的行为:产生双空行。"""
        if "\r\n" in stdin_text:
            normalized = stdin_text.replace("\r\n", "\n")
            return normalized.replace("\n\n", "\n\n\n") + "\n"
        return stdin_text

    real_run = _cf.subprocess.run

    def adaptive_run(cmd, **kwargs):
        if cmd and "astyle" in str(cmd[0]).lower():
            captured_stdin.append(kwargs.get("input", ""))
            return _cf.subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=buggy_astyle_output(kwargs.get("input", "")),
                stderr="",
            )
        return real_run(cmd, **kwargs)

    # 覆盖 fake_astyle_run 已 patch 的 subprocess.run(用 monkeypatch 直接 set)
    monkeypatch.setattr(_cf.subprocess, "run", adaptive_run)

    # 3. 跑格式化
    r = code_format.format(str(f), style="allman", indent=4)

    # 4. 断言修复点
    assert r["ok"] is True
    assert len(captured_stdin) == 1, "astyle 应被调用 1 次"
    stdin_text = captured_stdin[0]
    assert "\r\n" not in stdin_text, (
        f"修复后 astyle stdin 不应再含 CRLF;实得={stdin_text!r}"
    )
    assert stdin_text == "int main(){return 0;}\n\n", (
        f"stdin 应为 CRLF→LF 归一化的文本;实得={stdin_text!r}"
    )

    #   - 修复后:写回的文件不含双空行
    after_bytes = f.read_bytes()
    # 把任何剩余 CRLF 也归一,再检测是否有 "\n\n\n"
    normalized = after_bytes.replace(b"\r\n", b"\n")
    assert b"\n\n\n" not in normalized, (
        f"修复后文件不应含双空行;实得={after_bytes!r}"
    )


def test_format_crlf_input_normalized_before_astyle(
    tmp_path: Path, fake_astyle_run, monkeypatch
):
    """CRLF/CR 输入在 astyle stdin 之前必须归一为 LF (单元级断言)。

    不依赖 mock 的"输出端双空行"模拟;只验证 _format_with_astyle
    传给 subprocess.run 的 input=... 不再含 \r。
    """
    # 写 CRLF 源文件
    f = tmp_path / "crlf2.cpp"
    original_crlf = b"int x=1;\r\nint y=2;\r\nif(x==y){\r\n  return 0;\r\n}\r\n"
    f.write_bytes(original_crlf)

    # 让 mock subprocess.run 捕获 input;不再干预 astyle 输出
    import tools.code_format as _cf

    captured: dict = {}

    def capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return _cf.subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=kwargs.get("input", ""), stderr=""
        )

    monkeypatch.setattr(_cf.subprocess, "run", capture_run)

    code_format.format(str(f), style="allman", indent=4)

    stdin_text = captured["input"]
    # 1. stdin 必须不含 \r (任何形式的 CR)
    assert "\r" not in stdin_text, (
        f"修复后 astyle stdin 应已 CR 归一化;实得={stdin_text!r}"
    )
    # 2. stdin 应等于把 CRLF → LF 的源文件
    expected = original_crlf.decode("utf-8").replace("\r\n", "\n")
    assert stdin_text == expected, f"stdin 应等于 CRLF→LF 后的源文本;实得={stdin_text!r}"


# ── 19. ruff 调用走 python -m,不走路径检测 ──────


def test_format_ruff_uses_python_m_directly(monkeypatch, unformatted_py: Path):
    """ruff 调用必须用 ``[sys.executable, "-m", "ruff", "format", ...]``。

    v2.15 验证点:
      - 没有任何 ``shutil.which`` / ``_find_ruff`` 调用
      - subprocess.run 第一参是 ``[sys.executable, "-m", "ruff", ...]``
    """
    # 拦截 subprocess.run 记录 args
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # 模拟 ruff 返回:不需改
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(code_format.subprocess, "run", fake_run)

    code_format.format(str(unformatted_py))

    cmd = captured["cmd"]
    # 首参必须是 python 解释器本身
    assert cmd[0] == sys.executable, f"应走 sys.executable,实际: {cmd[0]}"
    # 必须是 -m ruff (而不是裸 ruff 二进制)
    assert "-m" in cmd, f"应包含 -m 旗标,实际: {cmd}"
    assert "ruff" in cmd, f"应包含 ruff 模块名,实际: {cmd}"
    assert "format" in cmd, f"应调 ruff format 子命令,实际: {cmd}"
    # 绝不能再走裸 ruff 二进制(那种会被 PATHEXT 顶替)
    assert not any(part.endswith("ruff.exe") for part in cmd), (
        f"绝不能 spawn ruff.exe(会被 .bat wrapper 顶替): {cmd}"
    )


# ── 22. (2026-08-11) astyle 保持原文件编码 (v2.23.0) ──
#
# 报告:code_format 对 GBK/cp936 编码的 C/C++ 源文件格式化后,
# 会把文件强转为 UTF-8(写回固定 encoding="utf-8")。
# 修复:_format_with_astyle 复用 tools._helpers._decode_text_bytes
# 探测原编码,写回时保持原编码(GBK 仍 GBK、UTF-8 BOM 保留 BOM)。
#
# Author: elecvoid243, 2026-08-11


def test_format_astyle_preserves_gbk_encoding(tmp_path: Path, fake_astyle_run):
    """GBK 编码 .cpp 格式化后仍保持 GBK(不被 astyle 强转 UTF-8)。"""
    f = tmp_path / "gbk.cpp"
    f.write_bytes("// 中文注释\nint main(){return 0;}\n".encode("gbk"))
    formatted = "// 中文注释\nint main()\n{\n    return 0;\n}\n"
    fake_astyle_run["state"]["formatted"] = formatted

    r = code_format.format(str(f), style="allman", indent=4)
    assert r["ok"] is True
    assert r["changed"] is True

    raw = f.read_bytes()
    # 写回字节(容错 Windows write_text 的 \n → \r\n 转换)必须等于
    # astyle 输出按 GBK 编码的字节 —— 旧实现固定 UTF-8 时此断言失败
    assert raw.replace(b"\r\n", b"\n") == formatted.encode("gbk"), (
        f"文件应保持 GBK 编码;实得前 32 字节={raw[:32]!r}"
    )
    # 双重确认:内容可按 GBK 解码且含中文注释
    assert "中文注释" in raw.decode("gbk")


def test_format_astyle_preserves_utf8_bom(tmp_path: Path, fake_astyle_run):
    """UTF-8 BOM 文件格式化后仍保留 BOM。"""
    f = tmp_path / "bom.cpp"
    f.write_bytes(b"\xef\xbb\xbfint main(){return 0;}\n")
    formatted = "int main()\n{\n    return 0;\n}\n"
    fake_astyle_run["state"]["formatted"] = formatted

    r = code_format.format(str(f), style="allman", indent=4)
    assert r["ok"] is True
    assert r["changed"] is True

    raw = f.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM 丢失"
    assert raw.replace(b"\r\n", b"\n") == b"\xef\xbb\xbf" + formatted.encode(
        "utf-8"
    ), f"BOM + UTF-8 内容应原样保留;实得={raw[:40]!r}"


def test_format_astyle_preserves_utf8_no_bom(tmp_path: Path, fake_astyle_run):
    """普通 UTF-8(无 BOM)文件格式化后仍是 UTF-8 且不新增 BOM。"""
    f = tmp_path / "plain.cpp"
    f.write_text("int main(){return 0;}\n", encoding="utf-8")
    formatted = "int main()\n{\n    return 0;\n}\n"
    fake_astyle_run["state"]["formatted"] = formatted

    r = code_format.format(str(f), style="allman", indent=4)
    assert r["ok"] is True
    assert r["changed"] is True

    raw = f.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "不应新增 BOM"
    raw.replace(b"\r\n", b"\n").decode("utf-8")

