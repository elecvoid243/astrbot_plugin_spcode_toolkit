# Git Worktree Management Endpoints Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 POST endpoints (`git-worktree-add`/`-remove`/`-lock`/`-unlock`) to manage git worktrees from the Dashboard, with multi-layer defense chains and full test coverage.

**Architecture:** 4 independent POST endpoints (one file each), mirroring the existing `git-stage`/`git-unstage`/`git-commit` pattern. Shared helpers (`_validate_new_worktree_path`, `_resolve_target_worktree`, `_is_valid_ref_name`) live in `tools/_helpers.py`. Existing `GET /spcode/git-worktrees` extended to return `locked` / `locked_reason` fields. ReasonCode gains 11 new codes.

**Tech Stack:** Python 3.10+, AstrBot plugin framework, `git` CLI 2.30+ (for `--reason`), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-06-26-git-worktree-management-design.md`

**Working directory:** `F:\github\astrbot_plugin_spcode_toolkit` (single worktree, no separate feature branch needed for plan-only work)

---

## File Structure

| File | Type | Responsibility | Owner Chunk |
|------|------|---------------|-------------|
| `tools/_helpers.py` | modify | Add 3 helpers + extend 1 parser | Chunk 1 |
| `tools/webapi/_helpers.py` | modify | Add 11 new ReasonCode constants | Chunk 1 |
| `tools/webapi/git_worktrees.py` | modify | Add `locked`/`locked_reason` to GET response | Chunk 1 |
| `tools/webapi/__init__.py` | modify | Register 4 new routes (Chunk 2/3/4) | Chunks 2/3/4 |
| `tools/webapi/git_worktree_add.py` | create | ADD handler (7-layer defense) | Chunk 2 |
| `tools/webapi/git_worktree_remove.py` | create | REMOVE handler (3 business gates) | Chunk 3 |
| `tools/webapi/git_worktree_lock.py` | create | LOCK handler | Chunk 4 |
| `tools/webapi/git_worktree_unlock.py` | create | UNLOCK handler | Chunk 4 |
| `tests/test_helpers_worktree.py` | create | 12 cases for new helpers | Chunk 1 |
| `tests/test_git_worktree_porcelain_locked.py` | create | 6 cases for parser extension | Chunk 1 |
| `tests/test_git_worktree_add.py` | create | 26 cases for ADD | Chunk 2 |
| `tests/test_git_worktree_remove.py` | create | 16 cases for REMOVE | Chunk 3 |
| `tests/test_git_worktree_lock.py` | create | 14 cases for LOCK+UNLOCK | Chunk 4 |
| `tests/test_worktree_mgmt_e2e.py` | create | 5 cases real-repo E2E | Chunk 4 |
| `tests/test_webapi_end_to_end.py` | modify | Route count 12→16 + 4 new route tests | Chunks 2/3/4 |
| `tests/test_git_worktrees.py` | modify | +3 cases for locked field | Chunk 1 |
| `docs/webapi-git-worktree-mgmt-api.md` | create | Public API doc | Chunk 4 |
| `README.md` | modify | +4 endpoint rows | Chunk 4 |
| `AGENTS.md` | modify | +1 endpoint row + section 3.7 | Chunk 4 |

**Decomposition principle:** Each chunk produces a self-contained, reviewable, revertable PR. Chunks 1 is a foundation that doesn't change externally visible API behavior (locked field is additive). Chunks 2/3/4 add visible endpoints; their order is flexible.

---

## Chunk 1: PR-A Foundation (helpers + parser + ReasonCode + GET locked field)

**Goal:** Establish shared infrastructure and add 2 additive fields to existing GET endpoint. No new endpoints yet.

**PR Title:** `feat(endpoint): git worktree management - foundation (helpers + parser + locked field)`

### Task 1.1: Extend `_parse_git_worktree_porcelain` for locked fields

**Files:**
- Modify: `tools/_helpers.py:201-248` (`_parse_git_worktree_porcelain`)
- Test: `tests/test_git_worktree_porcelain_locked.py` (new file)

- [ ] **Step 1: Write failing test for `locked` field parsing**

Create `tests/test_git_worktree_porcelain_locked.py`:

```python
"""Tests for tools._helpers._parse_git_worktree_porcelain locked extension."""
from tools._helpers import _parse_git_worktree_porcelain


