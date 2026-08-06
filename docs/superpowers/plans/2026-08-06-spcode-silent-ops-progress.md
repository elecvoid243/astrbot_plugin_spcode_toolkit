# spcode 静默操作 + chip 实时进度 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 dashboard 的 project load / unload / codegraph set 三个操作从"聊天框发命令"改为静默 Web API 调用,并通过新增的进度端点在 chip 上实时显示加载进度。

**Architecture:** 插件侧新增内存进度存储(`tools/operation_progress.py`,按 umo 键控),三个静默执行路径在执行中写入进度;新增 `POST /spcode/project-unload`、`POST /spcode/codegraph-set`、`GET /spcode/operation-progress` 三个端点。前端新增 `useSpcodeOperationProgress` 轮询 composable,`SpcodeProjectIndicator` / `SpcodeCodegraphChip` 增加 loading/failed 态,`ProjectLoadDialog` 的 submit 载荷从命令文本改为结构化 payload(附 `legacyText` 供无会话兜底)。

**Tech Stack:** Python 3.10+ (插件, pytest), Vue 3 + TypeScript + Vuetify + Pinia (dashboard, vitest)。

**Spec:** `docs/superpowers/specs/2026-08-06-spcode-silent-ops-progress-design.md`

## Global Constraints

- 插件仓工作区:`F:\github\astrbot_plugin_spcode_toolkit\.worktrees\feat-silent-ops-progress`(分支 `feat-silent-ops-progress`,已建好,spec 已提交于此)。
- Dashboard 工作区:`F:\github\Astrbot\.worktrees\feat-silent-ops-progress`(分支 `feat-silent-ops-progress`,Task 6 创建)。
- 插件测试命令(在插件 worktree 根目录):
  `powershell> $env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/<file> -q`
- Dashboard 测试命令(在 dashboard worktree 的 `dashboard/` 目录):`npx vitest run <spec相对路径>`
- 提交规范:conventional commits,英文 message;**只允许本地 commit,禁止 push、禁止 PR**。
- AstrBot 仓(dashboard):注释与 log 一律英文;不要新增 `*_SUMMARY.md` 类报告文件。
- 插件仓:注释/docstring 沿用该仓现有中文风格。
- 插件路由计数:ROUTES 46 → 49,`tests/test_webapi_end_to_end.py` 中路由集合断言与 `register_web_api.call_count` 断言同步更新。
- **回归基线**:本分支起点(main d52c4c2)存在预存在失败——`test_file_restore.py`×5、`test_git_branches.py` ETag×1、`test_no_window_kwargs.py`×1、`test_todo_split_tools.py` 等 14 errors(环境性:临时 git remote / 缺依赖)。验收标准为**相对基线无新增失败**;`test_project_load_endpoint.py`(21)与 `test_project_command_tokens.py`(4)由 Task 2 修复转绿。
- `_wrap` 适配器约定:POST 端点的 `umo` 从 JSON body 注入;GET 端点从 query 注入。
- envelope 工厂:`_make_envelope(*, success, reason=None, stderr="", elapsed_ms=0, **data_fields)`,成功时 `reason` 必须为 `None`。

---

## Task 1: 进度存储模块 `tools/operation_progress.py`

**Files:**
- Create: `tools/operation_progress.py`
- Test: `tests/test_operation_progress.py`

**Interfaces:**
- Produces(后续所有后端任务依赖):
  - `begin(umo: str, operation: str) -> bool` — False = 该 umo 有 running 记录(拒绝并发)
  - `append(umo: str, message: str) -> None`
  - `finish(umo: str, ok: bool, reason: str | None = None) -> None` — 仅从 running 转换,幂等
  - `query(umo: str) -> dict | None` — 返回副本
  - `class ProgressList(list)` — `ProgressList(umo)`,append 时自动镜像到进度存储
  - 记录字段:`operation / status("running"|"done"|"failed") / messages / current_step / started_at / finished_at / reason`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_operation_progress.py`:

```python
"""tests/test_operation_progress.py — 进度存储模块单测。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import time

from tools import operation_progress as prog


def setup_function() -> None:
    prog._records.clear()


class TestBegin:
    def test_first_begin_returns_true(self):
        assert prog.begin("u1", "project_load") is True
        rec = prog.query("u1")
        assert rec is not None
        assert rec["status"] == "running"
        assert rec["operation"] == "project_load"
        assert rec["messages"] == []
        assert rec["current_step"] == ""
        assert rec["finished_at"] is None
        assert rec["reason"] is None

    def test_begin_rejected_while_running(self):
        assert prog.begin("u1", "project_load") is True
        assert prog.begin("u1", "project_unload") is False
        # 原记录不被覆盖
        assert prog.query("u1")["operation"] == "project_load"

    def test_begin_allowed_after_finish(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=True)
        assert prog.begin("u1", "project_unload") is True
        assert prog.query("u1")["operation"] == "project_unload"

    def test_begin_independent_per_umo(self):
        prog.begin("u1", "project_load")
        assert prog.begin("u2", "project_load") is True


class TestAppend:
    def test_append_collects_messages(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "⏳ [1/3] AGENTS.md 初始化")
        prog.append("u1", "✅ 项目已加载: /x")
        rec = prog.query("u1")
        assert rec["messages"] == ["⏳ [1/3] AGENTS.md 初始化", "✅ 项目已加载: /x"]

    def test_append_updates_current_step_on_step_prefix(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "ℹ️ 信息行")
        assert prog.query("u1")["current_step"] == ""
        prog.append("u1", "⏳ [2/3] codegraph init: /x")
        assert prog.query("u1")["current_step"] == "⏳ [2/3] codegraph init: /x"
        prog.append("u1", "🔄 正在重启 codegraph MCP...")
        assert prog.query("u1")["current_step"] == "🔄 正在重启 codegraph MCP..."

    def test_current_step_takes_first_line_only(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "⏳ 步骤标题\n第二行详情")
        assert prog.query("u1")["current_step"] == "⏳ 步骤标题"

    def test_append_unknown_umo_is_noop(self):
        prog.append("ghost", "⏳ x")  # 不抛异常


class TestFinish:
    def test_finish_done(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=True)
        rec = prog.query("u1")
        assert rec["status"] == "done"
        assert rec["finished_at"] is not None
        assert rec["reason"] is None

    def test_finish_failed_with_reason(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=False, reason="path_unsafe")
        rec = prog.query("u1")
        assert rec["status"] == "failed"
        assert rec["reason"] == "path_unsafe"

    def test_finish_only_transitions_from_running(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=False, reason="path_unsafe")
        prog.finish("u1", ok=True)  # 已终态,不再覆盖
        assert prog.query("u1")["status"] == "failed"

    def test_finish_unknown_umo_is_noop(self):
        prog.finish("ghost", ok=True)  # 不抛异常


class TestQuery:
    def test_query_unknown_returns_none(self):
        assert prog.query("ghost") is None

    def test_query_returns_copy(self):
        prog.begin("u1", "project_load")
        prog.append("u1", "⏳ x")
        rec = prog.query("u1")
        rec["messages"].append("污染")
        assert prog.query("u1")["messages"] == ["⏳ x"]


class TestTtl:
    def test_finished_record_purged_after_ttl(self):
        prog.begin("u1", "project_load")
        prog.finish("u1", ok=True)
        prog._records["u1"]["finished_at"] = time.time() - 301
        # 下一次 begin 触发惰性清理
        assert prog.begin("u1", "project_unload") is True
        assert prog.query("u1")["operation"] == "project_unload"

    def test_running_record_never_purged(self):
        prog.begin("u1", "project_load")
        prog._records["u1"]["started_at"] = time.time() - 99999
        assert prog.begin("u2", "project_load") is True
        assert prog.query("u1") is not None


class TestProgressList:
    def test_append_mirrors_to_store(self):
        prog.begin("u1", "project_load")
        messages = prog.ProgressList("u1")
        messages.append("⏳ [1/3] init")
        messages.append("✅ done")
        rec = prog.query("u1")
        assert rec["messages"] == ["⏳ [1/3] init", "✅ done"]
        assert rec["current_step"] == "⏳ [1/3] init"
        # 仍是普通 list 行为
        assert list(messages) == ["⏳ [1/3] init", "✅ done"]

    def test_unknown_umo_noop(self):
        messages = prog.ProgressList("ghost")
        messages.append("x")  # 不抛异常
        assert list(messages) == ["x"]
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd F:\github\astrbot_plugin_spcode_toolkit\.worktrees\feat-silent-ops-progress
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_operation_progress.py -q
```

Expected: FAIL(`ModuleNotFoundError: No module named 'tools.operation_progress'`)

- [ ] **Step 3: 实现模块**

创建 `tools/operation_progress.py`:

```python
# tools/operation_progress.py
"""Per-umo 静默操作进度存储(内存级,2026-08-06 引入)。

供 webapi 静默端点(project-load / project-unload / codegraph-set)在执行
流水线时写入实时进度,``GET /spcode/operation-progress`` 读取并供 dashboard
轮询渲染 chip loading 态。

设计要点:
- 模块级 dict 按 umo 键控,每个 umo 只保留**最近一次**操作的记录。
- ``begin`` 拒绝并发(同 umo 已有 running 记录时返回 False)。
- ``finish`` 只从 running 转换(幂等),端点层的异常兜底 finish 不会
  覆盖 silent 方法已写入的终态。
- finished 记录保留 ``_FINISHED_TTL_SECONDS``(300s)供前端最后一次轮询
  拿终态;``begin`` / ``query`` 时惰性清理,不引入后台线程。
- ``current_step`` 取最后一条以 ⏳/🔄 开头的消息的首行——与
  ``project_load_step`` 的步骤文案约定耦合,若流水线改前缀需同步修改
  ``_STEP_PREFIXES``。
