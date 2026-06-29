"""tests/test_no_window_kwargs.py — 守护 _NO_WINDOW_KWARGS 跨平台契约。

WHY:
    AstrBot 主程序可能用 pythonw.exe(GUI subsystem,无控制台)启动;此时
    Windows 内核会自动为每个 CUI 子进程分配一个新的控制台窗口。修复需
    在 spawn 时显式传入 ``creationflags=CREATE_NO_WINDOW``。

    本测试把 helper 的跨平台契约锁死——后续如有人重构这个常量,平台分支
    错乱会被立即捕获。

Author: elecvoid243, 2026-06-29
"""
from __future__ import annotations

import subprocess
import sys

import pytest


# 同时覆盖 tools/_helpers.py 与 tools/webapi/_helpers.py 两处的副本。
@pytest.fixture(
    params=[
        ("tools._helpers", "_NO_WINDOW_KWARGS"),
        ("tools.webapi._helpers", "_NO_WINDOW_KWARGS"),
    ],
    ids=["tools._helpers", "tools.webapi._helpers"],
)
def no_window_kwargs_pair(request):
    """按参数化导入两个模块的 _NO_WINDOW_KWARGS 常量,逐个验证契约。"""
    import importlib

    module_name, attr = request.param
    mod = importlib.import_module(module_name)
    return getattr(mod, attr), module_name, attr


def test_no_window_kwargs_is_dict(no_window_kwargs_pair):
    """helper 必须是 dict(允许空 dict,非 Windows 上 `**{}` 是 no-op)。"""
    kw, mod_name, attr = no_window_kwargs_pair
    assert isinstance(kw, dict), f"{mod_name}.{attr} 不是 dict"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="非 Windows 平台契约;Windows 平台看下一个 test",
)
def test_no_window_kwargs_empty_on_non_windows(no_window_kwargs_pair):
    """非 Windows 平台必须返回 {} — 不应带 CREATE_NO_WINDOW(否则会 AttributeError)。"""
    kw, mod_name, _ = no_window_kwargs_pair
    assert kw == {}, f"{mod_name} 非 Windows 上应为空 dict,实际={kw!r}"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows 平台契约",
)
def test_no_window_kwargs_has_create_no_window_on_windows(no_window_kwargs_pair):
    """Windows 平台必须带 creationflags=CREATE_NO_WINDOW。"""
    kw, mod_name, _ = no_window_kwargs_pair
    assert "creationflags" in kw, f"{mod_name} Windows 上缺 creationflags"
    assert kw["creationflags"] == subprocess.CREATE_NO_WINDOW, (
        f"{mod_name} creationflags != CREATE_NO_WINDOW (实际={kw['creationflags']})"
    )


def test_two_copies_are_consistent():
    """tools._helpers 与 tools.webapi._helpers 应给出**等价**的 kwarg 集合。

    WHY: 这两个模块有独立副本(避免 webapi 跨层依赖 tools._helpers),
    若其中一个 drift(例如一人加 win32 平台,另一人忘加),会导致行为
    不一致。test 把这个不变量固化下来。
    """
    from tools._helpers import _NO_WINDOW_KWARGS as a
    from tools.webapi._helpers import _NO_WINDOW_KWARGS as b

    # 比较 keys + values(值是 int 常量,==即可)
    assert set(a.keys()) == set(b.keys()), (
        f"副本 keys 不一致: tools._helpers={set(a.keys())} "
        f"vs tools.webapi._helpers={set(b.keys())}"
    )
    for k in a:
        assert a[k] == b[k], f"副本 {k} 值不一致"


def test_subprocess_create_no_window_constant_available():
    """烟雾: subprocess.CREATE_NO_WINDOW 必须存在(win32) 或 用 hasattr 验证
    非 win32 平台可用性语义正确(此处只用 hasattr 模拟;真正断言在上面的
    平台分支 test 中)。
    """
    # 这个 test 是为了让 CI 至少 import 一次 subprocess,不会 AttributeError
    assert hasattr(subprocess, "CREATE_NO_WINDOW") == (sys.platform == "win32"), (
        f"CREATE_NO_WINDOW 可用性({hasattr(subprocess, 'CREATE_NO_WINDOW')})"
        f"与平台({sys.platform})不匹配"
    )