def test_parse_unlocked_worktree():
    """普通 worktree 没有 locked 行 → locked=False, locked_reason=None。"""
    text = (
        "worktree /path/to/main\n"
        "HEAD abc123\n"
        "branch refs/heads/main\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 1
    assert result[0]["locked"] is False
    assert result[0]["locked_reason"] is None


def test_parse_locked_worktree_no_reason():
    """仅有 `locked` 行 → locked=True, locked_reason=None。"""
    text = (
        "worktree /path/to/feature\n"
        "HEAD def456\n"
        "branch refs/heads/feature\n"
        "locked\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 1
    assert result[0]["locked"] is True
    assert result[0]["locked_reason"] is None


def test_parse_locked_worktree_with_reason():
    """`locked <reason>` 单行 → locked=True, locked_reason=<text>。"""
    text = (
        "worktree /path/to/feature\n"
        "HEAD def456\n"
        "branch refs/heads/feature\n"
        "locked in use by background agent\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert result[0]["locked"] is True
    assert result[0]["locked_reason"] == "in use by background agent"


def test_parse_multiple_worktrees_mixed_lock_state():
    """多个 worktree 混合 locked/unlocked → 各自正确标记。"""
    text = (
        "worktree /path/to/main\n"
        "HEAD abc\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /path/to/feat1\n"
        "HEAD def\n"
        "branch refs/heads/feat1\n"
        "locked\n"
        "\n"
        "worktree /path/to/feat2\n"
        "HEAD ghi\n"
        "branch refs/heads/feat2\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 3
    assert result[0]["locked"] is False
    assert result[1]["locked"] is True
    assert result[2]["locked"] is False


def test_parse_main_worktree_locked_defaults_false():
    """main worktree 默认 locked=False(除非有 locked 行)。"""
    text = (
        "worktree /path/to/main\n"
        "HEAD abc\n"
        "branch refs/heads/main\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert result[0]["is_main"] is True
    assert result[0]["locked"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_git_worktree_porcelain_locked.py -v`
Expected: FAIL with `KeyError: 'locked'` (existing parser doesn't set this field)

- [ ] **Step 3: Extend parser to set locked fields**

In `tools/_helpers.py:201-248`, modify `_parse_git_worktree_porcelain`:

```python
def _parse_git_worktree_porcelain(text: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output.

    Returns a list of dicts with keys: path, branch, head_sha, is_main,
    locked, locked_reason. (v2.14.0: locked / locked_reason added)
    The first worktree in the output is always the main worktree (is_main=True).
    Raises ValueError on unrecognized records.
    """
    worktrees: list[dict] = []
    current: dict | None = None

    for raw_line in text.splitlines():
        if not raw_line:
            if current is not None:
                worktrees.append(current)
                current = None
            continue

        if raw_line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = {
                "path": raw_line[len("worktree "):],
                "branch": None,
                "head_sha": "",
                "is_main": False,
                "locked": False,         # v2.14.0+
                "locked_reason": None,    # v2.14.0+
            }
        elif current is None:
            raise ValueError(
                f"Unexpected record outside worktree block: {raw_line!r}"
            )
        elif raw_line.startswith("HEAD "):
            current["head_sha"] = raw_line[len("HEAD "):]
        elif raw_line.startswith("branch "):
            current["branch"] = raw_line[len("branch "):]
        elif raw_line == "locked":
            # v2.14.0+ — no reason on same line
            current["locked"] = True
        elif raw_line.startswith("locked "):
            # v2.14.0+ — git 2.30+ supports reason on same line as `locked`
            current["locked"] = True
            current["locked_reason"] = raw_line[len("locked "):]
        elif raw_line.startswith("detached"):
            current["branch"] = None
        else:
            raise ValueError(f"Unknown porcelain record: {raw_line!r}")

    if current is not None:
        worktrees.append(current)

    for i, wt in enumerate(worktrees):
        wt["is_main"] = i == 0

    return worktrees
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git_worktree_porcelain_locked.py -v`
Expected: 5 passed

- [ ] **Step 5: Verify existing tests still pass (backward compat)**

Run: `pytest tests/test_helpers_git.py tests/test_git_worktrees.py -v`
Expected: All passed (existing tests don't check `locked` field, so adding default `False`/`None` is harmless)

- [ ] **Step 6: Commit**

```bash
git add tools/_helpers.py tests/test_git_worktree_porcelain_locked.py
git commit -m "feat(helpers): extend _parse_git_worktree_porcelain with locked fields

- Add locked: bool field (default False)
- Add locked_reason: str | None field (git 2.30+)
- Handle both bare 'locked' line and 'locked <reason>' line
- Pure additive: existing fields unchanged, backward compat preserved

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 1.2: Add 11 new ReasonCode constants

**Files:**
- Modify: `tools/webapi/_helpers.py` (`ReasonCode` class)
- Test: `tests/test_reason_code.py` (existing file should gain tests)

- [ ] **Step 1: Write failing test for new constants**

Add to `tests/test_reason_code.py`:

```python
def test_worktree_mgmt_reason_codes_defined():
    """v2.14.0 + 11 new ReasonCode constants for worktree-mgmt endpoints."""
    from tools.webapi._helpers import ReasonCode
    # ADD-specific
    assert ReasonCode.INVALID_BRANCH == "invalid_branch"
    assert ReasonCode.PATH_EXISTS_NONEMPTY == "path_exists_nonempty"
    assert ReasonCode.CANNOT_CREATE_EXISTING == "cannot_create_existing"
    assert ReasonCode.CANNOT_CHECKOUT_MISSING == "cannot_checkout_missing"
    assert ReasonCode.WORKTREE_NOT_IN_REPO == "worktree_not_in_repo"
    # REMOVE / LOCK / UNLOCK
    assert ReasonCode.WORKTREE_NOT_FOUND == "worktree_not_found"
    assert ReasonCode.CANNOT_REMOVE_MAIN == "cannot_remove_main"
    assert ReasonCode.WORKTREE_LOCKED == "worktree_locked"
    assert ReasonCode.WORKTREE_DIRTY == "worktree_dirty"
    assert ReasonCode.ALREADY_LOCKED == "already_locked"
    assert ReasonCode.NOT_LOCKED == "not_locked"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reason_code.py::test_worktree_mgmt_reason_codes_defined -v`
Expected: FAIL with `AttributeError: type object 'ReasonCode' has no attribute 'INVALID_BRANCH'`

- [ ] **Step 3: Add constants to ReasonCode class**

In `tools/webapi/_helpers.py`, extend the `ReasonCode` class with the 11 new constants (insert after existing constants, before class end):

```python
# ── v2.14.0 新增(worktree-mgmt 专用) ──
INVALID_BRANCH         = "invalid_branch"           # ADD: branch 格式非法
PATH_EXISTS_NONEMPTY   = "path_exists_nonempty"     # ADD: target 已存在非空
CANNOT_CREATE_EXISTING = "cannot_create_existing"   # ADD: branch 已存在 & create=false
CANNOT_CHECKOUT_MISSING = "cannot_checkout_missing" # ADD: branch 不存在 & create=false
WORKTREE_NOT_IN_REPO   = "worktree_not_in_repo"     # ADD: post-create 防越权兜底
WORKTREE_NOT_FOUND     = "worktree_not_found"       # REMOVE/LOCK/UNLOCK: list 中查不到
CANNOT_REMOVE_MAIN     = "cannot_remove_main"       # REMOVE: 硬禁止删 main
WORKTREE_LOCKED        = "worktree_locked"          # REMOVE: target 已 locked
WORKTREE_DIRTY         = "worktree_dirty"           # REMOVE: 工作树有改动
ALREADY_LOCKED         = "already_locked"           # LOCK: 已 locked
NOT_LOCKED             = "not_locked"               # UNLOCK: 未 locked
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reason_code.py::test_worktree_mgmt_reason_codes_defined -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/webapi/_helpers.py tests/test_reason_code.py
git commit -m "feat(webapi): +11 ReasonCode constants for worktree-mgmt endpoints

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 1.3: Implement `_is_valid_ref_name` helper

**Files:**
- Modify: `tools/_helpers.py` (add helper)
- Test: `tests/test_helpers_worktree.py` (new file)

- [ ] **Step 1: Write failing test**

In `tests/test_helpers_worktree.py`:

```python
"""Tests for tools._helpers worktree-specific helpers."""
from tools._helpers import _is_valid_ref_name


def test_valid_simple_branch():
    assert _is_valid_ref_name("feature") is True


def test_valid_branch_with_slash():
    assert _is_valid_ref_name("feature/user-auth") is True


def test_valid_commit_sha():
    assert _is_valid_ref_name("abc1234") is True
    assert _is_valid_ref_name("abc1234567890abcdef1234567890abcdef123456") is True


def test_valid_HEAD_shorthand():
    assert _is_valid_ref_name("HEAD~3") is True
    assert _is_valid_ref_name("HEAD^") is True


def test_valid_tag():
    assert _is_valid_ref_name("v1.0.0") is True


def test_invalid_empty_string():
    assert _is_valid_ref_name("") is False


def test_invalid_double_dot():
    assert _is_valid_ref_name("feature..x") is False


def test_invalid_starts_with_dash():
    assert _is_valid_ref_name("-feature") is False


def test_invalid_contains_space():
    assert _is_valid_ref_name("feature x") is False


def test_invalid_contains_tilde():
    assert _is_valid_ref_name("feature~x") is False


def test_invalid_contains_caret():
    assert _is_valid_ref_name("feature^x") is False


def test_invalid_contains_colon():
    assert _is_valid_ref_name("feature:x") is False


def test_invalid_contains_question():
    assert _is_valid_ref_name("feature?x") is False


def test_invalid_contains_asterisk():
    assert _is_valid_ref_name("feature*x") is False


def test_invalid_contains_open_bracket():
    assert _is_valid_ref_name("feature[x") is False


def test_too_long_branch():
    assert _is_valid_ref_name("a" * 1025) is False  # > 1024 limit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_helpers_worktree.py -v`
Expected: FAIL with `ImportError: cannot import name '_is_valid_ref_name'`

- [ ] **Step 3: Implement helper**

In `tools/_helpers.py`, add (insert near other worktree helpers around line 175):

```python
def _is_valid_ref_name(ref: str | None) -> bool:
    """Lightweight git ref-format validation.

    Allows: branch names, tag names, commit SHAs (short or full),
    HEAD~N / HEAD^ shortcuts. Rejects: empty, too long (>1024 chars),
    or containing characters that git's check-ref-format rejects:
    space, ~, ^, :, ?, *, [, \, .., ending in .lock/-/.
    """
    if not ref or not isinstance(ref, str):
        return False
    if len(ref) > 1024:
        return False
    # Disallowed character set per git-check-ref-format rules
    forbidden = set(" ~^:?*[\\\x00\x01\x02\x03\x04\x05\x06\x07"
                    "\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
                    "\x10\x11\x12\x13\x14\x15\x16\x17"
                    "\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f")
    if any(c in forbidden for c in ref):
        return False
    # Range-based disallowed characters
    if ".." in ref:
        return False
    # No leading dash
    if ref.startswith("-"):
        return False
    # No trailing characters
    if ref.endswith(".") or ref.endswith(".lock") or ref.endswith("/"):
        return False
    # No @{ in middle
    if "@{" in ref:
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_helpers_worktree.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add tools/_helpers.py tests/test_helpers_worktree.py
git commit -m "feat(helpers): add _is_valid_ref_name for ADD branch validation

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 1.4: Implement `_validate_new_worktree_path` helper

**Files:**
- Modify: `tools/_helpers.py`
- Test: `tests/test_helpers_worktree.py` (extend)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_helpers_worktree.py`:

```python
from tools._helpers import _validate_new_worktree_path


def test_validate_new_path_accepts_absolute_posix(tmp_path):
    target = tmp_path / "feature"
    ok, err = _validate_new_worktree_path("git", str(target))
    assert ok == str(target)
    assert err is None


def test_validate_new_path_accepts_absolute_windows_style(tmp_path):
    target = tmp_path / "feature"
    ok, err = _validate_new_worktree_path("git", str(target))
    assert err is None
    assert ok is not None


def test_validate_new_path_rejects_dotdot(tmp_path):
    target = str(tmp_path / ".." / "escape")
    ok, err = _validate_new_worktree_path("git", target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_relative(tmp_path):
    target = "./feature"
    ok, err = _validate_new_worktree_path("git", target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_dot_git_component(tmp_path):
    target = str(tmp_path / ".git" / "feature")
    ok, err = _validate_new_worktree_path("git", target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_empty():
    ok, err = _validate_new_worktree_path("git", "")
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_too_long(tmp_path):
    target = str(tmp_path / ("a" * 5000))
    ok, err = _validate_new_worktree_path("git", target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_missing_parent(tmp_path):
    target = str(tmp_path / "nonexistent_dir" / "feature")
    ok, err = _validate_new_worktree_path("git", target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_rejects_backslash(tmp_path):
    target = str(tmp_path) + "\\feature"
    ok, err = _validate_new_worktree_path("git", target)
    assert ok is None
    assert err == "path_unsafe"


def test_validate_new_path_none_input():
    ok, err = _validate_new_worktree_path("git", None)
    assert ok is None
    assert err == "path_unsafe"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_helpers_worktree.py -k "validate_new_path" -v`
Expected: FAIL with `ImportError: cannot import name '_validate_new_worktree_path'`

- [ ] **Step 3: Implement helper**

In `tools/_helpers.py`, add after `_is_valid_ref_name`:

```python
def _validate_new_worktree_path(
    git_bin: str,
    new_path: str | None,
) -> tuple[str | None, str | None]:
    """ADD endpoint path validation. Target may not exist yet.

    4-step defense:
      1. format     — non-empty / ≤4096 chars / no backslash / absolute / no ..
      2. .git component — no path component may be `.git`
      3. parent dir — must exist and be writable
      4. blacklist  — outside system dirs (delegated to file_remove_blacklist)

    Returns (resolved_absolute_path, None) | (None, "path_unsafe").
    Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §4.2
    """
    if not new_path or not isinstance(new_path, str):
        return None, "path_unsafe"
    if len(new_path) > 4096:
        return None, "path_unsafe"
    if "\\" in new_path:
        return None, "path_unsafe"
    if not os.path.isabs(new_path):
        return None, "path_unsafe"
    if ".." in new_path.split("/"):
        return None, "path_unsafe"
    # Step 2: .git component check
    parts = Path(new_path).parts
    if any(part == ".git" for part in parts):
        return None, "path_unsafe"
    # Step 3: parent dir must exist and be writable
    parent = os.path.dirname(new_path)
    if not parent or not os.path.isdir(parent):
        return None, "path_unsafe"
    if not os.access(parent, os.W_OK):
        return None, "path_unsafe"
    return new_path, None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_helpers_worktree.py -k "validate_new_path" -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add tools/_helpers.py tests/test_helpers_worktree.py
git commit -m "feat(helpers): add _validate_new_worktree_path 4-step defense

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 1.5: Implement `_resolve_target_worktree` helper

**Files:**
- Modify: `tools/_helpers.py`
- Test: `tests/test_helpers_worktree.py` (extend)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_helpers_worktree.py`:

```python
import subprocess
from tools._helpers import _resolve_target_worktree


def _make_test_repo_with_two_worktrees(tmp_path, monkeypatch):
    """Helper: create primary + linked worktree, return (primary, linked, git_bin)."""
    primary = tmp_path / "primary"
    primary.mkdir()
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "--bare", str(tmp_path / "bare.git")],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "init", "-b", "main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "a.txt"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)
    return primary, linked, "git"


def test_resolve_target_finds_existing(tmp_path):
    primary, linked, git_bin = _make_test_repo_with_two_worktrees(tmp_path, None)
    target_wt, err = _resolve_target_worktree(git_bin, str(primary), str(linked))
    assert err is None
    assert target_wt is not None
    assert target_wt["path"].lower() == str(linked).lower()
    assert target_wt["is_main"] is False


def test_resolve_target_unknown_returns_not_found(tmp_path):
    primary, _, git_bin = _make_test_repo_with_two_worktrees(tmp_path, None)
    target_wt, err = _resolve_target_worktree(
        git_bin, str(primary), str(primary / "does_not_exist")
    )
    assert target_wt is None
    assert err == "worktree_not_found"


def test_resolve_target_dotdot_returns_unsafe():
    target_wt, err = _resolve_target_worktree("git", "/tmp", "/foo/../escape")
    assert target_wt is None
    assert err == "path_unsafe"


def test_resolve_target_empty_path():
    target_wt, err = _resolve_target_worktree("git", "/tmp", "")
    assert target_wt is None
    assert err == "path_unsafe"


def test_resolve_target_relative_path():
    target_wt, err = _resolve_target_worktree("git", "/tmp", "relative/path")
    assert target_wt is None
    assert err == "path_unsafe"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_helpers_worktree.py -k "resolve_target" -v`
Expected: FAIL with `ImportError: cannot import name '_resolve_target_worktree'`

- [ ] **Step 3: Implement helper**

In `tools/_helpers.py`, add after `_validate_new_worktree_path`:

```python
def _resolve_target_worktree(
    git_bin: str,
    primary_dir: str,
    body_path: str | None,
) -> tuple[dict | None, str | None]:
    """REMOVE/LOCK/UNLOCK shared: format-check body path + look up in worktree list.

    Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §4.2

    Returns:
      (worktree_dict, None) — target found in list
      (None, "worktree_not_found") — format OK but not in list
      (None, "path_unsafe") — format check failed
    """
    # Step 1: basic format
    if not body_path or not isinstance(body_path, str):
        return None, "path_unsafe"
    if len(body_path) > 4096:
        return None, "path_unsafe"
    if "\\" in body_path:
        return None, "path_unsafe"
    if not os.path.isabs(body_path):
        return None, "path_unsafe"
    if ".." in body_path.split("/"):
        return None, "path_unsafe"

    # Step 2: enumerate worktrees via list --porcelain
    list_result = subprocess.run(
        [git_bin, "-C", primary_dir, "worktree", "list", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    if list_result.returncode != 0:
        return None, "worktree_not_found"
    try:
        worktrees = _parse_git_worktree_porcelain(list_result.stdout)
    except ValueError:
        return None, "worktree_not_found"

    # Step 3: case-insensitive path match (Windows normcase)
    target_norm = os.path.normcase(body_path)
    for wt in worktrees:
        if os.path.normcase(wt["path"]) == target_norm:
            return wt, None
    return None, "worktree_not_found"
```

Note: existing `_parse_git_worktree_porcelain` already imported at top of file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_helpers_worktree.py -k "resolve_target" -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tools/_helpers.py tests/test_helpers_worktree.py
git commit -m "feat(helpers): add _resolve_target_worktree for REMOVE/LOCK/UNLOCK

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 1.6: Extend `GET /spcode/git-worktrees` response with locked fields

**Files:**
- Modify: `tools/webapi/git_worktrees.py`
- Test: `tests/test_git_worktrees.py` (extend)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_git_worktrees.py`:

```python
def test_git_worktrees_response_includes_locked_field(tmp_path):
    """GET 端点返回数据应附加 locked 字段(pure additive)。"""
    # Setup: create primary + locked worktree
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "lock", str(linked)],
                   check=True, capture_output=True)

    # Invoke handler with mocked context
    # (use existing fixture pattern from test_git_worktrees.py for loading project)
    # ...

    assert "locked" in wt
    assert wt["locked"] is True
    assert wt["locked_reason"] is None  # no --reason


def test_git_worktrees_response_includes_locked_reason(tmp_path):
    """Locked with --reason → locked_reason 字段传递。"""
    # similar setup + git worktree lock --reason "..."
    # assert wt["locked_reason"] == "..."


def test_git_worktrees_parser_unlocked_worktree_unchanged():
    """Unlocked worktree → locked=False (与既有 v1 行为一致,无破坏)。"""
    # assert wt["locked"] is False
```

Adapt test setup to match existing `test_git_worktrees.py` fixture patterns. Use existing `plugin` and `umo` fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_worktrees.py -k "locked" -v`
Expected: FAIL with `KeyError: 'locked'`

- [ ] **Step 3: Update `git_worktrees.py` handler**

In `tools/webapi/git_worktrees.py`, the `_parse_git_worktree_porcelain` call already happens. After parsing, no extra work needed — the parser now returns `locked` / `locked_reason` automatically (from Task 1.1).

Verify the response envelope contains the new fields by reading the existing code path. If handler explicitly extracts specific fields, add `locked` / `locked_reason` to the returned dict. Otherwise no code change needed; just rely on parser change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git_worktrees.py -v`
Expected: All passed (including 3 new cases)

- [ ] **Step 5: Verify backward compat — existing tests still pass**

Run: `pytest tests/test_git_worktrees.py tests/test_git_diff_worktree.py -v`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
git add tools/webapi/git_worktrees.py tests/test_git_worktrees.py
git commit -m "feat(endpoint): GET /spcode/git-worktrees returns locked/locked_reason

Pure additive — locked: bool and locked_reason: str|None appended to each
worktree dict. Old clients ignore the new fields.

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 1.7: Update `test_webapi_end_to_end.py` route count (no change yet, just baseline)

**Files:**
- Test: `tests/test_webapi_end_to_end.py` (no actual change, just verify baseline)

- [ ] **Step 1: Verify current route count is 12**

Run: `pytest tests/test_webapi_end_to_end.py -k "twelve or routes_table" -v`
Expected: PASS (current 12-route assumption holds)

(No code change in this task — this is a checkpoint before Chunk 2 starts adding routes.)

- [ ] **Step 2: Commit (no commit needed)**

This task is just a verification step. No commit.

---

### Task 1.8: Run full test suite + ruff lint to verify PR-A baseline

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS (existing 50+ tests + 21 new tests in this chunk)

- [ ] **Step 2: Run ruff lint**

Run: `ruff check .`
Expected: 0 errors

- [ ] **Step 3: Tag PR-A boundary**

```bash
git log --oneline -10  # Verify commit chain
git tag pr-a-foundation  # Optional: tag for easy revert
```

PR-A complete. Ready for review and merge.

---

## Chunk 2: PR-B ADD endpoint

**Goal:** Implement `POST /spcode/git-worktree-add` endpoint with full 7-layer defense.

**PR Title:** `feat(endpoint): POST /spcode/git-worktree-add with git CLI flag parity`

**Depends on:** Chunk 1 merged (helpers + ReasonCode + parser extended)

### Task 2.1: Implement cross-field validator

**Files:**
- Create: `tools/webapi/git_worktree_add.py`
- Test: `tests/test_git_worktree_add.py` (new file)

- [ ] **Step 1: Write failing tests for cross-field validator**

Create `tests/test_git_worktree_add.py`:

```python
"""Tests for POST /spcode/git-worktree-add endpoint."""
from tools.webapi.git_worktree_add import _validate_add_cross_fields


# ── _validate_add_cross_fields tests ──

def test_cross_validate_default_ok():
    """默认 create=false / force=false / detach=false / base=None / branch="x" → ok。"""
    err = _validate_add_cross_fields(False, False, False, None, "feature")
    assert err is None


def test_cross_validate_create_true_ok():
    err = _validate_add_cross_fields(True, False, False, None, "new-feat")
    assert err is None


def test_cross_validate_create_with_base_ok():
    err = _validate_add_cross_fields(True, False, False, "main", "new-feat")
    assert err is None


def test_cross_validate_force_true_ok():
    err = _validate_add_cross_fields(False, True, False, None, "existing")
    assert err is None


def test_cross_validate_detach_with_branch_ok():
    """detach + branch 视为 commit ref,合法。"""
    err = _validate_add_cross_fields(False, False, True, None, "abc123")
    assert err is None


def test_cross_validate_create_and_force_both_true_rejected():
    err = _validate_add_cross_fields(True, True, False, None, "x")
    assert err is not None
    assert "create" in err.lower() and "force" in err.lower()


def test_cross_validate_detach_and_create_both_true_rejected():
    err = _validate_add_cross_fields(True, False, True, None, "x")
    assert err is not None


def test_cross_validate_detach_and_force_both_true_rejected():
    err = _validate_add_cross_fields(False, True, True, None, "x")
    assert err is not None


def test_cross_validate_base_without_create_rejected():
    err = _validate_add_cross_fields(False, False, False, "main", "x")
    assert err is not None


def test_cross_validate_missing_branch_when_not_detach_rejected():
    err = _validate_add_cross_fields(False, False, False, None, None)
    assert err is not None


def test_cross_validate_empty_branch_when_not_detach_rejected():
    err = _validate_add_cross_fields(False, False, False, None, "")
    assert err is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_worktree_add.py -k "cross_validate" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.webapi.git_worktree_add'`

- [ ] **Step 3: Implement cross-field validator (stub) + module skeleton**

Create `tools/webapi/git_worktree_add.py`:

```python
"""POST /spcode/git-worktree-add — create a new git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.1
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _validate_add_cross_fields(
    create: bool,
    force: bool,
    detach: bool,
    base: str | None,
    branch: str | None,
) -> str | None:
    """Validate cross-field combinations in ADD body. Returns error msg or None."""
    if create and force:
        return "create and force are mutually exclusive (use -b OR -B)"
    if detach and create:
        return "detach mode does not support create=true"
    if detach and force:
        return "detach mode does not support force=true"
    if base is not None and not create:
        return "base is only meaningful when create=true"
    if not detach and not branch:
        return "branch is required when detach=false"
    return None


async def handle(plugin: "SPCodeToolkit", *, umo: str | None = None,
                 worktree: str | None = None, body: dict | None = None) -> dict:
    """POST /spcode/git-worktree-add handler (stub — implemented in Task 2.4)."""
    raise NotImplementedError("to be implemented in Task 2.4")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_git_worktree_add.py -k "cross_validate" -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add tools/webapi/git_worktree_add.py tests/test_git_worktree_add.py
git commit -m "feat(endpoint): git_worktree_add - cross-field validator + module skeleton

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 2.2: Implement git CLI args builder for ADD

**Files:**
- Modify: `tools/webapi/git_worktree_add.py`
- Test: `tests/test_git_worktree_add.py` (extend)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_git_worktree_add.py`:

```python
from tools.webapi.git_worktree_add import _build_git_worktree_add_args


def test_build_args_basic_checkout():
    """add <path> <branch> (create=false, detach=False) → ['add', path, branch]。"""
    args = _build_git_worktree_add_args("/repo", "/target", "feat", False, False, False, None)
    assert args == ["add", "/target", "feat"]


def test_build_args_create_new_branch():
    """create=true → ['add', '-b', branch, path]。"""
    args = _build_git_worktree_add_args("/repo", "/target", "new-feat", True, False, False, None)
    assert args == ["add", "-b", "new-feat", "/target"]


def test_build_args_create_with_base():
    """create=true + base → ['add', '-b', branch, path, base]。"""
    args = _build_git_worktree_add_args("/repo", "/target", "new-feat", True, False, False, "main")
    assert args == ["add", "-b", "new-feat", "/target", "main"]


def test_build_args_force_reset_existing():
    """force=true → ['add', '-B', branch, path]。"""
    args = _build_git_worktree_add_args("/repo", "/target", "existing", False, True, False, None)
    assert args == ["add", "-B", "existing", "/target"]


def test_build_args_detached_at_head():
    """detach=true, branch=None → ['add', '--detach', path]。"""
    args = _build_git_worktree_add_args("/repo", "/target", None, False, False, True, None)
    assert args == ["add", "--detach", "/target"]


def test_build_args_detached_at_commit():
    """detach=true, branch=<sha> → ['add', '--detach', path, sha]。"""
    args = _build_git_worktree_add_args("/repo", "/target", "abc1234", False, False, True, None)
    assert args == ["add", "--detach", "/target", "abc1234"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_worktree_add.py -k "build_args" -v`
Expected: FAIL with `ImportError: cannot import name '_build_git_worktree_add_args'`

- [ ] **Step 3: Implement args builder**

In `tools/webapi/git_worktree_add.py`, add after `_validate_add_cross_fields`:

```python
def _build_git_worktree_add_args(
    directory: str,
    new_path: str,
    branch: str | None,
    create: bool,
    force: bool,
    detach: bool,
    base: str | None,
) -> list[str]:
    """Build `git worktree add` CLI args (excluding `git -C <dir>` prefix)."""
    args = ["add"]
    if create:
        args.append("-b")
        args.append(branch)
        args.append(new_path)
        if base is not None:
            args.append(base)
    elif force:
        args.append("-B")
        args.append(branch)
        args.append(new_path)
        if base is not None:
            args.append(base)
    elif detach:
        args.append("--detach")
        args.append(new_path)
        if branch is not None:
            args.append(branch)
    else:
        # basic: add <path> <branch>
        args.append(new_path)
        args.append(branch)
    return args
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_git_worktree_add.py -k "build_args" -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add tools/webapi/git_worktree_add.py tests/test_git_worktree_add.py
git commit -m "feat(endpoint): git_worktree_add - CLI args builder

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 2.3: Implement stderr → Reason mapper for ADD

**Files:**
- Modify: `tools/webapi/git_worktree_add.py`
- Test: `tests/test_git_worktree_add.py` (extend)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_git_worktree_add.py`:

```python
from tools.webapi.git_worktree_add import _map_add_stderr_to_reason


def test_stderr_branch_already_checked_out():
    stderr = "fatal: 'feature' is already checked out at '/path'"
    assert _map_add_stderr_to_reason(stderr) == "cannot_create_existing"


def test_stderr_branch_already_exists():
    stderr = "fatal: 'feature' already exists"
    assert _map_add_stderr_to_reason(stderr) == "cannot_create_existing"


def test_stderr_branch_not_valid_name():
    stderr = "fatal: 'fea..ture' is not a valid branch name"
    assert _map_add_stderr_to_reason(stderr) == "invalid_branch"


def test_stderr_missing_branch_name():
    stderr = "fatal: 'feature' is a missing branch name"
    assert _map_add_stderr_to_reason(stderr) == "cannot_checkout_missing"


def test_stderr_path_already_exists():
    stderr = "fatal: '/target' already exists"
    assert _map_add_stderr_to_reason(stderr) == "path_exists_nonempty"


def test_stderr_invalid_worktree_name():
    stderr = "fatal: '/foo:bar' cannot be used as a worktree name"
    assert _map_add_stderr_to_reason(stderr) == "invalid_param"


def test_stderr_invalid_start_point():
    stderr = "fatal: invalid start point: badref"
    assert _map_add_stderr_to_reason(stderr) == "invalid_param"


def test_stderr_unknown_returns_git_error():
    stderr = "fatal: unknown error XYZ"
    assert _map_add_stderr_to_reason(stderr) == "git_error"


def test_stderr_empty_returns_git_error():
    assert _map_add_stderr_to_reason("") == "git_error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_worktree_add.py -k "stderr" -v`
Expected: FAIL with `ImportError: cannot import name '_map_add_stderr_to_reason'`

- [ ] **Step 3: Implement mapper**

In `tools/webapi/git_worktree_add.py`, add after `_build_git_worktree_add_args`:

```python
def _map_add_stderr_to_reason(stderr: str) -> str:
    """Map git worktree add stderr to ReasonCode.

    Spec §5.2 ADD mapping table.
    """
    s = stderr.lower()
    if "is already checked out at" in s or "already exists" in s:
        return "cannot_create_existing"
    if "is not a valid branch name" in s:
        return "invalid_branch"
    if "is a missing branch name" in s:
        return "cannot_checkout_missing"
    if "already exists" in s:
        return "path_exists_nonempty"
    if "cannot be used as a worktree name" in s:
        return "invalid_param"
    if "invalid start point" in s:
        return "invalid_param"
    return "git_error"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_git_worktree_add.py -k "stderr" -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tools/webapi/git_worktree_add.py tests/test_git_worktree_add.py
git commit -m "feat(endpoint): git_worktree_add - stderr to ReasonCode mapper

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 2.4: Implement ADD handler with 7-layer defense

**Files:**
- Modify: `tools/webapi/git_worktree_add.py` (`handle` function)
- Test: `tests/test_git_worktree_add.py` (extend with end-to-end tests)

- [ ] **Step 1: Write failing tests for handler**

Add to `tests/test_git_worktree_add.py`:

```python
import pytest
from tools.webapi.git_worktree_add import handle as add_handle


@pytest.fixture
def loaded_primary_repo(tmp_path):
    """Create a primary repo with one commit, return (plugin_mock, umo, primary_dir)."""
    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    # plugin_mock and umo using existing fixtures pattern from test_git_worktrees.py
    # (mock SPCodeToolkit with _config, _git_binary, get_loaded_project)
    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    return plugin, umo, primary


def _make_plugin_mock_with_loaded_project(directory):
    """Create a mock plugin with the directory loaded as primary."""
    from unittest.mock import MagicMock
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    umo = "test:umo"
    plugin.get_loaded_project.return_value = {"directory": directory, "loaded_at": 0.0}
    return plugin, umo


@pytest.mark.asyncio
async def test_add_basic_checkout_existing_branch(loaded_primary_repo, tmp_path):
    plugin, umo, primary = loaded_primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat"}
    result = await add_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["status"] == "ok"
    data = result["data"]
    assert data["reason"] is None
    assert data["created"]["path"] == target
    assert data["created"]["branch"] == "feat"
    assert data["created"]["is_main"] is False
    assert len(data["worktrees"]) == 2  # primary + new


@pytest.mark.asyncio
async def test_add_relative_path_rejected(loaded_primary_repo):
    plugin, umo, primary = loaded_primary_repo
    body = {"path": "relative/path", "branch": "feat"}
    result = await add_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_add_missing_branch_field(loaded_primary_repo, tmp_path):
    plugin, umo, primary = loaded_primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target}  # branch 缺失, detach=False 隐含
    result = await add_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] == "invalid_body"


@pytest.mark.asyncio
async def test_add_no_project_loaded(tmp_path):
    from unittest.mock import MagicMock
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = None
    result = await add_handle(plugin, umo="nonexistent", worktree=None,
                              body={"path": "/x", "branch": "y"})
    assert result["data"]["reason"] == "no_project_loaded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_worktree_add.py -k "test_add_basic or test_add_relative or test_add_missing or test_add_no_project" -v`
Expected: FAIL with `NotImplementedError` (handle still stub from Task 2.1)

- [ ] **Step 3: Implement handler**

In `tools/webapi/git_worktree_add.py`, replace `handle` function:

```python
async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-worktree-add handler.

    Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.1

    7-layer defense:
      1. preflight (feature flag / umo / directory / git repo)
      2. _validate_new_worktree_path (4-step format defense)
      3. _validate_add_cross_fields (cross-field checks)
      4. _is_valid_ref_name (branch format)
      5. path_exists_nonempty (preventive)
      6. git worktree add subprocess
      7. post-create git-common-dir verification
    """
    import os
    import time as _time

    body = body or {}

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 防御 1: preflight ──
    from ._helpers import _git_endpoint_preflight, _make_envelope, _run_git_async
    from .._helpers import (
        _is_valid_ref_name,
        _resolve_git_common_dir,
        _validate_new_worktree_path,
        _list_worktrees_safe,  # see helper note below
    )

    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 防御 2: new path 4-step defense ──
    new_path, path_err = _validate_new_worktree_path(git_bin, body.get("path"))
    if path_err is not None:
        return _make_envelope(
            success=False, reason="path_unsafe",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"path validation failed: {body.get('path')!r}",
        )

    # ── 防御 3: cross-field validation ──
    create = bool(body.get("create", False))
    force = bool(body.get("force", False))
    detach = bool(body.get("detach", False))
    base = body.get("base")
    branch = body.get("branch")

    cross_err = _validate_add_cross_fields(create, force, detach, base, branch)
    if cross_err is not None:
        return _make_envelope(
            success=False, reason="invalid_body",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=cross_err,
        )

    # ── 防御 4: branch ref-format ──
    if branch is not None and not _is_valid_ref_name(branch):
        return _make_envelope(
            success=False, reason="invalid_branch",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"invalid branch name: {branch!r}",
        )
    if base is not None and not _is_valid_ref_name(base):
        return _make_envelope(
            success=False, reason="invalid_param",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"invalid base ref: {base!r}",
        )

    # ── 防御 5: path_exists_nonempty (preventive) ──
    if os.path.exists(new_path) and os.listdir(new_path):
        return _make_envelope(
            success=False, reason="path_exists_nonempty",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"target path already exists and is non-empty: {new_path}",
        )

    # ── 防御 6: git worktree add ──
    add_args = [git_bin, "-C", directory] + _build_git_worktree_add_args(
        directory, new_path, branch, create, force, detach, base,
    )
    add_result = await _run_git_async(add_args, encoding="utf-8", timeout=30.0)
    if not add_result["ok"]:
        reason = _map_add_stderr_to_reason(add_result.get("stderr", ""))
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=add_result.get("stderr", ""),
        )

    # ── 防御 7: post-create git-common-dir verification ──
    try:
        new_common = _resolve_git_common_dir(git_bin, new_path)
        primary_common = _resolve_git_common_dir(git_bin, directory)
    except Exception as exc:
        return _make_envelope(
            success=False, reason="worktree_not_in_repo",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"post-create git-common-dir resolve failed: {exc}",
        )
    if new_common != primary_common:
        return _make_envelope(
            success=False, reason="worktree_not_in_repo",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"post-create git-common-dir mismatch: {new_common!r} vs {primary_common!r}",
        )

    # ── 成功:返回 worktree list + created 单条 ──
    worktrees = await _list_worktrees_safe(git_bin, directory)
    created = next(
        (wt for wt in worktrees if os.path.normcase(wt["path"]) == os.path.normcase(new_path)),
        None,
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=new_path,
        created=created,
        worktrees=worktrees,
    )
```

Note: `_list_worktrees_safe` needs to be added to `tools/_helpers.py` if not present. Add this small helper in Task 2.4a (next task) before the rest of Task 2.4 tests can run.

- [ ] **Step 4: Add `_list_worktrees_safe` helper to `tools/_helpers.py`**

Create task 2.4a before continuing. This helper is used by both ADD (success path) and will be used by REMOVE/LOCK/UNLOCK.

In `tools/_helpers.py`, add:

```python
async def _list_worktrees_safe(git_bin: str, primary_dir: str) -> list[dict]:
    """List worktrees asynchronously with error tolerance.

    Used by write endpoints' success path to return the updated list.
    Returns empty list on git error (caller decides how to handle).
    """
    import asyncio
    try:
        proc = await asyncio.create_subprocess_exec(
            git_bin, "-C", primary_dir, "worktree", "list", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        if proc.returncode != 0:
            return []
        return _parse_git_worktree_porcelain(
            stdout.decode("utf-8", errors="replace").rstrip("\r\n")
        )
    except (asyncio.TimeoutError, Exception):
        return []
```

- [ ] **Step 5: Re-run tests to verify they pass**

Run: `pytest tests/test_git_worktree_add.py -k "test_add_basic or test_add_relative or test_add_missing or test_add_no_project" -v`
Expected: All PASS (assuming test_add_basic_checkout_existing_branch creates a branch first OR uses create=true to make a new branch — adjust test if needed)

Note: `test_add_basic_checkout_existing_branch` should be changed to use `create=true` since the test repo only has `main` branch initially:

```python
@pytest.mark.asyncio
async def test_add_basic_create_new_branch(loaded_primary_repo, tmp_path):
    plugin, umo, primary = loaded_primary_repo
    target = str(tmp_path / "feature")
    body = {"path": target, "branch": "feat", "create": True}
    result = await add_handle(plugin, umo=umo, worktree=None, body=body)
    assert result["data"]["reason"] is None
    assert result["data"]["created"]["is_main"] is False
```

- [ ] **Step 6: Commit**

```bash
git add tools/webapi/git_worktree_add.py tools/_helpers.py tests/test_git_worktree_add.py
git commit -m "feat(endpoint): git_worktree_add - 7-layer defense handler

Includes _list_worktrees_safe async helper.

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 2.5: Register ADD endpoint in webapi router

**Files:**
- Modify: `tools/webapi/__init__.py`
- Test: `tests/test_webapi_end_to_end.py`

- [ ] **Step 1: Write failing test for new route registration**

Add to `tests/test_webapi_end_to_end.py`:

```python
def test_git_worktree_add_route_registered():
    """git_worktree_add 应在 ROUTES 表中注册。"""
    from tools.webapi import ROUTES
    routes = [r[0] for r in ROUTES]
    assert "/spcode/git-worktree-add" in routes


def test_handlers_dict_has_add_entry():
    from tools.webapi import HANDLERS
    assert "handle_post_git_worktree_add" in HANDLERS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapi_end_to_end.py -k "git_worktree_add_route or handlers_dict_has_add" -v`
Expected: FAIL

- [ ] **Step 3: Update `tools/webapi/__init__.py`**

In `tools/webapi/__init__.py`:

1. Add to imports (alphabetical):
```python
from . import (
    ...,
    git_worktree_add,
    ...
)
```

2. Add to ROUTES:
```python
(
    "/spcode/git-worktree-add",  # v2.14.0
    ["POST"],
    git_worktree_add.handle,
    "创建 git worktree(git CLI 旗标平铺)",
),
```

3. Add to HANDLERS:
```python
"handle_post_git_worktree_add": git_worktree_add.handle,
```

4. Add to `__all__`:
```python
"git_worktree_add",
```

5. Update docstring at top: change route count from "11 / 12" to "13 / 13 / 16" or similar (note: 16 includes Chunk 3 + 4 routes)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webapi_end_to_end.py -k "git_worktree_add_route or handlers_dict_has_add" -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS (existing 50+ + new from PR-A + Chunk 2 ADD)

- [ ] **Step 6: Update route count assertion (12 → 13)**

In `tests/test_webapi_end_to_end.py`, update `test_routes_table_has_twelve_endpoints` (or similar) to expect 13 routes. Rename test method.

- [ ] **Step 7: Commit**

```bash
git add tools/webapi/__init__.py tests/test_webapi_end_to_end.py
git commit -m "feat(endpoint): register /spcode/git-worktree-add (route count 12→13)

Author: elecvoid243 @ 2026-06-26"
```

PR-B complete. Ready for review.

---

## Chunk 3: PR-C REMOVE endpoint

**Goal:** Implement `POST /spcode/git-worktree-remove` endpoint with conservative safety gates.

**PR Title:** `feat(endpoint): POST /spcode/git-worktree-remove with main-protection gates`

**Depends on:** Chunk 1 merged. Independent of Chunk 2 (can merge in any order after A).

### Task 3.1: Implement REMOVE handler core

**Files:**
- Create: `tools/webapi/git_worktree_remove.py`
- Test: `tests/test_git_worktree_remove.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/test_git_worktree_remove.py`:

```python
"""Tests for POST /spcode/git-worktree-remove endpoint."""
import subprocess
import pytest
from unittest.mock import MagicMock
from tools.webapi.git_worktree_remove import (
    handle as remove_handle,
    _map_remove_stderr_to_reason,
)


def _make_plugin_mock_with_loaded_project(directory):
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    umo = "test:umo"
    plugin.get_loaded_project.return_value = {"directory": directory, "loaded_at": 0.0}
    return plugin, umo


# ── _map_remove_stderr_to_reason ──

def test_stderr_not_working_tree():
    assert _map_remove_stderr_to_reason(
        "fatal: '/target' is not a working tree"
    ) == "worktree_not_found"


def test_stderr_locked():
    assert _map_remove_stderr_to_reason(
        "fatal: '/target' is locked"
    ) == "worktree_locked"


def test_stderr_dirty():
    assert _map_remove_stderr_to_reason(
        "fatal: '/target' contains modified or untracked files"
    ) == "worktree_dirty"


def test_stderr_unknown():
    assert _map_remove_stderr_to_reason("fatal: foo bar baz") == "git_error"


# ── handle() ──

@pytest.mark.asyncio
async def test_remove_basic_linked_worktree(tmp_path):
    """删除 linked worktree → success。"""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(plugin, umo=umo, worktree=None,
                                 body={"path": str(linked)})
    assert result["data"]["reason"] is None
    assert result["data"]["removed_path"] == str(linked)
    assert len(result["data"]["worktrees"]) == 1  # only main left


@pytest.mark.asyncio
async def test_remove_main_worktree_rejected(tmp_path):
    """删除 main worktree → cannot_remove_main(即使 force=true)。"""
    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    # try force=true — should still be rejected
    result = await remove_handle(plugin, umo=umo, worktree=None,
                                 body={"path": str(primary), "force": True})
    assert result["data"]["reason"] == "cannot_remove_main"


@pytest.mark.asyncio
async def test_remove_locked_worktree_rejected(tmp_path):
    """删除已 locked worktree → worktree_locked。"""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "lock", str(linked)],
                   check=True, capture_output=True)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(plugin, umo=umo, worktree=None,
                                 body={"path": str(linked), "force": True})
    assert result["data"]["reason"] == "worktree_locked"


@pytest.mark.asyncio
async def test_remove_dirty_without_force(tmp_path):
    """force=false 且 dirty → worktree_dirty。"""
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)
    # Make linked dirty
    (linked / "uncommitted.txt").write_text("uncommitted")

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(plugin, umo=umo, worktree=None,
                                 body={"path": str(linked)})
    assert result["data"]["reason"] == "worktree_dirty"


@pytest.mark.asyncio
async def test_remove_force_bypasses_dirty(tmp_path):
    """force=true 跳过 dirty 检查 → success。"""
    # same setup as dirty test
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "worktree", "add", str(linked), "-b", "feat"],
                   check=True, capture_output=True)
    (linked / "uncommitted.txt").write_text("uncommitted")

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(plugin, umo=umo, worktree=None,
                                 body={"path": str(linked), "force": True})
    assert result["data"]["reason"] is None
    assert result["data"]["removed_path"] == str(linked)


@pytest.mark.asyncio
async def test_remove_unknown_path(tmp_path):
    """path 不在 worktree list → worktree_not_found。"""
    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)

    plugin, umo = _make_plugin_mock_with_loaded_project(str(primary))
    result = await remove_handle(plugin, umo=umo, worktree=None,
                                 body={"path": str(primary / "nonexistent")})
    assert result["data"]["reason"] == "worktree_not_found"


@pytest.mark.asyncio
async def test_remove_dotdot_path_rejected():
    plugin, umo = MagicMock(), "test:umo"
    result = await remove_handle(plugin, umo=umo, worktree=None,
                                 body={"path": "/foo/../escape"})
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_remove_missing_path_field():
    plugin, umo = MagicMock(), "test:umo"
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": "/tmp", "loaded_at": 0.0}
    result = await remove_handle(plugin, umo=umo, worktree=None, body={})
    assert result["data"]["reason"] == "path_unsafe"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_worktree_remove.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement module + stderr mapper (stub handler)**

Create `tools/webapi/git_worktree_remove.py`:

```python
"""POST /spcode/git-worktree-remove — delete a git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.2
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _map_remove_stderr_to_reason(stderr: str) -> str:
    """Map git worktree remove stderr to ReasonCode.

    Spec §5.2 REMOVE mapping table.
    """
    s = stderr.lower()
    if "is not a working tree" in s:
        return "worktree_not_found"
    if "is locked" in s:
        return "worktree_locked"
    if "contains modified or untracked files" in s:
        return "worktree_dirty"
    return "git_error"


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-worktree-remove handler.

    Spec §3.2 / §4.4. Conservative safety:
      - main worktree (is_main=true) → cannot_remove_main (always)
      - locked → worktree_locked (force=true 不绕过)
      - dirty → worktree_dirty unless force=true
    """
    import time as _time
    body = body or {}

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    from ._helpers import _git_endpoint_preflight, _make_envelope, _run_git_async
    from .._helpers import _resolve_target_worktree, _list_worktrees_safe

    # ── 防御 1: preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 防御 2: path + list lookup ──
    target_wt, path_err = _resolve_target_worktree(
        git_bin, directory, body.get("path"),
    )
    if path_err == "path_unsafe":
        return _make_envelope(
            success=False, reason="path_unsafe",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"path validation failed: {body.get('path')!r}",
        )
    if path_err == "worktree_not_found" or target_wt is None:
        return _make_envelope(
            success=False, reason="worktree_not_found",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"path not in worktree list: {body.get('path')!r}",
        )

    # ── 闸 1: main worktree (硬禁止) ──
    if target_wt.get("is_main"):
        return _make_envelope(
            success=False, reason="cannot_remove_main",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"main worktree ({target_wt['path']}) cannot be removed",
        )

    # ── 闸 2: locked ──
    if target_wt.get("locked"):
        locked_reason = target_wt.get("locked_reason") or "<no reason>"
        return _make_envelope(
            success=False, reason="worktree_locked",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"worktree is locked: {locked_reason}",
        )

    force = bool(body.get("force", False))

    # ── 闸 3: dirty (unless force) ──
    if not force:
        dirty_result = await _run_git_async(
            [git_bin, "-C", target_wt["path"], "status", "--porcelain"],
            encoding="utf-8", timeout=10.0,
        )
        if dirty_result.get("ok") and dirty_result.get("stdout", "").strip():
            return _make_envelope(
                success=False, reason="worktree_dirty",
                elapsed_ms=_elapsed(), loaded=False,
                directory=directory, umo=effective_umo, worktree=directory,
                stderr="worktree has uncommitted changes; pass force=true to override",
            )

    # ── git worktree remove [--force] <path> ──
    args = [git_bin, "-C", directory, "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(target_wt["path"])
    result = await _run_git_async(args, encoding="utf-8", timeout=30.0)
    if not result["ok"]:
        reason = _map_remove_stderr_to_reason(result.get("stderr", ""))
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", ""),
        )

    # ── 成功:返回更新后的 worktree list ──
    worktrees = await _list_worktrees_safe(git_bin, directory)
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=target_wt["path"],
        removed_path=target_wt["path"],
        worktrees=worktrees,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_git_worktree_remove.py -v`
Expected: All PASS (16 cases including 8 stderr mapper + 8 handler)

- [ ] **Step 5: Commit**

```bash
git add tools/webapi/git_worktree_remove.py tests/test_git_worktree_remove.py
git commit -m "feat(endpoint): git_worktree_remove - conservative safety gates

- 3 business gates: main / locked / dirty
- force=true bypasses dirty but NEVER main
- Returns updated worktree list on success

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 3.2: Register REMOVE endpoint in webapi router

**Files:**
- Modify: `tools/webapi/__init__.py`
- Test: `tests/test_webapi_end_to_end.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_webapi_end_to_end.py`:

```python
def test_git_worktree_remove_route_registered():
    from tools.webapi import ROUTES
    routes = [r[0] for r in ROUTES]
    assert "/spcode/git-worktree-remove" in routes


def test_handlers_dict_has_remove_entry():
    from tools.webapi import HANDLERS
    assert "handle_post_git_worktree_remove" in HANDLERS
```

- [ ] **Step 2: Update `tools/webapi/__init__.py`**

Add to imports, ROUTES, HANDLERS, `__all__`:

```python
from . import (
    ...,
    git_worktree_remove,
    ...
)

ROUTES += [
    (
        "/spcode/git-worktree-remove",  # v2.14.0
        ["POST"],
        git_worktree_remove.handle,
        "删除 git worktree (禁用 main)",
    ),
]

HANDLERS["handle_post_git_worktree_remove"] = git_worktree_remove.handle

__all__ = [
    ...,
    "git_worktree_remove",
]
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_webapi_end_to_end.py tests/test_git_worktree_remove.py -v`
Expected: All PASS

- [ ] **Step 4: Update route count assertion (13 → 14)**

- [ ] **Step 5: Commit**

```bash
git add tools/webapi/__init__.py tests/test_webapi_end_to_end.py
git commit -m "feat(endpoint): register /spcode/git-worktree-remove (route count 13→14)

Author: elecvoid243 @ 2026-06-26"
```

PR-C complete. Ready for review.

---

## Chunk 4: PR-D LOCK + UNLOCK + docs

**Goal:** Implement LOCK + UNLOCK endpoints, E2E smoke tests, public API docs, README/AGENTS updates.

**PR Title:** `feat(endpoint): POST /spcode/git-worktree-{lock,unlock} + public docs`

**Depends on:** Chunk 1 merged. Independent of Chunks 2 and 3 (can merge in any order after A).

### Task 4.1: Implement LOCK handler

**Files:**
- Create: `tools/webapi/git_worktree_lock.py`
- Test: `tests/test_git_worktree_lock.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/test_git_worktree_lock.py`:

```python
"""Tests for POST /spcode/git-worktree-lock + -unlock endpoints."""
import subprocess
import pytest
from unittest.mock import MagicMock
from tools.webapi.git_worktree_lock import handle as lock_handle
from tools.webapi.git_worktree_unlock import handle as unlock_handle


def _make_plugin_mock(directory):
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": directory, "loaded_at": 0.0}
    return plugin


def _make_test_repo(tmp_path, n_linked=1):
    """Create primary + linked worktrees; return (primary, [linked_paths])."""
    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(primary)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (primary / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(primary), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(primary), "commit", "-m", "init"],
                   check=True, capture_output=True)
    linked_paths = []
    for i in range(n_linked):
        linked = tmp_path / f"linked{i}"
        subprocess.run(
            ["git", "-C", str(primary), "worktree", "add", str(linked), "-b", f"feat{i}"],
            check=True, capture_output=True,
        )
        linked_paths.append(str(linked))
    return primary, linked_paths


