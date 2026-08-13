# Git Commit Amend Web API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /spcode/git-commit-amend` so the Dashboard can modify the current HEAD commit message safely.

**Architecture:** One atomic POST endpoint that reuses `git_commit._build_git_env` and `_classify_commit_error`, plus the existing preflight, conflict detection, and staged-file helpers. It rejects empty repositories, in-progress conflict operations, merge commits, and staged changes before invoking `git commit --amend -F -`.

**Tech Stack:** Python 3.10+, AstrBot Web API, pytest, Git CLI.

**Spec:** `docs/superpowers/specs/2026-08-13-git-commit-amend-design.md`
**Author:** elecvoid243 @ 2026-08-13 11:15 CST

## Global Constraints

- Only modify `message`; no `--reset-author`, `--no-verify`, parent, or signoff options.
- Reject staged changes, merge commits, and in-progress conflict operations.
- Reuse `_build_git_env`, `_classify_commit_error`, `_git_endpoint_preflight`, `_get_staged_files`, and `_detect_conflict_operation`.
- Message limit matches `git_commit.MAX_MESSAGE_LENGTH` (8192).
- Run TDD: write failing tests first, verify the expected failure, then implement.
- Do not modify `README.md`, `AGENTS.md`, or `metadata.yaml`.
- Commit only task-owned files. No push and no PR.

---

### Task 1: Create isolated worktree and verify baseline

**Files:**
- No source files modified.

**Interfaces:**
- Produces: clean isolated worktree.

- [ ] **Step 1: Detect isolation**

```powershell
$gitDir = (Resolve-Path (git rev-parse --git-dir)).Path
$gitCommon = (Resolve-Path (git rev-parse --git-common-dir)).Path
git branch --show-current
git check-ignore -v .worktrees
```

Expected: normal checkout on `main`; `.worktrees` ignored.

- [ ] **Step 2: Create worktree**

```powershell
git worktree add .worktrees/git-commit-amend -b feat/git-commit-amend
```

- [ ] **Step 3: Run baseline focused tests**

```powershell
Set-Location .worktrees/git-commit-amend
$env:PYTHONPATH = 'F:\github\Astrbot'
D:\anaconda3\python.exe -m pytest tests/test_git_commit.py tests/test_git_commit_amend.py tests/test_reason_code.py tests/test_webapi_end_to_end.py -q
```

If `tests/test_git_commit_amend.py` does not exist yet, run only the existing three files and expect pass. Restore `PYTHONPATH` after.

- [ ] **Step 4: Confirm clean baseline**

```powershell
git status --short --branch
```

---

### Task 2: Add ReasonCodes

**Files:**
- Modify: `tools/webapi/_helpers.py`
- Test: `tests/test_reason_code.py`

**Interfaces:**
- Produces: `ReasonCode.CANNOT_AMEND_MERGE_COMMIT`, `STAGED_CHANGES_PRESENT`, `AMEND_FAILED`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_reason_code.py`:

```python
def test_git_commit_amend_reason_codes_defined():
    assert ReasonCode.CANNOT_AMEND_MERGE_COMMIT == "cannot_amend_merge_commit"
    assert ReasonCode.STAGED_CHANGES_PRESENT == "staged_changes_present"
    assert ReasonCode.AMEND_FAILED == "amend_failed"
```

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_reason_code.py -q
```

Expected: `AttributeError` for `CANNOT_AMEND_MERGE_COMMIT`.

- [ ] **Step 3: Implement**

Add to `ReasonCode` after the code-check/code-format section:

```python
    # ── git commit amend(2026-08-13) ──
    CANNOT_AMEND_MERGE_COMMIT = "cannot_amend_merge_commit"
    STAGED_CHANGES_PRESENT = "staged_changes_present"
    AMEND_FAILED = "amend_failed"
```

- [ ] **Step 4: Verify GREEN and lint**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_reason_code.py -q
```

Expected: all pass. `code_check` both files.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/_helpers.py tests/test_reason_code.py
git commit -m "feat(webapi): add git commit amend reason codes"
```

---

### Task 3: Implement `POST /spcode/git-commit-amend`

**Files:**
- Create: `tools/webapi/git_commit_amend.py`
- Test: `tests/test_git_commit_amend.py`

**Interfaces:**
- Produces: `handle(plugin, *, umo=None, worktree=None, body=None) -> dict`.

- [ ] **Step 1: Write failing endpoint tests**

Create `tests/test_git_commit_amend.py` with a real temporary repo:

```python
"""Unit/integration tests for POST /spcode/git-commit-amend."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from tools.project import state as _proj_state
from tools.webapi.git_commit_amend import handle

from tests.conftest import _make_plugin

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
    (path / "README.md").write_text("hi", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


def _load(path: Path) -> str:
    umo = "u:amend"
    _proj_state.put(umo, {"directory": str(path), "loaded_at": time.time()})
    return umo


async def test_amend_replaces_message(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    before = _git(repo, "rev-parse", "HEAD")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "fix: new message"})

    assert result["data"]["success"] is True
    assert result["data"]["amended"] is True
    assert result["data"]["before_sha"] == before
    assert result["data"]["after_sha"] != before
    assert _git(repo, "log", "-1", "--pretty=%s") == "fix: new message"


async def test_amend_multiline_message(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(
        plugin,
        umo=umo,
        body={"message": "fix: subject\n\nbody line"},
    )

    assert result["data"]["success"] is True
    assert result["data"]["subject"] == "fix: subject"
    assert "body line" in result["data"]["message"]


async def test_empty_repository(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "x"})

    assert result["data"]["reason"] == "empty_repository"


async def test_staged_changes_rejected(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "a.txt")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "x"})

    assert result["data"]["reason"] == "staged_changes_present"


async def test_merge_commit_rejected(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "f.txt").write_text("f", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "feature")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature", "-q")
    plugin = _make_plugin()
    umo = _load(repo)

    result = await handle(plugin, umo=umo, body={"message": "x"})

    assert result["data"]["reason"] == "cannot_amend_merge_commit"


async def test_invalid_message():
    plugin = _make_plugin()
    result = await handle(plugin, body={"message": ""})
    assert result["data"]["reason"] == "invalid_message"
```

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_git_commit_amend.py -q
```

Expected: `ModuleNotFoundError` for `tools.webapi.git_commit_amend`.

- [ ] **Step 3: Implement endpoint**

Create `tools/webapi/git_commit_amend.py`:

- Import `_build_git_env`, `_classify_commit_error`, `MAX_MESSAGE_LENGTH`, `COMMIT_TRUNCATE_BYTES` from `.git_commit`.
- Import `_detect_conflict_operation`, `_get_staged_files`, `_git_endpoint_preflight`, `_make_envelope`, `_JSONResponseCompat`, `_run_git_async`, `ReasonCode`.
- Validate `body` and `message` exactly like `git_commit.handle`.
- Preflight.
- Empty-repo check with `git rev-parse --verify HEAD`.
- Conflict check via `_detect_conflict_operation`.
- Merge-commit check via `git rev-list --parents -n 1 HEAD`.
- Staged check via `_get_staged_files`.
- Record `before_sha` via `git rev-parse HEAD`.
- Run:

```python
args = [
    git_bin,
    "-C",
    directory,
    "-c",
    "color.ui=never",
    "commit",
    "--amend",
    "-F",
    "-",
]
result = await _run_git_async(
    args,
    encoding="utf-8",
    input_text=message,
    env=_build_git_env(),
    timeout=30.0,
)
```

- On failure classify with `_classify_commit_error`; fallback `AMEND_FAILED` for unclassified.
- On success read `after_sha`, `git log -1 --pretty=%s`, and `git log -1 --pretty=%B`.
- Return `amended=True`, before/after SHA, subject, message, `files_changed=[]`, directory/umo/worktree.

- [ ] **Step 4: Verify GREEN and lint**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_git_commit_amend.py -q
```

Expected: all pass. `code_check` endpoint and test files.

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/git_commit_amend.py tests/test_git_commit_amend.py
git commit -m "feat(webapi): add git commit amend endpoint"
```

---

### Task 4: Register route and add API documentation

**Files:**
- Modify: `tools/webapi/__init__.py`
- Modify: `tests/test_webapi_end_to_end.py`
- Create: `docs/api/webapi-git-commit-amend-api.md`

**Interfaces:**
- Route: `POST /spcode/git-commit-amend`
- Handler alias: `handle_post_git_commit_amend`

- [ ] **Step 1: Write failing route tests**

Update `tests/test_webapi_end_to_end.py`:

```python
"/spcode/git-commit-amend",
```

Change POST count:

```python
assert methods.count("POST") == 37
```

Change registration count:

```python
assert plugin.context.register_web_api.call_count == 56
```

Add smoke test importing `git_commit_amend` and asserting the route path exists.

- [ ] **Step 2: Verify RED**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_webapi_end_to_end.py -q
```

Expected: route set/count assertions fail.

- [ ] **Step 3: Register route**

In `tools/webapi/__init__.py`:

- Import `git_commit_amend`.
- Append to `ROUTES` after `git-commit`:

```python
(
    "/spcode/git-commit-amend",
    ["POST"],
    git_commit_amend.handle,
    "修改当前 HEAD 提交信息（拒绝 staged / merge commit / 冲突中）",
),
```

- Append `handle_post_git_commit_amend` to `HANDLERS`, `git_commit_amend` to `__all__`.
- Update route-count docstring to 56.

- [ ] **Step 4: Verify GREEN**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_webapi_end_to_end.py -q
```

- [ ] **Step 5: Add API documentation**

Create `docs/api/webapi-git-commit-amend-api.md` with request/response examples, ReasonCode table, TypeScript interface, and fetch snippet.

- [ ] **Step 6: Commit**

```powershell
git add tools/webapi/__init__.py tests/test_webapi_end_to_end.py
git add -f docs/api/webapi-git-commit-amend-api.md
git commit -m "feat(webapi): register git commit amend endpoint"
```

---

### Task 5: Final verification

**Files:**
- No new source files unless verification exposes a defect.

- [ ] **Step 1: Run focused tests**

```powershell
D:\anaconda3\python.exe -m pytest tests/test_git_commit.py tests/test_git_commit_amend.py tests/test_reason_code.py tests/test_webapi_end_to_end.py -q
```

Expected: all pass.

- [ ] **Step 2: Lint new/modified files**

Use built-in `code_check` on:

- `tools/webapi/git_commit_amend.py`
- `tools/webapi/_helpers.py`
- `tools/webapi/__init__.py`
- `tests/test_git_commit_amend.py`
- `tests/test_reason_code.py`
- `tests/test_webapi_end_to_end.py`

Expected: no issues except documented pre-existing issues in `tests/test_webapi_end_to_end.py`.

- [ ] **Step 3: Check status and diff**

```powershell
git status --short --branch
git diff --check
git log --oneline main..HEAD
```

Expected: clean worktree; all task commits present.

- [ ] **Step 4: Report evidence**

Report worktree path, commits, focused test result, lint result, route count 56, and confirmation that nothing was pushed.

---

## Self-Review

- Spec coverage: all endpoint, safety, error, route, and documentation requirements map to tasks.
- Placeholder scan: no TBD or deferred step.
- Type consistency: endpoint and handler names match across tasks.
- Compatibility: existing `git-commit` behavior unchanged.
- TDD: every production task starts with failing tests.
