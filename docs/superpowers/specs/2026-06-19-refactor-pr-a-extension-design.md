# refactor-pr-a 分支扩展 — 把 main.py 真正瘦下来

> **For agentic workers:** 本文档是 **spec + plan 合并**。读完即可直接按"实施阶段"一节分阶段落地,无需再读 `writing-plans` 产出。每阶段任务以 checkbox (`- [ ]`) 标记,顺序执行。完成一个阶段再进入下一个,不要跳。
>
> **目标读者**:1) 用户 elecvoid243 审核;2) 后续 agent 按本文档落地代码。
>
> **关联文档**:
> - 现状基线:`refactor/pr-a-tool-registration` 分支当前状态(本文档 §4)
> - 同期补漏的设计依据:`docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md` / `2026-06-18-file_remove-trash-injection-design.md` / `2026-06-19-plan-mode-design.md` 等(已合入 main,本分支基线落后)

**状态**: 🚧 设计已批准,等待实施(2026-06-19)
**作者**: elecvoid243 + 主代理
**目标分支**: `refactor/pr-a-tool-registration`
**目标版本**: spcode_toolkit v2.10+
**优先级**: P0(必须)> P1(强烈建议)> P2(可选)

---

## 1. 动机与背景

### 1.1 初衷

`main.py` 在 2026-06-18 之前已膨胀到 3000+ 行,挤了 13 个工具类 + 4 个 web API + 4 个命令组 + 3 个 LLM 钩子 + codegraph MCP 生命周期。可读性、可测试性、可维护性都吃紧。

### 1.2 refactor 分支已做的工作(2026-06-18)

| 抽取到 | 内容 | 行数 |
|--------|------|------|
| `tools/registered_tools/`(8 文件) | 13 个 `FunctionTool` 工具类 + `_TodoToolBase` | ~1900 |
| `tools/_config.py` | `DEFAULT_CONFIG` / `_stats`+`record()` / git-diff 常量 / `/project` 注入文本 | ~150 |
| `tools/registered_tools/inta_shell_tools.py` | `set_component()` / `clear_component()` 替代模块级全局 | 包含在 1900 内 |
| 配套测试 | `tests/test_*_tool.py` 系列(7 个) | ~1200 |
| 迁移脚本 | `tests/_refactor_main.py`(一次性,**实施时删除**) | ~80 |

**当前主仓行数**:3155 行 → refactor 分支 1727 行(基线 −45%)。

### 1.3 还缺什么(对比 main,基线落后)

refactor 分支基于 2026-06-18 之前的 main,**未包含**此后加入的:

| 缺失内容 | 来源 commit | 行数 |
|----------|-------------|------|
| `handle_get_plan_mode` web API | feat/spcode-plan-mode-api | 44 |
| `_plan_mode_active` / `_plan_mode_active_count` | feat/spcode-plan-mode-api | 30 |
| `handle_get_git_worktrees` web API | feat/spcode-git-worktree | 148 |
| `_file_remove_inject_guidance` LLM 钩子 + 2 个常量 | fix/file_remove-injection | 25 + 12 |
| `_project_load_step` 独立函数 + `_ProjectLoadAbort` 异常类 | fix/project-load-abort | 49 + 8 |
| 4 个新 test 文件 | 同步新增 | ~900 |
| `tools/_helpers.py` 扩展(148 行 git 工具) | 同期 | 148 |
| `tools/todo_list.py` 扩展(79 行 TodoStore) | 同期 | 79 |
| 1 个 `astrbot_plugin_spcode_toolkit/` stray 目录 | 测试残留 | 17 项 |

### 1.4 真正的瓶颈

抽出 13 个工具类只是**最容易的部分**(机械式 move-and-rename)。剩下 main.py 仍有 ~1500 行结构性大块:

| 段 | 行数 | 性质 |
|----|------|------|
| 4 个 web API handler(项目状态 / git-diff / git-worktrees / plan-mode) | ~470 | 纯 I/O,可独立测试 |
| `/agentsmd` 命令组 + 5 个内部方法 | ~300 | 业务大块(从独立插件合入) |
| `/project` 命令组 + 7 个方法 | ~400 | 业务大块 |
| `/codegraph` 命令组 + 5 个方法 | ~250 | 业务大块 |
| `/plan` `/build` 命令 + 2 个钩子 | ~190 | plan 模式 |
| 3 个 `on_llm_request` 钩子 | ~150 | LLM 引导 |
| `_bootstrap_codegraph_mcp` / `_build_mcp_cfg` | ~65 | MCP 生命周期 |

---

## 2. 目标 / 非目标

### 2.1 目标(本 spec 完成时)

1. **main.py 缩到 600–800 行**(基线 3155 行 → 目标 ~25%)。具体行数不强求,只要满足 §5 "目标结构"。
2. **保留全部现有功能**:不丢功能、不改 API、不动 dashboard 端契约(已合入的 4 个 web API 必须保留响应 schema)。
3. **回归 `fix/project-load-abort` 修复**:refactor 当前把 abort 逻辑压扁了,本 spec 必须恢复独立函数 + 异常类(测试 `tests/test_project_cmd.py::test_*_abort_*` 必须能跑)。
4. **测试完整迁移**:8 个当前缺失的 test 文件搬过来,新模块配 6 个新 test 文件。
5. **导入关系干净**:不允许 `from main import X`;模块间通过显式函数调用或注册表传依赖。
6. **git worktree commit 干净**:7 个 commit(每阶段一个或多个),每个可独立 revert。

### 2.2 非目标(YAGNI)

