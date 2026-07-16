# git-init `force` 标志实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `POST /spcode/git-init` 端点新增 `force: bool` 请求字段,允许显式 opt-in 在非空目录上初始化 git 仓库。

**Architecture:** 纯加性变更,仅修改 2 个文件 - `tools/webapi/_helpers.py` 的 `_git_init_preflight` 加 `force` 参数使非空检查条件化;`tools/webapi/git_init.py` handler 读取 + 校验 + 透传 `force`,成功响应回显 `force`。`already_a_git_repo` 保持 hard-ban,`force` 永不绕过。

**Tech Stack:** Python 3.10+,pytest,asyncio,ruff

## Global Constraints

- **Python 版本**: 3.10+(使用 `bool`、`dict | None` 等内置泛型)
- **Lint**: `ruff check .` 必须 0 error
- **测试框架**: pytest(无 `--timeout` 插件,用 `-p no:cacheprovider` 避免 cache 污染)
- **测试运行目录**: `F:\github\astrbot_plugin_spcode_toolkit`
- **行宽**: ruff 默认 88
- **缩进**: 4 空格
- **字符串引号**: 双引号
- **commit message 格式**: `feat(scope): description` / `test(scope): description` / `docs(scope): description`,末行 `Author: elecvoid243 @ YYYY-MM-DD`
- **`force` 语义**: 仅豁免 `directory_not_empty`;**不豁免** `path_not_directory` / `path_unsafe` / `already_a_git_repo`
- **`force` 类型**: 必须 `bool`;`"true"` 字符串 / `1` int / `null` -> `invalid_param`
- **`already_a_git_repo` 是 hard-ban**: `force=true` 也拒绝(检查顺序在 `directory_not_empty` 之前)
- **`bare` 与 `force` 独立**: 可组合(`force=true, bare=true` 合法)
- **向后兼容**: v2.17.0 所有现有测试**零修改**,继续 PASS
- **不新增 ReasonCode**: 复用 v2.17.0 的 `path_not_directory` / `path_unsafe` / `directory_not_empty` / `already_a_git_repo` / `init_failed` / `invalid_param` / `invalid_body`
- **spec 文档**: `docs/superpowers/specs/2026-07-16-git-init-force-design.md`
- **docs/ 在 .gitignore**: commit 时用 `git add -f docs/...`

---

### Task 1: `_git_init_preflight` 加 `force` 参数(非空检查条件化)

**Files:**
- Modify: `tools/webapi/_helpers.py`(函数 `_git_init_preflight`,约 L410-480)
- Test: `tests/test_git_init_preflight.py`(已有文件,追加用例)

**Interfaces:**
- Consumes: 无(本 task 是基础设施)
- Produces: `_git_init_preflight(plugin, *, path, force=False) -> tuple[dict | None, dict | None]`(新增 `force` keyword-only 参数,默认 `False` 保持向后兼容)

**关键不变量**:`already_a_git_repo` 检查**在** `directory_not_empty` 检查之前,顺序不变。`force=true` 时跳过 `directory_not_empty` 这一步,但 `already_a_git_repo` 仍触发。

- [ ] **Step 1: 写失败测试 - `force=true` 跳过非空检查**

追加到 `tests/test_git_init_preflight.py` 末尾(若文件不存在则创建,但 v2.17.0 已存在):

