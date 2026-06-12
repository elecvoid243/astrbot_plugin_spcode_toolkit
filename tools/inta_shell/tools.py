"""
interactive_shell LLM 工具逻辑(v2.5 集成入 spcode)。

提供 5 个纯 async 函数供 main.py 的 FunctionTool.call() 委托：
- start: 启动交互式 Shell 会话
- send: 向活跃会话发送输入
- read: 从活跃会话读取输出
- stop: 终止会话
- list_sessions: 列出所有活跃会话

注意：admin 权限校验已由 spcode 的 _auth_guard 统一处理，
这里不再重复 check_admin_permission。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from astrbot import logger

from .component import LocalInteractiveShellComponent
from .paths import workspace_root
from .session_models import session_to_dict


def _deny(reason: str) -> str:
    return json.dumps({"success": False, "error": reason}, ensure_ascii=False)


def _ok(**fields: Any) -> str:
    payload = {"success": True}
    payload.update(fields)
    return json.dumps(payload, ensure_ascii=False)


async def start(
    component: LocalInteractiveShellComponent,
    umo: str,
    command: str,
    env: dict | None = None,
    default_cwd: str = "",
) -> str:
    """启动一个交互式 Shell 会话。"""
    try:
        custom_cwd = default_cwd
        if custom_cwd and os.path.isdir(custom_cwd):
            cwd = custom_cwd
        else:
            try:
                ws = workspace_root(umo)
                ws.mkdir(parents=True, exist_ok=True)
                cwd = str(ws)
            except Exception as e:
                logger.warning(
                    "[inta_shell] Failed to resolve workspace root, fallback to cwd: %s",
                    e,
                )
                cwd = os.getcwd()

        env_dict = {str(k): str(v) for k, v in (env or {}).items()}
        session = await component.start(command, cwd=cwd, env=env_dict)

        await asyncio.sleep(0.3)
        initial_output = await component.read(session.session_id, timeout=2.0)

        return _ok(
            session=session_to_dict(session),
            initial_output=initial_output,
            hint=(
                "Session started. Use astrbot_inta_shell_send/read to interact, "
                "or astrbot_inta_shell_stop to terminate."
            ),
        )
    except PermissionError as e:
        return _deny(str(e))
    except Exception as e:
        logger.exception("[inta_shell] start failed")
        return _deny(f"Failed to start interactive shell: {e}")


async def send(
    component: LocalInteractiveShellComponent,
    session_id: str,
    text: str,
    send_eof: bool = False,
) -> str:
    """向一个活跃的交互式 Shell 会话发送输入。"""
    try:
        await component.send(session_id, text, send_eof=send_eof)
        return _ok(message="Input sent successfully.")
    except ValueError as e:
        return _deny(f"Session not found: {e}")
    except Exception as e:
        logger.exception("[inta_shell] send failed")
        return _deny(f"Failed to send input: {e}")


async def read(
    component: LocalInteractiveShellComponent,
    session_id: str,
    timeout: float = 5.0,
    max_chars: int = 4096,
) -> str:
    """从活跃的交互式 Shell 会话读取输出。"""
    try:
        output = await component.read(session_id, timeout=timeout, max_chars=max_chars)
        session = await component.get_session(session_id)
        state_info = session_to_dict(session) if session else None
        return _ok(
            output=output,
            session=state_info,
            hint=(
                "Analyze the output to determine if the program is: "
                "(1) waiting for input (shows a prompt), "
                "(2) still processing (no prompt yet), or "
                "(3) has finished (exited)."
            ),
        )
    except ValueError as e:
        return _deny(f"Session not found: {e}")
    except Exception as e:
        logger.exception("[inta_shell] read failed")
        return _deny(f"Failed to read output: {e}")


async def stop(
    component: LocalInteractiveShellComponent,
    session_id: str,
    force: bool = False,
) -> str:
    """终止一个交互式 Shell 会话。"""
    try:
        session = await component.terminate(session_id, graceful=not force)
        return _ok(
            session=session_to_dict(session),
            message="Session terminated.",
        )
    except ValueError as e:
        return _deny(f"Session not found: {e}")
    except Exception as e:
        logger.exception("[inta_shell] stop failed")
        return _deny(f"Failed to terminate session: {e}")


async def list_sessions(component: LocalInteractiveShellComponent) -> str:
    """列出所有活跃的交互式 Shell 会话。"""
    try:
        sessions = await component.list_sessions()
        return _ok(
            sessions=[session_to_dict(s) for s in sessions],
            count=len(sessions),
        )
    except Exception as e:
        logger.exception("[inta_shell] list failed")
        return _deny(f"Failed to list sessions: {e}")
