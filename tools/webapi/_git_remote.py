"""Shared helpers for Git remote synchronization Web API endpoints.

Spec: docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md
Internal module; do not register as an AstrBot tool.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

from ._helpers import ReasonCode, _run_git_async

MAX_REMOTE_NAME_LENGTH = 128
MAX_REMOTE_URL_LENGTH = 2048
REMOTE_TIMEOUT_SECONDS = 60.0

_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _is_valid_remote_name(remote: str | None) -> bool:
    """Return True when a Git remote name is safe to pass as an argv item."""
    if not isinstance(remote, str) or not remote or len(remote) > MAX_REMOTE_NAME_LENGTH:
        return False
    if remote.startswith("-") or ".." in remote:
        return False
    return bool(_REMOTE_NAME_RE.fullmatch(remote))


def _is_valid_remote_url(url: str | None) -> bool:
    """Return True when a remote URL is non-empty and control-character free."""
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_REMOTE_URL_LENGTH:
        return False
    return not any(ord(char) < 32 or ord(char) == 127 for char in url)


def _mask_remote_url(url: str) -> str:
    """Mask credentials in HTTPS and scp-like SSH URLs for logs."""
    if "://" in url:
        parts = urlsplit(url)
        if "@" in parts.netloc:
            host = parts.hostname or ""
            try:
                port = parts.port
            except ValueError:
                port = None
            if port is not None:
                host = f"{host}:{port}"
            return urlunsplit(
                (parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment)
            )
        return url
    if "@" in url:
        return "***@" + url.split("@", 1)[1]
    return url


def _build_remote_git_env() -> dict[str, str]:
    """Build a complete child environment that disables credential prompts."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["SSH_ASKPASS"] = "echo"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    return env


def _classify_remote_error(stderr: str, stdout: str = "") -> str:
    """Classify pull/push/remote stderr into a stable ReasonCode."""
    combined = f"{stderr} {stdout}".lower()
    if any(
        token in combined
        for token in (
            "authentication failed",
            "authentication required",
            "could not read username",
            "permission denied",
            "access denied",
            "unauthorized",
            "terminal prompts disabled",
            "403",
        )
    ):
        return ReasonCode.AUTH_REQUIRED
    if any(
        token in combined
        for token in (
            "could not resolve host",
            "name or service not known",
            "temporary failure in name resolution",
            "failed to connect",
            "connection timed out",
            "operation timed out",
            "network is unreachable",
            "connection refused",
            "timeout",
            "timed out",
        )
    ):
        return ReasonCode.NETWORK_ERROR
    if "does not appear to be a git repository" in combined:
        return ReasonCode.REMOTE_NOT_FOUND
    if "non-fast-forward" in combined or "not possible to fast-forward" in combined:
        return ReasonCode.NON_FAST_FORWARD
    if "fetch first" in combined or "update were rejected" in combined:
        return ReasonCode.NON_FAST_FORWARD
    if "hook declined" in combined or "remote rejected" in combined:
        return ReasonCode.PUSH_REJECTED
    return ReasonCode.GIT_ERROR


def _is_already_up_to_date(stdout: str, stderr: str) -> bool:
    """Return True when git pull reports that no update was needed."""
    combined = f"{stdout} {stderr}".lower()
    return "already up to date" in combined or "already up-to-date" in combined


def _is_everything_up_to_date(stdout: str, stderr: str) -> bool:
    """Return True when git push reports that no ref update was needed."""
    combined = f"{stdout} {stderr}".lower()
    return "everything up-to-date" in combined


def _parse_upstream(upstream: str) -> tuple[str, str]:
    """Split an upstream short name into remote and branch."""
    if "/" not in upstream:
        return "origin", upstream
    remote, branch = upstream.split("/", 1)
    return remote, branch


async def _read_current_branch(git_bin: str, directory: str) -> str | None:
    """Read the current branch, returning None for detached HEAD or failure."""
    result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        return None
    branch = result.get("stdout", "").strip()
    return branch if branch and branch != "HEAD" else None


async def _read_upstream(git_bin: str, directory: str) -> str | None:
    """Read the current upstream short name, returning None when unset."""
    result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--abbrev-ref", "@{upstream}"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        return None
    upstream = result.get("stdout", "").strip()
    return upstream if upstream and upstream != "HEAD" else None


async def _get_remote_url(
    git_bin: str,
    directory: str,
    remote: str,
) -> tuple[bool, str, str]:
    """Read a remote URL.

    Returns:
        ``(exists, url, error)``. ``url`` is empty when the remote is absent.
    """
    result = await _run_git_async(
        [git_bin, "-C", directory, "remote", "get-url", remote],
        encoding="utf-8",
        timeout=5.0,
    )
    if result.get("ok"):
        return True, result.get("stdout", "").strip(), ""
    stderr = result.get("stderr", "") or result.get("error", "")
    return False, "", stderr


async def _read_head_sha(git_bin: str, directory: str) -> str:
    """Read the full HEAD SHA, returning an empty string on failure."""
    result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    return result.get("stdout", "").strip() if result.get("ok") else ""
