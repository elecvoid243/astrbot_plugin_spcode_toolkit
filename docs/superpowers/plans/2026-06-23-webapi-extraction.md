# WebAPI Extraction — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `main.py` 中的 6 个 webapi 端点 handler + 21 个端点专用辅助 + 3 个 git-diff 常量 + 2 个跨端点共享(`_run_git_async` + `_JSONResponseCompat`) 全部抽出到 `tools/webapi/` 子包,`main.py` 从 4378 行下降到 ≤ 200 行;端点对外契约**完全不变**。

**Architecture:** 新建 `tools/webapi/` 子包(8 个文件),handler 改为模块级协程函数;`tools/webapi/__init__.py` 提供 `register_webapi_routes(plugin)` 一次注册 6 个端点;`_wrap()` 适配层把 AstrBot `request.*` 隐式取值转换为 handler 关键字参数;handler 调用跨端点共享函数时**强制 Pattern B**(`from . import _helpers as _helpers` + `_helpers._run_git_async(...)`)以保证 pytest monkeypatch 生效。

**Tech Stack:**
- Python 3.10+(`list[str]` / `dict[str, int]` / `X | None` 内置泛型)
- `asyncio.create_subprocess_exec` + `communicate()` 异步 git 子进程
- `pytest` + `pytest-asyncio`(既有)
- `ruff check .` lint(既有)
- 端点注册用 AstrBot 框架 `context.register_web_api(route, view_handler, methods, desc)`

**Spec:** `docs/superpowers/specs/2026-06-23-webapi-extraction-design.md`(每个 chunk 开始前必读)

---

## File Structure

| Status | Path | Responsibility |
|--------|------|----------------|
| **Create** | `tools/webapi/__init__.py` | `ROUTES` 元组列表 + `HANDLERS` 字典 + `_wrap()` 适配层 + `register_webapi_routes()` |
| **Create** | `tools/webapi/_helpers.py` | `_run_git_async` + `class _JSONResponseCompat`(跨端点共享) |
| **Create** | `tools/webapi/project_status.py` | `handle()` for `GET /spcode/project-status` + 0 辅助 |
| **Create** | `tools/webapi/plan_mode.py` | `handle()` for `GET /spcode/plan-mode` + 0 辅助 |
| **Create** | `tools/webapi/git_worktrees.py` | `handle()` for `GET /spcode/git-worktrees` + 1 辅助(`_make_git_worktrees_empty_envelope`) |
| **Create** | `tools/webapi/git_diff.py` | `handle()` for `GET /spcode/git-diff` + 7 辅助 + 3 常量 |
| **Create** | `tools/webapi/file_browser.py` | `handle()` for `GET /spcode/file-browser` + 12 辅助 |
| **Create** | `tools/webapi/file_restore.py` | `handle()` for `POST /spcode/file-restore` + 4 辅助 |
| **Create** | `tests/test_project_status.py` | 新 smoke test(`handle_get_project_status` 之前无直接测试) |
| **Create** | `tests/test_webapi_helpers_smoke.py` | Chunk 1 smoke test:验证 `_run_git_async` + `_JSONResponseCompat` 可从 `tools.webapi._helpers` import |
| **Create** | `tests/test_webapi_end_to_end.py` | Chunk 5 smoke test:验证 6 个 handler 均可被调用且返回 dict |
| **Modify** | `main.py`(多处) | 删除 6 handler / 6 register 块 / 21 辅助 / 2 跨端点 / 3 常量 / `class _JSONResponseCompat`;`initialize()` 改为 `register_webapi_routes(self)` |
| **Modify** | `tests/conftest.py` | 新增 `handlers` fixture,导出 `HANDLERS` 字典 |
| **Modify** | `tests/test_plan_mode.py` | 5 处 `plugin.handle_get_plan_mode` → `handlers["handle_get_plan_mode"](plugin)` |
| **Modify** | `tests/test_git_worktrees.py` | ~10 处 `plugin.handle_get_git_worktrees` → `handlers[...](plugin, umo=..., worktree=...)` |
| **Modify** | `tests/test_git_diff.py` | ~15 处 `plugin.handle_get_git_diff` → `handlers[...]`;**4 处** `monkeypatch.setattr(main_mod, "_run_git_async", ...)` → `monkeypatch.setattr("astrbot_plugin_spcode_toolkit.tools.webapi._helpers", "_run_git_async", ...)` |
| **Modify** | `tests/test_git_diff_worktree.py` | ~10 处 `plugin.handle_get_git_diff(worktree=...)` → `handlers["handle_get_git_diff"](plugin, worktree=...)` |
| **Modify** | `tests/test_file_browser.py` | ~10 处 `plugin.handle_get_file_browser` → `handlers["handle_get_file_browser"](plugin, path=..., if_none_match=...)`;`call_file_browser` helper 简化或删除 |
| **Modify** | `tests/test_file_restore.py` | ~30 处 `plugin.handle_post_file_restore` → `handlers["handle_post_file_restore"](plugin, body=...)`;`_patch_post_body` helper **删除** |
| **Modify** | `tests/test_run_git_async.py` | 6 处 `from main import _run_git_async` → `from tools.webapi._helpers import _run_git_async` |

---

## Implementation Order

| Chunk | 内容 | 可独立验证 | 实际 commit 数 |
|-------|------|------------|---------------|
| **Chunk 1** | 共享层 `tools/webapi/_helpers.py` | ✅ `pytest tests/test_run_git_async.py` 通过 | 5(1.1/1.2/1.3/1.4/1.5) |
| **Chunk 2** | 3 个小端点(project_status/plan_mode/git_worktrees)+ `__init__.py` 骨架 | ✅ 对应测试通过 | 3(2.1/2.2/2.3) |
| **Chunk 3** | file_browser + file_restore | ✅ 对应测试通过 | 2(3.1/3.2) |
| **Chunk 4** | git_diff(大端点) | ✅ `test_git_diff*.py` 通过 | 1(4.1) |
| **Chunk 5** | `_wrap()` + `register_webapi_routes()` + main.py 瘦身 + 全量测试迁移 + 验收 | ✅ 全部 `pytest` 通过 + `wc -l main.py` ≤ 200 | 3(5.1/5.2/5.3) |

**总 commit 数**:5+3+2+1+3 = **14 个 commit**,每个 chunk 内部细分(failing test → minimal impl → green → commit)。

---

## Critical Patterns(整个 plan 通用)

### Pattern A:模块级 handler 函数
```python
# tools/webapi/file_browser.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import SPCodeToolkit  # forward ref,no runtime import


async def handle(
    plugin: "SPCodeToolkit",
    *,
    path: str = "",
    if_none_match: str | None = None,
) -> dict[str, Any]:
    ...
```

### Pattern B:跨端点共享调用(强制,pytest monkeypatch 依赖)
```python
# tools/webapi/git_diff.py 顶部
from . import _helpers  # noqa: F401  — module-globally-bound,must NOT do `from ._helpers import _run_git_async`

# 业务代码
result = await _helpers._run_git_async(probe_cmd, cwd=directory, encoding=_GIT_DIFF_ENCODING)
```

### 禁止 Pattern(测试会 fail)
```python
# ❌ 错 — 本地绑定,monkeypatch 失效
from ._helpers import _run_git_async
result = await _run_git_async(...)  # 用的是函数对象的本地副本
```

### 测试调用模式
```python
# tests/test_xxx.py 顶部
from tests.conftest import handlers  # fixture 暴露的 HANDLERS 字典

# 测试函数中
async def test_xxx(plugin):
    result = await handlers["handle_get_file_browser"](plugin, path="/some/path")
    assert ...
```