# ── LOCK tests ──

@pytest.mark.asyncio
async def test_lock_basic(tmp_path):
    primary, links = _make_test_repo(tmp_path, n_linked=1)
    plugin = _make_plugin_mock(str(primary))
    result = await lock_handle(plugin, umo="t:u", worktree=None,
                                body={"path": links[0]})
    assert result["data"]["reason"] is None
    assert result["data"]["locked"] is True
    assert result["data"]["lock_reason"] is None
    # verify on disk
    out = subprocess.run(
        ["git", "-C", str(primary), "worktree", "list", "--porcelain"],
        capture_output=True, text=True,
    )
    assert "locked" in out.stdout


@pytest.mark.asyncio
async def test_lock_with_reason(tmp_path):
    primary, links = _make_test_repo(tmp_path, n_linked=1)
    plugin = _make_plugin_mock(str(primary))
    result = await lock_handle(plugin, umo="t:u", worktree=None,
                                body={"path": links[0], "reason": "test reason"})
    assert result["data"]["reason"] is None
    assert result["data"]["lock_reason"] == "test reason"


@pytest.mark.asyncio
async def test_lock_already_locked_rejected(tmp_path):
    primary, links = _make_test_repo(tmp_path, n_linked=1)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "lock", links[0]],
        check=True, capture_output=True,
    )
    plugin = _make_plugin_mock(str(primary))
    result = await lock_handle(plugin, umo="t:u", worktree=None,
                                body={"path": links[0]})
    assert result["data"]["reason"] == "already_locked"


