# file_remove 改用系统回收站 + 启用时注入 LLM 指引 实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `astrbot_file_remove_tool` 的删除从物理删除改为送入系统回收站,并在该工具启用时向 LLM 系统提示词末尾注入一句简短指引,鼓励优先使用该工具。

**Architecture:**
1. 删除实现替换 `os.remove/os.rmdir` 为单次 `send2trash.send2trash(str(p))`(目录整体原子操作,移除 `deleted_paths`/`errors` 部分失败字段)
2. 新增 `@filter.on_llm_request()` 钩子 `_file_remove_inject_guidance`,用 `_FILE_REMOVE_GUIDANCE_MARKER` 防止重复注入;gate 由 `self._tool_names` 承担,无需新增 feature flag
3. 测试用 `pytest.fixture(autouse=True)` 默认 monkeypatch `send2trash`,成功路径用例显式断言 mock 调用

**Tech Stack:** Python 3.10+ / AstrBot FunctionTool / `send2trash`(新增运行时依赖)/ `@filter.on_llm_request()` 钩子 / pytest + monkeypatch / ruff

**Spec:** [`../specs/2026-06-18-file_remove-trash-injection-design.md`](../specs/2026-06-18-file_remove-trash-injection-design.md)

---

## File Structure

**Modified files:**
- `requirements.txt` — 加 `send2trash`
- `tools/file_remove.py` — 删除实现 + 错误矩阵 + docstring 末尾更新
- `main.py` — `FileRemoveTool.description` 末尾追加 + 常量 + 钩子函数
- `tests/test_file_remove.py` — autouse fixture monkeypatch + 5 个用例改 mock 断言 + 新增 1 个 trash_fails 用例
- `AGENTS.md` — §"依赖管理" 一句话更新

**New files:**
- `tests/test_file_remove_injection.py` — 4 个 LLM 提示词注入用例

**Not touched:**
- `metadata.yaml` / `_conf_schema.json` — 无新配置项,无需改
- 其他工具(`code_check` / `es_search` / `todo_list` 等)— 改动范围严格限定 file_remove

---

## Chunk 1: 依赖 + 工具实现层(`tools/file_remove.py`)

### Task 1.1: 新增 `send2trash` 运行时依赖

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 编辑 `requirements.txt`**

当前内容 (`type requirements.txt`):
```
ruff
cpplint
```

目标内容:
```
ruff
cpplint
send2trash
```

- [ ] **Step 2: 验证 send2trash 可装(可选)**

```bash
pip show send2trash 2>&1 || pip install send2trash
```