---

# Chunk 1: 共享层 `tools/webapi/_helpers.py`

**Goal:** 把跨端点共享的 `_run_git_async` + `class _JSONResponseCompat` 从 `main.py` 移到 `tools/webapi/_helpers.py`;改 `tests/test_run_git_async.py` 的 import 路径并确保全部测试通过。

**可独立验证**:`pytest tests/test_run_git_async.py` 6 个测试全绿;`grep -nE "^(async )?def _run_git_async|^class _JSONResponseCompat" main.py` 0 命中。

---

## Task 1.1: 创建 `tools/webapi/` 目录骨架 + `__init__.py` 占位

**Files:**
- Create: `tools/webapi/__init__.py`

- [ ] **Step 1: 创建空 `tools/webapi/__init__.py`**

```python
# tools/webapi/__init__.py
"""Web API endpoint handlers,extracted from main.py.

See docs/superpowers/specs/2026-06-23-webapi-extraction-design.md for architecture.
"""
```

- [ ] **Step 2: 验证 import 不报错**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from tools import webapi; print(webapi.__file__)"`
Expected: 打印 `...\tools\webapi\__init__.py`,无报错。

- [ ] **Step 3: 提交**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/__init__.py
git commit -m "feat(webapi): scaffold tools/webapi package"
```

---

## Task 1.2: 把 `_run_git_async` 移到 `tools/webapi/_helpers.py`

**Files:**
- Create: `tools/webapi/_helpers.py`
- Modify: `main.py` (line 73-130 周围,即 `_run_git_async` 函数定义及上方 docstring)

- [ ] **Step 1: 读 `main.py` 中 `_run_git_async` 的完整定义**

Run: 在 `main.py` 中定位 line 73-130(包含 docstring + 函数体)
Read: `main.py` line 73-130
Expected:看到完整函数定义 + 文档注释 + 类型注解。**记录函数签名、所有 import 依赖、特殊常量引用**。

- [ ] **Step 2: 在 `tools/webapi/_helpers.py` 创建占位文件**

```python
# tools/webapi/_helpers.py
"""Shared helpers for tools/webapi/* endpoints.

Only imported by webapi/* handler modules. Do NOT import from main.py
(circular dependency) or tools/_helpers.py (cross-layer coupling).
"""
from __future__ import annotations
import asyncio
from typing import Any
```

- [ ] **Step 3: 写一个最小失败的 import 测试(red)**

```python
# tests/test_webapi_helpers_smoke.py  (新文件,Task 1.4 会扩展)
import pytest


def test_run_git_async_importable_from_webapi_helpers():
    from tools.webapi import _helpers
    assert hasattr(_helpers, "_run_git_async")
    assert asyncio.iscoroutinefunction(_helpers._run_git_async)
```

- [ ] **Step 4: 运行测试,确认 red**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_webapi_helpers_smoke.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'tools.webapi._helpers'` 或 `AttributeError: module 'tools.webapi._helpers' has no attribute '_run_git_async'`。

- [ ] **Step 5: 把 `_run_git_async` 完整代码粘贴到 `tools/webapi/_helpers.py`**

从 `main.py` 复制 line 73-130 的内容到 `tools/webapi/_helpers.py` 末尾,**只改 import**:
- 保留 `import asyncio` 等内置 stdlib
- **删除** 任何 `from .xxx import yyy` 中涉及 `main` 的引用(应没有,因为 `_run_git_async` 是纯 stdlib 函数)
- 保持函数签名、行为、docstring 不变

- [ ] **Step 6: 运行测试,确认 green**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_webapi_helpers_smoke.py -v`
Expected: PASS,1 passed。

- [ ] **Step 7: 提交**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/_helpers.py tests/test_webapi_helpers_smoke.py
git commit -m "feat(webapi): add _run_git_async to webapi/_helpers.py"
```

---

## Task 1.3: 从 `main.py` 删除 `_run_git_async` 定义 + 处理 7 处调用点

**Files:**
- Modify: `main.py` (line 73-130 删除;7 处 `await _run_git_async(...)` 调用点保留不动,后续 chunk 处理)

- [ ] **Step 1: 验证 main.py 当前调用 `_run_git_async` 的位置(精确 7 处)**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -nE "_run_git_async" main.py`
Expected: 看到 **7 处** `await _run_git_async(...)` 调用:
- line 332(`head_result = await _run_git_async(...)` 在 `handle_get_git_diff` 内,P1 perf 改造)
- line 2941(`probe = await _run_git_async(...)`)
- line 2988(`raw_result = await _run_git_async(...)`)
- line 3002(`numstat_result = await _run_git_async(...)`)
- line 4174(file_restore `probe`)
- line 4236(file_restore `status`)
- line 4348(file_restore `restore_cmd`)

加 1 处 `async def _run_git_async(...)` 定义(line 80)。

> **重要**:此 Task **不**修改调用点代码(只删定义)。调用点改造属于 Chunk 4(git_diff 4 处:332/2941/2988/3002)和 Chunk 3(file_restore 3 处:4174/4236/4348)的 Pattern B 改造。本 Task 只确保"删定义后调用点临时 fail 之前能跑通测试"。
>
> **本 Task 的实际工作**:仅在 main.py 顶部加临时 re-export 占位,让 7 处调用通过 `main._run_git_async` 仍能找到符号;这些占位在 Chunk 5 集中删除。

- [ ] **Step 2: 从 main.py 删除 `_run_git_async` 函数定义(line 73-130)**

Edit `main.py`:
- 删除 line 73-130 的 docstring + `async def _run_git_async(...):` 函数体
- **不**改动 6 处 `await _run_git_async(...)` 调用(暂时)

- [ ] **Step 3: 临时在 main.py 顶部加 re-export 占位,让 main.py 仍可被 import**

在 `main.py` 顶部(已有 import 区域)添加:
```python
# TEMPORARY: re-export for callers in main.py;will be removed in Chunk 3/4
from tools.webapi import _helpers as _webapi_helpers_run_git  # noqa: F401
_run_git_async = _webapi_helpers_run_git._run_git_async  # noqa: F401
```

- [ ] **Step 4: 验证 main.py 仍可被 import + main.py 内部调用仍能找到符号**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from astrbot_plugin_spcode_toolkit import main; print(main._run_git_async.__module__)"`
Expected: 打印 `tools.webapi._helpers`,无报错。

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -nE "_run_git_async" main.py`
Expected: 看到 6 处 `await _run_git_async(...)`(调用未变)+ 2 行 re-export 占位;**没有** `async def _run_git_async`。

- [ ] **Step 5: 提交(临时状态,后续 chunk 会清理 re-export 占位)**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add main.py
git commit -m "refactor(webapi): remove _run_git_async from main.py, re-export from webapi/_helpers (TEMP, removed in Chunk 5)"
```

---

## Task 1.4: 迁移 `tests/test_run_git_async.py` import 路径(7 处)

**Files:**
- Modify: `tests/test_run_git_async.py` (6 处 `from main import _run_git_async` + 1 处 `from main import main as _m` + 2 处 `_m._run_git_async` 调用)

