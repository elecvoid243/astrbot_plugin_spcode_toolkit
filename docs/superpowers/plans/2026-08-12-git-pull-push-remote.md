# Git Pull / Push / Remote Set-URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three safe, headless Git remote synchronization Web API endpoints: `POST /spcode/git-pull`, `POST /spcode/git-push`, and `POST /spcode/git-remote-set-url`.

**Architecture:** Follow the established one-file-per-endpoint Web API pattern. Shared remote validation, upstream resolution, network environment setup, and stderr classification live in `tools/webapi/_git_remote.py`. Pull rebase conflicts are recoverable through the existing conflict lifecycle by extending it with `rebase` detection, continue, and abort support.

**Tech Stack:** Python 3.10+, AstrBot Web API, pytest, Git CLI, local bare repositories for hermetic integration tests.

**Spec:** `docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md`
**Author:** elecvoid243 @ 2026-08-12 13:05 CST

## Global Constraints

- Follow `docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md` exactly.
- Python 3.10+ syntax only; type annotations required for new functions.
- Use argv lists for subprocesses; never use `shell=True`.
- Reuse `_git_endpoint_preflight`, `_make_envelope`, `_JSONResponseCompat`, `ReasonCode`, and `_run_git_async`.
- All network git operations use a 60-second timeout and disable interactive credential prompts.
- Never log an unmasked remote URL.
- No force push, tag push, autostash, credential management, or frontend implementation.
- Do not modify `README.md`, `AGENTS.md`, or `metadata.yaml`; they already contain unrelated uncommitted user changes.
- Run TDD: write each failing test first, verify it fails for the expected missing behavior, then implement the minimal code.
- Commit only files owned by the current task; never commit unrelated dirty files.
- All commits remain local. Do not push and do not create a PR.

---

### Task 1: Create isolated worktree and verify clean baseline

**Files:**
- No repository source files are modified in this task.

**Interfaces:**
- Produces: isolated working directory for all later tasks.
- Consumes: current `main` HEAD, including approved design commit `d6ae61b`.

- [ ] **Step 1: Detect existing isolation**

Run from the repository root:

```powershell
$gitDir = (Resolve-Path (git rev-parse --git-dir)).Path
$gitCommon = (Resolve-Path (git rev-parse --git-common-dir)).Path
$branch = git branch --show-current
$superproject = git rev-parse --show-superproject-working-tree 2>$null
Write-Output "gitDir=$gitDir"
Write-Output "gitCommon=$gitCommon"
Write-Output "branch=$branch"
Write-Output "superproject=$superproject"
```

Expected: `gitDir` equals `gitCommon`, branch is `main`, and `superproject` is empty. If already inside a linked worktree, skip worktree creation and use that directory.

- [ ] **Step 2: Verify worktree directory is ignored**

```powershell
git check-ignore -v .worktrees
```

Expected: a `.gitignore` rule is printed. If no rule exists, stop and report it instead of creating an unignored worktree.

- [ ] **Step 3: Create worktree**

```powershell
git worktree add .worktrees/git-remote-sync -b feat/git-remote-sync
Set-Location .worktrees/git-remote-sync
```

Expected: worktree exists at `.worktrees/git-remote-sync` on branch `feat/git-remote-sync`.

- [ ] **Step 4: Run baseline tests**

```powershell
python -m pytest tests/test_webapi_end_to_end.py tests/test_git_conflict_helpers.py tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py tests/test_reason_code.py -q
```

Expected: all selected tests pass. If any fail before modifications, stop and report the pre-existing failure.

- [ ] **Step 5: Record baseline status**

```powershell
git status --short --branch
```

Expected: branch `feat/git-remote-sync`; no source modifications from this task. Unrelated dirty files from the primary checkout are not copied into the worktree.

---

### Task 2: Add rebase support to conflict lifecycle

**Files:**
- Modify: `tools/webapi/_helpers.py`
- Modify: `tools/webapi/git_conflict_continue.py`
- Modify: `tools/webapi/git_conflict_abort.py`
- Test: `tests/test_git_conflict_helpers.py`
- Test: `tests/test_git_conflict_continue.py`
- Test: `tests/test_git_conflict_abort.py`

**Interfaces:**
- Consumes: existing `_detect_conflict_operation(git_bin, directory) -> str | None`.
- Produces: operation literal `"rebase"`; `_CONTINUE_CMD["rebase"]`; `_ABORT_CMD["rebase"]`.

- [ ] **Step 1: Write failing rebase detection tests**

Append to `TestDetectConflictOperation` in `tests/test_git_conflict_helpers.py`:

```python
@pytest.mark.asyncio
async def test_rebase_merge_conflict(self, tmp_path):
    from tools.webapi._helpers import _detect_conflict_operation

    git_dir = tmp_path / ".git"
    (git_dir / "rebase-merge").mkdir(parents=True)

    with patch(
        "tools.webapi._helpers._run_git_async",
        new_callable=AsyncMock,
        return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
    ):
        result = await _detect_conflict_operation("git", str(tmp_path))
    assert result == "rebase"


@pytest.mark.asyncio
async def test_rebase_apply_conflict(self, tmp_path):
    from tools.webapi._helpers import _detect_conflict_operation

    git_dir = tmp_path / ".git"
    (git_dir / "rebase-apply").mkdir(parents=True)

    with patch(
        "tools.webapi._helpers._run_git_async",
        new_callable=AsyncMock,
        return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
    ):
        result = await _detect_conflict_operation("git", str(tmp_path))
    assert result == "rebase"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_git_conflict_helpers.py -k rebase -q
```

Expected: both tests fail with `assert None == 'rebase'`.

- [ ] **Step 3: Write failing continue/abort command tests**

Append to `tests/test_git_conflict_continue.py`:

```python
@pytest.mark.asyncio
async def test_rebase_continue_uses_rebase_continue(self):
    from tools.webapi.git_conflict_continue import handle

    plugin = _make_plugin()
    calls = []

    async def mock_run(args, **kwargs):
        calls.append(args)
        if "rebase" in args:
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}
        if "rev-parse" in args and "HEAD" in args:
            return {"ok": True, "stdout": "abc123\n", "stderr": "", "code": 0}
        return {"ok": True, "stdout": "", "stderr": "", "code": 0}

    with (
        patch("tools.webapi.git_conflict_continue._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf,
        patch("tools.webapi.git_conflict_continue._detect_conflict_operation", new_callable=AsyncMock, return_value="rebase"),
        patch("tools.webapi.git_conflict_continue._list_conflicted_files", new_callable=AsyncMock, return_value=[]),
        patch("tools.webapi.git_conflict_continue._run_git_async", side_effect=mock_run),
        patch("tools.webapi.git_conflict_continue._read_post_mutation_branch_state", new_callable=AsyncMock, return_value={"branches": [], "total": 0, "current": "main", "detached": False}),
        patch("tools.webapi.git_conflict_continue._build_git_env", return_value=None),
    ):
        mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
        result = await handle(plugin, body={})

    assert result["data"]["continued"] is True
    rebase_call = next(call for call in calls if "rebase" in call)
    assert "rebase" in rebase_call
    assert "--continue" in rebase_call
```

Append to `tests/test_git_conflict_abort.py`:

```python
@pytest.mark.asyncio
async def test_rebase_abort_uses_rebase_abort(self):
    from tools.webapi.git_conflict_abort import handle

    plugin = _make_plugin()
    calls = []
    detect_calls = [0]

    async def mock_detect(*args, **kwargs):
        detect_calls[0] += 1
        return "rebase" if detect_calls[0] == 1 else None

    async def mock_run(args, **kwargs):
        calls.append(args)
        return {"ok": True, "stdout": "", "stderr": "", "code": 0}

    with (
        patch("tools.webapi.git_conflict_abort._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf,
        patch("tools.webapi.git_conflict_abort._detect_conflict_operation", side_effect=mock_detect),
        patch("tools.webapi.git_conflict_abort._run_git_async", side_effect=mock_run),
        patch("tools.webapi.git_conflict_abort._read_operation_ref", new_callable=AsyncMock, return_value=("abc123", "commit")),
        patch("tools.webapi.git_conflict_abort._read_post_mutation_branch_state", new_callable=AsyncMock, return_value={"branches": [], "total": 0, "current": "main", "detached": False}),
    ):
        mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
        result = await handle(plugin, body={})

    assert result["data"]["aborted"] is True
    assert ["rebase", "--abort"] == calls[0][-2:]
```

- [ ] **Step 4: Verify RED**

Run:

```powershell
python -m pytest tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py -k rebase -q
```

Expected: continue fails with `KeyError: 'rebase'`; abort fails with `KeyError: 'rebase'`.

- [ ] **Step 5: Implement rebase lifecycle support**

In `tools/webapi/_helpers.py`, update `_detect_conflict_operation` to check rebase before merge:

```python
    if (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir():
        return "rebase"
    if (git_dir / "MERGE_HEAD").exists():
        return "merge"
```

Update its docstring return contract to `"merge" | "cherry_pick" | "revert" | "rebase" | None`.

Update `_SENTINEL_BY_OPERATION`:

```python
_SENTINEL_BY_OPERATION = {
    "merge": "MERGE_HEAD",
    "cherry_pick": "CHERRY_PICK_HEAD",
    "revert": "REVERT_HEAD",
    "rebase": "REBASE_HEAD",
}
```

In `tools/webapi/git_conflict_continue.py`:

