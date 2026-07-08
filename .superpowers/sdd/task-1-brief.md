# Task 1 Brief — Helpers refactor

## Task Description

**Goal:** Extract shared git porcelain X/Y constants to `tools/webapi/_helpers.py` and add 9 new `ReasonCode` constants for the upcoming `/spcode/file-discard-hunk` endpoint. Migrate `tools/webapi/file_restore.py` to use the new shared constants (removes duplication).

**Read the full task text from the plan:**
- Plan: `docs/superpowers/plans/2026-07-06-file-discard-hunk.md` lines 46-199 (the entire "Task 1" section)

**Reference spec:** `docs/superpowers/specs/2026-07-06-file-discard-hunk-design.md` §7 (auto-detect scope) and Reason code 集中表 (the table with "PATCH_*" and "MULTI_FILE_PATCH" entries).

## Files to Touch

- Modify: `tools/webapi/_helpers.py` (add 9 ReasonCode entries + 2 module-level frozensets)
- Modify: `tools/webapi/file_restore.py` (remove local constants, import from `_helpers`)
- Modify/Create: `tests/test_helpers.py` (add 2 new tests)

## Interfaces produced (consumed by Tasks 2-5)

- `from tools.webapi._helpers import X_TRULY_STAGED, Y_WORKTREE` (frozensets of single-char porcelain codes)
- `from tools.webapi._helpers import ReasonCode` — 9 new attributes:
  `PATCH_EMPTY`, `PATCH_TOO_LARGE`, `PATCH_MALFORMED`, `PATCH_UNSAFE_PATH`,
  `MULTI_FILE_PATCH`, `PATCH_FILE_MISMATCH`, `PATCH_BINARY`,
  `PATCH_CHECK_FAILED`, `PATCH_APPLY_FAILED`

## Work Directory

`F:\github\astrbot_plugin_spcode_toolkit-discard-hunk` (a worktree of the main repo on branch `feature/file-discard-hunk-impl`).

## Test Commands

```bash
# Targeted: new tests
pytest tests/test_helpers.py -v

# Regression: file_restore behavior must not change
pytest tests/test_file_restore.py -v
pytest tests/test_webapi_end_to_end.py -v

# Lint
ruff check tools/webapi/
```

## Constraints (verbatim from plan's Global Constraints)

- Python ≥ 3.10; use `str | None`, `list[str]`, `dict[str, int]`, `X | None`
- No new third-party dependencies
- Ruff configured (line-length 100) — `ruff check .` must be 0 error
- `pytest tests/` must be all PASS (allow existing `test_codegraph_*` skip)
- Do not modify `data/workspaces/`, `data/temp/` (real data)
- Do not rename existing `_conf_schema.json` fields
- `docs/` is in `.gitignore`; plans / specs stay local, do NOT `git add` them

## Report

Write your full report to: `F:\github\astrbot_plugin_spcode_toolkit-discard-hunk\.superpowers\sdd\task-1-report.md`

Then return a short status (under 15 lines) per the implementer contract in the skill template.
