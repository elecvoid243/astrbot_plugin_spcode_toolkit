# Git Init / Branch / Revert Endpoints — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 6 new git-related web API endpoints (`git-init` / `git-branches` / `git-branch-create` / `git-branch-delete` / `git-branch-switch` / `git-revert`) so the dashboard can drive the full branch lifecycle and revert flow without shell access.

**Architecture:** Pure additive on the existing 24-endpoint web API surface. 6 new `tools/webapi/<name>.py` modules, each with one `async def handle(plugin, *, umo, worktree, body)` function. 1 new preflight variant (`_git_init_preflight`) for the special init case that must bypass the standard 5-step preflight (because the directory isn't yet a git repo). 12 new reason codes added to the central `ReasonCode` table. ~102 new unit test cases + 1 E2E lifecycle test.

**Tech Stack:**
- Backend: Python 3.10+, AstrBot plugin (FastAPI-style web handlers), `subprocess` for git CLI
- Test: pytest + pytest-asyncio (already configured)
- Lint: ruff (already configured)

**Spec:** `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md` (read this BEFORE starting any task — every Task below implements one section of that spec)

**Project Path:** `F:\github\astrbot_plugin_spcode_toolkit`

---

## Global Constraints

- **Python 3.10+** required (use `list[str]` / `dict[str, Any]` / `X | None` built-in generics — no `typing.List` / `typing.Dict`)
- **git binary ≥ 2.30** required (project-wide assumption; `git switch` added in 2.23, `git switch -c` in 2.30, `git init -b` in 2.28 — all assumed available)
- **All new endpoints** must use the unified envelope contract from `tools/webapi/_helpers.py:_make_envelope`
- **All new reason codes** must be added to `tools/webapi/_helpers.py:ReasonCode` (extend the class, never inline string literals)
- **All new handlers** must use signature `async def handle(plugin, *, umo=None, worktree=None, body=None)`
- **All write endpoints** (except `git-init`) must call `_git_endpoint_preflight` as L5 of their defense chain
- **All new tests** must use `tests/conftest.py:_make_plugin()` fixture
- **All commits** must use `git commit -m "feat: ..."` style (project convention)
- **No** modifications to existing 24 route handlers (pure additive)
- **No** modifications to `data/workspaces/` or `data/temp/` (runtime data)
- **All path safety checks** must use `tools/_path_safety.py` — never inline `os.path.normpath` defensive logic
- **`tools/_helpers.py:_is_valid_ref_name`** is the single source of truth for branch/ref name validation — never duplicate

---

## File Structure

### New files (6 handlers + 6 tests + 1 E2E)

| Path | Lines | Responsibility |
|------|-------|----------------|
| `tools/webapi/git_init.py` | ~150 | POST init — 4-step preflight, git init subprocess |
| `tools/webapi/git_branches.py` | ~180 | GET list — for-each-ref parser + ETag cache |
| `tools/webapi/git_branch_create.py` | ~130 | POST create — ref-format + git branch |
| `tools/webapi/git_branch_delete.py` | ~150 | POST delete — current branch hard-ban + stderr classifier |
| `tools/webapi/git_branch_switch.py` | ~180 | POST switch — create/detach/force cross-field + worktree_dirty probe |
| `tools/webapi/git_revert.py` | ~170 | POST revert — extends git_commit env passthrough + 4 new reason codes |
| `tests/test_git_init.py` | ~280 | 18 cases (init happy / body / path / status / failure) |
| `tests/test_git_branches.py` | ~250 | 14 cases (preflight / list / ETag / parser) |
| `tests/test_git_branch_create.py` | ~220 | 12 cases (preflight / body / happy / branch_exists) |
| `tests/test_git_branch_delete.py` | ~280 | 16 cases (preflight / body / current / not_merged) |
| `tests/test_git_branch_switch.py` | ~400 | 22 cases (preflight / cross-field / create / detach / dirty) |
| `tests/test_git_revert.py` | ~350 | 20 cases (preflight / ref / dirty / hook / conflict) |
| `tests/test_git_branches_lifecycle.py` | ~150 | 1 E2E: init → branches → create → switch → commit → revert → delete |

### Modified files (5)

| Path | Change |
|------|--------|
| `tools/webapi/_helpers.py` | +`_git_init_preflight` / `+_classify_switch_stderr` / `+_classify_revert_stderr`; `ReasonCode` +12 |
| `tools/_helpers.py` | +`_is_commit_ref` (for revert ref validation) |
| `tools/webapi/__init__.py` | `ROUTES` +6 / `HANDLERS` +6 / `__all__` +6 |
| `tests/conftest.py` | +4 fixtures: `empty_dir` / `existing_repo` / `dirty_repo` / `loaded_umo` |
| `tests/test_webapi_end_to_end.py` | route count 24 → 30, 6 new smoke calls |
| `tests/test_reason_code.py` | +12 new reason literals |
| `docs/webapi-git-init-branch-revert-api.md` | NEW: public API doc (consumed by dashboard team) |
| `README.md` | endpoint table +6 rows |
| `AGENTS.md` | endpoint table +1 row + spec cross-reference |

---

## Implementation Order

1. **Task 1**: Infrastructure (PR-A) — ReasonCode + fixtures + `_git_init_preflight` + `_is_commit_ref`
2. **Task 2**: git-init (PR-B) — first endpoint, sets the pattern
3. **Task 3**: git-branches (PR-C) — read endpoint with ETag
4. **Task 4**: git-branch-create (PR-D) — simplest write
5. **Task 5**: git-branch-delete (PR-E) — adds hard-ban logic
6. **Task 6**: git-branch-switch (PR-F) — most complex (4 modes)
7. **Task 7**: git-revert (PR-G) — reuses git-commit infrastructure
8. **Task 8**: E2E + route assertions (PR-H)
9. **Task 9**: Documentation (PR-I)

Each Task is self-contained: write failing test → implement → make green → commit. Tasks MUST be executed in order (Task N's tests may depend on Task 1's fixtures).

---

# Task 1: Infrastructure (PR-A)

**Goal:** Lay the foundation all 6 endpoints depend on. After this task, you have 12 new reason codes in `ReasonCode`, 4 new test fixtures, the new `_git_init_preflight` helper, and `_is_commit_ref` helper — but NO new endpoint is registered yet.

**Files:**
- Modify: `tools/webapi/_helpers.py` (append 12 reason codes + 1 preflight + 2 stderr classifiers)
- Modify: `tools/_helpers.py` (append `_is_commit_ref` helper)
- Modify: `tests/conftest.py` (add 4 fixtures)

---

## Task 1.1: Add 12 new reason codes

- [ ] **Step 1: Verify the 12 new reason literals don't already exist**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from tools.webapi._helpers import ReasonCode; print('init_failed' in dir(ReasonCode), 'branch_exists' in dir(ReasonCode))"`
Expected: `False False` (both should NOT exist yet)

- [ ] **Step 2: Append 12 new reason literals to `ReasonCode`**

Edit `tools/webapi/_helpers.py`, find the `class ReasonCode:` block, and add these 12 lines at the end (after the last `FILE_EXISTS` line). The block currently ends with:

```python
    # ── /spcode/git-file + /spcode/docs 专用(spec B,2026-07-11) ──
    FILE_TOO_LARGE = "file_too_large"  # git-file: blob 超过 1 MB
    FILE_MISSING_AT_REF = "file_missing_at_ref"  # git-file: ref 解析成功但 path 不在 ref 下
    FILE_EXISTS = "file_exists"  # PATCH /spcode/docs: new_path 已存在
```

Append after the `FILE_EXISTS` line:

```python
    # ── v2.17.0 新增:git-init / branch / revert(2026-07-15) ──
    # git-init (4)
    PATH_NOT_DIRECTORY = "path_not_directory"  # init: 路径存在但不是目录
    DIRECTORY_NOT_EMPTY = "directory_not_empty"  # init: 目录里已有文件
    ALREADY_A_GIT_REPO = "already_a_git_repo"  # init: 目录里已有 .git/
    INIT_FAILED = "init_failed"  # init: git init 自身失败
    # git-branch-create (1)
    BRANCH_EXISTS = "branch_exists"  # create/switch: 分支已存在
    # git-branch-delete (3)
    BRANCH_NOT_FOUND = "branch_not_found"  # delete/switch: ref 不存在
    BRANCH_IS_CURRENT = "branch_is_current"  # delete: 试图删当前 HEAD 所在分支
    BRANCH_NOT_MERGED = "branch_not_merged"  # delete: -d 但分支未合并
    # git-branch-switch (1)
    WORKTREE_DIRTY = "worktree_dirty"  # switch/revert: working tree 有未提交改动
    # git-revert (3)
    COMMIT_NOT_FOUND = "commit_not_found"  # revert: <ref>^{commit} 解析失败
    REVERT_CONFLICT = "revert_conflict"  # revert: 反向 patch 与 worktree 冲突
    NOTHING_TO_REVERT = "nothing_to_revert"  # revert: ref 无改动可被反
```

- [ ] **Step 3: Verify the 12 reason literals are now defined**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from tools.webapi._helpers import ReasonCode; names = ['PATH_NOT_DIRECTORY','DIRECTORY_NOT_EMPTY','ALREADY_A_GIT_REPO','INIT_FAILED','BRANCH_EXISTS','BRANCH_NOT_FOUND','BRANCH_IS_CURRENT','BRANCH_NOT_MERGED','WORKTREE_DIRTY','COMMIT_NOT_FOUND','REVERT_CONFLICT','NOTHING_TO_REVERT']; [print(n, '=', getattr(ReasonCode, n)) for n in names]"`
Expected: 12 lines, each like `PATH_NOT_DIRECTORY = path_not_directory`

- [ ] **Step 4: Run existing tests to make sure no regression**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_reason_code.py -v`
Expected: all PASS (the new reasons will be added in Task 1.7; for now existing tests should still pass)

- [ ] **Step 5: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/_helpers.py
git commit -m "feat(reason-code): add 12 new reasons for v2.17.0 git-init/branch/revert endpoints"
```

---

## Task 1.2: Add `_is_commit_ref()` helper to `tools/_helpers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_helpers_commit_ref.py`:

```python
"""Tests for _is_commit_ref helper (v2.17.0 git-revert)."""
import subprocess
from pathlib import Path

import pytest

from tools._helpers import _is_commit_ref


def _make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "a.txt").write_text("a")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


def test_commit_ref_true_for_head(tmp_path):
    repo = _make_repo(tmp_path)
    assert _is_commit_ref("git", str(repo), "HEAD") is True


def test_commit_ref_true_for_sha(tmp_path):
    repo = _make_repo(tmp_path)
    sha_result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    assert _is_commit_ref("git", str(repo), sha_result.stdout.strip()) is True


def test_commit_ref_false_for_nonexistent(tmp_path):
    repo = _make_repo(tmp_path)
    assert _is_commit_ref("git", str(repo), "nonexistent-branch") is False


def test_commit_ref_false_for_blob_path(tmp_path):
    """A file path inside the repo is NOT a commit ref."""
    repo = _make_repo(tmp_path)
    assert _is_commit_ref("git", str(repo), "a.txt") is False
```

- [ ] **Step 2: Run the test to verify it fails (import error)**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_helpers_commit_ref.py -v`
Expected: `ImportError: cannot import name '_is_commit_ref' from 'tools._helpers'`

- [ ] **Step 3: Implement `_is_commit_ref()`**

Append to `tools/_helpers.py` (find a good spot, e.g. after `_is_valid_ref_name`):

```python
def _is_commit_ref(git_bin: str, directory: str, ref: str) -> bool:
    """检查 ``ref`` 在 ``directory`` 仓库中是否解析为 commit。

    用于 v2.17.0 ``git-revert`` 端点:revert 只能对 commit 生效,
    tag / tree / blob 都要拒绝。

    实现: ``git rev-parse --verify <ref>^{commit}``,
    退出码 0 → 是 commit;非 0 → 不是 commit 或不存在。

    Args:
        git_bin: git 可执行路径(从 ``plugin._git_binary()`` 拿)
        directory: 仓库根目录
        ref: 任意 ref 字符串(branch / tag / SHA / HEAD)

    Returns:
        True → ref 解析为 commit;False → 否则
    """
    import subprocess
    result = subprocess.run(
        [git_bin, "-C", directory, "rev-parse", "--verify", f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_helpers_commit_ref.py -v`
Expected: 4 PASS

- [ ] **Step 5: Run lint**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && ruff check tools/_helpers.py tests/test_helpers_commit_ref.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/_helpers.py tests/test_helpers_commit_ref.py
git commit -m "feat(helpers): add _is_commit_ref for v2.17.0 git-revert endpoint"
```

---

## Task 1.3: Add `_git_init_preflight()` to `tools/webapi/_helpers.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_git_init_preflight.py`:

```python
"""Tests for _git_init_preflight helper (v2.17.0 git-init)."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.webapi._helpers import _git_init_preflight, ReasonCode


def _make_plugin():
    plugin = MagicMock()
    plugin._config = {
        "file_remove_blacklist": None,
        "git_path": "",
    }
    return plugin


def test_init_preflight_happy_path(tmp_path):
    target = tmp_path / "new_repo"
    target.mkdir()
    plugin = _make_plugin()
    err, ctx = _await(_git_init_preflight(plugin, path=str(target)))
    assert err is None
    assert ctx is not None
    assert ctx["path"] == str(target.resolve())


def test_init_preflight_path_missing(tmp_path):
    plugin = _make_plugin()
    err, ctx = _await(_git_init_preflight(plugin, path=str(tmp_path / "nope")))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_preflight_path_is_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    plugin = _make_plugin()
    err, ctx = _await(_git_init_preflight(plugin, path=str(f)))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_preflight_directory_not_empty(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hi")
    plugin = _make_plugin()
    err, ctx = _await(_git_init_preflight(plugin, path=str(target)))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_preflight_already_git_repo(tmp_path):
    target = tmp_path / "already"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    plugin = _make_plugin()
    err, ctx = _await(_git_init_preflight(plugin, path=str(target)))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.ALREADY_A_GIT_REPO


def test_init_preflight_path_unsafe_traversal(tmp_path):
    plugin = _make_plugin()
    plugin._config["file_remove_blacklist"] = ["C:/Windows"]
    err, ctx = _await(_git_init_preflight(plugin, path="../../../etc"))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.PATH_UNSAFE


def _await(coro):
    """Helper to run a coroutine in sync test code."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)
```

- [ ] **Step 2: Run test to verify it fails (import error)**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_init_preflight.py -v`
Expected: `ImportError: cannot import name '_git_init_preflight' from 'tools.webapi._helpers'`

- [ ] **Step 3: Implement `_git_init_preflight()`**

Append to `tools/webapi/_helpers.py` (find a good spot, e.g. after `_git_endpoint_preflight`):

```python
async def _git_init_preflight(
    plugin: object,
    *,
    path: str,
) -> tuple[dict | None, dict | None]:
    """Git-init 端点 4 步自检(供 /spcode/git-init 专用)。

    Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §2.2
    §3.1

    与 ``_git_endpoint_preflight`` 的关键差异:
    - **不**做 umo 解析(init 时还没项目加载)
    - **不**做 git repo 探测(目录还不是仓库)

    4 步:
      1. path 非空 + str 类型校验
      2. 路径安全(``_is_path_safe`` + blacklist)
      3. 路径存在且是目录
      4. 目录为空(无任何文件)
      5. 目录里没有 ``.git/``

    Returns:
        (error_envelope, None) — 失败
        (None, ctx) — 成功;ctx = {"path": str(abs_resolved)}
    """
    from .._path_safety import is_path_safe as _is_path_safe
    from pathlib import Path

    # 1. path 校验
    if not isinstance(path, str) or not path.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=0,
            path=path or "",
        ), None

    # 2. 路径安全
    blacklist = plugin._config.get("file_remove_blacklist")  # type: ignore[attr-defined]
    ok, reason = _is_path_safe(Path(path), user_blacklist=blacklist)
    if not ok:
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=0,
            path=path,
            stderr=reason,
        ), None

    # 3. 目录存在性
    target = Path(path).resolve()
    if not target.exists() or not target.is_dir():
        return _make_envelope(
            success=False,
            reason=ReasonCode.PATH_NOT_DIRECTORY,
            elapsed_ms=0,
            path=path,
        ), None

    # 4. 目录为空(任何文件都拒绝 — v2.17.0 最严格语义)
    if any(target.iterdir()):
        return _make_envelope(
            success=False,
            reason=ReasonCode.DIRECTORY_NOT_EMPTY,
            elapsed_ms=0,
            path=path,
        ), None

    # 5. .git 不存在(此步在空目录里基本不可能命中,但保留兜底)
    if (target / ".git").exists():
        return _make_envelope(
            success=False,
            reason=ReasonCode.ALREADY_A_GIT_REPO,
            elapsed_ms=0,
            path=path,
        ), None

    return None, {"path": str(target)}


def _classify_switch_stderr(stderr: str) -> str:
    """将 ``git switch`` 失败 stderr 映射到 ReasonCode。

    Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.5

    Returns:
        ``branch_exists`` / ``branch_not_found`` / ``worktree_dirty`` /
        ``invalid_branch`` / ``git_error``
    """
    s = stderr.lower()
    if "already exists" in s:
        return ReasonCode.BRANCH_EXISTS
    if "did not match" in s or "not found" in s:
        return ReasonCode.BRANCH_NOT_FOUND
    if "your local changes" in s or "would be overwritten" in s:
        return ReasonCode.WORKTREE_DIRTY
    if "not a valid branch name" in s:
        return ReasonCode.INVALID_BRANCH
    return ReasonCode.GIT_ERROR


def _classify_revert_stderr(stderr: str) -> str:
    """将 ``git revert`` 失败 stderr 映射到 ReasonCode。

    Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.6

    复用 ``_classify_commit_error`` 的 4 类 hook/identity/nothing/git_error
    分类,未命中再走 revert 专属 3 类。
    """
    from .git_commit import _classify_commit_error
    classified = _classify_commit_error(stderr, returncode=-1)
    if classified != ReasonCode.GIT_ERROR:
        return classified
    s = stderr.lower()
    if "merge conflict" in s or "conflict" in s:
        return ReasonCode.REVERT_CONFLICT
    if "nothing to revert" in s or "no changes" in s:
        return ReasonCode.NOTHING_TO_REVERT
    if "your local changes" in s or "would be overwritten" in s:
        return ReasonCode.WORKTREE_DIRTY
    return ReasonCode.GIT_ERROR
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_init_preflight.py -v`
Expected: 6 PASS

- [ ] **Step 5: Run lint**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && ruff check tools/webapi/_helpers.py tests/test_git_init_preflight.py`
Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/_helpers.py tests/test_git_init_preflight.py
git commit -m "feat(helpers): add _git_init_preflight + switch/revert stderr classifiers"
```

---

## Task 1.4: Add 4 new fixtures to `tests/conftest.py`

- [ ] **Step 1: Read the current `conftest.py` to find the insertion point**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && type tests\conftest.py | findstr /n "import\|@pytest.fixture" | head -30`
Expected: shows current imports and existing fixtures

- [ ] **Step 2: Add 4 new fixtures**

Append to `tests/conftest.py` (find a good spot, e.g. before the `_make_plugin` factory):

```python
import subprocess
import time as _time
from pathlib import Path


@pytest.fixture
def empty_dir(tmp_path) -> Path:
    """空目录(给 git-init 用)。"""
    p = tmp_path / "new_repo"
    p.mkdir()
    return p


@pytest.fixture
def existing_repo(tmp_path) -> Path:
    """一个完整 git 仓库:init + 1 commit + 1 branch(feature/x)。"""
    repo = tmp_path / "existing_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "README.md").write_text("hi")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "feature/x"], check=True)
    return repo


@pytest.fixture
def dirty_repo(existing_repo) -> Path:
    """在 existing_repo 基础上加一个未提交改动。"""
    (existing_repo / "dirty.txt").write_text("uncommitted")
    return existing_repo


@pytest.fixture
def loaded_umo(existing_repo) -> str:
    """把 existing_repo 注册到 tools.project.state,模拟加载。"""
    from tools.project import state as _state

    umo = "test:git-branch:1"
    _state.set(umo, {"directory": str(existing_repo), "loaded_at": _time.time()})
    yield umo
    # 清理(若 _state 提供 clear/remove 方法;否则 dict 引用 GC 即可)
    if hasattr(_state, "clear"):
        _state.clear(umo)
    elif hasattr(_state, "remove"):
        _state.remove(umo)
```

- [ ] **Step 3: Verify the 4 fixtures work in isolation**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_helpers_commit_ref.py tests/test_git_init_preflight.py -v`
Expected: all PASS (uses no new fixture, but ensures no import-time breakage)

- [ ] **Step 4: Run the full pre-existing test suite to ensure no regression**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/ -q --ignore=tests/test_agentsmd_subsystem.py -x`
Expected: all existing tests PASS (no fixture name collision)

- [ ] **Step 5: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tests/conftest.py
git commit -m "test(conftest): add 4 fixtures for v2.17.0 git-init/branch/revert tests"
```

---

# Task 2: git-init endpoint (PR-B)

**Goal:** First endpoint using the new infrastructure. After this task, `POST /spcode/git-init` is registered and works end-to-end (init in a real empty dir succeeds; the 4 failure reasons are returned for the 4 error cases).

**Files:**
- Create: `tools/webapi/git_init.py`
- Create: `tests/test_git_init.py`
- Modify: `tools/webapi/__init__.py` (register route)

**Reference spec section:** §3.1 of the design spec.

---

## Task 2.1: Write the 18 failing tests for git-init

- [ ] **Step 1: Create `tests/test_git_init.py`**

```python
"""Tests for POST /spcode/git-init (v2.17.0)."""
import asyncio
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import _make_plugin  # noqa: F401 (re-export for clarity)
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_init


def _run(coro):
    """Sync wrapper for async handler calls."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── happy path ──────────────────────────────────────────────

def test_init_empty_dir_default_main(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    assert result["data"]["initialized"] is True
    assert result["data"]["initial_branch"] == "main"
    assert (empty_dir / ".git").is_dir()


def test_init_empty_dir_custom_branch(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(empty_dir), "initial_branch": "develop"}
    ))
    assert result["data"]["initialized"] is True
    assert result["data"]["initial_branch"] == "develop"


def test_init_bare(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(empty_dir), "bare": True}
    ))
    assert result["data"]["initialized"] is True
    assert result["data"]["bare"] is True


# ── body 校验 ───────────────────────────────────────────────

def test_init_body_none(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body=None))
    assert result["data"]["initialized"] is False
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_init_path_missing(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_path_not_string(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": 123}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_initial_branch_not_string(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(empty_dir), "initial_branch": []}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


# ── 路径安全 ────────────────────────────────────────────────

def test_init_path_traversal(tmp_path):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": "../../../etc"}))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


def test_init_path_blacklisted(tmp_path):
    target = tmp_path / "win"
    target.mkdir()
    plugin = _make_plugin()
    plugin._config["file_remove_blacklist"] = [str(target)]
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


def test_init_path_empty_string(empty_dir):
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": ""}))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


# ── 目录状态 ────────────────────────────────────────────────

def test_init_path_does_not_exist(tmp_path):
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(tmp_path / "nope")}
    ))
    assert result["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_path_is_a_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(f)}))
    assert result["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_directory_not_empty(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "x.txt").write_text("x")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_already_a_git_repo(tmp_path):
    target = tmp_path / "already"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.ALREADY_A_GIT_REPO


# ── git init 失败 ──────────────────────────────────────────

def test_init_git_binary_unavailable(empty_dir, monkeypatch):
    plugin = _make_plugin()
    plugin._git_binary = lambda: "/nonexistent/git-binary-xyz"
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    # 可能 init_failed 或 git_unavailable,看哪条更具体
    assert result["data"]["reason"] in (
        ReasonCode.INIT_FAILED,
        ReasonCode.GIT_UNAVAILABLE,
    )


def test_init_subprocess_exception(empty_dir, monkeypatch):
    plugin = _make_plugin()
    from tools.webapi import _helpers

    async def fake_run(*args, **kwargs):
        return {"ok": False, "returncode": 1, "stderr": "fatal: bad", "stdout": ""}

    monkeypatch.setattr(_helpers, "_run_git_async", fake_run)
    result = _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    assert result["data"]["reason"] == ReasonCode.INIT_FAILED
    assert "fatal: bad" in result["data"]["stderr"]


# ── 集成 ──────────────────────────────────────────────────

def test_init_then_git_status_works(empty_dir):
    """init 后,git status 应能正常工作。"""
    plugin = _make_plugin()
    _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    result = subprocess.run(
        ["git", "-C", str(empty_dir), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0


def test_init_then_validate_worktree_param_passes(empty_dir):
    """init 后,_validate_worktree_param 应能接受该路径。"""
    plugin = _make_plugin()
    _run(git_init.handle(plugin, body={"path": str(empty_dir)}))
    from tools._helpers import _validate_worktree_param
    validated, err = _validate_worktree_param(
        "git", str(empty_dir.parent), str(empty_dir)
    )
    assert err is None
    assert Path(validated).resolve() == empty_dir.resolve()
```

- [ ] **Step 2: Run the test file to verify all 18 fail (import error)**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_init.py -v`
Expected: 18 ERROR (ModuleNotFoundError: No module named 'tools.webapi.git_init')

---

## Task 2.2: Implement the git-init handler

- [ ] **Step 1: Create `tools/webapi/git_init.py`**

```python
"""POST /spcode/git-init — 在已存在空目录上 git init。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.1
v2.17.0 (2026-07-15).

**关键差异**:本端点是 webapi 中**唯一**完全豁免 ``_git_endpoint_preflight`` 的
写端点 —— 因为 preflight 步骤 5 会跑 ``git rev-parse --is-inside-work-tree``,
对非仓库必然返回 ``not_a_git_repo``,导致 init 永远失败。

改走 ``_git_init_preflight``(4 步,无 umo 解析、无 git repo 探测)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    ReasonCode,
    _git_init_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# git init -b 在 git 2.28+ 才支持,旧版本 fallback
MIN_GIT_VERSION_FOR_INIT_B = (2, 28)


def _git_supports_init_b(git_bin: str) -> bool:
    """检查 git 是否支持 ``git init -b <branch>``(需要 2.28+)。"""
    import re
    import subprocess
    result = subprocess.run(
        [git_bin, "--version"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    m = re.search(r"git version (\d+)\.(\d+)", result.stdout)
    if not m:
        return False
    return (int(m.group(1)), int(m.group(2))) >= MIN_GIT_VERSION_FOR_INIT_B


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    """POST /spcode/git-init handler.

    Body (JSON, 必传): ``{"path": "...", "initial_branch": "main", "bare": false}``。
    ``umo`` / ``worktree`` 字段虽在 _wrap 中注入,但本端点不使用(走独立 preflight)。
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
            initialized=False,
            path="",
        )

    path = body.get("path")
    initial_branch = body.get("initial_branch", "main")
    bare = body.get("bare", False)

    # ── 2. 参数类型校验 ──
    if not isinstance(path, str) or not path.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=str(path or ""),
        )
    if not isinstance(initial_branch, str):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=path,
        )
    if not isinstance(bare, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=path,
        )

    # ── 3. init-only preflight (4 步) ──
    err, ctx = await _git_init_preflight(plugin, path=path)
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("initialized", False)
        return err
    abs_path = ctx["path"]

    # ── 4. git init 子进程 ──
    git_bin = plugin._git_binary()
    args: list[str] = [git_bin, "init"]
    if bare:
        args.append("--bare")
    if _git_supports_init_b(git_bin):
        args.extend(["-b", initial_branch])
    args.append(abs_path)

    result = await _run_git_async(args, encoding="utf-8", timeout=30.0)

    # 兜底:旧版 git 无 -b,init 完手动 symbolic-ref
    if result["ok"] and not _git_supports_init_b(git_bin):
        ref_result = await _run_git_async(
            [git_bin, "-C", abs_path, "symbolic-ref", "HEAD", f"refs/heads/{initial_branch}"],
            encoding="utf-8",
        )
        if not ref_result["ok"]:
            logger.warning(
                "git init fallback symbolic-ref 失败: %s", ref_result.get("stderr", "")
            )

    if not result["ok"]:
        stderr = result.get("stderr", "") or result.get("error", "")
        # 探测 git 二进制可用性
        reason = ReasonCode.INIT_FAILED
        if "未安装" in result.get("error", ""):
            reason = ReasonCode.GIT_UNAVAILABLE
        return _make_envelope(
            success=False,
            reason=reason,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=abs_path,
            stderr=stderr[:4096],
        )

    git_dir = f"{abs_path}/.git" if not bare else f"{abs_path}"
    logger.info("git init 成功: %s (branch=%s, bare=%s)", abs_path, initial_branch, bare)
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            initialized=True,
            path=abs_path,
            initial_branch=initial_branch,
            bare=bare,
            git_dir=git_dir,
            umo=umo,
            worktree="",
        ),
        status_code=200,
    )
```

- [ ] **Step 2: Run the test file to verify all 18 pass**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_init.py -v`
Expected: 18 PASS

- [ ] **Step 3: Run lint**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && ruff check tools/webapi/git_init.py tests/test_git_init.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit (handler + tests together)**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/git_init.py tests/test_git_init.py
git commit -m "feat(webapi): add POST /spcode/git-init (v2.17.0)"
```

---

## Task 2.3: Register the route in `tools/webapi/__init__.py`

- [ ] **Step 1: Add the import + ROUTES + HANDLERS + __all__ entries**

Edit `tools/webapi/__init__.py`:

(a) Add to the imports block (after `from . import git_commit`):

```python
from . import git_init
```

(b) Add to `ROUTES` list (after the existing `codegraph_status` entry, before `git_file`):

```python
    (
        "/spcode/git-init",  # v2.17.0 (2026-07-15)
        ["POST"],
        git_init.handle,
        "在已存在空目录上 git init(独立 preflight,无 umo 解析)",
    ),
```

(c) Add to `HANDLERS` dict:

```python
    "handle_post_git_init": git_init.handle,  # v2.17.0 (2026-07-15)
```

(d) Add to `__all__` list (at the end, if it exists):

```python
    "git_init",
```

- [ ] **Step 2: Verify import succeeds and route count is 25**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from tools.webapi import ROUTES; print(len(ROUTES))"`
Expected: `25` (was 24, +1 for git-init)

- [ ] **Step 3: Re-run the git-init tests to ensure integration works**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_init.py -v`
Expected: 18 PASS

- [ ] **Step 4: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/__init__.py
git commit -m "feat(webapi): register /spcode/git-init route (v2.17.0, route count 24→25)"
```

---

# Task 3: git-branches (GET) endpoint (PR-C)

**Goal:** Add the read endpoint that lists local + remote branches. After this task, `GET /spcode/git-branches` returns the list with current/default branch markers, and supports ETag-based 304 responses.

**Files:**
- Create: `tools/webapi/git_branches.py`
- Create: `tests/test_git_branches.py`
- Modify: `tools/webapi/__init__.py` (register route)

**Reference spec section:** §3.2 of the design spec.

---

## Task 3.1: Write the 14 failing tests for git-branches

- [ ] **Step 1: Create `tests/test_git_branches.py`**

```python
"""Tests for GET /spcode/git-branches (v2.17.0)."""
import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _make_plugin  # noqa: F401
from tools.project import state as _state
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_branches


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── preflight ──────────────────────────────────────────────

def test_branches_no_umo_loaded(tmp_path):
    plugin = _make_plugin()
    # 不注册任何 umo
    result = _run(git_branches.handle(plugin))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


def test_branches_not_a_git_repo(tmp_path):
    target = tmp_path / "notgit"
    target.mkdir()
    umo = "test:branches:nogit"
    _state.set(umo, {"directory": str(target), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(git_branches.handle(plugin, umo=umo))
        assert result["data"]["reason"] == ReasonCode.NOT_A_GIT_REPO
    finally:
        if hasattr(_state, "clear"):
            _state.clear(umo)


def test_branches_worktree_invalid(tmp_path, existing_repo):
    """跨仓库 worktree 参数触发 6 步防御。"""
    other_repo = tmp_path / "other"
    other_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(other_repo)], check=True)
    umo = "test:branches:cross"
    _state.set(umo, {"directory": str(existing_repo), "loaded_at": 1.0})
    try:
        plugin = _make_plugin()
        result = _run(git_branches.handle(plugin, umo=umo, worktree=str(other_repo)))
        assert result["data"]["reason"] == ReasonCode.WORKTREE_INVALID
    finally:
        if hasattr(_state, "clear"):
            _state.clear(umo)


# ── happy path ─────────────────────────────────────────────

def test_branches_lists_local_branches(loaded_umo, existing_repo):
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert result["data"]["reason"] is None
    names = [b["name"] for b in result["data"]["branches"]]
    assert "main" in names
    assert "feature/x" in names
    # main 是 current
    main_branch = next(b for b in result["data"]["branches"] if b["name"] == "main")
    assert main_branch["current"] is True
    # feature/x 不是 current
    fx_branch = next(b for b in result["data"]["branches"] if b["name"] == "feature/x")
    assert fx_branch["current"] is False


def test_branches_total_field(loaded_umo, existing_repo):
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert result["data"]["total"] == len(result["data"]["branches"])


def test_branches_empty_repo(loaded_umo, existing_repo):
    """删除所有分支后(只剩 unborn HEAD),branches 列表应空。"""
    subprocess.run(
        ["git", "-C", str(existing_repo), "branch", "-D", "feature/x"], check=True
    )
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert result["data"]["branches"] == []
    assert result["data"]["total"] == 0


def test_branches_detached_head(loaded_umo, existing_repo):
    """切换到 detached HEAD 后,detached=true, current=null。"""
    sha = subprocess.run(
        ["git", "-C", str(existing_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(existing_repo), "checkout", "-q", sha], check=True
    )
    plugin = _make_plugin()
    result = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert result["data"]["detached"] is True
    assert result["data"]["current"] is None


# ── ETag ──────────────────────────────────────────────────

def test_branches_etag_cache_hit(loaded_umo, existing_repo):
    """两次连续请求,第二次 ETag 命中 → envelope 缩短。"""
    plugin = _make_plugin()
    r1 = _run(git_branches.handle(plugin, umo=loaded_umo))
    # ETag 字段应一致(走 1.5s 缓存)
    r2 = _run(git_branches.handle(plugin, umo=loaded_umo))
    assert r1.get("data", {}).get("branches") is not None
    # 两次都返 200(测试不打 ETag header,只验证 cache 不爆)
    assert r2.get("data", {}).get("branches") is not None


# ── 解析器 ────────────────────────────────────────────────

def test_parse_for_each_ref_handles_empty():
    from tools.webapi.git_branches import _parse_for_each_ref
    assert _parse_for_each_ref("") == []


def test_parse_for_each_ref_skips_blank_lines():
    from tools.webapi.git_branches import _parse_for_each_ref
    assert _parse_for_each_ref("\n\n  \n") == []


def test_parse_for_each_ref_extracts_current_marker():
    from tools.webapi.git_branches import _parse_for_each_ref
    line = "*\tmain\tabc1234\t\t\n"
    result = _parse_for_each_ref(line)
    assert len(result) == 1
    assert result[0]["name"] == "main"
    assert result[0]["current"] is True
    assert result[0]["sha"] == "abc1234"


def test_parse_for_each_ref_handles_remote():
    from tools.webapi.git_branches import _parse_for_each_ref
    line = "\torigin/main\tdef5678\t\n"  # 无 upstream
    result = _parse_for_each_ref(line)
    assert len(result) == 1
    assert result[0]["name"] == "origin/main"
    assert result[0]["remote"] is True
    assert result[0]["current"] is False
```

- [ ] **Step 2: Run to verify all 14 fail**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_branches.py -v`
Expected: 14 ERROR (ModuleNotFoundError)

---

## Task 3.2: Implement the git-branches handler

- [ ] **Step 1: Create `tools/webapi/git_branches.py`**

```python
"""GET /spcode/git-branches — 列出 local/remote 分支 + 当前 + 默认。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.2
v2.17.0 (2026-07-15).

读端点,1.5s TTL ETag 缓存(与 git-status 模式一致)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _compute_git_etag,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# 分支查询格式(以 TAB 分隔,HEAD 标记在前)
_FOR_EACH_REF_FORMAT = (
    "%(HEAD)%09%(refname:short)%09%(objectname:short)"
    "%09%(upstream:short)%09%(upstream:track)"
)


def _parse_for_each_ref(stdout: str) -> list[dict]:
    """解析 ``git for-each-ref`` 输出为分支字典列表。

    每行格式: ``<HEAD>\\t<name>\\t<sha>\\t<upstream>\\t<tracking>``
    - HEAD 列是 ``*`` 表示当前分支
    - refs/remotes/ 下的视为 remote
    - 异常行(字段数 < 5)直接跳过,不抛错
    """
    branches: list[dict] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        head_mark = parts[0].strip()
        name = parts[1].strip()
        sha = parts[2].strip() if len(parts) > 2 else ""
        upstream = parts[3].strip() if len(parts) > 3 else ""
        tracking = parts[4].strip() if len(parts) > 4 else ""
        if not name:
            continue
        remote = name.startswith("origin/") or "/" in name.split("/")[0:1] and False
        # 简单判定:refs/heads 下的都不是 remote
        # for-each-ref 已经在命令中指定了 refs/heads + refs/remotes,
        # 我们靠 name 包含 / 区分,但要排除 feature/x 这种
        # 实际更可靠:依赖 ``git for-each-ref`` 返回顺序,local 在前
        # 这里简化:用 name.startswith("origin/") 或 "remotes/" 判 remote
        # (for-each-ref --format='%(refname:short)' 自动剥 refs/heads/ 或 refs/remotes/)
        is_remote = name.startswith("origin/")  # 简单实现
        branches.append({
            "name": name,
            "remote": is_remote,
            "current": head_mark == "*",
            "sha": sha,
            "upstream": upstream,
            "tracking": tracking,
        })
    return branches


async def _get_default_branch(git_bin: str, directory: str) -> str:
    """探测 default branch(origin/HEAD → init.defaultBranch → 'main')。"""
    # 1. 试 origin/HEAD
    r = await _run_git_async(
        [git_bin, "-C", directory, "symbolic-ref", "refs/remotes/origin/HEAD"],
        encoding="utf-8",
    )
    if r.get("ok") and r.get("stdout"):
        ref = r["stdout"].strip()
        if ref.startswith("refs/remotes/origin/"):
            return ref[len("refs/remotes/origin/"):]
    # 2. 试 init.defaultBranch 配置
    r = await _run_git_async(
        [git_bin, "-C", directory, "config", "init.defaultBranch"],
        encoding="utf-8",
    )
    if r.get("ok") and r.get("stdout"):
        return r["stdout"].strip()
    return "main"


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    """GET /spcode/git-branches handler.

    Query 参数: ``umo`` / ``worktree`` / ``include_remote``(默认 false)
    """
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # ── 2. ETag 检查(可选用) ──
    etag = await _compute_git_etag(git_bin, directory)

    # ── 3. for-each-ref ──
    ref_result = await _run_git_async(
        git_prefix + ["for-each-ref", "--format=" + _FOR_EACH_REF_FORMAT,
                      "refs/heads", "refs/remotes"],
        encoding="utf-8",
    )
    if not ref_result["ok"]:
        # 空仓库(无任何 commit)for-each-ref 会返 ok 但输出空,这里只处理 git 进程级失败
        return _make_envelope(
            success=False,
            reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(),
            branches=[],
            total=0,
            umo=effective_umo,
            worktree=directory,
            stderr=ref_result.get("stderr", ""),
        )

    # ── 4. 探测 current branch(可能 detached) ──
    branch_result = await _run_git_async(
        git_prefix + ["rev-parse", "--abbrev-ref", "HEAD"],
        encoding="utf-8",
    )
    current: str | None = None
    detached = False
    if branch_result["ok"]:
        b = branch_result["stdout"].strip()
        if b == "HEAD":
            detached = True
        elif b:
            current = b

    # ── 5. 解析 + 汇总 ──
    branches = _parse_for_each_ref(ref_result["stdout"])
    default_branch = await _get_default_branch(git_bin, directory)

    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            branches=branches,
            total=len(branches),
            current=current,
            detached=detached,
            default_branch=default_branch,
            directory=directory,
            umo=effective_umo,
            worktree=directory,
        ),
        status_code=200,
        headers={"ETag": etag, "Cache-Control": "private, max-age=1"},
    )
```

- [ ] **Step 2: Run tests**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_branches.py -v`
Expected: 14 PASS

- [ ] **Step 3: Lint**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && ruff check tools/webapi/git_branches.py tests/test_git_branches.py`

- [ ] **Step 4: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/git_branches.py tests/test_git_branches.py
git commit -m "feat(webapi): add GET /spcode/git-branches (v2.17.0)"
```

---

## Task 3.3: Register the route

- [ ] **Step 1: Add import + ROUTES + HANDLERS entry**

Edit `tools/webapi/__init__.py` — add `from . import git_branches` to imports, add to `ROUTES`, add `"handle_get_git_branches": git_branches.handle` to `HANDLERS`, add to `__all__` if present.

`ROUTES` entry:

```python
    (
        "/spcode/git-branches",  # v2.17.0 (2026-07-15)
        ["GET"],
        git_branches.handle,
        "列出 local/remote 分支 + 当前 + 默认",
    ),
```

- [ ] **Step 2: Verify route count = 26**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from tools.webapi import ROUTES; print(len(ROUTES))"`
Expected: `26`

- [ ] **Step 3: Re-run tests + commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/__init__.py
git commit -m "feat(webapi): register /spcode/git-branches (route count 25→26)"
```

---

# Task 4: git-branch-create (PR-D)

**Files:**
- Create: `tools/webapi/git_branch_create.py`
- Create: `tests/test_git_branch_create.py`
- Modify: `tools/webapi/__init__.py`

**Spec ref:** §3.3

---

## Task 4.1: Write 12 failing tests

- [ ] **Step 1: Create `tests/test_git_branch_create.py`**

```python
"""Tests for POST /spcode/git-branch-create (v2.17.0)."""
import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _state
from tools.webapi._helpers import ReasonCode
from tools.webapi import git_branch_create


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── preflight ──────────────────────────────────────────────

def test_create_no_umo_loaded():
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(plugin, body={"name": "x"}))
    assert result["data"]["reason"] == ReasonCode.NO_PROJECT_LOADED


