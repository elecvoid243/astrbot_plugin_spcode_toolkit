# Task 5 Report — file-discard-hunk scope detect + git apply (steps 12-15)

**Date:** 2026-07-06 (CST)
**Author:** discard_hunk_impl_5 (subagent)
**Plan ref:** `docs/superpowers/plans/2026-07-06-file-discard-hunk.md` lines 798-1100 (Task 5)

---

## Status: ✅ COMPLETE

All steps completed per TDD. Final state of `tests/test_file_discard_hunk.py` = **37 passing**.

**Commit SHA:** `e512cf4` — `feat(webapi): add file-discard-hunk scope detect + git apply --reverse (steps 12-15)`

---

## What was implemented

### Files modified (3)

1. **`tools/webapi/file_discard_hunk.py`**
   - Added `_classify_apply_failure(stderr)` helper just above `handle()`. Maps
     `git apply --check --reverse` stderr to a reason code via 3-tier priority
     (malformed → context/whitespace → git_error fallback).
   - Replaced the second `GIT_ERROR` placeholder (Task 4 sentinel) with the
     spec §8 steps 12-15 logic:
     - **Step 12** — `git status --porcelain -- <file>` scope auto-detect.
       Reads X/Y columns: `??` → `untracked_file`; empty → `not_modified`;
       intent-to-add (`' 'A'`) and truly-staged (X ∈ {M,D,R,C,T}) both go
       `--cached`; everything else (` M`, `MM`, `AM`, etc.) goes unstaged.
     - **Step 13** — `git apply --check --reverse [--cached] --whitespace=error
       --no-unsafe-paths -c color.ui=never` (dry-run). On failure, maps stderr
       through `_classify_apply_failure()`.
     - **Step 14** — `git apply --reverse [--cached]` (real apply). On failure
       returns `patch_apply_failed`.
     - **Step 15** — `logger.info` audit line + success envelope via
       `_make_file_discard_hunk_success_envelope`.

2. **`tools/webapi/_helpers.py`**
   - Added 2 ReasonCode constants:
     - `NOT_MODIFIED = "not_modified"` — porcelain empty (file already clean).
     - `UNTRACKED_FILE = "untracked_file"` — porcelain X/Y = `??`.
   - **Why:** The plan code references `ReasonCode.NOT_MODIFIED` and
     `ReasonCode.UNTRACKED_FILE` but these were never declared (Task 1 only
     added the 9 patch-related reason codes + `FILE_NOT_FOUND` later in Task 4).
     Following the same convention as Task 4's `FILE_NOT_FOUND` addition.

3. **`tests/test_file_discard_hunk.py`**
   - Appended `TestHandlerGitOps` class with 7 tests using real `tmp_path`
     git repos (no mocks for git itself, only monkeypatch for the
     concurrent-modification case).
   - Fixed plan bug: `state.set(umo=..., directory=..., loaded_at=...)` does
     not exist — replaced with the canonical `_proj_state.put(umo, info)` API
     (same fix as Task 3 and Task 4).

---

## Minimal bugs found and fixed

| # | Symptom | Root cause | Minimal fix |
|---|---------|-----------|------------|
| 1 | `AttributeError: type object 'ReasonCode' has no attribute 'NOT_MODIFIED'` | Plan uses `ReasonCode.NOT_MODIFIED` and `ReasonCode.UNTRACKED_FILE` (steps 12 branches) but neither constant exists in `tools/webapi/_helpers.py` (Task 1 + Task 4 only added `FILE_NOT_FOUND` and 9 patch codes, not these) | Added both constants under the `# v2.16.0 file-discard-hunk` block, mirroring Task 4's `FILE_NOT_FOUND` style |
| 2 | `AttributeError: '_proj_state' object has no attribute 'set'` in `TestHandlerGitOps.plugin` fixture | Plan example used `_proj_state.set(umo=..., directory=..., loaded_at=...)` (kwarg style); the canonical API is `_proj_state.put(umo, info_dict)` (also fixed in Tasks 3 & 4) | Used `_proj_state.put("u", {"directory": str(repo), "loaded_at": 0})`; same approach as TestHandlerFileSafety above |
| 3 | `TestHandlerGitOps.test_reverts_unstaged_hunk` had a large block of dead-code attempt variables (`patch`, `revert_patch`) before the final `forward_patch` was actually used | The plan example wrote a confused draft of the patch (the "revert direction" / "forward direction" notes) and never deleted the dead code | Kept only the working `forward_patch` block; stripped the 25 lines of dead-code patch construction |

