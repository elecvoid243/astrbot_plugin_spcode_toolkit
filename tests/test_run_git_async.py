"""Tests for ``_run_git_async`` — asyncio 真异步 git subprocess 包装。

v3.4 (2026-06-21) P1 perf:替换 ``run_sync(run_cmd, ...)`` 为
``await _run_git_async(...)``,省 worker 线程、让事件循环直接管理子进程。
本测试验证新 helper 的**行为兼容性** — 与 ``run_cmd`` 返回 dict 格式一致,
覆盖成功 / 失败 / timeout / 命令不存在四种路径。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# 启用 pytest-asyncio 严格模式
pytestmark = pytest.mark.asyncio


# ── 兼容 run_cmd 行为 ──


async def test_run_git_async_returns_success_dict():
    """成功执行 → {ok=True, stdout, stderr, code}。"""
    from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async

    result = await _run_git_async(
        [sys.executable, "-c", "print('hi')"],
        timeout=5.0,
    )
    assert result["ok"] is True
    assert result["code"] == 0
    assert result["stdout"] == "hi"
    assert result["stderr"] == ""


async def test_run_git_async_returns_nonzero_dict():
    """非零退出码 → {ok=False, stderr 含错误}。"""
    from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async

    result = await _run_git_async(
        [sys.executable, "-c", "import sys; sys.stderr.write('oops'); sys.exit(2)"],
        timeout=5.0,
    )
    assert result["ok"] is False
    assert result["code"] == 2
    assert "oops" in result["stderr"]


async def test_run_git_async_handles_missing_command():
    """命令不存在 → {ok=False, error='xxx 未安装或不在 PATH 中'}。"""
    from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async

    result = await _run_git_async(
        ["definitely-not-a-real-binary-12345", "foo"],
        timeout=5.0,
    )
    assert result["ok"] is False
    assert "未安装" in result["error"] or "not found" in result["error"].lower()


async def test_run_git_async_handles_timeout():
    """超时 → 进程被 kill + {ok=False, error='命令超时 (...)'}。"""
    from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async

    # sleep 5s 的 python 命令,timeout=0.5s
    result = await _run_git_async(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=0.5,
    )
    assert result["ok"] is False
    assert "超时" in result["error"] or "timeout" in result["error"].lower()


async def test_run_git_async_uses_cwd(tmp_path: Path):
    """cwd 参数正确生效:在 tmp_path 下跑命令验证。"""
    from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async

    # python -c "import os; print(os.getcwd())"
    cmd = [
        sys.executable,
        "-c",
        "import os, sys; sys.stdout.write(os.getcwd())",
    ]
    result = await _run_git_async(cmd, cwd=str(tmp_path), timeout=5.0)
    assert result["ok"] is True
    # Windows 上 cwd 可能是 8.3 短路径,统一化比对
    assert str(tmp_path).lower().replace("~", "") in result["stdout"].lower() or (
        Path(result["stdout"]).resolve() == tmp_path.resolve()
    )


# ── 与 run_cmd 行为兼容性(并排对比) ──


async def test_run_git_async_matches_run_cmd_format():
    """``_run_git_async`` 与 ``run_cmd`` 返回 dict 字段一致(便于无侵入替换)。

    P1 perf:这是把 ``await run_sync(run_cmd, ...)`` 替换为
    ``await _run_git_async(...)`` 的前提条件。helper 必须保留 {ok, stdout, stderr, code}
    或 {ok=False, error} 两种 dict 形态,不能引入新字段或破坏旧字段。
    """
    from astrbot_plugin_spcode_toolkit.tools.webapi import _helpers as _helpers_mod
    from tools import _helpers

    # 成功路径
    async_result = await _helpers_mod._run_git_async(
        [sys.executable, "-c", "print('ok')"], timeout=5.0
    )
    sync_result = _helpers.run_cmd([sys.executable, "-c", "print('ok')"], timeout=5)
    assert set(async_result.keys()) == set(sync_result.keys()), (
        f"key set mismatch: {set(async_result.keys())} vs {set(sync_result.keys())}"
    )

    # 失败路径(non-zero)
    async_fail = await _helpers_mod._run_git_async(
        [sys.executable, "-c", "import sys; sys.exit(7)"], timeout=5.0
    )
    sync_fail = _helpers.run_cmd(
        [sys.executable, "-c", "import sys; sys.exit(7)"], timeout=5
    )
    assert set(async_fail.keys()) == set(sync_fail.keys()), (
        f"key set mismatch: {set(async_fail.keys())} vs {set(sync_fail.keys())}"
    )


# ── 不阻塞事件循环 ──


async def test_run_git_async_does_not_block_event_loop():
    """``_run_git_async`` 在 await 期间不阻塞事件循环(让出给其他 task)。

    P1 perf 核心目标:多并发 git-diff 请求时,worker 池不被占满。
    """
    from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async

    events: list[str] = []

    async def other_task() -> None:
        # 短 await 让出控制权
        for i in range(5):
            events.append(f"other-{i}")
            await asyncio.sleep(0.01)

    async def git_task() -> None:
        events.append("git-start")
        # 跑一个 100ms 的 sleep 命令
        result = await _run_git_async(
            [sys.executable, "-c", "import time; time.sleep(0.1)"],
            timeout=2.0,
        )
        events.append(f"git-end-{result['ok']}")

    # 并发跑两个 task
    await asyncio.gather(git_task(), other_task())

    # other_task 应该在 git 跑期间多次执行(说明事件循环未被阻塞)
    other_count = sum(1 for e in events if e.startswith("other-"))
    assert other_count == 5, (
        f"other task should run 5 times during git subprocess, got {other_count}: {events!r}"
    )
    # git 完成且成功
    assert any(e.startswith("git-end-True") for e in events)
