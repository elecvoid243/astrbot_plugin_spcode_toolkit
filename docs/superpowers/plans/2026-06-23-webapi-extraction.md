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

| Chunk | 内容 | 可独立验证 | 预计 commit 数 |
|-------|------|------------|---------------|
| **Chunk 1** | 共享层 `tools/webapi/_helpers.py` | ✅ `pytest tests/test_run_git_async.py` 通过 | 2 |
| **Chunk 2** | 3 个小端点(project_status/plan_mode/git_worktrees)+ `__init__.py` 骨架 | ✅ 对应测试通过 | 4 |
| **Chunk 3** | file_browser + file_restore | ✅ 对应测试通过 | 2 |
| **Chunk 4** | git_diff(大端点) | ✅ `test_git_diff*.py` 通过 | 1 |
| **Chunk 5** | `_wrap()` + `register_webapi_routes()` + main.py 瘦身 + 全量测试迁移 + 验收 | ✅ 全部 `pytest` 通过 + `wc -l main.py` ≤ 200 | 3 |

**总 commit 预算**:约 12 个,每个 chunk 内部细分(failing test → minimal impl → green → commit)。

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

## Task 1.3: 从 `main.py` 删除 `_run_git_async` 定义 + 处理 6 处调用点

**Files:**
- Modify: `main.py` (line 73-130 删除;2941/2988/3002 git_diff 调用;4174/4236/4348 file_restore 调用)

- [ ] **Step 1: 验证 main.py 当前调用 `_run_git_async` 的位置**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -nE "_run_git_async" main.py`
Expected: 看到 6 处 `await _run_git_async(...)` 调用(git_diff 3 处 + file_restore 3 处)+ 1 处 `async def _run_git_async` 定义。

> **重要**:此 Task **不**修改调用点代码(只删定义)。调用点改造属于 Chunk 4(git_diff)和 Chunk 3(file_restore)的 Pattern B 改造。本 Task 只确保 "删定义后调用点临时 fail 之前能跑通测试"。
>
> **本 Task 的实际工作**:仅在调用点临时加 `from .webapi import _helpers as _webapi_helpers` 等待 Chunk 3/4 处理;但更简洁的方案是**先 commit 删定义的失败,再在 Chunk 3/4 各自处理**。此处采用简洁方案。

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
git commit -m "refactor(webapi): remove _run_git_async from main.py, re-export from webapi/_helpers"
```

---

## Task 1.4: 迁移 `tests/test_run_git_async.py` import 路径

**Files:**
- Modify: `tests/test_run_git_async.py` (6 处 `from astrbot_plugin_spcode_toolkit.main import _run_git_async`)

- [ ] **Step 1: 定位全部 6 处 import**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && grep -nE "from astrbot_plugin_spcode_toolkit.main import _run_git_async" tests/test_run_git_async.py`
Expected: 6 行命中(行 25/39/52/64/75/82 附近)。

- [ ] **Step 2: 批量替换(逐行使用 `astrbot_file_edit_tool`)**

对每行 `from astrbot_plugin_spcode_toolkit.main import _run_git_async`:
- old: `from astrbot_plugin_spcode_toolkit.main import _run_git_async`
- new: `from astrbot_plugin_spcode_toolkit.tools.webapi._helpers import _run_git_async`

- [ ] **Step 3: 跑测试验证 green**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_run_git_async.py -v`
Expected: 全部测试 PASS(基线 6 passed)。

- [ ] **Step 4: 跑项目全量测试,确认未破坏其他文件**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/ -q`
Expected:全部 PASS(基线)。**`tests/test_git_diff.py` 可能 fail,因为它的 monkeypatch 仍指向 main.py 的 re-export 占位;如果 fail,记录在 CHANGELOG 但不修复(Chunk 4 处理)**。

- [ ] **Step 5: 提交**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tests/test_run_git_async.py
git commit -m "test(webapi): point test_run_git_async imports to webapi/_helpers"
```

---

## Task 1.5: 把 `class _JSONResponseCompat` 移到 `tools/webapi/_helpers.py`

**Files:**
- Create: `tools/webapi/_helpers.py` (添加 class)
- Modify: `main.py` (line 415 删除 class 定义;412/3023/4025/4042 调用点用 Pattern B 改造)

- [ ] **Step 1: 读 `main.py` 中 `_JSONResponseCompat` 完整定义**

Read: `main.py` line 415 附近
Expected: `class _JSONResponseCompat(JSONResponse):` + 完整类体。**记录 import 依赖**(`from astrbot.api.web import JSONResponse`)。

- [ ] **Step 2: 添加 `class _JSONResponseCompat` 到 `tools/webapi/_helpers.py`**

在 `tools/webapi/_helpers.py` 末尾添加:
```python
from astrbot.api.web import JSONResponse  # 实际 import,不是 TYPE_CHECKING


class _JSONResponseCompat(JSONResponse):
    """JSONResponse 兼容 shim:某些 AstrBot 版本不接受 headers kw。
    
    在 main.py 时期被 webapi handler 4 处使用(详见 spec §4.2)。
    搬迁到本文件以避免 webapi → main 反向 import 循环。
    """
    # 复制 main.py 原类的完整类体
    ...
```

具体类体内容请从 `main.py` line 415 复制,**不改**任何方法体。

- [ ] **Step 3: 写一个最小失败的 import 测试(red)**

在 `tests/test_webapi_helpers_smoke.py` 添加:
```python
def test_json_response_compat_importable_from_webapi_helpers():
    from tools.webapi import _helpers
    assert hasattr(_helpers, "_JSONResponseCompat")
    from tools.webapi._helpers import _JSONResponseCompat
    assert issubclass(_JSONResponseCompat, object)  # 简化基类检查
```

- [ ] **Step 4: 运行测试,确认 red → green**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && pytest tests/test_webapi_helpers_smoke.py -v`
Expected: PASS,2 passed。

- [ ] **Step 5: 从 main.py 删除 `class _JSONResponseCompat` 定义 + 临时 re-export**

同 Task 1.3 Step 2-3 模式:
- 删除 main.py line 415 的 class 定义
- main.py 顶部添加 re-export:
  ```python
  from tools.webapi._helpers import _JSONResponseCompat  # TEMPORARY
  ```

- [ ] **Step 6: 验证 main.py 仍可 import**

Run: `cd F:\github\astrbot_plugin_spcode_toolkit && python -c "from astrbot_plugin_spcode_toolkit import main; print(main._JSONResponseCompat.__module__)"`
Expected: 打印 `tools.webapi._helpers`。

- [ ] **Step 7: 提交**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/_helpers.py tests/test_webapi_helpers_smoke.py main.py
git commit -m "refactor(webapi): move _JSONResponseCompat to webapi/_helpers.py"
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
