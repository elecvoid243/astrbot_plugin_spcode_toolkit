"""cppcheck_enable 配置测试 (v2.21.1+)

覆盖:
  1. _get_cppcheck_enable: 优先级 / 不合法值 / 去重
  2. _run_cppcheck: 命令行参数根据配置正确变化
  3. 默认行为: 不传 --enable 旗标 (只报 error)
  4. 与 _get_shortcircuit_mode 互不干扰

Author: elecvoid243
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


# ── 工具: 清理跨测试状态 ─────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_cppcheck_enable_state(monkeypatch):
    """每个测试前后清理模块级覆盖 / 环境变量 / DEFAULT_CONFIG。

    WHY: 三个优先级源(模块 globals / env / DEFAULT_CONFIG)跨测试会污染,
    必须在 setup/teardown 全部清空,确保单测可重复。
    """
    from tools import code_check

    saved_module = getattr(code_check, "CPPCHECK_ENABLE", None)
    if hasattr(code_check, "CPPCHECK_ENABLE"):
        delattr(code_check, "CPPCHECK_ENABLE")
    saved_env = os.environ.pop("CPPCHECK_ENABLE", None)

    from tools import _config

    saved_default = _config.DEFAULT_CONFIG.get("cppcheck_enable", [])
    _config.DEFAULT_CONFIG["cppcheck_enable"] = []

    yield

    # teardown
    if hasattr(code_check, "CPPCHECK_ENABLE"):
        delattr(code_check, "CPPCHECK_ENABLE")
    if saved_module is not None:
        code_check.CPPCHECK_ENABLE = saved_module
    if saved_env is not None:
        os.environ["CPPCHECK_ENABLE"] = saved_env
    _config.DEFAULT_CONFIG["cppcheck_enable"] = saved_default


def _run_cppcheck_capture_args(code_check, fake_cppcheck_path: str = "fake-cppcheck.exe"):
    """调 _run_cppcheck 并返回 (captured_args, result)。

    WHY: 测试只关心命令构造,不关心 cppcheck 真实结果。
    用 patch 同时挡掉 _find_cppcheck 与 subprocess.run。
    """
    fake_file = Path(__file__)  # 任何已存在文件即可
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0] if args else kwargs.get("args")
        return subprocess.CompletedProcess(
            args=captured["args"] or [],
            returncode=0,
            stdout="",
            stderr="",  # 故意空,让 _parse_cppcheck_output 返 0 issue
        )

    with patch.object(code_check, "_find_cppcheck", return_value=[fake_cppcheck_path]):
        with patch.object(code_check.subprocess, "run", side_effect=fake_run):
            result = code_check._run_cppcheck(fake_file)

    return captured.get("args") or [], result


# ── 1. _get_cppcheck_enable 优先级 / 净化 / 去重 ──────────


def test_get_cppcheck_enable_default_empty():
    """默认: 三源全空 → 返 [] → 不传 --enable。"""
    from tools import code_check

    assert code_check._get_cppcheck_enable() == []


def test_get_cppcheck_enable_module_override_priority():
    """模块级覆盖优先于 env / DEFAULT_CONFIG。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning"]
    os.environ["CPPCHECK_ENABLE"] = "style,performance"
    from tools import _config

    _config.DEFAULT_CONFIG["cppcheck_enable"] = ["portability"]

    assert code_check._get_cppcheck_enable() == ["warning"]


def test_get_cppcheck_enable_env_var():
    """无模块覆盖时,环境变量优先于 DEFAULT_CONFIG (逗号分隔)。"""
    from tools import code_check

    os.environ["CPPCHECK_ENABLE"] = "warning,performance"
    from tools import _config

    _config.DEFAULT_CONFIG["cppcheck_enable"] = ["style"]

    assert code_check._get_cppcheck_enable() == ["warning", "performance"]


def test_get_cppcheck_enable_default_config_fallback():
    """全空时,fallback 到 DEFAULT_CONFIG['cppcheck_enable']。"""
    from tools import code_check

    from tools import _config

    _config.DEFAULT_CONFIG["cppcheck_enable"] = ["warning", "style"]

    assert code_check._get_cppcheck_enable() == ["warning", "style"]


def test_get_cppcheck_enable_drops_invalid_values():
    """不合法值被静默丢弃,合法值保留。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning", "bogus", "style", "", 123, None]

    result = code_check._get_cppcheck_enable()
    assert result == ["warning", "style"]


def test_get_cppcheck_enable_dedup():
    """重复类目去重,首次出现顺序保留。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning", "style", "warning", "performance", "style"]

    result = code_check._get_cppcheck_enable()
    assert result == ["warning", "style", "performance"]


def test_get_cppcheck_enable_env_dedup_and_trim():
    """环境变量: 逗号分隔 / 空白 / 重复都要正确处理。"""
    from tools import code_check

    os.environ["CPPCHECK_ENABLE"] = " warning , style ,warning , bogus "

    result = code_check._get_cppcheck_enable()
    assert result == ["warning", "style"]