- **不重写 dashboard 端**:web API 响应 schema 是契约,只动后端内部实现。
- **不引入新依赖**:不引入 LangChain / pydantic / 任何 ORM,沿用现有 stdlib + AstrBot。
- **不拆 inta_shell**:它已经是独立子包(`tools/inta_shell/`),不动。
- **不抽 dataclass / pydantic model 给工具返回值**:保持 dict + JSON 字符串契约(LLM 友好)。
- **不动 `_config_filter` / `_path_safety` / `codegraph` / `codegraph_cpp`**:这些已经是独立模块。
- **不重命名已注册的 web API 路径**:`/spcode/project-status` / `git-diff` / `git-worktrees` / `plan-mode` 全部保留。
- **不引入新配置项**:除非明确需要(目前不需要)。
- **不抽 dataclass 包装"4 步 project load 流水线"**:保持现在的 async generator 风格。

---

## 3. 目标 main.py 结构(实施完成后)

最终 main.py **只**留:

```python
# ── imports ──
import os
import time
from pathlib import Path
from collections import defaultdict
from astrbot.api import logger, star
from astrbot.api.star import Context, Star, register
from astrbot.api.event import AstrMessageEvent
from astrbot.api import sp
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

# ── 模块导入 ──
from .tools import (
    agentsmd as _agentsmd_mod,        # 已是独立模块
    codegraph as _codegraph_mod,
    codegraph_cpp,
    file_compare,
    inta_shell,
    _config as _cfg,                   # 新:_config 集中管理
    _config_filter,
    _helpers,
    _path_safety,
    todo_list,
)
from .tools.inta_shell import LocalInteractiveShellComponent
from .tools.registered_tools import (
    REGISTERED_TOOLS,
    set_component,
    clear_component,
    # 仍要直接拿 class 的:
    # (已经通过 REGISTERED_TOOLS 实例化,这里不需要)
)
# ↓ 新增的 6 个模块(本文档 §6)
from .tools import (
    agentsmd_cmd,
    codegraph_cmd,
    project_cmd,
    plan_cmd,
    dashboard_api,
    llm_hooks,
    codegraph_bootstrap,
)

# ── 插件类 ──
@register("astrbot_plugin_spcode_toolkit", "elecvoid243", "...")
class SPCodeToolkit(star.Star):
    def __init__(self, context, config=None): ...
    def initialize(self) -> None: ...
    async def terminate(self) -> None: ...

    # ── 命令路由(只留装饰器 + 委派)──
    @filter.command_group("codegraph", alias={"cg"})
    def codegraph(self): pass

    @filter.command_group("project")
    def project(self, event, sub_command="", *args): pass

    @filter.command_group("agentsmd")
    def agentsmd(self): pass

    @filter.command("plan")
    async def plan(self, event): ...

    @filter.command("build")
    async def build(self, event): ...

    # ── Web API 路由(只留装饰器 + 委派)──
    # (具体 handler 在 dashboard_api.py)

    # ── LLM 钩子(只留装饰器 + 委派)──
    @filter.on_llm_request()
    async def _auth_guard(self, event, req): ...
    @filter.on_llm_request()
    async def _plan_filter_tools(self, event, req): ...

    # ── 私有辅助(配置 / git)──
    @staticmethod
    def _flatten_config(config): ...
    def _git_binary(self) -> str: ...

    # ── 委派到独立模块 ──
    async def _project_router(self, event, sub_command, *args):
        return await project_cmd.router(self, event, sub_command, *args)
    async def _project_load_impl(self, event, directory):
        return await project_cmd.load_impl(self, event, directory)
    # ... 等等
```

**预期行数**:600–800 行(其中命令/钩子的装饰器委派占 ~250 行,插件类定义占 ~350 行)。

**禁止出现**:
- 任何 `class XxxTool(FunctionTool):` 定义(全在 `tools/registered_tools/`)
- 任何 web API handler 的实现体(全在 `dashboard_api.py`)
- 任何 sub-30-行 的独立函数(抽到 helpers 或对应模块)

---

## 4. 现状 vs 目标逐项对照

| 当前 main.py 段 | 行数 | 目标归宿 | 阶段 |
|-----------------|------|----------|------|
| 13 个工具类 | ~1900 | `tools/registered_tools/*` | ✅ 已完成 |
| `_DEFAULT_CONFIG` / `_stats` / `_record` | ~80 | `tools/_config.py` | ✅ 已完成 |
| `MAX_GIT_DIFF_BYTES` / `_GIT_DIFF_ENCODING` / `_make_git_diff_empty_envelope` | ~40 | `tools/_config.py` | ✅ 已完成 |
| `_PROJECT_GUIDANCE_MARKER` / `_PROJECT_CODEGRAPH_GUIDANCE` | ~20 | `tools/_config.py` | ✅ 已完成 |
| `_inta_component` / `_inta_default_cwd` 全局 | ~5 | `set_component` / `clear_component` | ✅ 已完成 |
| `_PLUGINS_TOOLS` 列表 | ~30 | `REGISTERED_TOOLS` | ✅ 已完成 |
| **`agentsmd` 命令组 + 4 个子命令 + 5 个内部方法** | **~300** | `tools/agentsmd_cmd.py` | **P1** |
| **`project` 命令组 + 3 个子命令 + 4 个 `_impl` + router + load_step** | **~400** | `tools/project_cmd.py` | **P2** |
| **`codegraph` 命令组 + 3 个子命令 + 2 个内部方法** | **~250** | `tools/codegraph_cmd.py` | **P2** |
| **`plan` / `build` 命令 + `_plan_filter_tools`** | **~190** | `tools/plan_cmd.py` | **P2** |
| **`handle_get_project_status` / `handle_get_git_diff` / `handle_get_git_worktrees` / `handle_get_plan_mode`** | **~470** | `tools/dashboard_api.py` | **P1** |
| **`_project_inject_codegraph_guidance` / `_file_remove_inject_guidance`** | **~55** | `tools/llm_hooks.py`(只 2 个;`_auth_guard` 留在 main) | **P1** |
| **`_agentsmd_inject_to_llm_request`** | **~60** | `tools/llm_hooks.py` | **P1** |
| **`_build_mcp_cfg` / `_bootstrap_codegraph_mcp`** | **~65** | `tools/codegraph_bootstrap.py` | **P2** |
| **`_flatten_config` / `_git_binary` / `_parse_files_changed`** | **~80** | 留在 main(太小,不值得抽) | — |
| **stray `astrbot_plugin_spcode_toolkit/` 目录** | — | 删除 | **P3** |
| **`tests/_refactor_main.py` 一次性脚本** | — | 删除 | **P3** |
| **`_FILE_REMOVE_GUIDANCE` / `_FILE_REMOVE_GUIDANCE_MARKER` 常量** | **~12** | `tools/_config.py` | **P0** |
| **`_make_git_worktrees_empty_envelope` 常量** | **~25** | `tools/_config.py` | **P0** |
| **缺失的 4 个 web API + plan helpers + file_remove hook** | — | 见 §7 | **P0** |
| **缺失的 `_project_load_step` 独立函数 + `_ProjectLoadAbort` 类** | **~57** | `tools/project_cmd.py` | **P0** |
| **缺失的 4 个 test 文件** | ~900 | 见 §10 | **P0** |