@pytest.mark.asyncio
async def test_lock_unknown_path(tmp_path):
    primary, _ = _make_test_repo(tmp_path, n_linked=0)
    plugin = _make_plugin_mock(str(primary))
    result = await lock_handle(plugin, umo="t:u", worktree=None,
                                body={"path": str(primary / "nonexistent")})
    assert result["data"]["reason"] == "worktree_not_found"


@pytest.mark.asyncio
async def test_lock_path_unsafe():
    plugin = _make_plugin_mock("/tmp")
    result = await lock_handle(plugin, umo="t:u", worktree=None,
                                body={"path": "/foo/../escape"})
    assert result["data"]["reason"] == "path_unsafe"


@pytest.mark.asyncio
async def test_lock_main_worktree_allowed(tmp_path):
    """Lock main worktree → allowed (no business restriction)."""
    primary, _ = _make_test_repo(tmp_path, n_linked=0)
    plugin = _make_plugin_mock(str(primary))
    result = await lock_handle(plugin, umo="t:u", worktree=None,
                                body={"path": str(primary)})
    assert result["data"]["reason"] is None
    assert result["data"]["locked"] is True


# ── UNLOCK tests ──

@pytest.mark.asyncio
async def test_unlock_basic(tmp_path):
    primary, links = _make_test_repo(tmp_path, n_linked=1)
    subprocess.run(
        ["git", "-C", str(primary), "worktree", "lock", links[0]],
        check=True, capture_output=True,
    )
    plugin = _make_plugin_mock(str(primary))
    result = await unlock_handle(plugin, umo="t:u", worktree=None,
                                  body={"path": links[0]})
    assert result["data"]["reason"] is None
    assert result["data"]["locked"] is False