```python
# ── v2.17.1: force 标志 ────────────────────────────────────

def test_init_preflight_force_true_skips_nonempty_check(tmp_path):
    """force=true 时,非空目录(无 .git)应通过 preflight。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    err, ctx = _run(_git_init_preflight(plugin, path=str(target), force=True))
    assert err is None
    assert ctx is not None
    assert Path(ctx["path"]).resolve() == target.resolve()


def test_init_preflight_force_false_rejects_nonempty(tmp_path):
    """force=false(默认)时,非空目录仍被拒绝。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    err, ctx = _run(_git_init_preflight(plugin, path=str(target), force=False))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY
    assert ctx is None


def test_init_preflight_force_default_rejects_nonempty(tmp_path):
    """不传 force(默认)时,非空目录仍被拒绝 - 向后兼容验证。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    err, ctx = _run(_git_init_preflight(plugin, path=str(target)))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_preflight_force_true_still_rejects_already_git_repo(tmp_path):
    """force=true 时,已有 .git 的目录仍被拒绝(hard-ban)。"""
    target = tmp_path / "already"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    plugin = _make_plugin()
    err, ctx = _run(_git_init_preflight(plugin, path=str(target), force=True))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.ALREADY_A_GIT_REPO


def test_init_preflight_force_true_still_rejects_path_not_directory(tmp_path):
    """force=true 时,不存在的路径仍被拒绝(force 不绕过路径防御)。"""
    plugin = _make_plugin()
    err, ctx = _run(_git_init_preflight(
        plugin, path=str(tmp_path / "nope"), force=True
    ))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_preflight_force_true_still_rejects_path_unsafe(tmp_path):
    """force=true 时,含 .. 的路径仍被拒绝(force 不绕过路径安全)。"""
    plugin = _make_plugin()
    err, ctx = _run(_git_init_preflight(plugin, path="../../../etc", force=True))
    assert err is not None
    assert err["data"]["reason"] == ReasonCode.PATH_UNSAFE


def test_init_preflight_force_true_with_subdirectory(tmp_path):
    """force=true 时,非空目录(含子目录)也应通过 preflight。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "src").mkdir()
    (target / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    plugin = _make_plugin()
    err, ctx = _run(_git_init_preflight(plugin, path=str(target), force=True))
    assert err is None
    assert ctx is not None
```

如果 `tests/test_git_init_preflight.py` 当前没有 import 这些符号,先在文件顶部确认 imports:

```python
import asyncio
import subprocess
from pathlib import Path

from tests.conftest import _make_plugin
from tools.webapi._helpers import ReasonCode, _git_init_preflight


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)
```

(若文件已有这些 import,不要重复添加。)

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init_preflight.py -k "force" --tb=short -q -p no:cacheprovider
```

Expected: FAIL,错误类似 `_git_init_preflight() got an unexpected keyword argument 'force'`(因为函数签名还没加 `force` 参数)。

- [ ] **Step 3: 实现 - 修改 `_git_init_preflight` 签名与非空检查**

修改 `tools/webapi/_helpers.py` 的 `_git_init_preflight` 函数(L407 附近):

**当前签名**:
```python
async def _git_init_preflight(
    plugin: object,
    *,
    path: str,
) -> tuple[dict | None, dict | None]:
```

**改为**:
```python
async def _git_init_preflight(
    plugin: object,
    *,
    path: str,
    force: bool = False,
) -> tuple[dict | None, dict | None]:
```

**docstring 更新**(在函数 docstring 末尾加一段,在 `"""` 之前):

```python
    """Run the git-init-only path preflight.

    Unlike ``_git_endpoint_preflight``, this helper does not resolve a loaded
    project or probe for an existing Git repository.

    ``force=True`` (v2.17.1) skips the ``directory_not_empty`` check, allowing
    initialization of a non-empty directory. It does NOT bypass
    ``already_a_git_repo`` (hard-ban), ``path_not_directory``, or
    ``path_unsafe`` - those are unconditional safety guards.
    """
```

**非空检查条件化**(L465 附近,在 `already_a_git_repo` 检查之后):

当前代码:
```python
    if any(target.iterdir()):
        return _make_envelope(
            success=False,
            reason=ReasonCode.DIRECTORY_NOT_EMPTY,
            elapsed_ms=0,
            path=path,
        ), None

    return None, {"path": str(target)}
```

改为:
```python
    # v2.17.1: force=True 跳过非空检查(允许在已有代码目录 init)。
    # already_a_git_repo 检查在上面已完成,force 不绕过 hard-ban。
    if not force and any(target.iterdir()):
        return _make_envelope(
            success=False,
            reason=ReasonCode.DIRECTORY_NOT_EMPTY,
            elapsed_ms=0,
            path=path,
        ), None

    return None, {"path": str(target)}
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init_preflight.py -k "force" --tb=short -q -p no:cacheprovider
```

Expected: PASS(7 个 force 相关用例)。

- [ ] **Step 5: 运行 preflight 全部测试验证向后兼容**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init_preflight.py --tb=short -q -p no:cacheprovider
```

Expected: PASS(原有用例 + 新增 7 个 force 用例,全部通过)。

- [ ] **Step 6: ruff lint**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && ruff check tools/webapi/_helpers.py tests/test_git_init_preflight.py
```

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && git add tools/webapi/_helpers.py tests/test_git_init_preflight.py && git commit -m "feat(webapi): _git_init_preflight adds force param (v2.17.1 Task 1)

force=True skips directory_not_empty check, allowing init on non-empty
directories. Does NOT bypass already_a_git_repo (hard-ban),
path_not_directory, or path_unsafe - those remain unconditional.

Backward compatible: force defaults to False, all v2.17.0 tests unchanged.

Author: elecvoid243 @ 2026-07-16"
```

---

### Task 2: handler 读取 + 校验 + 透传 `force`,成功响应回显

**Files:**
- Modify: `tools/webapi/git_init.py`(handler `handle()`,约 L60-220)
- Test: `tests/test_git_init.py`(已有文件,追加用例)

**Interfaces:**
- Consumes: `_git_init_preflight(plugin, *, path, force=False)`(Task 1 产出)
- Produces: `POST /spcode/git-init` 接受 `force: bool` body 字段;成功响应 payload 含 `force: bool`

**关键不变量**:
- `force` 类型校验位置:紧接现有 `bare` 类型校验之后(保持代码风格一致)
- preflight 调用:`_git_init_preflight(plugin, path=path, force=force)`
- 成功响应:在现有 `bare=bare` 之后加 `force=force`
- **不修改** v2.17.0 任何现有测试 - 它们不传 `force`,行为不变

- [ ] **Step 1: 写失败测试 - handler `force=true` 集成**

追加到 `tests/test_git_init.py` 末尾(在 `test_init_then_validate_worktree_param_passes` 之后):

```python
# ── v2.17.1: force 标志 ────────────────────────────────────

def test_init_force_true_nonempty_dir_with_file(tmp_path):
    """force=true + 非空目录(含文件) -> 成功 init,文件保留 untracked。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": True}
    ))
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True
    assert (target / ".git").is_dir()
    # 原文件保留
    assert (target / "README.md").read_text(encoding="utf-8") == "hello"