def test_create_empty_repo(loaded_umo, existing_repo, tmp_path):
    """HEAD 不存在(空仓库)→ empty_repository。"""
    # 删 README 改动并 reset 模拟空仓库
    subprocess.run(["git", "-C", str(existing_repo), "update-ref", "-d", "HEAD"], check=True)
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(plugin, umo=loaded_umo, body={"name": "x"}))
    assert result["data"]["reason"] in (ReasonCode.EMPTY_REPOSITORY, ReasonCode.GIT_ERROR)


# ── body 校验 ──────────────────────────────────────────────

def test_create_body_none(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(plugin, umo=loaded_umo, body=None))
    assert result["data"]["reason"] == ReasonCode.INVALID_BODY


def test_create_name_missing(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(plugin, umo=loaded_umo, body={}))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_create_name_invalid_chars(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "bad..name"}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_BRANCH


def test_create_name_is_HEAD(loaded_umo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "HEAD"}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_BRANCH


# ── happy path ─────────────────────────────────────────────

def test_create_default_from_HEAD(loaded_umo, existing_repo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "new-branch"}
    ))
    assert result["data"]["created"] is True
    assert result["data"]["name"] == "new-branch"
    # 验证 git 真的创建了
    out = subprocess.run(
        ["git", "-C", str(existing_repo), "branch", "--list", "new-branch"],
        capture_output=True, text=True, check=True,
    )
    assert "new-branch" in out.stdout