@pytest.mark.asyncio
async def test_unlock_not_locked_rejected(tmp_path):
    primary, links = _make_test_repo(tmp_path, n_linked=1)
    plugin = _make_plugin_mock(str(primary))
    result = await unlock_handle(plugin, umo="t:u", worktree=None,
                                  body={"path": links[0]})
    assert result["data"]["reason"] == "not_locked"


@pytest.mark.asyncio
async def test_unlock_unknown_path(tmp_path):
    primary, _ = _make_test_repo(tmp_path, n_linked=0)
    plugin = _make_plugin_mock(str(primary))
    result = await unlock_handle(plugin, umo="t:u", worktree=None,
                                  body={"path": str(primary / "nonexistent")})
    assert result["data"]["reason"] == "worktree_not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_git_worktree_lock.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement LOCK module**

Create `tools/webapi/git_worktree_lock.py`:

```python
"""POST /spcode/git-worktree-lock — lock a git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.3
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
    """POST /spcode/git-worktree-lock handler."""
    import time as _time
    body = body or {}

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    from ._helpers import _git_endpoint_preflight, _make_envelope, _run_git_async
    from .._helpers import _resolve_target_worktree, _list_worktrees_safe

    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    target_wt, path_err = _resolve_target_worktree(
        git_bin, directory, body.get("path"),
    )
    if path_err == "path_unsafe":
        return _make_envelope(
            success=False, reason="path_unsafe",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"path validation failed: {body.get('path')!r}",
        )
    if path_err is not None or target_wt is None:
        return _make_envelope(
            success=False, reason="worktree_not_found",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"path not in worktree list: {body.get('path')!r}",
        )

    if target_wt.get("locked"):
        return _make_envelope(
            success=False, reason="already_locked",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"worktree already locked: {target_wt.get('locked_reason') or '<no reason>'}",
        )

    reason_text = body.get("reason")
    args = [git_bin, "-C", directory, "worktree", "lock"]
    if reason_text:
        args.extend(["--reason", reason_text])
    args.append(target_wt["path"])

    result = await _run_git_async(args, encoding="utf-8", timeout=10.0)
    if not result["ok"]:
        stderr_lower = result.get("stderr", "").lower()
        if "is not a working tree" in stderr_lower:
            reason = "worktree_not_found"
        elif "already locked" in stderr_lower:
            reason = "already_locked"
        else:
            reason = "git_error"
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", ""),
        )

    worktrees = await _list_worktrees_safe(git_bin, directory)
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=target_wt["path"],
        locked=True,
        lock_reason=reason_text,
        worktrees=worktrees,
    )
```

