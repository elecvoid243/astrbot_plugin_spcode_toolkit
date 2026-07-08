# Task 2 Report — file-discard-hunk / `_parse_patch_header` (纯函数 6 步防御)

**Implementer:** discard_hunk_impl_2
**Date:** 2026-07-06 21:29 (CST)
**Status:** DONE_WITH_CONCERNS
**Commit:** `fa136a4`
**Branch:** `feature/file-discard-hunk-impl`

---

## What was implemented

| 文件 | 状态 | 用途 |
| --- | --- | --- |
| `tools/webapi/file_discard_hunk.py` | **created** (139 行) | 模块头、imports、`MAX_PATCH_BYTES`、3 个 regex、`PatchMeta` dataclass、`_parse_patch_header` 函数 |
| `tests/test_file_discard_hunk.py` | **created** (133 行) | `TestParsePatchHeader` 类 — 11 个单元测试 |

`handle()` 与 envelope 工厂未添加(留给 Task 3+)。

## TDD 流程

1. ✅ 先写测试(Step 2.1) — `tests/test_file_discard_hunk.py` 11 tests
2. ✅ 跑测试确认 fail (Step 2.2) — `ImportError: cannot import name 'file_discard_hunk' from 'tools.webapi'`
3. ✅ 实现模块(Step 2.3)
4. ✅ 跑测试确认 pass (Step 2.4) — **11 passed**
5. ✅ 跑 ruff (Step 2.5) — **0 errors**
6. ✅ 提交(Step 2.6) — `git commit -m "feat(webapi): add _parse_patch_header for file-discard-hunk (6-step validation)"` → `fa136a4`

## 接口产出(Task 3+ 消费)

```python
from tools.webapi.file_discard_hunk import _parse_patch_header, PatchMeta
from tools.webapi.file_discard_hunk import MAX_PATCH_BYTES

@dataclass
class PatchMeta:
    hunk_count: int = 0
    source_path: str = ""
    target_path: str = ""
    err: str | None = None

def _parse_patch_header(patch_text: str, expected_file: str) -> PatchMeta: ...
```

`_parse_patch_header` 返回 `err` ∈ {`None`, `"patch_malformed"`, `"multi_file_patch"`, `"patch_unsafe_path"`, `"patch_file_mismatch"`, `"patch_binary"`}。6 步防御全部覆盖:

| 步骤 | 校验 | 失败 → err |
| --- | --- | --- |
| 1 | 必须含 `diff --git a/X b/X` 头 | `patch_malformed` |
| 2 | 必须仅含 1 个 `diff --git` 头 | `multi_file_patch` |
| 3 | a/b 路径不能含 `..` / 绝对 / `.git/` | `patch_unsafe_path` |
| 4 | b/ 路径 == `expected_file`(允许尾部 trim) | `patch_file_mismatch` |
| 5 | 不能含 `Binary files ... differ` | `patch_binary` |
| 6 | 必须含 ≥1 个 `@@ ... @@` hunk 头 | `patch_malformed` |

## 验证结果

```
$ pytest tests/test_file_discard_hunk.py::TestParsePatchHeader -v
...
11 passed, 2 warnings in 0.13s

$ ruff check tools/webapi/file_discard_hunk.py tests/test_file_discard_hunk.py
All checks passed!
```

---

## 偏离计划 / 修复的真实 Bug(CONCERNS)

为满足 "ruff check 必须通过" 要求,在不偏离计划语义的前提下做以下最小修改。**这些是计划文档中的真实 bug**:

### Concern 1: `_validate_worktree_param` 的相对导入路径错误(计划第 421 行)

**计划原文** (line 421 附近):
```python
from ._helpers import (
    X_TRULY_STAGED,
    Y_WORKTREE,
    ReasonCode,
    _run_git_async,
    _validate_repo_relative_file,
    _validate_worktree_param,  # ← 这里
)
```