- [ ] **Step 1: 定位全部 7 处需要修改的位置**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -nE "from astrbot_plugin_spcode_toolkit.main import _run_git_async|from astrbot_plugin_spcode_toolkit import main as _m" tests/test_run_git_async.py`
Expected: 7 行命中(直接 import 6 处 + `_m` alias 1 处):
- line 25, 39, 52, 64, 77, 135: `from astrbot_plugin_spcode_toolkit.main import _run_git_async`
- line 103: `from astrbot_plugin_spcode_toolkit import main as _m`

加上 line 107/116 的 `_m._run_git_async(...)` 调用(line 103 的 import 改了之后,line 107/116 也要相应改)。

- [ ] **Step 2: 批量替换直接 import(6 处,逐行使用 `astrbot_file_edit_tool`)**

对每行 `from astrbot_plugin_spcode_toolkit.main import _run_git_async`:
- old: `from astrbot_plugin_spcode_toolkit.main import _run_git_async`
- new: `from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async`

- [ ] **Step 3: 替换 `_m` alias + 2 处调用(3 处修改)**

line 103:
- old: `from astrbot_plugin_spcode_toolkit import main as _m`
- new: `from astrbot_plugin_spcode_toolkit.tools.webapi import _helpers as _helpers_mod`

line 107 与 line 116 的 `_m._run_git_async(...)`:
- old: `await _m._run_git_async(...)`
- new: `await _helpers_mod._run_git_async(...)`

- [ ] **Step 4: 跑测试验证 green**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_run_git_async.py -v`
Expected: 全部测试 PASS(基线 6 passed)。

- [ ] **Step 5: 跑项目全量测试,确认未破坏其他文件**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/ -q`
Expected:大部分 PASS;**`tests/test_git_diff.py` 可能 fail**,因为它的 monkeypatch 仍指向 main.py 的 re-export 占位 → 实际指向 `tools.webapi._helpers._run_git_async`(通过 re-export shim),monkeypatch 改 main.py 符号,**不影响 _helpers 模块内部符号**,所以 test_git_diff monkeypatch 暂时失效,测试会 fail。**这是预期,Chunk 4 处理**。在本 Task Step 5 提交时,记录此 baseline break。

- [ ] **Step 6: 提交**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tests/test_run_git_async.py
git commit -m "test(webapi): point test_run_git_async imports to webapi/_helpers (7 sites)"
```

---

## Task 1.5: 把 `class _JSONResponseCompat` 移到 `tools/webapi/_helpers.py`(TDD: test-first)

**Files:**
- Create: `tools/webapi/_helpers.py` (添加 class)
- Create: `tests/test_webapi_helpers_smoke.py` (扩展)
- Modify: `main.py` (line 415 删除 class 定义)

- [ ] **Step 1: 读 `main.py` 中 `_JSONResponseCompat` 完整定义**

Read: `main.py` line 415 附近
Expected: `class _JSONResponseCompat(JSONResponse):` + 完整类体。**记录 import 依赖**(`from astrbot.api.web import JSONResponse`)。

- [ ] **Step 2: 写一个最小失败的 import 测试(red)**

在 `tests/test_webapi_helpers_smoke.py` 末尾添加:
```python
def test_json_response_compat_importable_from_webapi_helpers():
    """`_JSONResponseCompat` 必须能 import 且继承自某个基类。"""
    from tools.webapi._helpers import _JSONResponseCompat
    assert _JSONResponseCompat is not None
    assert isinstance(_JSONResponseCompat, type)  # 是类
    # 基类检查:JSONResponse 是 starlette/fastapi 的标准基类
    from astrbot.api.web import JSONResponse
    assert issubclass(_JSONResponseCompat, JSONResponse)
```

- [ ] **Step 3: 跑测试,确认 red**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_webapi_helpers_smoke.py::test_json_response_compat_importable_from_webapi_helpers -v`
Expected: FAIL,`ImportError: cannot import name '_JSONResponseCompat' from 'tools.webapi._helpers'`。

- [ ] **Step 4: 在 `tools/webapi/_helpers.py` 添加 class 实现**

在 `tools/webapi/_helpers.py` 末尾添加:
```python
from astrbot.api.web import JSONResponse  # 实际 import,不是 TYPE_CHECKING


class _JSONResponseCompat(JSONResponse):
    """JSONResponse 兼容 shim:某些 AstrBot 版本不接受 headers kw。

    在 main.py 时期被 webapi handler 4 处使用(详见 spec §4.2)。
    搬迁到本文件以避免 webapi → main 反向 import 循环。
    """
    # 复制 main.py 原类的完整类体(从 main.py line 415 起复制)
    def __init__(self, content=None, status_code: int = 200, headers: dict | None = None, **kwargs):
        # ... 实际类体从 main.py 复制 ...
        ...
```

**重要**:Step 4 的代码块**必须**包含从 `main.py` line 415 起的完整类体(所有 `__init__` / `render` / `__call__` 等方法)。**完整复制**,不做任何修改。

- [ ] **Step 5: 跑测试,确认 green**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_webapi_helpers_smoke.py -v`
Expected: PASS,2 passed(原 `_run_git_async` 1 个 + 新 `_JSONResponseCompat` 1 个)。

- [ ] **Step 6: 从 main.py 删除 `class _JSONResponseCompat` 定义 + 临时 re-export**

- 删除 main.py line 415 的 class 定义
- main.py 顶部(Task 1.3 加的 re-export 之后)添加 re-export:
  ```python
  from tools.webapi._helpers import _JSONResponseCompat  # TEMPORARY,removed in Chunk 5
  ```

- [ ] **Step 7: 验证 main.py 仍可 import + 类仍可被引用**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from astrbot_plugin_spcode_toolkit import main; print(main._JSONResponseCompat.__module__)"`
Expected: 打印 `tools.webapi._helpers`。

- [ ] **Step 8: 提交**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/_helpers.py tests/test_webapi_helpers_smoke.py main.py
git commit -m "refactor(webapi): move _JSONResponseCompat to webapi/_helpers.py (TEMP shim, removed in Chunk 5)"
```

---

## Task 1.6: 验收 Chunk 1

- [ ] **Step 1: 跑 lint**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && ruff check tools/webapi/ main.py`
Expected: 0 error。

- [ ] **Step 2: 跑 Chunk 1 涉及测试**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_run_git_async.py tests/test_webapi_helpers_smoke.py -v`
Expected: 全部 PASS。

- [ ] **Step 3: 验证文件状态**

Run:
```bash
cd F:\github\astrbot_plugin_spcode_toolkit
grep -nE "^async def _run_git_async|^class _JSONResponseCompat" main.py
```
Expected: **0 命中**。

Run: `ls tools/webapi/`
Expected: 2 个文件(`__init__.py` + `_helpers.py`)+ `test_webapi_helpers_smoke.py` 在 tests/。

- [ ] **Step 4: 提交(如果还没有)**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git status  # 应该 clean
```

---

# Chunk 1 Summary

| 验收项 | 目标 | 验证 |
|--------|------|------|
| `tools/webapi/_helpers.py` 存在 | ✓ | `ls tools/webapi/` |
| `_run_git_async` 在 webapi/_helpers | 1 | `grep "async def _run_git_async" tools/webapi/_helpers.py` |
| `_JSONResponseCompat` 在 webapi/_helpers | 1 | `grep "class _JSONResponseCompat" tools/webapi/_helpers.py` |
| main.py 已删除 2 个定义 | 0 | `grep -E "^(async )?def _run_git_async\|^class _JSONResponseCompat" main.py` |
| test_run_git_async 改 import | 6/6 | `grep -c "from .*webapi._helpers" tests/test_run_git_async.py` |
| ruff + pytest | pass | ruff + pytest |

**Chunk 1 → Chunk 2 衔接**:进入 Chunk 2 前,确认 main.py 顶部 4 行 re-export 占位仍存在(`_run_git_async` + `_JSONResponseCompat`);这些占位将在 Chunk 4(git_diff 调用 `_run_git_async` 改 Pattern B)和 Chunk 3(file_browser 改 Pattern B)完成后,在 Chunk 5 集中删除。

