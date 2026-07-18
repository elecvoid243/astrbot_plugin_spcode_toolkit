# Task 2 Report — NEEDS_CONTEXT

## Status: NEEDS_CONTEXT (brief internal inconsistency — STOPPED)

I followed the brief through Step 1 (tests appended, file syntax valid)
and Step 2 (RED confirmed: `AttributeError: module
'tools.webapi.git_stats' has no attribute 'handle'`). I then appended the
verbatim handler code from the brief (Step 3) — file syntax still valid.

At Step 5 (run new tests), I encountered a hard contradiction within the
brief itself and stopped per the system prompt's Rule 7 ("If
requirements are unclear, dependencies are missing, or you feel stuck:
STOP and report BLOCKED or NEEDS_CONTEXT with specifics. Never guess.").

---

## The contradiction

The brief's verbatim `_ISO_PARAM_RE` regex **rejects** the brief's
verbatim test data for `test_handle_since_until_passthrough`.

### Brief regex (verbatim, in Step 3 code block)
```python
_ISO_PARAM_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}([+-]\d{2}:?\d{2}|Z))?$"
)
```

### Brief test data (verbatim, in Step 1 code block)
```python
query={"since": "2026-07-11T00:00:00", "until": "2026-07-11T23:59:59"},
```

### Behaviour I verified
| Input | Brief regex | Should pass per brief test? |
|---|---|---|
| `2026-07-11T00:00:00` | **No match** | **Yes** (since param) |
| `2026-07-11T23:59:59` | **No match** | **Yes** (until param) |
| `not-a-date` | No match | No (correct) |
| `2026/07/10` | No match | No (correct) |

Root cause: the inner group `([+-]\d{2}:?\d{2}|Z)` is **required**
(though the outer `T…` group is optional). So when the input ends after
the seconds and there is no timezone suffix, the optional `T…` group
backtracks to "not taken" and the trailing chars fail `$`.

The spec (`docs/superpowers/specs/2026-07-18-git-stats-endpoint-design.md`
§3.1) only requires "ISO 日期/时间" — no mention of mandatory timezone.

### Verified via direct execution
```python
import re
_ISO_PARAM_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}([+-]\d{2}:?\d{2}|Z))?$"
)
assert _ISO_PARAM_RE.match("2026-07-10")               # True
assert _ISO_PARAM_RE.match("2026-07-11T00:00:00+08:00")  # True
assert _ISO_PARAM_RE.match("2026-07-11T00:00:00Z")     # True
assert _ISO_PARAM_RE.match("2026-07-11T00:00:00")      # False ← conflict
```

---

## Why I'm not just patching it

The dispatcher explicitly forbids guessing. The brief says "use exact
values verbatim", but its two verbatim pieces of code contradict each
other. I cannot satisfy "verbatim regex" AND "verbatim passing test"
simultaneously without picking one over the other.

I have NOT committed anything in Task 2 yet. My working tree currently
has:
- `tests/test_git_stats.py` — 11 handler tests appended (RED confirmed
  via the `AttributeError`, all collection clean)
- `tools/webapi/git_stats.py` — handler + ETag helper appended from the
  brief verbatim (still uncommitted, syntax-valid)
- No changes to `tools/webapi/__init__.py` (route not registered)
- No changes to `tests/test_webapi_end_to_end.py` (count not bumped)

I am stopping here per the dispatcher rules. The planner should decide.

---

## Plausible minimal fix (FOR PLANNER REVIEW ONLY)

A single-character edit makes both the regex AND the test agree:

```python
# Make inner tz group ALSO optional
_ISO_PARAM_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}([+-]\d{2}:?\d{2}|Z)?)?$"
)
#                                          ^ added one ?
```

Verified against all cases above — all 5 valid forms pass, all 3 invalid
forms still reject.

If the planner confirms this intent, I can:
1. Apply the 1-char fix to the regex.
2. Re-run `pytest tests/test_git_stats.py -q` (expect 18 pass).
3. Proceed with Steps 4-7 (route registration, end-to-end count bump,
   full regression, ruff, commit).

If the planner's intent is different (e.g. tests should use
`+08:00`-suffixed times, or the spec actually requires a tz), the
planner should say so and I'll adjust accordingly.

---

## What is currently in the working tree (uncommitted)

```
$ git status --short
 M .superpowers/sdd/progress.md
 M .superpowers/sdd/task-1-brief.md
 M .superpowers/sdd/task-2-brief.md
 M tests/test_git_stats.py       (Task 1 + 11 Task 2 handler tests appended)
 M tools/webapi/git_stats.py     (Task 1 + handler + ETag helper appended)
```

No route registration, no end-to-end count bump. All Task 1 files
(commit `eb16800`) are clean.

## Test status

- RED (Step 2) — confirmed via single failing handler test:
  `AttributeError: module 'tools.webapi.git_stats' has no attribute 'handle'`
- After Step 3 (handler appended): 17 pass, 1 fails
  (`test_handle_since_until_passthrough` — fails on the regex mismatch
  above; same data, same code, same root cause as before).

## Commits

None for Task 2. Task 1 commit `eb16800` is the only one on top of
`785fee1`.


---

## Fix applied — spcode_backend_fixer (2026-07-18 14:34 CST)

Applied the human-selected Option A relaxation to both `_ISO_PARAM_RE` in
`tools/webapi/git_stats.py` and the local `iso_date_re` in
`tools/webapi/git_log.py`. Bare `YYYY-MM-DDTHH:MM:SS` values are now accepted
while malformed values such as `not-a-date` and `2026/07/10` remain rejected.
Appended the git-log bare-datetime regression test, registered
`GET /spcode/git-stats`, and updated the route table expectations from 35 to 36.

### Verification

All commands were run from the worktree root with the required AstrBot
virtual-environment interpreter.

Focused regression command:

```text
F:\github\Astrbot\.venv\Scripts\python.exe -m pytest tests/test_git_stats.py tests/test_git_log.py -q
39 passed, 2 warnings in 33.67s
```

Endpoint and route coverage:

```text
F:\github\Astrbot\.venv\Scripts\python.exe -m pytest tests/test_git_stats.py tests/test_webapi_end_to_end.py -q
78 passed, 2 warnings in 24.02s
```

Full-suite command:

```text
F:\github\Astrbot\.venv\Scripts\python.exe -m pytest tests/ -q
1337 passed, 5 skipped, 2 warnings in 423.99s (0:07:03)
```

The two test warnings are the pre-existing AstrBot-core `audioop`
DeprecationWarning and the pre-existing `register_star` decorator
DeprecationWarning from `main.py:105`.

Ruff commands and outputs:

```text
F:\github\Astrbot\.venv\Scripts\python.exe -m ruff format tools/webapi/git_stats.py tests/test_git_stats.py tools/webapi/__init__.py tests/test_webapi_end_to_end.py tools/webapi/git_log.py tests/test_git_log.py
3 files reformatted, 3 files left unchanged

F:\github\Astrbot\.venv\Scripts\python.exe -m ruff check tools/webapi/git_stats.py tests/test_git_stats.py tools/webapi/__init__.py tests/test_webapi_end_to_end.py tools/webapi/git_log.py tests/test_git_log.py
All checks passed!
```

### Commits

- `5464c7b feat: add GET /spcode/git-stats endpoint with server-side aggregation`
- `8804544 fix: accept bare ISO datetimes in git-log since/until validation`

### Concerns

The pre-existing `register_star` DeprecationWarning remains in addition to the
explicitly tolerated `audioop` warning; no unrelated production/test-harness
change was made to suppress it.
