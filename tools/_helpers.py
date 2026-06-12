"""
spcode_toolkit 共享辅助函数。
提供 subprocess 封装、JSON 错误包装、提案协议响应、异步桥接。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any


def run_cmd(
    cmd_args: list[str],
    cwd: str = "",
    timeout: int = 15,
    encoding: str = "utf-8",
) -> dict:
    """统一 subprocess.run 封装。

    返回:
        ok=True  → {"ok": True, "stdout": str, "stderr": str, "code": int}
        ok=False → {"ok": False, "error": str}
    """
    if not cwd:
        cwd = "."
    try:
        result = subprocess.run(
            cmd_args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding=encoding,
            errors="replace",
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "code": result.returncode,
        }
    except FileNotFoundError:
        cmd_name = cmd_args[0] if cmd_args else "command"
        return {"ok": False, "error": f"{cmd_name} 未安装或不在 PATH 中"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"命令超时 ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def err_json(error: str) -> str:
    """生成错误 JSON 字符串。"""
    return json.dumps({"ok": False, "error": error}, ensure_ascii=False)


def unwrap(result: Any) -> str:
    """把工具函数返回的 dict 序列化为 JSON 字符串，供 FunctionTool.call 返回。

    - 含 proposal/options/evidence/next_call 协议字段 → 直接透传
    - 纯 ok:false → 包装为简洁错误
    - 纯 ok:true → 包装进 data 字段
    """
    if not isinstance(result, dict):
        return err_json(f"工具返回了非预期类型: {type(result).__name__}")
    if any(k in result for k in ("proposal", "options", "evidence", "next_call")):
        return json.dumps(result, ensure_ascii=False)
    if result.get("ok") is False:
        return err_json(result.get("error", "未知错误"))
    return json.dumps({"ok": True, "data": result}, ensure_ascii=False)


async def run_sync(func, *args, **kwargs):
    """在默认线程池中运行同步函数，避免阻塞 AstrBot 事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def proposal_reply(
    ok: bool,
    proposal: str,
    *,
    error: str = "",
    evidence: dict | None = None,
    options: list | None = None,
    next_call: dict | None = None,
    **extra,
) -> dict:
    """构建统一提案协议返回，供 LLM 在歧义/失败场景下推理。"""
    result: dict = {"ok": ok, "proposal": proposal, **extra}
    if error:
        result["error"] = error
    if evidence:
        result["evidence"] = evidence
    if options:
        result["options"] = options
    if next_call:
        result["next_call"] = next_call
    return result