def test_init_force_true_nonempty_dir_with_subdirectory(tmp_path):
    """force=true + 非空目录(含子目录) -> 成功 init。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "src").mkdir()
    (target / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": True}
    ))
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True


def test_init_force_true_still_rejects_already_git_repo(tmp_path):
    """force=true + 已有 .git -> 仍返回 already_a_git_repo(hard-ban)。"""
    target = tmp_path / "already"
    target.mkdir()
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": True}
    ))
    assert result["data"]["initialized"] is False
    assert result["data"]["reason"] == ReasonCode.ALREADY_A_GIT_REPO


def test_init_force_true_still_rejects_path_not_directory(tmp_path):
    """force=true + 不存在路径 -> 仍返回 path_not_directory。"""
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(tmp_path / "nope"), "force": True}
    ))
    assert result["data"]["reason"] == ReasonCode.PATH_NOT_DIRECTORY


def test_init_force_true_still_rejects_path_unsafe(tmp_path):
    """force=true + 含 .. 路径 -> 仍返回 path_unsafe。"""
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": "../../../etc", "force": True}
    ))
    assert result["data"]["reason"] == ReasonCode.PATH_UNSAFE


def test_init_force_true_bare_true_nonempty_dir(tmp_path):
    """force=true + bare=true + 非空目录 -> 成功 init bare repo。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": True, "bare": True}
    ))
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True
    assert result["data"]["bare"] is True


def test_init_force_true_empty_dir_still_works(empty_dir):
    """force=true + 空目录 -> 成功 init(与 force=false 一致)。"""
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(empty_dir), "force": True}
    ))
    assert result["data"]["initialized"] is True
    assert result["data"]["force"] is True


def test_init_force_default_false_rejects_nonempty(tmp_path):
    """不传 force(默认 false) -> 非空目录仍被拒绝 - 向后兼容验证。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(plugin, body={"path": str(target)}))
    assert result["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_force_explicit_false_rejects_nonempty(tmp_path):
    """显式 force=false -> 非空目录仍被拒绝。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "README.md").write_text("hello", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": False}
    ))
    assert result["data"]["reason"] == ReasonCode.DIRECTORY_NOT_EMPTY


