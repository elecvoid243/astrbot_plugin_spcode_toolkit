"""
spcode_toolkit 共享辅助函数。
提供 subprocess 封装、JSON 错误包装、提案协议响应、异步桥接、编码探测。
"""

from __future__ import annotations

import asyncio
import json
import locale
import os
import subprocess
import sys
from pathlib import Path
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


def detect_console_encoding() -> str:
    """探测控制台/子进程输出编码。

    WHY: Windows 原生 CLI 工具（Everything/es.exe、cppcheck.exe 等）的 stdout
    遵循**系统 ANSI 代码页**，中文 Windows 通常是 cp936 (GBK)。Python 用 utf-8
    解码 GBK 字节流会产生  之类乱码；errors="replace" 也无法恢复原字符。
    必须用与外部进程输出**一致**的编码去解码。

    探测策略：
      1. locale.getpreferredencoding(False) —— 中文 Windows → cp936
      2. sys.getfilesystemencoding() —— 通常与 1 一致；某些容器返回 ascii
      3. ascii/us-ascii/ansi_x3.4-1968 → 强制 fallback 到 utf-8（避免真出现时解码失败）

    Returns:
        编码名称字符串，永远不会是 ascii/us-ascii（已 fallback）。
    """
    enc = ""
    try:
        enc = locale.getpreferredencoding(False) or ""
    except Exception:
        enc = ""
    if not enc or enc.lower() in ("ascii", "us-ascii", "ansi_x3.4-1968"):
        enc = sys.getfilesystemencoding() or "utf-8"
    if enc.lower() in ("ascii", "us-ascii", "ansi_x3.4-1968"):
        enc = "utf-8"
    return enc


def safe_decode_bytes(
    data: bytes | str,
    preferred: str | None = None,
    fallback: tuple[str, ...] = ("cp936", "gbk", "utf-8", "latin-1"),
) -> str:
    """安全解码字节/字符串，多次兜底。

    WHY: 即使 detect_console_encoding 返回正确编码，子进程的 stderr 中也可能混入
    其它编码（例如 UTF-8 BOM、GBK escape 序列）。先尝试 preferred，再用 fallback
    链逐个尝试；仍失败则用 errors="replace" 兜底，保证**永不解码崩溃**。

    Args:
        data: 字节或字符串（已是 str 时直接返回）。
        preferred: 首选编码；None 时用 detect_console_encoding()。
        fallback: 兜底编码链（按顺序尝试）。

    Returns:
        字符串。
    """
    if isinstance(data, str):
        return data
    if not isinstance(data, (bytes, bytearray)):
        return str(data)
    if preferred is None:
        preferred = detect_console_encoding()
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    for enc in fallback:
        if enc.lower() != (preferred or "").lower() and enc not in candidates:
            candidates.append(enc)
    for enc in candidates:
        try:
            return bytes(data).decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # 全部失败：errors="replace" 兜底
    return bytes(data).decode(preferred or "utf-8", errors="replace")


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


# ─── Git worktree helpers (spec §2.3) ───────────────────────────────────
# Author: elecvoid243 @ 2026-06-18
# Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md


