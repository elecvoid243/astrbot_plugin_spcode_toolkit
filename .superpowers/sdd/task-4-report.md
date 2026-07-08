# Task 4 Report — file-discard-hunk file safety + git probe + patch parsing (steps 7-11)

**Date:** 2026-07-06 (CST)
**Author:** discard_hunk_impl_4 (subagent)
**Plan ref:** `docs/superpowers/plans/2026-07-06-file-discard-hunk.md` lines 539-797

---

## Status: ✅ COMPLETE

All steps completed per TDD. Final state of `tests/test_file_discard_hunk.py` = **30 passing**.

## What was implemented

### Files modified (3)

1. **`tools/webapi/file_discard_hunk.py`** — replaced Task 3 placeholder `GIT_ERROR` return with:
   - **Step 7**: `worktree_param` → 6-step defense via `_validate_worktree_param`; rejection returns envelope with `wt_err` ("worktree_invalid").
   - **Step 8**: `Path(directory).is_dir()` check → `directory_missing` if absent.
   - **Step 9**: `git rev-parse --is-inside-work-tree` probe → `not_a_git_repo` / `git_unavailable` / `git_error`.
   - **Step 10**: `_validate_repo_relative_file()` 4-step defense → `path_unsafe`; then `target.exists()` → `file_not_found`.
   - **Step 11**: `_parse_patch_header()` fail-fast on `diff --git` / `@@` / binary / path safety / file mismatch — returns `patch_meta.err` as reason.
   - **NEW placeholder** for steps 12-15 (deferred to Task 5): `reason=ReasonCode.GIT_ERROR, stderr="handler incomplete (Task 5 pending)"`.
   - Updated `_run_git_async` F401 comment: "Task 5" → "Task 4 (+ Task 5)".

2. **`tools/webapi/_helpers.py`** — real bug fix:
   - Added `FILE_NOT_FOUND = "file_not_found"` to `ReasonCode` class.
   - **Why:** Step 10 of the plan references `ReasonCode.FILE_NOT_FOUND`, but this constant was never declared in `_helpers.py` (Step 1 of Task 1 only declared the 9 patch-related reason codes + existing ones, but `file_not_found` was missing). Adding minimally — just one line under the `# v2.16.0 file-discard-hunk` block.

3. **`tests/test_file_discard_hunk.py`** — appended `TestHandlerFileSafety` (9 tests):
   - `test_directory_missing` — points state to a guaranteed-missing `_DELETED_BY_TEST` suffix path (Windows-friendly; avoids `.git/objects` PermissionError that `shutil.rmtree` triggers on Windows for locked object files).
   - `test_not_a_git_repo` — non-git `tmp_path`; uses inline `_Req` class instead of `post_body` fixture (the plan code had a broken `post_body({...})` call that referenced an undefined fixture in `test_not_a_git_repo` and was redundantly overridden).
   - `test_path_unsafe_absolute` / `_parent_traversal` / `_dot_git` → `path_unsafe`.
   - `test_file_not_found` → `file_not_found`.
   - `test_worktree_invalid_six_step_defense` — 5 attack vectors (`../escape`, `/etc/passwd`, `C:\Windows`, `.git/HEAD`, `non/existent/dir`); vector 6 (git-common-dir mismatch) requires mocking `_validate_worktree_param` itself and is covered in production by tools/_helpers.py:610 step 6 — comment in test explains the omission.
   - `test_patch_file_mismatch_in_handler` → `patch_file_mismatch`.
   - `test_multi_file_patch_in_handler` → `multi_file_patch`.
   - Added `from pathlib import Path` to test file imports (the plan's `-> "Path"` string-forward-reference raised ruff `F821`).

### Minimal bugs fixed

| # | Symptom | Root cause | Minimal fix |
|---|---------|-----------|-------------|
| 1 | `test_file_not_found` raised `AttributeError: type object 'ReasonCode' has no attribute 'FILE_NOT_FOUND'` | Plan step 10 references `ReasonCode.FILE_NOT_FOUND` but constant was never added in Task 1 | Added `FILE_NOT_FOUND = "file_not_found"` to `ReasonCode` class in `tools/webapi/_helpers.py` (1 line, 1 modified file) |
| 2 | `test_directory_missing` raised `PermissionError [WinError 5]` on `shutil.rmtree` of git repo | Windows transient file locks on `.git/objects/XX/YY` blocks rmdir; the plan's rmtree approach is not Windows-safe | Replaced physical `rmtree` with state-repoint: prefix path with `_DELETED_BY_TEST` so `Path.is_dir()` deterministically returns False |
| 3 | `test_not_a_git_repo` had `post_body({...})` calling undefined fixture then monkeypatched `web.request` to a different body | Plan code typo: `post_body` is a class-level fixture but `test_not_a_git_repo(self, tmp_path, monkeypatch)` signature did not include it as a parameter; the post_body call would raise `NameError`; the inline monkeypatch below overwrites the request anyway | Removed the `post_body({"file":...})` call; kept only the inline monkeypatch + `_Req` class with the body literal (matches plan's actual intent) |
| 4 | ruff `F821 Undefined name 'Path'` on line 306 (`def git_repo(self, tmp_path) -> "Path":`) | Plan used `"Path"` (forward reference) but did not import `Path` in the test file | Added `from pathlib import Path` to imports of `tests/test_file_discard_hunk.py` |

## Test results

```
$ pytest tests/test_file_discard_hunk.py -v --no-header
============================= test session starts =============================
collected 30 items
...
tests/test_file_discard_hunk.py::TestParsePatchHeader::*           PASSED (11)
tests/test_file_discard_hunk.py::TestEnvelopes::*                 PASSED (2)
tests/test_file_discard_hunk.py::TestHandlerBodyValidation::*     PASSED (8)
tests/test_file_discard_hunk.py::TestHandlerFileSafety::*         PASSED (9)
======================= 30 passed, 2 warnings in 4.87s ========================
```

Breakdown: 11 parser + 2 envelope + 8 body-validation + **9 file-safety (NEW)** = 30. Matches plan target.

## Ruff

```
$ ruff check tools/webapi/file_discard_hunk.py tests/test_file_discard_hunk.py
All checks passed!
```

## Regression check (broader sanity)

```
$ pytest tests/test_file_discard_hunk.py tests/test_file_restore.py tests/test_helpers.py
37 passed, 7 warnings in 4.68s
```

(30 from `test_file_discard_hunk.py` + 7 from `test_file_restore.py`/`test_helpers.py` — no regression.)

## Commit

```
88936b5 feat(webapi): add file-discard-hunk file safety + git probe + patch parsing (steps 7-11)
```

3 files changed, 312 insertions(+), 5 deletions(-).

## Plan deviation summary

4 minor plan deviations, all surfaced as real bugs and fixed minimally:

1. Added `FILE_NOT_FOUND` constant (missing in plan Task 1).
2. Replaced `shutil.rmtree` with state-repoint in `test_directory_missing` (Windows `.git/objects` race).
3. Removed redundant `post_body({...})` call from `test_not_a_git_repo` (fixture not in param list).
4. Added `from pathlib import Path` to test imports (ruff F821).

No deviations in handler implementation — steps 7-11 follow the plan code verbatim.

## Ready for Task 5

Handler now covers spec §8 steps 1-11. Steps 12-15 (scope auto-detect, `git apply --check --reverse`, `git apply --reverse`, success envelope) return the new `GIT_ERROR` placeholder. Plan Task 5 begins with the same TDD-first pattern.
