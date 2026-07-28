# Git Merge / Cherry-pick / Conflict Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 new webapi endpoints (git-merge, git-cherry-pick, git-conflict-status, git-conflict-resolve, git-conflict-continue, git-conflict-abort) plus a git-status field extension, enabling full merge/cherry-pick/conflict lifecycle management from the Dashboard.

**Architecture:** Each endpoint is a standalone `tools/webapi/<name>.py` module with an `async def handle(plugin, ...)` handler, registered in `ROUTES`. Shared conflict infrastructure (operation detection, hunk parsing, three-way read, stderr classifiers) lives in `tools/webapi/_helpers.py`. All endpoints reuse the existing 5-step `_git_endpoint_preflight` defense chain and `_make_envelope` response format.

**Tech Stack:** Python 3.10+, asyncio subprocess (`_run_git_async`), pytest, ruff

**Spec:** `docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md`

## Global Constraints

- Python 3.10+ (built-in generics: `list[str]`, `X | None`)
- All handlers: `async def handle(plugin: "SPCodeToolkit", *, umo: str | None = None, worktree: str | None = None, body: dict | None = None) -> dict`
- GET handlers omit `body` parameter
- Response envelope: `_make_envelope(success=..., reason=..., elapsed_ms=..., **data_fields)`
- Success responses wrapped in `_JSONResponseCompat(..., status_code=200)`
- All git calls via `_run_git_async` (never `subprocess.run` directly in webapi layer)
- Windows: `_NO_WINDOW_KWARGS` already handled by `_run_git_async`
- Path defense: `_validate_repo_relative_file` for user-supplied file paths
- Ref validation: `_is_valid_ref_name` from `tools._helpers`
- Commit ref check: `_is_commit_ref` from `tools._helpers`
- GIT_AUTHOR_* env passthrough: `_build_git_env()` from `tools.webapi.git_commit`
- Post-mutation branch state: `_read_post_mutation_branch_state` from `_helpers`
- Ruff lint clean (`ruff check .`) before every commit
- Test command: `pytest tests/test_<module>.py -v`
- Commit message format: `feat(webapi): <description>` or `test(webapi): <description>`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `tools/webapi/git_merge.py` | POST /spcode/git-merge handler |
| `tools/webapi/git_cherry_pick.py` | POST /spcode/git-cherry-pick handler |
| `tools/webapi/git_conflict_status.py` | GET /spcode/git-conflict-status handler |
| `tools/webapi/git_conflict_resolve.py` | POST /spcode/git-conflict-resolve handler |
| `tools/webapi/git_conflict_continue.py` | POST /spcode/git-conflict-continue handler |
| `tools/webapi/git_conflict_abort.py` | POST /spcode/git-conflict-abort handler |
| `tests/test_git_merge.py` | Unit tests for git-merge |
| `tests/test_git_cherry_pick.py` | Unit tests for git-cherry-pick |
| `tests/test_git_conflict_status.py` | Unit tests for git-conflict-status |
| `tests/test_git_conflict_resolve.py` | Unit tests for git-conflict-resolve |
| `tests/test_git_conflict_continue.py` | Unit tests for git-conflict-continue |
| `tests/test_git_conflict_abort.py` | Unit tests for git-conflict-abort |
| `tests/test_git_merge_cherry_pick_e2e.py` | E2E lifecycle test |

### Modified Files

| File | Change |
|------|--------|
| `tools/webapi/_helpers.py` | +9 ReasonCode, +7 helpers, +ConflictHunk dataclass |
| `tools/webapi/__init__.py` | +6 ROUTES entries, +6 HANDLERS aliases, +6 imports |
| `tools/webapi/git_status.py` | +operation/in_conflict fields in response |
| `tests/test_webapi_end_to_end.py` | Route count assertion 40 → 46 |

---

## Task 1: ReasonCode + ConflictHunk dataclass

**Files:**
- Modify: `tools/webapi/_helpers.py` (ReasonCode class, append after VIVADO section)
- Test: `tests/test_git_conflict_helpers.py` (new file, shared across Tasks 1-3)

**Interfaces:**
- Produces: `ReasonCode.MERGE_CONFLICT`, `ReasonCode.MERGE_ALREADY_UP_TO_DATE`, `ReasonCode.UNRELATED_HISTORIES`, `ReasonCode.CHERRY_PICK_CONFLICT`, `ReasonCode.CHERRY_PICK_EMPTY`, `ReasonCode.OPERATION_IN_PROGRESS`, `ReasonCode.NO_CONFLICT_IN_PROGRESS`, `ReasonCode.FILE_NOT_CONFLICTED`, `ReasonCode.UNRESOLVED_CONFLICTS_REMAIN`
- Produces: `ConflictHunk` dataclass with fields: `index: int`, `start_line: int`, `end_line: int`, `ours: str`, `theirs: str`, `base: str | None`, `ours_label: str`, `theirs_label: str`

- [ ] **Step 1: Write failing tests for ReasonCode constants**

```python
# tests/test_git_conflict_helpers.py
"""Tests for v2.22.0 conflict helpers in tools/webapi/_helpers.py."""

import pytest


class TestReasonCodeConflict:
    """Verify new ReasonCode constants exist and have correct string values."""

    def test_merge_conflict(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.MERGE_CONFLICT == "merge_conflict"

    def test_merge_already_up_to_date(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.MERGE_ALREADY_UP_TO_DATE == "merge_already_up_to_date"

    def test_unrelated_histories(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.UNRELATED_HISTORIES == "unrelated_histories"

    def test_cherry_pick_conflict(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.CHERRY_PICK_CONFLICT == "cherry_pick_conflict"

    def test_cherry_pick_empty(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.CHERRY_PICK_EMPTY == "cherry_pick_empty"

    def test_operation_in_progress(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.OPERATION_IN_PROGRESS == "operation_in_progress"

    def test_no_conflict_in_progress(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.NO_CONFLICT_IN_PROGRESS == "no_conflict_in_progress"

    def test_file_not_conflicted(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.FILE_NOT_CONFLICTED == "file_not_conflicted"

    def test_unresolved_conflicts_remain(self):
        from tools.webapi._helpers import ReasonCode

        assert ReasonCode.UNRESOLVED_CONFLICTS_REMAIN == "unresolved_conflicts_remain"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_conflict_helpers.py::TestReasonCodeConflict -v`
Expected: FAIL with `AttributeError: MERGE_CONFLICT`

- [ ] **Step 3: Implement ReasonCode additions**

Append to `tools/webapi/_helpers.py` inside the `ReasonCode` class, after the `INTERNAL_ERROR` line:

```python
    # ── v2.22.0 新增：git-merge / cherry-pick / conflict（2026-07-28）──
    # git-merge (3)
    MERGE_CONFLICT = "merge_conflict"
    MERGE_ALREADY_UP_TO_DATE = "merge_already_up_to_date"
    UNRELATED_HISTORIES = "unrelated_histories"

    # git-cherry-pick (2)
    CHERRY_PICK_CONFLICT = "cherry_pick_conflict"
    CHERRY_PICK_EMPTY = "cherry_pick_empty"

    # conflict lifecycle (4)
    OPERATION_IN_PROGRESS = "operation_in_progress"
    NO_CONFLICT_IN_PROGRESS = "no_conflict_in_progress"
    FILE_NOT_CONFLICTED = "file_not_conflicted"
    UNRESOLVED_CONFLICTS_REMAIN = "unresolved_conflicts_remain"
```

- [ ] **Step 4: Write failing test for ConflictHunk**

Append to `tests/test_git_conflict_helpers.py`:

```python
class TestConflictHunk:
    """Verify ConflictHunk dataclass structure."""

    def test_fields(self):
        from tools.webapi._helpers import ConflictHunk

        hunk = ConflictHunk(
            index=0,
            start_line=12,
            end_line=18,
            ours="ours text\n",
            theirs="theirs text\n",
            base="base text\n",
            ours_label="HEAD",
            theirs_label="feature/x",
        )
        assert hunk.index == 0
        assert hunk.start_line == 12
        assert hunk.end_line == 18
        assert hunk.ours == "ours text\n"
        assert hunk.theirs == "theirs text\n"
        assert hunk.base == "base text\n"
        assert hunk.ours_label == "HEAD"
        assert hunk.theirs_label == "feature/x"

    def test_base_optional(self):
        from tools.webapi._helpers import ConflictHunk

        hunk = ConflictHunk(
            index=1,
            start_line=5,
            end_line=9,
            ours="a\n",
            theirs="b\n",
            base=None,
            ours_label="HEAD",
            theirs_label="main",
        )
        assert hunk.base is None
```

- [ ] **Step 5: Implement ConflictHunk dataclass**

Add near the top of `tools/webapi/_helpers.py` (after imports, before `_NO_WINDOW_KWARGS`):

```python
from dataclasses import dataclass


@dataclass
class ConflictHunk:
    """Parsed conflict hunk from a file with <<<<<<< markers.

    Fields:
        index: 0-based hunk ordinal within the file.
        start_line: 1-based line number of the <<<<<<< line.
        end_line: 1-based line number of the >>>>>>> line.
        ours: Content between <<<<<<< and ======= (or |||||||).
        theirs: Content between ======= and >>>>>>>.
        base: Content between ||||||| and ======= (diff3 format); None for standard format.
        ours_label: Label after <<<<<<< (e.g. "HEAD").
        theirs_label: Label after >>>>>>> (e.g. "feature/login").
    """

    index: int
    start_line: int
    end_line: int
    ours: str
    theirs: str
    base: str | None
    ours_label: str
    theirs_label: str
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_git_conflict_helpers.py -v`
Expected: All PASS

- [ ] **Step 7: Lint check**

Run: `ruff check tools/webapi/_helpers.py tests/test_git_conflict_helpers.py`
Expected: No errors

- [ ] **Step 8: Commit**

```bash
git add tools/webapi/_helpers.py tests/test_git_conflict_helpers.py
git commit -m "feat(webapi): add v2.22.0 ReasonCode constants + ConflictHunk dataclass"
```

---

## Task 2: _detect_conflict_operation + _list_conflicted_files + _read_operation_ref

**Files:**
- Modify: `tools/webapi/_helpers.py` (append 3 async helpers)
- Test: `tests/test_git_conflict_helpers.py` (append test classes)

**Interfaces:**
- Consumes: `_run_git_async` (same module)
- Produces: `_detect_conflict_operation(git_bin: str, directory: str) -> str | None` — returns `"merge"` | `"cherry_pick"` | `"revert"` | `None`
- Produces: `_list_conflicted_files(git_bin: str, directory: str) -> list[dict]` — returns `[{"path": str, "status": str}]`
- Produces: `_read_operation_ref(git_bin: str, directory: str, operation: str) -> tuple[str, str]` — returns `(sha, subject)`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_git_conflict_helpers.py`:

```python
from unittest.mock import AsyncMock, patch