"""

from __future__ import annotations

import time

_FINISHED_TTL_SECONDS = 300.0
_STEP_PREFIXES = ("⏳", "🔄")

_records: dict[str, dict] = {}


def _purge_expired(now: float) -> None:
    """惰性删除已过 TTL 的 finished 记录(running 记录永不清理)。"""
    expired = [
        umo
        for umo, rec in _records.items()
        if rec["finished_at"] is not None
        and now - rec["finished_at"] > _FINISHED_TTL_SECONDS
    ]
    for umo in expired:
        del _records[umo]


def begin(umo: str, operation: str) -> bool:
    """开始一个操作。同 umo 有 running 记录时返回 False(拒绝并发)。"""
    now = time.time()
    _purge_expired(now)
    existing = _records.get(umo)
    if existing is not None and existing["status"] == "running":
        return False
    _records[umo] = {
        "operation": operation,
        "status": "running",
        "messages": [],
        "current_step": "",
        "started_at": now,
        "finished_at": None,
        "reason": None,
    }
    return True


def append(umo: str, message: str) -> None:
    """追加一条子步骤消息;⏳/🔄 开头的消息更新 current_step(取首行)。"""
    rec = _records.get(umo)
    if rec is None:
        return
    rec["messages"].append(message)
    if message.startswith(_STEP_PREFIXES):
        rec["current_step"] = message.splitlines()[0]


def finish(umo: str, ok: bool, reason: str | None = None) -> None:
    """写入终态。仅从 running 转换,重复调用不覆盖已有终态。"""
    rec = _records.get(umo)
    if rec is None or rec["status"] != "running":
        return
    rec["status"] = "done" if ok else "failed"
    rec["finished_at"] = time.time()
    rec["reason"] = reason


def query(umo: str) -> dict | None:
    """返回记录副本(messages 深拷贝一层);无记录返回 None。"""
    _purge_expired(time.time())
    rec = _records.get(umo)
    if rec is None:
        return None
    return {**rec, "messages": list(rec["messages"])}


class ProgressList(list):
    """list 子类:append 时自动镜像到进度存储。

    用途:silent 流水线里大量既有 ``messages.append(...)`` 调用点无需逐个
    修改,把 ``messages`` 初始化为 ``ProgressList(umo)`` 即可自动上报进度。
    """

    def __init__(self, umo: str) -> None:
        super().__init__()
        self._umo = umo

    def append(self, item: object) -> None:
        super().append(item)
        append(self._umo, str(item))
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_operation_progress.py -q
```

Expected: PASS(17 个测试)

- [ ] **Step 5: Commit**

```powershell
cd F:\github\astrbot_plugin_spcode_toolkit\.worktrees\feat-silent-ops-progress
git add tools/operation_progress.py tests/test_operation_progress.py
git commit -m "feat(progress): add per-umo silent operation progress store"
```

---

## Task 2: 修复 `_make_envelope` success 字段 + 既有破损测试(预存在 bug)

**背景:** 基线发现 `_make_envelope` 的返回 `{"status":"ok","data":{...}}` 中
**不含 success 字段**(success 形参被丢弃),导致:
(a) 前端 `useSpcodeProjectAutoLoad.postLoad` 读 `raw.success` 恒为 `undefined`,
静默加载在**线上永远被误判失败**;
(b) `tests/test_project_load_endpoint.py` 21 个用例自提交日起全挂
(断言旧的顶层 `success` 字段,envelope 从未输出过该字段)。
另:`tests/test_project_command_tokens.py` 4 个用例因 fake 未跟上
`create`/`git_init` 参数(2026-07-30 引入)而挂。

**Files:**
- Modify: `tools/webapi/_helpers.py`(`_make_envelope` data 载荷加 success)
- Modify: `tests/test_project_load_endpoint.py`(21 处断言改 `result["data"]["success"]`)
- Modify: `tests/test_project_command_tokens.py`(fake 补 `**_kwargs`)

**Interfaces:**
- Produces: envelope data 恒含 `success: bool`。后续所有新端点测试按
  `result["data"]["success"]` / `result["data"]["reason"]` 断言;
  前端 `raw.success` 从此有值,Task 8 的客户端代码依赖此契约。

- [ ] **Step 1: 确认基线失败清单**

```powershell
cd F:\github\astrbot_plugin_spcode_toolkit\.worktrees\feat-silent-ops-progress
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_project_load_endpoint.py tests/test_project_command_tokens.py -q
```

Expected: 21 + 4 failed(与基线一致)

- [ ] **Step 2: 修 `_make_envelope`**

`tools/webapi/_helpers.py` 的 `_make_envelope` return 改为:

```python
    return {
        "status": "ok",
        "data": {
            "success": success,
            **data_fields,
            "reason": None if success else reason,
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
        },
    }
```

docstring 的 Returns 行同步改为
``{"status": "ok", "data": {success + <data_fields> + reason + stderr + elapsed_ms}}``。

> 纯增量字段:现有通过中的测试只断言 `data` 的具体键(reason/staged/files
> 等),不断言整字典相等,不受影响。Step 4 的全量跑验证此假设——若有测试
> 做整字典相等断言而挂,按实际断言修正。

- [ ] **Step 3: 修两个测试文件**

`tests/test_project_load_endpoint.py`:全部旧的顶层 `success` 断言改为
`result["data"]["success"]`;旧的顶层 `reason` 断言同理改为
`result["data"]["reason"]`(先 grep 确认出现次数再批量替换)。

`tests/test_project_command_tokens.py`:把报
`got an unexpected keyword argument 'create'` 的 `_fake_load_impl`(及同类
fake)签名补成 `async def _fake_load_impl(self, event, directory, **_kwargs)`
形式(或在断言参数时显式接收 `create=False, git_init=False`)。

- [ ] **Step 4: 跑测试**

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_project_load_endpoint.py tests/test_project_command_tokens.py -q
```

Expected: 全 PASS

再跑 webapi 相关全量防"整字典断言"回归:

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_webapi_end_to_end.py tests/test_git_stage.py tests/test_git_status.py -q
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```powershell
git add tools/webapi/_helpers.py tests/test_project_load_endpoint.py tests/test_project_command_tokens.py
git commit -m "fix(webapi): include success flag in envelope data payload"
```

---

## Task 3: `load_impl_silent` 接入进度钩子 + `POST project-load` 并发门与兜底

**Files:**
- Modify: `tools/project/manager.py`(`load_impl_silent` 整体改为 wrapper + core)
- Modify: `tools/webapi/project_load.py`(begin 并发门 + 异常兜底 finish)
- Test: `tests/test_project_load_progress_hook.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `begin / finish / ProgressList`。
- Produces: `ProjectManager.load_impl_silent(...)` 签名与返回 schema **不变**(前端 `useSpcodeProjectAutoLoad` 零改动);执行期间进度存储实时更新。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_project_load_progress_hook.py`:

```python
"""tests/test_project_load_progress_hook.py — load_impl_silent 进度钩子测试。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from tools import operation_progress as prog
from tools.project.manager import ProjectManager
from tools.project import state as _state


def setup_function() -> None:
    prog._records.clear()
    _state._projects.clear() if hasattr(_state, "_projects") else None


def _make_event(umo: str = "u1") -> MagicMock:
    event = MagicMock()
    event.unified_msg_origin = umo
    event.plain_result = lambda text: text
    return event


def _make_manager(*, agentsmd_on: bool = True) -> ProjectManager:
    plugin = MagicMock()
    plugin._config = {
        "agentsmd_enabled": agentsmd_on,
        "codegraph_enabled": False,  # no_codegraph 由参数控制;这里默认关闭走 disabled 分支
    }
    return ProjectManager(plugin)


def test_progress_written_on_early_failure():
    """feature flag 关闭的早期返回也要写 failed 终态。"""
    mgr = _make_manager(agentsmd_on=False)
    prog.begin("u1", "project_load")
    result = asyncio.run(
        mgr.load_impl_silent(_make_event(), "C:/x", no_agentsmd=False, no_codegraph=True)
    )
    assert result["ok"] is False
    rec = prog.query("u1")
    assert rec["status"] == "failed"
    assert rec["reason"] == "agentsmd_disabled"


def test_progress_finish_without_begin_is_noop():
    """未经 begin 的直接调用(如旧测试)不因进度钩子崩溃。"""
    mgr = _make_manager(agentsmd_on=False)
    result = asyncio.run(
        mgr.load_impl_silent(_make_event(), "C:/x", no_agentsmd=False, no_codegraph=True)
    )
    assert result["ok"] is False
    assert prog.query("u1") is None
```

> 注意:`_state` 的清空方式以 `tools/project/state.py` 的实际 API 为准
> (读一下该文件,若有 `clear()`/内部 dict 名不同,按实际调整 setup)。
> `ProjectManager(plugin)` 的构造签名也先读 `manager.py` 头部确认。

- [ ] **Step 2: 跑测试确认失败**(钩子未接入时两个测试都应 FAIL:rec 仍为 running / 或行为不符)

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_project_load_progress_hook.py -q
```

- [ ] **Step 3: 实现**

`tools/project/manager.py` 顶部 import 区加:

```python
from ..operation_progress import ProgressList, finish as _progress_finish
```

把现有 `load_impl_silent` 整体改名改造:

```python
    async def load_impl_silent(
        self,
        event: AstrMessageEvent,
        directory: str,
        *,
        no_agentsmd: bool = False,
        no_codegraph: bool = False,
        create: bool = False,
        git_init: bool = False,
    ) -> dict:
        """(原 docstring 保留,末尾追加:)

        2026-08-06: 执行期间把每条子步骤消息镜像到
        :mod:`tools.operation_progress`(若端点层已 ``begin``),
        返回前写终态。未 begin 的直接调用(旧测试/旧调用方)自动 no-op。
        """
        result = await self._load_impl_silent_core(
            event,
            directory,
            no_agentsmd=no_agentsmd,
            no_codegraph=no_codegraph,
            create=create,
            git_init=git_init,
        )
        _progress_finish(
            event.unified_msg_origin,
            ok=bool(result.get("ok")),
            reason=result.get("reason"),
        )
        return result
```

原函数体重命名为 `_load_impl_silent_core`(签名相同),函数体内**只改一行**:

```python
        messages: list[str] = []
```

改为:

```python
        messages = ProgressList(event.unified_msg_origin)
```

> 注意:原函数体里 `umo = event.unified_msg_origin` 在步骤 2 才赋值,
> 而 ProgressList 初始化在函数顶部,所以这里直接用
> `event.unified_msg_origin`,不要依赖后面的局部变量顺序。

`tools/webapi/project_load.py` 的 `handle` 里,在参数校验之后、`force` 卸载**之前**插入并发门:

```python
    # ── 2.5 并发门:同 umo 只允许一个静默操作 ──
    from ..operation_progress import begin as _progress_begin

    if not _progress_begin(umo, "project_load"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
        )
```

再把 `result = await plugin.project.load_impl_silent(...)` 包进 try/except 兜底:

```python
    try:
        result = await plugin.project.load_impl_silent(
            silent_event,
            directory,
            no_agentsmd=no_agentsmd,
            no_codegraph=no_codegraph,
            create=create,
            git_init=git_init,
        )
    except Exception as exc:
        logger.exception("project-load: 未捕获异常 (umo=%s)", umo)
        from ..operation_progress import finish as _progress_finish

        _progress_finish(umo, ok=False, reason="internal_error")
        return _make_envelope(
            success=False,
            reason=ReasonCode.INTERNAL_ERROR,
            elapsed_ms=_elapsed(t0),
            loaded=False,
            umo=umo,
            substep_messages=[str(exc)],
        )
```