**问题:** `_validate_worktree_param` 不在 `tools/webapi/_helpers.py` 里,而是在 `tools/_helpers.py`(父模块)。`tools/webapi/_helpers.py` 只在函数体内 lazy-import(`from .._helpers import _validate_worktree_param`,见 `tools/webapi/_helpers.py:286`)。

**触发:** ModuleNotFoundError 100% 复现 —— 我先按计划 verbatim 提交,`pytest` 立刻:
```
ImportError: cannot import name '_validate_worktree_param' from 'tools.webapi._helpers'
```

**修复:** 拆出独立 `from .._helpers import _validate_worktree_param`,其余保留 `from ._helpers import (...)`:
```python
from .._helpers import (
    _validate_worktree_param,
)
from ._helpers import (
    X_TRULY_STAGED,
    Y_WORKTREE,
    ReasonCode,
    _run_git_async,
    _validate_repo_relative_file,
)
```

### Concern 2: F401 unused imports(计划第 411–426 行模块导入块)

**问题:** 计划模块头 import 了 `time as _time`、`pathlib.Path`、`SPCodeToolkit`、`X_TRULY_STAGED`、`Y_WORKTREE`、`_run_git_async`、`_validate_repo_relative_file`、`_validate_worktree_param` —— 这些是 Task 3-5 的 `handle()` 才使用的,但 plan 要求在 Task 2 就引入。`ruff check` 开启 F401 时会全部报错。

**修复:** 最小偏离 —— 为每个 Task 2 暂未使用的符号加 `# noqa: F401  # used by handle() in Tasks 3-5`,并在 module-level 注释解释意图。`ReasonCode` 是 Task 2 真用的,不标注。

### Concern 3: 测试文件 `import pytest` 未使用(计划第 14 行)

**问题:** 计划写 `import pytest` 但 Task 2 的 11 个测试都不使用 pytest(无 `@pytest.fixture`、无 `pytest.raises`、无 parametrize)。`F401` 会报错。

**修复:** `# noqa: F401  # used by TestEnvelopes / TestHandler* in Tasks 3-6`。

### Concern 4: 测试文件导入块顺序(I001 isort)

**问题:** ruff 的 `I` 规则集(非默认)会把 plan 的 `from __future__ import annotations` 后 + 空行 + `import pytest` + 空行 + `from tools.webapi import ...` 视作缺排序。`ruff check`(默认规则集 E+F)**不报**;但 plan 写的是 `ruff check` 不带 `--select I`,实际不触发。可选 concern。

**修复:** 把 `import pytest` 后的空行去掉,与 next `from tools.webapi import ...` 视为同一 import block。

---

## 关于计划 verbatim 原则

| 改动 | 类别 | 大小 |
| --- | --- | --- |
| `_validate_worktree_param` 拆出 import | Bug 修复 | 2 行 |
| 8 个 `# noqa: F401` 注释 | Lint 必需 | 8 行 |
| `import pytest` 加 `# noqa: F401` | Lint 必需 | 1 行 |
| `import pytest` 后去一行空行 | Lint 必需 | 删除 1 行 |
| 测试函数体、断言、`_parse_patch_header` 实现本身 | 100% verbatim | 0 行变更 |

`_parse_patch_header` 函数体与所有测试断言一字未改。

## 提请 Task 3+ 注意

1. **Tasks 3-5 移除 `# noqa: F401`** 当对应符号真正被使用时。`grep -n 'noqa: F401' tools/webapi/file_discard_hunk.py` 列出了 8 处。
2. **`_validate_worktree_param` 已在模块顶导入**(而非 lazy)——与 `tools/webapi/_helpers.py:286` 中的 lazy 风格不一致,但 Task 2+ 不需要按 lazy 模式。如要统一,后续可改。
3. **`MAX_PATCH_BYTES = 256 * 1024`** 已就位 —— Task 3 body validation 直接引用。
4. **`_DIFF_HEADER_RE` / `_HUNK_HEADER_RE` / `_BINARY_RE`** 已为 module-level —— Task 3+ 的 scope detection(如需扩展)直接复用。

---

**END OF REPORT**