class TestDetectConflictOperation:
    """Tests for _detect_conflict_operation."""

    @pytest.mark.asyncio
    async def test_no_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result is None

    @pytest.mark.asyncio
    async def test_merge_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "merge"

    @pytest.mark.asyncio
    async def test_cherry_pick_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "CHERRY_PICK_HEAD").write_text("def456\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "cherry_pick"

    @pytest.mark.asyncio
    async def test_revert_conflict(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "REVERT_HEAD").write_text("789abc\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "revert"

    @pytest.mark.asyncio
    async def test_git_dir_relative(self, tmp_path):
        """git rev-parse --git-dir may return relative '.git'."""
        from tools.webapi._helpers import _detect_conflict_operation

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc\n")

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": ".git", "stderr": "", "code": 0},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result == "merge"

    @pytest.mark.asyncio
    async def test_git_dir_probe_fails(self, tmp_path):
        from tools.webapi._helpers import _detect_conflict_operation

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": False, "stdout": "", "stderr": "fatal", "code": 128},
        ):
            result = await _detect_conflict_operation("git", str(tmp_path))
        assert result is None


class TestListConflictedFiles:
    """Tests for _list_conflicted_files."""

    @pytest.mark.asyncio
    async def test_no_conflicts(self):
        from tools.webapi._helpers import _list_conflicted_files

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": "", "stderr": "", "code": 0},
        ):
            result = await _list_conflicted_files("git", "/repo")
        assert result == []

    @pytest.mark.asyncio
    async def test_two_conflicted_files(self):
        from tools.webapi._helpers import _list_conflicted_files

        porcelain = "UU src/auth.py\nAU src/config.py\n M src/ok.py\n"
        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": porcelain, "stderr": "", "code": 0},
        ):
            result = await _list_conflicted_files("git", "/repo")
        assert len(result) == 2
        assert result[0] == {"path": "src/auth.py", "status": "UU"}
        assert result[1] == {"path": "src/config.py", "status": "AU"}


