# GitDiffSidebar Worktree Switcher — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add worktree-switching capability to the GitDiffSidebar so users can view per-worktree diffs, by extending the spcode backend with a new `git-worktrees` endpoint + `?worktree` query param, and the dashboard frontend with a tab-based switcher.

**Architecture:** Two-repo additive change. Backend keeps the existing `git-diff` endpoint (adds optional `?worktree` param with 6-step validation) and adds a new `git-worktrees` endpoint. Frontend adds a `useSpcodeWorktrees` composable, extends `useSpcodeGitDiff` to accept an active worktree ref, and renders horizontal tabs in `GitDiffSidebar` (multi-row wrap on overflow). Worktree list is fetched once on sidebar open + on manual refresh; diff polls only the active worktree.

**Tech Stack:**
- Backend: Python 3.10+, FastAPI-style web handlers (AstrBot plugin), `subprocess` for git CLI, `pytest` + `pytest-asyncio`
- Frontend: Vue 3 Composition API, TypeScript, Vuetify, Vite/Vitest

**Spec:** `docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md` (read this BEFORE starting any chunk)

---

## File Structure

### spcode plugin repo (`F:\github\astrbot_plugin_spcode_toolkit`)

| Status | Path | Responsibility |
|--------|------|----------------|
| **Create** | `tools/_helpers.py` additions | `_resolve_git_common_dir()`, `_parse_git_worktree_porcelain()` |
| **Create** | `tests/test_git_worktrees.py` | New endpoint + parser tests |
| **Modify** | `main.py` (line ~1080-1110) | Register `/spcode/git-worktrees` endpoint in `initialize()` |
| **Modify** | `main.py` (line ~1665-1820) | New `handle_get_git_worktrees()` handler |
| **Modify** | `main.py` (line ~1693-1790) | Extend `handle_get_git_diff()` with `?worktree` + 6-step validation |
| **Modify** | `tests/test_git_diff.py` | Add `TestGitDiffWithWorktree` test class |
| **Modify** | `README.md` | API table: add `/spcode/git-worktrees`, `?worktree` param on `git-diff` |

### dashboard repo (`F:\github\Astrbot\dashboard`)

| Status | Path | Responsibility |
|--------|------|----------------|
| **Create** | `src/composables/useSpcodeWorktrees.ts` | Worktree list fetching + state |
| **Create** | `tests/composables/useSpcodeWorktrees.test.ts` | Composable tests (new — no existing test file for this) |
| **Modify** | `src/composables/parseSpcodeGitDiff.ts` | Add `SpcodeWorktree`, `SpcodeGitWorktreesRawResponse` types; add `worktree` field to `SpcodeGitDiffRawResponse` |
| **Modify** | `src/composables/useSpcodeGitDiff.ts` | Accept `worktreeRef: MaybeRef<string\|null>`; add `watch` on it |
| **Modify** | `src/components/chat/GitDiffSidebar.vue` | Add `selectedWorktree` ref, `useSpcodeWorktrees`, tabs UI, sequential fetch flow, multi-row wrap CSS |
| **Modify** | `src/components/chat/message_list_comps/GitDiffBodyContent.vue` | Extend `REASON_I18N_KEYS` with 4 worktree reasons |
| **Modify** | `src/i18n/locales/zh-CN/features/chat.json` | Add `worktreeTabs.ariaLabel` + 4 reason keys |
| **Modify** | `src/i18n/locales/en-US/features/chat.json` | Same |
| **Modify** | `src/i18n/locales/ru-RU/features/chat.json` | Same |
| **Modify** | `src/i18n/locales/ja-JP/features/chat.json` | Same |
| **Create** | `tests/composables/useSpcodeGitDiff.test.ts` | NEW FILE — composable tests including worktree ref cases |
| **Create** | `tests/components/GitDiffSidebar.test.ts` | NEW FILE — sidebar component tests including tabs cases |
| **Modify** | `AGENTS.md` | New "## Worktree Switcher" section |

---

## Implementation Order

1. **Chunks 1-3**: Backend (spcode) — fully testable, deployable independently
2. **Chunks 4-6**: Frontend (dashboard) — depends on backend API contract (spec is sufficient)
3. **Chunk 7**: Docs + final cross-repo integration verification

Each chunk is self-contained: write failing tests → implement → make green → commit.

---

# Chunk 1: Backend helper + parser

**Goal:** Add two pure-Python helpers used by the new endpoint and the extended `?worktree` validator. Both are independently testable with no git / filesystem dependency for the parser; the resolver needs `git` binary.

## Task 1.1: `_resolve_git_common_dir()` helper

**Files:**
- Modify: `tools/_helpers.py` (append at end of module)
- Create: `tests/test_helpers_git.py`

- [ ] **Step 1: Write failing test for `_resolve_git_common_dir`**

```python
# tests/test_helpers_git.py
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools._helpers import _resolve_git_common_dir


def _make_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "x@x"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "x"], check=True)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


@pytest.fixture
def two_repos():
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        a = _make_repo(parent, "repoA")
        b = _make_repo(parent, "repoB")
        yield a, b


def test_resolve_returns_absolute_path(two_repos):
    a, _ = two_repos
    result = _resolve_git_common_dir("git", str(a))
    assert os.path.isabs(result)


def test_resolve_different_repos_differ(two_repos):
    """CRITICAL: prevents cross-repo bypass (spec §2.3)."""
    a, b = two_repos
    assert _resolve_git_common_dir("git", str(a)) != _resolve_git_common_dir("git", str(b))


def test_resolve_same_repo_two_worktrees_match():
    """Two worktrees of the same repo share a common dir."""
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        main = _make_repo(parent, "main")
        wt = parent / "wt"
        subprocess.run(
            ["git", "-C", str(main), "worktree", "add", str(wt), "-b", "wt2"],
            check=True,
        )
        assert _resolve_git_common_dir("git", str(main)) == _resolve_git_common_dir(
            "git", str(wt)
        )


def test_resolve_case_insensitive_on_windows(two_repos):
    a, _ = two_repos
    # normcase on Windows lowercases; on macOS/Linux, case is preserved but
    # the test still passes because both sides see the same case.
    r1 = _resolve_git_common_dir("git", str(a))
    r2 = _resolve_git_common_dir("git", str(a).upper())
    assert r1 == r2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_helpers_git.py -v`
Expected: `ImportError: cannot import name '_resolve_git_common_dir' from 'tools._helpers'`

- [ ] **Step 3: Implement `_resolve_git_common_dir`**

Append to `tools/_helpers.py`:

```python
import os


def _resolve_git_common_dir(git_bin: str, worktree_path: str) -> str:
    """Resolve `git rev-parse --git-common-dir` to an absolute, case-normalized path.

    `git rev-parse --git-common-dir` returns a RELATIVE path (e.g. ".git")
    regardless of the input directory. Two completely unrelated repos both
    return ".git" as a string, so a naive equality check would falsely match
    them — enabling a cross-repo data leak. We must resolve to an absolute
    path and normcase for Windows before comparing.

    See spec §2.3 for the empirical verification.
    """
    raw = subprocess.run(
        [git_bin, "-C", worktree_path, "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    ).stdout.strip()
    return os.path.normcase(os.path.abspath(os.path.join(worktree_path, raw)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_helpers_git.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tools/_helpers.py tests/test_helpers_git.py
git commit -m "feat(spcode): add _resolve_git_common_dir with abs-path defense"
```

## Task 1.2: `_parse_git_worktree_porcelain()` parser