---

# Chunk 2: 3 个小端点(`project_status` / `plan_mode` / `git_worktrees`)+ `__init__.py` 基础

**Goal:** 把 `main.py` 中 3 个最小端点(`handle_get_project_status` / `handle_get_plan_mode` / `handle_get_git_worktrees`)+ 1 个 git_worktrees 专用辅助(`_make_git_worktrees_empty_envelope`) 搬到 `tools/webapi/`;`tools/webapi/__init__.py` 导出 `HANDLERS` 字典供测试用(不包含 `register_webapi_routes`,后者在 Chunk 5)。

**可独立验证**:`pytest tests/test_plan_mode.py tests/test_git_worktrees.py tests/test_project_status.py -v` 全绿;`grep -nE "def handle_get_(project_status|plan_mode|git_worktrees)\b" main.py` 0 命中。

---

## Task 2.1: 搬移 `handle_get_project_status` → `tools/webapi/project_status.py`

**Files:**
- Create: `tools/webapi/project_status.py`
- Modify: `main.py` (删除 `handle_get_project_status` 方法,约 2516-2640 行)

- [ ] **Step 1: 读 main.py 中 `handle_get_project_status` 完整实现**

Read: `main.py` line 2516-2640 周围(项目状态端点)
**记录**:
- 方法签名(可能有 `self, umo=None, ...` 参数)
- 内部访问的 `self.xxx` 属性(改造时改 `plugin.xxx`)
- 任何 `from tools._helpers import` 引用

- [ ] **Step 2: 写 failing test**

```python
# tests/test_project_status.py(新文件,仅 1 个 smoke test)
import pytest
from tests.conftest import handlers


async def test_handle_get_project_status_returns_dict(plugin):
    """project_status 端点返回 dict(只检查不抛错,内容由 dashboard 端到端覆盖)。"""
    result = await handlers["handle_get_project_status"](plugin, umo="test-umo")
    assert isinstance(result, dict)
    assert "status" in result
```

- [ ] **Step 3: 跑测试 → red**

Run: `pytest tests/test_project_status.py -v`
Expected: FAIL(`KeyError: 'handle_get_project_status'` 或 `AttributeError: module 'tools.webapi' has no attribute 'HANDLERS'`)。

- [ ] **Step 4: 在 `tools/webapi/__init__.py` 导出 `HANDLERS` 字典骨架**

在 `tools/webapi/__init__.py` 添加:
```python
HANDLERS: dict[str, object] = {
    "handle_get_project_status": None,  # Task 2.5 step 3 替换
}
```

- [ ] **Step 5: 创建 `tools/webapi/project_status.py`(模块级 handler)**

```python
# tools/webapi/project_status.py
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
) -> dict[str, Any]:
    """GET /spcode/project-status 的处理器。
    
    从 main.py handle_get_project_status(line 2516-2640)整体迁移。
    """
    # ... 完整代码从 main.py 复制,所有 self.xxx 改为 plugin.xxx ...
    ...
```

- [ ] **Step 6: 在 `HANDLERS` 注册新 handler**

Edit `tools/webapi/__init__.py`:
- old: `"handle_get_project_status": None,`
- new: `"handle_get_project_status": __import__("tools.webapi.project_status", fromlist=["handle"]).handle,`

或更简单,直接 import:
```python
from tools.webapi.project_status import handle as handle_get_project_status
```

然后 `HANDLERS = {"handle_get_project_status": handle_get_project_status, ...}`。

- [ ] **Step 7: 从 main.py 删除原方法**

Edit `main.py`:删除 `async def handle_get_project_status(self, ...) -> dict:` 完整方法(约 100 行)。

- [ ] **Step 8: 跑测试 → green**

Run: `pytest tests/test_project_status.py -v`
Expected: PASS。

- [ ] **Step 9: 跑全量测试,确认 main.py 调用点未引用此方法**

Run: `pytest tests/ -q -k "not test_git_diff and not test_file_restore"`(临时跳过 git_diff/file_restore,因 Chunk 1 re-export 仍生效)
Expected: PASS。

- [ ] **Step 10: 提交**

```bash
git add tools/webapi/__init__.py tools/webapi/project_status.py tests/test_project_status.py main.py
git commit -m "refactor(webapi): move handle_get_project_status to webapi/project_status.py"
```

---

## Task 2.2: 搬移 `handle_get_plan_mode` → `tools/webapi/plan_mode.py`

**Files:**
- Create: `tools/webapi/plan_mode.py`
- Modify: `main.py` (删除 `handle_get_plan_mode` 方法,约 2642-2765 行)
- Modify: `tests/conftest.py` (确保 `handlers` fixture 暴露新 handler)
- Modify: `tests/test_plan_mode.py` (5 处 `plugin.handle_get_plan_mode()` → `handlers["handle_get_plan_mode"](plugin)`)

- [ ] **Step 1: 读 main.py 中 `handle_get_plan_mode` 完整实现 + 现有测试用法**

Read: `main.py` line 2642-2765 + `tests/test_plan_mode.py` 5 处调用
**记录**:
- 方法签名
- 现有测试用 `asyncio.run(plugin.handle_get_plan_mode())` 调用(注意是 `asyncio.run`,同步,不是 `await`)

- [ ] **Step 2: 写 1 个额外的 failing test(sanity)**

在 `tests/test_project_status.py` 末尾添加(临时),验证 plan_mode 在 `HANDLERS`:
```python
def test_plan_mode_in_handlers_dict():
    from tools.webapi import HANDLERS
    assert "handle_get_plan_mode" in HANDLERS
    assert HANDLERS["handle_get_plan_mode"] is not None
```

- [ ] **Step 3: red → 创建 `tools/webapi/plan_mode.py` + 注册 → green**

执行模式同 Task 2.1 Step 4-8:
- 创建 `plan_mode.py`(`handle(plugin, *, umo=None) -> dict`,完整代码从 main.py 复制,`self` → `plugin`)
- 在 `HANDLERS` 注册
- main.py 删除原方法

- [ ] **Step 4: 改造 `tests/test_plan_mode.py` 5 处调用**

对每行 `await plugin.handle_get_plan_mode()`(实际是 `asyncio.run(plugin.handle_get_plan_mode())`):
- old: `asyncio.run(plugin.handle_get_plan_mode())`
- new: `asyncio.run(handlers["handle_get_plan_mode"](plugin))`

(在文件顶部加 `from tests.conftest import handlers`)

- [ ] **Step 5: 跑测试 → green**

Run: `pytest tests/test_plan_mode.py -v`
Expected: 5/5 PASS(基线)。

- [ ] **Step 6: 提交**

```bash
git add tools/webapi/plan_mode.py tests/test_plan_mode.py main.py tools/webapi/__init__.py
git commit -m "refactor(webapi): move handle_get_plan_mode to webapi/plan_mode.py + test migration"
```

---

## Task 2.3: 搬移 `handle_get_git_worktrees` + `_make_git_worktrees_empty_envelope`

**Files:**
- Create: `tools/webapi/git_worktrees.py`
- Modify: `main.py` (删除 `handle_get_git_worktrees` 方法,约 2676-2790 行;删除 `_make_git_worktrees_empty_envelope` 辅助,约 750-770 行)
- Modify: `tests/test_git_worktrees.py` (10+ 处调用改造)

- [ ] **Step 1: 读 main.py 中 handler + 辅助 + 测试用法**