# ── 集成测试:模拟 spawn 调用,验证 helper 实际进入 kwargs ───────────


def test_helper_expands_into_subprocess_run_kwargs(monkeypatch):
    """模拟一次 spawn,确认 _NO_WINDOW_KWARGS 展开后实际带 creationflags 进 kwargs。"""
    captured: list[dict] = []

    class _FakeCP:
        returncode = 0
        stdout = b""
        stderr = b""

    def fake_run(*args, **kwargs):
        captured.append(kwargs)
        return _FakeCP()

    monkeypatch.setattr(subprocess, "run", fake_run)

    # 直接展开常量,模拟 spawn 调用
    from tools._helpers import _NO_WINDOW_KWARGS

    subprocess.run(["git", "--version"], capture_output=True, **_NO_WINDOW_KWARGS)

    assert len(captured) == 1
    kwargs = captured[0]
    if sys.platform == "win32":
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW
    else:
        # 非 Windows 上 helper 是空 dict
        assert "creationflags" not in kwargs


@pytest.mark.asyncio
async def test_helper_expands_into_create_subprocess_exec(monkeypatch):
    """异步路径:helper 展开后实际带 creationflags 进 asyncio.create_subprocess_exec kwargs。"""
    captured: list[dict] = []

    class _FakeProc:
        returncode = 0

    async def fake_exec(*args, **kwargs):
        captured.append(kwargs)
        return _FakeProc()

    import asyncio

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    from tools.webapi._helpers import _NO_WINDOW_KWARGS

    await asyncio.create_subprocess_exec(
        "git", "--version", **_NO_WINDOW_KWARGS
    )

    assert len(captured) == 1
    kwargs = captured[0]
    if sys.platform == "win32":
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW
    else:
        assert "creationflags" not in kwargs


# ── 守卫测试:扫描整个 production 代码禁止出现"未加 kwarg 的 spawn" ──────
#
# WHY: 用户要求"插件内提供的一切 subprocess 相关的都加 creationflag"。
# 本测试把这条规则固化下来:任何人(包括未来 PR)添加新的 spawn 站点但
# 忘记加 kwarg,这条 test 就会失败。


_PRODUCTION_SPAWN_PATTERNS = [
    r"subprocess\.(run|Popen|check_call|check_output|call)\s*\(",
    r"asyncio\.create_subprocess_(exec|shell)\s*\(",
    r"os\.(system|popen)\s*\(",
]


def _is_production_spawn(site_text: str) -> bool:
    """Filter: spawn 调用点(非 import / 注释 / docstring / type hint)。"""
    s = site_text.lstrip()
    if s.startswith("#"):
        return False
    if s.startswith("import") or s.startswith("from"):
        return False
    if "::" in s or "->" in s:  # 类型注解可能含 subprocess
        return False
    # run_cmd(...) — central wrapper, 内部已加,调用方无需展开
    return True


def test_run_cmd_internally_passes_no_window(monkeypatch):
    """中央 wrapper run_cmd 内部的 subprocess.run 必须带 creationflag。

    WHY: run_cmd 被 es_search / git_worktrees 等多个工具调用。修了它
    等于自动覆盖这些间接 caller,所以本测试是该路径的"链头"守卫。
    """
    captured: list[dict] = []

    import subprocess as _sp
    real_run = _sp.run

    def fake_run(*args, **kwargs):
        captured.append(kwargs)
        # 仍然调真实的 run 避免测试 break
        return real_run(*args, **kwargs)

    monkeypatch.setattr(_sp, "run", fake_run)
    # 也 patch code_check.py 等模块引用到的 subprocess.run
    import tools.code_check
    import tools.code_format

    monkeypatch.setattr(tools.code_check.subprocess, "run", fake_run)
    monkeypatch.setattr(tools.code_format.subprocess, "run", fake_run)

    from tools._helpers import run_cmd

    # 触发 run_cmd 真实一次
    proc = run_cmd(
        [sys.executable, "-c", "print('hello from run_cmd')"], timeout=5
    )

    assert proc["ok"], f"run_cmd 真实调用应成功,实际={proc}"
    assert len(captured) >= 1, "fake_run 未被调用"
    kwargs = captured[0]

    if sys.platform == "win32":
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW, (
            f"run_cmd 内部 subprocess.run 缺少 CREATE_NO_WINDOW: {kwargs}"
        )
    else:
        assert "creationflags" not in kwargs