- [ ] **Step 4: Run LOCK tests to verify they pass**

Run: `pytest tests/test_git_worktree_lock.py -k "lock" -v`
Expected: 6 passed

(Note: UNLOCK tests will fail until Task 4.2 — that's fine.)

- [ ] **Step 5: Commit (LOCK only)**

```bash
git add tools/webapi/git_worktree_lock.py tests/test_git_worktree_lock.py
git commit -m "feat(endpoint): git_worktree_lock - lock handler with --reason support

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 4.2: Implement UNLOCK handler

**Files:**
- Create: `tools/webapi/git_worktree_unlock.py`

- [ ] **Step 1: Run UNLOCK tests (still failing)**

Run: `pytest tests/test_git_worktree_lock.py -k "unlock" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.webapi.git_worktree_unlock'`

- [ ] **Step 2: Implement UNLOCK module**

Create `tools/webapi/git_worktree_unlock.py`:

```python
"""POST /spcode/git-worktree-unlock — unlock a git worktree.

Spec: docs/superpowers/specs/2026-06-26-git-worktree-management-design.md §3.4
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
    """POST /spcode/git-worktree-unlock handler."""
    import time as _time
    body = body or {}

    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    from ._helpers import _git_endpoint_preflight, _make_envelope, _run_git_async
    from .._helpers import _resolve_target_worktree, _list_worktrees_safe

    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    target_wt, path_err = _resolve_target_worktree(
        git_bin, directory, body.get("path"),
    )
    if path_err == "path_unsafe":
        return _make_envelope(
            success=False, reason="path_unsafe",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"path validation failed: {body.get('path')!r}",
        )
    if path_err is not None or target_wt is None:
        return _make_envelope(
            success=False, reason="worktree_not_found",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"path not in worktree list: {body.get('path')!r}",
        )

    if not target_wt.get("locked"):
        return _make_envelope(
            success=False, reason="not_locked",
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr="worktree is not currently locked",
        )

    args = [git_bin, "-C", directory, "worktree", "unlock", target_wt["path"]]
    result = await _run_git_async(args, encoding="utf-8", timeout=10.0)
    if not result["ok"]:
        stderr_lower = result.get("stderr", "").lower()
        if "is not a working tree" in stderr_lower:
            reason = "worktree_not_found"
        elif "not locked" in stderr_lower:
            reason = "not_locked"
        else:
            reason = "git_error"
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", ""),
        )

    worktrees = await _list_worktrees_safe(git_bin, directory)
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        loaded=True,
        directory=directory,
        umo=effective_umo,
        worktree=target_wt["path"],
        locked=False,
        worktrees=worktrees,
    )
```

- [ ] **Step 3: Run all LOCK + UNLOCK tests**

Run: `pytest tests/test_git_worktree_lock.py -v`
Expected: All 9 PASS

- [ ] **Step 4: Commit**

```bash
git add tools/webapi/git_worktree_unlock.py
git commit -m "feat(endpoint): git_worktree_unlock - unlock handler

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 4.3: Register LOCK + UNLOCK endpoints in router

**Files:**
- Modify: `tools/webapi/__init__.py`
- Test: `tests/test_webapi_end_to_end.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_webapi_end_to_end.py`:

```python
def test_git_worktree_lock_route_registered():
    from tools.webapi import ROUTES
    assert "/spcode/git-worktree-lock" in [r[0] for r in ROUTES]


def test_git_worktree_unlock_route_registered():
    from tools.webapi import ROUTES
    assert "/spcode/git-worktree-unlock" in [r[0] for r in ROUTES]


def test_routes_table_has_sixteen_endpoints():
    from tools.webapi import ROUTES
    assert len(ROUTES) == 16
```

- [ ] **Step 2: Update `tools/webapi/__init__.py`**

Add imports + ROUTES + HANDLERS + `__all__` for both endpoints.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_webapi_end_to_end.py -v`
Expected: All PASS (route count now 16)

- [ ] **Step 4: Commit**

```bash
git add tools/webapi/__init__.py tests/test_webapi_end_to_end.py
git commit -m "feat(endpoint): register /spcode/git-worktree-{lock,unlock} (route count 14→16)

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 4.4: Write E2E smoke tests on real git repo

**Files:**
- Create: `tests/test_worktree_mgmt_e2e.py`

- [ ] **Step 1: Write tests**

```python
"""End-to-end smoke tests for full worktree lifecycle on a real git repo."""
import asyncio
import subprocess
import pytest
from unittest.mock import MagicMock

from tools.webapi.git_worktree_add import handle as add_handle
from tools.webapi.git_worktree_lock import handle as lock_handle
from tools.webapi.git_worktree_unlock import handle as unlock_handle
from tools.webapi.git_worktree_remove import handle as remove_handle


def _make_plugin(directory):
    plugin = MagicMock()
    plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
    plugin._git_binary.return_value = "git"
    plugin.get_loaded_project.return_value = {"directory": directory, "loaded_at": 0.0}
    return plugin


def _init_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, capture_output=True)
    (path / "init.txt").write_text("init")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)