def test_init_force_string_type_rejected(tmp_path):
    """force='true'(字符串) -> invalid_param。"""
    target = tmp_path / "occupied"
    target.mkdir()
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": "true"}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_force_int_type_rejected(tmp_path):
    """force=1(int) -> invalid_param。"""
    target = tmp_path / "occupied"
    target.mkdir()
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": 1}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_force_null_type_rejected(tmp_path):
    """force=null(None) -> invalid_param(严格 bool 校验)。"""
    target = tmp_path / "occupied"
    target.mkdir()
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": None}
    ))
    assert result["data"]["reason"] == ReasonCode.INVALID_PARAM


def test_init_force_true_response_echoes_force(tmp_path):
    """成功响应必须回显 force 字段(便于前端审计)。"""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "x.txt").write_text("x", encoding="utf-8")
    plugin = _make_plugin()
    result = _run(git_init.handle(
        plugin, body={"path": str(target), "force": True}
    ))
    assert result["data"]["initialized"] is True
    assert "force" in result["data"]
    assert result["data"]["force"] is True
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init.py -k "force" --tb=short -q -p no:cacheprovider
```

Expected: FAIL,错误类似:
- `force=true` 用例:`directory_not_empty`(因为 handler 还没读 force)
- `force` 类型校验用例:不返回 `invalid_param`(因为还没加类型校验)
- `force` 回显用例:`KeyError: 'force'`(因为响应还没加 force 字段)

- [ ] **Step 3: 实现 - handler 读取 + 校验 `force`**

修改 `tools/webapi/git_init.py` 的 `handle()` 函数。

**3a. 读取 force**(L85 附近,在 `bare = body.get("bare", False)` 之后):

当前代码:
```python
    path = body.get("path")
    initial_branch = body.get("initial_branch", "main")
    bare = body.get("bare", False)
```

改为:
```python
    path = body.get("path")
    initial_branch = body.get("initial_branch", "main")
    bare = body.get("bare", False)
    force = body.get("force", False)
```

**3b. 类型校验 force**(L118 附近,在 `if not isinstance(bare, bool):` 校验块之后):

当前代码(bare 校验):
```python
    if not isinstance(bare, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=path,
        )
```

在此块之后追加(force 校验,完全平行结构):
```python
    if not isinstance(force, bool):
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(),
            initialized=False,
            path=path,
        )
```

**3c. preflight 调用透传 force**(L125 附近):

当前代码:
```python
    # ── 3. init-only preflight (4 步) ──
    err, ctx = await _git_init_preflight(plugin, path=path)
```

改为:
```python
    # ── 3. init-only preflight (4 步,force 透传给非空检查) ──
    err, ctx = await _git_init_preflight(plugin, path=path, force=force)
```

- [ ] **Step 4: 实现 - 成功响应回显 `force`**

修改 `tools/webapi/git_init.py` 成功响应构造(L210 附近,`_JSONResponseCompat` 调用内的 `_make_envelope`):

当前代码:
```python
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

改为(在 `bare=bare,` 之后加 `force=force,`):
```python
    return _JSONResponseCompat(
        _make_envelope(
            success=True,
            elapsed_ms=_elapsed(),
            initialized=True,
            path=abs_path,
            initial_branch=initial_branch,
            bare=bare,
            force=force,
            git_dir=git_dir,
            umo=umo,
            worktree="",
        ),
        status_code=200,
    )
```

- [ ] **Step 5: 运行 force 测试验证通过**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init.py -k "force" --tb=short -q -p no:cacheprovider
```

Expected: PASS(13 个 force 相关用例)。

- [ ] **Step 6: 运行 test_git_init.py 全部测试验证向后兼容**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init.py --tb=short -q -p no:cacheprovider
```

Expected: PASS(v2.17.0 原有用例 + 新增 13 个 force 用例,全部通过)。