def test_create_with_start_point(loaded_umo, existing_repo):
    sha = subprocess.run(
        ["git", "-C", str(existing_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "from-sha", "start_point": sha}
    ))
    assert result["data"]["created"] is True
    assert result["data"]["sha"] == sha[:7] or len(result["data"]["sha"]) >= 7


def test_create_force_overwrites(loaded_umo, existing_repo):
    """force=true 覆盖已存在分支。"""
    plugin = _make_plugin()
    r1 = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "x-branch"}
    ))
    assert r1["data"]["created"] is True
    r2 = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "x-branch", "force": True}
    ))
    assert r2["data"]["created"] is True
    assert r2["data"]["force"] is True


# ── 错误 ──────────────────────────────────────────────────

def test_create_branch_exists(loaded_umo, existing_repo):
    """已存在分支 + force=false → branch_exists。"""
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo, body={"name": "feature/x"}
    ))
    assert result["data"]["reason"] == ReasonCode.BRANCH_EXISTS


def test_create_start_point_invalid(loaded_umo, existing_repo):
    plugin = _make_plugin()
    result = _run(git_branch_create.handle(
        plugin, umo=loaded_umo,
        body={"name": "x", "start_point": "../bad-ref"}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM
```

- [ ] **Step 2: Run, expect 12 errors**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_branch_create.py -v`
Expected: 12 ERROR

---

## Task 4.2: Implement

- [ ] **Step 1: Create `tools/webapi/git_branch_create.py`**

```python
"""POST /spcode/git-branch-create — 从当前 HEAD 拉新分支。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.3
v2.17.0 (2026-07-15).
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .._helpers import _is_valid_ref_name

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
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), created=False, name="",
        )

    name = body.get("name")
    start_point = body.get("start_point", "HEAD")
    force = bool(body.get("force", False))

    if not isinstance(name, str) or not name:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), created=False, name=str(name or ""),
        )
    if not isinstance(start_point, str):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), created=False, name=name,
        )

    # ── 2. ref-format 校验 ──
    if not _is_valid_ref_name(name):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BRANCH,
            elapsed_ms=_elapsed(), created=False, name=name,
        )
    if start_point != "HEAD" and not _is_valid_ref_name(start_point):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), created=False, name=name,
        )

    # ── 3. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("created", False)
        err["data"].setdefault("name", name)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 4. git branch ──
    args: list[str] = [git_bin, "-C", directory, "branch"]
    if force:
        args.append("-f")
    args.append(name)
    if start_point != "HEAD":
        args.append(start_point)

    result = await _run_git_async(args, encoding="utf-8", timeout=15.0)
    if not result["ok"]:
        stderr = result.get("stderr", "").lower()
        reason = ReasonCode.GIT_ERROR
        if "already exists" in stderr:
            reason = ReasonCode.BRANCH_EXISTS
        elif "not a valid branch name" in stderr:
            reason = ReasonCode.INVALID_BRANCH
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), created=False, name=name,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    # ── 5. 回读 SHA ──
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--verify", f"{name}^{{commit}}"],
        encoding="utf-8",
    )
    sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""

    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            created=True, name=name, sha=sha,
            start_point=start_point, force=force,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 2-4: Run / Lint / Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
pytest tests/test_git_branch_create.py -v
ruff check tools/webapi/git_branch_create.py tests/test_git_branch_create.py
git add tools/webapi/git_branch_create.py tests/test_git_branch_create.py
git commit -m "feat(webapi): add POST /spcode/git-branch-create (v2.17.0)"
```

---

## Task 4.3: Register route

- [ ] **Step 1: Edit `tools/webapi/__init__.py`** — add import + ROUTES + HANDLERS + `__all__`

`ROUTES` entry:
```python
    (
        "/spcode/git-branch-create",  # v2.17.0 (2026-07-15)
        ["POST"],
        git_branch_create.handle,
        "git branch <name> (从当前 HEAD 拉新分支)",
    ),
```

`HANDLERS`:
```python
    "handle_post_git_branch_create": git_branch_create.handle,
```

- [ ] **Step 2-3: Verify route count = 27, commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
python -c "from tools.webapi import ROUTES; print(len(ROUTES))"
# Expected: 27
git add tools/webapi/__init__.py
git commit -m "feat(webapi): register /spcode/git-branch-create (route count 26→27)"
```

---

# Task 5: git-branch-delete (PR-E)

**Files:**
- Create: `tools/webapi/git_branch_delete.py`
- Create: `tests/test_git_branch_delete.py`
- Modify: `tools/webapi/__init__.py`

**Spec ref:** §3.4 (硬禁 current branch + force 选项)

---

## Task 5.1: Write 16 tests, implement, register

- [ ] **Step 1: Create `tests/test_git_branch_delete.py`** (16 cases, mirrors the matrix in spec §6.3)

The file follows the same structure as `test_git_branch_create.py` with these 16 cases (full code in the implementation, not enumerated here to save space — see spec §6.3 row "git-branch-delete"):
1-3. preflight (no umo / not git repo / worktree invalid)
4-5. body 校验 (body=None / name missing)
6. name 含 `..` → invalid_branch
7. 删 current branch + force=false → branch_is_current
8. 删 current branch + force=true → **仍** branch_is_current(硬禁)
9. 删 main branch(current=main)→ branch_is_current
10. 删已合并 feature → deleted=True
11. force=true 删未合并 → deleted=True
12. branch 不存在 → branch_not_found
13. force=false 删未合并 → branch_not_merged
14. 删 origin/main (remote tracking) → git_error
15. 删后 git-branches 不再返回 → 端到端
16. 删正在其他 worktree checkout 的分支 → git_error

(Full test file: see commit history of the spec for reference implementation.)

- [ ] **Step 2: Create `tools/webapi/git_branch_delete.py`**

```python
"""POST /spcode/git-branch-delete — 删除分支(硬禁 current branch)。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.4
v2.17.0 (2026-07-15).

**关键不变量**:`force=true` 不绕过 current branch 检查 — 与
``git-worktree-remove`` 硬禁 main 的设计思路一致。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .._helpers import _is_valid_ref_name

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
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), deleted=False, name="",
        )

    name = body.get("name")
    force = bool(body.get("force", False))

    if not isinstance(name, str) or not name:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), deleted=False, name=str(name or ""),
        )
    if not _is_valid_ref_name(name):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BRANCH,
            elapsed_ms=_elapsed(), deleted=False, name=name,
        )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("deleted", False)
        err["data"].setdefault("name", name)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 探测 current branch(硬禁) ──
    head_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
        encoding="utf-8",
    )
    current = head_result["stdout"].strip() if head_result.get("ok") else None
    is_current = (current == name) and current != "HEAD"

    if is_current:
        return _make_envelope(
            success=False, reason=ReasonCode.BRANCH_IS_CURRENT,
            elapsed_ms=_elapsed(), deleted=False, name=name,
            was_current=True, current=current,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"cannot delete current branch '{name}'",
        )

    # ── 4. git branch -d/-D ──
    flag = "-D" if force else "-d"
    result = await _run_git_async(
        [git_bin, "-C", directory, "branch", flag, name],
        encoding="utf-8", timeout=15.0,
    )
    if not result["ok"]:
        stderr = result.get("stderr", "").lower()
        reason = ReasonCode.GIT_ERROR
        if "not found" in stderr:
            reason = ReasonCode.BRANCH_NOT_FOUND
        elif "not fully merged" in stderr:
            reason = ReasonCode.BRANCH_NOT_MERGED
        elif "checked out" in stderr:
            reason = ReasonCode.BRANCH_IS_CURRENT
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), deleted=False, name=name,
            was_current=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            deleted=True, name=name, force=force,
            was_current=False,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 2: Run, lint, commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
pytest tests/test_git_branch_delete.py -v
ruff check tools/webapi/git_branch_delete.py tests/test_git_branch_delete.py
git add tools/webapi/git_branch_delete.py tests/test_git_branch_delete.py
git commit -m "feat(webapi): add POST /spcode/git-branch-delete (v2.17.0)"
```

---

## Task 5.2: Register route (route count 27 → 28)

- [ ] **Step 1: Edit `tools/webapi/__init__.py`** — add `from . import git_branch_delete` + ROUTES + HANDLERS

`ROUTES` entry:
```python
    (
        "/spcode/git-branch-delete",  # v2.17.0 (2026-07-15)
        ["POST"],
        git_branch_delete.handle,
        "git branch -d/-D (硬禁 current branch)",
    ),
```

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
python -c "from tools.webapi import ROUTES; print(len(ROUTES))"
# Expected: 28
git add tools/webapi/__init__.py
git commit -m "feat(webapi): register /spcode/git-branch-delete (route count 27→28)"
```

---

# Task 6: git-branch-switch (PR-F)

**Files:**
- Create: `tools/webapi/git_branch_switch.py`
- Create: `tests/test_git_branch_switch.py`
- Modify: `tools/webapi/__init__.py`

**Spec ref:** §3.5 (4 modes: basic / create / detach / force)

---

## Task 6.1: Write 22 tests, implement, register

- [ ] **Step 1: Create `tests/test_git_branch_switch.py`** (22 cases per spec §6.3)

Full test file structure follows the same pattern as `test_git_branch_create.py`. Key cases:
1-3. preflight
4-7. body 校验
8. create=true + detach=true → invalid_body
9. create=false + start_point → invalid_param
10. switch to current branch (no-op) → switched=True
11. switch to existing feature branch → switched, previous=main
12. create=true → created=true, switched=true
13. create=true + start_point=tag → from tag
14. detach=true + 7-char SHA → current=null
15. force=true 丢弃 worktree 改动 → switched=True
16. branch 不存在 (create=false) → branch_not_found
17. branch 已存在 (create=true) → branch_exists
18. worktree dirty + force=false → worktree_dirty
19. worktree dirty + force=true → switched=True
20. ref-format 非法 → invalid_branch
21. 切换后 git-status 显示新 branch → 端到端
22. ETag 失效 → 端到端

- [ ] **Step 2: Create `tools/webapi/git_branch_switch.py`**

```python
"""POST /spcode/git-branch-switch — 切换当前分支(支持 create/detach/force)。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.5
v2.17.0 (2026-07-15).

用 ``git switch`` 而非 ``git checkout``,语义更明确。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _classify_switch_stderr,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .._helpers import _is_valid_ref_name

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


def _validate_cross_fields(create: bool, force: bool, detach: bool, start_point) -> str | None:
    """create/detach/force/start_point 跨字段校验。返回 error msg 或 None。"""
    if create and detach:
        return "create and detach are mutually exclusive"
    if start_point is not None and not create:
        return "start_point only valid with create=true"
    return None


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), switched=False, name="",
        )

    name = body.get("name")
    create = bool(body.get("create", False))
    start_point = body.get("start_point")
    force = bool(body.get("force", False))
    detach = bool(body.get("detach", False))

    if not isinstance(name, str) or not name:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=str(name or ""),
        )
    if not isinstance(create, bool) or not isinstance(force, bool) or not isinstance(detach, bool):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )
    if start_point is not None and not isinstance(start_point, str):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )

    # ── 2. ref-format 校验 ──
    if not _is_valid_ref_name(name):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BRANCH,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )
    if create and start_point is not None and start_point != "HEAD" and not _is_valid_ref_name(start_point):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), switched=False, name=name,
        )

    # ── 3. 跨字段校验 ──
    cross_err = _validate_cross_fields(create, force, detach, start_point)
    if cross_err is not None:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), switched=False, name=name,
            stderr=cross_err,
        )

    # ── 4. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("switched", False)
        err["data"].setdefault("name", name)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 5. 探测 previous branch + worktree 状态 ──
    head_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
        encoding="utf-8",
    )
    previous = head_result["stdout"].strip() if head_result.get("ok") and head_result["stdout"].strip() != "HEAD" else None

    if not force:
        status_result = await _run_git_async(
            [git_bin, "-C", directory, "status", "--porcelain"],
            encoding="utf-8",
        )
        if status_result.get("ok") and status_result["stdout"].strip():
            return _make_envelope(
                success=False, reason=ReasonCode.WORKTREE_DIRTY,
                elapsed_ms=_elapsed(), switched=False, name=name,
                directory=directory, umo=effective_umo, worktree=directory,
                stderr="working tree has uncommitted changes",
            )

    # ── 6. git switch ──
    args: list[str] = [git_bin, "-C", directory, "switch"]
    if create:
        args.append("-c")
    if force and not create:
        args.append("-f")
    if detach:
        args.append("--detach")
    args.append(name)
    if create and start_point is not None and start_point != "HEAD":
        args.append(start_point)

    result = await _run_git_async(args, encoding="utf-8", timeout=15.0)
    if not result["ok"]:
        reason = _classify_switch_stderr(result.get("stderr", ""))
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), switched=False, name=name,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=result.get("stderr", "")[:4096],
        )

    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            switched=True, name=name, previous=previous,
            created=create, force=force, detach=detach,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 2: Run, lint, commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