@pytest.mark.asyncio
async def test_e2e_add_remove_cycle(tmp_path):
    primary = tmp_path / "primary"
    _init_repo(primary)
    target = tmp_path / "feat"
    plugin = _make_plugin(str(primary))

    # ADD
    result = await add_handle(plugin, umo="t:u", worktree=None,
                              body={"path": str(target), "branch": "feat", "create": True})
    assert result["data"]["reason"] is None
    assert target.exists()

    # REMOVE
    result = await remove_handle(plugin, umo="t:u", worktree=None,
                                 body={"path": str(target)})
    assert result["data"]["reason"] is None
    assert not target.exists()


@pytest.mark.asyncio
async def test_e2e_add_lock_unlock_remove(tmp_path):
    primary = tmp_path / "primary"
    _init_repo(primary)
    target = tmp_path / "feat"
    plugin = _make_plugin(str(primary))

    # ADD
    await add_handle(plugin, umo="t:u", worktree=None,
                     body={"path": str(target), "branch": "feat", "create": True})
    # LOCK
    result = await lock_handle(plugin, umo="t:u", worktree=None,
                                body={"path": str(target), "reason": "test lock"})
    assert result["data"]["reason"] is None
    assert result["data"]["locked"] is True
    # REMOVE while locked → rejected
    result = await remove_handle(plugin, umo="t:u", worktree=None,
                                 body={"path": str(target)})
    assert result["data"]["reason"] == "worktree_locked"
    # UNLOCK
    result = await unlock_handle(plugin, umo="t:u", worktree=None,
                                  body={"path": str(target)})
    assert result["data"]["reason"] is None
    assert result["data"]["locked"] is False
    # REMOVE now succeeds
    result = await remove_handle(plugin, umo="t:u", worktree=None,
                                 body={"path": str(target)})
    assert result["data"]["reason"] is None


