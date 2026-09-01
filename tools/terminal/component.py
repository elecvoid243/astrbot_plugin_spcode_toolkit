"""Terminal session manager for the WebUI terminal page.

Design mirrors the proven architecture of AstrBot core's
``LocalShellComponent`` (asyncio-native subprocess, on-disk output
append with cursor-based incremental reads, event-driven long polling)
but scoped to a persistent interactive shell (cmd.exe / PowerShell)
owned by one ``umo`` (single session per owner).

Author: elecvoid243 · 2026-09-01
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astrbot.core.utils.astrbot_path import get_astrbot_system_tmp_path

from .decode import decode_bytes_with_fallback

SHELL_KINDS = ("powershell", "cmd")


@dataclass
class _TerminalSession:
    """Runtime state for one managed terminal shell process."""

    session_id: str
    owner_id: str  # umo — the session belongs to exactly one conversation
    shell_kind: str
    process: asyncio.subprocess.Process
    output_path: Path
    started_at: float
    output_event: asyncio.Event
    reader_task: asyncio.Task[None]
    wait_task: asyncio.Task[int]
    cursor: int = 0
    terminated: bool = False


class TerminalSessionManager:
    """Manages one persistent interactive shell session per ``umo``."""

    def __init__(self) -> None:
        self._sessions: dict[str, _TerminalSession] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def start(
        self,
        *,
        owner_id: str,
        shell: str = "powershell",
        cwd: str,
        exe_override: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start (or replace) the owner's persistent terminal session.

        Args:
            owner_id: Unified message origin owning the session.
            shell: ``"powershell"`` (default) or ``"cmd"``.
            cwd: Absolute working directory for the shell.
            exe_override: Test hook — replaces the argv entirely.

        Returns:
            Session info dict: ``{session_id, pid, shell, cwd, status}``.

        Raises:
            ValueError: Unsupported shell kind or missing working dir.
        """
        if shell not in SHELL_KINDS:
            raise ValueError(f"Unsupported shell: {shell!r}")
        working_dir = Path(cwd).resolve()
        if not working_dir.is_dir():
            raise ValueError(f"Working directory does not exist: {working_dir}")

        existing = await self.owner_session(owner_id)
        if existing is not None:
            await self.terminate(owner_id=owner_id, session_id=existing["session_id"])

        argv = self._resolve_argv(shell, exe_override)
        session_id = f"term_{uuid.uuid4().hex[:12]}"
        owner_digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16]
        output_dir = Path(get_astrbot_system_tmp_path()) / "terminal" / owner_digest
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{session_id}.log"
        output_path.touch()

        run_env = os.environ.copy()
        run_env["PYTHONIOENCODING"] = "utf-8"
        run_env["PYTHONUTF8"] = "1"

        process_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP keeps CTRL_BREAK_EVENT (interrupt)
            # working by giving the child its own process group.
            # CREATE_NO_WINDOW must NOT be used here — it drops the
            # console entirely and breaks that interrupt path (same
            # reasoning as AstrBot core local.py).
            process_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE
            process_kwargs["startupinfo"] = startupinfo
        else:
            process_kwargs["start_new_session"] = True

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(working_dir),
                env=run_env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **process_kwargs,
            )
        except Exception:
            output_path.unlink(missing_ok=True)
            raise

        output_event = asyncio.Event()

        async def _capture_output() -> None:
            if process.stdout is None:
                return
            with output_path.open("ab") as output_file:
                while chunk := await process.stdout.read(8192):
                    output_file.write(chunk)
                    output_file.flush()
                    output_event.set()

        reader_task = asyncio.create_task(
            _capture_output(), name=f"terminal_output_{session_id}"
        )
        wait_task = asyncio.create_task(
            process.wait(), name=f"terminal_wait_{session_id}"
        )
        wait_task.add_done_callback(lambda _: output_event.set())

        session = _TerminalSession(
            session_id=session_id,
            owner_id=owner_id,
            shell_kind=shell,
            process=process,
            output_path=output_path,
            started_at=time.time(),
            output_event=output_event,
            reader_task=reader_task,
            wait_task=wait_task,
        )
        async with self._lock:
            self._sessions[session_id] = session
        # Give the child process a beat to attach its stdin pipe on
        # Windows (Proactor loop): stdin writes issued immediately after
        # spawn can be dropped before the child starts reading. Same
        # 0.3s convention as AstrBot core's managed shell boot.
        await asyncio.sleep(0.3)
        return {
            "session_id": session_id,
            "pid": process.pid,
            "shell": shell,
            "cwd": str(working_dir),
            "status": "running",
        }

    @staticmethod
    def _resolve_argv(shell: str, exe_override: list[str] | None) -> list[str]:
        """Resolve argv for the requested shell kind (tests may override)."""
        if exe_override:
            return exe_override
        if shell == "cmd":
            return ["cmd.exe", "/Q"]
        exe = shutil.which("pwsh") or shutil.which("powershell")
        if exe is None:
            raise ValueError("PowerShell executable not found on PATH")
        return [exe, "-NoLogo", "-NoProfile"]

    async def poll(
        self,
        *,
        owner_id: str,
        session_id: str,
        cursor: int | None = None,
        yield_time_ms: int = 1_000,
        max_output_chars: int = 65_536,
        advance: bool = True,
    ) -> dict[str, Any]:
        """Incrementally read new output and status from a session.

        Args:
            owner_id: Unified message origin owning the session.
            session_id: Terminal session identifier.
            cursor: Byte offset to read from; None = last returned offset.
            yield_time_ms: Max wait (ms) for new output / process exit.
            max_output_chars: Max bytes returned in one call.
            advance: False = peek (do not move the session cursor), used
                by the status snapshot endpoint.

        Returns:
            ``{session_id, pid, status, stdout, exit_code, cursor, has_more}``.

        Raises:
            ValueError: Unknown session, owner mismatch, or bad argument.
        """
        if yield_time_ms < 0 or yield_time_ms > 30_000:
            raise ValueError("`yield_time_ms` must be between 0 and 30000.")
        if max_output_chars < 1:
            raise ValueError("`max_output_chars` must be greater than 0.")

        session = await self._get_owned_session(owner_id, session_id)
        read_cursor = session.cursor if cursor is None else cursor
        if read_cursor < 0:
            raise ValueError("`cursor` must be greater than or equal to 0.")

        def _read() -> tuple[bytes, int, int]:
            try:
                output_size = session.output_path.stat().st_size
            except FileNotFoundError:
                return b"", read_cursor, read_cursor
            normalized = min(read_cursor, output_size)
            with session.output_path.open("rb") as output_file:
                output_file.seek(normalized)
                raw_output = output_file.read(max_output_chars)
            return raw_output, normalized + len(raw_output), output_size

        raw_output, next_cursor, output_size = await asyncio.to_thread(_read)

        if not raw_output and session.process.returncode is None and yield_time_ms > 0:
            session.output_event.clear()
            raw_output, next_cursor, output_size = await asyncio.to_thread(_read)
            if not raw_output and session.process.returncode is None:
                output_waiter = asyncio.create_task(session.output_event.wait())
                done, _ = await asyncio.wait(
                    {output_waiter, session.wait_task},
                    timeout=yield_time_ms / 1000,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if output_waiter not in done:
                    output_waiter.cancel()
                    try:
                        await output_waiter
                    except asyncio.CancelledError:
                        pass
                if session.wait_task.done():
                    await session.reader_task
                raw_output, next_cursor, output_size = await asyncio.to_thread(
                    _read
                )

        exit_code = session.process.returncode
        if exit_code is not None and not session.reader_task.done():
            await session.reader_task
            raw_output, next_cursor, output_size = await asyncio.to_thread(_read)

        if advance:
            session.cursor = next_cursor
        status = self._status_of(session, exit_code)
        has_more = next_cursor < output_size
        # A finished process is never consumed again (the SSE loop
        # exits right after the exit event), so the session and its
        # output file are removed unconditionally — this poll already
        # read up to max_output_chars of the remaining tail.
        session_closed = exit_code is not None
        result = {
            "session_id": session_id,
            "pid": session.process.pid,
            "status": status,
            "stdout": decode_bytes_with_fallback(raw_output),
            "exit_code": exit_code,
            "cursor": next_cursor,
            "has_more": has_more,
        }
        if session_closed:
            await self._remove_session(session)
        return result

    async def write(
        self,
        *,
        owner_id: str,
        session_id: str,
        chars: str,
    ) -> dict[str, Any]:
        """Write text verbatim to the session's stdin pipe.

        Args:
            owner_id: Unified message origin owning the session.
            session_id: Terminal session identifier.
            chars: Text to write (caller controls newlines).

        Returns:
            ``{session_id, pid, status}``.

        Raises:
            ValueError: Unknown session or process is exiting.
        """
        session = await self._get_owned_session(owner_id, session_id)
        if session.process.returncode is not None or session.process.stdin is None:
            raise ValueError(f"Shell session {session_id} is not accepting input.")
        session.process.stdin.write(chars.encode("utf-8"))
        await session.process.stdin.drain()
        return {
            "session_id": session_id,
            "pid": session.process.pid,
            "status": self._status_of(session, session.process.returncode),
        }

    async def interrupt(
        self,
        *,
        owner_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Send an interrupt (Ctrl+C semantics) to the process group.

        Args:
            owner_id: Unified message origin owning the session.
            session_id: Terminal session identifier.

        Returns:
            ``{session_id, pid, status}``.
        """
        session = await self._get_owned_session(owner_id, session_id)
        if session.process.returncode is None:
            if os.name == "nt":
                session.process.send_signal(
                    getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)
                )
            else:
                try:
                    os.killpg(session.process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
        return {
            "session_id": session_id,
            "pid": session.process.pid,
            "status": self._status_of(session, session.process.returncode),
        }

    async def terminate(
        self,
        *,
        owner_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Terminate a session's whole process tree and remove it.

        Args:
            owner_id: Unified message origin owning the session.
            session_id: Terminal session identifier.

        Returns:
            Final ``{session_id, pid, status, ...}`` from the cleanup poll.
        """
        session = await self._get_owned_session(owner_id, session_id)
        session.terminated = True
        await self._terminate_process(session)
        return await self.poll(
            owner_id=owner_id,
            session_id=session_id,
            yield_time_ms=0,
        )

    async def owner_session(self, owner_id: str) -> dict[str, Any] | None:
        """Return the live session info for one owner, if any.

        Args:
            owner_id: Unified message origin to look up.

        Returns:
            ``{session_id, pid, status, shell}`` or None.
        """
        async with self._lock:
            for session in self._sessions.values():
                if session.owner_id == owner_id:
                    return {
                        "session_id": session.session_id,
                        "pid": session.process.pid,
                        "status": self._status_of(
                            session, session.process.returncode
                        ),
                        "shell": session.shell_kind,
                    }
        return None

    async def shutdown_sessions(self) -> None:
        """Terminate and remove every session (plugin unload)."""
        async with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.terminated = True
        await asyncio.gather(
            *(self._terminate_process(session) for session in sessions),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(session.reader_task for session in sessions),
            return_exceptions=True,
        )
        for session in sessions:
            await self._remove_session(session)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _get_owned_session(
        self, owner_id: str, session_id: str
    ) -> _TerminalSession:
        """Resolve a session while enforcing owner (umo) ownership."""
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.owner_id != owner_id:
            raise ValueError(f"Shell session {session_id} was not found.")
        return session

    @staticmethod
    def _status_of(session: _TerminalSession, exit_code: int | None) -> str:
        """Map process state to the public status string."""
        if exit_code is None:
            return "running"
        if session.terminated:
            return "terminated"
        return "completed" if exit_code == 0 else "failed"

    async def _terminate_process(self, session: _TerminalSession) -> None:
        """Gracefully terminate a process group, then force it if needed."""
        if session.process.returncode is not None:
            return
        if os.name == "nt":
            try:
                taskkill_result = await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill", "/F", "/T", "/PID", str(session.process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                session.process.terminate()
            else:
                if taskkill_result.returncode != 0:
                    session.process.terminate()
        else:
            try:
                os.killpg(session.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        try:
            await asyncio.wait_for(
                asyncio.shield(session.wait_task),
                timeout=5,
            )
        except asyncio.TimeoutError:
            if os.name == "nt":
                session.process.kill()
            else:
                try:
                    os.killpg(session.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await session.wait_task

    async def _remove_session(self, session: _TerminalSession) -> None:
        """Remove a finished session and its temporary output file."""
        async with self._lock:
            if self._sessions.get(session.session_id) is session:
                self._sessions.pop(session.session_id, None)
        session.output_path.unlink(missing_ok=True)
        try:
            session.output_path.parent.rmdir()
        except OSError:
            pass