期望:`Name: Send2Trash` 或安装成功。仅在开发环境/CI 跑测试需要;AstrBot 宿主用户在自己环境装。

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: 新增 send2trash 运行时依赖(回收站)"
```

---

### Task 1.2: 单文件删除改造(TDD)

**Files:**
- Test: `tests/test_file_remove.py`
- Modify: `tools/file_remove.py`

- [ ] **Step 1: 先看现有单文件测试(基线)**

确认现状(任务开始时 `tests/test_file_remove.py` line 23-32):
```python
def test_remove_single_file(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    r = file_remove.remove(str(f))
    assert r["ok"] is True
    assert r["deleted"] == 1
    assert f.exists() is False   # ← 改造后这条会失效
```

- [ ] **Step 2: 在 `tests/test_file_remove.py` 顶部加 autouse fixture**

**改造前**(line 1-21 之间,导入语句后):

```python
"""file_remove 工具测试。

覆盖评审报告附录 A 的 6 个关键场景 + 修复 bug 时的回归用例。
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from unittest.mock import MagicMock

# 把项目根加入 sys.path,便于直接 `from tools import file_remove`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import file_remove  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_send2trash(monkeypatch):
    """默认对所有用例 monkeypatch send2trash,避免污染真实回收站。

    单文件/目录删除的成功路径用例可直接读取 `mock_send2trash` 做断言。
    失败/拦截路径用例默认不被调用(`mock_send2trash.send2trash.assert_not_called()`)。
    """
    mock_send2trash = MagicMock()
    monkeypatch.setattr(file_remove, "send2trash", mock_send2trash)
    return mock_send2trash
```

注意:把 `import` 顺序整理好(`MagicMock` 来自 `unittest.mock`,需在 import 段加)。

- [ ] **Step 3: 修改 `test_remove_single_file` 改用 mock 断言**

替换 `tests/test_file_remove.py` line 23-32:

```python
def test_remove_single_file(tmp_path: Path, _mock_send2trash):
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    r = file_remove.remove(str(f))
    assert r["ok"] is True
    assert r["deleted"] == 1
    _mock_send2trash.send2trash.assert_called_once_with(str(f))
```

**关键改动**:
- 加 `_mock_send2trash` 参数(autouse fixture 自动注入)
- 删 `assert f.exists() is False`(回收站不在原路径)
- 加 `mock.assert_called_once_with(str(f))`

- [ ] **Step 4: 运行测试,期望 FAIL(因 send2trash 还没接)**

```bash
pytest tests/test_file_remove.py::test_remove_single_file -v
```

期望输出:
```
FAILED tests/test_file_remove.py::test_remove_single_file
AttributeError: module 'tools.file_remove' has no attribute 'send2trash'
```
(或 `'SPCodeToolkit' object has no attribute '_file_remove_inject_guidance'`,先跑 Task 2.3 测试时遇到的错误信息)

- [ ] **Step 5: 实现 send2trash 改造(单文件路径)**

编辑 `tools/file_remove.py`:

1. 顶部 import 增加 `import send2trash`(放在 `import os` 后)。
2. 找到单文件删除代码(line 113-117 当前大致内容):
   ```python
   if p.is_file():
       try:
           size = p.stat().st_size
           os.remove(p)
           return {"ok": True, "deleted": 1, "freed": _human_size(size)}
       except OSError as e:
           return {"ok": False, "error": str(e)}
   ```
   替换为:
   ```python
   if p.is_file():
       try:
           size = p.stat().st_size
           send2trash.send2trash(str(p))
           return {"ok": True, "deleted": 1, "freed": _human_size(size)}
       except FileNotFoundError:
           return {"ok": False, "error": f"路径不存在: {path}"}
       except PermissionError:
           return {"ok": False, "error": f"无权限移入回收站: {path}"}
       except OSError as e:
           return {"ok": False, "error": f"回收站不可用: {e}。请确认系统已安装 trash-cli (Linux) 或回收站服务可用。"}
       except Exception as e:
           return {"ok": False, "error": str(e)}
   ```

3. 把模块 docstring 末尾的"执行"改为"送入回收站"(spec §"模块 docstring 同步更新")。

- [ ] **Step 6: 运行测试,期望 PASS**

```bash
pytest tests/test_file_remove.py::test_remove_single_file -v
```

期望:`1 passed`。

- [ ] **Step 7: 顺便跑下其他相关用例看是否回归**

```bash
pytest tests/test_file_remove.py -v -k "not user_blacklist"
```

期望:`test_remove_blocks_system_dir` / `test_remove_rejects_traversal` / `test_remove_unc_path_rejected` / `test_remove_macos_system_dir_blocked` / `test_remove_windows_programdata_blocked` 全部 PASS(这些是路径拦截,send2trash 不应被调,autouse fixture 已默认 mock 掉)。

- [ ] **Step 8: Commit**

```bash
git add tests/test_file_remove.py tools/file_remove.py
git commit -m "feat(file_remove): 单文件删除改用系统回收站 + autouse mock fixture"
```

---

### Task 1.3: 目录删除改造(TDD)

**Files:**
- Test: `tests/test_file_remove.py`
- Modify: `tools/file_remove.py`

- [ ] **Step 1: 修改 `test_remove_dir_with_confirm` 改用 mock**

替换 `tests/test_file_remove.py` line 47-58:

```python
def test_remove_dir_with_confirm(tmp_path: Path, _mock_send2trash):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "a").write_text("x", encoding="utf-8")
    (d / "b").write_text("y", encoding="utf-8")
    r = file_remove.remove(str(d), confirm=True)
    assert r["ok"] is True
    assert r["deleted"] == 2
    _mock_send2trash.send2trash.assert_called_once_with(str(d))
```

- [ ] **Step 2: 跑测试,期望 FAIL**

```bash
pytest tests/test_file_remove.py::test_remove_dir_with_confirm -v
```

期望:`AssertionError: ... deleted_paths` 或类似 — 因为现状 `os.walk` 真实删除了文件,断言"send2trash.send2trash 被调用"会失败。

- [ ] **Step 3: 改 `tools/file_remove.py` 目录删除路径**

找到 line 162-204 当前大致内容(目录删除的 `try/except OSError` 整段)。**保留**:
- `rglob("*")` 统计 `file_count` / `total_size` 的循环
- `confirm=False` 提前返回 proposal
- `file_count > max_items` 提前返回 proposal

**替换**整个 `try/except OSError` 块(以及紧随其后的 `errors` 累加逻辑)为:

```python
try:
    send2trash.send2trash(str(p))
    return {
        "ok": True,
        "deleted": file_count,
        "freed": _human_size(total_size),
    }
except FileNotFoundError:
    return {"ok": False, "error": f"路径不存在: {path}"}
except PermissionError:
    return {"ok": False, "error": f"无权限移入回收站: {path}"}
except OSError as e:
    return {"ok": False, "error": f"回收站不可用: {e}。请确认系统已安装 trash-cli (Linux) 或回收站服务可用。"}
except Exception as e:
    return {"ok": False, "error": str(e)}
```

**关键删除**: `errors: list[dict] = []`、`for root, dirs, files in os.walk(...)` 整段循环、`os.rmdir(p)` 末尾调用、以及返回结构里的 `deleted_paths[:10]` / `errors[:10]` 字段。

- [ ] **Step 4: 跑测试,期望 PASS**

```bash
pytest tests/test_file_remove.py::test_remove_dir_with_confirm -v
```

期望:`1 passed`。

- [ ] **Step 5: Commit**

```bash
git add tests/test_file_remove.py tools/file_remove.py
git commit -m "feat(file_remove): 目录删除改用 send2trash 单次原子调用,移除 deleted_paths/errors 字段"
```

---

### Task 1.4: 错误处理矩阵新增测试(trash 失败)

**Files:**
- Test: `tests/test_file_remove.py`

- [ ] **Step 1: 加新测试用例**

在 `tests/test_file_remove.py` 末尾追加:

```python
def test_remove_returns_error_when_trash_fails(tmp_path: Path, _mock_send2trash):
    """send2trash 抛 OSError(常见:Linux 缺 trash-cli) → 返回结构化错误,不 fallback 到物理删除。"""
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    _mock_send2trash.send2trash.side_effect = OSError("trash-cli not found")

    r = file_remove.remove(str(f))

    assert r["ok"] is False
    assert "回收站不可用" in r["error"]
    assert "trash-cli" in r["error"]
    # 文件仍在原路径(没有 fallback 到 os.remove)
    assert f.exists() is True
```

- [ ] **Step 2: 跑测试,期望 PASS(实现已在 Task 1.2 写好)**

```bash
pytest tests/test_file_remove.py::test_remove_returns_error_when_trash_fails -v
```

期望:`1 passed`。

- [ ] **Step 3: 顺便加 1 个 PermissionError 用例**

```python
def test_remove_returns_error_on_permission_denied(tmp_path: Path, _mock_send2trash):
    """send2trash 抛 PermissionError → 明确提示权限,不走 OSError 兜底。"""
    f = tmp_path / "locked.txt"
    f.write_text("x", encoding="utf-8")
    _mock_send2trash.send2trash.side_effect = PermissionError("Access denied")

    r = file_remove.remove(str(f))

    assert r["ok"] is False
    assert "无权限移入回收站" in r["error"]
```

跑:`pytest tests/test_file_remove.py::test_remove_returns_error_on_permission_denied -v` → PASS。

- [ ] **Step 4: 跑整个 test_file_remove.py**

```bash
pytest tests/test_file_remove.py -v
```

期望:全部 PASS(20 个用例左右)。如果有失败,逐个修。

特别注意 `test_remove_user_blacklist_*` 的 3 个用例 — 它们是成功路径,autouse fixture 已 mock,断言也要相应改。逐一修改:

**`test_remove_user_blacklist_empty_allows`**:
```python
def test_remove_user_blacklist_empty_allows(tmp_path: Path, _mock_send2trash):
    """空列表 = 不加额外限制。"""
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    r = file_remove.remove(str(f), custom_blacklist=[])
    assert r["ok"] is True
    assert r["deleted"] == 1
    _mock_send2trash.send2trash.assert_called_once_with(str(f))
```

**`test_remove_user_blacklist_none_allows`**:
```python
def test_remove_user_blacklist_none_allows(tmp_path: Path, _mock_send2trash):
    """None = 不加额外限制(与空列表等价)。"""
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    r = file_remove.remove(str(f), custom_blacklist=None)
    assert r["ok"] is True
    assert r["deleted"] == 1
    _mock_send2trash.send2trash.assert_called_once_with(str(f))
```

**`test_remove_user_blacklist_unrelated_allows`**:
```python
def test_remove_user_blacklist_unrelated_allows(tmp_path: Path, _mock_send2trash):
    """黑名单与目标路径无关 → 允许删除。"""
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    r = file_remove.remove(str(f), custom_blacklist=[str(tmp_path / "other_path")])
    assert r["ok"] is True
    assert r["deleted"] == 1
    _mock_send2trash.send2trash.assert_called_once_with(str(f))
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_file_remove.py
git commit -m "test(file_remove): 新增 trash 失败 + 权限拒绝 + 3 个用户黑名单用例改 mock 断言"
```

---

### Task 1.5: 验证/拦截路径测试加 `mock.assert_not_called()` 守卫

**Files:**
- Modify: `tests/test_file_remove.py`

- [ ] **Step 1: 给 12 个拦截/验证用例加 `assert_not_called()`**

这些用例在路径校验阶段就返回了,send2trash 不应被调用。autouse fixture 已默认 mock,加断言即可防止未来重构意外触发真实删除。

需要修改的 12 个用例(在 `tests/test_file_remove.py` 中的现有函数签名后追加 `_mock_send2trash` 参数 + 函数体末尾追加 `_mock_send2trash.send2trash.assert_not_called()`):

- `test_remove_blocks_system_dir` (line 61)
- `test_remove_rejects_traversal` (line 74)
- `test_remove_dir_requires_confirm` (line 35)
- `test_remove_large_dir_returns_proposal` (line 84)
- `test_remove_unc_path_rejected` (line 113)
- `test_remove_macos_system_dir_blocked` (line 126)
- `test_remove_windows_programdata_blocked` (line 138)
- `test_remove_user_blacklist_blocks_dir` (line 149)
- `test_remove_user_blacklist_blocks_file` (line 162)
- `test_remove_user_blacklist_prefix_match` (line 172)
- `test_remove_user_blacklist_distinguishes_from_builtin` (line 212)
- `test_remove_user_blacklist_error_message` (line 225)

**模板**(以 `test_remove_blocks_system_dir` 为例):
```python
def test_remove_blocks_system_dir(_mock_send2trash):
    if platform.system() != "Windows":
        r = file_remove.remove("/etc")
    else:
        r = file_remove.remove("C:/Windows/System32")
    assert r["ok"] is False
    assert "blocked_by" in r.get("evidence", {})
    _mock_send2trash.send2trash.assert_not_called()
```

注意:此用例原签名是 `test_remove_blocks_system_dir()`,没有 `tmp_path`。直接加 `_mock_send2trash` 参数即可。其它用例类似处理。

**建议用 Edit tool 的 `replace_all`** 或逐个手工改 — 每个用例改动 2 行(签名 + 末尾断言)。

- [ ] **Step 2: 跑整个 test_file_remove.py,期望全 PASS**

```bash
pytest tests/test_file_remove.py -v
```

期望:全部 PASS(22 个用例左右)。

- [ ] **Step 3: Commit**

```bash
git add tests/test_file_remove.py
git commit -m "test(file_remove): 12 个拦截/验证用例加 send2trash.assert_not_called() 守卫"
```

---

## Chunk 2: `main.py` 集成 + LLM 提示词注入

### Task 2.1: `FileRemoveTool.description` 末尾追加回收站说明

**Files:**
- Modify: `main.py`(`FileRemoveTool` 类,line ~331-359)

- [ ] **Step 1: 定位 description 当前文本**

读 `main.py` line 333-348 确认 description 末尾是 `"Single files are deleted without confirm."`,在其后追加:

- [ ] **Step 2: 编辑 description**

替换 `main.py` line 348(确保原句不动):

```python
    description: str = (
        "Delete an entire file or directory. Before deleting, it is necessary to ask the user. "
        "If delete fragments instead of the entire file, use `astrbot_file_edit_tool`. "
        "Deleting a DIRECTORY requires parameter 'confirm=true'. "
        "If a directory contains more than max_items files, the call returns a "
        "proposal asking for batch confirmation INSTEAD of deleting — read the "
        "proposal/options, then retry with confirm=true. "
        "Single files are deleted without confirm. "
        "Items are sent to the system recycle bin (recoverable), not permanently deleted."
    )
```

唯一新增:末尾加一个空格 + `"Items are sent to the system recycle bin (recoverable), not permanently deleted."`

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat(file_remove): 工具 description 末尾追加回收站说明"
```

---

### Task 2.2: 常量定义

**Files:**
- Modify: `main.py`(在 `_PROJECT_CODEGRAPH_GUIDANCE` 附近,line 145-153 后)

- [ ] **Step 1: 定位现有常量**

读 `main.py` line 145-153,确认 `_PROJECT_CODEGRAPH_GUIDANCE` 与 `_PROJECT_GUIDANCE_MARKER` 已存在。

- [ ] **Step 2: 追加常量**

在 `_PROJECT_CODEGRAPH_GUIDANCE` 末尾(line 153 后)新增:

```python
# astrbot_file_remove_tool 启用时注入到 system_prompt 末尾的指引。
# 设计目标:让 LLM 优先使用 file_remove 工具(自带路径安全 + 回收站)而非绕过。
# 无 session state 依赖——只靠 self._tool_names 作为 gate。
_FILE_REMOVE_GUIDANCE_MARKER = "[ASTRBOT_FILE_REMOVE_TOOL_GUIDANCE]"

_FILE_REMOVE_GUIDANCE = f"""
{_FILE_REMOVE_GUIDANCE_MARKER}
优先使用 `astrbot_file_remove_tool` 进行文件或目录删除,不要用 shell 命令(如 `rm`/`del`)或 Python 调用绕过它。
"""
```

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat(file_remove): 新增 LLM 提示词注入常量(marker + 指引)"
```

---

### Task 2.3: 新增 `_file_remove_inject_guidance` 钩子(TDD)

**Files:**
- Test: 新建 `tests/test_file_remove_injection.py`
- Modify: `main.py`

- [ ] **Step 1: 创建测试文件骨架**

新建 `tests/test_file_remove_injection.py`:

```python
"""astrbot_file_remove_tool 启用时 LLM 提示词注入测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import (  # noqa: E402
    _FILE_REMOVE_GUIDANCE_MARKER,
    SPCodeToolkit,
)


def _make_plugin(tool_enabled: bool) -> SPCodeToolkit:
    """最小 helper:实例化 SPCodeToolkit,设置 _tool_names 反映 enabled_tools 过滤结果。

    用 __new__ 跳过 __init__,避免触发 AstrBot 启动副作用(注册 filter / 加载 config)。
    """
    plugin = SPCodeToolkit.__new__(SPCodeToolkit)
    plugin._tool_names = (
        {"astrbot_file_remove_tool"} if tool_enabled else set()
    )
    return plugin


def _make_event(umo: str = "test:umo") -> MagicMock:
    ev = MagicMock()
    ev.unified_msg_origin = umo
    return ev


# ── 1. 工具启用时注入 ───────────────────────────────


def test_inject_when_tool_enabled():
    plugin = _make_plugin(tool_enabled=True)
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))

    assert _FILE_REMOVE_GUIDANCE_MARKER in req.system_prompt
    assert "base" in req.system_prompt  # 原文保留