> import 统一提到模块顶部(`from ..operation_progress import begin as
> _progress_begin, finish as _progress_finish`),函数体内不重复 import。

- [ ] **Step 4: 跑测试**

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_project_load_progress_hook.py tests/test_project_load_endpoint.py -q
```

Expected: 全新 PASS;`test_project_load_endpoint.py` 在 Task 2 修复后应全绿,
本任务不得引入新失败(注意既有用例 mock 点是 `plugin.project.load_impl_silent`,
签名不变,wrapper 改名不影响)。

- [ ] **Step 5: Commit**

```powershell
git add tools/project/manager.py tools/webapi/project_load.py tests/test_project_load_progress_hook.py
git commit -m "feat(project-load): wire silent load into operation progress store"
```

---

## Task 4: `unload_impl_silent` + `POST /spcode/project-unload`

**Files:**
- Modify: `tools/project/manager.py`(新增 `unload_impl_silent`)
- Create: `tools/webapi/project_unload.py`
- Test: `tests/test_project_unload_endpoint.py`(新建)

**Interfaces:**
- Consumes: Task 1 `begin/finish/ProgressList`;现有 `ProjectManager.unload_impl`(async generator)。
- Produces:
  - `ProjectManager.unload_impl_silent(event) -> dict`,返回 `{ok, directory, substep_messages, reason}`;reason ∈ `None | "feature_disabled" | "no_project_loaded"`
  - `POST /spcode/project-unload`,body `{umo}`,envelope data `{unloaded, directory, umo, substep_messages}`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_project_unload_endpoint.py`:

```python
"""tests/test_project_unload_endpoint.py — POST /spcode/project-unload 测试。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tools import operation_progress as prog
from tools.webapi.project_unload import handle


def setup_function() -> None:
    prog._records.clear()


def _make_plugin(*, silent_result: dict) -> MagicMock:
    plugin = MagicMock()
    plugin.project.unload_impl_silent = AsyncMock(return_value=silent_result)
    return plugin


def test_missing_umo_rejected():
    plugin = _make_plugin(silent_result={})
    result = asyncio.run(handle(plugin, umo=None, body={"umo": None}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


def test_concurrent_operation_rejected():
    plugin = _make_plugin(silent_result={})
    prog.begin("u1", "project_load")  # 占用 running
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "operation_in_progress"


def test_success():
    plugin = _make_plugin(
        silent_result={
            "ok": True,
            "directory": "C:/proj",
            "substep_messages": ["✅ 项目已卸载: C:/proj"],
            "reason": None,
        }
    )
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is True
    assert result["data"]["unloaded"] is True
    assert result["data"]["directory"] == "C:/proj"
    assert result["data"]["substep_messages"] == ["✅ 项目已卸载: C:/proj"]


def test_no_project_loaded_maps_reason():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "",
            "substep_messages": [],
            "reason": "no_project_loaded",
        }
    )
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "no_project_loaded"


def test_feature_disabled_maps_reason():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "",
            "substep_messages": [],
            "reason": "feature_disabled",
        }
    )
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "feature_disabled"


def test_unhandled_exception_gives_internal_error_and_finish():
    plugin = MagicMock()
    plugin.project.unload_impl_silent = AsyncMock(side_effect=RuntimeError("boom"))
    result = asyncio.run(handle(plugin, umo="u1", body={"umo": "u1"}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "internal_error"
    rec = prog.query("u1")
    assert rec["status"] == "failed"
    assert rec["reason"] == "internal_error"
```

`silent 方法本体`的测试(迭代 `unload_impl` 收集消息)加到同文件:

```python
class TestUnloadImplSilent:
    """ProjectManager.unload_impl_silent 本体(不走 endpoint)。"""

    def test_not_loaded_returns_reason(self):
        from tools.project.manager import ProjectManager
        from tools.project import state as _state

        plugin = MagicMock()
        plugin._config = {"agentsmd_enabled": True, "codegraph_enabled": True}
        mgr = ProjectManager(plugin)
        event = MagicMock()
        event.unified_msg_origin = "u-x"
        event.plain_result = lambda text: text
        _state.pop("u-x") if _state.get("u-x") else None
        result = asyncio.run(mgr.unload_impl_silent(event))
        assert result["ok"] is False
        assert result["data"]["reason"] == "no_project_loaded"

    def test_feature_disabled(self):
        from tools.project.manager import ProjectManager

        plugin = MagicMock()
        plugin._config = {"agentsmd_enabled": False, "codegraph_enabled": True}
        mgr = ProjectManager(plugin)
        event = MagicMock()
        event.unified_msg_origin = "u-y"
        result = asyncio.run(mgr.unload_impl_silent(event))
        assert result["ok"] is False
        assert result["data"]["reason"] == "feature_disabled"
```

- [ ] **Step 2: 跑测试确认失败**(`ModuleNotFoundError: tools.webapi.project_unload`)

- [ ] **Step 3: 实现**

`tools/project/manager.py` 的 `ProjectManager` 新增方法(紧跟 `unload_impl` 之后):

```python
    async def unload_impl_silent(self, event: AstrMessageEvent) -> dict:
        """``/project unload`` 的静默变体,供 webapi 端点调用(2026-08-06 引入)。

        与 :meth:`unload_impl` 行为一致(feature flag 校验、no-op 守卫、
        agentsmd unload、codegraph 回默认、state 清理),但不向聊天框产出
        任何消息——yield 全部收集到返回 dict。每条消息经
        :class:`ProgressList` 镜像到进度存储(若端点已 begin)。

        Args:
            event: 仅需 ``unified_msg_origin`` 属性 + ``plain_result``
                可调(传 ``plain_result=lambda x: x`` 的 MagicMock)。

        Returns:
            ``{ok, directory, substep_messages, reason}``;
            reason ∈ ``None | "feature_disabled" | "no_project_loaded"``。
        """
        umo = event.unified_msg_origin
        agentsmd_on = self._plugin._config.get("agentsmd_enabled", True)
        codegraph_on = self._plugin._config.get("codegraph_enabled", True)
        if not (agentsmd_on and codegraph_on):
            _progress_finish(umo, ok=False, reason="feature_disabled")
            return {
                "ok": False,
                "directory": "",
                "substep_messages": [],
                "reason": "feature_disabled",
            }
        info = _state.get(umo)
        if info is None:
            _progress_finish(umo, ok=False, reason="no_project_loaded")
            return {
                "ok": False,
                "directory": "",
                "substep_messages": [],
                "reason": "no_project_loaded",
            }
        directory = info.get("directory", "")
        messages = ProgressList(umo)
        async for msg in self.unload_impl(event):
            messages.append(_msg_to_text(msg))
        _progress_finish(umo, ok=True)
        return {
            "ok": True,
            "directory": directory,
            "substep_messages": list(messages),
            "reason": None,
        }
```

新建 `tools/webapi/project_unload.py`:

```python
"""POST /spcode/project-unload — 静默卸载项目(dashboard 调用,不在聊天框 yield)。

Author: elecvoid243 @ 2026-08-06

仿照 ``project_load.py`` 的静默模式:不调 ``unload_impl``(async generator,
yield 用户消息),改调 :meth:`ProjectManager.unload_impl_silent`(返回结构化
dict)。body 仅需 ``umo``(由 _wrap 从 JSON body 注入)。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ..operation_progress import begin as _progress_begin
from ..operation_progress import finish as _progress_finish
from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

# silent 层 reason → envelope reason 码
_SILENT_REASON_TO_ENVELOPE: dict[str, str] = {
    "feature_disabled": ReasonCode.FEATURE_DISABLED,
    "no_project_loaded": ReasonCode.NO_PROJECT_LOADED,
}


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,  # noqa: ARG001  # 对齐 _wrap 注入
    body: dict | None = None,  # noqa: ARG001  # umo 已被 _wrap 单独注入
) -> dict:
    """POST /spcode/project-unload handler — 静默卸载当前会话的项目。

    Returns:
        成功: ``data{unloaded: True, directory, umo, substep_messages}``
        失败: ``success=False + reason``(invalid_body / operation_in_progress /
              feature_disabled / no_project_loaded / internal_error)
    """
    t0 = _time.time()
    if not umo:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
        )
    if not _progress_begin(umo, "project_unload"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
            umo=umo,
        )

    from unittest.mock import MagicMock  # 延迟到 runtime

    silent_event = MagicMock()
    silent_event.unified_msg_origin = umo
    silent_event.plain_result = lambda text: text  # noqa: ARG005

    try:
        result = await plugin.project.unload_impl_silent(silent_event)
    except Exception as exc:
        logger.exception("project-unload: 未捕获异常 (umo=%s)", umo)
        _progress_finish(umo, ok=False, reason="internal_error")
        return _make_envelope(
            success=False,
            reason=ReasonCode.INTERNAL_ERROR,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
            umo=umo,
            substep_messages=[str(exc)],
        )

    if not result.get("ok"):
        silent_reason = result.get("reason") or ""
        envelope_reason = _SILENT_REASON_TO_ENVELOPE.get(
            silent_reason, ReasonCode.GIT_ERROR
        )
        logger.info("project-unload: 失败 (umo=%s, reason=%s)", umo, silent_reason)
        return _make_envelope(
            success=False,
            reason=envelope_reason,
            elapsed_ms=_elapsed(t0),
            unloaded=False,
            umo=umo,
            substep_messages=result.get("substep_messages", []),
            silent_reason=silent_reason,
        )

    logger.info(
        "project-unload: 成功 (umo=%s, dir=%s)", umo, result.get("directory", "")
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        unloaded=True,
        directory=result.get("directory", ""),
        umo=umo,
        substep_messages=result.get("substep_messages", []),
    )
```

> 注意:`unload_impl_silent` 内部已调 `_progress_finish`;端点 except 分支
> 的兜底 finish 因幂等设计不会覆盖已有终态。测试里 plugin 是 MagicMock,
> mock 的 `unload_impl_silent` 不会调 finish,所以成功路径测试里进度记录
> 停留在 running 是**预期行为**(真实实现会 finish),断言不要查终态。

- [ ] **Step 4: 跑测试**

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_project_unload_endpoint.py -q
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```powershell
git add tools/project/manager.py tools/webapi/project_unload.py tests/test_project_unload_endpoint.py
git commit -m "feat(webapi): add POST /spcode/project-unload silent endpoint"
```

---

## Task 5: `set_project_silent` + `POST /spcode/codegraph-set`