---

## TDD adherence

- **Step 5.1 (write failing tests)** — appended `TestHandlerGitOps` to
  `tests/test_file_discard_hunk.py` BEFORE implementing the handler. Initial
  run: 7 FAIL (each returning `git_error` from the Task 4 placeholder). ✅
- **Step 5.2 (confirm fail)** — pytest output captured during the failing run
  shows all 7 returning the Task 4 `GIT_ERROR` placeholder
  (or `git_error` for `test_patch_check_failed_whitespace`). ✅
- **Step 5.3 (implement)** — `_classify_apply_failure` + steps 12-15 inserted
  verbatim from the plan (with the 3 real-bug fixes noted above). ✅

---

## Test results

### Task 5 (this task's 7 new tests)

```
$ pytest tests/test_file_discard_hunk.py::TestHandlerGitOps -v
collected 7 items

tests/test_file_discard_hunk.py::TestHandlerGitOps::test_reverts_unstaged_hunk PASSED
tests/test_file_discard_hunk.py::TestHandlerGitOps::test_reverts_staged_hunk PASSED
tests/test_file_discard_hunk.py::TestHandlerGitOps::test_not_modified PASSED
tests/test_file_discard_hunk.py::TestHandlerGitOps::test_untracked_file PASSED
tests/test_file_discard_hunk.py::TestHandlerGitOps::test_patch_check_failed_context_mismatch PASSED
tests/test_file_discard_hunk.py::TestHandlerGitOps::test_patch_check_failed_whitespace PASSED
tests/test_file_discard_hunk.py::TestHandlerGitOps::test_patch_apply_failed_concurrent PASSED
======================== 7 passed, 2 warnings in 5.64s ========================
```

### Full `test_file_discard_hunk.py` (37 tests = 11+2+8+9+7)

```
$ pytest tests/test_file_discard_hunk.py -v
collected 37 items
...
tests/test_file_discard_hunk.py::TestParsePatchHeader::test_*  (11 PASSED)
tests/test_file_discard_hunk.py::TestEnvelopes::test_*         (2 PASSED)
tests/test_file_discard_hunk.py::TestHandlerBodyValidation::*  (8 PASSED)
tests/test_file_discard_hunk.py::TestHandlerFileSafety::*      (9 PASSED)
tests/test_file_discard_hunk.py::TestHandlerGitOps::*          (7 PASSED)
======================= 37 passed, 2 warnings in 10.14s =======================
```

### Regression check

```
$ pytest tests/test_file_restore.py tests/test_helpers.py tests/test_webapi_end_to_end.py -v
======================= 44 passed, 7 warnings in 1.70s ========================
```

No regression on existing endpoints.

### Ruff

```
$ ruff check tools/webapi/file_discard_hunk.py tests/test_file_discard_hunk.py
All checks passed!

$ ruff check tools/webapi/_helpers.py
All checks passed!
```

---

## Commit

```
e512cf4 feat(webapi): add file-discard-hunk scope detect + git apply --reverse (steps 12-15)
```

3 files changed, 344 insertions(+), 6 deletions(-):
- `tests/test_file_discard_hunk.py`   | +237 -2
- `tools/webapi/_helpers.py`          |   +2  -0
- `tools/webapi/file_discard_hunk.py` | +105 -4

---

## Plan deviation summary

3 minor plan deviations, all surfaced as real bugs and fixed minimally:

1. Added `NOT_MODIFIED` and `UNTRACKED_FILE` ReasonCode constants
   (missing in plan Task 1).
2. Replaced `_proj_state.set()` with `_proj_state.put()` in the
   `plugin` fixture (canonical API; same fix as Tasks 3 & 4).
3. Stripped dead-code patch construction in `test_reverts_unstaged_hunk`
   (plan example left 25 lines of abandoned draft + `revert_patch` variable
   in place before the actually-used `forward_patch`).

No deviations in handler implementation — steps 12-15 follow the plan code
verbatim, and `_classify_apply_failure` is exactly as specified.

---

## Handler status: COMPLETE

After this task, the `POST /spcode/file-discard-hunk` handler covers the
full spec §8 flow (steps 1-15). Subsequent Task 6 will register the
route in `tools/webapi/__init__.py` and add the e2e test for the
HANDLERS dict entry.