Read: `main.py` line 2676-2790(handlelr) + line 750-770(envelope helper) + `tests/test_git_worktrees.py` 调用模式
**记录**:
- handler 接受哪些 query 参数(`umo` / `worktree` 等)
- envelope helper 签名(可能无参,返回固定 dict)
- 测试是否使用 `asyncio.run` 同步

- [ ] **Step 2: failing test 验证 HANDLERS 存在 + handler 工作**

在 `tests/test_git_worktrees.py` 顶部加 `from tests.conftest import handlers`,然后改造 1 个 test:
```python
# Before
async def test_git_worktrees_basic(plugin, tmp_path):
    result = await plugin.handle_get_git_worktrees()
    ...

# After(注意:asyncio.run 同步风格)
async def test_git_worktrees_basic(plugin, tmp_path):
    result = await handlers["handle_get_git_worktrees"](plugin)
    ...
```

跑 → red(`KeyError`)。

- [ ] **Step 3: 创建 `tools/webapi/git_worktrees.py`**

```python
# tools/webapi/git_worktrees.py
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    """GET /spcode/git-worktrees 的处理器。"""
    # 完整代码从 main.py 复制,self → plugin
    ...


def _make_git_worktrees_empty_envelope(directory: str, ...) -> dict[str, Any]:
    """main.py line 750-770 整体迁移。"""
    ...
```

- [ ] **Step 4: 在 HANDLERS 注册**

- [ ] **Step 5: 从 main.py 删除 handler + 辅助**

- [ ] **Step 6: 跑 → green**

Run: `pytest tests/test_git_worktrees.py -v`
Expected:全部 PASS(基线)。

- [ ] **Step 7: 提交**

```bash
git add tools/webapi/git_worktrees.py tests/test_git_worktrees.py main.py tools/webapi/__init__.py
git commit -m "refactor(webapi): move handle_get_git_worktrees + _make_git_worktrees_empty_envelope"
```

---

## Task 2.4: 验收 Chunk 2

- [ ] **Step 1: 验证 main.py 已删 3 个 handler + 1 个辅助**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -nE "def handle_get_(project_status|plan_mode|git_worktrees)\b" main.py`
Expected: 0 命中。

Run: `grep -nE "^def _make_git_worktrees_empty_envelope" main.py`
Expected: 0 命中。

- [ ] **Step 2: 跑相关测试**

Run: `pytest tests/test_project_status.py tests/test_plan_mode.py tests/test_git_worktrees.py -v`
Expected: 全部 PASS。

- [ ] **Step 3: ruff lint**

Run: `ruff check tools/webapi/`
Expected: 0 error。

- [ ] **Step 4: main.py 行数监控**

Run: `wc -l main.py`
Expected: ≤ 4300 行(已减约 250-300 行)。

---

# Chunk 3: `file_browser` + `file_restore` 端点

**Goal:** 搬移 `handle_get_file_browser` + 12 个 file_browser 辅助 + `handle_post_file_restore` + 4 个 file_restore 辅助 + file_browser 1 处 `_run_git_async` 调用(file_restore 3 处 Pattern B 改造)。

**可独立验证**:`pytest tests/test_file_browser.py tests/test_file_restore.py -v` 全绿;`grep -nE "def handle_get_file_browser|def handle_post_file_restore" main.py` 0 命中。

---

## Task 3.1: 搬移 `handle_get_file_browser` + 12 个辅助

**Files:**
- Create: `tools/webapi/file_browser.py`
- Modify: `main.py` (删除 `handle_get_file_browser` ~470 行 + 12 个辅助函数 + 1 处 `_make_304_response` 中的 `_JSONResponseCompat` 调用)

- [ ] **Step 1: 读 main.py line 3991-4460(handle + 12 辅助)**

Read: `main.py` line 3991-4460
**记录**:
- handler 签名(可能接受 `path` query)
- 12 个辅助函数名 + 行号
- `_JSONResponseCompat` 的 2 处调用点(file_browser 内部)

- [ ] **Step 2: failing test**

在 `tests/test_file_browser.py` 改 1 个 test 调用 + 头部加 `from tests.conftest import handlers`:
```python
# Before
async def test_file_browser_returns_file_content(plugin, tmp_path):
    result = await plugin.handle_get_file_browser(path=str(tmp_path / "a.txt"))
    ...

# After
async def test_file_browser_returns_file_content(plugin, tmp_path):
    result = await handlers["handle_get_file_browser"](plugin, path=str(tmp_path / "a.txt"))
    ...
```

跑 → red。

- [ ] **Step 3: 创建 `tools/webapi/file_browser.py`**

```python
# tools/webapi/file_browser.py
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit

# Pattern B:_JSONResponseCompat 从 webapi/_helpers 引用,保持 module-globally-bound
from . import _helpers  # noqa: F401


async def handle(
    plugin: "SPCodeToolkit",
    *,
    path: str = "",
    if_none_match: str | None = None,
) -> dict[str, Any]:
    """GET /spcode/file-browser 的处理器。"""
    # 完整代码从 main.py 复制,self → plugin
    # 所有 _JSONResponseCompat(...) → _helpers._JSONResponseCompat(...)
    ...


# 12 个辅助函数(从 main.py 整体复制,包括 _make_304_response)
def _make_304_response(headers: dict[str, str]):
    return _helpers._JSONResponseCompat({}, status_code=304, headers=headers)

# ... 其他 11 个辅助 ...
```

**关键**:所有 `_JSONResponseCompat(...)` 调用改为 `_helpers._JSONResponseCompat(...)`(Pattern B),包括 `_make_304_response` 内的 line 412。

- [ ] **Step 4: 在 HANDLERS 注册**

- [ ] **Step 5: 从 main.py 删除 handler + 12 辅助**

- [ ] **Step 6: 跑 → green**

Run: `pytest tests/test_file_browser.py -v`
Expected:全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add tools/webapi/file_browser.py tests/test_file_browser.py main.py tools/webapi/__init__.py
git commit -m "refactor(webapi): move file_browser handler + 12 helpers to webapi/file_browser.py"
```

---

## Task 3.2: 搬移 `handle_post_file_restore` + 4 辅助 + 3 处 `_run_git_async` Pattern B 改造

**Files:**
- Create: `tools/webapi/file_restore.py`
- Modify: `main.py` (删除 `handle_post_file_restore` ~320 行 + 4 辅助 + 3 处 `await _run_git_async(...)` 改 `_helpers._run_git_async(...)`)
- Modify: `tests/test_file_restore.py` (~30 处调用改造 + 删除 `_patch_post_body` helper)

- [ ] **Step 1: 读 main.py line 4100-4460(handler + 4 辅助)**

Read: `main.py` line 4100-4460
**记录**:
- handler 签名(接受 `umo` / `worktree` query,body 含 `file`)
- 4 辅助函数名 + 行号
- 3 处 `await _run_git_async(...)`(line 4174/4236/4348)

- [ ] **Step 2: failing test**

在 `tests/test_file_restore.py` 改 1 个 test + 头部加 `from tests.conftest import handlers`:
```python
# Before
async def test_file_restore_basic(plugin, tmp_path, monkeypatch):
    _patch_post_body(monkeypatch, body={"file": "a.txt"})
    result = await plugin.handle_post_file_restore(umo="...")
    ...

# After
async def test_file_restore_basic(plugin, tmp_path):
    result = await handlers["handle_post_file_restore"](plugin, body={"file": "a.txt"}, umo="...")
    ...
```

跑 → red。

- [ ] **Step 3: 创建 `tools/webapi/file_restore.py`**

