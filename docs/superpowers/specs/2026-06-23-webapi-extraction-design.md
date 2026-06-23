# webapi 拆分 — main.py 端点代码独立化重构

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后,下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已通过(用户确认日期 2026-06-23,Q1=A, Q2=A, Q3=A, Q4=A, Q5=A, Q6=A, Q7=A, Q8=A)
**作者**: elecvoid243 @ 2026-06-23 08:46
**目标插件**: `astrbot_plugin_spcode_toolkit`(主干)
**前置版本**: 当前 HEAD(6 个 `/spcode/*` 端点全部已交付,本 spec 是工程重构,不改变任何端点的对外契约)
**配套 UI**: 无(纯服务端重构;dashboard 不感知)

## 背景与目标

`main.py` 当前 **4378 行**,堆叠了 6 个 Web API 端点 + 6 段重复的路由注册块 + 20+ 个端点专用辅助函数 + 1 个大型 plugin 类。文件已经超过 AstrBot 插件健康阈值的 4-5 倍,导致:

- 单次阅读 / 改动要在 4000+ 行间反复跳转
- 端点与端点之间没有清晰边界,git-diff 1170 行的 handler 把其它 5 个端点"挤"在文件尾
- 路由注册样板代码 6 次重复(try/except + 同样的 register_web_api 调用模板)
- 新增端点时,缺乏"在哪里写 handler"的明确位置,容易继续塞 main.py

**目标**:把 6 个 webapi 端点的代码全部从 `main.py` 抽到 `tools/webapi/` 子包;`main.py` 只保留插件类壳、命令路由(`/project`、`/codegraph` 等)、`register_webapi_routes()` 一次调用。最终 `main.py` 行数预计下降到 ~180 行(纯启动 + 命令分发)。

**硬约束(对外契约零变化)**:
- 6 个端点 URL、HTTP method、request/response shape **完全不变**
- dashboard / 前端不需要任何适配
- `_conf_schema.json`、`metadata.yaml` 不动
- 所有现有测试 `pytest tests/` 必须继续全绿

## 用户决策记录

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: 端点代码组织粒度** | **A** — 每端点一个独立文件(6 个端点 + 2 个共享文件) | 单文件最小(~50-1170 行);单一职责 |
| **Q2: handler 函数形态** | **A** — 模块级协程函数(不绑 plugin 类) | 真正解耦;测试要小改 |
| **Q3: 共享层拆分粒度** | **A** — `_envelopes.py` + `_helpers.py` 两个文件 | 响应骨架与通用工具分清楚 |
| **Q4: `_wrap` 适配层位置** | **A** — 内联在 `tools/webapi/__init__.py` | 6 段注册的胶水本就该集中 |
| **Q5: 测试迁移方式** | **A** — 测试侧改 import + 调用(无 main.py shim) | 一次性固化契约,不背长期兼容债 |
| **Q6: 旧 handler 兼容 shim** | **A** — 不留 | 信号清晰;新端点不会继续塞 plugin 类 |
| **Q7: git-diff 1170 行再切** | **A** — 不切,单文件 1170 行已比 4378 行好得多 | YAGNI;真有需要时再切 |
| **Q8: 目录命名** | **A** — `tools/webapi/`(与 `tools/inta_shell/` 平级) | 风格一致;子包而非顶层 |

## 目标目录结构

```
astrbot_plugin_spcode_toolkit/
├── main.py                        # 启动后预计 ~180 行
└── tools/
    └── webapi/                    # 【新增】本任务产物
        ├── __init__.py            # HANDLERS + register_webapi_routes() + _wrap()
        ├── _envelopes.py          # 共享响应骨架(empty envelope, _record 计时)
        ├── _helpers.py            # 共享工具(_JSONResponseCompat 等)
        ├── project_status.py      # GET /spcode/project-status
        ├── plan_mode.py           # GET /spcode/plan-mode
        ├── git_worktrees.py       # GET /spcode/git-worktrees
        ├── git_diff.py            # GET /spcode/git-diff
        ├── file_browser.py        # GET /spcode/file-browser
        └── file_restore.py        # POST /spcode/file-restore
```