---

## 5. 实施阶段总览

| 阶段 | 任务数 | 估算 LOC 变化 | 验收 | commit |
|------|--------|---------------|------|--------|
| **P0** 补漏 | 4 项 | main.py +0 → 仍 ~1700 | 缺失功能全补回,4 个新 test 文件全 PASS | `fix(refactor): port missing main features` |
| **P1** 大块抽取 | 3 项 | main.py 1700 → ~900 | agentsmd / dashboard_api / llm_hooks 抽完,3 个新 test 文件全 PASS | `refactor(spcode): extract command groups, dashboard api, llm hooks` |
| **P2** 剩余抽取 | 3 项 | main.py 900 → ~700 | project / codegraph / plan 抽完,3 个新 test 文件全 PASS | `refactor(spcode): extract remaining command groups` |
| **P3** 清理 | 2 项 | main.py 700 → 600–800 | stray 目录删,migration 脚本删,AGENTS.md 更新 | `chore(refactor): cleanup strays + update AGENTS.md` |
| **集成验证** | 1 项 | — | `pytest tests/` 全 PASS(排除超时的 codegraph MCP) | `chore: integration verification` |

**总 commit 数**:6。**总耗时估算**:每阶段 30–60 分钟,全程 4–6 小时。

---

## 6. 阶段 P0:补漏(必须)

### P0-1:把 4 个缺失的 web API / plan helpers / file_remove hook 从 main 搬到 refactor

#### 来源
主仓 main 分支(基线 `7fd7e91`):
- `handle_get_plan_mode` (main.py:1850)
- `_plan_mode_active` (main.py:1820)
- `_plan_mode_active_count` (main.py:1840)
- `handle_get_git_worktrees` (main.py:1894)
- `_file_remove_inject_guidance` (main.py:2940)
- 常量 `_FILE_REMOVE_GUIDANCE` / `_FILE_REMOVE_GUIDANCE_MARKER` (main.py:216 附近)
- 常量 `_make_git_worktrees_empty_envelope` (main.py:96 附近)
- `tools/_helpers.py` 的扩展(148 行新增的 git 工具)

#### 操作

1. **从 main 复制以下符号到 refactor 的 main.py**:
   - 直接 `git show main:main.py | grep -A 200 "def handle_get_plan_mode"` 复制
   - 用 `git show main:tools/_helpers.py > tools/_helpers.py`(整体覆盖)
2. **`_FILE_REMOVE_GUIDANCE` / `_FILE_REMOVE_GUIDANCE_MARKER` 移到 `tools/_config.py`**(保持与 `_PROJECT_CODEGRAPH_GUIDANCE` 风格一致)
3. **`_make_git_worktrees_empty_envelope` 移到 `tools/_config.py`**
4. **更新 `tools/registered_tools/inta_shell_tools.py`** 的 import 路径(如果它引用了被搬走的常量)

#### 验收

```bash
# 1. 4 个端点都注册
grep -E "register_web_api.*(project-status|git-diff|git-worktrees|plan-mode)" main.py

# 2. file_remove LLM 钩子存在
grep -c "_file_remove_inject_guidance" main.py  # >= 1

# 3. 跑相关测试
pytest tests/test_plan_mode.py -v
pytest tests/test_file_remove_injection.py -v
pytest tests/test_git_worktrees.py -v
pytest tests/test_git_diff_worktree.py -v
pytest tests/test_helpers_git.py -v
```

#### 风险
- 4 个 test 文件目前不在 refactor 分支(主仓独占)。需要 `git checkout main -- tests/test_*.py` 拉过来。

---

### P0-2:恢复 `_project_load_step` 独立函数 + `_ProjectLoadAbort` 异常类

#### 来源
主仓 main:在 `_project_load_impl` 内部 inline 的"子步 abort"逻辑被 refactor 压扁了。

#### 设计

