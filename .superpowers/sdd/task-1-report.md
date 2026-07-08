# Task 1 Report — Helpers refactor

**Date:** 2026-07-06 21:12 (CST)
**Status:** DONE
**Branch:** `feature/file-discard-hunk-impl`
**Commit SHA:** `5645e65`

## Summary

Extracted two shared git porcelain X/Y constants (`X_TRULY_STAGED`, `Y_WORKTREE`) to `tools/webapi/_helpers.py` and added 9 new `ReasonCode` attributes for the upcoming `/spcode/file-discard-hunk` endpoint. Migrated `tools/webapi/file_restore.py` to import from the new shared location instead of carrying its own local copies.

## TDD cycle (executed)

1. **Read `file_restore.py` lines 330-400** to confirm the local constants being migrated:
   - `_TRULY_STAGED_X = frozenset({"M", "D", "R", "C", "T"})` (line 337)
   - `_WORKTREE_Y = frozenset({"M", "A", "D", "R", "C", "T"})` (line 338)
   - Used in `is_truly_staged = x_status in _TRULY_STAGED_X` and `is_worktree_dirty = y_status in _WORKTREE_Y` (lines 340-341)
   - Names matched the plan's expectation; no stop needed.

2. **Created `tests/test_helpers.py`** with the two failing tests verbatim from plan lines 67-104.

3. **Ran failing tests** to confirm the proper error messages:
   - `test_reason_code_has_new_patch_constants` → `AssertionError: missing patch_empty` ✅
   - `test_x_truly_staged_and_y_worktree_constants` → `ImportError: cannot import name 'X_TRULY_STAGED'` ✅

4. **Added 9 ReasonCode entries + 2 module-level frozensets to `_helpers.py`** at the exact locations the plan specified:
   - 9 new attributes appended inside `class ReasonCode` after `NOT_LOCKED` and before `class` close (line 205-216)
   - 2 new frozensets inserted as module-level constants after `class ReasonCode` and before `def _make_envelope` (line 219-224)

5. **Re-ran new tests** → 2 PASSED.

6. **Migrated `file_restore.py`**:
   - Expanded the import from `from ._helpers import _run_git_async, _validate_repo_relative_file` to a 4-name parenthesized import (alphabetical order preserved).
   - Removed the two local `_TRULY_STAGED_X = ...` / `_WORKTREE_Y = ...` lines.
   - Renamed `_TRULY_STAGED_X` → `X_TRULY_STAGED` and `_WORKTREE_Y` → `Y_WORKTREE` at the 2 call sites.
   - Confirmed no `grep` matches for old names remain.

7. **Re-ran full regression scope** → 44/44 PASSED (5 file_restore + 37 webapi_end_to_end + 2 new helpers).

8. **Ran `ruff check tools/webapi/`** → "All checks passed!" (0 errors). Also verified `ruff check tests/test_helpers.py` clean.

9. **Committed** with the exact message from plan line 192.

## Files modified

| File | Change |
|------|--------|
| `tools/webapi/_helpers.py` | +19 lines: 9 new `ReasonCode` attributes + 2 module-level `frozenset` constants |
| `tools/webapi/file_restore.py` | Net +8 / −5: import expanded to parenthesized 4-name; removed 2 local frozenset defs; renamed 2 references |
| `tests/test_helpers.py` | **New file**: 41 lines, 2 tests |

## Test results

```
tests/test_helpers.py::test_reason_code_has_new_patch_constants PASSED
tests/test_helpers.py::test_x_truly_staged_and_y_worktree_constants PASSED
tests/test_file_restore.py::test_make_file_restore_empty_envelope_has_required_fields PASSED
tests/test_file_restore.py::test_make_file_restore_success_envelope_has_restored_true PASSED
tests/test_file_restore.py::test_validate_restore_file_rejects_absolute_path PASSED
tests/test_file_restore.py::test_validate_restore_file_rejects_parent_traversal PASSED
tests/test_file_restore.py::test_validate_restore_file_rejects_dot_git PASSED
tests/test_webapi_end_to_end.py ... (37 tests) PASSED
======================= 44 passed, 7 warnings in 1.75s ========================
```

Pre-existing warnings (unrelated): `audioop` deprecation in `tencent_record_helper.py:4`, `register_star` decorator deprecation in `main.py:105`, `pytest.mark.asyncio` warnings on sync `test_file_restore.py` tests (the test file already has `pytestmark = pytest.mark.asyncio` at module level — pre-existing, not introduced by this task).

## Lint results

```
$ ruff check tools/webapi/ tests/test_helpers.py
All checks passed!
```

## Concerns

None. The plan was unambiguous, names matched the spec, all tests pass, ruff is clean.

A broader `pytest tests/` run was attempted for added safety, but timed out (>180s) on slow integration tests unrelated to this refactor (the suite includes a `test_file_remove.py` that fails to collect because `send2trash` is missing from the venv — both pre-existing conditions). The brief's required scope (`test_helpers.py` + `test_file_restore.py` + `test_webapi_end_to_end.py`) is the appropriate verification surface for this refactor, since Task 1 only touches shared helpers and file_restore's scope-detection branch — no other endpoint is affected.

## Interfaces produced (consumed by Tasks 2-5)

- `from tools.webapi._helpers import X_TRULY_STAGED, Y_WORKTREE` — `frozenset[str]`, single-char porcelain codes
- `from tools.webapi._helpers import ReasonCode.PATCH_EMPTY` / `.PATCH_TOO_LARGE` / `.PATCH_MALFORMED` / `.PATCH_UNSAFE_PATH` / `.MULTI_FILE_PATCH` / `.PATCH_FILE_MISMATCH` / `.PATCH_BINARY` / `.PATCH_CHECK_FAILED` / `.PATCH_APPLY_FAILED` — string constants

Ready for Task 2 (`_parse_patch_header`).