`main.py` 的预期效果:
- 6 个 `handle_get_*` / `handle_post_*` 方法 **全部删除**
- 6 段 `register_web_api` 块 **合并为 1 次 `register_webapi_routes(self)`**
- 20+ 个 `_xxx` 模块级辅助函数 **全部删除**(随各自端点搬走)
- 整体行数从 4378 → ~180

## 核心设计

### 1. Handler 函数签名

所有 handler 统一为模块级协程函数,**首参数 `plugin`,其后参数 keyword-only**。

```python
# tools/webapi/git_diff.py
async def handle(
    plugin: "StarToolkitPlugin",  # TYPE_CHECKING 块声明 forward ref
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    ...
```

**设计要点**:
1. **首参数 `plugin`** —— 取代 `self`,让 handler 不绑死在 plugin 类上
2. **keyword-only 后续参数** —— `umo`、`worktree` 等 query/body 参数显式列出,API 契约一眼可见
3. **不写 `from main import StarToolkitPlugin`** —— 用 `TYPE_CHECKING` 块声明 forward reference,运行期零循环依赖
4. **返回 dict** —— 与原 handler 行为完全一致

### 2. 路由注册改造

`tools/webapi/__init__.py` 内单点注册:

```python
# tools/webapi/__init__.py

# 路由表(顺序敏感,先注册先匹配)
ROUTES: list[tuple[str, list[str], Callable, str]] = [
    ("/spcode/project-status",  ["GET"],  project_status.handle,
     "获取 spcode 当前会话已加载的项目信息(供 dashboard 调用)"),
    ("/spcode/plan-mode",       ["GET"],  plan_mode.handle,
     "获取 spcode 当前 plan-mode 状态(只读)"),
    ("/spcode/git-worktrees",   ["GET"],  git_worktrees.handle,
     "列出已加载项目的 git worktree 列表"),
    ("/spcode/git-diff",        ["GET"],  git_diff.handle,
     "获取已加载项目的未暂存 git diff"),
    ("/spcode/file-browser",    ["GET"],  file_browser.handle,
     "读取文件内容或列出单层目录"),
    ("/spcode/file-restore",    ["POST"], file_restore.handle,
     "恢复工作区中某一文件相对于 index 的改动"),
]

# 测试入口:方法名 -> handler 函数
HANDLERS: dict[str, Callable] = {
    "handle_get_project_status": project_status.handle,
    "handle_get_plan_mode":      plan_mode.handle,
    "handle_get_git_worktrees":  git_worktrees.handle,
    "handle_get_git_diff":       git_diff.handle,
    "handle_get_file_browser":   file_browser.handle,
    "handle_post_file_restore":  file_restore.handle,
}


def register_webapi_routes(plugin: "StarToolkitPlugin") -> None:
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

`main.py` 的 `initialize()` 由 6 段注册块变成:
```python
from .tools.webapi import register_webapi_routes
...
register_webapi_routes(self)
```

### 3. `_wrap` 适配层(AstrBot request → handler 关键字参数)

AstrBot 的 `view_handler` 签名是 `(*args, **kwargs)`,handler 内部所有 query/body 参数都通过 `request.*` 隐式访问。改为模块级函数后需要显式取值。`tools/webapi/__init__.py` 提供 `_wrap(handler, plugin)`:

```python
def _wrap(handler: Callable, plugin: "StarToolkitPlugin") -> Callable:
    """适配 view_handler 接口,把 request.* 参数映射到 handler 关键字参数。"""
    sig = inspect.signature(handler)
    accepts = set(sig.parameters) - {"plugin"}

    async def view(*args, **kwargs):
        request = kwargs.get("request") or (args[0] if args else None)
        call_kwargs: dict[str, Any] = {}

        if "umo" in accepts and request is not None:
            call_kwargs["umo"] = request.query.get("umo") or request.json.get("umo")
        if "worktree" in accepts and request is not None:
            call_kwargs["worktree"] = request.query.get("worktree")
        if "body" in accepts and request is not None:
            if request.method == "POST":
                try:
                    call_kwargs["body"] = await request.json()
                except Exception:
                    call_kwargs["body"] = {}
            else:
                call_kwargs["body"] = {}
        if "if_none_match" in accepts and request is not None:
            call_kwargs["if_none_match"] = request.headers.get("if-none-match")

        return await handler(plugin, **call_kwargs)

    return view