```python
# tools/webapi/file_restore.py
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit

from . import _helpers  # noqa: F401 — Pattern B


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST /spcode/file-restore 的处理器。"""
    # 完整代码从 main.py 复制
    # 3 处 await _run_git_async(...) 改为 await _helpers._run_git_async(...)
    # body = body or {}  # 安全默认值
    ...


# 4 辅助
def _make_file_restore_empty_envelope(...):
    ...

def _make_file_restore_success_envelope(...):
    ...

def _validate_restore_file(...):
    ...
```

- [ ] **Step 4: 在 HANDLERS 注册**

- [ ] **Step 5: 从 main.py 删除 handler + 4 辅助(本 Task 不改 main.py 中的 `_run_git_async` 调用)**

**关键**:本 Task Step 5 整段删除 `handle_post_file_restore` 方法体(含 3 处 `await _run_git_async(...)` 调用),不需要在 main.py 中改 Pattern B —— 调用点随 handler 整体移到 `tools/webapi/file_restore.py`,在 Chunk 3 同一 Task 的 Step 3 创建 file_restore.py 时已用 Pattern B 改写。main.py 在 Step 5 之后应无 `_run_git_async` 调用残留。

- [ ] **Step 6: 跑 → green**

Run: `pytest tests/test_file_restore.py -v`
Expected:全部 PASS。

- [ ] **Step 7: 删除 `_patch_post_body` helper + 全面改造 test_file_restore.py 调用**

对每行:
- 删除 `_patch_post_body(monkeypatch, body={...})` 之前的 monkeypatch.setattr
- 改为直接传 `body={...}` 到 `handlers["handle_post_file_restore"](plugin, body=..., umo=...)`
- 删除 `_patch_post_body` 函数定义(行 66-83)

- [ ] **Step 8: 跑 → green(二次验证)**

Run: `pytest tests/test_file_restore.py -v`
Expected:全部 PASS。

- [ ] **Step 9: 提交**

```bash
git add tools/webapi/file_restore.py tests/test_file_restore.py main.py tools/webapi/__init__.py
git commit -m "refactor(webapi): move file_restore + 4 helpers + Pattern B for _run_git_async"
```

---

## Task 3.3: 验收 Chunk 3

- [ ] **Step 1: 验证 main.py 已删 2 handler + 16 辅助**

Run:
```bash
cd F:\github\astrbot_plugin_spcode_toolkit
grep -nE "def handle_get_file_browser|def handle_post_file_restore" main.py
grep -nE "^def (_compute_file_etag|_common_cache_headers|_get_if_none_match|_make_304_response|_build_error_response|_classify_entry|_safe_lstat_mtime|_make_entry|_build_file_response|_classify_oserror|_build_directory_response|_build_symlink_response|_make_file_restore_empty_envelope|_make_file_restore_success_envelope|_validate_restore_file|_make_git_worktrees_empty_envelope)\(" main.py
```
Expected: 全部 0 命中(注意 git_worktrees 已经在 Chunk 2 删除)。

- [ ] **Step 2: 跑 file_browser + file_restore 测试**

Run: `pytest tests/test_file_browser.py tests/test_file_restore.py -v`
Expected:全部 PASS。

- [ ] **Step 3: ruff lint**

Run: `ruff check tools/webapi/`
Expected: 0 error。

- [ ] **Step 4: main.py 行数监控**

Run: `wc -l main.py`
Expected: ≤ 3600 行。

---

# Chunk 4: `git_diff` 端点(最大)

**Goal:** 搬移 `handle_get_git_diff` + 7 个 git-diff 专用辅助 + 3 个 git-diff 模块级常量 + 4 处 `_run_git_async` Pattern B 改造。

**可独立验证**:`pytest tests/test_git_diff.py tests/test_git_diff_worktree.py -v` 全绿;`grep -nE "def handle_get_git_diff|^(MAX_GIT_DIFF_BYTES|_GIT_DIFF_ENCODING|_DIFF_ETAG_CACHE_MAX)" main.py` 0 命中。

---

## Task 4.1: 搬移 `handle_get_git_diff` + 7 辅助 + 3 常量 + 4 处 Pattern B

**Files:**
- Create: `tools/webapi/git_diff.py`
- Modify: `main.py` (删除 `handle_get_git_diff` ~243 行 + 7 辅助 + 3 常量 + 4 处 `await _run_git_async(...)` 改 Pattern B)
- Modify: `tests/test_git_diff.py` (15+ 处调用改造 + 4 处 monkeypatch 改路径)
- Modify: `tests/test_git_diff_worktree.py` (10+ 处调用改造)

- [ ] **Step 1: 读 main.py line 172-358 + 2803-3045**

Read: `main.py` line 172-358(7 辅助: `_parse_diff_status_map` / `_parse_numstat_counts` / `_build_stat_text` / `_compute_diff_etag` / `_make_git_diff_empty_envelope` / ...)+ line 2803-3045(handler)+ line 135-146(3 常量)+ 4 处 `_run_git_async` 调用(line 332/2941/2988/3002)
**记录**:
- 7 辅助完整函数体
- 3 常量值
- 4 处调用行号 + 参数

- [ ] **Step 2: failing test 验证 HANDLERS + 改 1 个 test**

在 `tests/test_git_diff.py` 头部加 `from tests.conftest import handlers`,改 1 个 test:
```python
# Before
result = await plugin.handle_get_git_diff()

# After
result = await handlers["handle_get_git_diff"](plugin)
```

跑 → red。

- [ ] **Step 3: 创建 `tools/webapi/git_diff.py`**

```python
# tools/webapi/git_diff.py
from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from main import SPCodeToolkit

from . import _helpers  # noqa: F401 — Pattern B
from .._helpers import detect_console_encoding  # 复用 tools._helpers.detect_console_encoding

# 3 个 git-diff 专用常量
MAX_GIT_DIFF_BYTES = 1 * 1024 * 1024
_GIT_DIFF_ENCODING = detect_console_encoding()
_DIFF_ETAG_CACHE_MAX = 64


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    scope: str = "unstaged",
) -> dict[str, Any]:
    """GET /spcode/git-diff 的处理器。"""
    # 完整代码从 main.py 复制
    # 4 处 await _run_git_async(...) → await _helpers._run_git_async(...)
    # 所有 MAX_GIT_DIFF_BYTES / _GIT_DIFF_ENCODING 保持原引用
    ...


# 7 辅助函数
def _parse_diff_status_map(text: str) -> dict[str, str]:
    ...

# ... 其他 6 个 ...
```

- [ ] **Step 4: 在 HANDLERS 注册**

- [ ] **Step 5: 从 main.py 删除 handler + 7 辅助 + 3 常量 + 4 处 Pattern B**

**关键**:
- 删除 line 135-146 的 3 个常量定义
- 删除 7 个辅助函数(行号依 §4.1)
- 删除 `handle_get_git_diff` 方法体(243 行)
- 4 处 `await _run_git_async(...)` 改为 `await _webapi_helpers_run_git._run_git_async(...)`(利用 Chunk 1 re-export)

- [ ] **Step 6: 跑 test_git_diff.py → green(初次)**

Run: `pytest tests/test_git_diff.py -v`
Expected: 15+ passed。

- [ ] **Step 7: 改造 test_git_diff.py **7 处** monkeypatch 路径(精确行号以 `grep` 为准)**

先用 grep 定位全部 monkeypatch:
```bash
cd F:\github\astrbot_plugin_spcode_toolkit
grep -nE 'monkeypatch.setattr\(.*"_run_git_async"' tests/test_git_diff.py
```
Expected: 7 行命中(`main_mod` alias 3 处 + `_m` alias 4 处)。