```python
_CONTINUE_CMD = {
    "merge": None,
    "cherry_pick": ["cherry-pick", "--continue", "--no-edit"],
    "revert": ["revert", "--continue", "--no-edit"],
    "rebase": [
        "-c",
        "core.editor=true",
        "-c",
        "sequence.editor=true",
        "rebase",
        "--continue",
    ],
}
```

In `tools/webapi/git_conflict_abort.py`:

```python
_ABORT_CMD = {
    "merge": ["merge", "--abort"],
    "cherry_pick": ["cherry-pick", "--abort"],
    "revert": ["revert", "--abort"],
    "rebase": ["rebase", "--abort"],
}
```

- [ ] **Step 6: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_git_conflict_helpers.py tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py -q
```

Expected: all tests pass, including the new rebase tests.

- [ ] **Step 7: Commit**

```powershell
git add tools/webapi/_helpers.py tools/webapi/git_conflict_continue.py tools/webapi/git_conflict_abort.py tests/test_git_conflict_helpers.py tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py
git commit -m "feat(webapi): support rebase conflict lifecycle"
```

---

### Task 3: Add shared remote helper module and ReasonCodes

**Files:**
- Create: `tools/webapi/_git_remote.py`
- Modify: `tools/webapi/_helpers.py`
- Test: `tests/test_git_remote_helpers.py`
- Test: `tests/test_reason_code.py`

**Interfaces:**
- Produces:
  - `_is_valid_remote_name(remote: str | None) -> bool`
  - `_is_valid_remote_url(url: str | None) -> bool`
  - `_mask_remote_url(url: str) -> str`
  - `_build_remote_git_env() -> dict[str, str]`
  - `_classify_remote_error(stderr: str, stdout: str = "") -> str`
  - `_is_already_up_to_date(stdout: str, stderr: str) -> bool`
  - `_is_everything_up_to_date(stdout: str, stderr: str) -> bool`
  - `_parse_upstream(upstream: str) -> tuple[str, str]`
  - `_read_current_branch(git_bin: str, directory: str) -> str | None`
  - `_read_upstream(git_bin: str, directory: str) -> str | None`
  - `_get_remote_url(git_bin: str, directory: str, remote: str) -> tuple[bool, str, str]`
  - `_read_head_sha(git_bin: str, directory: str) -> str`

- [ ] **Step 1: Write failing helper tests**

Create `tests/test_git_remote_helpers.py`:

```python
"""Tests for shared git remote Web API helpers."""

import os

import pytest


def test_remote_name_validation():
    from tools.webapi._git_remote import _is_valid_remote_name

    assert _is_valid_remote_name("origin")
    assert _is_valid_remote_name("upstream")
    assert _is_valid_remote_name("team-origin")
    assert not _is_valid_remote_name("")
    assert not _is_valid_remote_name(None)
    assert not _is_valid_remote_name("-origin")
    assert not _is_valid_remote_name("bad name")
    assert not _is_valid_remote_name("bad..name")
    assert not _is_valid_remote_name("x" * 129)


def test_remote_url_validation():
    from tools.webapi._git_remote import _is_valid_remote_url

    assert _is_valid_remote_url("https://example.com/org/repo.git")
    assert _is_valid_remote_url("git@example.com:org/repo.git")
    assert _is_valid_remote_url("F:/tmp/remote.git")
    assert not _is_valid_remote_url("")
    assert not _is_valid_remote_url(None)
    assert not _is_valid_remote_url("https://example.com/a\nb")
    assert not _is_valid_remote_url("x" * 2049)


def test_remote_url_masking():
    from tools.webapi._git_remote import _mask_remote_url

    assert (
        _mask_remote_url("https://user:secret@example.com/org/repo.git")
        == "https://***@example.com/org/repo.git"
    )
    assert _mask_remote_url("git@example.com:org/repo.git") == "***@example.com:org/repo.git"


def test_remote_env_disables_prompts(monkeypatch):
    from tools.webapi._git_remote import _build_remote_git_env

    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    env = _build_remote_git_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == "echo"
    assert env["SSH_ASKPASS"] == "echo"
    assert "BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert "PATH" in env


def test_remote_error_classification():
    from tools.webapi._git_remote import _classify_remote_error
    from tools.webapi._helpers import ReasonCode

    assert _classify_remote_error("fatal: Authentication failed") == ReasonCode.AUTH_REQUIRED
    assert _classify_remote_error("fatal: Could not resolve host example") == ReasonCode.NETWORK_ERROR
    assert _classify_remote_error("! [rejected] main -> main (non-fast-forward)") == ReasonCode.NON_FAST_FORWARD
    assert _classify_remote_error("remote: pre-receive hook declined") == ReasonCode.PUSH_REJECTED
    assert _classify_remote_error("fatal: unexpected") == ReasonCode.GIT_ERROR


