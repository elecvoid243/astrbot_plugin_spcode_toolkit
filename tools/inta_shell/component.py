"""
本地交互式 Shell 组件实现。

基于 ``subprocess.Popen`` 的持久子进程实现，支持有状态的双向通信。

设计要点：
- 使用二进制管道 + 后台读取线程，避免管道死锁（pipe deadlock）。
- 每个会话由独立的 ``_LocalInteractiveSession`` 记录。
- 后台 ``_cleanup_loop`` 协程周期性地清理已退出会话与空闲超时会话。
- 收到读取线程上报的 EOF 时立即触发清理，避免等待 60s 周期。
- 启动时阻止高危命令（rm -rf、shutdown、mkfs 等），可通过构造参数调整。
- 跨平台兼容：Windows 下使用 CREATE_NO_WINDOW、UTF-8 代码页、UTF-8 解码并
  对替换字符回退到 GBK 系列编码。

Author: AstrBot Agent Harness Expert
Date: 2026-06-06
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

import psutil

from astrbot import logger


# ---------------------------------------------------------------------------
# 默认高危命令模式（不区分大小写、首尾补空格以避免 ``rm -rf /`` 误匹配）
# ---------------------------------------------------------------------------
DEFAULT_BLOCKED_COMMAND_PATTERNS: list[str] = [
    " rm -rf ",
    " rm -fr ",
    " rm -r ",
    " mkfs",
    " dd if=",
    " shutdown",
    " reboot",
    " poweroff",
    " halt",
    " sudo ",
    ":(){:|:&};:",
    " kill -9 ",
    " killall ",
]


def _build_blocked_patterns(
    base: list[str] | None,
    extra: list[str] | None,
) -> list[str]:
    """合并默认与用户自定义高危模式，统一首尾补空格。"""
    merged: list[str] = []
    for src in (base or DEFAULT_BLOCKED_COMMAND_PATTERNS, extra or []):
        for pat in src:
            stripped = pat.strip()
            if not stripped:
                continue
            merged.append(f" {stripped.lower()} ")
    # 去重
    return list(dict.fromkeys(merged))


def _is_safe_command(command: str, blocked: list[str]) -> bool:
    cmd = f" {command.strip().lower()} "
    return not any(pat in cmd for pat in blocked)


# ---------------------------------------------------------------------------
# 内部会话状态
# ---------------------------------------------------------------------------
@dataclass
class _LocalInteractiveSession:
    """内部会话状态（线程安全访问通过 ``lock`` 保护）。"""

    session_id: str
    command: str
    process: subprocess.Popen
    stdout_buffer: bytearray = field(default_factory=bytearray)
    stderr_buffer: bytearray = field(default_factory=bytearray)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_activity: float = field(default_factory=time.time)
    read_threads: list[threading.Thread] = field(default_factory=list)
    stop_reading: threading.Event = field(default_factory=threading.Event)
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# 组件主类
# ---------------------------------------------------------------------------
class LocalInteractiveShellComponent:
    """本地交互式 Shell 组件，基于 ``subprocess.Popen`` 维护持久进程。"""

    def __init__(
        self,
        *,
        max_sessions: int = 10,
        session_timeout_seconds: int = 1800,
        blocked_patterns: list[str] | None = None,
        blocked_patterns_extra: list[str] | None = None,
        enable_block: bool = True,
        shutdown_graceful_timeout: int = 3,
        shutdown_force_timeout: int = 2,
    ) -> None:
        self._sessions: dict[str, _LocalInteractiveSession] = {}
        self._session_lock = threading.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._eof_queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

        self._max_sessions = max(1, int(max_sessions))
        self._session_timeout_seconds = max(60, int(session_timeout_seconds))
        self._enable_block = bool(enable_block)
        self._blocked_patterns: list[str] = []
        if self._enable_block:
            self._blocked_patterns = _build_blocked_patterns(
                blocked_patterns,
                blocked_patterns_extra,
            )

        # 插件停用时清理所有活跃进程的两阶段超时
        self._shutdown_graceful_timeout = max(0, int(shutdown_graceful_timeout))
        self._shutdown_force_timeout = max(1, int(shutdown_force_timeout))

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def _ensure_cleanup_task(self) -> None:
        """确保周期清理任务已启动。"""
        if self._cleanup_task is None or self._cleanup_task.done():
            # 捕获运行中的事件循环，读取线程可以通过 call_soon_threadsafe
            # 安全地将 EOF 通知投递回异步侧。
            self._loop = asyncio.get_running_loop()
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def shutdown(self) -> dict[str, int]:
        """关闭组件：取消清理任务、并行终止所有活跃会话。

        对每个会话执行两阶段清理：
          Phase A: 发送 SIGINT (POSIX) / CTRL_C_EVENT (Windows)，
                   等待最多 ``shutdown_graceful_timeout`` 秒。
          Phase B: 若进程仍存活，递归 kill 进程树（父 + 全部子进程），
                   等待最多 ``shutdown_force_timeout`` 秒。

        全部会话**并行**处理，单个失败不影响其他。

        Returns:
            汇总统计 dict: ``{"total", "graceful", "forced", "failed"}``。
        """
        # 1. 取消后台清理任务
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except (asyncio.CancelledError, Exception):
                pass
            self._cleanup_task = None

        # 2. 快照当前活跃会话（在锁内）
        with self._session_lock:
            active = list(self._sessions.items())

        if not active:
            logger.info("[InteractiveShell] Shutdown: no active sessions to terminate.")
            return {
                "total": 0,
                "graceful": 0,
                "forced": 0,
                "skipped": 0,
                "failed": 0,
            }

        logger.info(
            "[InteractiveShell] Shutdown: terminating %d active session(s) "
            "(graceful_timeout=%ds, force_timeout=%ds)...",
            len(active),
            self._shutdown_graceful_timeout,
            self._shutdown_force_timeout,
        )

        # 3. 并行清理所有会话
        tasks = [self._shutdown_one(sid, session) for sid, session in active]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. 汇总结果
        summary = {"total": 0, "graceful": 0, "forced": 0, "failed": 0}
        for r in results:
            if isinstance(r, BaseException):
                summary["failed"] += 1
                logger.warning(
                    "[InteractiveShell] Shutdown task raised: %s: %s",
                    type(r).__name__,
                    r,
                )
            else:
                # r 是 "graceful" / "forced" / "skipped" 之一
                summary["total"] += 1
                summary[r] = summary.get(r, 0) + 1

        # 5. 清空 _sessions（在锁内）
        with self._session_lock:
            self._sessions.clear()

        logger.info(
            "[InteractiveShell] Shutdown complete: %s",
            summary,
        )
        return summary

    async def _shutdown_one(
        self,
        session_id: str,
        session: "_LocalInteractiveSession",
    ) -> str:
        """清理单个会话：先优雅退出，超时后强制 kill 进程树。

        Returns:
            阶段标签: ``"skipped"``（进程已退出）/
            ``"graceful"``（SIGINT 后干净退出）/
            ``"forced"``（Phase B 强制 kill 成功）。
        """
        proc = session.process

        # 先让 reader 线程停止 read()，避免 join 时阻塞
        session.stop_reading.set()

        try:
            # ---- 进程已自然退出 ----
            if proc.poll() is not None:
                logger.info(
                    "[InteractiveShell] Session %s (pid=%d) already exited (code=%s).",
                    session_id,
                    proc.pid,
                    proc.returncode,
                )
                self._close_pipes(proc)
                self._join_threads(session)
                return "skipped"

            # ---- Phase A: 优雅退出 ----
            if self._shutdown_graceful_timeout > 0:
                logger.info(
                    "[InteractiveShell] Session %s (pid=%d) sending interrupt, "
                    "waiting up to %ds for graceful exit...",
                    session_id,
                    proc.pid,
                    self._shutdown_graceful_timeout,
                )
                self._send_interrupt(proc)
                try:
                    exit_code = proc.wait(timeout=self._shutdown_graceful_timeout)
                    logger.info(
                        "[InteractiveShell] Session %s (pid=%d) exited gracefully "
                        "(code=%s).",
                        session_id,
                        proc.pid,
                        exit_code,
                    )
                    self._close_pipes(proc)
                    self._join_threads(session)
                    return "graceful"
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "[InteractiveShell] Session %s (pid=%d) did not exit within "
                        "%ds, escalating to force kill.",
                        session_id,
                        proc.pid,
                        self._shutdown_graceful_timeout,
                    )
            else:
                logger.info(
                    "[InteractiveShell] Session %s (pid=%d) graceful timeout = 0, "
                    "skipping to force kill.",
                    session_id,
                    proc.pid,
                )

            # ---- Phase B: 强制 kill 进程树 ----
            killed_children = self._kill_process_tree(proc)
            try:
                proc.wait(timeout=self._shutdown_force_timeout)
            except subprocess.TimeoutExpired:
                logger.error(
                    "[InteractiveShell] Session %s (pid=%d) STILL ALIVE after "
                    "force kill + %ds wait! Process is unresponsive.",
                    session_id,
                    proc.pid,
                    self._shutdown_force_timeout,
                )

            if killed_children > 0:
                logger.info(
                    "[InteractiveShell] Session %s (pid=%d) force killed "
                    "(also terminated %d child process(es)).",
                    session_id,
                    proc.pid,
                    killed_children,
                )
            else:
                logger.info(
                    "[InteractiveShell] Session %s (pid=%d) force killed.",
                    session_id,
                    proc.pid,
                )

            self._close_pipes(proc)
            self._join_threads(session)
            return "forced"

        except BaseException as exc:  # noqa: BLE001
            # 任何未预期异常：尽力清理资源，不抛出
            logger.exception(
                "[InteractiveShell] Unexpected error shutting down session %s: %s",
                session_id,
                exc,
            )
            try:
                self._close_pipes(proc)
            except Exception:
                pass
            try:
                self._join_threads(session)
            except Exception:
                pass
            return "failed"

    @staticmethod
    def _send_interrupt(proc: subprocess.Popen) -> None:
        """发送平台特定的"中断"信号。

        - POSIX: ``SIGINT``（Ctrl+C 语义）
        - Windows: ``CTRL_C_EVENT``（仅对创建了 console 的子进程有效）
        """
        try:
            if sys.platform == "win32":
                proc.send_signal(subprocess.signal.CTRL_C_EVENT)
            else:
                proc.send_signal(subprocess.signal.SIGINT)
        except (ValueError, OSError, ProcessLookupError) as exc:
            # ValueError: 子进程已结束；OSError/ProcessLookupError: 进程不存在
            logger.debug("[InteractiveShell] send_interrupt ignored: %s", exc)

    @staticmethod
    def _kill_process_tree(proc: subprocess.Popen) -> int:
        """递归 kill 父进程及其所有子进程。先子后父。

        Returns:
            实际被 kill 的子进程数（不含父进程本身）。
        """
        killed = 0
        try:
            parent = psutil.Process(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0

        # 先递归拿到所有后代
        try:
            children = parent.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            children = []

        # 先 kill 叶子，再往上
        for child in reversed(children):
            try:
                child.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[InteractiveShell] Failed to kill child pid=%d: %s",
                    child.pid,
                    exc,
                )

        # 最后 kill 父进程
        try:
            parent.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[InteractiveShell] Failed to kill parent pid=%d: %s",
                parent.pid,
                exc,
            )

        return killed

    @staticmethod
    def _close_pipes(proc: subprocess.Popen) -> None:
        """关闭 stdin / stdout / stderr 管道，吞掉所有异常。"""
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is None:
                continue
            try:
                pipe.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[InteractiveShell] pipe.close() failed: %s", exc)

    @staticmethod
    def _join_threads(session: "_LocalInteractiveSession") -> None:
        """join 所有后台 reader 线程，每个最多等 1s。"""
        for t in session.read_threads:
            if not t.is_alive():
                continue
            try:
                t.join(timeout=1.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[InteractiveShell] thread.join() failed: %s", exc)

    # ------------------------------------------------------------------
    # 清理任务
    # ------------------------------------------------------------------
    async def _cleanup_loop(self) -> None:
        """周期清理任务。

        - 优先响应读取线程的 EOF 信号（60s 超时回退）。
        - 每次循环都额外执行一次定期扫描，及时回收已退出/空闲会话。
        """
        while True:
            try:
                session_id = await asyncio.wait_for(self._eof_queue.get(), timeout=60)
                self._cleanup_session_by_id(session_id)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.warning("[InteractiveShell] Cleanup error: %s", e)
                continue

            try:
                self._cleanup_terminated()
                self._cleanup_idle_sessions()
            except Exception as e:  # noqa: BLE001
                logger.warning("[InteractiveShell] Cleanup error: %s", e)

    def _cleanup_session_by_id(self, session_id: str) -> None:
        """根据 session_id 清理指定会话（EOF 信号触发）。"""
        session: _LocalInteractiveSession | None
        with self._session_lock:
            session = self._sessions.get(session_id)
        if session is None:
            return

        session.stop_reading.set()
        for t in session.read_threads:
            if t.is_alive():
                t.join(timeout=1.0)

        for pipe in [
            session.process.stdin,
            session.process.stdout,
            session.process.stderr,
        ]:
            if pipe:
                try:
                    pipe.close()
                except Exception:
                    pass

        with self._session_lock:
            removed = self._sessions.pop(session_id, None)
        if removed is not None:
            logger.info(
                "[InteractiveShell] Cleaned up terminated session: %s", session_id
            )

    def _cleanup_terminated(self) -> None:
        """回收已退出进程的会话。"""
        to_remove: list[tuple[str, _LocalInteractiveSession]] = []
        with self._session_lock:
            for session_id, session in self._sessions.items():
                if session.process.poll() is not None:
                    to_remove.append((session_id, session))

        for _, session in to_remove:
            session.stop_reading.set()
            for t in session.read_threads:
                if t.is_alive():
                    t.join(timeout=1.0)

        with self._session_lock:
            for session_id, _ in to_remove:
                self._sessions.pop(session_id, None)
                logger.info(
                    "[InteractiveShell] Cleaned up terminated session: %s",
                    session_id,
                )

    def _cleanup_idle_sessions(self) -> None:
        """回收空闲时间超过阈值的会话。"""
        now = time.time()
        to_remove: list[tuple[str, _LocalInteractiveSession]] = []
        with self._session_lock:
            for session_id, session in self._sessions.items():
                if session.process.poll() is None:  # 仍在运行
                    idle_time = now - session.last_activity
                    if idle_time > self._session_timeout_seconds:
                        to_remove.append((session_id, session))

        for session_id, session in to_remove:
            logger.warning(
                "[InteractiveShell] Session %s idle for %.0fs, forcing termination",
                session_id,
                self._session_timeout_seconds,
            )
            session.stop_reading.set()
            try:
                session.process.kill()
                session.process.wait(timeout=2.0)
            except Exception:
                pass
            for t in session.read_threads:
                if t.is_alive():
                    t.join(timeout=1.0)

        with self._session_lock:
            for session_id, _ in to_remove:
                self._sessions.pop(session_id, None)

    # ------------------------------------------------------------------
    # 读取线程
    # ------------------------------------------------------------------
    def _start_reader_threads(self, session: _LocalInteractiveSession) -> None:
        """启动后台线程持续读取子进程输出（二进制模式）。"""

        def _read_stream(stream, is_stderr: bool) -> None:
            eof_reached = False
            try:
                while not session.stop_reading.is_set():
                    chunk = stream.read(4096)
                    if not chunk:
                        eof_reached = True
                        break
                    with session.lock:
                        if is_stderr:
                            session.stderr_buffer.extend(chunk)
                        else:
                            session.stdout_buffer.extend(chunk)
                        session.last_activity = time.time()
            except Exception:
                pass
            finally:
                if eof_reached:
                    loop = self._loop
                    if loop is not None and not loop.is_closed():
                        try:
                            loop.call_soon_threadsafe(
                                self._eof_queue.put_nowait, session.session_id
                            )
                        except RuntimeError:
                            # 事件循环在检测与投递之间被关闭，忽略。
                            pass

        if session.process.stdout:
            t = threading.Thread(
                target=_read_stream,
                args=(session.process.stdout, False),
                daemon=True,
            )
            t.start()
            session.read_threads.append(t)

        if session.process.stderr:
            t = threading.Thread(
                target=_read_stream,
                args=(session.process.stderr, True),
                daemon=True,
            )
            t.start()
            session.read_threads.append(t)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    async def start(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        shell: bool = True,
    ):
        # 延迟导入避免循环依赖
        from .session_models import InteractiveSession, InteractiveSessionState

        if self._enable_block and not _is_safe_command(command, self._blocked_patterns):
            raise PermissionError(
                "Blocked unsafe shell command. If this is a legitimate use, "
                "configure 'block_unsafe_commands=false' in the plugin config."
            )

        await self._ensure_cleanup_task()

        def _start() -> _LocalInteractiveSession:
            with self._session_lock:
                if len(self._sessions) >= self._max_sessions:
                    raise RuntimeError(
                        f"Maximum number of interactive sessions "
                        f"({self._max_sessions}) reached. "
                        "Please stop some sessions before starting new ones."
                    )

            run_env = os.environ.copy()
            if env:
                run_env.update({str(k): str(v) for k, v in env.items()})
            working_dir = os.path.abspath(cwd) if cwd else os.path.abspath(os.getcwd())

            if sys.platform == "win32":
                run_env["PYTHONIOENCODING"] = "utf-8"

            popen_kwargs: dict = {
                "shell": shell,
                "cwd": working_dir,
                "env": run_env,
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "bufsize": 0,
            }

            actual_command = command
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                )
                if shell and actual_command.strip().lower().startswith("cmd"):
                    actual_command = f"chcp 65001 >nul && {actual_command}"
            #
            proc = subprocess.Popen(actual_command, **popen_kwargs)

            session_id = str(uuid.uuid4())[:8]
            session = _LocalInteractiveSession(
                session_id=session_id,
                command=command,
                process=proc,
            )
            self._start_reader_threads(session)
            return session

        session = await asyncio.to_thread(_start)

        with self._session_lock:
            self._sessions[session.session_id] = session

        logger.info(
            "[InteractiveShell] Started session %s (pid=%d): %s",
            session.session_id,
            session.process.pid,
            command,
        )

        # 等待进程初始化
        await asyncio.sleep(0.3)

        return InteractiveSession(
            session_id=session.session_id,
            command=command,
            pid=session.process.pid,
            state=InteractiveSessionState.RUNNING,
            created_at=session.created_at,
            last_activity=session.last_activity,
        )

    async def send(
        self,
        session_id: str,
        input_data: str,
        send_eof: bool = False,
    ) -> None:
        def _send() -> None:
            session = self._get_session(session_id)
            if session.process.stdin is None:
                raise RuntimeError("Session stdin is not available")
            if session.process.poll() is not None:
                raise RuntimeError("Session process has already exited")

            data = input_data.encode("utf-8", errors="replace")
            if not input_data.endswith("\n"):
                data += b"\n"

            session.process.stdin.write(data)
            session.process.stdin.flush()
            session.last_activity = time.time()

            if send_eof:
                session.process.stdin.close()

        await asyncio.to_thread(_send)

    async def read(
        self,
        session_id: str,
        timeout: float = 5.0,
        max_chars: int | None = None,
    ) -> str:
        def _read() -> str:
            session = self._get_session(session_id)
            deadline = time.time() + timeout
            result_parts: list[str] = []
            chars_collected = 0
            has_data = False

            while time.time() < deadline:
                stdout_chunk = b""
                stderr_chunk = b""

                with session.lock:
                    if session.stdout_buffer:
                        stdout_chunk = bytes(session.stdout_buffer)
                        session.stdout_buffer.clear()
                    if session.stderr_buffer:
                        stderr_chunk = bytes(session.stderr_buffer)
                        session.stderr_buffer.clear()

                chunks = [(stdout_chunk, False), (stderr_chunk, True)]
                for chunk, is_stderr in chunks:
                    if not chunk:
                        continue

                    text = chunk.decode("utf-8", errors="replace")

                    # Windows 下 UTF-8 全部变为替换字符时回退到 GBK 系列
                    if sys.platform == "win32" and "\ufffd" in text and len(text) > 1:
                        for fallback_encoding in (
                            "gbk",
                            "gb18030",
                            "cp936",
                        ):
                            try:
                                fallback_text = chunk.decode(fallback_encoding)
                                if "\ufffd" not in fallback_text:
                                    text = fallback_text
                                    break
                            except (UnicodeDecodeError, LookupError):
                                continue

                    if max_chars and chars_collected + len(text) > max_chars:
                        take = max_chars - chars_collected
                        result_parts.append(text[:take])
                        overflow = text[take:].encode("utf-8", errors="replace")
                        with session.lock:
                            if is_stderr:
                                session.stderr_buffer[:0] = overflow
                            else:
                                session.stdout_buffer[:0] = overflow
                        chars_collected += take
                        has_data = True
                        break

                    result_parts.append(text)
                    chars_collected += len(text)
                    has_data = True

                if has_data:
                    grace_end = time.time() + 0.15
                    while time.time() < grace_end:
                        with session.lock:
                            if session.stdout_buffer or session.stderr_buffer:
                                break
                        time.sleep(0.03)
                    if time.time() >= grace_end:
                        break
                    continue

                time.sleep(0.05)

            return "".join(result_parts)

        return await asyncio.to_thread(_read)

    async def terminate(self, session_id: str, graceful: bool = True):
        from .session_models import InteractiveSession, InteractiveSessionState

        def _terminate():
            session = self._get_session(session_id)
            proc = session.process

            session.stop_reading.set()

            if proc.poll() is not None:
                exit_code = proc.returncode
            else:
                if graceful:
                    if sys.platform == "win32":
                        try:
                            proc.send_signal(subprocess.signal.CTRL_C_EVENT)
                        except (ValueError, OSError):
                            pass
                    else:
                        try:
                            proc.send_signal(subprocess.signal.SIGINT)
                        except (ValueError, OSError):
                            pass

                    try:
                        exit_code = proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        exit_code = None
                else:
                    exit_code = None

                if proc.poll() is None:
                    proc.kill()
                    try:
                        exit_code = proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        exit_code = None
                    if proc.poll() is None:
                        exit_code = -9

            for pipe in [proc.stdin, proc.stdout, proc.stderr]:
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            for t in session.read_threads:
                if t.is_alive():
                    t.join(timeout=1.0)

            with self._session_lock:
                self._sessions.pop(session_id, None)

            logger.info(
                "[InteractiveShell] Terminated session %s (exit_code=%s)",
                session_id,
                exit_code,
            )

            return InteractiveSession(
                session_id=session_id,
                command=session.command,
                pid=proc.pid,
                state=InteractiveSessionState.TERMINATED,
                exit_code=exit_code,
                created_at=session.created_at,
                last_activity=session.last_activity,
            )

        return await asyncio.to_thread(_terminate)

    async def get_session(self, session_id: str):
        from .session_models import InteractiveSession, InteractiveSessionState

        def _get():
            with self._session_lock:
                session = self._sessions.get(session_id)
                if session is None:
                    return None

                proc = session.process
                poll_result = proc.poll()
                if poll_result is not None:
                    state = InteractiveSessionState.TERMINATED
                    exit_code = poll_result
                else:
                    state = InteractiveSessionState.RUNNING
                    exit_code = None

                return InteractiveSession(
                    session_id=session_id,
                    command=session.command,
                    pid=proc.pid,
                    state=state,
                    exit_code=exit_code,
                    created_at=session.created_at,
                    last_activity=session.last_activity,
                )

        return await asyncio.to_thread(_get)

    async def list_sessions(self) -> list:
        from .session_models import InteractiveSession, InteractiveSessionState

        def _list() -> list:
            result: list[InteractiveSession] = []
            with self._session_lock:
                for session_id, session in self._sessions.items():
                    proc = session.process
                    poll_result = proc.poll()
                    if poll_result is not None:
                        state = InteractiveSessionState.TERMINATED
                        exit_code = poll_result
                    else:
                        state = InteractiveSessionState.RUNNING
                        exit_code = None

                    result.append(
                        InteractiveSession(
                            session_id=session_id,
                            command=session.command,
                            pid=proc.pid,
                            state=state,
                            exit_code=exit_code,
                            created_at=session.created_at,
                            last_activity=session.last_activity,
                        )
                    )
            return result

        return await asyncio.to_thread(_list)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _get_session(self, session_id: str) -> _LocalInteractiveSession:
        with self._session_lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Interactive session not found: {session_id}")
        return session