**Files:**
- Modify: `tools/_helpers.py` (append after the previous helper)
- Modify: `tests/test_helpers_git.py` (append)

- [ ] **Step 1: Write failing tests**

```python
# Append to tests/test_helpers_git.py

from tools._helpers import _parse_git_worktree_porcelain


def test_parse_single_main_worktree():
    text = "worktree /r/main\nHEAD abc1234\nbranch refs/heads/main\n"
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 1
    assert result[0] == {
        "path": "/r/main",
        "branch": "main",
        "head_sha": "abc1234",
        "is_main": True,
    }


def test_parse_multiple_worktrees():
    text = (
        "worktree /r/main\n"
        "HEAD 1111111\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /r/feat\n"
        "HEAD 2222222\n"
        "branch refs/heads/feat/x\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert len(result) == 2
    assert result[0]["path"] == "/r/main"
    assert result[0]["is_main"] is True
    assert result[1]["path"] == "/r/feat"
    assert result[1]["branch"] == "feat/x"
    assert result[1]["is_main"] is False


def test_parse_detached_worktree():
    text = (
        "worktree /r/main\n"
        "HEAD 1111111\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /r/detached\n"
        "HEAD 2222222\n"
        "detached\n"
    )
    result = _parse_git_worktree_porcelain(text)
    assert result[1]["branch"] is None
    assert result[1]["is_main"] is False


def test_parse_empty_returns_empty_list():
    assert _parse_git_worktree_porcelain("") == []


def test_parse_malformed_raises():
    """Unrecognized records should raise rather than silently corrupt."""
    import pytest
    with pytest.raises(ValueError, match="Unknown porcelain record"):
        _parse_git_worktree_porcelain("worktree /r/main\nWAT abc\n")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_helpers_git.py -v -k parse`
Expected: ImportError on `_parse_git_worktree_porcelain`

- [ ] **Step 3: Implement the parser**

Append to `tools/_helpers.py`:

```python
def _parse_git_worktree_porcelain(text: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output.

    Returns a list of dicts with keys: path, branch, head_sha, is_main.
    The first worktree in the output is always the main worktree (is_main=True).
    Raises ValueError on unrecognized records.
    """
    worktrees: list[dict] = []
    current: dict | None = None

    for raw_line in text.splitlines():
        if not raw_line:
            # Blank line separates worktree blocks
            if current is not None:
                worktrees.append(current)
                current = None
            continue

        # Porcelain records: "word <value>" where value may contain spaces
        # for `worktree <path>` records. Other records have a single token value.
        if raw_line.startswith("worktree "):
            if current is not None:
                worktrees.append(current)
            current = {
                "path": raw_line[len("worktree "):],
                "branch": None,
                "head_sha": "",
                "is_main": False,
            }
        elif current is None:
            raise ValueError(
                f"Unexpected record outside worktree block: {raw_line!r}"
            )
        elif raw_line.startswith("HEAD "):
            current["head_sha"] = raw_line[len("HEAD "):]
        elif raw_line.startswith("branch "):
            ref = raw_line[len("branch "):]
            # ref form: "refs/heads/<name>"
            prefix = "refs/heads/"
            current["branch"] = ref[len(prefix):] if ref.startswith(prefix) else ref
        elif raw_line == "detached":
            current["branch"] = None
        else:
            raise ValueError(f"Unknown porcelain record: {raw_line!r}")

    if current is not None:
        worktrees.append(current)

    # First worktree in output is always the main worktree (git invariant)
    for i, wt in enumerate(worktrees):
        wt["is_main"] = i == 0

    return worktrees
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_helpers_git.py -v -k parse`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tools/_helpers.py tests/test_helpers_git.py
git commit -m "feat(spcode): add _parse_git_worktree_porcelain with strict validation"
```

- [ ] **Step 6: Chunk 1 review gate**

Before proceeding to Chunk 2, dispatch the plan-document-reviewer subagent to review this chunk. Continue to Chunk 2 only after APPROVED.

---

# Chunk 2: Backend worktree list endpoint

**Goal:** Add `GET /spcode/git-worktrees` that returns the list of worktrees for the currently loaded project's primary directory.

## Task 2.1: `handle_get_git_worktrees()` handler

**Files:**
- Modify: `main.py` (insert before `handle_get_git_diff` at line ~1667)
- Create: `tests/test_git_worktrees.py`

- [ ] **Step 1: Read existing handler to match style**

Read `main.py` lines 1667-1820 (`handle_get_git_diff`) to mirror:
- The `umo` resolution pattern
- The `_make_git_diff_empty_envelope` helper signature
- The `logger.warning` / `logger.debug` log style
- The 200-with-reason error envelope pattern

- [ ] **Step 2: Write failing test for happy path**

```python
# tests/test_git_worktrees.py
import pytest
from pathlib import Path
import subprocess