# ── 2. 工具未启用时不注入 ───────────────────────────


def test_no_inject_when_tool_disabled():
    plugin = _make_plugin(tool_enabled=False)
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))

    assert req.system_prompt == "base"
    assert _FILE_REMOVE_GUIDANCE_MARKER not in req.system_prompt


# ── 3. 幂等性 ──────────────────────────────────────


def test_inject_idempotent():
    plugin = _make_plugin(tool_enabled=True)
    req = MagicMock()
    req.system_prompt = "base"

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))
    first_len = len(req.system_prompt)
    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))
    second_len = len(req.system_prompt)

    assert first_len == second_len, (
        f"重复注入应被 marker 阻止,长度变化: {first_len} → {second_len}"
    )


# ── 4. system_prompt = None 时正确初始化 ──────────


def test_inject_handles_none_system_prompt():
    plugin = _make_plugin(tool_enabled=True)
    req = MagicMock()
    req.system_prompt = None

    asyncio.run(plugin._file_remove_inject_guidance(_make_event(), req))

    assert req.system_prompt is not None
    assert _FILE_REMOVE_GUIDANCE_MARKER in req.system_prompt
```

- [ ] **Step 2: 跑测试,期望 FAIL**

```bash
pytest tests/test_file_remove_injection.py -v
```

期望:`AttributeError: 'Plugin' object has no attribute '_file_remove_inject_guidance'` 或 `ImportError`。

- [ ] **Step 3: 在 main.py 实现钩子**

定位:在 `_project_inject_codegraph_guidance` 方法定义附近(line 2467),并列新增方法(放在它之后):

```python
    @filter.on_llm_request()
    async def _file_remove_inject_guidance(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """astrbot_file_remove_tool 启用时,把"优先使用 file_remove"指引注入到 system_prompt 末尾。

        触发条件(全部满足):
        - `astrbot_file_remove_tool` 在 self._tool_names 中(说明用户已启用)
        - 同 req.system_prompt 中尚未包含 marker(防重复注入)

        设计要点(对照 _project_inject_codegraph_guidance):
        1. 无 session state / 无 feature flag——_tool_names 即 gate
        2. system_prompt = None 时用 lstrip("\\n") 避免前置空行
        3. 已存在 system_prompt 时追加在末尾
        """
        if "astrbot_file_remove_tool" not in self._tool_names:
            return
        if _FILE_REMOVE_GUIDANCE_MARKER in (req.system_prompt or ""):
            return
        if req.system_prompt is None or req.system_prompt == "":
            req.system_prompt = _FILE_REMOVE_GUIDANCE.lstrip("\n")
        else:
            req.system_prompt = req.system_prompt + _FILE_REMOVE_GUIDANCE
        logger.debug("[file_remove] 已向 system_prompt 注入优先使用指引")
```

注意:
- `filter.on_llm_request()` 是已有装饰器,直接用
- `AstrMessageEvent` / `ProviderRequest` 已在文件中 import(line 1-30 附近),无需新增 import
- `logger` 模块级变量应在文件中已定义,复用即可

- [ ] **Step 4: 跑测试,期望 PASS**

```bash
pytest tests/test_file_remove_injection.py -v
```

期望:`4 passed`。

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_file_remove_injection.py
git commit -m "feat(file_remove): 新增 on_llm_request 钩子,工具启用时注入 LLM 指引"
```

---

## Chunk 3: 文档 + 验收

### Task 3.1: AGENTS.md 一句话更新

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 定位"依赖管理"行**

读 `AGENTS.md`,找到 `- **依赖管理**: \`requirements.txt\`(仅含 lint 工具,运行时依赖由 AstrBot 宿主提供)`。

- [ ] **Step 2: 编辑**

替换 `AGENTS.md` line 11(项目信息段)和 line 87(目录树注释):

**line 11:**
```
- **依赖管理**: `requirements.txt`(仅含 lint 工具,运行时依赖由 AstrBot 宿主提供)
```
改为:
```
- **依赖管理**: `requirements.txt`(含 lint 工具与运行时依赖,如 `send2trash`;AstrBot 宿主环境装运行时依赖)
```

**line 87(目录树注释):**
```
├── requirements.txt              # lint 依赖:ruff, cpplint
```
改为:
```
├── requirements.txt              # lint + 运行时依赖:ruff, cpplint, send2trash
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs(AGENTS): 依赖管理说明补充运行时依赖项"
```

---

### Task 3.2: 验收 — ruff + pytest 全跑

- [ ] **Step 1: ruff lint**

```bash
ruff check .
```

期望:`All checks passed!` 或无 error。如果有 warning,review 后决定是否修(本特性应无新 warning)。

- [ ] **Step 2: 跑 file_remove 相关测试**

```bash
pytest tests/test_file_remove.py tests/test_file_remove_injection.py -v
```

期望:全部 PASS(约 25 个用例)。

- [ ] **Step 3: 跑全测试套件(codegraph_* 可 skip)**

```bash
pytest tests/ -v --ignore=tests/test_codegraph_cmd.py --ignore=tests/test_codegraph_lifecycle.py --ignore=tests/test_codegraph_mcp.py
```

期望:全部 PASS(无回归)。

- [ ] **Step 4: 检查文件清单**

```bash
git log --oneline main..HEAD
```

期望:本特性 6-8 个 commit,每个独立可回滚。

- [ ] **Step 5: 推送(用户已说不要 push,跳过)**

✅ 实施完成。spec 已实现。

---

## 备注

- **Git worktree**: 本计划可直接在 `main` 分支上执行(改动已 review,小半径)。如需隔离可执行 `git worktree add ../spcode-worktree -b feat/file-remove-trash`,在 worktree 里跑。
- **依赖版本**: `send2trash` 不锁版本(最新稳定即可)。如 AstrBot 宿主侧需要锁定,在 `requirements.txt` 加 `send2trash>=1.8`。
- **回归风险**: 单文件删除路径已 100% 测;目录删除移除 `deleted_paths`/`errors` 字段,如果外部脚本依赖此字段需同步更新(spec §7 已记录)。