def test_upstream_parse():
    from tools.webapi._git_remote import _parse_upstream

    assert _parse_upstream("origin/main") == ("origin", "main")
    assert _parse_upstream("upstream/feature/x") == ("upstream", "feature/x")
```

Append to `tests/test_reason_code.py`:

```python
def test_git_remote_sync_reason_codes_defined():
    expected = {
        "INVALID_REMOTE": "invalid_remote",
        "INVALID_URL": "invalid_url",
        "REMOTE_NOT_FOUND": "remote_not_found",
        "NO_UPSTREAM": "no_upstream",
        "DETACHED_HEAD": "detached_head",
        "AUTH_REQUIRED": "auth_required",
        "NETWORK_ERROR": "network_error",
        "NON_FAST_FORWARD": "non_fast_forward",
        "PUSH_REJECTED": "push_rejected",
        "REBASE_CONFLICT": "rebase_conflict",
    }
    for name, value in expected.items():
        assert getattr(ReasonCode, name) == value
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_git_remote_helpers.py tests/test_reason_code.py -q
```

Expected: helper tests fail with `ModuleNotFoundError: tools.webapi._git_remote`; ReasonCode test fails with `AttributeError`.

- [ ] **Step 3: Implement shared helper module**

Create `tools/webapi/_git_remote.py` with this implementation:

```python
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
            if parts.port is not None:
                host = f"{host}:{parts.port}"
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
    combined = f"{stdout} {stderr}".lower()
    return "already up to date" in combined or "already up-to-date" in combined


def _is_everything_up_to_date(stdout: str, stderr: str) -> bool:
    combined = f"{stdout} {stderr}".lower()
    return "everything up-to-date" in combined


def _parse_upstream(upstream: str) -> tuple[str, str]:
    """Split an upstream short name into remote and branch."""
    if "/" not in upstream:
        return "origin", upstream
    remote, branch = upstream.split("/", 1)
    return remote, branch


async def _read_current_branch(git_bin: str, directory: str) -> str | None:
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
    git_bin: str, directory: str, remote: str
) -> tuple[bool, str, str]:
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
    result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
        timeout=5.0,
    )
    return result.get("stdout", "").strip() if result.get("ok") else ""
```

Add the ReasonCode constants listed in the failing test to `tools/webapi/_helpers.py` in a new `git remote sync` section.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_git_remote_helpers.py tests/test_reason_code.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Lint changed files**

Use the built-in `code_check` tool on:

```text
tools/webapi/_git_remote.py
tools/webapi/_helpers.py
tests/test_git_remote_helpers.py
tests/test_reason_code.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```powershell
git add tools/webapi/_git_remote.py tools/webapi/_helpers.py tests/test_git_remote_helpers.py tests/test_reason_code.py
git commit -m "feat(webapi): add git remote sync helpers"
```

---

### Task 4: Implement `POST /spcode/git-remote-set-url`

**Files:**
- Create: `tools/webapi/git_remote_set_url.py`
- Test: `tests/test_git_remote_set_url.py`

**Interfaces:**
- Consumes: `_is_valid_remote_name`, `_is_valid_remote_url`, `_get_remote_url`, `_mask_remote_url`.
- Produces: `handle(plugin, *, umo=None, worktree=None, body=None) -> dict`.

- [ ] **Step 1: Write failing add/update/unchanged tests**

Create `tests/test_git_remote_set_url.py` with hermetic local repositories:

```python
"""Integration tests for POST /spcode/git-remote-set-url."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi.git_remote_set_url import handle

pytestmark = pytest.mark.asyncio


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _load(path: Path) -> str:
    umo = "u:remote"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_adds_missing_origin(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(
        plugin,
        umo=umo,
        body={"remote": "origin", "url": str(tmp_path / "remote.git")},
    )

    assert result["data"]["success"] is True
    assert result["data"]["action"] == "added"
    check = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert check.stdout.strip() == str(tmp_path / "remote.git")


async def test_updates_existing_origin(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "remote", "add", "origin", "old-url"], cwd=repo, check=True)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(
        plugin,
        umo=umo,
        body={"remote": "origin", "url": "https://example.com/new.git"},
    )

    assert result["data"]["success"] is True
    assert result["data"]["action"] == "updated"


async def test_same_url_is_unchanged(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "remote", "add", "origin", "same-url"], cwd=repo, check=True)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(
        plugin,
        umo=umo,
        body={"remote": "origin", "url": "same-url"},
    )

    assert result["data"]["success"] is True
    assert result["data"]["action"] == "unchanged"


async def test_invalid_url(tmp_path: Path):
    plugin = _make_plugin()
    result = await handle(plugin, body={"remote": "origin", "url": "bad\nurl"})
    assert result["data"]["reason"] == "invalid_url"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_git_remote_set_url.py -q
```

Expected: `ModuleNotFoundError` for `tools.webapi.git_remote_set_url`.

