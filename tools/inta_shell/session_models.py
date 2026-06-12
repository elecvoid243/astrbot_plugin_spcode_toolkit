"""
交互式 Shell 会话数据模型。

提供与 AstrBot 内核解耦的会话状态与会话信息数据类，独立成包以便插件复用。

Author: AstrBot Agent Harness Expert
Date: 2026-06-06
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InteractiveSessionState(Enum):
    """交互式 Shell 会话状态。"""

    RUNNING = "running"
    """进程正在运行，等待输入或产生输出。"""

    WAITING_INPUT = "waiting_input"
    """进程看起来在等待用户输入（检测到 prompt）。"""

    OUTPUT_READY = "output_ready"
    """有输出可供读取。"""

    TERMINATED = "terminated"
    """进程已退出。"""

    ERROR = "error"
    """会话发生错误。"""


@dataclass
class InteractiveSession:
    """表示一个活跃的交互式 Shell 会话。"""

    session_id: str
    """会话唯一标识。"""

    command: str
    """启动该会话的原始命令。"""

    pid: int
    """运行中的 shell 进程 PID。"""

    state: InteractiveSessionState
    """会话当前状态。"""

    exit_code: int | None = None
    """若进程已退出，则为退出码；否则为 None。"""

    error_message: str | None = None
    """若状态为 ERROR，则为错误信息。"""

    created_at: float | None = None
    """会话创建时间戳（time.time()）。"""

    last_activity: float | None = None
    """会话最后一次 send/read 操作的时间戳。"""


def session_to_dict(session: InteractiveSession) -> dict:
    """将会话对象转换为可 JSON 序列化的字典。"""
    return {
        "session_id": session.session_id,
        "command": session.command,
        "pid": session.pid,
        "state": session.state.value,
        "exit_code": session.exit_code,
        "error_message": session.error_message,
        "created_at": session.created_at,
        "last_activity": session.last_activity,
    }
