# Task 3 Report: file-discard-hunk envelope + handler body validation

**Status:** ✅ COMPLETED
**Date:** 2026-07-06
**Commit:** `32c3164cf24375a1fb66785056b0ff63ab191b0a`
**Test result:** 21 PASS / 0 FAIL (11 parser + 2 envelope + 8 body validation)
**Ruff:** 0 errors

---

## Summary

Implemented envelope factories + handler skeleton (steps 1-6 of spec §8) for the
new `POST /spcode/file-discard-hunk` endpoint. TDD was followed strictly:
tests were written first, confirmed to fail (10 of 10), then implementation
landed and all tests pass.

---

## Files Changed

| File | Lines Added | Lines Removed |
|------|-------------|---------------|
| `tools/webapi/file_discard_hunk.py` | 189 | 9 |
| `tests/test_file_discard_hunk.py` | 142 | 0 |
| **Total** | **322** | **9** |

---

## What was implemented

### `tools/webapi/file_discard_hunk.py`

1. **`_make_file_discard_hunk_empty_envelope(...)`** — failure-path envelope factory.
   Fields: `discarded=False`, `directory`, `umo`, `worktree`, `file`, `scope`
   (default `"unstaged"`), `hunks_reverted=0`, `patch_sha256`, `elapsed_ms`,
   `stderr`, `reason`.

2. **`_make_file_discard_hunk_success_envelope(...)`** — success-path envelope
   factory. Fields: `discarded=True`, `reason=None`, plus same set as empty
   envelope with actual values.

3. **`async def handle(plugin, **kwargs) -> dict`** — handler skeleton covering
   steps 1-6 of spec §8:
   - **Step 1:** body parse via `await web.request.json(default=None)` →
     `invalid_body` if not a dict.
   - **Step 2:** extract `file` → `missing_file` if empty/whitespace/non-str.
   - **Step 3:** extract `patch_text` → `invalid_body` / `patch_empty` /
     `patch_too_large` (>256 KB). Compute SHA-256 prefix (16 hex chars).
   - **Step 4:** extract `umo` / `worktree` (worktree consumed in Task 4).
   - **Step 5:** feature flag → `feature_disabled` if `agentsmd_enabled` or
     `codegraph_enabled` is False.
   - **Step 6:** resolve `umo` (explicit or fallback to most-recently-loaded);
     `no_project_loaded` if none.
   - **Step 7-15:** deferred to Tasks 4-5. Currently returns
     `_empty(reason=GIT_ERROR, stderr="handler incomplete (Tasks 4-5 pending)")`.

### `tests/test_file_discard_hunk.py`

Added two classes (verbatim from plan, except `_proj_state.set()` →
`_proj_state.put()` due to plan bug):

- **`TestEnvelopes`** (2 tests):
  - `test_empty_envelope_has_required_fields`
  - `test_success_envelope_has_discarded_true`

- **`TestHandlerBodyValidation`** (8 tests, each `@pytest.mark.asyncio`):
  - `test_invalid_body_when_not_dict` (str body → `invalid_body`)
  - `test_missing_file_field` (no `file` key → `missing_file`)
  - `test_empty_file_field` (whitespace-only → `missing_file`)
  - `test_patch_text_not_string` (int → `invalid_body`)
  - `test_empty_patch_text` (`""` → `patch_empty`)
  - `test_patch_too_large` (>256 KB → `patch_too_large`)
  - `test_feature_disabled_agentsmd` (config flag off → `feature_disabled`)
  - `test_no_project_loaded` (state empty → `no_project_loaded`)

### Imports housekeeping (Task 3 begins using some Task 2 imports)

- `_time` → used by `t0 = _time.time()` and `_elapsed()` → removed `# noqa: F401`.
- `SPCodeToolkit` (TYPE_CHECKING) → used in `handle(plugin: "SPCodeToolkit")`
  → removed `# noqa: F401`.
- `Path` / `_validate_worktree_param` / `X_TRULY_STAGED` / `Y_WORKTREE` /
  `_run_git_async` / `_validate_repo_relative_file` → still deferred to Tasks
  4-5 → keep `# noqa: F401` with Task-N annotations.

---

## Deviations from plan (real plan bugs found & fixed minimally)

### Bug 1: `_proj_state.set()` does not exist

The plan example code in `TestHandlerBodyValidation.mock_plugin` fixture used:

```python
_proj_state.set(
    umo="test-umo",
    directory=str(tmp_path),
    loaded_at=0,
)
```

But `tools/project/state.py` only exposes `put(umo, info)`, `get(umo)`,
`pop(umo)`, `items()`, `reset()`, `__contains__` — no `set()`.

**Fix:** Replaced with `_proj_state.put("test-umo", {"directory": ..., "loaded_at": 0})`
matching the canonical API used by all 15+ existing test fixtures
(`test_git_diff.py`, `test_git_status.py`, `test_git_stage.py`, etc.).
Added a `NOTE:` comment in the fixture explaining the deviation.

### Bug 2: `ReasonCode.MISSING_FILE` does not exist

The plan handler code used `reason=ReasonCode.MISSING_FILE` in the Step 2
branch, but the `ReasonCode` enum in `tools/webapi/_helpers.py` does not
have `MISSING_FILE`. The existing `tools/webapi/file_restore.py:149` uses
the raw string literal `"missing_file"` for the same case.

**Fix:** Replaced with `reason="missing_file"` (raw string literal) for
consistency with the existing handler convention. Added a `NOTE:` comment
explaining the rationale. Test assertions are unaffected because they only
check `result["data"]["reason"] == "missing_file"`.

### Bug 3: `worktree_param` triggers ruff F841

After implementing the handler, ruff flagged
`worktree_param = wt_raw if ... else None` as a local variable assigned but
never used. This is expected — it's the worktree parameter that gets passed
to `_validate_worktree_param()` in Task 4's step 7.

**Fix:** Added `  # noqa: F841  # consumed in Task 4 (_validate_worktree_param)`
to the line so ruff accepts it. Cleaner alternative (extracting in Task 4)
deferred to that task.

---

## Verification commands

```bash
pytest tests/test_file_discard_hunk.py -v
# 21 passed, 2 warnings in 0.21s
#   TestParsePatchHeader:    11 PASS
#   TestEnvelopes:            2 PASS
#   TestHandlerBodyValidation: 8 PASS

ruff check tools/webapi/file_discard_hunk.py tests/test_file_discard_hunk.py
# All checks passed!

pytest tests/test_file_discard_hunk.py tests/test_file_restore.py tests/test_helpers.py -v
# 28 passed (sanity check — no regression on existing endpoints)
```

---

## Next steps (Task 4)

Per the plan, Task 4 will replace the temporary `GIT_ERROR` placeholder
return at the end of `handle()` with:

- Step 7: `worktree_param` → `_validate_worktree_param` (6-step defense)
- Step 8: `Path(directory).is_dir()` → `directory_missing`
- Step 9: `git rev-parse --is-inside-work-tree` → `not_a_git_repo` /
  `git_unavailable` / `git_error`
- Step 10: `_validate_repo_relative_file` → `path_unsafe` / `file_not_found`
- Step 11: `_parse_patch_header(patch_text, expected_file=file)` → patch
  parsing errors

The `TestHandlerFileSafety` class (9 tests) will land with Task 4.

---

**Implementer:** discard_hunk_impl_3
**Plan ref:** `docs/superpowers/plans/2026-07-06-file-discard-hunk.md` lines 324-538 (Task 3)