**恢复**:
```python
class _ProjectLoadAbort(Exception):
    """Raised by _project_load_step when any sub-step yields an error message.

    Raised after the step has already yielded a friendly summary message
    to the user. The caller (typically _project_load_impl) should catch
    this and return immediately — further steps are skipped.
    """
    def __init__(self, step_label: str) -> None:
        super().__init__(step_label)
        self.step_label = step_label


async def _project_load_step(event, sub_gen, step_label: str):
    """Forward sub-step messages; abort on first '❌' message.

    行为与现状完全一致(main.py:1453-1500),从 main.py 抽到 tools/project_cmd.py。
    """
    failed = False
    async for msg in sub_gen:
        yield msg
        if isinstance(msg.chain[0].text, str) and msg.chain[0].text.startswith("❌"):
            failed = True
    if failed:
        yield event.plain_result(
            f"❌ {step_label} 失败,/project load 中止。"
            "请根据上方错误信息修复后,重试 /project load <directory>。"
        )
        raise _ProjectLoadAbort(step_label)
```

#### 验收
- `pytest tests/test_project_cmd.py -k "abort" -v` 全 PASS
- 现有 5 个 project_cmd 集成测试也 PASS

#### 风险
- 这个修复来自 main 的 `7fd7e91`,refactor 分支基线更老,合入时不要 rebase(rebase 会冲突),直接 `git checkout main -- main.py` 拿相关行再 cherry-pick 改动。

---

### P0-3:从 main 同步 4 个新 test 文件

| test 文件 | 行数 | 来源 |
|-----------|------|------|
| `tests/test_file_remove_injection.py` | 109 | main 分支 |
| `tests/test_git_diff_worktree.py` | 327 | main 分支 |
| `tests/test_git_worktrees.py` | 204 | main 分支 |
| `tests/test_helpers_git.py` | 143 | main 分支 |
| `tests/test_plan_mode.py` | 247 | main 分支 |

**操作**:`git checkout main -- tests/test_file_remove_injection.py tests/test_git_diff_worktree.py tests/test_git_worktrees.py tests/test_helpers_git.py tests/test_plan_mode.py`

#### 验收
- 5 个文件**独立**能跑(不依赖其他新模块)
- 失败原因只可能是缺 §P0-1/§P0-2 改的代码;补完后必过

---

### P0-4:确保 `_conf_schema.json` 与 main 一致

主仓的 `_conf_schema.json` 可能在 refactor 期间新增了 `plan_mode_blocked_tools` / `worktree_param` 等配置项。

**操作**:
```bash
diff <(git show main:_conf_schema.json) _conf_schema.json
# 若有差异,执行:
git checkout main -- _conf_schema.json
```

#### 验收
- `python -c "import json; json.load(open('_conf_schema.json'))"` 不抛异常
- 配置测试如果存在必过

---

## 7. 阶段 P1:大块抽取(强烈建议)

### P1-1:抽 `tools/agentsmd_cmd.py`

#### 设计

**文件路径**:`tools/agentsmd_cmd.py`

**职责**:`/agentsmd` 命令组的全部命令、子命令、内部方法、`on_llm_request` 钩子。

**与 `tools/agentsmd.py` 的关系**:
- `tools/agentsmd.py`:已存在的**工具实现层**(TodoStore / INJECTION_MARKER / build_injection 等)
- `tools/agentsmd_cmd.py`(**本次新增**):**命令层**,调用 `tools/agentsmd.py` 的功能

**接口契约**(从 main.py 直接迁移,签名不变):

```python
# tools/agentsmd_cmd.py
from astrbot.api import filter
from astrbot.api.event import AstrMessageEvent

# 模块级常量(从 main.py 同步搬过来)
_AGENTSMD_LOADING_HINT = "..."


def register_commands(plugin, filter_decorator):
    """把 agentsmd 命令组挂到 plugin 上。

    用法(在 main.py 的 __init__ / initialize 中):
        from .tools import agentsmd_cmd
        agentsmd_cmd.bind(self)  # 让模块拿到 plugin 引用
    """
    pass


class AgentsmdCommandGroup:
    """把 agentsmd 命令组 + 5 个子方法封装到一个类。

    实例化后,所有方法都接收 plugin 实例作为第一个参数(从 self 改为 plugin 参数)。
    """

    def __init__(self, plugin): self.plugin = plugin

    @filter.command("agentsmd")
    async def cmd_agentsmd(self, event): ...   # 原 agentsmd()

    @filter.command("init")
    async def cmd_init(self, event, directory: str): ...   # 原 agentsmd_init

    @filter.command("load")
    async def cmd_load(self, event, directory: str): ...   # 原 agentsmd_load

    @filter.command("unload")
    async def cmd_unload(self, event): ...                 # 原 agentsmd_unload

    @filter.command("update")
    async def cmd_update(self, event): ...                 # 原 agentsmd_update

    # 内部 5 个方法
    async def _agentsmd_init(self, event, directory): ...
    async def _agentsmd_load(self, event, directory): ...
    async def _agentsmd_unload(self, event): ...
    async def _agentsmd_update(self, event): ...
    async def _agentsmd_inject_to_llm_request(self, event, req): ...

    @filter.on_llm_request()
    async def hook_inject(self, event, req): ...
```

**注意**:`@filter.command_group` 和 `@filter.command` 是**模块级装饰器**。两种实现风格:
- **风格 A(推荐)**:在 main.py 留装饰器,委派到 `agentsmd_cmd.run_xxx(plugin, ...)`。装饰器必须在模块加载时执行,不能在函数内动态装饰。
- **风格 B**:在 main.py 用 `from .tools.agentsmd_cmd import _AGENTSMD_LOADING_HINT` 拿到模块级常量,装饰器留 main,但函数体只委派。

**实施时选 A**,因为 agentsmd 命令数量多(5 子命令),委派会让 main.py 反而更冗长。

#### 测试

**新文件**:`tests/test_agentsmd_cmd.py`