**Files:**
- Modify: `tools/codegraph/manager.py`(新增 `set_project_silent`)
- Create: `tools/webapi/codegraph_set.py`
- Test: `tests/test_codegraph_set_endpoint.py`(新建)

**Interfaces:**
- Consumes: Task 1;现有 `CodegraphManager.set_project`(async generator)。
- Produces:
  - `CodegraphManager.set_project_silent(event, directory) -> dict`,返回 `{ok, directory, substep_messages, reason, mcp_restarted}`;reason ∈ `None | "path_invalid" | "mcp_restart_failed"`
  - `POST /spcode/codegraph-set`,body `{umo, directory}`;envelope reason 映射:`path_invalid→path_unsafe`、`mcp_restart_failed→git_error`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_codegraph_set_endpoint.py`:

```python
"""tests/test_codegraph_set_endpoint.py — POST /spcode/codegraph-set 测试。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from tools import operation_progress as prog
from tools.webapi.codegraph_set import handle


def setup_function() -> None:
    prog._records.clear()


def _make_plugin(*, silent_result: dict) -> MagicMock:
    plugin = MagicMock()
    plugin.codegraph.set_project_silent = AsyncMock(return_value=silent_result)
    return plugin


def test_missing_umo_rejected():
    result = asyncio.run(handle(_make_plugin(silent_result={}), umo=None, body={}))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


def test_missing_directory_rejected():
    result = asyncio.run(
        handle(_make_plugin(silent_result={}), umo="u1", body={"umo": "u1"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_param"


def test_concurrent_operation_rejected():
    prog.begin("u1", "project_load")
    result = asyncio.run(
        handle(
            _make_plugin(silent_result={}),
            umo="u1",
            body={"umo": "u1", "directory": "C:/x"},
        )
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "operation_in_progress"


def test_success():
    plugin = _make_plugin(
        silent_result={
            "ok": True,
            "directory": "C:/proj",
            "substep_messages": ["✅ codegraph 已切换到新项目: C:/proj"],
            "reason": None,
            "mcp_restarted": True,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is True
    assert result["data"]["set"] is True
    assert result["data"]["mcp_restarted"] is True


def test_path_invalid_maps_to_path_unsafe():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/nope",
            "substep_messages": ["❌ 目录不存在: C:/nope"],
            "reason": "path_invalid",
            "mcp_restarted": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/nope"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "path_unsafe"


def test_mcp_restart_failed_maps_to_git_error():
    plugin = _make_plugin(
        silent_result={
            "ok": False,
            "directory": "C:/proj",
            "substep_messages": ["❌ codegraph MCP 重启失败: boom"],
            "reason": "mcp_restart_failed",
            "mcp_restarted": False,
        }
    )
    result = asyncio.run(
        handle(plugin, umo="u1", body={"umo": "u1", "directory": "C:/proj"})
    )
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "git_error"


class TestSetProjectSilent:
    """CodegraphManager.set_project_silent 本体。"""

    def _run_silent(self, yielded: list[str]) -> dict:
        from tools.codegraph.manager import CodegraphManager

        plugin = MagicMock()
        mgr = CodegraphManager(plugin)

        async def fake_set_project(event, directory):
            for m in yielded:
                yield m

        mgr.set_project = fake_set_project
        event = MagicMock()
        event.unified_msg_origin = "u-cg"
        return asyncio.run(mgr.set_project_silent(event, "C:/proj"))

    def test_success_ok(self):
        result = self._run_silent(["✅ codegraph 已切换到新项目: C:/proj"])
        assert result["ok"] is True
        assert result["mcp_restarted"] is True

    def test_first_message_failure_is_path_invalid(self):
        result = self._run_silent(["❌ 目录不存在: C:/nope"])
        assert result["ok"] is False
        assert result["data"]["reason"] == "path_invalid"

    def test_late_failure_is_mcp_restart_failed(self):
        result = self._run_silent(
            ["🔄 正在重启 codegraph MCP...", "❌ codegraph MCP 重启失败: boom"]
        )
        assert result["ok"] is False
        assert result["data"]["reason"] == "mcp_restart_failed"
```

> `CodegraphManager(plugin)` 构造签名先读 `tools/codegraph/manager.py` 头部
> 确认;若构造参数不同按实际调整。

- [ ] **Step 2: 跑测试确认失败**(`ModuleNotFoundError: tools.webapi.codegraph_set`)

- [ ] **Step 3: 实现**

`tools/codegraph/manager.py` 顶部 import:

```python
from ..operation_progress import ProgressList, finish as _progress_finish
```

`CodegraphManager` 新增方法(紧跟 `set_project` 之后):

```python
    async def set_project_silent(
        self, event: AstrMessageEvent, directory: str
    ) -> dict:
        """``/codegraph set`` 的静默变体,供 webapi 端点调用(2026-08-06 引入)。

        迭代 :meth:`set_project` 收集全部 yield,不回传聊天框。每条消息
        经 :class:`ProgressList` 镜像到进度存储(若端点已 begin)。

        reason 判定约定(与 set_project 的 yield 文案耦合):
          - 首条消息以 ❌ 开头 → 路径校验失败(``path_invalid``)
          - 后续消息出现 ❌ → MCP 重启失败(``mcp_restart_failed``)
          - 无 ❌ → 成功
        若 set_project 的错误文案前缀变化,此处需同步。

        Returns:
            ``{ok, directory, substep_messages, reason, mcp_restarted}``
        """
        umo = event.unified_msg_origin
        messages = ProgressList(umo)
        async for msg in self.set_project(event, directory):
            messages.append(str(msg))
        first = messages[0] if messages else ""
        if first.startswith("❌"):
            reason: str | None = "path_invalid"
        elif any(m.startswith("❌") for m in messages):
            reason = "mcp_restart_failed"
        else:
            reason = None
        ok = reason is None
        _progress_finish(umo, ok=ok, reason=reason)
        return {
            "ok": ok,
            "directory": directory,
            "substep_messages": list(messages),
            "reason": reason,
            "mcp_restarted": any(m.startswith("✅ codegraph 已切换") for m in messages),
        }
```

新建 `tools/webapi/codegraph_set.py`:

```python
"""POST /spcode/codegraph-set — 静默切换 codegraph 默认项目(2026-08-06)。

Author: elecvoid243 @ 2026-08-06

仿照 ``project_load.py`` 的静默模式。body: ``umo``(必传)+ ``directory``(必传)。
MCP 重启最长 180s,前端通过 ``GET /spcode/operation-progress`` 轮询进度。
"""

from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING

from ..operation_progress import begin as _progress_begin
from ..operation_progress import finish as _progress_finish
from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

_SILENT_REASON_TO_ENVELOPE: dict[str, str] = {
    "path_invalid": ReasonCode.PATH_UNSAFE,
    "mcp_restart_failed": ReasonCode.GIT_ERROR,
}


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


async def handle(
    plugin: SPCodeToolkit,
    *,
    umo: str | None = None,
    worktree: str | None = None,  # noqa: ARG001
    body: dict | None = None,
) -> dict:
    """POST /spcode/codegraph-set handler — 静默切换 codegraph 默认项目。"""
    t0 = _time.time()
    if not isinstance(body, dict) or not umo:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(t0),
            set=False,
        )
    directory = body.get("directory")
    if not isinstance(directory, str) or not directory.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
        )
    if not _progress_begin(umo, "codegraph_set"):
        return _make_envelope(
            success=False,
            reason=ReasonCode.OPERATION_IN_PROGRESS,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
        )

    from unittest.mock import MagicMock

    silent_event = MagicMock()
    silent_event.unified_msg_origin = umo
    silent_event.plain_result = lambda text: text  # noqa: ARG005

    try:
        result = await plugin.codegraph.set_project_silent(
            silent_event, directory.strip()
        )
    except Exception as exc:
        logger.exception("codegraph-set: 未捕获异常 (umo=%s)", umo)
        _progress_finish(umo, ok=False, reason="internal_error")
        return _make_envelope(
            success=False,
            reason=ReasonCode.INTERNAL_ERROR,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
            substep_messages=[str(exc)],
        )

    if not result.get("ok"):
        silent_reason = result.get("reason") or ""
        envelope_reason = _SILENT_REASON_TO_ENVELOPE.get(
            silent_reason, ReasonCode.GIT_ERROR
        )
        logger.info("codegraph-set: 失败 (umo=%s, reason=%s)", umo, silent_reason)
        return _make_envelope(
            success=False,
            reason=envelope_reason,
            elapsed_ms=_elapsed(t0),
            set=False,
            umo=umo,
            directory=result.get("directory", ""),
            substep_messages=result.get("substep_messages", []),
            mcp_restarted=result.get("mcp_restarted", False),
            silent_reason=silent_reason,
        )

    logger.info(
        "codegraph-set: 成功 (umo=%s, dir=%s)", umo, result.get("directory", "")
    )
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(t0),
        set=True,
        directory=result.get("directory", ""),
        umo=umo,
        substep_messages=result.get("substep_messages", []),
        mcp_restarted=result.get("mcp_restarted", False),
    )
```

- [ ] **Step 4: 跑测试**

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_codegraph_set_endpoint.py -q
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```powershell
git add tools/codegraph/manager.py tools/webapi/codegraph_set.py tests/test_codegraph_set_endpoint.py
git commit -m "feat(webapi): add POST /spcode/codegraph-set silent endpoint"
```

---

## Task 6: `GET /spcode/operation-progress` + 路由注册(46→49)

**Files:**
- Create: `tools/webapi/operation_progress.py`
- Modify: `tools/webapi/__init__.py`(import + ROUTES 三条 + docstring 计数)
- Modify: `tests/test_webapi_end_to_end.py`(路由集合 + call_count 46→49)
- Test: `tests/test_operation_progress_endpoint.py`(新建)

**Interfaces:**
- Produces: `GET /spcode/operation-progress?umo=...` → envelope data `{status: "idle"}` 或 `{operation, status, current_step, messages, started_at, finished_at, reason}`。前端 Task 7 依赖此 schema。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_operation_progress_endpoint.py`:

```python
"""tests/test_operation_progress_endpoint.py — GET /spcode/operation-progress。

Author: elecvoid243 @ 2026-08-06
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from tools import operation_progress as prog
from tools.webapi.operation_progress import handle


def setup_function() -> None:
    prog._records.clear()


def test_missing_umo_rejected():
    result = asyncio.run(handle(MagicMock(), umo=None))
    assert result["data"]["success"] is False
    assert result["data"]["reason"] == "invalid_body"


def test_idle_when_no_record():
    result = asyncio.run(handle(MagicMock(), umo="u1"))
    assert result["data"]["success"] is True
    assert result["data"]["status"] == "idle"