- [ ] **Step 7: ruff lint**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && ruff check tools/webapi/git_init.py tests/test_git_init.py
```

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && git add tools/webapi/git_init.py tests/test_git_init.py && git commit -m "feat(webapi): git-init handler reads+validates+echoes force (v2.17.1 Task 2)

Handler now accepts force: bool in request body (default False).
- Type validation: non-bool (string/int/null) -> invalid_param
- force=True passed to _git_init_preflight, skips directory_not_empty
- Success response echoes force field for frontend audit

Backward compatible: v2.17.0 tests (no force field) unchanged.

Author: elecvoid243 @ 2026-07-16"
```

---

### Task 3: E2E smoke 验证 + 文档更新

**Files:**
- Test: `tests/test_webapi_end_to_end.py`(验证路由表 smoke 不受影响,无需新增用例)
- Modify: `docs/api/v2.17.0-endpoints-frontend.md`(前端参考清单加 `force` 字段)
- Modify: `docs/api/webapi-git-init-branch-revert-api.md`(详细 API reference 加 `force`)
- Modify: `AGENTS.md`(git-init 行为矩阵加 `force` 列)
- Modify: `README.md`(API 表 git-init 行更新)
- Modify: `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md`(加 v2.17.1 changelog 备注)

**Interfaces:**
- Consumes: Task 1 + Task 2 产出的 `force` 功能
- Produces: 文档同步更新,E2E 验证通过

- [ ] **Step 1: 运行 E2E smoke 验证路由表不受影响**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_webapi_end_to_end.py --tb=short -q -p no:cacheprovider
```

Expected: PASS(30 条路由 smoke,git-init 路由仍注册,`_wrap` 注入不受影响 - force 是 body 字段,不涉及路由表)。

- [ ] **Step 2: 运行 v2.17.0 全套 git-init 相关测试最终验证**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init.py tests/test_git_init_preflight.py tests/test_webapi_end_to_end.py --tb=short -q -p no:cacheprovider
```

Expected: PASS(全部用例,含 v2.17.0 原有 + v2.17.1 新增 force)。

- [ ] **Step 3: 更新 `docs/api/v2.17.0-endpoints-frontend.md`**

在文件中 `POST /spcode/git-init` 章节的 **Request Body** 部分,在 `bare?: boolean` 行之后追加:

```typescript
  force?: boolean;                     // 【v2.17.1 新增】可选,默认 false;true 时允许在非空目录 init
```

在 **成功响应** 的 `bare: boolean;` 行之后追加:

```typescript
  force:          boolean;       // 【v2.17.1 新增】实际使用的 force
```

在 **错误 reason** 表中,`directory_not_empty` 行的 UX 列更新为:

```markdown
| `directory_not_empty` | 目录非空(且 `bare=false`);**v2.17.1: `force=true` 可豁免** | 弹二次确认对话框,用户确认后用 `force=true` 重试 |
```

在 **示例** 部分追加 force 二次确认示例:

```bash
POST /spcode/git-init
{"path": "D:/Users/dev/projects/myapp", "force": true}
```

```json
{
  "success": true, "reason": null, "elapsed_ms": 234,
  "data": {
    "initialized": true,
    "path": "D:/Users/dev/projects/myapp",
    "initial_branch": "main",
    "bare": false,
    "force": true,
    "git_dir": "D:/Users/dev/projects/myapp/.git",
    "umo": "",
    "worktree": ""
  }
}
```

- [ ] **Step 4: 更新 `docs/api/webapi-git-init-branch-revert-api.md`**

在 git-init 端点的 Body schema 加 `force` 字段说明,行为矩阵加 `force=true` 列,ReasonCode 表更新 `directory_not_empty` 的 force 影响。具体改动参考 spec §3.2 行为矩阵。

- [ ] **Step 5: 更新 `AGENTS.md`**

在 v2.17.0 端点表的 git-init 行后追加一行注释,或在 ReasonCode 表附近加备注:

```markdown
> **v2.17.1 (2026-07-16) git-init `force` 标志**:`POST /spcode/git-init` 新增 `force: bool`
> 请求字段(默认 `false`)。`force=true` 跳过 `directory_not_empty` 检查,允许在非空目录
> init git 仓库。`already_a_git_repo` 是 hard-ban,`force` 不绕过。详见
> `docs/superpowers/specs/2026-07-16-git-init-force-design.md`。
```