class TestReadOperationRef:
    """Tests for _read_operation_ref."""

    @pytest.mark.asyncio
    async def test_reads_merge_head(self, tmp_path):
        from tools.webapi._helpers import _read_operation_ref

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "MERGE_HEAD").write_text("abc123def456\n")

        async def mock_run(args, **kwargs):
            if "log" in args:
                return {"ok": True, "stdout": "feat: add login", "stderr": "", "code": 0}
            return {"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0}

        with patch("tools.webapi._helpers._run_git_async", side_effect=mock_run):
            sha, subject = await _read_operation_ref("git", str(tmp_path), "merge")
        assert sha == "abc123def456"
        assert subject == "feat: add login"

    @pytest.mark.asyncio
    async def test_missing_sentinel_returns_empty(self, tmp_path):
        from tools.webapi._helpers import _read_operation_ref

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        with patch(
            "tools.webapi._helpers._run_git_async",
            new_callable=AsyncMock,
            return_value={"ok": True, "stdout": str(git_dir), "stderr": "", "code": 0},
        ):
            sha, subject = await _read_operation_ref("git", str(tmp_path), "merge")
        assert sha == ""
        assert subject == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_conflict_helpers.py::TestDetectConflictOperation -v`
Expected: FAIL with `ImportError: cannot import name '_detect_conflict_operation'`

- [ ] **Step 3: Implement _detect_conflict_operation**

Append to `tools/webapi/_helpers.py`:

```python
# ── v2.22.0 conflict helpers ──────────────────────────────────────────


async def _detect_conflict_operation(git_bin: str, directory: str) -> str | None:
    """Detect which conflict operation is in progress.

    Checks sentinel files in the git directory:
      MERGE_HEAD → "merge"
      CHERRY_PICK_HEAD → "cherry_pick"
      REVERT_HEAD → "revert"

    Returns:
        "merge" | "cherry_pick" | "revert" | None
    """
    result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--git-dir"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        return None
    git_dir = Path(result["stdout"].strip())
    if not git_dir.is_absolute():
        git_dir = Path(directory) / git_dir

    if (git_dir / "MERGE_HEAD").exists():
        return "merge"
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return "cherry_pick"
    if (git_dir / "REVERT_HEAD").exists():
        return "revert"
    return None
```

- [ ] **Step 4: Implement _list_conflicted_files**

Append to `tools/webapi/_helpers.py`:

```python
# Unmerged XY status codes (X or Y is 'U', or both are 'A'/'D' combos)
_UNMERGED_XY = frozenset({"UU", "AA", "DD", "AU", "UA", "DU", "UD"})


async def _list_conflicted_files(git_bin: str, directory: str) -> list[dict]:
    """List unmerged (conflicted) files with their porcelain XY status.

    Returns:
        [{"path": "src/main.py", "status": "UU"}, ...]
    """
    result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        return []
    conflicted = []
    for line in result.get("stdout", "").splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        if xy in _UNMERGED_XY:
            path = line[3:]
            conflicted.append({"path": path, "status": xy})
    return conflicted
```

- [ ] **Step 5: Implement _read_operation_ref**

Append to `tools/webapi/_helpers.py`:

```python
_SENTINEL_BY_OPERATION = {
    "merge": "MERGE_HEAD",
    "cherry_pick": "CHERRY_PICK_HEAD",
    "revert": "REVERT_HEAD",
}


async def _read_operation_ref(
    git_bin: str, directory: str, operation: str
) -> tuple[str, str]:
    """Read the sentinel file SHA and commit subject for the given operation.

    Returns:
        (sha, subject) — both empty strings if sentinel missing or unreadable.
    """
    sentinel_name = _SENTINEL_BY_OPERATION.get(operation)
    if not sentinel_name:
        return "", ""

    result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--git-dir"],
        encoding="utf-8",
        timeout=5.0,
    )
    if not result.get("ok"):
        return "", ""
    git_dir = Path(result["stdout"].strip())
    if not git_dir.is_absolute():
        git_dir = Path(directory) / git_dir

    sentinel = git_dir / sentinel_name
    if not sentinel.exists():
        return "", ""
    sha = sentinel.read_text(encoding="utf-8").strip()

    subject_result = await _run_git_async(
        [git_bin, "-C", directory, "log", "-1", "--pretty=%s", sha],
        encoding="utf-8",
        timeout=5.0,
    )
    subject = subject_result.get("stdout", "").strip() if subject_result.get("ok") else ""
    return sha, subject
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_git_conflict_helpers.py -v`
Expected: All PASS

- [ ] **Step 7: Lint + commit**

```bash
ruff check tools/webapi/_helpers.py tests/test_git_conflict_helpers.py
git add tools/webapi/_helpers.py tests/test_git_conflict_helpers.py
git commit -m "feat(webapi): add conflict detection helpers (_detect/_list/_read_operation_ref)"
```

---

## Task 3: _parse_conflict_hunks + _read_three_way + stderr classifiers

**Files:**
- Modify: `tools/webapi/_helpers.py` (append 4 functions)
- Test: `tests/test_git_conflict_helpers.py` (append test classes)

**Interfaces:**
- Produces: `_parse_conflict_hunks(content: str) -> list[ConflictHunk]`
- Produces: `_read_three_way(git_bin: str, directory: str, file: str) -> dict` — returns `{"base": str|None, "ours": str|None, "theirs": str|None, "truncated": bool}`
- Produces: `_classify_merge_stderr(stderr: str, stdout: str = "") -> str`
- Produces: `_classify_cherry_pick_stderr(stderr: str) -> str`

- [ ] **Step 1: Write failing tests for _parse_conflict_hunks**

Append to `tests/test_git_conflict_helpers.py`:

```python
class TestParseConflictHunks:
    """Tests for _parse_conflict_hunks."""

    def test_standard_format_single_hunk(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "line1\n"
            "<<<<<<< HEAD\n"
            "ours content\n"
            "=======\n"
            "theirs content\n"
            ">>>>>>> feature/login\n"
            "line7\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert len(hunks) == 1
        h = hunks[0]
        assert h.index == 0
        assert h.start_line == 2
        assert h.end_line == 6
        assert h.ours == "ours content\n"
        assert h.theirs == "theirs content\n"
        assert h.base is None
        assert h.ours_label == "HEAD"
        assert h.theirs_label == "feature/login"

    def test_diff3_format(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "<<<<<<< HEAD\n"
            "ours\n"
            "||||||| merged common ancestors\n"
            "base\n"
            "=======\n"
            "theirs\n"
            ">>>>>>> branch-b\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert len(hunks) == 1
        h = hunks[0]
        assert h.ours == "ours\n"
        assert h.base == "base\n"
        assert h.theirs == "theirs\n"
        assert h.ours_label == "HEAD"
        assert h.theirs_label == "branch-b"

    def test_multiple_hunks(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "a\n"
            "<<<<<<< HEAD\n"
            "o1\n"
            "=======\n"
            "t1\n"
            ">>>>>>> b\n"
            "mid\n"
            "<<<<<<< HEAD\n"
            "o2\n"
            "=======\n"
            "t2\n"
            ">>>>>>> b\n"
            "end\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert len(hunks) == 2
        assert hunks[0].index == 0
        assert hunks[0].ours == "o1\n"
        assert hunks[1].index == 1
        assert hunks[1].ours == "o2\n"
        assert hunks[1].start_line == 8

    def test_no_conflicts(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        assert _parse_conflict_hunks("clean file\nno markers\n") == []

    def test_empty_content(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        assert _parse_conflict_hunks("") == []

    def test_binary_content_returns_empty(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        assert _parse_conflict_hunks("abc\x00def") == []

    def test_multiline_ours_theirs(self):
        from tools.webapi._helpers import _parse_conflict_hunks

        content = (
            "<<<<<<< HEAD\n"
            "line1\n"
            "line2\n"
            "=======\n"
            "line3\n"
            "line4\n"
            "line5\n"
            ">>>>>>> other\n"
        )
        hunks = _parse_conflict_hunks(content)
        assert hunks[0].ours == "line1\nline2\n"
        assert hunks[0].theirs == "line3\nline4\nline5\n"
```

- [ ] **Step 2: Write failing tests for classifiers**

Append to `tests/test_git_conflict_helpers.py`:

```python
class TestClassifyMergeStderr:
    """Tests for _classify_merge_stderr."""

    def test_conflict(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert _classify_merge_stderr("CONFLICT (content): Merge conflict in f.py") == ReasonCode.MERGE_CONFLICT

    def test_unrelated_histories(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert _classify_merge_stderr("fatal: refusing to merge unrelated histories") == ReasonCode.UNRELATED_HISTORIES

    def test_unknown_revision(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert _classify_merge_stderr("merge: bogus - not something we can merge") == ReasonCode.REF_NOT_FOUND

    def test_local_changes(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert _classify_merge_stderr("error: Your local changes would be overwritten") == ReasonCode.WORKTREE_DIRTY

    def test_already_up_to_date_in_stdout(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert _classify_merge_stderr("", stdout="Already up to date.") == ReasonCode.MERGE_ALREADY_UP_TO_DATE

    def test_generic_error(self):
        from tools.webapi._helpers import ReasonCode, _classify_merge_stderr

        assert _classify_merge_stderr("fatal: something weird") == ReasonCode.GIT_ERROR


class TestClassifyCherryPickStderr:
    """Tests for _classify_cherry_pick_stderr."""

    def test_conflict(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert _classify_cherry_pick_stderr("CONFLICT (content): Merge conflict in x.py") == ReasonCode.CHERRY_PICK_CONFLICT

    def test_empty(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert _classify_cherry_pick_stderr("The previous cherry-pick is now empty") == ReasonCode.CHERRY_PICK_EMPTY

    def test_bad_object(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert _classify_cherry_pick_stderr("fatal: bad object abc123") == ReasonCode.COMMIT_NOT_FOUND

    def test_dirty(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert _classify_cherry_pick_stderr("error: Your local changes would be overwritten") == ReasonCode.WORKTREE_DIRTY

    def test_generic(self):
        from tools.webapi._helpers import ReasonCode, _classify_cherry_pick_stderr

        assert _classify_cherry_pick_stderr("fatal: unknown") == ReasonCode.GIT_ERROR
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_git_conflict_helpers.py::TestParseConflictHunks -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Implement _parse_conflict_hunks**

Append to `tools/webapi/_helpers.py`:

```python
def _parse_conflict_hunks(content: str) -> list[ConflictHunk]:
    """Parse conflict markers from file content into structured hunks.

    Supports both standard (merge) and diff3 conflict styles.
    Returns empty list for binary content, empty content, or no markers.
    """
    if not content or "\x00" in content:
        return []

    lines = content.split("\n")
    # Rejoin with \n for consistent line handling; track 1-based line numbers
    hunks: list[ConflictHunk] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if line.startswith("<<<<<<< "):
            ours_label = line[8:].strip()
            start_line = i + 1  # 1-based
            ours_parts: list[str] = []
            base_parts: list[str] = []
            theirs_parts: list[str] = []
            base: str | None = None
            theirs_label = ""
            # State: ours -> base (optional) -> theirs
            state = "ours"
            j = i + 1
            while j < n:
                l = lines[j]
                if l.startswith("||||||| ") and state == "ours":
                    state = "base"
                    j += 1
                    continue
                if l.startswith("=======") and state in ("ours", "base"):
                    if state == "base":
                        base = "".join(base_parts)
                    state = "theirs"
                    j += 1
                    continue
                if l.startswith(">>>>>>> "):
                    theirs_label = l[8:].strip()
                    end_line = j + 1  # 1-based
                    hunks.append(
                        ConflictHunk(
                            index=len(hunks),
                            start_line=start_line,
                            end_line=end_line,
                            ours="".join(ours_parts),
                            theirs="".join(theirs_parts),
                            base=base,
                            ours_label=ours_label,
                            theirs_label=theirs_label,
                        )
                    )
                    i = j + 1
                    break
                if state == "ours":
                    ours_parts.append(l + "\n")
                elif state == "base":
                    base_parts.append(l + "\n")
                elif state == "theirs":
                    theirs_parts.append(l + "\n")
                j += 1
            else:
                # Malformed: no closing >>>>>>> found; skip
                i += 1
        else:
            i += 1

    return hunks
```

- [ ] **Step 5: Implement _read_three_way**

Append to `tools/webapi/_helpers.py`:

```python
_THREE_WAY_MAX_BYTES = 1 * 1024 * 1024  # 1 MB per stage


async def _read_three_way(git_bin: str, directory: str, file: str) -> dict:
    """Read base/ours/theirs versions of a conflicted file via git show :N:<file>.

    Returns:
        {"base": str|None, "ours": str|None, "theirs": str|None, "truncated": bool}
    """
    result: dict = {"base": None, "ours": None, "theirs": None, "truncated": False}
    for stage, key in (("1", "base"), ("2", "ours"), ("3", "theirs")):
        r = await _run_git_async(
            [git_bin, "-C", directory, "show", f":{stage}:{file}"],
            encoding="utf-8",
            timeout=5.0,
        )
        if r.get("ok"):
            content = r.get("stdout", "")
            if len(content.encode("utf-8", errors="replace")) > _THREE_WAY_MAX_BYTES:
                result["truncated"] = True
            else:
                result[key] = content
        # stage missing (e.g. AA conflict has no base) → leave as None
    return result
```

- [ ] **Step 6: Implement _classify_merge_stderr**

Append to `tools/webapi/_helpers.py`:

```python
def _classify_merge_stderr(stderr: str, stdout: str = "") -> str:
    """Classify git merge failure output into a ReasonCode."""
    combined = (stderr + " " + stdout).lower()
    if "already up to date" in combined or "already up-to-date" in combined:
        return ReasonCode.MERGE_ALREADY_UP_TO_DATE
    if "conflict" in combined:
        return ReasonCode.MERGE_CONFLICT
    if "unrelated histories" in combined:
        return ReasonCode.UNRELATED_HISTORIES
    if "not something we can merge" in combined or "unknown revision" in combined:
        return ReasonCode.REF_NOT_FOUND
    if "your local changes" in combined or "would be overwritten" in combined:
        return ReasonCode.WORKTREE_DIRTY
    # Delegate to commit classifier for hook/identity errors
    from .git_commit import _classify_commit_error

    classified = _classify_commit_error(stderr, returncode=-1)
    if classified != ReasonCode.GIT_ERROR:
        return classified
    return ReasonCode.GIT_ERROR
```

- [ ] **Step 7: Implement _classify_cherry_pick_stderr**

Append to `tools/webapi/_helpers.py`:

```python
def _classify_cherry_pick_stderr(stderr: str) -> str:
    """Classify git cherry-pick failure output into a ReasonCode."""
    s = stderr.lower()
    if "conflict" in s:
        return ReasonCode.CHERRY_PICK_CONFLICT
    if "empty commit" in s or "now empty" in s:
        return ReasonCode.CHERRY_PICK_EMPTY
    if "bad object" in s or "unknown revision" in s:
        return ReasonCode.COMMIT_NOT_FOUND
    if "your local changes" in s or "would be overwritten" in s:
        return ReasonCode.WORKTREE_DIRTY
    from .git_commit import _classify_commit_error

    classified = _classify_commit_error(stderr, returncode=-1)
    if classified != ReasonCode.GIT_ERROR:
        return classified
    return ReasonCode.GIT_ERROR
```

- [ ] **Step 8: Run all tests**

Run: `pytest tests/test_git_conflict_helpers.py -v`
Expected: All PASS

- [ ] **Step 9: Lint + commit**

```bash
ruff check tools/webapi/_helpers.py tests/test_git_conflict_helpers.py
git add tools/webapi/_helpers.py tests/test_git_conflict_helpers.py
git commit -m "feat(webapi): add hunk parser, three-way reader, merge/cp stderr classifiers"
```

---

## Task 4: POST /spcode/git-merge handler

**Files:**
- Create: `tools/webapi/git_merge.py`
- Test: `tests/test_git_merge.py`

**Interfaces:**
- Consumes: `_git_endpoint_preflight`, `_make_envelope`, `_JSONResponseCompat`, `_run_git_async`, `ReasonCode`, `_detect_conflict_operation`, `_list_conflicted_files`, `_classify_merge_stderr`, `_read_post_mutation_branch_state` (all from `._helpers`)
- Consumes: `_is_valid_ref_name` (from `.._helpers`)
- Produces: `handle(plugin, *, umo, worktree, body) -> dict`

- [ ] **Step 1: Write failing tests (body validation + cross-field)**

```python
# tests/test_git_merge.py
"""Unit tests for POST /spcode/git-merge."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestGitMergeBodyValidation:
    """Body validation and cross-field mutual exclusion."""

    @pytest.mark.asyncio
    async def test_invalid_body_none(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body=None)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_missing_source(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"message": "hi"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_empty_source(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": ""})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_no_ff_and_ff_only_mutual_exclusion(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": "dev", "no_ff": True, "ff_only": True})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_squash_and_no_ff_mutual_exclusion(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": "dev", "squash": True, "no_ff": True})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_squash_and_ff_only_mutual_exclusion(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": "dev", "squash": True, "ff_only": True})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_invalid_ref_name(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": "bad branch name"})
        assert result["data"]["reason"] == "invalid_branch"

    @pytest.mark.asyncio
    async def test_message_too_long(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        result = await handle(plugin, body={"source": "dev", "message": "x" * 8193})
        assert result["data"]["reason"] == "invalid_body"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_merge.py::TestGitMergeBodyValidation -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.webapi.git_merge'`

- [ ] **Step 3: Implement git_merge.py**

```python
# tools/webapi/git_merge.py
"""POST /spcode/git-merge — merge a ref into current HEAD.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §3
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .._helpers import _is_valid_ref_name
from ._helpers import (
    ReasonCode,
    _classify_merge_stderr,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _read_post_mutation_branch_state,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 8192


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-merge handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), merged=False, source="")

    source = body.get("source")
    message = body.get("message", "")
    no_ff = bool(body.get("no_ff", False))
    ff_only = bool(body.get("ff_only", False))
    squash = bool(body.get("squash", False))

    if not isinstance(source, str) or not source.strip():
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), merged=False, source="")
    source = source.strip()

    if not isinstance(message, str) or len(message) > MAX_MESSAGE_LENGTH:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), merged=False, source=source)

    # Cross-field mutual exclusion
    if (no_ff and ff_only) or (squash and no_ff) or (squash and ff_only):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), merged=False, source=source, stderr="no_ff/ff_only/squash mutual exclusion violated")

    # ── 2. ref-format validation ──
    if not _is_valid_ref_name(source):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BRANCH, elapsed_ms=_elapsed(), merged=False, source=source)

    # ── 3. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("merged", False)
        err["data"].setdefault("source", source)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 4. conflict state pre-check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _make_envelope(
            success=False, reason=ReasonCode.OPERATION_IN_PROGRESS, elapsed_ms=_elapsed(),
            merged=False, source=source, directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"operation already in progress: {operation}",
        )

    # ── 5. worktree dirty check (squash exempt) ──
    if not squash:
        status_result = await _run_git_async(
            [git_bin, "-C", directory, "status", "--porcelain"], encoding="utf-8", timeout=5.0,
        )
        if status_result.get("ok") and status_result["stdout"].strip():
            return _make_envelope(
                success=False, reason=ReasonCode.WORKTREE_DIRTY, elapsed_ms=_elapsed(),
                merged=False, source=source, directory=directory, umo=effective_umo, worktree=directory,
                stderr="working tree has uncommitted changes",
            )

    # ── 6. execute git merge ──
    args: list[str] = [git_bin, "-C", directory, "-c", "color.ui=never", "merge", "--no-edit"]
    if no_ff:
        args.append("--no-ff")
    if ff_only:
        args.append("--ff-only")
    if squash:
        args.append("--squash")
    if message:
        args.extend(["-m", message])
    args.append(source)

    result = await _run_git_async(args, encoding="utf-8", timeout=30.0)

    # ── 7. classify result ──
    stdout = result.get("stdout", "")
    stderr = result.get("stderr", "")

    if result["ok"]:
        # Check "Already up to date" (returncode=0 but no merge happened)
        if "already up to date" in stdout.lower() or "already up-to-date" in stdout.lower():
            return _make_envelope(
                success=False, reason=ReasonCode.MERGE_ALREADY_UP_TO_DATE, elapsed_ms=_elapsed(),
                merged=False, source=source, directory=directory, umo=effective_umo, worktree=directory,
            )
        # Squash: staged but not committed
        if squash:
            files_result = await _run_git_async(
                [git_bin, "-C", directory, "diff", "--cached", "--name-only"], encoding="utf-8", timeout=5.0,
            )
            files_touched = [f for f in files_result.get("stdout", "").splitlines() if f.strip()] if files_result.get("ok") else []
            return _JSONResponseCompat(
                _make_envelope(
                    success=True, elapsed_ms=_elapsed(),
                    merged=False, source=source, squash=True, files_touched=files_touched,
                    directory=directory, umo=effective_umo, worktree=directory,
                ),
                status_code=200,
            )
        # Normal success (fast-forward or merge commit)
        fast_forward = "fast-forward" in stdout.lower()
        sha_result = await _run_git_async([git_bin, "-C", directory, "rev-parse", "HEAD"], encoding="utf-8", timeout=5.0)
        merge_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""
        msg_result = await _run_git_async([git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"], encoding="utf-8", timeout=5.0)
        merge_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""
        files_result = await _run_git_async([git_bin, "-C", directory, "show", "--name-only", "--pretty=", "HEAD"], encoding="utf-8", timeout=5.0)
        files_touched = [f for f in files_result.get("stdout", "").splitlines() if f.strip()] if files_result.get("ok") else []
        post_state = await _read_post_mutation_branch_state(git_bin, directory)

        logger.info("git-merge: %s → %s (ff=%s, umo=%s)", source, merge_sha[:12], fast_forward, effective_umo)
        return _JSONResponseCompat(
            _make_envelope(
                success=True, elapsed_ms=_elapsed(),
                merged=True, source=source, merge_sha=merge_sha, merge_message=merge_message,
                fast_forward=fast_forward, squash=False, files_touched=files_touched,
                current=post_state["current"], detached=post_state["detached"],
                branches=post_state["branches"], total=post_state["total"],
                directory=directory, umo=effective_umo, worktree=directory,
            ),
            status_code=200,
        )

    # Failure path
    reason = _classify_merge_stderr(stderr, stdout)
    conflicted_files = []
    if reason == ReasonCode.MERGE_CONFLICT:
        conflicted_files = await _list_conflicted_files(git_bin, directory)

    logger.info("git-merge: failed %s (%s): %s", source, reason, stderr[:200])
    return _make_envelope(
        success=False, reason=reason, elapsed_ms=_elapsed(),
        merged=False, source=source, conflict=(reason == ReasonCode.MERGE_CONFLICT),
        operation="merge", conflicted_files=conflicted_files,
        directory=directory, umo=effective_umo, worktree=directory,
        stderr=stderr[:4096],
    )
```

- [ ] **Step 4: Run body validation tests**

Run: `pytest tests/test_git_merge.py::TestGitMergeBodyValidation -v`
Expected: All PASS

- [ ] **Step 5: Write tests for operation_in_progress and dirty worktree**

Append to `tests/test_git_merge.py`:

```python
class TestGitMergePreflight:
    """Preflight checks: operation_in_progress, worktree_dirty."""

    @pytest.mark.asyncio
    async def test_operation_in_progress(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        with patch("tools.webapi.git_merge._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_merge._detect_conflict_operation", new_callable=AsyncMock) as mock_detect:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = "merge"
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "operation_in_progress"

    @pytest.mark.asyncio
    async def test_worktree_dirty(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        with patch("tools.webapi.git_merge._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_merge._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_merge._run_git_async", new_callable=AsyncMock) as mock_run:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            mock_run.return_value = {"ok": True, "stdout": " M file.py\n", "stderr": "", "code": 0}
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "worktree_dirty"

    @pytest.mark.asyncio
    async def test_squash_skips_dirty_check(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()
        call_log = []

        async def mock_run(args, **kwargs):
            call_log.append(args)
            if "merge" in args:
                return {"ok": True, "stdout": "Squash commit -- not updating HEAD\n", "stderr": "", "code": 0}
            if "diff" in args and "--cached" in args:
                return {"ok": True, "stdout": "file.py\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_merge._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_merge._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run):
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={"source": "dev", "squash": True})
        # Should NOT have called status --porcelain (dirty check skipped)
        status_calls = [a for a in call_log if "status" in a and "--porcelain" in a]
        assert len(status_calls) == 0
        assert result["data"]["squash"] is True
```

- [ ] **Step 6: Write tests for success/conflict/already-up-to-date paths**

Append to `tests/test_git_merge.py`:

```python
class TestGitMergeExecution:
    """Execution paths: success, fast-forward, conflict, already-up-to-date."""

    @pytest.mark.asyncio
    async def test_successful_merge_commit(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "merge" in args:
                return {"ok": True, "stdout": "Merge made by the 'ort' strategy.\n", "stderr": "", "code": 0}
            if "rev-parse" in args and "HEAD" in args:
                return {"ok": True, "stdout": "abc123\n", "stderr": "", "code": 0}
            if "log" in args:
                return {"ok": True, "stdout": "Merge branch 'dev'\n", "stderr": "", "code": 0}
            if "show" in args:
                return {"ok": True, "stdout": "src/a.py\nsrc/b.py\n", "stderr": "", "code": 0}
            if "for-each-ref" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "rev-parse" in args and "--abbrev-ref" in args:
                return {"ok": True, "stdout": "main\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_merge._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_merge._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run), \
             patch("tools.webapi.git_merge._read_post_mutation_branch_state", new_callable=AsyncMock) as mock_state:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            mock_state.return_value = {"branches": [], "total": 0, "current": "main", "detached": False}
            # Skip dirty check
            with patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run):
                result = await handle(plugin, body={"source": "dev"})

        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["merged"] is True
        assert data["merge_sha"] == "abc123"

    @pytest.mark.asyncio
    async def test_already_up_to_date(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "merge" in args:
                return {"ok": True, "stdout": "Already up to date.\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_merge._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_merge._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run):
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "merge_already_up_to_date"
        assert result["data"]["merged"] is False

    @pytest.mark.asyncio
    async def test_merge_conflict(self):
        from tools.webapi.git_merge import handle

        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "merge" in args:
                return {"ok": False, "stdout": "", "stderr": "CONFLICT (content): Merge conflict in src/a.py\n", "code": 1}
            if "status" in args and "--porcelain" in args:
                return {"ok": True, "stdout": "UU src/a.py\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_merge._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_merge._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_merge._run_git_async", side_effect=mock_run):
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={"source": "dev"})
        assert result["data"]["reason"] == "merge_conflict"
        assert result["data"]["conflict"] is True
        assert len(result["data"]["conflicted_files"]) == 1
```

- [ ] **Step 7: Run all git-merge tests**

Run: `pytest tests/test_git_merge.py -v`
Expected: All PASS

- [ ] **Step 8: Lint + commit**

```bash
ruff check tools/webapi/git_merge.py tests/test_git_merge.py
git add tools/webapi/git_merge.py tests/test_git_merge.py
git commit -m "feat(webapi): add POST /spcode/git-merge endpoint"
```

---

## Task 5: POST /spcode/git-cherry-pick handler

**Files:**
- Create: `tools/webapi/git_cherry_pick.py`
- Test: `tests/test_git_cherry_pick.py`

**Interfaces:**
- Consumes: Same helpers as Task 4 + `_is_commit_ref` (from `.._helpers`) + `_classify_cherry_pick_stderr`
- Produces: `handle(plugin, *, umo, worktree, body) -> dict`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_git_cherry_pick.py
"""Unit tests for POST /spcode/git-cherry-pick."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestCherryPickBodyValidation:
    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from tools.webapi.git_cherry_pick import handle
        result = await handle(_make_plugin(), body=None)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_missing_ref(self):
        from tools.webapi.git_cherry_pick import handle
        result = await handle(_make_plugin(), body={})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_invalid_mainline_zero(self):
        from tools.webapi.git_cherry_pick import handle
        result = await handle(_make_plugin(), body={"ref": "abc", "mainline": 0})
        assert result["data"]["reason"] == "invalid_param"

    @pytest.mark.asyncio
    async def test_invalid_mainline_negative(self):
        from tools.webapi.git_cherry_pick import handle
        result = await handle(_make_plugin(), body={"ref": "abc", "mainline": -1})
        assert result["data"]["reason"] == "invalid_param"

    @pytest.mark.asyncio
    async def test_invalid_mainline_string(self):
        from tools.webapi.git_cherry_pick import handle
        result = await handle(_make_plugin(), body={"ref": "abc", "mainline": "x"})
        assert result["data"]["reason"] == "invalid_param"


class TestCherryPickExecution:
    @pytest.mark.asyncio
    async def test_commit_not_found(self):
        from tools.webapi.git_cherry_pick import handle
        plugin = _make_plugin()
        with patch("tools.webapi.git_cherry_pick._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_cherry_pick._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=False):
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "bad"})
        assert result["data"]["reason"] == "commit_not_found"

    @pytest.mark.asyncio
    async def test_successful_pick(self):
        from tools.webapi.git_cherry_pick import handle
        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "cherry-pick" in args:
                return {"ok": True, "stdout": "", "stderr": "", "code": 0}
            if "rev-parse" in args and "HEAD" in args:
                return {"ok": True, "stdout": "new123\n", "stderr": "", "code": 0}
            if "log" in args:
                return {"ok": True, "stdout": "fix: bug\n", "stderr": "", "code": 0}
            if "show" in args:
                return {"ok": True, "stdout": "src/x.py\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_cherry_pick._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_cherry_pick._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=True), \
             patch("tools.webapi.git_cherry_pick._run_git_async", side_effect=mock_run):
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "abc123"})
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["picked"] is True
        assert data["new_sha"] == "new123"

    @pytest.mark.asyncio
    async def test_cherry_pick_conflict(self):
        from tools.webapi.git_cherry_pick import handle
        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "cherry-pick" in args:
                return {"ok": False, "stdout": "", "stderr": "CONFLICT (content): Merge conflict in f.py\n", "code": 1}
            if "status" in args and "--porcelain" in args:
                return {"ok": True, "stdout": "UU f.py\n", "stderr": "", "code": 0}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_cherry_pick._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_cherry_pick._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=True), \
             patch("tools.webapi.git_cherry_pick._run_git_async", side_effect=mock_run):
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "abc123"})
        assert result["data"]["reason"] == "cherry_pick_conflict"
        assert result["data"]["conflict"] is True

    @pytest.mark.asyncio
    async def test_cherry_pick_empty(self):
        from tools.webapi.git_cherry_pick import handle
        plugin = _make_plugin()

        async def mock_run(args, **kwargs):
            if "cherry-pick" in args:
                return {"ok": False, "stdout": "", "stderr": "The previous cherry-pick is now empty\n", "code": 1}
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_cherry_pick._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_cherry_pick._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_cherry_pick._is_commit_ref", return_value=True), \
             patch("tools.webapi.git_cherry_pick._run_git_async", side_effect=mock_run):
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={"ref": "abc123"})
        assert result["data"]["reason"] == "cherry_pick_empty"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_cherry_pick.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement git_cherry_pick.py**

```python
# tools/webapi/git_cherry_pick.py
"""POST /spcode/git-cherry-pick — cherry-pick a single commit onto current HEAD.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §4
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .._helpers import _is_commit_ref
from ._helpers import (
    ReasonCode,
    _classify_cherry_pick_stderr,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
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
    """POST /spcode/git-cherry-pick handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), picked=False, ref="")

    ref = body.get("ref")
    mainline = body.get("mainline")

    if not isinstance(ref, str) or not ref.strip():
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), picked=False, ref="")
    ref = ref.strip()

    if mainline is not None:
        if not isinstance(mainline, int) or isinstance(mainline, bool) or mainline < 1:
            return _make_envelope(success=False, reason=ReasonCode.INVALID_PARAM, elapsed_ms=_elapsed(), picked=False, ref=ref, stderr="mainline must be a positive integer or null")

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("picked", False)
        err["data"].setdefault("ref", ref)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. conflict state pre-check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is not None:
        return _make_envelope(
            success=False, reason=ReasonCode.OPERATION_IN_PROGRESS, elapsed_ms=_elapsed(),
            picked=False, ref=ref, directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"operation already in progress: {operation}",
        )

    # ── 4. commit ref validation ──
    if not _is_commit_ref(git_bin, directory, ref):
        return _make_envelope(
            success=False, reason=ReasonCode.COMMIT_NOT_FOUND, elapsed_ms=_elapsed(),
            picked=False, ref=ref, directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"ref does not resolve to a commit: {ref}",
        )

    # ── 5. worktree dirty check ──
    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"], encoding="utf-8", timeout=5.0,
    )
    if status_result.get("ok") and status_result["stdout"].strip():
        return _make_envelope(
            success=False, reason=ReasonCode.WORKTREE_DIRTY, elapsed_ms=_elapsed(),
            picked=False, ref=ref, directory=directory, umo=effective_umo, worktree=directory,
            stderr="working tree has uncommitted changes",
        )

    # ── 6. execute git cherry-pick ──
    args: list[str] = [git_bin, "-C", directory, "-c", "color.ui=never", "cherry-pick", "--no-edit"]
    if mainline is not None:
        args.extend(["-m", str(mainline)])
    args.append(ref)

    result = await _run_git_async(args, encoding="utf-8", timeout=30.0)

    # ── 7. classify + readback ──
    if result["ok"]:
        sha_result = await _run_git_async([git_bin, "-C", directory, "rev-parse", "HEAD"], encoding="utf-8", timeout=5.0)
        new_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""
        msg_result = await _run_git_async([git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"], encoding="utf-8", timeout=5.0)
        original_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""
        files_result = await _run_git_async([git_bin, "-C", directory, "show", "--name-only", "--pretty=", "HEAD"], encoding="utf-8", timeout=5.0)
        files_touched = [f for f in files_result.get("stdout", "").splitlines() if f.strip()] if files_result.get("ok") else []

        logger.info("git-cherry-pick: %s → %s (umo=%s)", ref, new_sha[:12], effective_umo)
        return _JSONResponseCompat(
            _make_envelope(
                success=True, elapsed_ms=_elapsed(),
                picked=True, ref=ref, new_sha=new_sha, original_message=original_message,
                files_touched=files_touched,
                directory=directory, umo=effective_umo, worktree=directory,
            ),
            status_code=200,
        )

    stderr = result.get("stderr", "")
    reason = _classify_cherry_pick_stderr(stderr)
    conflicted_files = []
    if reason == ReasonCode.CHERRY_PICK_CONFLICT:
        conflicted_files = await _list_conflicted_files(git_bin, directory)

    logger.info("git-cherry-pick: failed %s (%s): %s", ref, reason, stderr[:200])
    return _make_envelope(
        success=False, reason=reason, elapsed_ms=_elapsed(),
        picked=False, ref=ref, conflict=(reason == ReasonCode.CHERRY_PICK_CONFLICT),
        operation="cherry_pick", conflicted_files=conflicted_files,
        directory=directory, umo=effective_umo, worktree=directory,
        stderr=stderr[:4096],
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_git_cherry_pick.py -v`
Expected: All PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check tools/webapi/git_cherry_pick.py tests/test_git_cherry_pick.py
git add tools/webapi/git_cherry_pick.py tests/test_git_cherry_pick.py
git commit -m "feat(webapi): add POST /spcode/git-cherry-pick endpoint"
```

---

## Task 6: GET /spcode/git-conflict-status handler

**Files:**
- Create: `tools/webapi/git_conflict_status.py`
- Test: `tests/test_git_conflict_status.py`

**Interfaces:**
- Consumes: `_git_endpoint_preflight`, `_make_envelope`, `_JSONResponseCompat`, `_run_git_async`, `ReasonCode`, `_detect_conflict_operation`, `_list_conflicted_files`, `_read_operation_ref`, `_parse_conflict_hunks`, `_read_three_way`, `ConflictHunk`
- Produces: `handle(plugin, *, umo, worktree) -> dict`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_git_conflict_status.py
"""Unit tests for GET /spcode/git-conflict-status."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestConflictStatusNoConflict:
    @pytest.mark.asyncio
    async def test_no_conflict_returns_in_conflict_false(self):
        from tools.webapi.git_conflict_status import handle

        plugin = _make_plugin()
        with patch("tools.webapi.git_conflict_status._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_conflict_status._detect_conflict_operation", new_callable=AsyncMock) as mock_detect:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin)
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["in_conflict"] is False
        assert data["operation"] is None
        assert data["all_resolved"] is True


class TestConflictStatusWithConflict:
    @pytest.mark.asyncio
    async def test_merge_conflict_with_hunks(self, tmp_path):
        from tools.webapi.git_conflict_status import handle

        plugin = _make_plugin()
        conflicted = [{"path": "src/a.py", "status": "UU"}]
        file_content = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> dev\n"

        async def mock_run(args, **kwargs):
            return {"ok": True, "stdout": "", "stderr": "", "code": 0}

        with patch("tools.webapi.git_conflict_status._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_conflict_status._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_conflict_status._list_conflicted_files", new_callable=AsyncMock) as mock_list, \
             patch("tools.webapi.git_conflict_status._read_operation_ref", new_callable=AsyncMock) as mock_ref, \
             patch("tools.webapi.git_conflict_status._run_git_async", side_effect=mock_run), \
             patch("tools.webapi.git_conflict_status._read_three_way", new_callable=AsyncMock) as mock_tw, \
             patch("pathlib.Path.read_text", return_value=file_content), \
             patch("pathlib.Path.exists", return_value=True):
            mock_pf.return_value = (None, {"directory": str(tmp_path), "umo": "u1", "worktree": str(tmp_path)})
            mock_detect.return_value = "merge"
            mock_list.return_value = conflicted
            mock_ref.return_value = ("abc123", "feat: x")
            mock_tw.return_value = {"base": None, "ours": "ours\n", "theirs": "theirs\n", "truncated": False}
            result = await handle(plugin)

        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["in_conflict"] is True
        assert data["operation"] == "merge"
        assert data["operation_ref"] == "abc123"
        assert len(data["conflicted_files"]) == 1
        assert data["conflicted_files"][0]["path"] == "src/a.py"
        assert len(data["conflicted_files"][0]["hunks"]) == 1
        assert data["conflicted_files"][0]["hunks"][0]["ours"] == "ours\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_conflict_status.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement git_conflict_status.py**

```python
# tools/webapi/git_conflict_status.py
"""GET /spcode/git-conflict-status — query conflict state + hunk details.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §5
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    ConflictHunk,
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _parse_conflict_hunks,
    _read_operation_ref,
    _read_three_way,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_MAX_HUNK_FILES = 20
_MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-conflict-status handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 2. detect operation ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _JSONResponseCompat(
            _make_envelope(
                success=True, elapsed_ms=_elapsed(),
                in_conflict=False, operation=None, operation_ref=None, operation_subject=None,
                conflicted_files=[], resolved_files=[],
                total_conflicted=0, total_resolved=0, all_resolved=True,
                directory=directory, umo=effective_umo, worktree=directory,
            ),
            status_code=200,
        )

    # ── 3. read operation ref ──
    op_sha, op_subject = await _read_operation_ref(git_bin, directory, operation)

    # ── 4. list conflicted files ──
    conflicted = await _list_conflicted_files(git_bin, directory)

    # ── 5. resolved files (staged minus unmerged) ──
    staged_result = await _run_git_async(
        [git_bin, "-C", directory, "diff", "--cached", "--name-only", "-z"],
        encoding="utf-8", timeout=5.0,
    )
    staged_paths = set()
    if staged_result.get("ok"):
        staged_paths = {p for p in staged_result.get("stdout", "").split("\0") if p}
    conflicted_paths = {c["path"] for c in conflicted}
    resolved_files = sorted(staged_paths - conflicted_paths)

    # ── 6. parse hunks + three-way for each conflicted file ──
    enriched: list[dict] = []
    for i, cf in enumerate(conflicted):
        entry: dict = {"path": cf["path"], "status": cf["status"], "hunks": [], "three_way": {"base": None, "ours": None, "theirs": None}, "binary": False, "truncated": False}
        file_path = Path(directory) / cf["path"]

        if i < _MAX_HUNK_FILES and file_path.exists():
            try:
                raw = file_path.read_bytes()
                if b"\x00" in raw:
                    entry["binary"] = True
                elif len(raw) > _MAX_FILE_BYTES:
                    entry["truncated"] = True
                else:
                    content = raw.decode("utf-8", errors="replace")
                    hunks = _parse_conflict_hunks(content)
                    entry["hunks"] = [
                        {"index": h.index, "start_line": h.start_line, "end_line": h.end_line, "ours": h.ours, "theirs": h.theirs, "base": h.base, "ours_label": h.ours_label, "theirs_label": h.theirs_label}
                        for h in hunks
                    ]
            except OSError:
                pass

        # Three-way (always attempt, even beyond _MAX_HUNK_FILES)
        tw = await _read_three_way(git_bin, directory, cf["path"])
        entry["three_way"] = {"base": tw["base"], "ours": tw["ours"], "theirs": tw["theirs"]}
        if tw.get("truncated"):
            entry["truncated"] = True

        enriched.append(entry)

    all_resolved = len(conflicted) == 0

    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            in_conflict=True, operation=operation, operation_ref=op_sha, operation_subject=op_subject,
            conflicted_files=enriched, resolved_files=resolved_files,
            total_conflicted=len(conflicted), total_resolved=len(resolved_files),
            all_resolved=all_resolved,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_git_conflict_status.py -v`
Expected: All PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check tools/webapi/git_conflict_status.py tests/test_git_conflict_status.py
git add tools/webapi/git_conflict_status.py tests/test_git_conflict_status.py
git commit -m "feat(webapi): add GET /spcode/git-conflict-status endpoint"
```

---

## Task 7: POST /spcode/git-conflict-resolve handler

**Files:**
- Create: `tools/webapi/git_conflict_resolve.py`
- Test: `tests/test_git_conflict_resolve.py`

**Interfaces:**
- Consumes: `_git_endpoint_preflight`, `_make_envelope`, `_JSONResponseCompat`, `_run_git_async`, `ReasonCode`, `_detect_conflict_operation`, `_list_conflicted_files`, `_parse_conflict_hunks`, `_validate_repo_relative_file`, `ConflictHunk`
- Produces: `handle(plugin, *, umo, worktree, body) -> dict`
- Produces (internal): `_rebuild_file_from_hunks(original_lines, hunks, choices) -> str | None`

- [ ] **Step 1: Write failing tests for _rebuild_file_from_hunks**

```python
# tests/test_git_conflict_resolve.py
"""Unit tests for POST /spcode/git-conflict-resolve."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRebuildFileFromHunks:
    def test_all_hunks_resolved(self):
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks
        from tools.webapi._helpers import ConflictHunk

        content = "line1\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> dev\nline7\n"
        lines = content.split("\n")
        hunks = [ConflictHunk(index=0, start_line=2, end_line=6, ours="ours\n", theirs="theirs\n", base=None, ours_label="HEAD", theirs_label="dev")]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "theirs"})
        assert result == "line1\ntheirs\nline7\n"

    def test_partial_resolution_returns_none(self):
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks
        from tools.webapi._helpers import ConflictHunk

        content = "<<<<<<< HEAD\no1\n=======\nt1\n>>>>>>> b\nmid\n<<<<<<< HEAD\no2\n=======\nt2\n>>>>>>> b\n"
        lines = content.split("\n")
        hunks = [
            ConflictHunk(index=0, start_line=1, end_line=5, ours="o1\n", theirs="t1\n", base=None, ours_label="HEAD", theirs_label="b"),
            ConflictHunk(index=1, start_line=7, end_line=11, ours="o2\n", theirs="t2\n", base=None, ours_label="HEAD", theirs_label="b"),
        ]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "ours"})
        assert result is None

    def test_base_choice(self):
        from tools.webapi.git_conflict_resolve import _rebuild_file_from_hunks
        from tools.webapi._helpers import ConflictHunk

        content = "<<<<<<< HEAD\nours\n||||||| base\nbaseval\n=======\ntheirs\n>>>>>>> dev\n"
        lines = content.split("\n")
        hunks = [ConflictHunk(index=0, start_line=1, end_line=7, ours="ours\n", theirs="theirs\n", base="baseval\n", ours_label="HEAD", theirs_label="dev")]
        result = _rebuild_file_from_hunks(lines, hunks, {0: "base"})
        assert result == "baseval\n"


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestResolveBodyValidation:
    @pytest.mark.asyncio
    async def test_invalid_body(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body=None)
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_file_and_all_both_provided(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body={"file": "a.py", "all": True, "resolution": "ours"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_neither_file_nor_all(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body={"resolution": "ours"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_hunks_and_resolution_mutual_exclusion(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body={"file": "a.py", "hunks": [{"index": 0, "choice": "ours"}], "resolution": "theirs"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_all_with_custom_forbidden(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body={"all": True, "resolution": "custom", "content": "x"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_all_with_hunks_forbidden(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body={"all": True, "hunks": [{"index": 0, "choice": "ours"}]})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_custom_without_content(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body={"file": "a.py", "resolution": "custom"})
        assert result["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_content_too_large(self):
        from tools.webapi.git_conflict_resolve import handle
        result = await handle(_make_plugin(), body={"file": "a.py", "resolution": "custom", "content": "x" * (1024 * 1024 + 1)})
        assert result["data"]["reason"] == "invalid_param"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_conflict_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement git_conflict_resolve.py**

(Full implementation — see spec §6 for algorithm details. Key structure:)

```python
# tools/webapi/git_conflict_resolve.py
"""POST /spcode/git-conflict-resolve — resolve conflicted files.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §6
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    ConflictHunk,
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _parse_conflict_hunks,
    _run_git_async,
    _validate_repo_relative_file,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_MAX_CONTENT_BYTES = 1 * 1024 * 1024  # 1 MB


def _rebuild_file_from_hunks(
    original_lines: list[str],
    hunks: list[ConflictHunk],
    choices: dict[int, str],
) -> str | None:
    """Rebuild file content by replacing conflict regions per choices.

    Returns None if any hunk lacks a choice (partial resolution).
    """
    # Check all hunks have choices
    for h in hunks:
        if h.index not in choices:
            return None

    # Build output by walking lines, replacing conflict regions
    output_parts: list[str] = []
    line_idx = 0  # 0-based index into original_lines
    sorted_hunks = sorted(hunks, key=lambda h: h.start_line)

    for hunk in sorted_hunks:
        # Lines before this hunk (1-based start_line → 0-based = start_line - 1)
        hunk_start_0 = hunk.start_line - 1
        hunk_end_0 = hunk.end_line  # end_line is 1-based inclusive → 0-based exclusive

        # Append non-conflict lines before this hunk
        while line_idx < hunk_start_0 and line_idx < len(original_lines):
            output_parts.append(original_lines[line_idx] + "\n")
            line_idx += 1

        # Replace conflict region with chosen content
        choice = choices[hunk.index]
        if choice == "ours":
            output_parts.append(hunk.ours)
        elif choice == "theirs":
            output_parts.append(hunk.theirs)
        elif choice == "base" and hunk.base is not None:
            output_parts.append(hunk.base)

        # Skip past the conflict region
        line_idx = hunk_end_0

    # Append remaining lines after last hunk
    while line_idx < len(original_lines):
        output_parts.append(original_lines[line_idx] + "\n")
        line_idx += 1

    result = "".join(output_parts)
    # Remove trailing extra newline if original didn't end with one
    if result.endswith("\n") and original_lines and original_lines[-1] == "":
        result = result[:-1]
    return result


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-conflict-resolve handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False)

    file_field = body.get("file")
    all_flag = bool(body.get("all", False))
    hunks_field = body.get("hunks")
    resolution = body.get("resolution")
    content = body.get("content")

    # file/all mutual exclusion
    has_file = isinstance(file_field, str) and file_field.strip()
    if has_file and all_flag:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, stderr="file and all are mutually exclusive")
    if not has_file and not all_flag:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, stderr="either file or all must be provided")

    # hunks/resolution mutual exclusion
    if hunks_field is not None and resolution is not None:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, stderr="hunks and resolution are mutually exclusive")

    # all + custom/hunks forbidden
    if all_flag and resolution == "custom":
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, stderr="all=true does not support custom resolution")
    if all_flag and hunks_field is not None:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, stderr="all=true does not support per-hunk resolution")

    # custom requires content
    if resolution == "custom" and (not isinstance(content, str) or not content):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, stderr="resolution=custom requires non-empty content")

    # content size limit
    if isinstance(content, str) and len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_PARAM, elapsed_ms=_elapsed(), resolved=False, stderr="content exceeds 1 MB limit")

    # resolution enum check
    if resolution is not None and resolution not in ("ours", "theirs", "custom"):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, stderr="resolution must be ours/theirs/custom")

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("resolved", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. conflict state check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _make_envelope(success=False, reason=ReasonCode.NO_CONFLICT_IN_PROGRESS, elapsed_ms=_elapsed(), resolved=False, directory=directory, umo=effective_umo, worktree=directory)

    # ── 4. resolve ──
    if all_flag:
        # Batch mode: resolve all conflicted files with ours/theirs
        conflicted = await _list_conflicted_files(git_bin, directory)
        for cf in conflicted:
            checkout_side = "--ours" if resolution == "ours" else "--theirs"
            await _run_git_async([git_bin, "-C", directory, "checkout", checkout_side, "--", cf["path"]], encoding="utf-8", timeout=10.0)
        await _run_git_async([git_bin, "-C", directory, "add", "--all"], encoding="utf-8", timeout=10.0)
        remaining = await _list_conflicted_files(git_bin, directory)
        return _JSONResponseCompat(
            _make_envelope(
                success=True, elapsed_ms=_elapsed(),
                resolved=True, file=None, mode="all", resolution=resolution,
                files_resolved=len(conflicted), remaining_conflicts=remaining,
                all_resolved=(len(remaining) == 0),
                directory=directory, umo=effective_umo, worktree=directory,
            ),
            status_code=200,
        )

    # Single file mode
    file = file_field.strip()
    target, path_err = _validate_repo_relative_file(file, Path(directory))
    if path_err is not None:
        return _make_envelope(success=False, reason=ReasonCode.PATH_UNSAFE, elapsed_ms=_elapsed(), resolved=False, file=file, directory=directory, umo=effective_umo, worktree=directory)

    # Check file is actually conflicted
    conflicted = await _list_conflicted_files(git_bin, directory)
    conflicted_paths = {c["path"] for c in conflicted}
    if file not in conflicted_paths:
        return _make_envelope(success=False, reason=ReasonCode.FILE_NOT_CONFLICTED, elapsed_ms=_elapsed(), resolved=False, file=file, directory=directory, umo=effective_umo, worktree=directory)

    if hunks_field is not None:
        # Mode 1: per-hunk resolution
        if not isinstance(hunks_field, list):
            return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), resolved=False, file=file)
        # Binary check
        raw = target.read_bytes() if target.exists() else b""
        if b"\x00" in raw:
            return _make_envelope(success=False, reason=ReasonCode.INVALID_PARAM, elapsed_ms=_elapsed(), resolved=False, file=file, stderr="binary file does not support hunk resolution")

        file_content = raw.decode("utf-8", errors="replace")
        parsed_hunks = _parse_conflict_hunks(file_content)
        choices: dict[int, str] = {}
        for item in hunks_field:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            choice = item.get("choice")
            if isinstance(idx, int) and choice in ("ours", "theirs", "base"):
                # Validate base choice requires diff3
                if choice == "base":
                    matching = [h for h in parsed_hunks if h.index == idx]
                    if matching and matching[0].base is None:
                        return _make_envelope(success=False, reason=ReasonCode.INVALID_PARAM, elapsed_ms=_elapsed(), resolved=False, file=file, stderr=f"hunk {idx} has no base (not diff3 format)")
                choices[idx] = choice

        # Validate indices in range
        valid_indices = {h.index for h in parsed_hunks}
        for idx in choices:
            if idx not in valid_indices:
                return _make_envelope(success=False, reason=ReasonCode.INVALID_PARAM, elapsed_ms=_elapsed(), resolved=False, file=file, stderr=f"hunk index {idx} out of range")

        lines = file_content.split("\n")
        rebuilt = _rebuild_file_from_hunks(lines, parsed_hunks, choices)

        if rebuilt is None:
            # Partial resolution
            unresolved = [h for h in parsed_hunks if h.index not in choices]
            remaining = await _list_conflicted_files(git_bin, directory)
            return _JSONResponseCompat(
                _make_envelope(
                    success=True, elapsed_ms=_elapsed(),
                    resolved=False, file=file, mode="hunks",
                    hunks_resolved=len(choices), hunks_total=len(parsed_hunks),
                    partial=True,
                    unresolved_hunks=[{"index": h.index, "start_line": h.start_line, "end_line": h.end_line} for h in unresolved],
                    remaining_conflicts=remaining, all_resolved=False,
                    directory=directory, umo=effective_umo, worktree=directory,
                ),
                status_code=200,
            )

        # Write rebuilt content
        target.write_text(rebuilt, encoding="utf-8")
        await _run_git_async([git_bin, "-C", directory, "add", "--", file], encoding="utf-8", timeout=10.0)

    elif resolution in ("ours", "theirs"):
        # Mode 2: whole-file ours/theirs
        checkout_side = "--ours" if resolution == "ours" else "--theirs"
        await _run_git_async([git_bin, "-C", directory, "checkout", checkout_side, "--", file], encoding="utf-8", timeout=10.0)
        await _run_git_async([git_bin, "-C", directory, "add", "--", file], encoding="utf-8", timeout=10.0)

    elif resolution == "custom":
        # Mode 3: custom content
        target.write_text(content, encoding="utf-8")
        await _run_git_async([git_bin, "-C", directory, "add", "--", file], encoding="utf-8", timeout=10.0)

    # ── 5. readback ──
    remaining = await _list_conflicted_files(git_bin, directory)
    mode = "hunks" if hunks_field is not None else ("custom" if resolution == "custom" else "whole_file")

    logger.info("git-conflict-resolve: file=%s mode=%s remaining=%d", file, mode, len(remaining))
    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            resolved=True, file=file, mode=mode,
            hunks_resolved=len(choices) if hunks_field else None,
            hunks_total=len(parsed_hunks) if hunks_field else None,
            partial=False,
            remaining_conflicts=remaining, all_resolved=(len(remaining) == 0),
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_git_conflict_resolve.py -v`
Expected: All PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check tools/webapi/git_conflict_resolve.py tests/test_git_conflict_resolve.py
git add tools/webapi/git_conflict_resolve.py tests/test_git_conflict_resolve.py
git commit -m "feat(webapi): add POST /spcode/git-conflict-resolve endpoint"
```

---

## Task 8: POST /spcode/git-conflict-continue + git-conflict-abort

**Files:**
- Create: `tools/webapi/git_conflict_continue.py`
- Create: `tools/webapi/git_conflict_abort.py`
- Test: `tests/test_git_conflict_continue.py`
- Test: `tests/test_git_conflict_abort.py`

**Interfaces:**
- Consumes: `_git_endpoint_preflight`, `_make_envelope`, `_JSONResponseCompat`, `_run_git_async`, `ReasonCode`, `_detect_conflict_operation`, `_list_conflicted_files`, `_read_post_mutation_branch_state`, `_read_operation_ref`
- Consumes: `_build_git_env` (from `.git_commit`)
- Consumes: `_classify_commit_error` (from `.git_commit`)
- Produces: `handle(plugin, *, umo, worktree, body) -> dict` for both modules

- [ ] **Step 1: Write failing tests for continue**

```python
# tests/test_git_conflict_continue.py
"""Unit tests for POST /spcode/git-conflict-continue."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestContinueValidation:
    @pytest.mark.asyncio
    async def test_no_conflict_in_progress(self):
        from tools.webapi.git_conflict_continue import handle
        plugin = _make_plugin()
        with patch("tools.webapi.git_conflict_continue._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_conflict_continue._detect_conflict_operation", new_callable=AsyncMock) as mock_detect:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={})
        assert result["data"]["reason"] == "no_conflict_in_progress"

    @pytest.mark.asyncio
    async def test_unresolved_conflicts_remain(self):
        from tools.webapi.git_conflict_continue import handle
        plugin = _make_plugin()
        with patch("tools.webapi.git_conflict_continue._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_conflict_continue._detect_conflict_operation", new_callable=AsyncMock) as mock_detect, \
             patch("tools.webapi.git_conflict_continue._list_conflicted_files", new_callable=AsyncMock) as mock_list:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = "merge"
            mock_list.return_value = [{"path": "a.py", "status": "UU"}]
            result = await handle(plugin, body={})
        assert result["data"]["reason"] == "unresolved_conflicts_remain"

    @pytest.mark.asyncio
    async def test_message_too_long(self):
        from tools.webapi.git_conflict_continue import handle
        plugin = _make_plugin()
        result = await handle(plugin, body={"message": "x" * 8193})
        assert result["data"]["reason"] == "invalid_body"
```

- [ ] **Step 2: Write failing tests for abort**

```python
# tests/test_git_conflict_abort.py
"""Unit tests for POST /spcode/git-conflict-abort."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/repo"}
    return plugin


class TestAbortValidation:
    @pytest.mark.asyncio
    async def test_no_conflict_in_progress(self):
        from tools.webapi.git_conflict_abort import handle
        plugin = _make_plugin()
        with patch("tools.webapi.git_conflict_abort._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_conflict_abort._detect_conflict_operation", new_callable=AsyncMock) as mock_detect:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_detect.return_value = None
            result = await handle(plugin, body={})
        assert result["data"]["reason"] == "no_conflict_in_progress"

    @pytest.mark.asyncio
    async def test_successful_abort(self):
        from tools.webapi.git_conflict_abort import handle
        plugin = _make_plugin()
        detect_calls = [0]

        async def mock_detect(*a, **kw):
            detect_calls[0] += 1
            return "merge" if detect_calls[0] == 1 else None

        with patch("tools.webapi.git_conflict_abort._git_endpoint_preflight", new_callable=AsyncMock) as mock_pf, \
             patch("tools.webapi.git_conflict_abort._detect_conflict_operation", side_effect=mock_detect), \
             patch("tools.webapi.git_conflict_abort._run_git_async", new_callable=AsyncMock) as mock_run, \
             patch("tools.webapi.git_conflict_abort._read_operation_ref", new_callable=AsyncMock) as mock_ref, \
             patch("tools.webapi.git_conflict_abort._read_post_mutation_branch_state", new_callable=AsyncMock) as mock_state:
            mock_pf.return_value = (None, {"directory": "/repo", "umo": "u1", "worktree": "/repo"})
            mock_run.return_value = {"ok": True, "stdout": "", "stderr": "", "code": 0}
            mock_ref.return_value = ("abc123", "feat: x")
            mock_state.return_value = {"branches": [], "total": 0, "current": "main", "detached": False}
            result = await handle(plugin, body={})
        data = result["data"] if isinstance(result, dict) else result._content["data"]
        assert data["aborted"] is True
        assert data["operation"] == "merge"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement git_conflict_continue.py**

```python
# tools/webapi/git_conflict_continue.py
"""POST /spcode/git-conflict-continue — complete merge/cherry-pick/revert after resolution.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §7
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from .git_commit import _build_git_env, _classify_commit_error
from ._helpers import (
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _list_conflicted_files,
    _make_envelope,
    _read_post_mutation_branch_state,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 8192

_CONTINUE_CMD = {
    "merge": None,  # merge uses git commit
    "cherry_pick": ["cherry-pick", "--continue", "--no-edit"],
    "revert": ["revert", "--continue", "--no-edit"],
}


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-conflict-continue handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body validation ──
    if not isinstance(body, dict):
        body = {}
    message = body.get("message", "")
    if not isinstance(message, str) or len(message) > MAX_MESSAGE_LENGTH:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY, elapsed_ms=_elapsed(), continued=False)

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("continued", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. conflict state check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _make_envelope(success=False, reason=ReasonCode.NO_CONFLICT_IN_PROGRESS, elapsed_ms=_elapsed(), continued=False, directory=directory, umo=effective_umo, worktree=directory)

    # ── 4. unmerged check ──
    remaining = await _list_conflicted_files(git_bin, directory)
    if remaining:
        return _make_envelope(
            success=False, reason=ReasonCode.UNRESOLVED_CONFLICTS_REMAIN, elapsed_ms=_elapsed(),
            continued=False, operation=operation, remaining_conflicts=remaining,
            directory=directory, umo=effective_umo, worktree=directory,
        )

    # ── 5. execute continue ──
    git_env = _build_git_env()
    if operation == "merge":
        args = [git_bin, "-C", directory, "-c", "color.ui=never", "commit", "--no-edit"]
        input_text = None
        if message:
            args = [git_bin, "-C", directory, "-c", "color.ui=never", "commit", "-F", "-"]
            input_text = message
    else:
        cmd_parts = _CONTINUE_CMD[operation]
        args = [git_bin, "-C", directory, "-c", "color.ui=never"] + cmd_parts
        input_text = None

    result = await _run_git_async(args, encoding="utf-8", input_text=input_text, env=git_env, timeout=30.0)

    if not result["ok"]:
        stderr = result.get("stderr", "")
        reason = _classify_commit_error(stderr, result.get("code", -1))
        return _make_envelope(
            success=False, reason=reason, elapsed_ms=_elapsed(),
            continued=False, operation=operation,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=stderr[:4096],
        )

    # ── 6. readback ──
    sha_result = await _run_git_async([git_bin, "-C", directory, "rev-parse", "HEAD"], encoding="utf-8", timeout=5.0)
    commit_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""
    msg_result = await _run_git_async([git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"], encoding="utf-8", timeout=5.0)
    commit_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""
    files_result = await _run_git_async([git_bin, "-C", directory, "show", "--name-only", "--pretty=", "HEAD"], encoding="utf-8", timeout=5.0)
    files_touched = [f for f in files_result.get("stdout", "").splitlines() if f.strip()] if files_result.get("ok") else []
    post_state = await _read_post_mutation_branch_state(git_bin, directory)

    logger.info("git-conflict-continue: %s → %s (umo=%s)", operation, commit_sha[:12], effective_umo)
    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            continued=True, operation=operation, commit_sha=commit_sha, commit_message=commit_message,
            files_touched=files_touched,
            current=post_state["current"], detached=post_state["detached"],
            branches=post_state["branches"], total=post_state["total"],
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 5: Implement git_conflict_abort.py**

```python
# tools/webapi/git_conflict_abort.py
"""POST /spcode/git-conflict-abort — abort merge/cherry-pick/revert.

Spec: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md §8
v2.22.0 (2026-07-28).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _detect_conflict_operation,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _read_operation_ref,
    _read_post_mutation_branch_state,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_ABORT_CMD = {
    "merge": ["merge", "--abort"],
    "cherry_pick": ["cherry-pick", "--abort"],
    "revert": ["revert", "--abort"],
}


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-conflict-abort handler."""
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. preflight ──
    err, ctx = await _git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("aborted", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 2. conflict state check ──
    operation = await _detect_conflict_operation(git_bin, directory)
    if operation is None:
        return _make_envelope(success=False, reason=ReasonCode.NO_CONFLICT_IN_PROGRESS, elapsed_ms=_elapsed(), aborted=False, directory=directory, umo=effective_umo, worktree=directory)

    # ── 3. read operation ref before abort ──
    op_sha, _ = await _read_operation_ref(git_bin, directory, operation)

    # ── 4. execute abort ──
    cmd_parts = _ABORT_CMD[operation]
    result = await _run_git_async(
        [git_bin, "-C", directory] + cmd_parts, encoding="utf-8", timeout=15.0,
    )
    if not result["ok"]:
        return _make_envelope(
            success=False, reason=ReasonCode.GIT_ERROR, elapsed_ms=_elapsed(),
            aborted=False, operation=operation,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    # ── 5. verify sentinel gone ──
    still = await _detect_conflict_operation(git_bin, directory)
    if still is not None:
        return _make_envelope(
            success=False, reason=ReasonCode.GIT_ERROR, elapsed_ms=_elapsed(),
            aborted=False, operation=operation,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr="abort did not clear sentinel file",
        )

    # ── 6. readback ──
    post_state = await _read_post_mutation_branch_state(git_bin, directory)

    logger.info("git-conflict-abort: %s (ref=%s, umo=%s)", operation, op_sha[:12], effective_umo)
    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            aborted=True, operation=operation, operation_ref=op_sha,
            current=post_state["current"], detached=post_state["detached"],
            branches=post_state["branches"], total=post_state["total"],
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py -v`
Expected: All PASS

- [ ] **Step 7: Lint + commit**

```bash
ruff check tools/webapi/git_conflict_continue.py tools/webapi/git_conflict_abort.py tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py
git add tools/webapi/git_conflict_continue.py tools/webapi/git_conflict_abort.py tests/test_git_conflict_continue.py tests/test_git_conflict_abort.py
git commit -m "feat(webapi): add git-conflict-continue + git-conflict-abort endpoints"
```

---

## Task 9: Route registration + git-status extension + E2E test

**Files:**
- Modify: `tools/webapi/__init__.py` (ROUTES + HANDLERS + imports)
- Modify: `tools/webapi/git_status.py` (append operation/in_conflict)
- Modify: `tests/test_webapi_end_to_end.py` (route count 40 → 46)
- Create: `tests/test_git_merge_cherry_pick_e2e.py`

**Interfaces:**
- Consumes: All 6 new handler modules
- Produces: Registered routes accessible via AstrBot web framework

- [ ] **Step 1: Add imports and ROUTES entries to __init__.py**

Add to the import block in `tools/webapi/__init__.py`:

```python
from . import (
    # ... existing imports ...
    git_merge,  # v2.22.0
    git_cherry_pick,  # v2.22.0
    git_conflict_status,  # v2.22.0
    git_conflict_resolve,  # v2.22.0
    git_conflict_continue,  # v2.22.0
    git_conflict_abort,  # v2.22.0
)
```

Append to `ROUTES` list (before the closing `]`):

```python
    (
        "/spcode/git-merge",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_merge.handle,
        "git merge <source>（合并分支/tag/SHA 到当前 HEAD）",
    ),
    (
        "/spcode/git-cherry-pick",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_cherry_pick.handle,
        "git cherry-pick <ref>（拣选单 commit 到当前 HEAD）",
    ),
    (
        "/spcode/git-conflict-status",  # v2.22.0 (2026-07-28)
        ["GET"],
        git_conflict_status.handle,
        "查询当前冲突状态（操作类型 + 冲突文件 + hunk 详情 + 三路原文）",
    ),
    (
        "/spcode/git-conflict-resolve",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_conflict_resolve.handle,
        "解决冲突文件（按 hunk 选择 / 整文件 ours/theirs / custom content）",
    ),
    (
        "/spcode/git-conflict-continue",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_conflict_continue.handle,
        "冲突全部解决后继续完成 merge/cherry-pick/revert",
    ),
    (
        "/spcode/git-conflict-abort",  # v2.22.0 (2026-07-28)
        ["POST"],
        git_conflict_abort.handle,
        "中止当前 merge/cherry-pick/revert 操作",
    ),
```

Append to `HANDLERS` dict:

```python
    "handle_post_git_merge": git_merge.handle,
    "handle_post_git_cherry_pick": git_cherry_pick.handle,
    "handle_get_git_conflict_status": git_conflict_status.handle,
    "handle_post_git_conflict_resolve": git_conflict_resolve.handle,
    "handle_post_git_conflict_continue": git_conflict_continue.handle,
    "handle_post_git_conflict_abort": git_conflict_abort.handle,
```

- [ ] **Step 2: Extend git_status.py**

In `tools/webapi/git_status.py`, after the preflight succeeds and before assembling the final envelope, add:

```python
    # v2.22.0: lightweight conflict state detection
    from ._helpers import _detect_conflict_operation

    operation = await _detect_conflict_operation(git_bin, directory)
```

Then add `operation=operation, in_conflict=operation is not None` to the `_make_envelope(...)` call's kwargs.

- [ ] **Step 3: Update route count assertion**

In `tests/test_webapi_end_to_end.py`, find the route count assertion and change:

```python
assert len(ROUTES) == 46  # 40 + 6 (v2.22.0 merge/cherry-pick/conflict)
```

- [ ] **Step 4: Write E2E test**

```python
# tests/test_git_merge_cherry_pick_e2e.py
"""E2E lifecycle test: merge conflict → resolve → continue; cherry-pick; abort.

Requires git on PATH. Uses real git repos in tmp_path.
"""

import subprocess
import sys

import pytest

_NO_WINDOW = {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=10, **_NO_WINDOW)


def _init_repo(path):
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@test.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)


class TestMergeConflictLifecycle:
    def test_full_lifecycle(self, tmp_path):
        repo = str(tmp_path / "repo")
        import os
        os.makedirs(repo)
        _init_repo(repo)

        # Initial commit on main
        (tmp_path / "repo" / "file.txt").write_text("base\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "init"], cwd=repo)

        # Feature branch modifies file
        _git(["checkout", "-b", "feature"], cwd=repo)
        (tmp_path / "repo" / "file.txt").write_text("theirs change\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "feature change"], cwd=repo)

        # Main modifies same file
        _git(["checkout", "main"], cwd=repo)
        (tmp_path / "repo" / "file.txt").write_text("ours change\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "main change"], cwd=repo)

        # Merge → conflict
        result = _git(["merge", "--no-edit", "feature"], cwd=repo)
        assert result.returncode != 0
        assert "conflict" in (result.stdout + result.stderr).lower()

        # Verify MERGE_HEAD exists
        assert (tmp_path / "repo" / ".git" / "MERGE_HEAD").exists()

        # Resolve: accept theirs
        _git(["checkout", "--theirs", "--", "file.txt"], cwd=repo)
        _git(["add", "file.txt"], cwd=repo)

        # Continue (commit)
        result = _git(["commit", "--no-edit"], cwd=repo)
        assert result.returncode == 0

        # Verify merge commit
        log = _git(["log", "--oneline", "-1"], cwd=repo)
        assert "Merge" in log.stdout or "feature" in log.stdout

        # MERGE_HEAD gone
        assert not (tmp_path / "repo" / ".git" / "MERGE_HEAD").exists()


class TestCherryPick:
    def test_successful_pick(self, tmp_path):
        repo = str(tmp_path / "repo")
        import os
        os.makedirs(repo)
        _init_repo(repo)

        (tmp_path / "repo" / "a.txt").write_text("v1\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "init"], cwd=repo)

        _git(["checkout", "-b", "feature"], cwd=repo)
        (tmp_path / "repo" / "b.txt").write_text("new file\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "add b"], cwd=repo)
        sha = _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()

        _git(["checkout", "main"], cwd=repo)
        result = _git(["cherry-pick", "--no-edit", sha], cwd=repo)
        assert result.returncode == 0
        assert (tmp_path / "repo" / "b.txt").exists()


class TestAbort:
    def test_merge_abort(self, tmp_path):
        repo = str(tmp_path / "repo")
        import os
        os.makedirs(repo)
        _init_repo(repo)

        (tmp_path / "repo" / "f.txt").write_text("base\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "init"], cwd=repo)

        _git(["checkout", "-b", "feat"], cwd=repo)
        (tmp_path / "repo" / "f.txt").write_text("feat\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "feat"], cwd=repo)

        _git(["checkout", "main"], cwd=repo)
        (tmp_path / "repo" / "f.txt").write_text("main\n")
        _git(["add", "."], cwd=repo)
        _git(["commit", "-m", "main"], cwd=repo)

        _git(["merge", "--no-edit", "feat"], cwd=repo)
        assert (tmp_path / "repo" / ".git" / "MERGE_HEAD").exists()

        result = _git(["merge", "--abort"], cwd=repo)
        assert result.returncode == 0
        assert not (tmp_path / "repo" / ".git" / "MERGE_HEAD").exists()
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/test_git_merge_cherry_pick_e2e.py tests/test_webapi_end_to_end.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -x -q`
Expected: All PASS (no regressions)

- [ ] **Step 7: Lint + commit**

```bash
ruff check tools/webapi/__init__.py tools/webapi/git_status.py tests/test_webapi_end_to_end.py tests/test_git_merge_cherry_pick_e2e.py
git add tools/webapi/__init__.py tools/webapi/git_status.py tests/test_webapi_end_to_end.py tests/test_git_merge_cherry_pick_e2e.py
git commit -m "feat(webapi): register 6 new routes + git-status conflict fields + E2E test"
```

---

## Task 10: Documentation + version bump

**Files:**
- Modify: `AGENTS.md` (endpoint table, ReasonCode table, directory structure, version)
- Modify: `metadata.yaml` (version → 2.22.0)

- [ ] **Step 1: Update AGENTS.md**

- Add 6 new endpoints to the Web API endpoint table
- Add 9 new ReasonCodes to the ReasonCode table
- Add 6 new files to the directory structure
- Update route count: "40 条路由记录" → "46 条路由记录（44 个唯一路径）"
- Update version references: v2.21.1 → v2.22.0
- Add design spec reference to §设计依据文档索引

- [ ] **Step 2: Update metadata.yaml version**

```yaml
version: "2.22.0"
```

- [ ] **Step 3: Final full test run**

Run: `pytest tests/ -q`
Expected: All PASS

- [ ] **Step 4: Final lint**

Run: `ruff check .`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md metadata.yaml
git commit -m "docs: update AGENTS.md + version bump to v2.22.0"
```

---

> Author: elecvoid243 · 2026-07-28 19:32 CST
