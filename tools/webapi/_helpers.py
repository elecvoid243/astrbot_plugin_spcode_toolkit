"""Shared helpers for tools/webapi/* endpoints.

Only imported by webapi/* handler modules. Do NOT import from main.py
(circular dependency) or tools/_helpers.py (cross-layer coupling).
"""
from __future__ import annotations
import asyncio
from typing import Any


# 从 main.py line 80-136 整体迁移,行为不变。
# 行为兼容 ``run_cmd`` 的返回 dict 格式,便于无侵入替换 ``run_sync(run_cmd, ...)``:
#   - 成功: {ok: True, stdout: str, stderr: str, code: int}
#   - 失败: {ok: False, error: str}(命令不存在 / 超时)
#   - 非零退出: {ok: False, stdout, stderr, code}(run_cmd 兼容)
async def _run_git_async(
    cmd_args: list[str],
    cwd: str = "",
    timeout: float = 15.0,
    encoding: str = "utf-8",
) -> dict:
    """Asyncio 真异步版本的 ``run_cmd``,用于 git 调用。

    Args:
        cmd_args: 子进程参数列表(首元素为可执行文件名)。
        cwd: 工作目录;空串则不设(用当前进程 cwd)。
        timeout: 子进程超时(秒);超时自动 kill 进程。
        encoding: stdout/stderr 解码用。

    Returns:
        与 ``run_cmd`` 兼容的 dict。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
        )
    except FileNotFoundError:
        cmd_name = cmd_args[0] if cmd_args else "command"
        return {"ok": False, "error": f"{cmd_name} 未安装或不在 PATH 中"}

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return {"ok": False, "error": f"命令超时 ({timeout}s)"}

    return {
        "ok": proc.returncode == 0,
        # 只去尾部换行(``, ``),**不**用 ``.strip()``:后者会把
        # ``git status --porcelain`` 第一列的 `` ``(X=未暂存)误删,
        # 导致下游解析 `` M`` → ``M`` 后误判为已暂存(v3.6 file-restore bug)。
        "stdout": stdout_bytes.decode(encoding, errors="replace").rstrip("\r\n"),
        "stderr": stderr_bytes.decode(encoding, errors="replace").rstrip("\r\n"),
        "code": proc.returncode,
    }