**测试场景**(从 `tests/test_project_subcommand.py` 复制模式):
- `test_cmd_agentsmd_help` — 无子命令时显示帮助
- `test_cmd_init_success` — 成功路径
- `test_cmd_init_already_loaded` — 重复 init
- `test_cmd_init_invalid_dir` — 路径校验失败
- `test_cmd_load_then_unload` — 完整生命周期
- `test_cmd_update_no_loaded` — 异常路径
- `test_inject_guidance_idempotent` — 钩子防重复

#### 验收
- `pytest tests/test_agentsmd_cmd.py -v` 全 PASS
- main.py 行数减少 ~270

---

### P1-2:抽 `tools/dashboard_api.py`

#### 设计

**文件路径**:`tools/dashboard_api.py`

**职责**:4 个 web API handler 的实现。

**接口契约**:

```python
# tools/dashboard_api.py
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..main import SPCodeToolkit  # 仅类型提示,不允许运行时 import

from ._config import (
    make_git_diff_empty_envelope,
    make_git_worktrees_empty_envelope,
)


class DashboardAPI:
    """4 个 web API handler 的容器。

    通过构造时注入 plugin 实例,handler 内部访问 plugin._loaded_projects / _config / _tool_names。
    """

    def __init__(self, plugin: "SPCodeToolkit"):
        self.plugin = plugin

    async def get_project_status(self) -> dict: ...
    async def get_git_diff(self) -> dict: ...
    async def get_git_worktrees(self) -> dict: ...
    async def get_plan_mode(self) -> dict: ...


def register_handlers(plugin, context):
    """把 4 个端点注册到 AstrBot context。"""
    api = DashboardAPI(plugin)
    context.register_web_api(
        route="/spcode/project-status",
        view_handler=api.get_project_status,
        methods=["GET"],
        desc="..."
    )
    # ... 同样注册另外 3 个
    return api  # 供测试用
```

**`make_git_worktrees_empty_envelope`** 同步从 main.py 搬到 `tools/_config.py`:

```python
# tools/_config.py(新增)
def make_git_worktrees_empty_envelope(
    umo: str | None,
    reason: str,
    directory: str | None = None,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    return {
        "status": "ok",
        "data": {
            "loaded": False,
            "directory": directory,
            "umo": umo,
            "reason": reason,
            "worktrees": [],
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
        },
    }
```

#### main.py 变更

```python
# main.py initialize() 改为:
async def initialize(self):
    from .tools import dashboard_api
    self._dashboard_api = dashboard_api.register_handlers(self, self.context)
    # 删掉原来的 4 个 register_web_api 块
```

#### 测试

**新文件**:`tests/test_dashboard_api.py`

**测试矩阵**(每个 handler 至少 6 个用例):
- `test_get_project_status_no_project` → reason="no_project_loaded"
- `test_get_project_status_with_project` → loaded=True
- `test_get_git_diff_no_project` → reason="no_project_loaded"
- `test_get_git_diff_with_worktree_param` → ?worktree= 攻击向量(10 个)
- `test_get_git_diff_truncated` → >1MB 截断
- `test_get_git_worktrees_*` → 4 个 reason 分支
- `test_get_plan_mode_no_umo` → active=false
- `test_get_plan_mode_with_umo_active` → active=true
- `test_get_plan_mode_all_active_count` → 多 umo 计数

#### 验收
- `pytest tests/test_dashboard_api.py -v` 全 PASS
- 4 个 web API 端点全部仍能注册(用 `register_web_api` 验证)
- main.py 行数减少 ~430

---

### P1-3:抽 `tools/llm_hooks.py`

#### 设计

**文件路径**:`tools/llm_hooks.py`

**职责**:3 个 `on_llm_request` 钩子(2 个独立,`_auth_guard` 留在 main)。

**为什么 `_auth_guard` 不抽**:它需要直接访问 plugin 的 L1 鉴权状态(`_auth_admin_ids` / `_auth_blocked`),跟 plugin 生命周期绑得太紧,抽出去反而绕。

**接口契约**:

```python
# tools/llm_hooks.py
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..main import SPCodeToolkit

from astrbot.api import filter
from astrbot.api.event import AstrMessageEvent
from astrbot.core.agent.provider import ProviderRequest

from . import _agentsmd_mod
from ._config import _PROJECT_CODEGRAPH_GUIDANCE, _FILE_REMOVE_GUIDANCE


class LLMHooks:
    """3 个 on_llm_request 钩子的容器。"""

    def __init__(self, plugin: "SPCodeToolkit"):
        self.plugin = plugin

    @filter.on_llm_request()
    async def project_inject_codegraph_guidance(self, event, req): ...

    @filter.on_llm_request()
    async def file_remove_inject_guidance(self, event, req): ...

    @filter.on_llm_request()
    async def agentsmd_inject_to_llm_request(self, event, req): ...


def register_hooks(plugin) -> LLMHooks:
    """挂载 3 个钩子到 plugin。"""
    hooks = LLMHooks(plugin)
    plugin._llm_hooks = hooks  # 留引用供测试
    return hooks
```

**main.py 变更**:

```python
# main.py initialize() 改为:
async def initialize(self):
    from .tools import llm_hooks
    self._llm_hooks = llm_hooks.register_hooks(self)
    # 删掉原来的 3 个 @filter.on_llm_request 方法
```

**注意**:`@filter.on_llm_request()` 是**实例方法装饰器**。装饰后,方法会被 AstrBot 注册到 LLM 钩子链。抽到类里后,AstrBot 看到的还是 `plugin._llm_hooks.project_inject_codegraph_guidance`,但注册路径走 `LLMHooks` 实例方法。

**实施时确认**:AstrBot 的 `@filter.on_llm_request()` 是否支持**类实例方法**作为目标?查 AstrBot 文档或源码确认;若不支持,回退到"装饰器留 main,函数体只委派"风格。