# ── 2. _run_cppcheck 命令行构造 ──────────────────────


def test_run_cppcheck_default_omits_enable_flag():
    """默认(空 enable)→ 命令行不含 --enable → cppcheck 仅报 error。"""
    from tools import code_check

    args, result = _run_cppcheck_capture_args(code_check)

    assert "--enable" not in " ".join(args), f"默认应不传 --enable,实际: {args!r}"
    assert "--quiet" in args
    assert "--inline-suppr" in args
    # file 路径应作为最后一个参数(但前面允许有 --enable 时会插到中间)
    assert str(Path(__file__)) in args
    assert result["ok"] is True
    assert result["linter"] == "cppcheck"


def test_run_cppcheck_single_category():
    """单选 → --enable=warning。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning"]

    args, _ = _run_cppcheck_capture_args(code_check)

    assert "--enable=warning" in args
    assert "--quiet" in args
    assert "--inline-suppr" in args


def test_run_cppcheck_multiple_categories():
    """多选 → --enable=warning,style (逗号拼接)。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning", "style"]

    args, _ = _run_cppcheck_capture_args(code_check)

    assert "--enable=warning,style" in args


def test_run_cppcheck_all_four_categories_preserves_legacy_behavior():
    """全 4 项 → 与 v2.21 旧行为一致(向后兼容路径)。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning", "style", "performance", "portability"]

    args, _ = _run_cppcheck_capture_args(code_check)

    assert "--enable=warning,style,performance,portability" in args


def test_run_cppcheck_enable_position():
    """--enable 应在 --quiet / --inline-suppr 之前(旗标放前面)。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning"]

    args, _ = _run_cppcheck_capture_args(code_check)

    enable_idx = args.index("--enable=warning")
    quiet_idx = args.index("--quiet")
    inline_idx = args.index("--inline-suppr")
    file_idx = next(
        i for i, a in enumerate(args) if a.endswith("test_cppcheck_enable_config.py")
    )
    # file 必须是最后一个
    assert file_idx == len(args) - 1
    # --enable 在 --quiet / --inline-suppr / file 之前
    assert enable_idx < quiet_idx < inline_idx < file_idx


# ── 3. 与 shortcircuit 互不干扰 ──────────────────────


def test_shortcircuit_still_works_with_enable_config():
    """配置 cppcheck_enable 后,shortcircuit 模式行为不受影响。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning"]
    code_check.SHORTCIRCUIT_MODE = "warning"

    # 模拟 cppcheck 报一条 warning
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args[0] if args else kwargs.get("args")
        return subprocess.CompletedProcess(
            args=captured["args"] or [],
            returncode=0,
            stdout="",
            stderr="src/foo.cpp:6:12:  warning: Something  [someId]\n",
        )

    fake_file = Path(__file__)
    with patch.object(code_check, "_find_cppcheck", return_value=["fake-cppcheck"]):
        with patch.object(code_check.subprocess, "run", side_effect=fake_run):
            result = code_check._run_cppcheck(fake_file)

    # 验证 _run_cppcheck 自身仍是 pure (不应用 shortcircuit 过滤)
    assert "--enable=warning" in captured["args"]
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["issues"][0]["severity"] == "warning"


# ── 4. 集成: 整体不破坏 _parse_cppcheck_output ──────────


def test_run_cppcheck_with_enable_parses_all_severities():
    """即使传了 --enable=warning, _parse_cppcheck_output 仍能解析多个 severity
    (本测试确保 _parse_cppcheck_output 不变)。"""
    from tools import code_check

    code_check.CPPCHECK_ENABLE = ["warning", "style"]

    fake_file = Path(__file__)
    stderr = (
        "src/foo.cpp:1:1:  error: bad  [errId]\n"
        "src/foo.cpp:2:2:  warning: warn  [warnId]\n"
        "src/foo.cpp:3:3:  style: sty  [styleId]\n"
        "src/foo.cpp:4:4:  performance: perf  [perfId]\n"
        "src/foo.cpp:5:5:  portability: port  [portId]\n"
        "src/foo.cpp:6:6:  information: info  [infoId]\n"  # 噪声, 应被过滤
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0] if args else kwargs.get("args", []),
            returncode=0,
            stdout="",
            stderr=stderr,
        )

    with patch.object(code_check, "_find_cppcheck", return_value=["fake-cppcheck"]):
        with patch.object(code_check.subprocess, "run", side_effect=fake_run):
            result = code_check._run_cppcheck(fake_file)

    # information 应被 _parse_cppcheck_output 过滤
    severities = {i["severity"] for i in result["issues"]}
    assert "information" not in severities
    assert {"error", "warning", "style", "performance", "portability"} == severities