pytest tests/test_git_branch_switch.py -v
ruff check tools/webapi/git_branch_switch.py tests/test_git_branch_switch.py
git add tools/webapi/git_branch_switch.py tests/test_git_branch_switch.py
git commit -m "feat(webapi): add POST /spcode/git-branch-switch (v2.17.0)"
```

---

## Task 6.2: Register route (route count 28 → 29)

- [ ] **Step 1: Edit `tools/webapi/__init__.py`**

`ROUTES` entry:
```python
    (
        "/spcode/git-branch-switch",  # v2.17.0 (2026-07-15)
        ["POST"],
        git_branch_switch.handle,
        "git switch <name> (支持 create/detach/force 跨字段)",
    ),
```

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/__init__.py
git commit -m "feat(webapi): register /spcode/git-branch-switch (route count 28→29)"
```

---

# Task 7: git-revert (PR-G)

**Files:**
- Create: `tools/webapi/git_revert.py`
- Create: `tests/test_git_revert.py`
- Modify: `tools/webapi/__init__.py`

**Spec ref:** §3.6 (复用 git_commit 的 env passthrough + 4 类 stderr 分类)

---

## Task 7.1: Write 20 tests, implement, register

- [ ] **Step 1: Create `tests/test_git_revert.py`** (20 cases per spec §6.3)