#### 测试

**新文件**:`tests/test_llm_hooks.py`

**测试场景**:
- `test_project_inject_only_when_project_loaded` — 5 个用例
- `test_project_inject_handles_none_system_prompt` — None/空路径
- `test_project_inject_preserves_existing_prompt` — 已有 prompt 追加
- `test_file_remove_inject_only_when_tool_enabled` — 4 个用例
- `test_agentsmd_inject_*` — 5 个用例(从原 main.py 内部测试复制)

#### 验收
- `pytest tests/test_llm_hooks.py -v` 全 PASS
- main.py 行数减少 ~120

---

## 8. 阶段 P2:剩余抽取(可选,目标 600-800 行)

### P2-1:抽 `tools/project_cmd.py`

把 `project` 命令组 + 5 个 `_project_*_impl` + router + `_project_load_step` + `_ProjectLoadAbort` 抽到独立模块。

**接口契约**(参考 §6 P1-1 的 `AgentsmdCommandGroup` 模式):

```python
# tools/project_cmd.py
class ProjectCommandGroup:
    def __init__(self, plugin): self.plugin = plugin

    @filter.command("project")
    def cmd_project(self, event, sub_command="", *args): pass

    @filter.command("load")
    async def cmd_load(self, event, directory: str): ...

    @filter.command("unload")
    async def cmd_unload(self, event): ...

    @filter.command("status")
    async def cmd_status(self, event): ...

    async def router(self, event, sub_command, *args): ...
    async def load_impl(self, event, directory): ...
    async def load_step(self, event, sub_gen, step_label): ...  # P0-2 恢复
    async def unload_impl(self, event): ...
    async def status_impl(self, event): ...

    def get_loaded_project(self, umo) -> dict | None: ...


class _ProjectLoadAbort(Exception): ...  # P0-2 已定义
```

**测试新文件**:`tests/test_project_cmd_extract.py`(只测 P2 抽出的薄壳层;具体功能测试保留在 `test_project_cmd.py`)

**预计 main.py 减**:~350 行

---

### P2-2:抽 `tools/codegraph_cmd.py`

`codegraph` 命令组 + `codegraph_init` / `uninit` / `set` + 2 个内部方法。

**接口契约**:

```python
# tools/codegraph_cmd.py
class CodegraphCommandGroup:
    def __init__(self, plugin): self.plugin = plugin

    @filter.command("codegraph", alias={"cg"})
    def cmd_codegraph(self): pass

    @filter.command("init")
    async def cmd_init(self, event, directory: str): ...

    @filter.command("uninit")
    async def cmd_uninit(self, event, directory: str): ...

    @filter.command("set")
    async def cmd_set(self, event, directory: str): ...

    async def set_project(self, event, directory): ...
    async def init_or_uninit(self, event, directory, *, init: bool): ...
```

**测试新文件**:`tests/test_codegraph_cmd_extract.py`

**预计 main.py 减**:~220 行

---

### P2-3:抽 `tools/plan_cmd.py`

`plan` / `build` 命令 + `_plan_filter_tools` 钩子。`_auth_guard` 仍留 main。

**接口契约**:

```python
# tools/plan_cmd.py
class PlanCommandGroup:
    def __init__(self, plugin): self.plugin = plugin

    @filter.command("plan")
    async def cmd_plan(self, event): ...

    @filter.command("build")
    async def cmd_build(self, event): ...

    @filter.on_llm_request()
    async def plan_filter_tools(self, event, req): ...

    def plan_mode_active(self, umo) -> bool: ...
    def plan_mode_active_count(self) -> int: ...
```

**测试新文件**:`tests/test_plan_cmd_extract.py`

**预计 main.py 减**:~170 行

---

### P2-4:抽 `tools/codegraph_bootstrap.py`(可选)

`_build_mcp_cfg` / `_bootstrap_codegraph_mcp` / terminate 中清理 codegraph MCP 的代码。

**预计 main.py 减**:~65 行

---

## 9. 阶段 P3:清理

### P3-1:删除 stray `astrbot_plugin_spcode_toolkit/` 目录

```bash
git rm -r astrbot_plugin_spcode_toolkit/
```

(此目录是早期测试残留,17 项含 .git)

### P3-2:删除一次性迁移脚本

```bash
git rm tests/_refactor_main.py
```

### P3-3:更新 `AGENTS.md`

按本文档的"工具层"和"测试层"结构更新"目录结构与架构"一节。

### P3-4:更新 `README.md`

如有"项目结构"图,同步更新。

---

## 10. 测试迁移矩阵

### 10.1 现状(refactor 分支当前)

```
tests/
├── conftest.py                    (53 行,refactor 中存在)
├── test_code_check_tool.py        (新增,§P0 未动)
├── test_config.py                 (新增)
├── test_config_filter.py          (现有)
├── test_es_search_tool.py         (新增)
├── test_file_remove.py            (现有,test_file_remove_injection.py 缺失)
├── test_file_tools.py             (新增)
├── test_inta_shell.py             (现有)
├── test_inta_shell_tools.py       (新增)
├── test_registered_tools.py       (新增)
├── test_registered_tools_base.py  (新增)
├── test_todo_tools.py             (新增,test_todo_list.py 缺失)
├── _refactor_main.py              (一次性,§P3-2 删)
├── fixtures/
└── (以下 5 个 P0 缺失)
    ├── test_file_remove_injection.py
    ├── test_git_diff_worktree.py
    ├── test_git_worktrees.py
    ├── test_helpers_git.py
    └── test_plan_mode.py
```

### 10.2 目标(本 spec 完成时)