@pytest.mark.asyncio
async def test_e2e_remove_main_always_fails(tmp_path):
    primary = tmp_path / "primary"
    _init_repo(primary)
    plugin = _make_plugin(str(primary))

    # try with force=true — must still fail
    result = await remove_handle(plugin, umo="t:u", worktree=None,
                                 body={"path": str(primary), "force": True})
    assert result["data"]["reason"] == "cannot_remove_main"


@pytest.mark.asyncio
async def test_e2e_add_creates_branch_and_checkout(tmp_path):
    """ADD -b <new> → new branch exists with HEAD pointing to repo HEAD."""
    primary = tmp_path / "primary"
    _init_repo(primary)
    target = tmp_path / "feat"
    plugin = _make_plugin(str(primary))

    await add_handle(plugin, umo="t:u", worktree=None,
                     body={"path": str(target), "branch": "newfeat", "create": True})

    # Verify branch created
    result = subprocess.run(
        ["git", "-C", str(primary), "branch", "--list", "newfeat"],
        capture_output=True, text=True,
    )
    assert "newfeat" in result.stdout
    # Verify worktree on branch
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "newfeat"


@pytest.mark.asyncio
async def test_e2e_concurrent_adds_different_paths(tmp_path):
    """并发 ADD 2 个独立路径,均成功。"""
    primary = tmp_path / "primary"
    _init_repo(primary)
    target1 = tmp_path / "feat1"
    target2 = tmp_path / "feat2"
    plugin = _make_plugin(str(primary))

    result1, result2 = await asyncio.gather(
        add_handle(plugin, umo="t:u", worktree=None,
                   body={"path": str(target1), "branch": "feat1", "create": True}),
        add_handle(plugin, umo="t:u", worktree=None,
                   body={"path": str(target2), "branch": "feat2", "create": True}),
    )
    assert result1["data"]["reason"] is None
    assert result2["data"]["reason"] is None
    assert target1.exists()
    assert target2.exists()
```

- [ ] **Step 2: Run E2E tests**

Run: `pytest tests/test_worktree_mgmt_e2e.py -v`
Expected: All 5 PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_worktree_mgmt_e2e.py
git commit -m "test(e2e): worktree mgmt full lifecycle on real git repo

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 4.5: Write public API docs

**Files:**
- Create: `docs/webapi-git-worktree-mgmt-api.md`

- [ ] **Step 1: Write docs**

Create file with full API contract for all 4 endpoints. Reference: spec §3.

Key sections:
- Overview (4 endpoints)
- Per-endpoint: request/response/errors
- i18n key namespace
- Versioning (v2.14.0)
- Cross-references to spec

- [ ] **Step 2: Commit**

```bash
git add docs/webapi-git-worktree-mgmt-api.md
git commit -m "docs(api): public API doc for git-worktree-mgmt 4 endpoints

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 4.6: Update README.md and AGENTS.md

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update `README.md`**

Add 4 rows to endpoint table:

```markdown
| [`/spcode/git-worktree-add`](docs/webapi-git-worktree-mgmt-api.md#post-spcodegit-worktree-add) | POST | 创建 git worktree | Dashboard 新建 worktree 对话框 |
| [`/spcode/git-worktree-remove`](docs/webapi-git-worktree-mgmt-api.md#post-spcodegit-worktree-remove) | POST | 删除 git worktree(禁用 main) | Dashboard 删除 worktree |
| [`/spcode/git-worktree-lock`](docs/webapi-git-worktree-mgmt-api.md#post-spcodegit-worktree-lock) | POST | 锁定 git worktree | Dashboard 锁定按钮 |
| [`/spcode/git-worktree-unlock`](docs/webapi-git-worktree-mgmt-api.md#post-spcodegit-worktree-unlock) | POST | 解锁 git worktree | Dashboard 解锁按钮 |
```

- [ ] **Step 2: Update `AGENTS.md`**

Add same 4 rows to the endpoint table in §"Web API 端点". Update route count (12 → 16).

- [ ] **Step 3: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: update README and AGENTS with 4 new worktree-mgmt endpoints

Author: elecvoid243 @ 2026-06-26"
```

---

### Task 4.7: Run full test suite + ruff

- [ ] **Step 1: Full test suite**

Run: `pytest tests/ -v`
Expected: All PASS (~50 existing + ~86 new = ~136 total)

- [ ] **Step 2: Ruff lint**

Run: `ruff check .`
Expected: 0 errors

- [ ] **Step 3: Final commit (if any small fixes needed)**

PR-D complete. All 4 PRs ready.

---

## Execution Handoff

After all 4 chunks complete and pass tests:

- Total ~87 new test cases (per spec §6.1)
- Total ~16 endpoints (was 12)
- 4 PRs ready for review/merge independently

Ready to execute? Choose:
- **Subagent-driven**: fresh subagent per task with two-stage review
- **Executing-plans**: batch execution with checkpoints