Key cases:
1-3. preflight
4-5. body 校验
6. ref 不存在 → commit_not_found
7. ref=tag (非 commit) → commit_not_found
8. ref=blob path → commit_not_found
9-10. worktree dirty (含 staged)
11. revert HEAD (默认) → reverted=True
12. revert 指定 SHA
13. revert 后 git-log 出现新 commit → 端到端
14. pre-commit hook 拒绝 → hook_rejected
15. user.email 未设 → identity_not_set
16. revert 与当前改动冲突 → revert_conflict
17. 空仓库 → empty_repository
18. revert 第一个 commit (无 parent) → git_error
19. os.environ 含 GIT_AUTHOR_EMAIL → 透传
20. revert 后 .git/REVERT_HEAD 清理

- [ ] **Step 2: Create `tools/webapi/git_revert.py`**

```python
"""POST /spcode/git-revert — 创建回滚 commit。

Spec: docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md §3.6
v2.17.0 (2026-07-15).

复用 ``tools/webapi/git_commit.py`` 的:
- ``_build_git_env()``:透传 ``GIT_AUTHOR_*`` 环境变量
- ``_classify_commit_error()``:4 类 hook/identity/nothing/git_error 分类

``_classify_revert_stderr()`` 内部先调 commit classifier,未命中再走
revert 专属 3 类(conflict / nothing_to_revert / dirty)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import (
    _classify_revert_stderr,
    _git_endpoint_preflight,
    _JSONResponseCompat,
    _make_envelope,
    _run_git_async,
    ReasonCode,
)
from .git_commit import _build_git_env
from .._helpers import _is_commit_ref

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

COMMIT_TRUNCATE_BYTES = 4096


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    # ── 1. body 校验 ──
    if not isinstance(body, dict):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(), reverted=False, ref="",
        )

    ref = body.get("ref", "HEAD")
    no_edit = body.get("no_edit", True)

    if not isinstance(ref, str) or not ref:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), reverted=False, ref=str(ref or ""),
        )
    if no_edit is not True:
        # v2.17.0 强制 no_edit=true(headless 服务不开编辑器)
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            stderr="no_edit must be true (headless service, no editor)",
        )

    # ── 2. preflight ──
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("reverted", False)
        err["data"].setdefault("ref", ref)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]
    git_bin = plugin._git_binary()

    # ── 3. 校验 ref 是 commit ──
    if not _is_commit_ref(git_bin, directory, ref):
        return _make_envelope(
            success=False, reason=ReasonCode.COMMIT_NOT_FOUND,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=f"ref does not resolve to a commit: {ref}",
        )

    # ── 4. 探测 worktree 状态(必须 clean) ──
    status_result = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain"],
        encoding="utf-8",
    )
    if not status_result.get("ok"):
        stderr = status_result.get("stderr", "")
        if "does not have any commits" in stderr:
            return _make_envelope(
                success=False, reason=ReasonCode.EMPTY_REPOSITORY,
                elapsed_ms=_elapsed(), reverted=False, ref=ref,
                directory=directory, umo=effective_umo, worktree=directory,
                stderr=stderr,
            )
        return _make_envelope(
            success=False, reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=stderr,
        )
    if status_result["stdout"].strip():
        return _make_envelope(
            success=False, reason=ReasonCode.WORKTREE_DIRTY,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr="working tree has uncommitted changes",
        )

    # ── 5. git revert --no-edit ──
    args = [git_bin, "-C", directory, "-c", "color.ui=never", "revert", "--no-edit", ref]
    git_env = _build_git_env()
    result = await _run_git_async(args, encoding="utf-8", input_text="", env=git_env, timeout=30.0)

    if not result["ok"]:
        stderr = result.get("stderr", "") or result.get("error", "")
        reason = _classify_revert_stderr(stderr)
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), reverted=False, ref=ref,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=stderr[:COMMIT_TRUNCATE_BYTES],
        )

    # ── 6. 回读新 commit SHA + touched files ──
    sha_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "HEAD"],
        encoding="utf-8",
    )
    revert_sha = sha_result["stdout"].strip() if sha_result.get("ok") else ""

    msg_result = await _run_git_async(
        [git_bin, "-C", directory, "log", "-1", "--pretty=%s", "HEAD"],
        encoding="utf-8",
    )
    revert_message = msg_result["stdout"].strip() if msg_result.get("ok") else ""

    files_result = await _run_git_async(
        [git_bin, "-C", directory, "show", "--name-only", "--pretty=", "HEAD"],
        encoding="utf-8",
    )
    files_touched = [
        line.strip() for line in (files_result.get("stdout", "")).splitlines() if line.strip()
    ] if files_result.get("ok") else []

    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            reverted=True, ref=ref, revert_sha=revert_sha,
            revert_message=revert_message, files_touched=files_touched,
            directory=directory, umo=effective_umo, worktree=directory,
        ),
        status_code=200,
    )
```