def _resolve_git_common_dir(git_bin: str, worktree_path: str) -> str:
    """Resolve `git rev-parse --git-common-dir` to an absolute, case-normalized path.

    `git rev-parse --git-common-dir` returns a RELATIVE path (e.g. ".git")
    regardless of the input directory. Two completely unrelated repos both
    return ".git" as a string, so a naive equality check would falsely match
    them — enabling a cross-repo data leak. We must resolve to an absolute
    path and normcase for Windows before comparing.

    See spec §2.3 for the empirical verification.
    """
    raw = subprocess.run(
        [git_bin, "-C", worktree_path, "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    ).stdout.strip()
    return os.path.normcase(os.path.abspath(os.path.join(worktree_path, raw)))


def _parse_git_worktree_porcelain(text: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output.

    Returns a list of dicts with keys: path, branch, head_sha, is_main,
    locked, locked_reason. (v2.14.0: locked / locked_reason added)
    The first worktree in the output is always the main worktree (is_main=True).
    Raises ValueError on unrecognized records.

    Branch prefix stripping: `branch refs/heads/main` → `branch="main"`
    (preserved from v2.x — do not regress).
    """
    worktrees: list[dict] = []
    current: dict | None = None
    # v2.14.0+ multiline reason accumulator
    multiline_buffer: list[str] | None = None

    for raw_line in text.splitlines():
        if not raw_line:
            # Blank line ends current worktree block
            if multiline_buffer is not None and current is not None:
                current["locked_reason"] = "\n".join(multiline_buffer)
                multiline_buffer = None
            if current is not None:
                worktrees.append(current)
                current = None
            continue

        if raw_line.startswith("worktree "):
            # Flush prior block's multiline buffer first
            if multiline_buffer is not None and current is not None:
                current["locked_reason"] = "\n".join(multiline_buffer)
                multiline_buffer = None
            if current is not None:
                worktrees.append(current)
            current = {
                "path": raw_line[len("worktree "):],
                "branch": None,
                "head_sha": "",
                "is_main": False,
                "locked": False,         # v2.14.0+
                "locked_reason": None,    # v2.14.0+
            }
        elif current is None:
            raise ValueError(
                f"Unexpected record outside worktree block: {raw_line!r}"
            )
        elif raw_line.startswith("HEAD "):
            # Flush multiline buffer before new record
            if multiline_buffer is not None:
                current["locked_reason"] = "\n".join(multiline_buffer)
                multiline_buffer = None
            current["head_sha"] = raw_line[len("HEAD "):]
        elif raw_line.startswith("branch "):
            if multiline_buffer is not None:
                current["locked_reason"] = "\n".join(multiline_buffer)
                multiline_buffer = None
            # v2.x preserved: strip refs/heads/ prefix
            ref = raw_line[len("branch "):]
            prefix = "refs/heads/"
            current["branch"] = ref[len(prefix):] if ref.startswith(prefix) else ref
        elif raw_line == "locked":
            if multiline_buffer is not None:
                current["locked_reason"] = "\n".join(multiline_buffer)
                multiline_buffer = None
            # v2.14.0+ — no reason on same line, multiline may follow
            current["locked"] = True
            multiline_buffer = []  # start accumulating multiline reason
        elif raw_line.startswith("locked "):
            if multiline_buffer is not None:
                current["locked_reason"] = "\n".join(multiline_buffer)
                multiline_buffer = None
            # v2.14.0+ — git 2.30+ supports reason on same line as `locked`
            current["locked"] = True
            current["locked_reason"] = raw_line[len("locked "):]
        elif multiline_buffer is not None:
            # We're inside a multiline reason continuation
            multiline_buffer.append(raw_line)
        elif raw_line.startswith("detached"):
            current["branch"] = None
        else:
            raise ValueError(f"Unknown porcelain record: {raw_line!r}")

    if current is not None:
        if multiline_buffer is not None:
            current["locked_reason"] = "\n".join(multiline_buffer) or None
        worktrees.append(current)

    for i, wt in enumerate(worktrees):
        wt["is_main"] = i == 0

    return worktrees


def _validate_worktree_param(
    git_bin: str,
    loaded_dir: str,
    candidate: str | None,
) -> tuple[str | None, str | None]:
    """6-step defense for the ``?worktree=`` query parameter.

    Returns ``(validated_path, error_reason)``:
      - On success: ``(absolute_path, None)`` — safe to use as the ``-C`` arg.
      - On rejection: ``(None, "worktree_invalid")``.

    Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §2.3

    The 6 steps:
      1. Empty / whitespace → reject
      2. Path-traversal chars (`..`, `\\`, `//`) → reject
      3. Not an absolute path → reject
      4. Any path component starts with `.` (hidden dir like ``.git``) → reject
      5. Symlink resolution: realpath must equal original → reject
      6. ``git rev-parse --git-common-dir`` must match the loaded project → reject
    """
    if candidate is None:
        return None, "worktree_invalid"
    stripped = candidate.strip()
    if not stripped:
        return None, "worktree_invalid"
    if ".." in stripped:
        return None, "worktree_invalid"

    # Step 3: must be absolute
    if not os.path.isabs(stripped):
        return None, "worktree_invalid"

    # Step 4: block only the .git directory itself, NOT arbitrary dotfile
    # directories. Git's official worktree convention is to place linked
    # worktrees under `<repo>/.worktrees/<name>/` (e.g. `git worktree add
    # .worktrees/feature-x`). A blanket "no dot-prefixed component" rule
    # wrongly rejects that legitimate layout, breaking the standard
    # `.worktrees/*` deployment pattern. Cross-repo path attacks are
    # already blocked by step 6 (git-common-dir equality), so this check
    # only needs to defend against attempts to read .git internals
    # directly. A nested form like ".git/foo" is also caught by the
    # equality check below.
    parts = Path(stripped).parts
    if any(part == ".git" for part in parts):
        return None, "worktree_invalid"

    # Step 5: symlink defense — realpath must match
    try:
        real = os.path.realpath(stripped)
    except OSError:
        return None, "worktree_invalid"
    if os.path.normcase(real) != os.path.normcase(stripped):
        return None, "worktree_invalid"

    if not os.path.isdir(real):
        return None, "worktree_invalid"

    # Step 6: cross-repo defense (git-common-dir must match loaded project)
    try:
        candidate_common = _resolve_git_common_dir(git_bin, real)
        loaded_common = _resolve_git_common_dir(git_bin, loaded_dir)
    except Exception:
        return None, "worktree_invalid"
    if candidate_common != loaded_common:
        return None, "worktree_invalid"

    return real, None