| test 文件 | 来源 | 归属阶段 |
|-----------|------|----------|
| `test_file_remove_injection.py` | 从 main 搬 | **P0** |
| `test_git_diff_worktree.py` | 从 main 搬 | **P0** |
| `test_git_worktrees.py` | 从 main 搬 | **P0** |
| `test_helpers_git.py` | 从 main 搬 | **P0** |
| `test_plan_mode.py` | 从 main 搬 | **P0** |
| `test_agentsmd_cmd.py` | 本 spec 新增 | **P1-1** |
| `test_dashboard_api.py` | 本 spec 新增 | **P1-2** |
| `test_llm_hooks.py` | 本 spec 新增 | **P1-3** |
| `test_project_cmd_extract.py` | 本 spec 新增 | **P2-1** |
| `test_codegraph_cmd_extract.py` | 本 spec 新增 | **P2-2** |
| `test_plan_cmd_extract.py` | 本 spec 新增 | **P2-3** |
| `_refactor_main.py` | 删除 | **P3-2** |
| 其余 ~12 个 test 文件 | 保留 | — |

### 10.3 命名规范(已存在,沿用)

- `tests/test_<module>.py` 测对应 `tools/<module>.py`
- `tests/test_<module>_extract.py` 测 P2 抽出的"命令组类"
- `tests/test_<module>_base.py` 测基类

---

## 11. 风险登记册

| 风险 | 等级 | 缓解 |
|------|------|------|
| 4 个 web API 响应 schema 改了导致 dashboard 端挂 | 🔴 高 | 严格从 main 复制实现,不改 dict 结构;`test_dashboard_api.py` 严格断言所有字段 |
| `abort` 逻辑回归(漏恢复 `_project_load_step`) | 🔴 高 | P0-2 必做;`test_project_cmd.py::test_*_abort_*` 必须 PASS |
| `_FILE_REMOVE_GUIDANCE` 漏搬导致 LLM 不优先用 file_remove | 🟡 中 | P0-1 含 5 个 test 覆盖 |
| `@filter.on_llm_request()` 不支持类实例方法 | 🟡 中 | P1-3 实施前**先验证**;若不支持,回退到"装饰器留 main"风格 |
| 抽 `agentsmd_cmd` 时 `register_web_api` 时机问题 | 🟢 低 | AstrBot `initialize()` 阶段调;`register_commands` 在那调 |
| 导入循环:`dashboard_api` 类型提示 `SPCodeToolkit` | 🟢 低 | 用 `TYPE_CHECKING` 包起来,运行时禁止 import |
| test 数据耦合:某些 test 用 `MagicMock` 假设 `self._tool_names` | 🟢 低 | 改用 plugin 实例 mock,或委派后通过 `plugin._tool_names` 访问 |
| 5 个 P0 缺失 test 文件与现有 test 共享 fixture | 🟢 低 | refactor 的 `conftest.py` 已经有大部分 fixture,拉文件时检查 import |

---

## 12. 实施流程(给 implementer 的 step-by-step)

> **新 agent 启动入口**:另见同目录的 `2026-06-19-refactor-p0-launcher.md`。
> 那是给"全新 session 的实施 agent"的开机 prompt,包含路径速查、必读文档、必看代码、commit 策略。
> 本节是 P0 之后的逐步命令;launcher 是 P0 启动时的上下文装配。两者**互不替代**。

### 12.1 准备

```bash
cd F:/github/astrbot_plugin_spcode_toolkit/.worktrees/refactor-pr-a-tool-registration
git status                              # 确认干净
git fetch origin main                   # 拉 main 引用
git rev-parse main                      # 记录:7fd7e91...
git rev-parse HEAD                      # 记录当前 refactor tip
```

### 12.2 P0 阶段(4 步,单 commit)

```bash
# P0-1:搬 web API + helpers
git checkout main -- main.py tools/_helpers.py
# 但只取需要的段(不能全量覆盖!)。具体:
# - 复制 main.py:1820-1890 (plan mode helpers)
# - 复制 main.py:1850-1893 (handle_get_plan_mode)
# - 复制 main.py:1894-2041 (handle_get_git_worktrees)
# - 复制 main.py:2940-2961 (_file_remove_inject_guidance)
# - 复制 main.py:96-130 (_make_git_worktrees_empty_envelope)
# - 复制 main.py:215-225 (_FILE_REMOVE_GUIDANCE 常量)
# 用 python 脚本精确复制段,不要 sed 整文件

# P0-2:恢复 _project_load_step
# 复制 main.py:1453-1500 + 异常类定义,放进 refactor 的 _project_load_impl 上方

# P0-3:拉 5 个 test 文件
git checkout main -- tests/test_file_remove_injection.py tests/test_git_diff_worktree.py tests/test_git_worktrees.py tests/test_helpers_git.py tests/test_plan_mode.py

# P0-4:同步 _conf_schema.json
diff <(git show main:_conf_schema.json) _conf_schema.json
git checkout main -- _conf_schema.json  # 如果有差异

# 跑测试
pytest tests/test_plan_mode.py tests/test_file_remove_injection.py tests/test_git_worktrees.py tests/test_git_diff_worktree.py tests/test_helpers_git.py -v
pytest tests/test_project_cmd.py -k "abort" -v

# commit
git add -A
git commit -m "fix(refactor): port missing main features to refactor branch

- Import 4 web API handlers (project-status, git-diff, git-worktrees, plan-mode) from main
- Restore _project_load_step + _ProjectLoadAbort (regression risk from 7fd7e91)
- Add _file_remove_inject_guidance hook + constants
- Add 5 missing test files
- Sync _conf_schema.json

Targets: bring refactor branch up to par with main HEAD before further extraction.
Spec: docs/superpowers/specs/2026-06-19-refactor-pr-a-extension-design.md §6"
```

