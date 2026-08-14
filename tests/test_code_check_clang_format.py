"""tools/code_check.py 的 clang-format 格式检查测试(2026-08-14,cpplint → clang-format)。

Spec: docs/superpowers/specs/2026-08-14-clang-format-unify-design.md

覆盖:
  - 显式 linter="clang-format":已格式化 / 未格式化 / 未安装 / 语法错误
  - 显式 linter="cpplint":不再是合法值
  - auto 模式 .cpp:cppcheck 不可用 → clang-format 兜底;短路逻辑不变
  - merge 模式(shortcircuit=never):linters.clang_format 块结构
  - 风格配置链:模块级覆盖 CLANG_FORMAT_STYLE / CLANG_FORMAT_INDENT
  - 参数链与 code_format 同源(--style=file + --fallback-style)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import code_check  # noqa: E402


@pytest.fixture(autouse=True)
def clear_clang_format_overrides():
    """每个用例前后清理模块级 CLANG_FORMAT_STYLE / CLANG_FORMAT_INDENT 覆盖。"""
    saved = {}
    for name in ("CLANG_FORMAT_STYLE", "CLANG_FORMAT_INDENT"):
        if hasattr(code_check, name):
            saved[name] = getattr(code_check, name)
            delattr(code_check, name)
    yield
    for name in ("CLANG_FORMAT_STYLE", "CLANG_FORMAT_INDENT"):
        if hasattr(code_check, name):
            delattr(code_check, name)
    for name, value in saved.items():
        setattr(code_check, name, value)


@pytest.fixture
def cpp_file(tmp_path: Path) -> Path:
    f = tmp_path / "a.cpp"
    f.write_text("int main(){return 0;}\n", encoding="utf-8")
    return f


@pytest.fixture
def fake_clang_format(monkeypatch):
    """mock clang-format:_find_clang_format → fake 路径;subprocess.run 可控。

    state["stdout"]: 返回的 stdout bytes(默认 = stdin 原文 → 0 issues)
    state["returncode"]: 默认 0
    """
    calls: list[dict] = []
    state = {"stdout": None, "stderr": b"", "returncode": 0}

    monkeypatch.setattr(code_check, "_find_clang_format", lambda: ["fake-cf"])
    # auto 模式会先跑 cppcheck;默认让其不可用,专注 clang-format 路径
    monkeypatch.setattr(code_check, "_find_cppcheck", lambda: [])

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": list(cmd), "input": kwargs.get("input")})
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=state["returncode"],
            stdout=(
                state["stdout"]
                if state["stdout"] is not None
                else kwargs.get("input", b"")
            ),
            stderr=state["stderr"],
        )

    monkeypatch.setattr(code_check.subprocess, "run", fake_run)
    return {"calls": calls, "state": state}


# ── 显式 linter="clang-format" ──


def test_explicit_clang_format_clean_file(cpp_file: Path, fake_clang_format):
    """已格式化文件(stdout==stdin)→ count=0。"""
    r = code_check.check(str(cpp_file), "clang-format")
    assert r["ok"] is True
    assert r["linter"] == "clang-format"
    assert r["count"] == 0
    assert r["issues"] == []


def test_explicit_clang_format_unformatted_file(cpp_file: Path, fake_clang_format):
    """未格式化文件 → per-line format issues + 首条带 context。"""
    fake_clang_format["state"]["stdout"] = b"int main() {\n    return 0;\n}\n"
    r = code_check.check(str(cpp_file), "clang-format")
    assert r["ok"] is True
    assert r["count"] >= 1
    issue = r["issues"][0]
    assert issue["category"] == "format"
    assert issue["line"] == 1
    assert "clang-format" in issue["message"]
    # _add_context 为前 5 条附加源码上下文
    assert "context" in issue
    assert r["proposal"]
    assert "code_format" in r["options"][0]


def test_explicit_clang_format_not_installed(cpp_file: Path, monkeypatch):
    """clang-format 未安装 → ok=False + pip install 提示。"""
    monkeypatch.setattr(code_check, "_find_clang_format", lambda: [])
    r = code_check.check(str(cpp_file), "clang-format")
    assert r["ok"] is False
    assert r["error"] == "clang-format 未安装"
    assert "pip install clang-format" in r["proposal"]


def test_explicit_clang_format_syntax_error(cpp_file: Path, fake_clang_format):
    """clang-format 非零退出(解析失败)→ category=syntax 单条 issue,不中断。"""
    fake_clang_format["state"]["returncode"] = 1
    fake_clang_format["state"]["stderr"] = b"error: expected ';' after expression"
    r = code_check.check(str(cpp_file), "clang-format")
    assert r["ok"] is True
    assert r["count"] == 1
    assert r["issues"][0]["category"] == "syntax"
    assert "expected" in r["issues"][0]["message"]


def test_explicit_cpplint_rejected(cpp_file: Path):
    """linter='cpplint' 不再是合法值 → ok=False + supported 列表。"""
    r = code_check.check(str(cpp_file), "cpplint")
    assert r["ok"] is False
    assert "cpplint" not in r["supported"]
    assert "clang-format" in r["supported"]


# ── auto 模式 ──


def test_auto_cpp_falls_through_to_clang_format(cpp_file: Path, fake_clang_format):
    """auto + cppcheck 不可用 → 走 clang-format 格式检查(merge 输出)。"""
    r = code_check.check(str(cpp_file), "auto")
    assert r["ok"] is True
    assert r["linter"] == "merge"
    assert "clang_format" in r["linters"]
    assert r["linters"]["clang_format"]["available"] is True
    assert r["linters"]["cppcheck"]["available"] is False


def test_auto_cpp_shortcircuit_skips_clang_format(cpp_file: Path, monkeypatch):
    """cppcheck 报 error → 短路,不调用 clang-format。"""
    monkeypatch.setattr(code_check, "_find_cppcheck", lambda: ["fake-cppcheck"])
    clang_called = []

    monkeypatch.setattr(code_check, "_find_clang_format", lambda: ["fake-cf"])

    def fake_run(cmd, **kwargs):
        cmd0 = str(cmd[0])
        if "fake-cf" in cmd0:
            clang_called.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        # cppcheck:报 1 条 error
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="",
            stderr=f"{cpp_file}:1:5: error: bad thing  [badThing]\n",
        )

    monkeypatch.setattr(code_check.subprocess, "run", fake_run)
    r = code_check.check(str(cpp_file), "auto", cppcheck_shortcircuit="error")
    assert r["linter"] == "cppcheck"
    assert r["count"] == 1
    assert clang_called == [], "cppcheck 短路时不应调用 clang-format"


def test_auto_cpp_merge_mode_block_structure(cpp_file: Path, monkeypatch):
    """shortcircuit=never → merge 模式,linters 含 clang_format 块。"""
    monkeypatch.setattr(code_check, "_find_cppcheck", lambda: ["fake-cppcheck"])
    monkeypatch.setattr(code_check, "_find_clang_format", lambda: ["fake-cf"])

    def fake_run(cmd, **kwargs):
        cmd0 = str(cmd[0])
        if "fake-cf" in cmd0:
            # clang-format:原文未格式化 → 1 处差异
            return subprocess.CompletedProcess(
                cmd, 0, stdout=b"int main() {\n    return 0;\n}\n", stderr=b""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(code_check.subprocess, "run", fake_run)
    r = code_check.check(str(cpp_file), "auto", cppcheck_shortcircuit="never")
    assert r["ok"] is True
    assert r["linter"] == "merge"
    cf_block = r["linters"]["clang_format"]
    assert cf_block["available"] is True
    assert cf_block["count"] >= 1
    assert cf_block["issues"][0]["_linter"] == "clang-format"
    assert r["linters"]["cppcheck"]["count"] == 0


# ── 配置链与参数链 ──


def test_clang_format_uses_same_flags_as_code_format(cpp_file: Path, fake_clang_format):
    """code_check 的 clang-format 参数链必须与 code_format 同源。"""
    code_check.CLANG_FORMAT_STYLE = "google"
    code_check.CLANG_FORMAT_INDENT = 2
    code_check.check(str(cpp_file), "clang-format")
    cmd = fake_clang_format["calls"][0]["cmd"]
    assert f"--assume-filename={cpp_file}" in cmd
    # tmp_path 无 .clang-format → 内联 style,与 code_format 同源
    style_arg = next(a for a in cmd if a.startswith("--style="))
    assert style_arg == "--style={BasedOnStyle: google, IndentWidth: 2}"


def test_clang_format_module_override_beats_env(
    cpp_file: Path, fake_clang_format, monkeypatch
):
    """模块级覆盖 CLANG_FORMAT_STYLE 优先于环境变量。"""
    monkeypatch.setenv("CLANG_FORMAT_STYLE", "webkit")
    code_check.CLANG_FORMAT_STYLE = "gnu"
    code_check.check(str(cpp_file), "clang-format")
    cmd = fake_clang_format["calls"][0]["cmd"]
    style_arg = next(a for a in cmd if a.startswith("--style="))
    assert "BasedOnStyle: gnu" in style_arg


def test_clang_format_env_style_used(cpp_file: Path, fake_clang_format, monkeypatch):
    """无模块级覆盖时读环境变量 CLANG_FORMAT_STYLE / CLANG_FORMAT_INDENT。"""
    monkeypatch.setenv("CLANG_FORMAT_STYLE", "chromium")
    monkeypatch.setenv("CLANG_FORMAT_INDENT", "8")
    code_check.check(str(cpp_file), "clang-format")
    cmd = fake_clang_format["calls"][0]["cmd"]
    style_arg = next(a for a in cmd if a.startswith("--style="))
    assert "BasedOnStyle: chromium" in style_arg
    assert "IndentWidth: 8" in style_arg


def test_clang_format_stdin_is_binary(cpp_file: Path, fake_clang_format):
    """stdin 必须是 bytes(二进制管道,编码保真)。"""
    code_check.check(str(cpp_file), "clang-format")
    assert isinstance(fake_clang_format["calls"][0]["input"], bytes)
    assert fake_clang_format["calls"][0]["input"] == cpp_file.read_bytes()