async def test_returns_single_main_worktree(plugin, tmp_path):
    # tmp_path is a git repo with one worktree (itself)
    (tmp_path / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "x@x"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "x"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)

    # _load_project is a fixture-helper used in test_git_diff.py — copy the pattern
    from tests.test_git_diff import _load_project
    _load_project(plugin, "test:umo", str(tmp_path))

    result = await plugin.handle_get_git_worktrees()
    assert result["status"] == "ok"
    data = result["data"]
    assert data["loaded"] is True
    assert data["directory"] == str(tmp_path)
    assert data["umo"] == "test:umo"
    assert data["reason"] is None
    assert len(data["worktrees"]) == 1
    wt = data["worktrees"][0]
    assert wt["path"] == str(tmp_path)
    assert wt["is_main"] is True
    assert wt["branch"] is not None
    assert len(wt["head_sha"]) > 0
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_git_worktrees.py::test_returns_single_main_worktree -v`
Expected: `AttributeError: 'SPCodeToolkit' object has no attribute 'handle_get_git_worktrees'`

- [ ] **Step 4: Implement `handle_get_git_worktrees`**

Insert in `main.py` just BEFORE `handle_get_git_diff`:

```python
async def handle_get_git_worktrees(self) -> dict:
    """Web API handler for ``GET /spcode/git-worktrees``.

    Returns the list of git worktrees for the currently loaded project's
    primary directory. The first worktree is the main one (`is_main: true`);
    subsequent entries are linked worktrees of the same repository.

    Response shape::

        {
            "status": "ok",
            "data": {
                "loaded": bool,
                "directory": str | None,        # primary worktree path
                "umo": str | None,
                "worktrees": [
                    {"path": str, "branch": str | null,
                     "head_sha": str, "is_main": bool}
                ] | None,
                "reason": str | None,
                "stderr": str,                  # only for git_error
                "elapsed_ms": int
            }
        }
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # 1. Resolve umo
    from astrbot.api import web
    umo: str | None = None
    try:
        umo = web.request.query.get("umo") or None
    except Exception:
        umo = None

    # 2. Resolve project (same fallback rules as git-diff / project-status)
    info = self._resolve_loaded_project(umo)  # returns dict | None
    if info is None:
        return self._make_git_worktrees_empty_envelope(
            umo=umo, reason="no_project_loaded", elapsed_ms=_elapsed()
        )
    directory = info.get("directory", "")

    # 3. Feature flag check
    if not (self._config.get("agentsmd_enabled", True)
            and self._config.get("codegraph_enabled", True)):
        return self._make_git_worktrees_empty_envelope(
            umo=umo, directory=directory, reason="feature_disabled",
            elapsed_ms=_elapsed(),
        )

    # 4. Directory existence
    if not Path(directory).is_dir():
        return self._make_git_worktrees_empty_envelope(
            umo=umo, directory=directory, reason="directory_missing",
            elapsed_ms=_elapsed(),
        )

    # 5. Git repo probe
    git_bin = self._git_binary()
    probe = await run_sync(
        run_cmd,
        [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            return self._make_git_worktrees_empty_envelope(
                umo=umo, directory=directory, reason="not_a_git_repo",
                elapsed_ms=_elapsed(),
            )
        if "未安装" in probe.get("error", ""):
            return self._make_git_worktrees_empty_envelope(
                umo=umo, directory=directory, reason="git_unavailable",
                elapsed_ms=_elapsed(),
            )
        return self._make_git_worktrees_empty_envelope(
            umo=umo, directory=directory, reason="git_error",
            stderr=probe.get("stderr", "") or probe.get("error", ""),
            elapsed_ms=_elapsed(),
        )

    # 6. List worktrees
    list_result = await run_sync(
        run_cmd,
        [git_bin, "-C", directory, "worktree", "list", "--porcelain"],
        encoding="utf-8",
    )
    if not list_result["ok"]:
        return self._make_git_worktrees_empty_envelope(
            umo=umo, directory=directory, reason="git_error",
            stderr=list_result.get("stderr", "") or list_result.get("error", ""),
            elapsed_ms=_elapsed(),
        )

    try:
        worktrees = _parse_git_worktree_porcelain(list_result["stdout"])
    except ValueError as e:
        logger.warning(f"[git-worktrees] porcelain parse failed: {e}")
        return self._make_git_worktrees_empty_envelope(
            umo=umo, directory=directory, reason="git_error",
            stderr=str(e), elapsed_ms=_elapsed(),
        )

    elapsed = _elapsed()
    logger.debug(f"[git-worktrees] listed {len(worktrees)} worktrees in {elapsed}ms")
    return {
        "status": "ok",
        "data": {
            "loaded": True,
            "directory": directory,
            "umo": umo,
            "worktrees": worktrees,
            "reason": None,
            "stderr": "",
            "elapsed_ms": elapsed,
        },
    }
```

- [ ] **Step 5: Add the envelope helper**

In `main.py`, near the existing `_make_git_diff_empty_envelope` (around line 70-90), add:

```python
def _make_git_worktrees_empty_envelope(
    *,
    umo: str | None = None,
    directory: str | None = None,
    reason: str,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    return {
        "status": "ok",
        "data": {
            "loaded": False,
            "directory": directory,
            "umo": umo,
            "worktrees": None,
            "reason": reason,
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
        },
    }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_git_worktrees.py::test_returns_single_main_worktree -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_git_worktrees.py
git commit -m "feat(spcode): add /spcode/git-worktrees endpoint"
```

## Task 2.2: Register the new endpoint

**Files:**
- Modify: `main.py` `initialize()` method (find where `/spcode/project-status` and `/spcode/git-diff` are registered, line ~1080-1110)

- [ ] **Step 1: Find the registration block**

```bash
grep -n "spcode/git-diff\|spcode/project-status" main.py
```

- [ ] **Step 2: Add registration next to existing git-diff registration**

Add a sibling line (e.g., right after the `/spcode/git-diff` registration):

```python
self.context.register_web_api_route("/spcode/git-worktrees", "GET", self.handle_get_git_worktrees)
```

(Use the exact registration API the codebase uses. The snippet above is illustrative — match the existing pattern from the `/spcode/git-diff` registration 5 lines above.)

- [ ] **Step 3: Smoke-test that the endpoint is reachable**

In a Python REPL or test:

```python
from astrbot_plugin_spcode_toolkit.main import SPCodeToolkit
# (follow the existing test pattern for booting the plugin)
# then call plugin.context.web_api.routes  # adjust to your registration API
# and confirm "/spcode/git-worktrees" appears
```

Or: write a small integration test in `tests/test_git_worktrees.py`:

```python
def test_endpoint_registered(plugin):
    routes = plugin.context.web_api_routes  # or whatever accessor exposes routes
    assert "/spcode/git-worktrees" in [r.path for r in routes if r.method == "GET"]
```

Adjust the `routes` accessor to match the actual codebase. Skip this step if the codebase has no clean route-introspection API.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(spcode): register /spcode/git-worktrees route"
```

- [ ] **Step 5: Chunk 2 review gate**

Dispatch plan-document-reviewer. Continue to Chunk 3 only after APPROVED.

---

# Chunk 3: Backend diff endpoint extension (?worktree param)

**Goal:** Extend `handle_get_git_diff` to accept `?worktree=<path>` with 6-step validation. When valid, run all 4 git diff subprocesses with `git -C <worktree>` instead of primary. When invalid, return a structured `reason`.

## Task 3.1: Extract worktree validation into a helper

**Files:**
- Modify: `main.py` (insert helper near `_make_git_diff_empty_envelope`)
- Create: `tests/test_git_diff_worktree.py` (or add to existing test_git_diff.py)

- [ ] **Step 1: Write failing tests for the validator**

```python
# Add to tests/test_git_diff.py as TestWorktreeValidation class
import pytest
from pathlib import Path
import subprocess
import tempfile


@pytest.fixture
def repo_with_worktree():
    with tempfile.TemporaryDirectory() as tmp:
        main = Path(tmp) / "main"
        main.mkdir()
        subprocess.run(["git", "init", "-q", str(main)], check=True)
        subprocess.run(["git", "-C", str(main), "config", "user.email", "x@x"], check=True)
        subprocess.run(["git", "-C", str(main), "config", "user.name", "x"], check=True)
        (main / "a.txt").write_text("a")
        subprocess.run(["git", "-C", str(main), "add", "."], check=True)
        subprocess.run(["git", "-C", str(main), "commit", "-q", "-m", "init"], check=True)
        wt = Path(tmp) / "wt2"
        subprocess.run(
            ["git", "-C", str(main), "worktree", "add", str(wt), "-b", "feat"],
            check=True,
        )
        yield str(main), str(wt)


class TestWorktreeValidation:
    def test_none_passes_through_as_primary(self, plugin, repo_with_worktree):
        """?worktree absent or empty → validation skipped, primary used."""
        main, _ = repo_with_worktree
        # No ?worktree — should not raise
        result = plugin._validate_worktree_param(None, main, file_remove_blacklist=[])
        assert result is None

    def test_empty_string_passes_through(self, plugin, repo_with_worktree):
        main, _ = repo_with_worktree
        assert plugin._validate_worktree_param("", main, file_remove_blacklist=[]) is None
        assert plugin._validate_worktree_param("   ", main, file_remove_blacklist=[]) is None

    def test_too_long_rejected(self, plugin, repo_with_worktree):
        main, _ = repo_with_worktree
        result = plugin._validate_worktree_param("a" * 5000, main, file_remove_blacklist=[])
        assert result["reason"] == "worktree_path_invalid"

    def test_dotdot_rejected(self, plugin, repo_with_worktree):
        main, _ = repo_with_worktree
        result = plugin._validate_worktree_param(
            "../etc/passwd", main, file_remove_blacklist=[]
        )
        assert result["reason"] == "worktree_path_invalid"

    def test_nonexistent_path_rejected(self, plugin, repo_with_worktree):
        main, _ = repo_with_worktree
        result = plugin._validate_worktree_param(
            str(Path(main).parent / "ghost"), main, file_remove_blacklist=[]
        )
        assert result["reason"] == "worktree_missing"

    def test_different_repo_rejected(self, plugin, repo_with_worktree):
        """CRITICAL: cross-repo paths must be rejected (spec §2.3)."""
        main, _ = repo_with_worktree
        with tempfile.TemporaryDirectory() as tmp2:
            other = Path(tmp2) / "other"
            other.mkdir()
            subprocess.run(["git", "init", "-q", str(other)], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.email", "x@x"], check=True)
            subprocess.run(["git", "-C", str(other), "config", "user.name", "x"], check=True)
            (other / "b.txt").write_text("b")
            subprocess.run(["git", "-C", str(other), "add", "."], check=True)
            subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "init"], check=True)
            result = plugin._validate_worktree_param(
                str(other), main, file_remove_blacklist=[]
            )
            assert result["reason"] == "worktree_not_in_repo"

    def test_valid_worktree_passes(self, plugin, repo_with_worktree):
        main, wt = repo_with_worktree
        result = plugin._validate_worktree_param(wt, main, file_remove_blacklist=[])
        assert result is None  # None = OK, use as resolved_worktree
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_git_diff.py::TestWorktreeValidation -v`
Expected: `AttributeError: 'SPCodeToolkit' object has no attribute '_validate_worktree_param'`

- [ ] **Step 3: Implement `_validate_worktree_param`**

Insert in `main.py` near `_make_git_diff_empty_envelope`:

```python
MAX_WORKTREE_PATH_LEN = 4096


def _validate_worktree_param(
    self,
    worktree_raw: str | None,
    primary: str,
    *,
    file_remove_blacklist: list,
) -> dict | None:
    """Validate the ?worktree query parameter. Returns None on success,
    or a dict with `reason` (and optionally `directory`) on failure.

    Implements the 6-step defense in spec §2.4:
      ① length & format (≤4096 chars, no `..`)
      ② Path.resolve() (parse symlinks/relative paths)
      ③ _is_path_safe blacklist
      ④ Path.is_dir()
      ⑤ git rev-parse --is-inside-work-tree
      ⑥ _resolve_git_common_dir matches primary
    """
    # Empty / None → fall through to primary (v1 behavior preserved)
    if worktree_raw is None:
        return None
    worktree_stripped = worktree_raw.strip()
    if not worktree_stripped:
        return None

    # ① format
    if len(worktree_stripped) > MAX_WORKTREE_PATH_LEN:
        return {"reason": "worktree_path_invalid", "directory": worktree_stripped}
    if ".." in Path(worktree_stripped).parts:
        return {"reason": "worktree_path_invalid", "directory": worktree_stripped}

    # ② resolve symlinks / relative paths / drive letters
    try:
        worktree_resolved = str(Path(worktree_stripped).resolve())
    except (OSError, ValueError) as e:
        return {"reason": "worktree_path_invalid", "directory": worktree_stripped}

    # ③ blacklist
    try:
        ok, _reason = _is_path_safe(
            Path(worktree_resolved), user_blacklist=file_remove_blacklist
        )
    except Exception:
        ok = False
    if not ok:
        return {"reason": "worktree_path_unsafe", "directory": worktree_resolved}

    # ④ existence
    if not Path(worktree_resolved).is_dir():
        return {"reason": "worktree_missing", "directory": worktree_resolved}

    # ⑤ git worktree check + ⑥ common-dir match
    git_bin = self._git_binary()
    probe = run_cmd(
        [git_bin, "-C", worktree_resolved, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        return {"reason": "not_a_git_repo", "directory": worktree_resolved}

    common_primary = _resolve_git_common_dir(git_bin, primary)
    common_requested = _resolve_git_common_dir(git_bin, worktree_resolved)
    if common_primary != common_requested:
        return {"reason": "worktree_not_in_repo", "directory": worktree_resolved}

    return None  # success — caller uses worktree_resolved
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_git_diff.py::TestWorktreeValidation -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_git_diff.py
git commit -m "feat(spcode): add _validate_worktree_param with 6-step defense"
```

## Task 3.2: Wire `?worktree` into `handle_get_git_diff`

**Files:**
- Modify: `main.py` (line ~1693-1820, the `handle_get_git_diff` method)

- [ ] **Step 1: Write failing test for the wired-up endpoint**

```python
# Add to tests/test_git_diff.py
class TestGitDiffWithWorktree:
    async def test_no_worktree_param_uses_primary(self, plugin, tmp_path):
        # v1 backward compat: omitted ?worktree returns primary diff
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "x@x"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "x"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
        (tmp_path / "b.txt").write_text("new", encoding="utf-8")
        from tests.test_git_diff import _load_project
        _load_project(plugin, "test:umo", str(tmp_path))
        result = await plugin.handle_get_git_diff()
        data = result["data"]
        assert data["loaded"] is True
        assert data["reason"] is None
        assert data["worktree"] == str(tmp_path)
        assert "+new" in data["diff"]

    async def test_specific_worktree_returns_its_diff(self, plugin):
        # Use the repo_with_worktree fixture from Task 3.1
        main, wt = ...  # build via subprocess as in repo_with_worktree fixture
        # Modify a file in wt only
        (Path(wt) / "a.txt").write_text("modified in wt", encoding="utf-8")
        from tests.test_git_diff import _load_project
        _load_project(plugin, "test:umo", main)
        # Simulate the query param via the request helper used in your test infra
        # ... call plugin.handle_get_git_diff(worktree=wt) or set the query
        result = await plugin.handle_get_git_diff()
        # assertion depends on how the test framework injects query params
        # See your existing test pattern for project-status (which also reads ?umo)
```

**Note**: the exact mechanism for injecting `?worktree` into `handle_get_git_diff` from tests depends on the test infrastructure (likely a mock `web.request.query` similar to the existing umo tests). Mirror the pattern used by `test_umo_fallback_to_most_recent` in `test_git_diff.py`.

- [ ] **Step 2: Read existing handler signature & query injection pattern**

Read `main.py` lines 1693-1710 (the `web.request.query.get("umo")` block) to understand the existing pattern for query parameter injection in tests.

- [ ] **Step 3: Wire `?worktree` into the handler**

Make these targeted edits in `handle_get_git_diff`:

1. **Add worktree reading** (right after the `umo` block, line ~1705):

```python
worktree_raw: str | None = None
try:
    worktree_raw = web.request.query.get("worktree") or None
except Exception:
    worktree_raw = None
```

2. **Add validation step** (right after the git probe at line ~1770, before the `git_prefix` line at line 1781):

```python
# Validate ?worktree (6-step defense, spec §2.4)
worktree_validation = self._validate_worktree_param(
    worktree_raw,
    directory,
    file_remove_blacklist=self._config.get("file_remove_blacklist") or [],
)
if worktree_validation is not None:
    return _make_git_diff_empty_envelope(
        umo=umo,
        reason=worktree_validation["reason"],
        directory=worktree_validation.get("directory", directory),
        elapsed_ms=_elapsed(),
    )

# Resolve the effective worktree path
resolved_worktree = directory  # default
if worktree_raw is not None:
    worktree_stripped = worktree_raw.strip()
    if worktree_stripped:
        resolved_worktree = str(Path(worktree_stripped).resolve())
```

3. **Use `resolved_worktree` in the `git_prefix` line** (line ~1781):

```python
# OLD: git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]
git_prefix = [git_bin, "-C", resolved_worktree, "-c", "color.ui=never"]
```

4. **Set `data.worktree` in the success response** (find the success envelope block, around line 1810-1820):

```python
# In the success return, add (or update):
"worktree": resolved_worktree,
"directory": resolved_worktree,  # override: now reflects the effective worktree
```

- [ ] **Step 4: Run all diff tests to confirm nothing regressed**

Run: `pytest tests/test_git_diff.py -v`
Expected: all old tests pass + new worktree tests pass

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_git_diff.py
git commit -m "feat(spcode): wire ?worktree param into handle_get_git_diff"
```

- [ ] **Step 6: Chunk 3 review gate**

Dispatch plan-document-reviewer. Continue to Chunk 4 only after APPROVED.

---

# Chunk 4: Frontend types + `useSpcodeWorktrees` composable

**Goal:** Add the type definitions and a new composable for fetching the worktree list.

## Task 4.1: Add types to `parseSpcodeGitDiff.ts`

**Files:**
- Modify: `src/composables/parseSpcodeGitDiff.ts`

- [ ] **Step 1: Add `SpcodeWorktree` and `SpcodeGitWorktreesRawResponse` types**

Append to the file (after existing types, before the export list is closed):

```typescript
export interface SpcodeWorktree {
  path: string
  branch: string | null    // null = detached HEAD
  head_sha: string
  is_main: boolean
}

export interface SpcodeGitWorktreesRawResponse {
  loaded: boolean
  directory: string | null
  umo: string | null
  worktrees: SpcodeWorktree[] | null
  reason: string | null
  stderr?: string
  elapsed_ms: number
}
```

- [ ] **Step 2: Add `worktree` field to `SpcodeGitDiffRawResponse`**

Find the existing interface and add the new field. Match the type of `directory`:

```typescript
export interface SpcodeGitDiffRawResponse {
  // ... existing fields ...
  directory: string | null
  /** Effective worktree path used for the diff (spec §2.2).
   *  Always equal to `directory` in v1; the two fields are kept separate
   *  to allow future divergence (e.g. staged diff vs worktree diff). */
  worktree: string | null
  // ... rest of fields ...
}
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd F:\github\Astrbot\dashboard && pnpm tsc --noEmit` (or `npm run typecheck` / `npx vue-tsc --noEmit` per the project's setup)
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add src/composables/parseSpcodeGitDiff.ts
git commit -m "feat(dashboard): add SpcodeWorktree types and worktree field on diff response"
```

## Task 4.2: Create `useSpcodeWorktrees` composable

**Files:**
- Create: `src/composables/useSpcodeWorktrees.ts`

- [ ] **Step 1: Write the composable**

```typescript
// src/composables/useSpcodeWorktrees.ts
//
// Author: elecvoid243
// Date: 2026-06-18
// Spec: docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md §3.3
//
// Fetches the list of git worktrees for the currently loaded project.
// Does NOT poll (worktree list is stable; only changes on
// `git worktree add`/`remove`). Does NOT watch umo internally — the
// caller (GitDiffSidebar) already watches project directory and triggers
// refresh explicitly.

import { ref, type Ref } from 'vue'
import { pluginExtensionApi } from '@/api/v1'
import { useSpcodeProjectStatus } from '@/composables/useSpcodeProjectStatus'
import type {
  SpcodeGitWorktreesRawResponse,
  SpcodeWorktree,
} from '@/composables/parseSpcodeGitDiff'

export interface UseSpcodeWorktrees {
  worktrees: Ref<SpcodeWorktree[]>
  isLoading: Ref<boolean>
  error: Ref<string | null>
  refresh: () => Promise<void>
}

export function useSpcodeWorktrees(): UseSpcodeWorktrees {
  const worktrees = ref<SpcodeWorktree[]>([])
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const spcodeStatus = useSpcodeProjectStatus()

  async function refresh(): Promise<void> {
    if (isLoading.value) return
    const umo = spcodeStatus.status.value.umo
    if (!umo) {
      worktrees.value = []
      error.value = 'no_project_loaded'
      return
    }
    isLoading.value = true
    error.value = null
    try {
      const resp = await pluginExtensionApi.get<SpcodeGitWorktreesRawResponse>(
        'spcode/git-worktrees',
        { params: { umo } },
      )
      const data = resp.data?.data
      if (data?.loaded && data.worktrees) {
        worktrees.value = data.worktrees
        error.value = null
      } else {
        worktrees.value = []
        error.value = data?.reason ?? 'unknown'
      }
    } catch (err) {
      worktrees.value = []
      error.value = classifyError(err)
    } finally {
      isLoading.value = false
    }
  }

  return { worktrees, isLoading, error, refresh }
}

function classifyError(err: unknown): string {
  if (typeof err === 'object' && err !== null) {
    const anyErr = err as { code?: string; response?: { status?: number } }
    if (anyErr.code === 'ERR_NETWORK') return 'network'
    // 404 from a legacy backend (no /spcode/git-worktrees route) is
    // treated as "unknown" — the spec defines this as a graceful
    // degradation: tabs are hidden, the v1 diff still works.
    if (anyErr.response?.status === 404) return 'unknown'
  }
  return 'unknown'
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `pnpm tsc --noEmit`
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add src/composables/useSpcodeWorktrees.ts
git commit -m "feat(dashboard): add useSpcodeWorktrees composable"
```

- [ ] **Step 4: Chunk 4 review gate**

Dispatch plan-document-reviewer. Continue to Chunk 5 only after APPROVED.

---

# Chunk 5: Frontend `useSpcodeGitDiff` extension

**Goal:** Make the diff composable accept an active worktree ref and auto-refresh on changes.

## Task 5.1: Extend `useSpcodeGitDiff` signature

**Files:**
- Modify: `src/composables/useSpcodeGitDiff.ts`

- [ ] **Step 1: Read the existing composable to understand structure**

Read `src/composables/useSpcodeGitDiff.ts` (full file is ~115 lines per the spec exploration).

- [ ] **Step 2: Add `worktreeRef` parameter and watcher**

Targeted edits to the file:

1. **Update imports** — add `watch`, `toValue`, `MaybeRef` from Vue:

```typescript
import { ref, watch, toValue, type Ref, type MaybeRef } from 'vue'
```

2. **Change the exported function signature**:

```typescript
// OLD: function useSpcodeGitDiff(): UseSpcodeGitDiff
export function useSpcodeGitDiff(
  worktreeRef: MaybeRef<string | null>,
): UseSpcodeGitDiff
```

3. **Inside the function body, read worktree in `refresh()`** (find the `params: { umo }` block):

```typescript
// OLD: params: { umo },
params: {
  umo,
  ...(toValue(worktreeRef) ? { worktree: toValue(worktreeRef) } : {}),
},
```

4. **Add the worktree watcher** (insert after `dispose()` or at end of function body):

```typescript
// Watch worktreeRef changes; auto-refetch the diff for the new worktree.
// flush: 'post' defers the refresh to after Vue commits the DOM update,
// preventing cascade re-renders if multiple state changes occur in the
// same tick. immediate: false (default) avoids a duplicate fetch on
// mount — the initial fetch is triggered by the sidebar's modelValue
// watcher.
watch(
  worktreeRef,
  () => {
    if (isMounted) void refresh()
  },
  { flush: 'post' },
)
```

- [ ] **Step 3: Update existing call sites of `useSpcodeGitDiff()`**

Search for all callers (likely just `GitDiffSidebar.vue`):

```bash
grep -rn "useSpcodeGitDiff" src/
```

For each caller, pass a `ref<string | null>(null)` (or similar). **The call site changes are part of Chunk 6** (where the sidebar gets its own `selectedWorktree` ref). For now, just verify the composable compiles standalone.

- [ ] **Step 4: TypeScript check**

Run: `pnpm tsc --noEmit`
Expected: errors at call sites that haven't been updated yet (expected; will be fixed in Chunk 6)

- [ ] **Step 5: Commit (composable only)**

```bash
git add src/composables/useSpcodeGitDiff.ts
git commit -m "feat(dashboard): useSpcodeGitDiff accepts worktreeRef with auto-refetch"
```

---

# Chunk 6: Frontend UI (sidebar tabs + reason map + i18n)

**Goal:** Wire everything together: `selectedWorktree` ref, sequential fetch flow, tabs UI, error reason extension, i18n keys.

## Task 6.1: Update `GitDiffBodyContent` reason map

**Files:**
- Modify: `src/components/chat/message_list_comps/GitDiffBodyContent.vue`

- [ ] **Step 1: Find the `REASON_I18N_KEYS` constant**

```bash
grep -n "REASON_I18N_KEYS" src/components/chat/message_list_comps/GitDiffBodyContent.vue
```

- [ ] **Step 2: Add 4 new reason keys**

```typescript
const REASON_I18N_KEYS: Record<string, string> = {
  // ... existing 7 entries ...
  worktree_path_invalid: 'spcodeProjectLoad.diffSidebar.error.reason.worktree_path_invalid',
  worktree_path_unsafe:  'spcodeProjectLoad.diffSidebar.error.reason.worktree_path_unsafe',
  worktree_missing:      'spcodeProjectLoad.diffSidebar.error.reason.worktree_missing',
  worktree_not_in_repo:  'spcodeProjectLoad.diffSidebar.error.reason.worktree_not_in_repo',
}
```

- [ ] **Step 3: Commit**

```bash
git add src/components/chat/message_list_comps/GitDiffBodyContent.vue
git commit -m "feat(dashboard): add 4 worktree error reason i18n keys"
```

## Task 6.2: i18n locale files (4 locales)

**Files:**
- Modify: `src/i18n/locales/zh-CN/features/chat.json`
- Modify: `src/i18n/locales/en-US/features/chat.json`
- Modify: `src/i18n/locales/ru-RU/features/chat.json`
- Modify: `src/i18n/locales/ja-JP/features/chat.json`

- [ ] **Step 1: Find the existing `spcodeProjectLoad.diffSidebar.error.reason` block in each file**

```bash
grep -n "worktree_path\|spcodeProjectLoad" src/i18n/locales/*/features/chat.json | head -30
```

- [ ] **Step 2: For each locale file, add the 4 new reason keys under `spcodeProjectLoad.diffSidebar.error.reason` and the new `worktreeTabs` key**

Add to each of the 4 locale files:

```json
"worktreeTabs": {
  "ariaLabel": "<localized text>"
},
"error": {
  "reason": {
    "feature_disabled": "<existing>",
    ...,
    "worktree_path_invalid": "<localized text>",
    "worktree_path_unsafe":  "<localized text>",
    "worktree_missing":      "<localized text>",
    "worktree_not_in_repo":  "<localized text>"
  }
}
```

Localized text per locale (use these strings):

| key | zh-CN | en-US | ru-RU | ja-JP |
|-----|-------|-------|-------|-------|
| ariaLabel | 选择 worktree 查看 diff | Select worktree to view diff | Выберите worktree для просмотра diff | worktree を選択して diff を表示 |
| worktree_path_invalid | 无效的 worktree 路径 | Invalid worktree path | Недопустимый путь worktree | 無効な worktree パス |
| worktree_path_unsafe | worktree 路径被安全策略拒绝 | Worktree path blocked by security policy | Путь worktree заблокирован политикой безопасности | worktree パスはセキュリティポリシーでブロックされています |
| worktree_missing | Worktree 已不存在（可能已被外部 `git worktree remove`） | Worktree no longer exists (possibly removed externally) | Worktree больше не существует (возможно, удалён извне) | Worktree が存在しません（外部から削除された可能性があります） |
| worktree_not_in_repo | 该路径不属于当前项目的 git 仓库 | Path does not belong to current project's git repository | Путь не принадлежит git-репозиторию текущего проекта | このパスは現在のプロジェクトの git リポジトリに属していません |

- [ ] **Step 3: Validate JSON syntax**

```bash
for f in src/i18n/locales/*/features/chat.json; do
  python -c "import json; json.load(open('$f', encoding='utf-8'))" || echo "INVALID: $f"
done
```

Expected: no "INVALID" lines

- [ ] **Step 4: Commit**

```bash
git add src/i18n/locales/
git commit -m "feat(dashboard): add 4 worktree error reasons + tabs aria-label i18n"
```

## Task 6.3: `GitDiffSidebar.vue` — full UI integration

**Files:**
- Modify: `src/components/chat/GitDiffSidebar.vue`

This is the largest change. Do it in tightly-scoped commits.

- [ ] **Step 1: Add imports + `selectedWorktree` ref + `useSpcodeWorktrees`**

In `<script setup>`:

```typescript
import { ref, watch, onBeforeUnmount, computed, toRef } from 'vue'  // add toRef
import { useSpcodeWorktrees } from '@/composables/useSpcodeWorktrees'

// ... existing code ...

const wt = useSpcodeWorktrees()
const selectedWorktree = ref<string | null>(null)
const composable = useSpcodeGitDiff(selectedWorktree)  // CHANGED: was useSpcodeGitDiff()
```

- [ ] **Step 2: Replace parallel fetch with sequential flow (spec §3.4)**

Find the `watch(() => props.modelValue, ...)` block and replace with:

```typescript
watch(() => props.modelValue, async (open) => {
  if (open) {
    await wt.refresh()
    if (wt.worktrees.value.length > 0) {
      const primary = wt.worktrees.value.find(w => w.is_main) ?? wt.worktrees.value[0]
      selectedWorktree.value = primary.path
    }
    await composable.refresh()
    if (props.modelValue) composable.startPolling(10_000)
  } else {
    composable.stopPolling()
  }
}, { immediate: true })
```

- [ ] **Step 3: Add `onSelectWorktree` helper + `onManualRefresh` extension**

```typescript
function onSelectWorktree(path: string | null): void {
  if (selectedWorktree.value === path) return
  selectedWorktree.value = path
  expandedSet.value = new Set()
}

// REPLACE existing onManualRefresh with:
async function onManualRefresh(): Promise<void> {
  if (isFetching.value) return
  isFetching.value = true
  try {
    await Promise.all([wt.refresh(), composable.refresh()])
  } finally {
    isFetching.value = false
  }
}
```

- [ ] **Step 4: Add project-change watcher (resets selection when loaded dir changes)**

Add a new watch (separate from the existing `modelValue` and `loaded` ones):

```typescript
watch(
  () => spcodeStatus.status.value.directory,
  async (newDir, oldDir) => {
    if (newDir === oldDir) return
    selectedWorktree.value = null
    expandedSet.value = new Set()
    if (props.modelValue) {
      await Promise.all([wt.refresh(), composable.refresh()])
    }
  },
)
```

- [ ] **Step 5: Add tabs template + CSS**

In the template, **insert a new `<div class="worktree-tabs">` block** between the existing `<div class="git-diff-sidebar-warning">` and `<div class="git-diff-sidebar-body">`:

```vue
<div
  v-if="wt.worktrees.value.length >= 2 && !wt.isLoading.value && !wt.error.value"
  class="worktree-tabs"
  :aria-label="tm('spcodeProjectLoad.diffSidebar.worktreeTabs.ariaLabel')"
>
  <button
    v-for="w in wt.worktrees.value"
    :key="w.path"
    type="button"
    class="worktree-tab"
    :class="{ active: w.path === selectedWorktree, primary: w.is_main }"
    @click="onSelectWorktree(w.path)"
  >
    <v-icon
      :icon="w.is_main ? 'mdi-home' : 'mdi-source-branch'"
      size="12"
      class="worktree-tab-icon"
    />
    <span class="worktree-tab-label">
      {{ w.branch ?? w.head_sha.slice(0, 7) }}
    </span>
    <v-tooltip v-if="!w.branch" location="bottom" :open-delay="200">
      <template #activator="{ props: tipProps }">
        <span v-bind="tipProps" class="worktree-tab-sha-anchor" />
      </template>
      <span class="worktree-tab-sha">{{ w.head_sha }}</span>
    </v-tooltip>
  </button>
</div>
```

In `<style scoped>`, append:

```css
.worktree-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  padding: 6px 14px 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  max-height: 30%;
  overflow-y: auto;
}

.worktree-tab {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px;
  border-radius: 6px;
  font-size: 12px;
  font-family: monospace;
  background: rgba(var(--v-theme-on-surface), 0.04);
  color: rgba(var(--v-theme-on-surface), 0.7);
  border: 1px solid transparent;
  cursor: pointer;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}

.worktree-tab:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.worktree-tab.active {
  background: rgba(var(--v-theme-primary), 0.12);
  color: rgb(var(--v-theme-primary));
  border-color: rgba(var(--v-theme-primary), 0.3);
}

.worktree-tab-icon { flex-shrink: 0; }
.worktree-tab-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.worktree-tab-sha-anchor { display: inline-block; width: 0; height: 0; }
.worktree-tab-sha { font-family: monospace; font-size: 12px; }

@media (max-width: 760px) {
  .worktree-tab { font-size: 13px; }
  .worktree-tab .worktree-tab-icon { font-size: 14px; }
  .worktree-tabs { padding: 6px 12px 8px; }
}
```

- [ ] **Step 6: TypeScript check**

Run: `pnpm tsc --noEmit`
Expected: 0 errors

- [ ] **Step 7: Manual smoke test**

1. `cd F:\github\Astrbot\dashboard && pnpm dev`
2. Open browser, load a project that has multiple worktrees
3. Open GitDiffSidebar → tabs should appear after worktrees load
4. Click a non-primary tab → diff should reload for that worktree
5. Click Refresh → both worktrees + diff should reload
6. `/project load <other-dir>` → tabs should reset to new primary

- [ ] **Step 8: Commit**

```bash
git add src/components/chat/GitDiffSidebar.vue
git commit -m "feat(dashboard): add worktree switcher tabs to GitDiffSidebar"
```

- [ ] **Step 9: Chunk 6 review gate**

Dispatch plan-document-reviewer. Continue to Chunk 7 only after APPROVED.

---

# Chunk 7: Tests + Documentation

**Goal:** Add automated tests for both repos and write user-facing documentation.

## Task 7.1: Frontend tests

**Files:**
- Create: `tests/composables/useSpcodeWorktrees.test.ts` (new — no existing test file)
- Create: `tests/composables/useSpcodeGitDiff.test.ts` (new — no existing test file)
- Create: `tests/components/GitDiffSidebar.test.ts` (new — no existing test file)

- [ ] **Step 1: Create `useSpcodeWorktrees.test.ts`**

```typescript
// tests/composables/useSpcodeWorktrees.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useSpcodeWorktrees } from '@/composables/useSpcodeWorktrees'

// Mock the API client — match the pattern in useSpcodeGitDiff.test.ts
vi.mock('@/api/v1', () => ({
  pluginExtensionApi: {
    get: vi.fn(),
  },
}))

vi.mock('@/composables/useSpcodeProjectStatus', () => ({
  useSpcodeProjectStatus: () => ({
    status: { value: { umo: 'test:umo', loaded: true, directory: '/r' } },
  }),
}))

import { pluginExtensionApi } from '@/api/v1'

describe('useSpcodeWorktrees', () => {
  beforeEach(() => {
    vi.mocked(pluginExtensionApi.get).mockReset()
  })

  it('fetches and stores worktree list on success', async () => {
    vi.mocked(pluginExtensionApi.get).mockResolvedValue({
      data: {
        data: {
          loaded: true,
          directory: '/r',
          umo: 'test:umo',
          worktrees: [
            { path: '/r', branch: 'main', head_sha: 'abc1234', is_main: true },
            { path: '/r/wt2', branch: 'feat', head_sha: 'def5678', is_main: false },
          ],
          reason: null,
          elapsed_ms: 5,
        },
      },
    } as any)

    const { worktrees, error, refresh } = useSpcodeWorktrees()
    await refresh()

    expect(error.value).toBeNull()
    expect(worktrees.value).toHaveLength(2)
    expect(worktrees.value[0].is_main).toBe(true)
  })

  it('returns empty list and error reason when backend returns not_a_git_repo', async () => {
    vi.mocked(pluginExtensionApi.get).mockResolvedValue({
      data: { data: { loaded: false, worktrees: null, reason: 'not_a_git_repo' } },
    } as any)

    const { worktrees, error, refresh } = useSpcodeWorktrees()
    await refresh()

    expect(worktrees.value).toEqual([])
    expect(error.value).toBe('not_a_git_repo')
  })

  it('treats 404 from legacy backend as unknown error (graceful degradation)', async () => {
    // spec §6.1 "新 dashboard × 旧 backend" 降级路径
    vi.mocked(pluginExtensionApi.get).mockRejectedValue({
      response: { status: 404 },
    })

    const { worktrees, error, refresh } = useSpcodeWorktrees()
    await refresh()

    expect(worktrees.value).toEqual([])
    expect(error.value).toBe('unknown')
  })
})
```

- [ ] **Step 2: Extend `useSpcodeGitDiff.test.ts`**

Add test cases (match the existing test style):

```typescript
describe('useSpcodeGitDiff with worktree ref', () => {
  it('omits ?worktree param when ref is null', async () => {
    const ref = ref<string | null>(null)
    const { refresh } = useSpcodeGitDiff(ref)
    await refresh()
    expect(vi.mocked(pluginExtensionApi.get).mock.calls[0][1]?.params)
      .toEqual({ umo: 'test:umo' })
  })

  it('includes ?worktree param when ref is set', async () => {
    const worktreeRef = ref<string | null>(null)
    const { refresh } = useSpcodeGitDiff(worktreeRef)
    worktreeRef.value = '/r/wt2'
    await refresh()
    expect(vi.mocked(pluginExtensionApi.get).mock.calls[0][1]?.params)
      .toEqual({ umo: 'test:umo', worktree: '/r/wt2' })
  })

  it('refetches when worktree ref changes', async () => {
    const worktreeRef = ref<string | null>(null)
    const { refresh } = useSpcodeGitDiff(worktreeRef)
    await refresh()  // initial
    worktreeRef.value = '/r/wt2'
    await nextTick()
    await new Promise(r => setTimeout(r, 0))  // let the watcher's microtask run
    expect(vi.mocked(pluginExtensionApi.get)).toHaveBeenCalledTimes(2)
  })
})
```

- [ ] **Step 3: Extend `GitDiffSidebar.test.ts`**

Add test cases:

```typescript
describe('GitDiffSidebar with worktree tabs', () => {
  it('hides tabs when worktree count < 2', async () => { ... })
  it('hides tabs while worktree list is loading', async () => { ... })
  it('hides tabs when worktree list errors', async () => { ... })
  it('shows primary worktree active by default', async () => { ... })
  it('clicking tab updates selectedWorktree', async () => { ... })
  it('resets to primary when project changes', async () => { ... })
  it('directory tooltip reflects active worktree path', async () => { ... })
  it('hides tabs when /spcode/git-worktrees returns 404 (legacy backend)', async () => { ... })
})
```

(Full code: mirror the pattern from existing tests in the file. Use `wrapper.find('[data-testid="worktree-tab"]')` after adding `data-testid` attributes in the template.)

Add `data-testid` attributes to the tabs in the template (in `GitDiffSidebar.vue`):

```vue
<button
  v-for="w in wt.worktrees.value"
  :key="w.path"
  :data-testid="`worktree-tab-${w.branch ?? w.head_sha.slice(0, 7)}`"
  ...
>
```

- [ ] **Step 4: Run all frontend tests**

```bash
cd F:\github\Astrbot\dashboard
pnpm test -- --run
```

Expected: all tests pass (new + old)

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test(dashboard): add worktree switcher tests for composables and sidebar"
```

## Task 7.2: Backend tests (final test cases)

**Files:**
- Modify: `tests/test_git_worktrees.py` (add more test cases per spec Appendix A)
- Modify: `tests/test_git_diff.py` (add Chinese-path regression tests)

- [ ] **Step 1: Add the rest of test cases from spec Appendix A**

For `test_git_worktrees.py`, add:
- `test_returns_multiple_worktrees` (creates wt2 via `git worktree add`)
- `test_detached_worktree_branch_is_none`
- `test_marks_main_worktree_correctly`
- `test_chinese_path_in_worktree_works` (utf-8 regression — repo under 中文 dir)
- `test_no_project_loaded`
- `test_directory_missing`
- `test_not_a_git_repo`
- `test_git_unavailable`
- `test_git_error`
- `test_feature_disabled`
- `test_umo_fallback_to_most_recent`
- `test_porcelain_parser_handles_malformed`

For `test_git_diff.py`, add to `TestGitDiffWithWorktree`:
- `test_empty_worktree_param_uses_primary`
- `test_whitespace_worktree_param_uses_primary`
- `test_worktree_path_invalid_dotdot`
- `test_worktree_path_invalid_too_long`
- `test_worktree_path_unsafe_blacklisted`
- `test_worktree_missing`
- `test_worktree_not_in_repo`
- `test_chinese_path_worktree_diff` (utf-8 regression)
- `test_does_not_modify_loaded_projects` (Q1=A invariant)

- [ ] **Step 2: Run all backend tests**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
pytest -v
```

Expected: all pass (existing + new)

- [ ] **Step 3: Run ruff**

```bash
ruff check main.py tools/_helpers.py tests/
ruff format --check main.py tools/_helpers.py tests/
```

Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test(spcode): complete worktree switcher test coverage"
```

## Task 7.3: Documentation

**Files:**
- Modify: `F:\github\Astrbot\dashboard\AGENTS.md` (new section)
- Modify: `F:\github\astrbot_plugin_spcode_toolkit\README.md` (API table)

- [ ] **Step 1: Add Worktree Switcher section to `dashboard/AGENTS.md`**

Find a good insertion point (after the existing "## GitDiffSidebar" section if any) and add:

```markdown
## Worktree Switcher

The GitDiffSidebar supports switching between git worktrees of the same
project. Implementation lives in 4 files:

- `src/composables/useSpcodeWorktrees.ts` — fetches the worktree list
- `src/composables/useSpcodeGitDiff.ts` — accepts an active worktree ref
- `src/components/chat/GitDiffSidebar.vue` — tabs UI + state management
- `src/composables/parseSpcodeGitDiff.ts` — `SpcodeWorktree` types

**Rules:**
- Default selection is the primary worktree (Q3=A in spec).
- Diff polling targets only the active worktree, not all (Q5=A).
- `useSpcodeWorktrees` does NOT watch umo internally — the sidebar
  already watches `spcodeStatus.status.value.directory` and triggers
  refresh. Adding a watch here would cause double-fetch.
- Tabs use `flex-wrap: wrap` and `max-height: 30%` to handle many
  worktrees without overflowing the sidebar.

**Spec:** `astrbot_plugin_spcode_toolkit/docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md`
```

- [ ] **Step 2: Add worktree endpoints to spcode `README.md` API table**

Find the existing API table (search for `/spcode/git-diff` in `README.md`) and add 1-2 rows:

```markdown
| `/spcode/git-worktrees` | `GET` | List git worktrees for the loaded project's primary directory |
| `/spcode/git-diff` | `GET` | Project diff. Add `?worktree=<path>` to scope to a specific worktree (validated via 6-step defense; spec §2.4) |
```

- [ ] **Step 3: Commit both repos**

```bash
# spcode repo
cd F:\github\astrbot_plugin_spcode_toolkit
git add README.md
git commit -m "docs(spcode): document /spcode/git-worktrees and ?worktree on git-diff"

# dashboard repo
cd F:\github\Astrbot\dashboard
git add AGENTS.md
git commit -m "docs(dashboard): document Worktree Switcher"
```

- [ ] **Step 4: Final cross-repo integration verification**

1. **Backend integration**: 
   - From `F:\github\astrbot_plugin_spcode_toolkit`, run `pytest -v` → all pass
   - Run `ruff check .` and `ruff format --check .` → 0 errors
2. **Frontend integration**:
   - From `F:\github\Astrbot\dashboard`, run `pnpm test -- --run` → all pass
   - Run `pnpm tsc --noEmit` → 0 errors
   - Run `pnpm dev` → manually verify the smoke test from Chunk 6 step 7
3. **Cross-repo end-to-end** (if you have a runtime with multiple worktrees):
   - Restart AstrBot (loads updated spcode plugin)
   - Open dashboard, open GitDiffSidebar
   - Verify tabs appear + switching works

- [ ] **Step 5: Tag a release in spcode plugin (optional, ask user)**

If the user wants a release tag:

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
# Determine next version (current version is __main__.__version__ or metadata.json)
git tag -a v2.X.Y -m "feat: git worktree switcher"
```

(Skip this step unless the user explicitly requests a tagged release.)

- [ ] **Step 6: Final review gate**

Dispatch plan-document-reviewer for the entire plan (or just Chunk 7 if reviewing in isolation). Surface final report to user.

---

# Cross-Cutting Reminders

**TDD discipline**: For each task, write the failing test FIRST, run it to confirm it fails, then implement. Never write code without a failing test driving it.

**Frequent commits**: Each task ends with a commit. Each commit message follows conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).

**Reference the spec**: For any ambiguity, re-read `docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md`. Don't invent new behaviors not in the spec.

**Cross-repo coordination**: Backend (Chunks 1-3) can be implemented and merged independently. Frontend (Chunks 4-6) compiles but can't be runtime-tested without backend (404 fallback is OK). Chunk 7 is the integration step.

**Don't drift on core invariants** (spec §6.8):
1. `_loaded_projects` never modified by this feature
2. `handle_get_git_diff` inline `encoding="utf-8"` calls preserved (don't touch the module-level `_GIT_DIFF_ENCODING` constant)
3. Worktree list derived from primary's git dir
4. Polling targets only active worktree
5. Default = primary, no persistence
6. `_resolve_git_common_dir` uses abs-path + normcase