### 12.3 P1 阶段(3 步,可分别 commit)

```bash
# P1-1:抽 agentsmd_cmd
# 创建 tools/agentsmd_cmd.py
# 创建 tests/test_agentsmd_cmd.py
# 修改 main.py 把 agentsmd 命令组委派出去
pytest tests/test_agentsmd_cmd.py -v
git add -A
git commit -m "refactor(spcode): extract agentsmd command group

- New: tools/agentsmd_cmd.py (AgentsmdCommandGroup, ~300 lines)
- New: tests/test_agentsmd_cmd.py
- main.py: replace @filter.command('agentsmd') with delegate

main.py: -270 lines.
Spec: docs/superpowers/specs/2026-06-19-refactor-pr-a-extension-design.md §7"
```

```bash
# P1-2:抽 dashboard_api
# 创建 tools/dashboard_api.py
# 在 tools/_config.py 加 make_git_worktrees_empty_envelope
# 创建 tests/test_dashboard_api.py
# 修改 main.py initialize() 用 register_handlers
pytest tests/test_dashboard_api.py -v
git commit -m "refactor(spcode): extract dashboard web API handlers

- New: tools/dashboard_api.py (DashboardAPI, 4 handlers)
- New: tools/_config.py: make_git_worktrees_empty_envelope
- New: tests/test_dashboard_api.py
- main.py initialize(): replace 4 register_web_api blocks with dashboard_api.register_handlers

main.py: -430 lines.
Spec: docs/superpowers/specs/2026-06-19-refactor-pr-a-extension-design.md §7"
```

```bash
# P1-3:抽 llm_hooks
# 创建 tools/llm_hooks.py
# 创建 tests/test_llm_hooks.py
# 修改 main.py initialize() 用 register_hooks
# 验证 @filter.on_llm_request() 支持类方法
pytest tests/test_llm_hooks.py -v
git commit -m "refactor(spcode): extract LLM request hooks

- New: tools/llm_hooks.py (LLMHooks, 3 hooks)
- New: tests/test_llm_hooks.py
- main.py: replace 3 @filter.on_llm_request methods with delegate

main.py: -120 lines.
Spec: docs/superpowers/specs/2026-06-19-refactor-pr-a-extension-design.md §7"
```

### 12.4 P2 阶段(3 步,可分别 commit)

类似 P1,每步:
- 抽命令组类
- 创建对应 test
- 跑测试
- commit

### 12.5 P3 阶段(1 个 commit)

```bash
git rm -r astrbot_plugin_spcode_toolkit/
git rm tests/_refactor_main.py
# 更新 AGENTS.md "目录结构"一节
# 更新 README.md "项目结构"图(如果有)
git add -A
git commit -m "chore(refactor): cleanup strays + update AGENTS.md

- Remove stray astrbot_plugin_spcode_toolkit/ directory (test residue)
- Remove one-shot migration script tests/_refactor_main.py
- Update AGENTS.md directory structure to reflect new modules
- Update README.md if needed

main.py: target reached (600-800 lines).
Spec: docs/superpowers/specs/2026-06-19-refactor-pr-a-extension-design.md §9"
```

### 12.6 集成验证

```bash
# 1. 完整 test
pytest tests/ -q --ignore=tests/test_codegraph_mcp.py  # 排除需要 MCP 的
# 期望: 全 PASS(已知 test_codegraph_mcp.py 慢且需 MCP,排除)

# 2. lint
ruff check .

# 3. 手动验证
python -c "from main import SPCodeToolkit; print('plugin imports OK')"

# 4. 行数
wc -l main.py
# 期望: 600-800
```

---

## 13. 验收清单(implementer 完成时核对)

- [ ] P0-1:4 个 web API / plan helpers / file_remove hook 全部到位
- [ ] P0-2:`_project_load_step` + `_ProjectLoadAbort` 恢复
- [ ] P0-3:5 个 test 文件全部 PASS
- [ ] P0-4:`_conf_schema.json` 与 main 一致
- [ ] P1-1:`tools/agentsmd_cmd.py` 抽取,`test_agentsmd_cmd.py` 全 PASS
- [ ] P1-2:`tools/dashboard_api.py` 抽取,`test_dashboard_api.py` 全 PASS,4 个端点都注册
- [ ] P1-3:`tools/llm_hooks.py` 抽取,`test_llm_hooks.py` 全 PASS,`@filter.on_llm_request()` 类方法支持已验证
- [ ] P2-1:`tools/project_cmd.py` 抽取
- [ ] P2-2:`tools/codegraph_cmd.py` 抽取
- [ ] P2-3:`tools/plan_cmd.py` 抽取
- [ ] P2-4:`tools/codegraph_bootstrap.py` 抽取(可选)
- [ ] P3-1:`astrbot_plugin_spcode_toolkit/` 删除
- [ ] P3-2:`_refactor_main.py` 删除
- [ ] P3-3:`AGENTS.md` 更新
- [ ] P3-4:`README.md` 更新
- [ ] 集成验证:pytest 全 PASS(排除 MCP),ruff clean,main.py 600-800 行
- [ ] 6 个 commit 全部提交,每个 commit 单独可 revert

---

## 14. 未来(不在本 spec 范围)

- 抽 `tools/inta_shell/` 子包为更细的 component / session / tools 三层
- 引入 `pydantic` 给 web API 响应加 schema 校验
- 把 `tools/registered_tools/` 子包用 `setuptools` entry_points 暴露,允许外部插件继承工具基类
- 用 `pytest-cov` 跟踪 main.py 覆盖率(目标 <30%)

这些不是 refactor 范围,留待后续 spec。