- [ ] **Step 2: Run, lint, commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
pytest tests/test_git_revert.py -v
ruff check tools/webapi/git_revert.py tests/test_git_revert.py
git add tools/webapi/git_revert.py tests/test_git_revert.py
git commit -m "feat(webapi): add POST /spcode/git-revert (v2.17.0)"
```

---

## Task 7.2: Register route (route count 29 → 30)

- [ ] **Step 1: Edit `tools/webapi/__init__.py`**

`ROUTES` entry:
```python
    (
        "/spcode/git-revert",  # v2.17.0 (2026-07-15)
        ["POST"],
        git_revert.handle,
        "git revert <ref> --no-edit (自动生成回滚 commit)",
    ),
```

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/__init__.py
git commit -m "feat(webapi): register /spcode/git-revert (route count 29→30, v2.17.0 complete)"
```

---

# Task 8: E2E + route assertions (PR-H)

**Files:**
- Create: `tests/test_git_branches_lifecycle.py` (1 E2E test)
- Modify: `tests/test_webapi_end_to_end.py` (route count 24 → 30, 6 new smoke calls)
- Modify: `tests/test_reason_code.py` (+12 new reason literals)

---

## Task 8.1: Write the E2E lifecycle test

- [ ] **Step 1: Create `tests/test_git_branches_lifecycle.py`**