- [ ] **Step 3: Implement endpoint**

Create `tools/webapi/git_remote_set_url.py`:

```python
"""POST /spcode/git-remote-set-url — upsert a Git remote URL.

Spec: docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._git_remote import (
    _get_remote_url,
    _is_valid_remote_name,
    _is_valid_remote_url,
    _mask_remote_url,
)
from ._helpers import (
    ReasonCode,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            configured=False,
        )

    remote = body.get("remote", "origin")
    url = body.get("url")
    if not _is_valid_remote_name(remote):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_REMOTE,
            elapsed_ms=_elapsed(),
            configured=False,
            remote=str(remote or ""),
        )
    if not _is_valid_remote_url(url):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_URL,
            elapsed_ms=_elapsed(),
            configured=False,
            remote=remote,
        )

    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("configured", False)
        err["data"].setdefault("remote", remote)
        return err

    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    exists, current_url, probe_error = await _get_remote_url(git_bin, directory, remote)
    if exists and current_url == url:
        action = "unchanged"
    else:
        args = [
            git_bin,
            "-C",
            directory,
            "remote",
            "add" if not exists else "set-url",
            remote,
            url,
        ]
        result = await _run_git_async(args, encoding="utf-8", timeout=15.0)
        if not result.get("ok"):
            stderr = result.get("stderr", "") or result.get("error", "") or probe_error
            return _make_envelope(
                success=False,
                reason=ReasonCode.GIT_ERROR,
                elapsed_ms=_elapsed(),
                configured=False,
                remote=remote,
                url=url,
                directory=directory,
                umo=effective_umo,
                worktree=directory,
                stderr=stderr[:4096],
            )
        action = "added" if not exists else "updated"

    logger.info(
        "git-remote-set-url: %s %s (%s, umo=%s)",
        remote,
        _mask_remote_url(url),
        action,
        effective_umo,
    )
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            configured=True,
            action=action,
            remote=remote,
            url=url,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_git_remote_set_url.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/git_remote_set_url.py tests/test_git_remote_set_url.py
git commit -m "feat(webapi): add git remote set-url endpoint"
```

---

### Task 5: Implement `POST /spcode/git-push`

**Files:**
- Create: `tools/webapi/git_push.py`
- Test: `tests/test_git_push.py`

**Interfaces:**
- Consumes: shared helpers from Task 3 and conflict detection from Task 2.
- Produces: push endpoint handler returning `pushed`, `set_upstream`, `remote`, `branch`, `remote_branch`, `local_sha`, and `upstream`.

- [ ] **Step 1: Write failing integration tests**

Create `tests/test_git_push.py` with these tests:

```python
"""Integration tests for POST /spcode/git-push."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi.git_push import handle

pytestmark = pytest.mark.asyncio


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("init", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


def _commit(path: Path, name: str, content: str) -> str:
    (path / name).write_text(content, encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", f"add {name}")
    return _git(path, "rev-parse", "HEAD")


def _load(path: Path, umo: str) -> str:
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_first_push_sets_upstream(tmp_path: Path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(bare))
    plugin = _make_plugin()
    umo = _load(repo, "u:push-first")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is True
    assert result["data"]["pushed"] is True
    assert result["data"]["set_upstream"] is True
    assert result["data"]["upstream"] == "origin/main"
    assert _git(repo, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/main"


async def test_push_new_commit_and_noop(tmp_path: Path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "main")
    plugin = _make_plugin()
    umo = _load(repo, "u:push")
    sha = _commit(repo, "a.txt", "a")

    first = await handle(plugin, umo=umo, body={})
    assert first["data"]["success"] is True
    assert first["data"]["pushed"] is True
    assert first["data"]["local_sha"] == sha

    second = await handle(plugin, umo=umo, body={})
    assert second["data"]["success"] is True
    assert second["data"]["pushed"] is False


async def test_non_fast_forward(tmp_path: Path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    one = tmp_path / "one"
    _init_repo(one)
    _git(one, "remote", "add", "origin", str(bare))
    _git(one, "push", "-u", "origin", "main")
    two = tmp_path / "two"
    subprocess.run(["git", "clone", "-q", str(bare), str(two)], check=True)
    _git(two, "config", "user.email", "t@t")
    _git(two, "config", "user.name", "t")
    _commit(two, "two.txt", "two")
    _git(two, "push")
    _commit(one, "one.txt", "one")
    plugin = _make_plugin()
    umo = _load(one, "u:push-nff")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "non_fast_forward"


async def test_missing_remote(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo, "u:push-missing")
    result = await handle(plugin, umo=umo, body={"remote": "missing"})
    assert result["data"]["reason"] == "remote_not_found"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_git_push.py -q
```

Expected: `ModuleNotFoundError` for `tools.webapi.git_push`.

- [ ] **Step 3: Implement endpoint**