def test_running_record_returned():
    prog.begin("u1", "project_load")
    prog.append("u1", "⏳ [1/3] init")
    result = asyncio.run(handle(MagicMock(), umo="u1"))
    data = result["data"]
    assert data["status"] == "running"
    assert data["operation"] == "project_load"
    assert data["current_step"] == "⏳ [1/3] init"
    assert data["messages"] == ["⏳ [1/3] init"]
    assert data["finished_at"] is None
```

- [ ] **Step 2: 跑测试确认失败**(`ModuleNotFoundError`)

- [ ] **Step 3: 实现 + 注册**

新建 `tools/webapi/operation_progress.py`:

```python
"""GET /spcode/operation-progress — 查询静默操作实时进度(2026-08-06)。

Author: elecvoid243 @ 2026-08-06

dashboard 在发出静默 POST(project-load / project-unload / codegraph-set)
后以 500ms 间隔轮询本端点,直到 status 进入 done/failed。
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

from ..operation_progress import query as _progress_query
from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit


async def handle(
    plugin: "SPCodeToolkit",  # noqa: ARG001  # 进度存储是模块级,无需 plugin
    *,
    umo: str | None = None,
) -> dict:
    """GET /spcode/operation-progress handler。

    Query params:
        umo (required): 会话 umo。

    Returns:
        无记录: ``data{status: "idle"}``
        有记录: ``data{operation, status, current_step, messages,
        started_at, finished_at, reason}``
    """
    t0 = _time.time()
    if not umo:
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=int((_time.time() - t0) * 1000),
            status="idle",
        )
    rec = _progress_query(umo)
    if rec is None:
        return _make_envelope(
            success=True,
            elapsed_ms=int((_time.time() - t0) * 1000),
            status="idle",
        )
    return _make_envelope(
        success=True,
        elapsed_ms=int((_time.time() - t0) * 1000),
        **rec,
    )
```

`tools/webapi/__init__.py`:
- 顶部 import 区加 `operation_progress`(新模块)、`project_unload`、`codegraph_set`(按现有 `from . import (...)` 列表字母序插入);
- ROUTES 表追加三条(位置:紧跟现有 project-load 条目之后):

```python
    (
        "/spcode/project-unload",
        ["POST"],
        project_unload.handle,
        "静默卸载当前会话已加载的项目(供 dashboard 调用)",
    ),
    (
        "/spcode/codegraph-set",
        ["POST"],
        codegraph_set.handle,
        "静默切换 codegraph 默认项目(供 dashboard 调用)",
    ),
    (
        "/spcode/operation-progress",
        ["GET"],
        operation_progress.handle,
        "查询静默操作实时进度(供 dashboard 轮询)",
    ),
```

- `register_webapi_routes` docstring 里 "46" 改 "49" 并加一行变更记录(`2026-08-06: 46 -> 49 (+project-unload +codegraph-set +operation-progress)`)。

`tests/test_webapi_end_to_end.py`:
- 路由集合断言加 `"/spcode/project-unload"`、`"/spcode/codegraph-set"`、`"/spcode/operation-progress"`;
- 两处 `call_count == 46` / `call_count += ... 46` 断言改 49,注释同步。

- [ ] **Step 4: 跑测试**

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests/test_operation_progress_endpoint.py tests/test_webapi_end_to_end.py tests/test_webapi_helpers_smoke.py -q
```

Expected: 全 PASS

- [ ] **Step 5: 插件全量回归 + ruff**

```powershell
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests -q
ruff check tools tests; ruff format --check tools tests
```

Expected: 全 PASS,无 lint 错误。

- [ ] **Step 6: Commit**

```powershell
git add tools/webapi/operation_progress.py tools/webapi/__init__.py tests/test_operation_progress_endpoint.py tests/test_webapi_end_to_end.py
git commit -m "feat(webapi): add GET /spcode/operation-progress and register new routes (46->49)"
```

---

## Task 7: dashboard worktree + `useSpcodeOperationProgress` 轮询 composable

**Files:**
- Create: `F:\github\Astrbot\.worktrees\feat-silent-ops-progress`(worktree,分支 `feat-silent-ops-progress`)
- Create: `dashboard/src/composables/useSpcodeOperationProgress.ts`
- Test: `dashboard/src/composables/useSpcodeOperationProgress.spec.ts`

**Interfaces:**
- Produces(前端后续任务依赖):
  - `useSpcodeOperationProgress()` → `{ progress: Ref<OperationProgress>, startPolling(umo: string): void, stopPolling(): void, clear(): void }`
  - `OperationProgress = { status: "idle"|"running"|"done"|"failed", operation: "project_load"|"project_unload"|"codegraph_set"|null, currentStep: string, messages: string[], reason: string|null }`

- [ ] **Step 1: 建 dashboard worktree 并装依赖**

```powershell
cd F:\github\Astrbot
git worktree add .worktrees/feat-silent-ops-progress -b feat-silent-ops-progress
cd .worktrees/feat-silent-ops-progress/dashboard
pnpm install --frozen-lockfile
```

- [ ] **Step 2: 写失败测试**

创建 `dashboard/src/composables/useSpcodeOperationProgress.spec.ts`:

```typescript
// useSpcodeOperationProgress.spec.ts
// Author: elecvoid243 @ 2026-08-06
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn() },
}));

import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeOperationProgress } from "./useSpcodeOperationProgress";

function mockGet(status: string, extra: Record<string, unknown> = {}) {
  (pluginExtensionApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
    data: { data: { status, ...extra } },
  });
}

describe("useSpcodeOperationProgress", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useSpcodeOperationProgress().clear();
  });
  afterEach(() => {
    useSpcodeOperationProgress().stopPolling();
    vi.useRealTimers();
  });

  it("starts in idle", () => {
    const { progress } = useSpcodeOperationProgress();
    expect(progress.value.status).toBe("idle");
    expect(progress.value.operation).toBeNull();
  });

  it("polls and mirrors running progress", async () => {
    mockGet("running", {
      operation: "project_load",
      current_step: "⏳ [1/3] init",
      messages: ["⏳ [1/3] init"],
    });
    const { progress, startPolling } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0); // first immediate poll
    expect(progress.value.status).toBe("running");
    expect(progress.value.currentStep).toBe("⏳ [1/3] init");
  });

  it("stops polling on terminal state", async () => {
    mockGet("done", { operation: "project_load" });
    const { progress, startPolling } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0);
    expect(progress.value.status).toBe("done");
    const calls = (pluginExtensionApi.get as ReturnType<typeof vi.fn>).mock
      .calls.length;
    await vi.advanceTimersByTimeAsync(2000);
    expect(
      (pluginExtensionApi.get as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBe(calls); // no more polls after terminal
  });

  it("keeps failed terminal state for the chip popover", async () => {
    mockGet("failed", {
      operation: "project_load",
      reason: "path_unsafe",
      messages: ["❌ path unsafe"],
    });
    const { progress, startPolling } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0);
    expect(progress.value.status).toBe("failed");
    expect(progress.value.reason).toBe("path_unsafe");
    expect(progress.value.messages).toEqual(["❌ path unsafe"]);
  });

  it("clear resets to idle", async () => {
    mockGet("failed", { operation: "project_load" });
    const { progress, startPolling, clear } = useSpcodeOperationProgress();
    startPolling("u1");
    await vi.advanceTimersByTimeAsync(0);
    clear();
    expect(progress.value.status).toBe("idle");
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

```powershell
cd F:\github\Astrbot\.worktrees\feat-silent-ops-progress\dashboard
npx vitest run src/composables/useSpcodeOperationProgress.spec.ts
```

Expected: FAIL(模块不存在)

- [ ] **Step 4: 实现**

创建 `dashboard/src/composables/useSpcodeOperationProgress.ts`:

```typescript
// dashboard/src/composables/useSpcodeOperationProgress.ts
//
// Singleton polling driver for the spcode silent-operation progress
// endpoint (GET /spcode/operation-progress). Any silent POST (project
// load / unload / codegraph set) calls startPolling(umo) before firing;
// the chips read `progress` to render their loading / failed states.
//
// Author: elecvoid243 @ 2026-08-06

import { ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";

export type OperationKind =
  | "project_load"
  | "project_unload"
  | "codegraph_set";
export type OperationStatus = "idle" | "running" | "done" | "failed";

export interface OperationProgress {
  status: OperationStatus;
  operation: OperationKind | null;
  currentStep: string;
  messages: string[];
  reason: string | null;
}

const IDLE: OperationProgress = {
  status: "idle",
  operation: null,
  currentStep: "",
  messages: [],
  reason: null,
};

const progress = ref<OperationProgress>({ ...IDLE });

const POLL_INTERVAL_MS = 500;
const POLL_TIMEOUT_MS = 200_000; // codegraph MCP restart can take 180 s
let timer: ReturnType<typeof setInterval> | null = null;
let deadline = 0;

async function pollOnce(umo: string): Promise<void> {
  try {
    const res = await pluginExtensionApi.get<{
      status: OperationStatus;
      operation?: OperationKind;
      current_step?: string;
      messages?: string[];
      reason?: string | null;
    }>("spcode/operation-progress", { params: { umo } });
    const data = res.data?.data;
    if (!data || data.status === "idle") return;
    progress.value = {
      status: data.status,
      operation: data.operation ?? null,
      currentStep: data.current_step ?? "",
      messages: data.messages ?? [],
      reason: data.reason ?? null,
    };
    if (data.status === "done" || data.status === "failed") stopPolling();
  } catch {
    // Network hiccup: keep polling until the deadline; the POST's own
    // error handling covers real failures.
  }
  if (Date.now() > deadline) {
    progress.value = {
      ...progress.value,
      status: "failed",
      reason: "network_timeout",
    };
    stopPolling();
  }
}

export function useSpcodeOperationProgress() {
  /** Reset to running and poll every 500 ms until a terminal state. */
  function startPolling(umo: string): void {
    stopPolling();
    progress.value = {
      ...IDLE,
      status: "running",
      operation: progress.value.operation, // caller may preset; else set below
    };
    deadline = Date.now() + POLL_TIMEOUT_MS;
    timer = setInterval(() => void pollOnce(umo), POLL_INTERVAL_MS);
    void pollOnce(umo);
  }

  function stopPolling(): void {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  /** Stop polling and reset to idle (session switch / manual dismiss). */
  function clear(): void {
    stopPolling();
    progress.value = { ...IDLE };
  }

  return { progress, startPolling, stopPolling, clear };
}
```

> 注:`startPolling` 里保留 `operation` 的注释行若造成困惑,可简化为
> `progress.value = { ...IDLE, status: "running" }`——第一条 poll 返回
> 会立即带上真实 operation。按简化版实现。

- [ ] **Step 5: 跑测试确认通过**

```powershell
npx vitest run src/composables/useSpcodeOperationProgress.spec.ts
```

Expected: 5 个测试全 PASS

- [ ] **Step 6: Commit**

```powershell
cd F:\github\Astrbot\.worktrees\feat-silent-ops-progress
git add dashboard/src/composables/useSpcodeOperationProgress.ts dashboard/src/composables/useSpcodeOperationProgress.spec.ts
git commit -m "feat: add spcode operation progress polling composable"
```

---

## Task 8: 静默 API 客户端扩展(`useSpcodeProjectAutoLoad.ts`)

**Files:**
- Modify: `dashboard/src/composables/useSpcodeProjectAutoLoad.ts`
- Test: `dashboard/src/composables/useSpcodeSilentOps.spec.ts`(新建)

**Interfaces:**
- Consumes: Task 7 的 `useSpcodeOperationProgress`(本任务**不**接轮询,轮询由 Task 10 的调用方发起)。
- Produces:
  - `useSpcodeSilentOps()` → `{ silentLoadDirectory(req): Promise<ProjectLoadData>, silentUnload(umo): Promise<void>, silentCodegraphSet(umo, directory): Promise<void> }`
  - `SilentLoadDirectoryRequest = { umo, directory, noAgentsmd?, noCodegraph?, force?, create?, gitInit?, timeoutMs? }`
  - 失败一律 `throw ProjectLoadError(reason, data)`(复用现有类)
  - 既有 `silentLoad(req: SilentLoadRequest)` 签名与行为不变(内部改为委托 `silentLoadDirectory`)

- [ ] **Step 1: 写失败测试**

创建 `dashboard/src/composables/useSpcodeSilentOps.spec.ts`:

```typescript
// useSpcodeSilentOps.spec.ts
// Author: elecvoid243 @ 2026-08-06
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { post: vi.fn() },
}));

import { pluginExtensionApi } from "@/api/v1";
import { ProjectLoadError, useSpcodeSilentOps } from "./useSpcodeProjectAutoLoad";

const postMock = () => pluginExtensionApi.post as ReturnType<typeof vi.fn>;

function mockPostOk(extra: Record<string, unknown> = {}) {
  postMock().mockResolvedValue({
    data: { data: { success: true, reason: null, ...extra } },
  });
}

function mockPostFail(reason: string, messages: string[] = []) {
  postMock().mockResolvedValue({
    data: {
      data: { success: false, reason, substep_messages: messages },
    },
  });
}

describe("useSpcodeSilentOps", () => {
  beforeEach(() => postMock().mockReset());

  it("silentLoadDirectory posts full flag set", async () => {
    mockPostOk({ loaded: true, directory: "C:/p", umo: "u1" });
    const { silentLoadDirectory } = useSpcodeSilentOps();
    const data = await silentLoadDirectory({
      umo: "u1",
      directory: "C:/p",
      noAgentsmd: true,
      noCodegraph: true,
      force: true,
      create: true,
      gitInit: true,
    });
    expect(data.loaded).toBe(true);
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/project-load",
      {
        directory: "C:/p",
        umo: "u1",
        force: true,
        no_agentsmd: true,
        no_codegraph: true,
        create: true,
        git_init: true,
      },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("silentLoadDirectory omits false flags", async () => {
    mockPostOk({ loaded: true, directory: "C:/p", umo: "u1" });
    const { silentLoadDirectory } = useSpcodeSilentOps();
    await silentLoadDirectory({ umo: "u1", directory: "C:/p" });
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/project-load",
      { directory: "C:/p", umo: "u1", force: false },
      expect.anything(),
    );
  });

  it("silentLoadDirectory throws ProjectLoadError on failure", async () => {
    mockPostFail("path_unsafe", ["❌ path unsafe"]);
    const { silentLoadDirectory } = useSpcodeSilentOps();
    await expect(
      silentLoadDirectory({ umo: "u1", directory: "C:/x" }),
    ).rejects.toMatchObject({ reason: "path_unsafe" });
  });

  it("silentUnload posts umo only", async () => {
    mockPostOk({ unloaded: true, directory: "C:/p" });
    const { silentUnload } = useSpcodeSilentOps();
    await silentUnload("u1");
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/project-unload",
      { umo: "u1" },
      expect.anything(),
    );
  });

  it("silentUnload throws on failure", async () => {
    mockPostFail("feature_disabled");
    const { silentUnload } = useSpcodeSilentOps();
    await expect(silentUnload("u1")).rejects.toBeInstanceOf(ProjectLoadError);
  });

  it("silentCodegraphSet posts umo + directory", async () => {
    mockPostOk({ set: true, directory: "C:/p", mcp_restarted: true });
    const { silentCodegraphSet } = useSpcodeSilentOps();
    await silentCodegraphSet("u1", "C:/p");
    expect(postMock()).toHaveBeenCalledWith(
      "spcode/codegraph-set",
      { umo: "u1", directory: "C:/p" },
      expect.anything(),
    );
  });
});
```

- [ ] **Step 2: 跑测试确认失败**(`useSpcodeSilentOps` 未导出)

- [ ] **Step 3: 实现**

`useSpcodeProjectAutoLoad.ts` 改动:

1. 文件头注释更新:说明本文件同时是"手动 dialog 静默操作"的客户端。
2. 新增接口与函数(放在 `postLoad` 之后):

```typescript
export interface SilentLoadDirectoryRequest {
  umo: string;
  directory: string;
  noAgentsmd?: boolean;
  noCodegraph?: boolean;
  force?: boolean;
  create?: boolean;
  gitInit?: boolean;
  timeoutMs?: number;
}

/** Shared POST with timeout/abort plumbing, returns the flattened data dict. */
async function postSilent(
  path: string,
  body: Record<string, unknown>,
  timeoutMs: number,
): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await pluginExtensionApi.post(path, body, {
      signal: controller.signal,
    });
    return (res.data?.data ?? {}) as Record<string, unknown>;
  } catch (err) {
    throw new ProjectLoadError("network_timeout", {
      loaded: false,
      directory: String(body.directory ?? ""),
      umo: String(body.umo ?? ""),
      skipped_substeps: [],
      substep_messages: [String((err as Error)?.message ?? err)],
    });
  } finally {
    clearTimeout(timer);
  }
}

/** Read the flat envelope into a failure or return null on success. */
function raiseOnFailure(raw: Record<string, unknown>, umo: string): void {
  if (raw.success) return;
  throw new ProjectLoadError(
    (raw.reason as ProjectLoadReason | null) ?? "unknown",
    {
      loaded: Boolean(raw.loaded),
      directory: String(raw.directory ?? ""),
      umo,
      skipped_substeps: (raw.skipped_substeps as string[]) ?? [],
      substep_messages: (raw.substep_messages as string[]) ?? [],
      previous_directory: raw.previous_directory
        ? String(raw.previous_directory)
        : undefined,
      silent_reason: raw.silent_reason ? String(raw.silent_reason) : undefined,
    },
  );
}

export function useSpcodeSilentOps() {
  /** Silent project load for the manual dialog path (all flags explicit). */
  async function silentLoadDirectory(
    req: SilentLoadDirectoryRequest,
  ): Promise<ProjectLoadData> {
    const body: Record<string, unknown> = {
      directory: req.directory,
      umo: req.umo,
      force: req.force === true,
    };
    if (req.noAgentsmd) body.no_agentsmd = true;
    if (req.noCodegraph) body.no_codegraph = true;
    if (req.create) body.create = true;
    if (req.gitInit) body.git_init = true;
    const raw = await postSilent(
      "spcode/project-load",
      body,
      req.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
    raiseOnFailure(raw, req.umo);
    return {
      loaded: Boolean(raw.loaded),
      directory: String(raw.directory ?? ""),
      umo: String(raw.umo ?? req.umo),
      skipped_substeps: (raw.skipped_substeps as string[]) ?? [],
      substep_messages: (raw.substep_messages as string[]) ?? [],
      previous_directory: raw.previous_directory
        ? String(raw.previous_directory)
        : undefined,
      silent_reason: raw.silent_reason ? String(raw.silent_reason) : undefined,
    };
  }

  /** Silent project unload. Throws ProjectLoadError on failure. */
  async function silentUnload(umo: string): Promise<void> {
    const raw = await postSilent(
      "spcode/project-unload",
      { umo },
      DEFAULT_TIMEOUT_MS,
    );
    raiseOnFailure(raw, umo);
  }

  /** Silent codegraph set. Throws ProjectLoadError on failure. */
  async function silentCodegraphSet(
    umo: string,
    directory: string,
  ): Promise<void> {
    const raw = await postSilent(
      "spcode/codegraph-set",
      { umo, directory },
      200_000, // MCP restart timeout is 180 s on the backend
    );
    raiseOnFailure(raw, umo);
  }

  return { silentLoadDirectory, silentUnload, silentCodegraphSet };
}
```

> 既有 `silentLoad` / `postLoad` 保持不动(它们的 reshape 与重试逻辑
> auto-load 路径在用)。新函数与旧函数并存是有意的:auto-load 的
> idempotent-retry 语义只适用于 project 会话自动加载,不适用于手动 dialog。

- [ ] **Step 4: 跑测试**

```powershell
npx vitest run src/composables/useSpcodeSilentOps.spec.ts src/composables/useSpcodeOperationProgress.spec.ts
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```powershell
git add dashboard/src/composables/useSpcodeProjectAutoLoad.ts dashboard/src/composables/useSpcodeSilentOps.spec.ts
git commit -m "feat: add silent unload/codegraph-set clients for spcode"
```

---

## Task 9: `ProjectLoadDialog` submit 载荷结构化

**Files:**
- Modify: `dashboard/src/components/chat/ProjectLoadDialog.vue`
- Test: `dashboard/src/components/chat/ProjectLoadDialog.spec.ts`(改写断言)

**Interfaces:**
- Produces:
  - `export interface ProjectLoadSubmitPayload { mode: "project" | "codegraph" | "unload"; path?: string; noAgentsmd?: boolean; noCodegraph?: boolean; create?: boolean; gitInit?: boolean; force?: boolean; legacyText: string }`
  - `emit("submit", payload: ProjectLoadSubmitPayload)`(替代原 `submit: [text: string]`)
  - `legacyText` 保留原命令文本(`/project load ...` 含 replace 标志 / `/codegraph set ...` / `/project unload`),供无会话兜底路径逐字发送。

- [ ] **Step 1: 改写测试为先**

`ProjectLoadDialog.spec.ts`:把 `submitPath` helper 改为返回 emit 的 payload 对象,原命令文本断言改为断言 `payload.legacyText`,并补 payload 字段断言。示例(替换原 `it.each` 块):

```typescript
  it.each([
    [true, true, "/project load C:/projects/demo", false, false],
    [false, true, "/project load C:/projects/demo no_agentsmd", true, false],
    [true, false, "/project load C:/projects/demo no_codegraph", false, true],
    [
      false,
      false,
      "/project load C:/projects/demo no_agentsmd no_codegraph",
      true,
      true,
    ],
  ])(
    "maps AGENTS.md=%s and Codegraph=%s to the expected payload",
    async (loadAgentsMd, loadCodegraph, expectedText, noAgentsmd, noCodegraph) => {
      const wrapper = mountDialog();
      await openDialog(wrapper);
      const checkboxes = wrapper.findAll<HTMLInputElement>(
        'input[type="checkbox"]',
      );
      // (按原测试的 checkbox 操作逻辑设置)
      const payload = await submitPayload(wrapper, "C:/projects/demo");
      expect(payload).toMatchObject({
        mode: "project",
        path: "C:/projects/demo",
        noAgentsmd,
        noCodegraph,
        legacyText: expectedText,
      });
    },
  );
```

`submitPayload` helper(替代 `submitPath`,读 emitted):

```typescript
async function submitPayload(wrapper: ReturnType<typeof mountDialog>, path: string) {
  // 与原 submitPath 相同的填路径+点确认流程,最后:
  const events = wrapper.emitted("submit");
  return events?.at(-1)?.[0] as Record<string, unknown>;
}
```

overwrite 用例:断言 `payload.force === true` 且 `legacyText` 含 `replace`。
unload 用例:断言 `{ mode: "unload", legacyText: "/project unload" }`。
codegraph 模式:断言 `{ mode: "codegraph", path, legacyText: "/codegraph set ..." }`。

- [ ] **Step 2: 跑测试确认失败**(emit 的还是 string,`toMatchObject` 失败)

- [ ] **Step 3: 实现**

`ProjectLoadDialog.vue`:

1. script 顶部导出 payload 类型(放在 `<script setup lang="ts">` 外的
   普通 `<script lang="ts">` 块,或移到 `parseSpcodeStatus.ts` 旁的
   类型文件;选前者,与组件同文件):

```typescript
export interface ProjectLoadSubmitPayload {
  mode: "project" | "codegraph" | "unload";
  path?: string;
  noAgentsmd?: boolean;
  noCodegraph?: boolean;
  create?: boolean;
  gitInit?: boolean;
  force?: boolean;
  /** Legacy chat-command text, dispatched verbatim when no session exists. */
  legacyText: string;
}
```

2. `defineEmits` 改为:

```typescript
const emit = defineEmits<{
  submit: [payload: ProjectLoadSubmitPayload];
}>();
```

3. `onConfirm` 的 codegraph 分支:

```typescript
  if (props.commandMode === "codegraph") {
    addToPathHistory(trimmed);
    emit("submit", {
      mode: "codegraph",
      path: trimmed,
      legacyText: buildLoadCommand(
        prefix, trimmed, "codegraph", loadAgentsMd.value, loadCodegraph.value,
      ),
    });
    dialogOpen.value = false;
    return;
  }
```

4. project 分支末尾(原 `emit("submit", buildLoadCommand(...))` 处)改为:

```typescript
  addToPathHistory(trimmed);
  emit("submit", {
    mode: "project",
    path: trimmed,
    noAgentsmd: !effectiveLoadAgentsMd,
    noCodegraph: !effectiveLoadCodegraph,
    create: isCreate,
    gitInit: isCreate && autoInitGit.value,
    force: overwriteConfirmed, // 原 replace 分支的确认结果变量,按现有代码名
    legacyText: command, // 原 buildLoadCommand(...) 的结果(含 replace)
  });
  dialogOpen.value = false;
```

> 实现时按 onConfirm 现有局部变量名对齐(`extraFlags` / overwrite 确认
> 分支的变量);`legacyText` 就是现有逻辑拼出的那条命令,一行不改。

5. `onUnload`:

```typescript
function onUnload(): void {
  const prefix = props.wakePrefixes[0] || "/";
  emit("submit", { mode: "unload", legacyText: `${prefix}project unload` });
  dialogOpen.value = false;
}
```

6. `buildLoadCommand` 函数**保留不动**(legacyText 的生成器 + 既有单测覆盖)。

- [ ] **Step 4: 跑测试**

```powershell
npx vitest run src/components/chat/ProjectLoadDialog.spec.ts
```

Expected: 全 PASS

- [ ] **Step 5: Commit**

```powershell
git add dashboard/src/components/chat/ProjectLoadDialog.vue dashboard/src/components/chat/ProjectLoadDialog.spec.ts
git commit -m "feat: emit structured payload from project load dialog"
```

---

## Task 10: `ChatInput` 静默分发 + 失败 toast

**Files:**
- Modify: `dashboard/src/components/chat/ChatInput.vue`(`handleProjectLoadSubmit` / `handleCodegraphSubmit`)

**Interfaces:**
- Consumes: Task 7 `useSpcodeOperationProgress`;Task 8 `useSpcodeSilentOps`;Task 9 `ProjectLoadSubmitPayload`;`@/stores/toast` 的 `useToastStore().add({message, color, timeout, multiLine})`;`useSpcodeCodegraphStatus().refresh()`;`buildWebchatUmoDetails`(已 import)。
- Produces: 无新导出。

- [ ] **Step 1: 实现(此任务为接线,UI 断言在 Task 11/12 的组件测试覆盖;ChatInput 无独立 spec,走 Task 13 全量回归)**

替换 `handleProjectLoadSubmit`:

```typescript
/**
 * Handle a spcode project-load dialog submission: dispatch the silent
 * webapi call (no chat-history pollution) and drive the chip's live
 * progress via the operation-progress poller.
 *
 * Fallback: without a current session there is no umo to address the
 * POST at, so we keep the previous behavior — write the legacy command
 * text into the prompt and emit `send` (Chat.vue.sendSystemCommand
 * creates the session lazily).
 */
async function handleProjectLoadSubmit(
  payload: ProjectLoadSubmitPayload,
): Promise<void> {
  const session = props.currentSession;
  if (!session) {
    applyOptimisticProjectStatus(payload.legacyText);
    localPrompt.value = payload.legacyText;
    emit("send");
    return;
  }
  const umo = buildWebchatUmoDetails(
    session.session_id,
    Boolean(session.is_group),
  ).umo;
  operationProgress.startPolling(umo);
  try {
    if (payload.mode === "unload") {
      await silentOps.silentUnload(umo);
    } else if (payload.mode === "codegraph") {
      await silentOps.silentCodegraphSet(umo, payload.path!);
      await codegraphStatus.refresh();
    } else {
      await silentOps.silentLoadDirectory({
        umo,
        directory: payload.path!,
        noAgentsmd: payload.noAgentsmd,
        noCodegraph: payload.noCodegraph,
        force: payload.force,
        create: payload.create,
        gitInit: payload.gitInit,
      });
    }
  } catch (err) {
    // The chip's failed state + popover come from the progress ref; the
    // toast carries the one-line summary (last ❌ message) for immediate
    // visibility.
    const messages =
      (err as ProjectLoadError)?.data?.substep_messages ?? [];
    const summary =
      [...messages].reverse().find((m) => m.startsWith("❌")) ??
      String((err as Error)?.message ?? err);
    toastStore.add({
      message: summary,
      color: "error",
      multiLine: true,
      timeout: 6000,
    });
  } finally {
    // Authoritative chip state, whatever the outcome.
    await spcodeStatus.refresh(umo);
  }
}
```

`handleCodegraphSubmit` 删除,codegraph dialog 实例的 `@submit` 也指向
`handleProjectLoadSubmit`(payload.mode === "codegraph" 已覆盖)。

script 顶部新增:

```typescript
import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";
import {
  ProjectLoadError,
  useSpcodeSilentOps,
} from "@/composables/useSpcodeProjectAutoLoad";
import { useToastStore } from "@/stores/toast";
import type { ProjectLoadSubmitPayload } from "./ProjectLoadDialog.vue";
```

setup 区新增:

```typescript
const operationProgress = useSpcodeOperationProgress();
const silentOps = useSpcodeSilentOps();
const toastStore = useToastStore();
```

> `codegraphStatus` 已在文件中(若变量名不同按现有);`ProjectLoadDialog`
> 的 `@submit="handleProjectLoadSubmit"` 模板绑定不变(签名变了,值不变)。
> codegraph dialog 实例的 `@submit="handleCodegraphSubmit"` 改为
> `@submit="handleProjectLoadSubmit"`。

- [ ] **Step 2: 类型检查 + 相关测试**

```powershell
npx vue-tsc --noEmit -p tsconfig.json 2>&1 | Select-String -Pattern "ChatInput|ProjectLoad"
npx vitest run src/components/chat/ProjectLoadDialog.spec.ts
```

Expected: 无新增类型错误;dialog 测试全 PASS

- [ ] **Step 3: Commit**

```powershell
git add dashboard/src/components/chat/ChatInput.vue
git commit -m "feat: dispatch spcode dialog submissions via silent webapi"
```

---

## Task 11: `SpcodeProjectIndicator` loading / failed 态

**Files:**
- Modify: `dashboard/src/components/chat/SpcodeProjectIndicator.vue`
- Test: `dashboard/src/components/chat/SpcodeProjectIndicator.spec.ts`(新建)
- i18n: `dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/features/chat.json` 的
  `spcodeProjectLoad.indicator` 下新增 key(Task 13 统一做,本任务先用 key 占位)

**Interfaces:**
- Consumes: Task 7 `progress`;i18n key `spcodeProjectLoad.indicator.loading` / `.failed` / `.failedDetailTitle`。
- Produces: 事件契约不变(`open-load-dialog`);loading 时点击不 emit。

- [ ] **Step 1: 写失败测试**(渲染三态 + loading 点击不 emit + failed popover 内容)

```typescript
// SpcodeProjectIndicator.spec.ts
// Author: elecvoid243 @ 2026-08-06
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("@/api/v1", () => ({
  pluginExtensionApi: { get: vi.fn(), post: vi.fn() },
}));

import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";
import SpcodeProjectIndicator from "./SpcodeProjectIndicator.vue";

// i18n mock 方式参照同目录其他 spec(如 ProjectLoadDialog.spec.ts)的写法。

describe("SpcodeProjectIndicator progress states", () => {
  it("shows current step while loading and suppresses click", async () => {
    const { progress } = useSpcodeOperationProgress();
    progress.value = {
      status: "running",
      operation: "project_load",
      currentStep: "⏳ [2/3] codegraph init",
      messages: [],
      reason: null,
    };
    const wrapper = mount(SpcodeProjectIndicator);
    expect(wrapper.text()).toContain("⏳ [2/3] codegraph init");
    await wrapper.find("button").trigger("click");
    expect(wrapper.emitted("open-load-dialog")).toBeUndefined();
  });

  it("shows failed state with detail popover", async () => {
    const { progress } = useSpcodeOperationProgress();
    progress.value = {
      status: "failed",
      operation: "project_load",
      currentStep: "",
      messages: ["⏳ [1/3] init", "❌ path unsafe"],
      reason: "path_unsafe",
    };
    const wrapper = mount(SpcodeProjectIndicator);
    expect(wrapper.text()).toContain("失败");
    // popover activator exists only in failed state
    expect(wrapper.find(".sp-chip-details-btn").exists()).toBe(true);
  });

  it("non-project operations do not hijack the chip", () => {
    const { progress } = useSpcodeOperationProgress();
    progress.value = {
      status: "running",
      operation: "codegraph_set",
      currentStep: "🔄 restart",
      messages: [],
      reason: null,
    };
    const wrapper = mount(SpcodeProjectIndicator);
    expect(wrapper.text()).not.toContain("🔄 restart");
  });

  // 每个用例 afterEach: useSpcodeOperationProgress().clear()
});
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现**

`SpcodeProjectIndicator.vue` script 追加:

```typescript
import { useSpcodeOperationProgress } from "@/composables/useSpcodeOperationProgress";

const { progress } = useSpcodeOperationProgress();
const popoverOpen = ref(false);

const isProjectOp = computed(
  () =>
    progress.value.operation === "project_load" ||
    progress.value.operation === "project_unload",
);
const isLoading = computed(
  () => isProjectOp.value && progress.value.status === "running",
);
const isFailed = computed(
  () => isProjectOp.value && progress.value.status === "failed",
);
```

`icon` computed 顶部插入:

```typescript
  if (isLoading.value) return "mdi-loading";
  if (isFailed.value) return "mdi-alert-circle-outline";
```

`label` computed 顶部插入:

```typescript
  if (isLoading.value)
    return (
      progress.value.currentStep || tm("spcodeProjectLoad.indicator.loading")
    );
  if (isFailed.value) return tm("spcodeProjectLoad.indicator.failed");
```

`openLoadDialog` 改为:

```typescript
function openLoadDialog(): void {
  if (isLoading.value) return; // one silent operation at a time
  emit("open-load-dialog");
}
```

template 的 button 上加动态 class 与旋转动画类:

```html
      <button
        v-bind="tipProps"
        type="button"
        :class="[
          'sp-status-badge',
          {
            'sp-status-badge--empty': isEmptyState,
            'sp-status-badge--failed': isFailed,
          },
        ]"
        :aria-label="tooltipText"
        @click="openLoadDialog"
      >
```

button 内 label span 之后追加 failed 详情入口(复用 v-menu 模式):

```html
        <span class="sp-status-badge__label">{{ label }}</span>
        <v-menu
          v-if="isFailed"
          v-model="popoverOpen"
          location="bottom start"
          transition="none"
        >
          <template #activator="{ props: menuProps }">
            <button
              v-bind="menuProps"
              class="sp-chip-details-btn"
              type="button"
              :aria-label="tm('spcodeProjectLoad.indicator.failedDetailTitle')"
              @click.stop
            >
              <v-icon size="14">mdi-chevron-down</v-icon>
            </button>
          </template>
          <v-card min-width="320" max-width="480">
            <v-card-text>
              <div class="sp-chip-popover-title">
                {{ tm("spcodeProjectLoad.indicator.failedDetailTitle") }}
              </div>
              <pre class="sp-chip-popover-messages">{{ progress.messages.join("\n") }}</pre>
            </v-card-text>
          </v-card>
        </v-menu>
```

> 注意:嵌套 button 在 HTML 上不合法——把外层改为 `<div role="button"
> tabindex="0" @click="openLoadDialog" @keydown.enter="openLoadDialog">`,
> 或在 failed 态把 chevron 渲染在 button **之外**、包一层 flex div。
> 实现时选后者(改动小):外层 template 包 `<div class="sp-chip-wrap">`,
> badge button 与 v-menu 并列。

style 追加:

```css
.sp-status-badge--failed {
  color: rgb(var(--v-theme-error));
}
.sp-status-badge--failed .sp-status-badge__icon {
  color: rgb(var(--v-theme-error));
}
.sp-status-badge .mdi-loading {
  animation: sp-rotate 1s linear infinite;
}
@keyframes sp-rotate {
  to {
    transform: rotate(360deg);
  }
}
.sp-chip-details-btn {
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  padding: 0;
}
.sp-chip-popover-title {
  font-weight: 600;
  margin-bottom: 8px;
}
.sp-chip-popover-messages {
  margin: 0;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  max-height: 240px;
  overflow-y: auto;
}
```

`import { computed, ref } from "vue";`(原文件只 import 了 computed,补 ref)。

- [ ] **Step 4: 跑测试**(i18n key 缺失时先加 zh-CN 再跑,或 mock tm)

```powershell
npx vitest run src/components/chat/SpcodeProjectIndicator.spec.ts
```

- [ ] **Step 5: Commit**

```powershell
git add dashboard/src/components/chat/SpcodeProjectIndicator.vue dashboard/src/components/chat/SpcodeProjectIndicator.spec.ts
git commit -m "feat: add loading and failed states to spcode project chip"
```

---

## Task 12: `SpcodeCodegraphChip` loading 态

**Files:**
- Modify: `dashboard/src/components/chat/SpcodeCodegraphChip.vue`
- Test: 追加用例到 `SpcodeProjectIndicator.spec.ts` 同目录新建 `SpcodeCodegraphChip.spec.ts`

- [ ] **Step 1: 写失败测试**

```typescript
it("shows current step during codegraph set and suppresses click", async () => {
  const { progress } = useSpcodeOperationProgress();
  progress.value = {
    status: "running",
    operation: "codegraph_set",
    currentStep: "🔄 正在重启 codegraph MCP...",
    messages: [],
    reason: null,
  };
  const wrapper = mount(SpcodeCodegraphChip);
  expect(wrapper.text()).toContain("🔄 正在重启 codegraph MCP...");
  await wrapper.find("button").trigger("click");
  expect(wrapper.emitted("open-codegraph-dialog")).toBeUndefined();
});
```

- [ ] **Step 2: 实现**(与 Task 11 同构,只接 `operation === "codegraph_set"` 的 running/failed;failed 只 snackbar 已由 Task 10 负责,chip 红态文案用 `spcodeProjectLoad.codegraphChip.failed`)

`icon`/`label` computed 顶部分别插入:

```typescript
  if (isCgOp.value && progress.value.status === "running") return "mdi-loading";
```
```typescript
  if (isCgOp.value && progress.value.status === "running")
    return progress.value.currentStep || tm("spcodeProjectLoad.codegraphChip.setting");
  if (isCgOp.value && progress.value.status === "failed")
    return tm("spcodeProjectLoad.codegraphChip.failed");
```

点击守卫:`if (isCgOp.value && progress.value.status === "running") return;`

- [ ] **Step 3: 跑测试 + Commit**

```powershell
npx vitest run src/components/chat/SpcodeCodegraphChip.spec.ts
git add dashboard/src/components/chat/SpcodeCodegraphChip.vue dashboard/src/components/chat/SpcodeCodegraphChip.spec.ts
git commit -m "feat: add loading state to spcode codegraph chip"
```

---

## Task 13: i18n 三语种补 key + 全量回归

**Files:**
- Modify: `dashboard/src/i18n/locales/zh-CN/features/chat.json`
- Modify: `dashboard/src/i18n/locales/en-US/features/chat.json`
- Modify: `dashboard/src/i18n/locales/ru-RU/features/chat.json`

- [ ] **Step 1: 加 key**(`spcodeProjectLoad.indicator` 下与 `spcodeProjectLoad.codegraphChip` 下)

zh-CN:

```json
"loading": "加载中…",
"failed": "加载失败",
"failedDetailTitle": "失败详情",
```

```json
"codegraphChip": { "setting": "设置中…", "failed": "设置失败" }
```

en-US:

```json
"loading": "Loading…",
"failed": "Load failed",
"failedDetailTitle": "Failure details",
```

```json
"codegraphChip": { "setting": "Setting…", "failed": "Set failed" }
```

ru-RU:

```json
"loading": "Загрузка…",
"failed": "Ошибка загрузки",
"failedDetailTitle": "Подробности ошибки",
```

```json
"codegraphChip": { "setting": "Настройка…", "failed": "Ошибка настройки" }
```

> 注意 `indicator` 下已有 key,新 key 合并进既有对象;JSON 尾逗号检查。
> 若 `codegraphChip` 命名空间已存在则合并。

- [ ] **Step 2: 全量回归**

插件 worktree:

```powershell
cd F:\github\astrbot_plugin_spcode_toolkit\.worktrees\feat-silent-ops-progress
$env:PYTHONPATH="F:\github\Astrbot"; D:\anaconda3\python.exe -m pytest tests -q
ruff check tools tests
```

dashboard worktree:

```powershell
cd F:\github\Astrbot\.worktrees\feat-silent-ops-progress\dashboard
npx vitest run src/components/chat src/composables src/i18n
```

Expected: 全 PASS(含 `i18n.completeness.spec.ts` 的跨语种 key 一致性)

- [ ] **Step 3: Commit**

```powershell
git add dashboard/src/i18n/locales
git commit -m "feat: add i18n strings for spcode chip progress states"
```

---

## Self-Review 记录

- Spec 覆盖:§3.1→Task 1;envelope 修复(2026-08-06 增补)→Task 2;
  §3.2 三路径→Task 3/4/5;§3.3 端点→Task 3/4/5/6;
  §4.1→Task 7;§4.2→Task 8;§4.3→Task 11/12;§4.4→Task 9/10;i18n→Task 13。✅
- 类型一致性:`ProgressList / begin / append / finish / query` 全计划统一;
  `ProjectLoadSubmitPayload` 字段(Task 9 产出)与 Task 10 消费一致;
  `OperationProgress` 字段(Task 7 产出)与 Task 11/12 消费一致。✅
- 占位符:Task 9 Step 3 中 overwrite 确认变量名需按现有代码对齐(已注明);
  Task 3 测试中 `_state` 清空方式与 `ProjectManager` 构造签名注明先读源码确认。
  其余无 TBD。✅