```python
# Before(无论是 main_mod 还是 _m,均统一替换)
from astrbot_plugin_spcode_toolkit import main as main_mod  # 或:from ... import main as _m
monkeypatch.setattr(main_mod, "_run_git_async", fake)  # 或:monkeypatch.setattr(_m, "_run_git_async", fake)

# After
from astrbot_plugin_spcode_toolkit.tools.webapi import _helpers as _webapi_helpers
monkeypatch.setattr(_webapi_helpers, "_run_git_async", fake)
```

**注意**:此 Step 只改 `_run_git_async` 的 monkeypatch target,不动 `run_cmd` 的 monkeypatch(因为 `run_cmd` 在 tools/_helpers.py,不在 webapi 范围)。

- [ ] **Step 8: 改造 test_git_diff.py 15+ 处调用**

所有 `await plugin.handle_get_git_diff(...)` 改为 `await handlers["handle_get_git_diff"](plugin, ...)`。

- [ ] **Step 9: 改造 test_git_diff_worktree.py 10+ 处调用**

同上模式,注意 `worktree=` 关键字参数。

- [ ] **Step 10: 跑 → green(二次验证)**

Run: `pytest tests/test_git_diff.py tests/test_git_diff_worktree.py -v`
Expected:全部 PASS。

- [ ] **Step 11: 提交**

```bash
git add tools/webapi/git_diff.py tests/test_git_diff.py tests/test_git_diff_worktree.py main.py tools/webapi/__init__.py
git commit -m "refactor(webapi): move git_diff handler + 7 helpers + 3 constants + Pattern B"
```

---

## Task 4.2: 验收 Chunk 4

- [ ] **Step 1: 验证 main.py 已删 1 handler + 7 辅助 + 3 常量**

Run:
```bash
cd F:\github\astrbot_plugin_spcode_toolkit
grep -nE "def handle_get_git_diff\b" main.py
grep -nE "^def (_parse_diff_status_map|_parse_numstat_counts|_build_stat_text|_compute_diff_etag|_make_git_diff_empty_envelope|_compute_file_etag)\(" main.py
grep -nE "^(MAX_GIT_DIFF_BYTES|_GIT_DIFF_ENCODING|_DIFF_ETAG_CACHE_MAX)\s*=" main.py
grep -nE "await _run_git_async" main.py
```
Expected: 第 1-3 行 0 命中;第 4 行 **0 命中**(Chunk 3 已将 file_restore 全部搬走,Chunk 4 已将 git_diff 全部搬走,main.py 中应无残留)。

- [ ] **Step 2: 跑 git_diff 相关测试**

Run: `pytest tests/test_git_diff.py tests/test_git_diff_worktree.py -v`
Expected:全部 PASS。

- [ ] **Step 3: ruff lint**

Run: `ruff check tools/webapi/`
Expected: 0 error。

- [ ] **Step 4: main.py 行数监控**

Run: `wc -l main.py`
Expected: ≤ 2200 行(已减约 1400 行 = 4378 - 6 端点 + 7 辅助 + 3 常量 + 1 跨端点 class 等)。

---

# Chunk 5: `_wrap` + `register_webapi_routes` + main.py 瘦身 + 全量验证

**Goal:** 完善 `tools/webapi/__init__.py` 的 `ROUTES` + `register_webapi_routes()` + `_wrap()` 适配层;替换 main.py 中 6 段 `register_web_api` 块;删除 4 行 re-export 占位;全量测试 + 验收。

**可独立验证**:`pytest tests/` 全绿;`wc -l main.py` ≤ 200;`ruff check .` 0 error;6 个端点 dashboard 端到端 smoke test 通过。

---

## Task 5.1: 实现 `_wrap()` 适配层

**Files:**
- Modify: `tools/webapi/__init__.py` (添加 `_wrap` 私有函数 + `ROUTES` + `register_webapi_routes`)

- [ ] **Step 1: 写 failing test 验证 `register_webapi_routes` 存在且能 mock 端点**

```python
# tests/test_webapi_helpers_smoke.py 末尾添加
def test_register_webapi_routes_callable():
    from tools.webapi import register_webapi_routes
    assert callable(register_webapi_routes)


def test_wrap_function_returns_async_callable():
    from tools.webapi import _wrap
    async def fake_handler(plugin, *, umo=None):
        return {"status": "ok", "umo": umo}
    wrapped = _wrap(fake_handler, plugin=None)
    import inspect
    assert inspect.iscoroutinefunction(wrapped) or asyncio.iscoroutinefunction(wrapped)
```

- [ ] **Step 2: red → green: 实现 `_wrap` + `register_webapi_routes`**

在 `tools/webapi/__init__.py` 添加以下代码(完整可粘贴,**不要省略**):

```python
# tools/webapi/__init__.py(替换之前的 HANDLERS 骨架)
import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from main import SPCodeToolkit

from tools.webapi import (
    file_browser,
    file_restore,
    git_diff,
    git_worktrees,
    plan_mode,
    project_status,
)

logger = logging.getLogger(__name__)

# (route, methods, handler, desc) —— 注册顺序敏感
ROUTES: list[tuple[str, list[str], Callable, str]] = [
    ("/spcode/project-status", ["GET"], project_status.handle,
     "获取 spcode 当前会话已加载的项目信息(供 dashboard 调用)"),
    ("/spcode/plan-mode", ["GET"], plan_mode.handle,
     "获取 spcode 当前 plan-mode 状态(只读)"),
    ("/spcode/git-worktrees", ["GET"], git_worktrees.handle,
     "列出已加载项目的 git worktree 列表"),
    ("/spcode/git-diff", ["GET"], git_diff.handle,
     "获取已加载项目的未暂存 git diff"),
    ("/spcode/file-browser", ["GET"], file_browser.handle,
     "读取文件内容或列出单层目录"),
    ("/spcode/file-restore", ["POST"], file_restore.handle,
     "恢复工作区中某一文件相对于 index 的改动"),
]

# 测试入口:旧方法名 -> 新模块级 handler
HANDLERS: dict[str, Callable] = {
    "handle_get_project_status": project_status.handle,
    "handle_get_plan_mode": plan_mode.handle,
    "handle_get_git_worktrees": git_worktrees.handle,
    "handle_get_git_diff": git_diff.handle,
    "handle_get_file_browser": file_browser.handle,
    "handle_post_file_restore": file_restore.handle,
}


def _wrap(handler: Callable, plugin: "SPCodeToolkit") -> Callable:
    """适配 view_handler 接口,把 request.* 参数映射到 handler 关键字参数。

    支持自动注入的字段(按 handler 签名声明):
    - umo:        GET  query.umo; POST body.umo
    - worktree:   GET  query.worktree; POST body.worktree
    - scope:      GET  query.scope(默认 "unstaged";git_diff 专用)
    - path:       GET  query.path(默认 "";file_browser 专用)
    - if_none_match: GET header.If-None-Match
    - body:       POST body 整体(作为 dict)
    """
    sig = inspect.signature(handler)
    accepts = set(sig.parameters) - {"plugin"}

    async def view(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.get("request") or (args[0] if args else None)
        call_kwargs: dict[str, Any] = {}

        if "umo" in accepts and request is not None:
            if request.method == "POST":
                try:
                    _body = await request.json() or {}
                except Exception:
                    _body = {}
                call_kwargs["umo"] = _body.get("umo")
            else:
                call_kwargs["umo"] = request.query.get("umo") or None
        if "worktree" in accepts and request is not None:
            if request.method == "POST":
                if "body" not in call_kwargs:
                    try:
                        call_kwargs["body"] = await request.json() or {}
                    except Exception:
                        call_kwargs["body"] = {}
                call_kwargs["worktree"] = call_kwargs["body"].get("worktree")
            else:
                call_kwargs["worktree"] = request.query.get("worktree")
        if "scope" in accepts and request is not None:
            call_kwargs["scope"] = request.query.get("scope") or "unstaged"
        if "path" in accepts and request is not None:
            call_kwargs["path"] = request.query.get("path", "").strip()
        if "if_none_match" in accepts and request is not None:
            call_kwargs["if_none_match"] = request.headers.get("if-none-match")
        if "body" in accepts and request is not None and "body" not in call_kwargs:
            if request.method == "POST":
                try:
                    call_kwargs["body"] = await request.json() or {}
                except Exception:
                    call_kwargs["body"] = {}
            else:
                call_kwargs["body"] = {}

        return await handler(plugin, **call_kwargs)

    return view


def register_webapi_routes(plugin: "SPCodeToolkit") -> None:
    """统一注册所有 webapi 路由。供 main.py.initialize() 调用一次。"""
    for route, methods, handler, desc in ROUTES:
        try:
            plugin.context.register_web_api(
                route=route,
                view_handler=_wrap(handler, plugin),
                methods=methods,
                desc=desc,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"注册 spcode {route} web API 失败: {exc!s}")
```