Create `tools/webapi/git_push.py` implementing the flow in spec §3.2. Required command behavior:

```python
if upstream and explicit_remote is None and explicit_branch is None:
    push_args = base_args + ["push"]
    set_upstream = False
    remote, branch = _parse_upstream(upstream)
else:
    remote = explicit_remote or (_parse_upstream(upstream)[0] if upstream else "origin")
    branch = explicit_branch or current_branch
    exists, _, _ = await _get_remote_url(git_bin, directory, remote)
    if not exists:
        return remote_not_found_envelope
    push_args = base_args + ["push"]
    if not upstream:
        push_args.append("-u")
        set_upstream = True
    else:
        set_upstream = False
    push_args.extend([remote, branch])
```

Use:

```python
result = await _run_git_async(
    push_args,
    encoding="utf-8",
    env=_build_remote_git_env(),
    timeout=REMOTE_TIMEOUT_SECONDS,
)
```

Success readback:

```python
pushed = not _is_everything_up_to_date(
    result.get("stdout", ""), result.get("stderr", "")
)
local_sha = await _read_head_sha(git_bin, directory)
new_upstream = await _read_upstream(git_bin, directory)
```

Failure classification:

```python
reason = _classify_remote_error(
    result.get("stderr", "") or result.get("error", ""),
    result.get("stdout", ""),
)
```

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_git_push.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/git_push.py tests/test_git_push.py
git commit -m "feat(webapi): add git push endpoint"
```

---

### Task 6: Implement `POST /spcode/git-pull`

**Files:**
- Create: `tools/webapi/git_pull.py`
- Test: `tests/test_git_pull.py`

**Interfaces:**
- Consumes: shared helpers from Task 3, conflict helpers from Task 2.
- Produces: pull endpoint handler returning `pulled`, `updated`, `mode`, `before_sha`, `after_sha`, `fast_forward`, `files_touched`, and `upstream`.

- [ ] **Step 1: Write failing integration tests**

Create `tests/test_git_pull.py` with these tests:

```python
"""Integration tests for POST /spcode/git-pull."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _proj_state
from tools.webapi.git_pull import handle

pytestmark = pytest.mark.asyncio


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _seed_remote(tmp_path: Path) -> tuple[Path, Path, Path]:
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "a.txt").write_text("one", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "-u", "origin", "main")
    local = tmp_path / "local"
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(local)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    for repo in (local, other):
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
    return bare, local, other


def _commit(path: Path, name: str, content: str, message: str) -> str:
    (path / name).write_text(content, encoding="utf-8")
    _git(path, "add", name)
    _git(path, "commit", "-q", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _load(path: Path, umo: str) -> str:
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_pull_noop(tmp_path: Path):
    _, local, _ = _seed_remote(tmp_path)
    plugin = _make_plugin()
    umo = _load(local, "u:pull-noop")
    result = await handle(plugin, umo=umo, body={})
    assert result["data"]["success"] is True
    assert result["data"]["pulled"] is False
    assert result["data"]["updated"] is False


async def test_pull_fast_forward(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    remote_sha = _commit(other, "b.txt", "two", "add b")
    _git(other, "push")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-ff")

    result = await handle(plugin, umo=umo, body={})

    assert result["data"]["success"] is True
    assert result["data"]["updated"] is True
    assert result["data"]["after_sha"] == remote_sha
    assert "b.txt" in result["data"]["files_touched"]


async def test_pull_ff_only_diverged(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    _commit(other, "other.txt", "other", "other")
    _git(other, "push")
    _commit(local, "local.txt", "local", "local")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-nff")
    result = await handle(plugin, umo=umo, body={"ff_only": True})
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "non_fast_forward"


async def test_pull_merge_conflict(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    _commit(other, "a.txt", "remote", "remote change")
    _git(other, "push")
    _commit(local, "a.txt", "local", "local change")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-conflict")
    result = await handle(plugin, umo=umo, body={})
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "merge_conflict"
    assert result["data"]["operation"] == "merge"


async def test_pull_rebase_conflict(tmp_path: Path):
    _, local, other = _seed_remote(tmp_path)
    _commit(other, "a.txt", "remote", "remote change")
    _git(other, "push")
    _commit(local, "a.txt", "local", "local change")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-rebase-conflict")
    result = await handle(plugin, umo=umo, body={"rebase": True})
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "rebase_conflict"
    assert result["data"]["operation"] == "rebase"


async def test_ff_only_and_rebase_are_mutually_exclusive():
    plugin = _make_plugin()
    result = await handle(plugin, body={"ff_only": True, "rebase": True})
    assert result["data"]["reason"] == "invalid_body"


async def test_dirty_worktree_rejected(tmp_path: Path):
    _, local, _ = _seed_remote(tmp_path)
    (local / "dirty.txt").write_text("dirty", encoding="utf-8")
    plugin = _make_plugin()
    umo = _load(local, "u:pull-dirty")
    result = await handle(plugin, umo=umo, body={})
    assert result["data"]["reason"] == "worktree_dirty"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_git_pull.py -q
```

Expected: `ModuleNotFoundError` for `tools.webapi.git_pull`.

- [ ] **Step 3: Implement endpoint**

Create `tools/webapi/git_pull.py` implementing spec §3.1. Required target resolution:

```python
explicit_remote = body.get("remote")
explicit_branch = body.get("branch")
upstream = await _read_upstream(git_bin, directory)
current_branch = await _read_current_branch(git_bin, directory)

if explicit_remote is None:
    if upstream is None:
        return no_upstream_envelope
    remote, branch = _parse_upstream(upstream)
    pull_target_args: list[str] = []
else:
    if not _is_valid_remote_name(explicit_remote):
        return invalid_remote_envelope
    remote = explicit_remote
    branch = explicit_branch or current_branch
    if branch is None:
        return detached_head_envelope
    if not _is_valid_ref_name(branch):
        return invalid_branch_envelope
    exists, _, _ = await _get_remote_url(git_bin, directory, remote)
    if not exists:
        return remote_not_found_envelope
    pull_target_args = [remote, branch]
```

Required command:

```python
args = [
    git_bin,
    "-C",
    directory,
    "-c",
    "color.ui=never",
    "pull",
    "--no-edit",
]
if ff_only:
    args.append("--ff-only")
elif rebase:
    args.append("--rebase")
args.extend(pull_target_args)
```

Success readback:

```python
already = _is_already_up_to_date(result.get("stdout", ""), result.get("stderr", ""))
after_sha = await _read_head_sha(git_bin, directory)
files_result = await _run_git_async(
    [git_bin, "-C", directory, "diff", "--name-only", f"{before_sha}..{after_sha}"],
    encoding="utf-8",
    timeout=5.0,
)
files_touched = (
    [line for line in files_result.get("stdout", "").splitlines() if line]
    if files_result.get("ok") and before_sha != after_sha
    else []
)
```

Failure conflict handling:

```python
operation = await _detect_conflict_operation(git_bin, directory)
if operation == "rebase":
    reason = ReasonCode.REBASE_CONFLICT
elif operation == "merge":
    reason = ReasonCode.MERGE_CONFLICT
else:
    reason = _classify_remote_error(stderr, stdout)
conflicted_files = (
    await _list_conflicted_files(git_bin, directory)
    if reason in (ReasonCode.REBASE_CONFLICT, ReasonCode.MERGE_CONFLICT)
    else []
)
```

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_git_pull.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/git_pull.py tests/test_git_pull.py
git commit -m "feat(webapi): add git pull endpoint"
```

---

### Task 7: Register routes and add API documentation

**Files:**
- Modify: `tools/webapi/__init__.py`
- Modify: `tests/test_webapi_end_to_end.py`
- Create: `docs/api/webapi-git-pull-push-remote-api.md`
- Modify: `docs/webapi_endpoints_report.md`

**Interfaces:**
- Produces routes:
  - `POST /spcode/git-pull`
  - `POST /spcode/git-push`
  - `POST /spcode/git-remote-set-url`
- Produces handler aliases:
  - `handle_post_git_pull`
  - `handle_post_git_push`
  - `handle_post_git_remote_set_url`

- [ ] **Step 1: Write failing route tests**

Update `tests/test_webapi_end_to_end.py`:

1. Add these paths to the route set:

```python
"/spcode/git-pull",
"/spcode/git-push",
"/spcode/git-remote-set-url",
```

2. Change method counts:

```python
assert methods.count("GET") == 17
assert methods.count("POST") == 34
assert methods.count("PATCH") == 1
assert methods.count("DELETE") == 1
```

3. Change registration count:

```python
assert plugin.context.register_web_api.call_count == 53
```

4. Add a smoke class:

```python
class TestGitRemoteSyncEndpointsSmoke:
    @staticmethod
    def _route_paths() -> set[str]:
        from tools.webapi import ROUTES

        return {route[0] for route in ROUTES}

    def test_git_pull_route_registered(self) -> None:
        from tools.webapi import git_pull

        assert git_pull.handle is not None
        assert "/spcode/git-pull" in self._route_paths()

    def test_git_push_route_registered(self) -> None:
        from tools.webapi import git_push

        assert git_push.handle is not None
        assert "/spcode/git-push" in self._route_paths()

    def test_git_remote_set_url_route_registered(self) -> None:
        from tools.webapi import git_remote_set_url

        assert git_remote_set_url.handle is not None
        assert "/spcode/git-remote-set-url" in self._route_paths()
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_webapi_end_to_end.py -q
```

Expected: route set/count assertions fail because the three routes are not registered.

- [ ] **Step 3: Register modules, routes, aliases, and exports**

In `tools/webapi/__init__.py`:

```python
from . import (
    # existing imports...
    git_pull,
    git_push,
    git_remote_set_url,
)
```

Append to `ROUTES` after `git-conflict-abort`:

```python
    (
        "/spcode/git-pull",
        ["POST"],
        git_pull.handle,
        "git pull --no-edit（支持 ff_only / rebase，禁交互认证）",
    ),
    (
        "/spcode/git-push",
        ["POST"],
        git_push.handle,
        "git push（无 upstream 时自动 -u 设置 upstream）",
    ),
    (
        "/spcode/git-remote-set-url",
        ["POST"],
        git_remote_set_url.handle,
        "upsert git remote URL（存在则 set-url，不存在则 add）",
    ),
```

Append to `HANDLERS`:

```python
    "handle_post_git_pull": git_pull.handle,
    "handle_post_git_push": git_push.handle,
    "handle_post_git_remote_set_url": git_remote_set_url.handle,
```

Append to `__all__`:

```python
    "git_pull",
    "git_push",
    "git_remote_set_url",
```

Update the `register_webapi_routes` docstring route count from 50 to 53 and add the 2026-08-12 route-count line.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/test_webapi_end_to_end.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Add frontend API documentation**

Create `docs/api/webapi-git-pull-push-remote-api.md` with:

- Overview and common envelope
- `POST /spcode/git-pull` request body, success/no-op/conflict examples, ReasonCode table
- `POST /spcode/git-push` request body, first-push/no-op/non-fast-forward examples, ReasonCode table
- `POST /spcode/git-remote-set-url` request body, added/updated/unchanged examples, ReasonCode table
- TypeScript interfaces and `fetch` snippets
- Note that auth is non-interactive and force push is unsupported

Update `docs/webapi_endpoints_report.md` to list the three new routes and route count 53.

- [ ] **Step 6: Verify documentation and commit**

Run:

```powershell
git diff --check
```

Expected: exit code 0.

Commit:

```powershell
git add tools/webapi/__init__.py tests/test_webapi_end_to_end.py docs/api/webapi-git-pull-push-remote-api.md docs/webapi_endpoints_report.md
git commit -m "feat(webapi): register git remote sync endpoints"
```

If `docs/` files are ignored, force-add only the two documentation files explicitly:

```powershell
git add -f docs/api/webapi-git-pull-push-remote-api.md docs/webapi_endpoints_report.md
```

---

### Task 8: Final verification and completion

**Files:**
- No new source changes unless verification exposes a defect.

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified local branch and final report.

- [ ] **Step 1: Run focused tests**

```powershell
python -m pytest tests/test_git_remote_helpers.py tests/test_git_remote_set_url.py tests/test_git_push.py tests/test_git_pull.py tests/test_git_conflict_helpers.py tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py tests/test_reason_code.py tests/test_webapi_end_to_end.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run lint**

Use the built-in `code_check` tool on every new or modified Python file:

```text
tools/webapi/_helpers.py
tools/webapi/_git_remote.py
tools/webapi/git_conflict_continue.py
tools/webapi/git_conflict_abort.py
tools/webapi/git_remote_set_url.py
tools/webapi/git_push.py
tools/webapi/git_pull.py
tools/webapi/__init__.py
tests/test_git_remote_helpers.py
tests/test_git_remote_set_url.py
tests/test_git_push.py
tests/test_git_pull.py
tests/test_git_conflict_helpers.py
tests/test_git_conflict_continue.py
tests/test_git_conflict_abort.py
tests/test_reason_code.py
tests/test_webapi_end_to_end.py
```

Expected: no errors.

- [ ] **Step 3: Run complete test suite**

```powershell
python -m pytest tests/ -q
```

Expected: all tests pass. If unrelated pre-existing failures occur, rerun the focused tests and report both results explicitly.

- [ ] **Step 4: Check diff and unrelated changes**

```powershell
git status --short --branch
git diff --stat
git diff --check
```

Expected: only files owned by this feature are modified on the feature branch; no whitespace errors.

- [ ] **Step 5: Report evidence**

Report:

- Worktree path and branch
- Each commit hash and subject
- Focused test output summary
- Full test output summary
- Lint result
- Any unrelated pre-existing failures or dirty files
- Confirmation that nothing was pushed and no PR was created

---

## Self-Review

- Spec coverage: Tasks 2–8 cover rebase lifecycle, helper utilities, all three endpoints, route registration, API docs, and final verification.
- Placeholder scan: no `TBD`, placeholder implementation, or deferred validation step is present.
- Type consistency: helper names and handler signatures match the spec and are reused consistently across tasks.
- Worktree policy: Task 1 establishes isolation before source changes, per `using-git-worktrees`.
- TDD policy: every production behavior has a RED step before implementation.