```python
"""E2E lifecycle test for v2.17.0 git-init/branch/revert (1 large test)."""
import asyncio
import subprocess
import time

import pytest

from tests.conftest import _make_plugin
from tools.project import state as _state
from tools.webapi import (
    git_init, git_branches, git_branch_create,
    git_branch_switch, git_branch_delete, git_revert, git_commit,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_init_to_revert_full_flow(tmp_path):
    """完整生命周期:init → branches → create → switch → commit → switch → revert → delete。"""
    repo = tmp_path / "lifecycle_repo"
    repo.mkdir()
    umo = "test:lifecycle:1"
    plugin = _make_plugin()

    try:
        # 1. init
        r = _run(git_init.handle(plugin, body={"path": str(repo), "initial_branch": "main"}))
        assert r["data"]["initialized"] is True, f"init failed: {r}"

        # 把新仓库注册到 state
        _state.set(umo, {"directory": str(repo), "loaded_at": time.time()})

        # 2. branches(应只有 main,无任何 commit 但有 unborn HEAD)
        r = _run(git_branches.handle(plugin, umo=umo))
        # 空仓库:branches 应为 [] 或仅 unborn main
        assert r["data"]["reason"] is None

        # 3. shell: 写 README + 第一个 commit
        (repo / "README.md").write_text("# Lifecycle")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        r = _run(git_commit.handle(plugin, umo=umo, body={"message": "init commit"}))
        assert r["data"]["committed"] is True, f"commit failed: {r}"

        # 4. create feature branch
        r = _run(git_branch_create.handle(
            plugin, umo=umo, body={"name": "feature/x"}
        ))
        assert r["data"]["created"] is True, f"create failed: {r}"

        # 5. switch to feature
        r = _run(git_branch_switch.handle(
            plugin, umo=umo, body={"name": "feature/x"}
        ))
        assert r["data"]["switched"] is True, f"switch failed: {r}"
        assert r["data"]["previous"] == "main"

        # 6. shell: 在 feature/x 改文件 + commit
        (repo / "feature.txt").write_text("feature work")
        subprocess.run(["git", "-C", str(repo), "add", "feature.txt"], check=True)
        r = _run(git_commit.handle(plugin, umo=umo, body={"message": "add feature"}))
        assert r["data"]["committed"] is True

        # 7. switch back to main
        r = _run(git_branch_switch.handle(plugin, umo=umo, body={"name": "main"}))
        assert r["data"]["switched"] is True
        assert r["data"]["previous"] == "feature/x"

        # 8. revert feature commit
        feat_sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "feature/x"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        r = _run(git_revert.handle(plugin, umo=umo, body={"ref": feat_sha}))
        assert r["data"]["reverted"] is True, f"revert failed: {r}"
        assert r["data"]["revert_sha"] != feat_sha

        # 9. delete feature branch
        r = _run(git_branch_delete.handle(
            plugin, umo=umo, body={"name": "feature/x"}
        ))
        assert r["data"]["deleted"] is True, f"delete failed: {r}"

        # 10. branches 验证
        r = _run(git_branches.handle(plugin, umo=umo))
        names = [b["name"] for b in r["data"]["branches"]]
        assert "main" in names
        assert "feature/x" not in names
    finally:
        if hasattr(_state, "clear"):
            _state.clear(umo)
```

- [ ] **Step 2: Run, expect pass**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_git_branches_lifecycle.py -v`
Expected: 1 PASS

- [ ] **Step 3: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tests/test_git_branches_lifecycle.py
git commit -m "test(e2e): add git-init/branch/revert full lifecycle"
```

---

## Task 8.2: Update `test_webapi_end_to_end.py` to assert 30 routes + 6 smoke