- [ ] **Step 6: 更新 `README.md`**

在 API 表 git-init 行的"关键参数"列追加 `force?`:

```markdown
| `/spcode/git-init` | POST | 在空目录上 git init(**唯一**豁免 preflight) | body: `{path, initial_branch?="main", bare?=false, force?=false}` |
```

- [ ] **Step 7: 更新 v2.17.0 spec 加 changelog 备注**

在 `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md` 顶部 metadata 之后加:

```markdown
> **v2.17.1 Update (2026-07-16)**: 本 spec §3.1 的 `directory_not_empty` "最严格"语义
> 已被 `docs/superpowers/specs/2026-07-16-git-init-force-design.md` 放宽为 opt-in。
> `force=true` 时跳过非空检查;`force=false`(默认)行为不变。
```

- [ ] **Step 8: ruff lint(确认无 Python 文件被误改)**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 9: Commit 文档更新**

```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && git add -f docs/api/v2.17.0-endpoints-frontend.md docs/api/webapi-git-init-branch-revert-api.md docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md AGENTS.md README.md && git commit -m "docs: sync v2.17.1 git-init force flag across api/agentsmd/readme/specs

- frontend api reference: force field +二次确认 UX example
- detailed api reference: behavior matrix + reason code updates
- v2.17.0 spec: changelog note pointing to v2.17.1 spec
- AGENTS.md: v2.17.1 changelog block
- README.md: git-init row adds force? param

Author: elecvoid243 @ 2026-07-16"
```

---

### Task 4: 最终验收 + spec 验收标准核对

**Files:**
- 无文件修改,仅验证

**Interfaces:**
- Consumes: Task 1 + 2 + 3 全部产出
- Produces: 验收报告

- [ ] **Step 1: 运行 ruff check 全项目**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 2: 运行 git-init 相关全部测试**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && pytest tests/test_git_init.py tests/test_git_init_preflight.py tests/test_webapi_end_to_end.py --tb=short -q -p no:cacheprovider
```

Expected: PASS(全部用例)。

- [ ] **Step 3: 核对 spec §12 验收标准**

逐项核对 `docs/superpowers/specs/2026-07-16-git-init-force-design.md` §12:

1. ✅ `ruff check .` 无 error - Step 1 验证
2. ✅ `pytest tests/test_git_init.py tests/test_git_init_preflight.py` 全部 PASS(含新增 ~20 个用例)- Step 2 验证
3. ✅ `pytest tests/test_webapi_end_to_end.py` PASS - Step 2 验证
4. ✅ v2.17.0 现有测试用例零修改,全部继续 PASS - Task 1 Step 5 + Task 2 Step 6 验证
5. ✅ 手动验证:`force=true` + 含 `README.md` 的目录 -> 成功 init - `test_init_force_true_nonempty_dir_with_file` 验证

- [ ] **Step 4: 核对 git log 干净**

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && git log --oneline -5
```

Expected: 看到 3 个新 commit(Task 1 / Task 2 / Task 3),无 WIP 或未提交改动。

Run:
```bash
cd /d "F:\github\astrbot_plugin_spcode_toolkit" && git status -s
```

Expected: 空(working tree clean)。

- [ ] **Step 5: 验收报告**

向用户报告:
- ruff: ✅
- pytest: ✅ (N passed)
- 向后兼容: ✅ v2.17.0 测试零修改
- 文档: ✅ 5 个文件已更新
- commits: 3 个(Task 1 / 2 / 3)

---

## Self-Review 备注

**spec §4.2 响应字段偏差**:spec 写成功响应字段为 `directory` / `hint`,但 v2.17.0 实际代码字段为 `path` / `git_dir` / `umo` / `worktree`。本 plan 按**实际代码字段**实现(加 `force` 到现有字段集),不重命名现有字段。spec 文档的 `directory`/`hint` 是设计意图描述,实际字段名以代码为准 - 这是 spec 与代码的既有偏差,不在本 plan scope 内修正。

**测试用例计数**:
- Task 1 新增 7 个 preflight 用例
- Task 2 新增 13 个 handler 用例
- 总计新增 20 个测试用例(spec §7.3 估计 ~10 个,实际更细粒度)