- [ ] **Step 3: 跑 → green**

Run: `pytest tests/test_webapi_helpers_smoke.py -v`
Expected: 4 passed。

- [ ] **Step 4: 提交**

```bash
git add tools/webapi/__init__.py tests/test_webapi_helpers_smoke.py
git commit -m "feat(webapi): add _wrap() + register_webapi_routes() + ROUTES"
```

---

## Task 5.2: 改造 main.py:删除 6 段 `register_web_api` + 4 行 re-export 占位 + 调用 `register_webapi_routes`

**Files:**
- Modify: `main.py` (line 约 50 + 6 段 register 块 + 4 行 re-export)

- [ ] **Step 1: 定位 main.py 6 段 register 块**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -nE "register_web_api\(.*spcode" main.py`
Expected: 6 处。

- [ ] **Step 2: 替换 6 段为单次 `register_webapi_routes(self)`**

Edit main.py:
- 删除 6 段 `try/except register_web_api(route=..., view_handler=..., methods=..., desc=...)` 块
- 在 `initialize()` 末尾加:
  ```python
  from tools.webapi import register_webapi_routes
  register_webapi_routes(self)
  ```

- [ ] **Step 3: 删除 4 行 re-export 占位**

Edit main.py 顶部:
- 删除 `from tools.webapi import _helpers as _webapi_helpers_run_git`
- 删除 `_run_git_async = _webapi_helpers_run_git._run_git_async`
- 删除 `from tools.webapi._helpers import _JSONResponseCompat`
- 删除任何 _make_304_response 等 webapi 内部引用(应已随 Chunk 3 搬走)

- [ ] **Step 4: 验证 main.py 不再有 webapi 残留**

Run:
```bash
cd F:\github\astrbot_plugin_spcode_toolkit
grep -nE "register_web_api\(.*spcode" main.py
grep -nE "^def handle_(get|post)_" main.py
grep -nE "_run_git_async|_JSONResponseCompat" main.py
grep -nE "^(MAX_GIT_DIFF_BYTES|_GIT_DIFF_ENCODING|_DIFF_ETAG_CACHE_MAX)\s*=" main.py
```
Expected: 全部 0 命中。

- [ ] **Step 5: 跑全量测试 → green**

Run: `pytest tests/ -v`
Expected:全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add main.py
git commit -m "refactor(webapi): register all 6 routes via register_webapi_routes + clean shims"
```

---

## Task 5.3: main.py 验收 + ruff 验收 + 文档更新

**Files:**
- Modify: `README.md`(可选,提及 webapi 已拆分)
- Modify: `AGENTS.md`(更新 `tools/webapi/` 描述)

- [ ] **Step 1: 行数验证**

Run: `wc -l main.py`
Expected: ≤ 200 行。

- [ ] **Step 2: ruff 验证**

Run: `ruff check .`
Expected: 0 error。

- [ ] **Step 3: 端到端 smoke test(模拟 dashboard 行为)**

```python
# tests/test_webapi_end_to_end.py(新文件,可选)
import pytest
from unittest.mock import MagicMock
from tests.conftest import handlers


async def test_all_six_handlers_callable():
    """Smoke test: 6 个 handler 都可被调用且返回 dict。"""
    plugin = MagicMock()
    for name in handlers:
        result = await handlers[name](plugin)
        assert isinstance(result, dict)
```

Run: `pytest tests/test_webapi_end_to_end.py -v`
Expected: PASS。

- [ ] **Step 4: 更新 AGENTS.md 的"目录结构"段**

Edit `AGENTS.md` 的目录树 + 工具层说明,加入 `tools/webapi/` 描述。

- [ ] **Step 5: 跑全量 ruff + pytest 最终验证**

Run:
```bash
cd F:\github\astrbot_plugin_spcode_toolkit
ruff check .
pytest tests/ -q
```
Expected: ruff 0 error + pytest 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add AGENTS.md tests/test_webapi_end_to_end.py
git commit -m "docs: update AGENTS.md for tools/webapi/ + end-to-end smoke test"
```

---

## Task 5.4: 最终验收(spec §验收标准逐项)

- [ ] **Step 1: 验收矩阵**

| 指标 | 验证命令 | 期望 |
|------|----------|------|
| main.py ≤ 200 行 | `wc -l main.py` | ≤ 200 |
| 6 handler 全外迁 | `grep -nE "def handle_get_\|def handle_post_" main.py` | 0 命中 |
| 8 个 webapi 文件 | `ls tools/webapi/` | 8 文件 |
| 21 webapi 辅助全外迁 | `grep -E "^def (_parse_diff_status_map\|...)" main.py` | 0 命中(21 个) |
| 跨端点 `_run_git_async` 归位 | `grep "async def _run_git_async" main.py` + `grep "async def _run_git_async" tools/webapi/_helpers.py` | main 0,helpers 1 |
| 跨端点 `_JSONResponseCompat` 归位 | `grep "class _JSONResponseCompat" main.py` + `grep "class _JSONResponseCompat" tools/webapi/_helpers.py` | main 0,helpers 1 |
| 3 个常量全外迁 | `grep "^(MAX_GIT_DIFF_BYTES\|_GIT_DIFF_ENCODING\|_DIFF_ETAG_CACHE_MAX)" main.py` | 0 命中 |
| `_record` 保留 main.py | `grep "^def _record" main.py` | 1 命中 |
| tools/_helpers.py 未动 | `git diff tools/_helpers.py` | 空 |
| ruff | `ruff check .` | 0 error |
| pytest | `pytest tests/ -q` | 全部 PASS |

- [ ] **Step 2: 提交最终 commit(如需要)**

如果验收通过且无变更,不需要新 commit。

---

# Plan Complete

**总 commit 数**:14 个 commit(与 Implementation Order 表一致)
- Chunk 1: 5 commit(1.1/1.2/1.3/1.4/1.5)
- Chunk 2: 3 commit(2.1/2.2/2.3)
- Chunk 3: 2 commit(3.1/3.2)
- Chunk 4: 1 commit(4.1)
- Chunk 5: 3 commit(5.1/5.2/5.3)

**执行模式**:子代理驱动开发(subagent-driven-development),每 chunk 由 fresh subagent 执行,2 阶段 review(spec 路径 + 文件清单双重确认)。