def test_no_production_subprocess_call_without_no_window():
    """扫描整个 tools/ + main.py,任何 spawn 都必须带 _NO_WINDOW_KWARGS
    或位于 run_cmd / inta_shell Popen 的中央 wrapper 内部。

    WHY: 这是"全量覆盖"规则的实现。规则来自用户:
        "插件内提供的一切 subprocess 相关的都请加上 creationflag"

    实现在 tools._helpers.run_cmd / tools.inta_shell.component 中:
    - ``run_cmd`` 在 tools._helpers 内拼了 ``**_NO_WINDOW_KWARGS``
    - ``subprocess.Popen`` 在 tools.inta_shell.component.py:632 显式设置
      ``creationflags=CREATE_NO_WINDOW``,这两个调用点视为"间接受保护"

    此 test 用 AST 扫描源文件找 spawn 调用;不对应的就 fail,
    提示修复者加上 ``**_NO_WINDOW_KWARGS`` 或迁到 wrapper。
    """
    import ast
    from pathlib import Path

    ROOT = Path(".")
    WRAPPER_FILES = {
        Path("tools/_helpers.py").resolve(),  # run_cmd
        Path("tools/inta_shell/component.py").resolve(),  # Popen (component.py:632)
    }

    spawn_calls = []  # list of (file, lineno, text)
    for py in ROOT.rglob("*.py"):
        # 排除 tests / data / docs / 缓存
        if any(part in py.parts for part in {"tests", "data", "docs", ".git", "__pycache__", ".codegraph", ".pytest_cache", ".ruff_cache"}):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 处理 subprocess.run / subprocess.Popen / asyncio.create_subprocess_exec 等
            qualified = None
            if isinstance(func, ast.Attribute):
                qualified_parts = []
                cur = func
                while isinstance(cur, ast.Attribute):
                    qualified_parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    qualified_parts.append(cur.id)
                qualified_parts.reverse()
                qualified = ".".join(qualified_parts)
            if qualified not in {
                "subprocess.run", "subprocess.Popen", "subprocess.check_call",
                "subprocess.check_output", "subprocess.call",
                "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
                "os.system", "os.popen",
            }:
                continue

            # 检查附近源码(子进程调用通常在 5-15 行内展开 kwargs)
            src = py.read_text(encoding="utf-8")
            lines = src.splitlines()
            window_start = max(0, node.lineno - 1)
            window_end = min(len(lines), node.lineno + 20)  # 20 行窗口覆盖 kwargs 块
            window_text = "\n".join(lines[window_start:window_end])

            # 小写不敏感:匹配 _NO_WINDOW_KWARGS / _no_window
            # 也匹配显式写死的 creationflags=(CREATE_NO_WINDOW ...)(
            # inta_shell/component.py 不在此 wrapper 列表,显式条件单独处理)
            has_no_window = (
                "no_window" in window_text.lower()
                or ("creationflags" in window_text and "CREATE_NO_WINDOW" in window_text)
            )
            in_wrapper = py.resolve() in WRAPPER_FILES

            spawn_calls.append((
                py, node.lineno, qualified, has_no_window, in_wrapper
            ))

    # 找出违规:既不在 wrapper 内、附近 12 行也无 _NO_WINDOW_KWARGS
    violations = [
        (f, ln, qual) for (f, ln, qual, has_no, in_wrap) in spawn_calls
        if not (has_no or in_wrap)
    ]
    if violations:
        msg_lines = [
            "发现了未加 CREATE_NO_WINDOW 的 production subprocess 调用:"
        ]
        for f, ln, qual in violations:
            msg_lines.append(f"  {f}:{ln}  {qual}(...)")
        msg_lines.append("")
        msg_lines.append("修复:加 `**_NO_WINDOW_KWARGS,` (import 自 tools._helpers)")
        msg_lines.append("或迁到 run_cmd() 中央 wrapper。")
        pytest.fail("\n".join(msg_lines))