```

> **关键不变量**:handler 内部仍然用 `plugin.XXX` 访问插件状态,**业务代码 100% 不动**;只动"取值入口"

### 4. 辅助函数归属

按"谁使用"原则就近搬移,**严格遵守"主用端点拥有"原则**:

| 辅助函数 | 归属 |
|----------|------|
| `_parse_diff_status_map` | `git_diff.py` |
| `_parse_numstat_counts` | `git_diff.py` |
| `_build_stat_text` | `git_diff.py` |
| `_make_git_diff_empty_envelope` | `git_diff.py` |
| `_compute_file_etag` | `file_browser.py` |
| `_common_cache_headers` | `file_browser.py` |
| `_get_if_none_match` | `file_browser.py` |
| `_make_304_response` | `file_browser.py` |
| `_build_error_response` | `file_browser.py` |
| `_classify_entry` | `file_browser.py` |
| `_safe_lstat_mtime` | `file_browser.py` |
| `_make_entry` | `file_browser.py` |
| `_build_file_response` | `file_browser.py` |
| `_classify_oserror` | `file_browser.py` |
| `_build_directory_response` | `file_browser.py` |
| `_build_symlink_response` | `file_browser.py` |
| `_make_git_worktrees_empty_envelope` | `git_worktrees.py` |
| `_make_file_restore_empty_envelope` | `file_restore.py` |
| `_make_file_restore_success_envelope` | `file_restore.py` |
| `_validate_restore_file` | `file_restore.py` |
| `_record`(decorator) | `_envelopes.py`(共享) |
| `class _JSONResponseCompat` | `_helpers.py`(共享) |

**关于 `tools/_helpers.py` 中已存在的跨端点共享函数**(`_validate_worktree_param`、`_git_worktrees_response_skeleton`、`_file_restore_failure_response`、`_file_restore_success_response`、`_resolve_git_common_dir`、`_parse_git_worktree_porcelain`、`run_cmd`):
- **保留原位**!它们已经在 `tools/_helpers.py`,且有现成测试覆盖
- handler 改用 `from tools._helpers import _validate_worktree_param`(已存在的 import 模式)

### 5. 测试迁移策略

**现状**:~6 个 `test_*.py` 文件,约 **100+ 处** `await plugin.handle_get_xxx()` 直接调用

**迁移方案**(最小改动,一次性固化):

1. **`tests/conftest.py` 增加 fixture**:
   ```python
   @pytest.fixture
   def handlers():
       """Map: 旧方法名 -> 新模块级 handler。"""
       from tools.webapi import HANDLERS
       return HANDLERS
   ```

2. **测试侧改 1 行**(模式替换):
   ```python
   # Before
   result = await plugin.handle_get_git_diff()
   # After
   result = await handlers["handle_get_git_diff"](plugin)

   # Before
   result = await plugin.handle_get_git_diff(worktree="wt1")
   # After
   result = await handlers["handle_get_git_diff"](plugin, worktree="wt1")
   ```

3. **post 端点 body 改造**:`test_file_restore.py` 中传 body 的测试改为走新签名(若原 handler 直接用 `request.json()`,改为 `handle(plugin, body=...)` 形式)

**不在 main.py 保留兼容 shim**(`def handle_get_xxx(self): return handler(self, ...)`):
- 表面友好,实际把 "handler 已迁移" 的信号藏起来
- 未来加新端点还会有人继续挂到 plugin 类上
- 一次性迁移测试,把契约固化在 `HANDLERS` 字典

### 6. 鉴权与启动顺序

- **鉴权**:沿用 AstrBot 框架统一鉴权(本插件不实现独立鉴权);`L1 鉴权` 已在 plugin 类的 `__init__` 完成(管理员工具集)
- **启动顺序**:`__init__` → git 二进制探测 → `initialize()` → `register_webapi_routes(self)` 一次注册 6 个路由 → 命令路由注册
- **失败模式**:任一端点注册失败,只记 WARNING 不中断插件加载(与现有行为一致)

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| `self.xxx` 引用被遗漏(原 handler 用 `self`) | 搬迁后 grep 全文 `self\.[a-z_]+`,逐个改 `plugin.<name>`;`pytest tests/` 兜底 |
| `_wrap()` 适配层对 POST body 解析行为变化 | 对 `file-restore` 加专门 monkey-patch 测试,验证 `body` 参数正确传透 |
| `view_handler` 签名不匹配导致 AstrBot 拒绝注册 | 启动期集成测试(6 个端点全部 `register_web_api` 不抛错);加一条 unit test 验证 `_wrap()` 返回的协程可被 `inspect.iscoroutinefunction` 识别 |
| 跨循环依赖:`webapi/*` 引用 `plugin`,`main.py` 引用 `webapi` | webapi 用 `TYPE_CHECKING` forward ref;运行期零 import |
| `_record` 装饰器被多处复用导致搬迁路径错 | `_record` 放 `_envelopes.py`,所有使用方 `from ._envelopes import _record`;grep 全文 `def _record` 确认仅 1 个定义 |
| `main.py` 漏搬移导致重复定义 | ruff + 全局 grep 校验 6 个 handler 名 + 20+ 辅助函数名在 main.py 全文 0 命中 |
| git-diff handler 1170 行搬迁后行数波动 | 不切分,只搬迁;`wc -l tools/webapi/git_diff.py` 单独监控 |
| 大型 git diff 文件中嵌套了 `class StarToolkitPlugin` 之外的引用(如全局常量) | 搬迁前先 `grep -nE '^[A-Z_]+ =' main.py | head` 列举所有模块级常量,确认 webapi 需要的常量全部就近搬到 `webapi/_envelopes.py` 或 `webapi/__init__.py` |

## 执行顺序

1. **共享层先行**:`tools/webapi/_envelopes.py`、`tools/webapi/_helpers.py`(被多端点共用)
2. **依次迁移 6 个端点**(按文件大小升序,小文件先):
   - `project_status.py`(最小,~115 行)
   - `plan_mode.py`(~45 行)
   - `git_worktrees.py`(~150 行)
   - `file_browser.py`(~80 行 + 16 个 ETag/cache 辅助)
   - `file_restore.py`(~320 行)
   - `git_diff.py`(最大,~1170 行,**留最后单独审**)
3. **`tools/webapi/__init__.py`**:`_wrap()` + `HANDLERS` 字典 + `ROUTES` 列表 + `register_webapi_routes()`
4. **改造 `main.py`**:
   - 删除 6 段 `register_web_api` 块,替换为 `register_webapi_routes(self)`
   - 删除 6 个 `handle_*` 方法
   - 删除搬走的模块级辅助函数
   - 删除搬走的模块级常量(若仅 webapi 用)
5. **改造 `tests/conftest.py` + 各 `test_*.py` 调用方式**
6. **验证**:
   - `ruff check .` 0 error
   - `pytest tests/` 全 PASS
   - `wc -l main.py` ≤ 200
   - `grep -nE "def handle_get_|def handle_post_" main.py` 0 命中

## 不在本 spec 范围内

- 任何端点的功能/契约修改
- 任何配置 schema 修改
- 任何 dashboard 适配
- 性能优化(本重构只搬代码,不改算法)
- 文档重写(README 提及的"web API 端点"小节后续 PR 跟进)

## 验收标准

| 指标 | 目标 | 验证方法 |
|------|------|----------|
| `main.py` 行数 | ≤ 200 | `wc -l main.py` |
| 端点 handler 全部外迁 | 6/6 | `grep -nE "def handle_get_\|def handle_post_" main.py` 0 命中 |
| `tools/webapi/` 文件数 | 8(`__init__` + 2 共享 + 5 端点 + git_diff) | `ls tools/webapi/` |
| `ruff check .` | 0 error | ruff |
| `pytest tests/` | 100% PASS(基线 = 当前 HEAD) | pytest |
| dashboard 对外契约 | 完全不变 | 手动 curl 6 个端点,response shape 与 v1 一致 |