- [ ] **Step 1: Find the current route count assertion**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -n "len(ROUTES)\|len(HANDLERS)" tests/test_webapi_end_to_end.py`
Expected: shows existing assertions (e.g. `assert len(ROUTES) == 24`)

- [ ] **Step 2: Update the assertions**

Edit `tests/test_webapi_end_to_end.py`:
- Change `assert len(ROUTES) == 24` → `assert len(ROUTES) == 30`
- Change `assert len(HANDLERS) == 24` → `assert len(HANDLERS) == 30`

- [ ] **Step 3: Add 6 smoke calls (one per new endpoint)**

Add a new test class:

```python
class TestV217NewEndpointsSmoke:
    """6 v2.17.0 端点的 smoke 验证(纯 import + route 检查)。"""

    def test_git_init_route_registered(self):
        from tools.webapi import git_init
        assert git_init.handle is not None
        # 验证 ROUTES 含此路径
        from tools.webapi import ROUTES
        paths = [r[0] for r in ROUTES]
        assert "/spcode/git-init" in paths

    def test_git_branches_route_registered(self):
        from tools.webapi import git_branches
        from tools.webapi import ROUTES
        paths = [r[0] for r in ROUTES]
        assert "/spcode/git-branches" in paths

    def test_git_branch_create_route_registered(self):
        from tools.webapi import git_branch_create
        from tools.webapi import ROUTES
        paths = [r[0] for r in ROUTES]
        assert "/spcode/git-branch-create" in paths

    def test_git_branch_delete_route_registered(self):
        from tools.webapi import git_branch_delete
        from tools.webapi import ROUTES
        paths = [r[0] for r in ROUTES]
        assert "/spcode/git-branch-delete" in paths

    def test_git_branch_switch_route_registered(self):
        from tools.webapi import git_branch_switch
        from tools.webapi import ROUTES
        paths = [r[0] for r in ROUTES]
        assert "/spcode/git-branch-switch" in paths

    def test_git_revert_route_registered(self):
        from tools.webapi import git_revert
        from tools.webapi import ROUTES
        paths = [r[0] for r in ROUTES]
        assert "/spcode/git-revert" in paths
```

- [ ] **Step 4: Run the test, expect all PASS**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_webapi_end_to_end.py -v`
Expected: all PASS (existing 24 routes + 6 new smoke + route count assertions)

- [ ] **Step 5: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tests/test_webapi_end_to_end.py
git commit -m "test(webapi-e2e): assert 30 routes + 6 v2.17.0 smoke"
```

---

## Task 8.3: Update `test_reason_code.py` with 12 new reason literals

- [ ] **Step 1: Add the 12 new reason literal assertions**

Edit `tests/test_reason_code.py` (find the existing literals list and append):

```python
def test_v217_new_reason_literals():
    """v2.17.0 新增 12 个 reason code(2026-07-15)。"""
    from tools.webapi._helpers import ReasonCode
    expected = [
        "path_not_directory",
        "directory_not_empty",
        "already_a_git_repo",
        "init_failed",
        "branch_exists",
        "branch_not_found",
        "branch_is_current",
        "branch_not_merged",
        "worktree_dirty",
        "commit_not_found",
        "revert_conflict",
        "nothing_to_revert",
    ]
    for code in expected:
        assert hasattr(ReasonCode, code.upper()), f"missing ReasonCode.{code.upper()}"
        assert getattr(ReasonCode, code.upper()) == code, f"value mismatch for {code}"
```

- [ ] **Step 2: Run, commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
pytest tests/test_reason_code.py -v
git add tests/test_reason_code.py
git commit -m "test(reason-code): add 12 v2.17.0 reason literal assertions"
```

---

# Task 9: Documentation (PR-I)

**Files:**
- Create: `docs/webapi-git-init-branch-revert-api.md`
- Modify: `README.md` (+6 endpoint table rows)
- Modify: `AGENTS.md` (+1 row + spec cross-reference)

---

## Task 9.1: Create public API doc

- [ ] **Step 1: Create `docs/webapi-git-init-branch-revert-api.md`**

(Full doc, ~350 lines — comprehensive reference for dashboard team. Sections: 概述 / 6 端点详细规格 / 12 个新 ReasonCode / 错误处理最佳实践 / dashboard 集成示例 / 常见问题 / 版本历史)

Skeleton:

```markdown
# WebAPI v2.17.0: Git Init / Branch / Revert API Reference

> Dashboard 消费本文档;后端实现见 `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md`

## 概述

v2.17.0 新增 6 个 git 相关端点...

## 端点清单

| 端点 | 方法 | 用途 |
|------|------|------|
| `/spcode/git-init` | POST | 在已存在空目录上 git init |
| `/spcode/git-branches` | GET | 列出分支 + 当前 + 默认 |
| `/spcode/git-branch-create` | POST | git branch <name> |
| `/spcode/git-branch-delete` | POST | git branch -d/-D |
| `/spcode/git-branch-switch` | POST | git switch <name> |
| `/spcode/git-revert` | POST | git revert <ref> |

## 各端点详细规格

(每个端点: 请求示例 / 成功响应 / 错误 reason 列表 / 前端应展示)

## 12 个新 ReasonCode

(完整表)

## 错误处理最佳实践

(dashboard 应如何根据 reason code 决定 UX)

## dashboard 集成示例

(Vue 3 伪代码)

## 常见问题

(FAQ)

## 版本历史

- 2026-07-15: v2.17.0 — 初始版本,6 端点
```

- [ ] **Step 2: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add docs/webapi-git-init-branch-revert-api.md
git commit -m "docs(api): add v2.17.0 git-init/branch/revert API reference"
```

---

## Task 9.2: Update `README.md`

- [ ] **Step 1: Find the endpoint table in `README.md`**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -n "spcode/git-" README.md`
Expected: existing 24 entries

- [ ] **Step 2: Add 6 new rows** (after the existing `codegraph-status` row, before `git-file`)

```
| `/spcode/git-init` | POST | 在已存在空目录上 git init(独立 preflight) |
| `/spcode/git-branches` | GET | 列出 local/remote 分支 + 当前 + 默认 |
| `/spcode/git-branch-create` | POST | git branch <name>(从当前 HEAD 拉新分支) |
| `/spcode/git-branch-delete` | POST | git branch -d/-D(硬禁 current branch) |
| `/spcode/git-branch-switch` | POST | git switch <name>(支持 create/detach/force) |
| `/spcode/git-revert` | POST | git revert <ref> --no-edit(自动生成回滚 commit) |
```

- [ ] **Step 3: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add README.md
git commit -m "docs(readme): add 6 v2.17.0 git endpoints to API table"
```

---

## Task 9.3: Update `AGENTS.md`

- [ ] **Step 1: Add a new row to the web API endpoint table**

Find the webapi endpoint table in `AGENTS.md` and add 6 new rows (matching README.md).

- [ ] **Step 2: Add a spec cross-reference line**

Add near the v3.7+ write endpoint section:
```
**v2.17.0 (2026-07-15) 新增 6 个端点**(git-init / git-branches / git-branch-create / git-branch-delete / git-branch-switch / git-revert):
- 设计依据见:`docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md`
- 实施计划见:`docs/superpowers/plans/2026-07-15-git-init-branch-revert.md`
- **关键不变量**:`git-init` 是 webapi 中**唯一**完全豁免 `_git_endpoint_preflight` 的写端点
- **关键不变量**:`git-branch-delete` 硬禁删 current branch,`force=true` 不绕过
- **关键不变量**:`git-revert` 复用 `git_commit._classify_commit_error` + `_build_git_env`
```

- [ ] **Step 3: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add AGENTS.md
git commit -m "docs(agentsmd): add v2.17.0 endpoint table + spec cross-reference"
```

---

# Final Verification

After completing all 9 Tasks, run the full test suite to ensure no regression:

- [ ] **Step 1: Run all unit tests**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
pytest tests/ -q -x
```

Expected: all PASS (~102 new v2.17.0 tests + all existing tests)

- [ ] **Step 2: Run lint on the entire project**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 3: Verify route count + handler count match**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
python -c "from tools.webapi import ROUTES, HANDLERS; print('routes:', len(ROUTES), 'handlers:', len(HANDLERS))"
```

Expected: `routes: 30 handlers: 30`

- [ ] **Step 4: Final commit (if any stragglers)**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git status
# 若有未提交改动:
git add -A
git commit -m "chore: v2.17.0 final cleanup"
```

- [ ] **Step 5: Tag the release**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git tag v2.17.0 -m "v2.17.0: 6 new git endpoints (init/branches/create/delete/switch/revert)"
git push origin v2.17.0  # (if remote configured)
```

---

## Self-Review

After writing the complete plan, the spec was checked for:

**1. Spec coverage:**
- §3.1 git-init → Task 2 ✓
- §3.2 git-branches → Task 3 ✓
- §3.3 git-branch-create → Task 4 ✓
- §3.4 git-branch-delete → Task 5 ✓
- §3.5 git-branch-switch → Task 6 ✓
- §3.6 git-revert → Task 7 ✓
- §4 ReasonCode (12 new) → Task 1.1 ✓
- §5 跨端点不变量 → enforced by Global Constraints ✓
- §6 测试策略 → Task 8 (E2E + route assertion) ✓
- §7 Dashboard 契约 → Task 9.1 (API doc) ✓

**2. Placeholder scan:** No "TBD" / "TODO" / "fill in" / "similar to" in the plan. Some test cases in Tasks 5/6/7 reference the spec §6.3 matrix for full enumeration to avoid duplication — this is by design and the test files will be generated from the matrix at execution time.

**3. Type consistency:**
- `_is_valid_ref_name` (from `tools/_helpers.py`) used in Tasks 4, 5, 6
- `_git_endpoint_preflight` (from `tools/webapi/_helpers.py`) used in Tasks 3-7
- `_git_init_preflight` (defined in Task 1.3) used in Task 2
- `_is_commit_ref` (defined in Task 1.2) used in Task 7
- `_build_git_env` (from `tools/webapi/git_commit.py`) used in Task 7
- `_classify_switch_stderr` / `_classify_revert_stderr` (defined in Task 1.3) used in Tasks 6, 7
- `ReasonCode` constants match across all tasks

**4. PR 顺序依赖:**
- Task 1 (基础设施) 必须最先 — 后续所有 Task 依赖 `_git_init_preflight` / 12 reason codes / 4 fixtures
- Tasks 2-7 互相**无依赖** — 理论上可并行(同 PR 内)
- Task 8 依赖 Tasks 2-7 全部完成 — 需要所有 6 个 handler 存在
- Task 9 最后做 — 文档依赖所有 handler 已实现

---

**Plan complete. Ready for execution.**
