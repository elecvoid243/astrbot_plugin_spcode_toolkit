# AGENTS.md - spcode 工具箱

> **当前版本: v2.20** · Author: elecvoid243 · 最后更新: 2026-07-17

本文件供在本仓库工作的编程代理（coding agent / LLM agent）使用，描述项目结构、构建/测试命令与代码规范。修改任何代码前请先通读本文件。

## 项目信息

- **项目名称**: spcode 工具箱 (`astrbot_plugin_spcode_toolkit`)
- **项目绝对路径**: `F:\github\astrbot_plugin_spcode_toolkit`
- **项目类型**: AstrBot 插件（Python）
- **Python 版本**: 3.10+（用到 `list[str]`、`X | None` 等内置泛型）
- **依赖管理**: `requirements.txt`（含 lint 工具与运行时依赖：`ruff`、`cpplint`、`astyle`、`send2trash`；AstrBot 宿主环境装运行时依赖）
- **测试框架**: pytest
- **目标平台**: 以 Windows 为首选（`es.exe` / PowerShell 路径）；Linux/macOS 有 fallback
- **仓库**: https://github.com/elecvoid243/astrbot_plugin_spcode_toolkit

## 构建 / Lint / 测试命令

> 本项目为 Python AstrBot 插件，**不使用 msbuild**，无需独立构建步骤。打包/加载由 AstrBot 宿主在启动时读取 `main.py` 完成。

### Lint

```bash
# Python 代码 lint（项目根目录）
ruff check .

# Python 代码自动修复（可选，提交前请审阅 diff）
ruff check . --fix

# C/C++ lint（单文件）
cpplint path/to/file.cpp
cpplint path/to/file.h
```

> **注意**: 当需要 lint Python/C++ 文件时，优先使用内置 `code_check` 工具（运行 ruff / cpplint），不要通过 `subprocess.run` 或 shell 调用。格式化同理使用内置 `code_format` 工具。

### 测试（pytest）

```bash
# 运行全部测试
pytest tests/

# 全部测试 + 详细输出
pytest tests/ -v

# 单个测试文件（按模块/子领域命名，与 tools/ 对应）
pytest tests/test_agentsmd.py
pytest tests/test_codegraph_cmd.py
pytest tests/test_codegraph_mcp.py
pytest tests/test_codegraph_subsystem.py
pytest tests/test_codegraph_status.py
pytest tests/test_code_format.py
pytest tests/test_config_filter.py
pytest tests/test_docs_crud.py
pytest tests/test_file_browser.py
pytest tests/test_file_discard_hunk.py
pytest tests/test_file_name_search.py
pytest tests/test_file_remove.py
pytest tests/test_file_remove_injection.py
pytest tests/test_file_restore.py
pytest tests/test_file_search.py
pytest tests/test_git_branches.py
pytest tests/test_git_branches_lifecycle.py
pytest tests/test_git_branch_create.py
pytest tests/test_git_branch_delete.py
pytest tests/test_git_branch_switch.py
pytest tests/test_git_commit.py
pytest tests/test_git_diff.py
pytest tests/test_git_diff_worktree.py
pytest tests/test_git_endpoint_preflight.py
pytest tests/test_git_file.py
pytest tests/test_git_init.py
pytest tests/test_git_init_preflight.py
pytest tests/test_git_log.py
pytest tests/test_git_log_parsers.py
pytest tests/test_git_log_smoke_e2e.py
pytest tests/test_git_repo_check.py
pytest tests/test_git_revert.py
pytest tests/test_git_show.py
pytest tests/test_git_stage.py
pytest tests/test_git_status.py
pytest tests/test_git_status_parsers.py
pytest tests/test_git_unstage.py
pytest tests/test_git_workflow_e2e.py
pytest tests/test_git_worktrees.py
pytest tests/test_git_worktree_add.py
pytest tests/test_git_worktree_e2e.py
pytest tests/test_git_worktree_lock.py
pytest tests/test_git_worktree_porcelain_locked.py
pytest tests/test_git_worktree_remove.py
pytest tests/test_git_worktree_unlock.py
pytest tests/test_guidance_text.py
pytest tests/test_helpers.py
pytest tests/test_helpers_commit_ref.py
pytest tests/test_helpers_encoding.py
pytest tests/test_helpers_git.py
pytest tests/test_helpers_worktree.py
pytest tests/test_inta_shell.py
pytest tests/test_llm_inject.py
pytest tests/test_no_window_kwargs.py
pytest tests/test_path_safety.py
pytest tests/test_plan_mode.py
pytest tests/test_project_cmd.py
pytest tests/test_project_status.py
pytest tests/test_project_subcommand.py
pytest tests/test_reason_code.py
pytest tests/test_reason_codes_spec_b.py
pytest tests/test_run_git_async.py
pytest tests/test_security_admin.py
pytest tests/test_security_plan_mode.py
pytest tests/test_smoke_todo_refactor.py
pytest tests/test_stats.py
pytest tests/test_todo_guidance_inject.py
pytest tests/test_todo_list.py
pytest tests/test_todo_split_tools.py
pytest tests/test_validate_repo_relative_file.py
pytest tests/test_webapi_end_to_end.py
pytest tests/test_webapi_helpers_smoke.py

# 单个测试用例（按函数名）
pytest tests/test_file_remove.py::test_specific_function_name

# 单个测试用例（按类+方法）
pytest tests/test_inta_shell.py::TestClassName::test_method_name

# 按关键字筛选
pytest tests/ -k "todo"
pytest tests/ -k "codegraph"
pytest tests/ -k "path_safety"
pytest tests/ -k "worktree"

# 首个失败即停止，便于定位
pytest tests/ -x

# 显示 print / logging 输出
pytest tests/ -s

# 覆盖率（可选）
pytest tests/ --cov=tools --cov-report=term-missing
```

### 加载到 AstrBot

将项目根目录放入 AstrBot 插件目录，启动 AstrBot 即可加载 `main.py`。本地开发可用符号链接而非复制。

## 目录结构与架构

```text
astrbot_plugin_spcode_toolkit/
├── main.py                       # 插件入口：注册工具/命令/事件 + L1 鉴权 + 多个 @filter.on_llm_request 钩子
├── metadata.yaml                 # 插件元信息（AstrBot 加载识别）
├── _conf_schema.json             # 配置 schema（分组结构，_flatten_config 拍平）
├── requirements.txt              # lint + 运行时依赖：ruff, cpplint, astyle, send2trash
├── README.md                     # 项目说明（面向用户/社区）
├── AGENTS.md                     # 本文件（面向 coding agent）
│
├── data/                         # 数据/资源（随插件分发）
│   ├── t2i_templates/            # HTML 模板
│   ├── temp/                     # 临时文件
│   └── workspaces/               # 沙箱化工作区
│
├── docs/                         # 项目文档 / 评审记录
│   ├── api/                      # Web API 前端消费参考
│   │   ├── web-api.md
│   │   ├── webapi-file-discard-hunk-api.md
│   │   ├── webapi-git-init-branch-revert-api.md
│   │   ├── webapi-git-repo-check-api.md
│   │   ├── webapi-git-worktree-mgmt-api.md
│   │   ├── v2.17.0-endpoints-frontend.md
│   │   └── v2.17.1-git-init-force-frontend-notice.md
│   ├── webapi_endpoints_report.md
│   ├── webapi-git-show-api.md
│   ├── webapi-git-workflow-api.md
│   ├── code_format_response_spec.md
│   ├── file_remove_review_2026-06-07.md
│   └── superpowers/              # specs/（设计）+ plans/（实施计划）+ reviews/ + handoffs/
│
├── tests/                        # pytest 测试（75+ 文件，与 tools/ 对应）
│   ├── __init__.py
│   ├── conftest.py               # 共享 fixtures
│   ├── test_*.py                 # 见上方"测试"命令清单
│   └── fixtures/                 # 测试夹具（样本文件/目录）
│       ├── webchat_todo_fixture.md
│       └── cpp_sample/           # C++ 样本（main.cpp / widget.cpp / widget.h）
│
└── tools/                        # 工具实现层（子包化，PR-0~PR-7 拆分自 main.py）
    ├── __init__.py
    ├── _helpers.py               # [内部] run_cmd / unwrap / proposal_reply / run_sync /
    │                             #            _is_valid_ref_name / _validate_worktree_param
    ├── _config.py                # [内部] 配置读取辅助
    ├── _config_filter.py         # [内部] enabled_tools 过滤逻辑 + 快捷组名展开
    │                             #            (ALL_TOOL_NAMES / filter_enabled_tools / _TOOL_GROUPS)
    ├── _path_safety.py           # [内部] 统一路径安全校验（系统黑名单 + 用户黑名单）
    ├── _codegraph_mcp.py         # [内部] codegraph 启动器检测 + CLI 构造（legacy，新代码用 codegraph/ 子包）
    ├── _code_detect.py           # [内部] 代码语言/扩展名检测
    ├── _guidance_text.py         # [内部] system_prompt 注入指引文本常量
    ├── _stats.py                 # [内部] 统计辅助
    │
    ├── agentsmd/                 # AGENTS.md 生成/扫描/注入子系统
    │   ├── __init__.py           #   AgentsmdSubsystem facade
    │   ├── _core.py
    │   ├── _handlers.py          #   on_llm_request 注入逻辑
    │   └── _state.py             #   per-umo state
    │
    ├── codegraph/                # codegraph MCP + 命令管理子系统（PR-6 拆分）
    │   ├── __init__.py           #   CodegraphManager / bootstrap_mcp / shutdown_mcp / state
    │   ├── bootstrap.py          #   MCP server 启动 + 配置构造 (build_mcp_cfg)
    │   ├── manager.py            #   /codegraph init/uninit/set handler
    │   └── state.py              #   模块级状态（task 引用 + per-dir lock）
    │
    ├── project/                  # /project load/unload/status 子系统
    │   ├── __init__.py           #   ProjectManager facade
    │   ├── manager.py            #   命令分发 + 状态查询 (get_loaded_project)
    │   ├── pipeline.py           #   load/unload 流水线
    │   └── state.py              #   per-umo 已加载项目 state（模块级单例）
    │
    ├── security/                 # 鉴权 + plan/build 模式
    │   ├── __init__.py           #   check_is_admin / PlanModeController
    │   ├── admin.py              #   L1 管理员鉴权
    │   └── plan_mode.py          #   plan/build 模式控制器（过滤工具 + reminder 注入）
    │
    ├── function_tools/           # 16 个 LLM FunctionTool 类（一文件一工具，PR-2 拆分）
    │   ├── __init__.py           #   ALL_TOOL_CLASSES 集中注册表
    │   ├── _common.py            #   record_and_run 模板
    │   ├── code_check.py
    │   ├── code_format.py        #   v2.14（写入工具）
    │   ├── es_search.py
    │   ├── file_diff.py          #   astrbot_file_compare
    │   ├── file_remove.py
    │   ├── todo_base.py          #   _TodoToolBase 共用基类
    │   ├── todo_create.py
    │   ├── todo_query.py
    │   ├── todo_add.py           #   v2.12 拆分自 todo_modify
    │   ├── todo_update.py        #   v2.12 拆分自 todo_modify
    │   ├── todo_delete.py        #   v2.12 拆分自 todo_modify
    │   ├── todo_clear.py
    │   ├── todo_modify.py        #   legacy（v2.12 已从注册表移除，保留后端兼容）
    │   └── inta_shell_{start,send,read,stop,list}.py
    │
    ├── inta_shell/               # 交互式 Shell 复合工具集
    │   ├── __init__.py
    │   ├── component.py          #   持久子进程管理组件
    │   ├── tools.py              #   5 个 Shell 工具的纯函数实现
    │   ├── session_models.py     #   会话数据模型
    │   ├── paths.py              #   workspace 路径解析
    │   └── runtime.py            #   模块级单例（component + default_cwd）
    │
    ├── llm_inject/               # system_prompt 指引注入辅助
    │   └── __init__.py           #   inject_guidance（marker 防重复）
    │
    ├── code_check.py             # [legacy 入口] code_check 业务实现
    ├── code_format.py            # [legacy 入口] code_format 业务实现
    ├── es_search.py              # [legacy 入口] es_search 业务实现
    ├── file_compare.py           # [legacy 入口] 文件差异业务实现
    ├── file_remove.py            # [legacy 入口] 删除业务实现
    ├── todo_list.py              # [legacy 入口] v2.6+ stub，保留兼容
    │
    └── webapi/                   # Web API 层（31 条路由记录 / 28 个唯一端点，每端点一文件）
        ├── __init__.py           #   ROUTES 路由表 + HANDLERS 别名 + _wrap() 适配器 + register_webapi_routes()
        ├── _helpers.py           #   ReasonCode / _make_envelope / _git_endpoint_preflight /
        │                         #   _git_init_preflight / _validate_repo_relative_file /
        │                         #   _run_git_async / _JSONResponseCompat /
        │                         #   _compute_git_etag / _compute_porcelain_diffs
        ├── project_status.py     #   GET    /spcode/project-status
        ├── plan_mode.py          #   GET    /spcode/plan-mode
        ├── git_worktrees.py      #   GET    /spcode/git-worktrees
        ├── git_diff.py           #   GET    /spcode/git-diff
        ├── git_status.py         #   GET    /spcode/git-status          (v2.13)
        ├── git_log.py            #   GET    /spcode/git-log             (v3.7)
        ├── git_show.py           #   GET    /spcode/git-show            (v3.8)
        ├── git_file.py           #   GET    /spcode/git-file            (spec B)
        ├── git_branches.py       #   GET    /spcode/git-branches        (v2.17.0)
        ├── git_repo_check.py     #   GET    /spcode/git-repo-check      (v2.18.0)
        ├── codegraph_status.py   #   GET    /spcode/codegraph-status    (v2.14.x)
        ├── file_browser.py       #   GET    /spcode/file-browser
        ├── git_init.py           #   POST   /spcode/git-init            (v2.17.0, 唯一豁免 preflight)
        ├── git_stage.py          #   POST   /spcode/git-stage           (v3.7)
        ├── git_unstage.py        #   POST   /spcode/git-unstage         (v3.7)
        ├── git_commit.py         #   POST   /spcode/git-commit          (v3.7)
        ├── git_revert.py         #   POST   /spcode/git-revert          (v2.17.0)
        ├── git_branch_create.py  #   POST   /spcode/git-branch-create   (v2.17.0)
        ├── git_branch_delete.py  #   POST   /spcode/git-branch-delete   (v2.17.0)
        ├── git_branch_switch.py  #   POST   /spcode/git-branch-switch   (v2.17.0)
        ├── file_restore.py       #   POST   /spcode/file-restore
        ├── file_discard_hunk.py  #   POST   /spcode/file-discard-hunk   (v2.16.0)
        ├── file_search.py        #   POST   /spcode/file-search         (v2.15.0)
        ├── file_name_search.py   #   POST   /spcode/file-name-search    (v2.15.0)
        ├── git_worktree_add.py   #   POST   /spcode/git-worktree-add    (v2.14.0)
        ├── git_worktree_remove.py#   POST   /spcode/git-worktree-remove (v2.14.0)
        ├── git_worktree_lock.py  #   POST   /spcode/git-worktree-lock   (v2.14.0)
        ├── git_worktree_unlock.py#   POST   /spcode/git-worktree-unlock (v2.14.0)
        └── docs_crud.py          #   POST/PATCH/DELETE /spcode/docs     (spec B, 三方法复用一路径)
```

### 架构分层

1. **入口层** `main.py`
   - 在 AstrBot 启动时被加载
   - 注册 AstrBot **工具**（16 个本地 LLM 工具，经 `enabled_tools` 过滤）
   - 注册 AstrBot **命令**（`/codegraph`(+别名`/cg`)、`/agentsmd`、`/project`、`/plan`、`/build`）
   - 注册多个 `@filter.on_llm_request()` 钩子：AGENTS.md 注入、codegraph 指引、todo/file_remove/code_check/code_format 指引、L1 鉴权、plan 模式过滤
   - 读取 `_conf_schema.json` 配置（`_flatten_config` 拍平嵌套分组）
   - **L1 鉴权**：整个 spcode 工具箱为管理员工具集，非管理员不可见任何工具（`_auth_guard` 钩子）

2. **工具层** `tools/`（PR-0~PR-7 拆分自 main.py，子包化）
   - `function_tools/` - 16 个 LLM FunctionTool 类，一文件一工具，`ALL_TOOL_CLASSES` 集中注册表；`main.py` 迭代此列表传给 `context.add_llm_tools(...)`
   - `inta_shell/` - 交互式 Shell 复合工具集（component + tools + session_models + paths + runtime 模块级单例）
   - `agentsmd/` / `codegraph/` / `project/` / `security/` / `llm_inject/` - 各业务子系统，对外暴露 facade 类
   - 下划线前缀模块（`_xxx.py`）：内部模块，不直接注册为 AstrBot 工具，供其他模块复用
   - 顶层 `xxx.py`（如 `code_check.py`、`file_remove.py`）：legacy 业务实现入口，被 `function_tools/` 引用
   - **关键设计**：`main.py` 仅保留插件入口职责，业务逻辑全部下沉到 `tools/*` 子包

3. **Web API 层** `tools/webapi/`（v3.6+ 自 main.py 拆出；当前 31 条路由记录 / 29 个唯一路径）
   - 每个端点一个文件，handler 命名固定为 `async def handle(plugin, ...) -> dict`
     （`docs_crud.py` 例外：一个文件承载 `handle_post_docs` / `handle_patch_docs` / `handle_delete_docs` 三个方法，复用同一 `/spcode/docs` 路径）
   - `__init__.py` 拥有 `ROUTES` 路由表 + `HANDLERS` 别名表 + `_wrap()` 适配器 + `register_webapi_routes()`
   - `main.py.initialize()` 调用一次 `register_webapi_routes(self)` 注册全部路由
   - `_wrap()` 通过 `inspect.signature(handler)` 自动注入 `umo` / `worktree` / `scope` / `path` / `if_none_match` / `body` 形参（handler 必须显式声明才注入；GET 从 query/header 取，POST/PATCH/DELETE 从 JSON body 取）
   - **共享基础设施**（`tools/webapi/_helpers.py`）：
     - `ReasonCode` - 集中所有 reason 码字面量（读/写端点统一引用）
     - `_make_envelope(**fields)` - 统一 envelope 工厂（success/reason/data/elapsed_ms）
     - `_git_endpoint_preflight(plugin, *, umo, worktree_param)` - 5 步前置校验（feature flag / project loaded / worktree 安全 / directory 存在 / git repo）
     - `_git_init_preflight(...)` - `git-init` 专用 preflight（唯一豁免 `_git_endpoint_preflight` 的写端点）
     - `_validate_repo_relative_file(path, repo_root)` - 4 步路径防御
     - `_run_git_async(..., input_text=None, env=None)` - 异步子进程（支持 stdin / env 覆盖）
     - `_JSONResponseCompat` - 同时是 Response + dict-like，兼容 framework + 测试
     - `_compute_git_etag` / `_compute_porcelain_diffs` - 只读端点弱 ETag（3 路 porcelain 探测，解决 staleness），供 git-diff / git-status / git-log / git-show / git-branches 共享
   - **新增 webapi 端点流程**：`tools/webapi/<name>.py` 写 `handle()` -> 在 `ROUTES` 添加 `(route, methods, handler, desc)` -> 在 `HANDLERS` 添加别名 -> `tests/test_webapi_end_to_end.py` 更新 routes/handlers 计数断言 -> `tests/test_<name>.py` 单元测试

4. **测试层** `tests/`（75+ 文件）
   - 与 `tools/` 模块对应，命名 `test_<模块名>.py`
   - `conftest.py` 提供共享 fixtures（workspace、临时目录等）
   - `tests/fixtures/` 存放静态样本（含 `cpp_sample/` C++ 样本）
   - `tests/test_webapi_end_to_end.py` 跨端点烟囱测试（路由表 + `_wrap` 注入 + `register_webapi_routes` 注册流程 + handler smoke）

5. **数据层** `data/`
   - `t2i_templates/`：HTML 模板资源
   - `workspaces/`：沙箱化操作的目标根目录（可由配置重定向）
   - `temp/`：临时文件存放

6. **文档层** `docs/`
   - `api/`：Web API 前端消费参考（TypeScript / fetch 示例）
   - `superpowers/`：`specs/`（设计文档）+ `plans/`（实施计划）+ `reviews/` + `handoffs/`
   - 顶层 `webapi_endpoints_report.md` 等端点报告

## 代码风格指南

### 导入 (Imports)

- **遵循 ruff 默认规则**（≈ PEP 8 + pyflakes + isort）
- 顺序：标准库 -> 第三方 -> 本地模块，组间空一行
- 默认绝对导入；仅在循环依赖等必要时使用相对导入
- 禁止 `from x import *`
- 内部模块用下划线前缀（如 `_helpers`），不在 `__init__.py` 中导出
- 跨子包导入使用 `from tools.xxx import yyy`（显式包名）

### 格式化 (Formatting)

- **缩进**：4 空格（PEP 8）
- **行宽**：遵循 ruff 配置（默认 88）
- **字符串**：统一使用双引号 `"`；docstring 同样
- **空行**：模块级函数/类之间 2 行；类内方法之间 1 行
- **文件末尾**：保留单个换行符
- **编码**：UTF-8

### 类型 (Typing)

- **强制使用类型注解**：函数参数、返回值均需标注
- 优先 Python 3.10+ 内置泛型：`list[str]`、`dict[str, int]`、`X | None`
- 复杂结构优先使用 `@dataclass`，避免裸 `dict` 传递
- 工具返回结构必须是 AstrBot 可序列化的（`dict` / `@dataclass` / 基本类型）

### 命名约定 (Naming)

| 类别 | 规范 | 示例 |
|------|------|------|
| 模块 | `snake_case`，内部模块下划线前缀 | `file_remove.py`、`_helpers.py` |
| 类 | `PascalCase` | `FileRemoveTool`、`TodoItem` |
| 函数/方法 | `snake_case` | `delete_file`、`parse_config` |
| 变量 | `snake_case` | `workspace_root`、`max_depth` |
| 常量 | `UPPER_SNAKE_CASE` | `MAX_FILE_SIZE`、`DEFAULT_TIMEOUT` |
| 私有成员 | 下划线前缀 | `_internal_state`、`_validate()` |
| AstrBot 工具名 | 业务前缀 + 功能 | `astrbot_file_remove`、`astrbot_inta_shell_start` |
| MCP 工具名 | 无 `astrbot_` 前缀 | `codegraph_search`、`codegraph_explore` |

### 错误处理 (Error Handling)

- **禁止裸 `except:`**，必须指定异常类型
- 优先捕获具体异常；`except Exception` 仅用于顶层兜底
- 工具层错误：**返回结构化错误给 LLM**，而非抛出异常中断会话
- 路径操作**必须经过 `tools/_path_safety.py` 校验**，防止沙箱逃逸
- 高危操作（文件删除等）实现**双层黑名单 + 批量确认**机制（参考 `file_remove.py`）
- 外部进程调用（cppcheck / cpplint / es.exe / codegraph MCP / git）：**必须**处理退出码与 stderr
- 资源清理：`try/finally` 或 `contextlib` 确保子进程、文件句柄、网络连接关闭
- 自定义异常：为每个工具模块定义专属异常类（如 `FileRemoveError`）

### 日志

- 使用 `logger = logging.getLogger(__name__)` 模块级 logger
- **禁止**使用 `print()` 输出调试信息（测试代码例外）
- 关键操作（文件删除、Shell 执行、MCP 调用、git 写操作）必须记录 INFO 级别日志
- 错误捕获处必须记录 ERROR + 堆栈

### 注释与文档

- 模块/类/公开函数使用 docstring
- 关键逻辑前简短说明意图：`# WHY: ...` 而非 `# WHAT: ...`
- TODO/FIXME 标注：`# TODO(author): description`
- 跨 PR 拆分的模块在 docstring 标注来源 spec（如 `详见 docs/superpowers/specs/...`）

### 工具实现约定

- 工具函数返回值 = AstrBot 可序列化的结构
- 大文件/目录操作必须有**超时**与**进度反馈**
- 涉及外部进程：`subprocess.run(..., check=False, timeout=...)`，**禁止** `shell=True`（除非绝对必要）
- Windows 下子进程调用须抑制 cmd 黑窗：传 `creationflags=subprocess.CREATE_NO_WINDOW`（仅 win32）
- 用户可见的工具名以 `astrbot_` 开头，与 MCP 工具（无前缀）区分
- LLM 工具类继承 `FunctionTool`（dataclass），放在 `tools/function_tools/` 一文件一工具

## 编程代理工作须知

1. **修改前先阅读**对应 `tools/` 模块与 `tests/` 内现有测试，理解架构后再动手
2. **新增 LLM 工具**：在 `tools/function_tools/` 添加文件 -> 在 `__init__.py` 的 `ALL_TOOL_CLASSES` 注册 -> 在 `_config_filter.py` 的 `ALL_TOOL_NAMES` 加名 -> 在 `main.py`（按需）注入配置 -> 在 `tests/` 添加对应测试
3. **新增 webapi 端点**：`tools/webapi/<name>.py` 写 `handle()` -> 在 `ROUTES` 添加 `(route, methods, handler, desc)` -> 在 `HANDLERS` 添加别名 -> `tests/test_webapi_end_to_end.py` 更新路由计数断言 -> `tests/test_<name>.py` 单元测试
4. **修改工具行为**：**必须同步更新** `tests/` 内测试用例
5. **不要修改** `data/workspaces/`、`data/temp/` 下的实际数据 - 这是用户/运行时数据
6. **提交前**必须保证：`ruff check .` 无 error，`pytest tests/` 全部 PASS
7. **新功能原则**：任何供 LLM 调用的能力都应作为 `tools/function_tools/` 下的独立工具实现，并附带测试；避免把逻辑塞进 `main.py`
8. **变更前**可参考 `docs/superpowers/specs/` 下的设计文档与 `docs/superpowers/plans/` 下的实施计划
9. **不要**修改 `_conf_schema.json` 的字段名而不迁移既有用户配置
10. **路径安全**：任何涉及用户输入路径的代码，先调用 `_path_safety` 校验，**不要**自己实现路径判断
11. **Web API 参数安全**：`?worktree=` 等用户控制的路径参数，必须经过 `_validate_worktree_param`（位于 `tools/_helpers.py`）的 6 步防御链：**关键不变量 - git-common-dir 不匹配 = 直接拒绝**
12. **配置拍平**：`_conf_schema.json` 是分组结构，`main.py._flatten_config()` 会把嵌套分组拍平为顶层键（如 `codegraph.codegraph_enabled` -> `codegraph_enabled`）。新增配置项时保持此约定
13. **版本号统一**：当前版本统一为 **v2.20**。发布时同步更新 `metadata.yaml` 的 `version` 字段

## Web API 端点（供 Dashboard 消费）

Web 路由由 `tools/webapi/register_webapi_routes(plugin)` 在 `main.py.initialize()` 中注册，挂载前缀 `/spcode`。当前共 **31 条路由记录**（29 个唯一路径，`/spcode/docs` 一路径复用 POST/PATCH/DELETE 三方法）：

| 端点 | 方法 | 用途 | 关键参数 |
|------|------|------|---------|
| `/spcode/project-status` | GET | 当前加载项目状态 | `umo?` |
| `/spcode/plan-mode` | GET | 当前 plan-mode 状态（严格 per-session，不回退） | `umo?` |
| `/spcode/git-worktrees` | GET | 列出 worktree | `umo?` |
| `/spcode/git-diff` | GET | 工作区 diff（ETag/304） | `umo?`, `worktree?` |
| `/spcode/git-status` | GET | 工作区状态（branch/upstream/staged/unstaged/untracked，ETag/304） | `umo?`, `worktree?` |
| `/spcode/git-log` | GET | git 历史（8 字段标准粒度，ETag/304） | `umo?`, `worktree?`, `n?`, `ref?`, `path?`, `author?`, `since?`, `until?` |
| `/spcode/git-show` | GET | 某 ref 修改的文件列表（name-status+numstat），可选单文件 patch | `umo?`, `worktree?`, `ref`(默认 `HEAD`), `max_files?`(≤2000), `path?` |
| `/spcode/git-file` | GET | 给定 ref 下某文件的完整内容（blob，≤1MB，no-store） | `umo?`, `worktree?`, `ref`(默认 `HEAD`), `path` |
| `/spcode/git-branches` | GET | 列出 branch（local+remote）+current+default（ETag/304） | `umo?`, `worktree?` |
| `/spcode/git-branch-create` | POST | 从 HEAD/指定 start_point 创建 branch | body: `{name, start_point?, force?}` |
| `/spcode/git-branch-delete` | POST | 删除 branch（`-d` 仅 merged，`-D` 含 unmerged；硬禁 current/main） | body: `{name, force?=false}` |
| `/spcode/git-branch-switch` | POST | git switch `<name>`（支持 create/detach/force 跨字段） | body: `{name, create?=false, start_point?, force?=false, detach?=false}` |
| `/spcode/git-revert` | POST | 创建回滚 commit（`--no-edit`，复用 `git_commit` env passthrough） | body: `{ref?="HEAD", no_edit?=true}` |
| `/spcode/git-repo-check` | GET | 判断给定目录是否为 git 仓库（`git rev-parse --is-inside-work-tree`，独立 preflight，不走 umo 解析链） | `path` |
| `/spcode/git-init` | POST | 在目录上 git init（**唯一**豁免 `_git_endpoint_preflight`，走 `_git_init_preflight`；`force=true` 允许非空目录） | body: `{path, initial_branch?="main", bare?=false, force?=false}` |
| `/spcode/git-stage` | POST | git add（指定文件 or all，互斥） | body: `{files:[…]}` \| `{all:true}` |
| `/spcode/git-unstage` | POST | git reset HEAD（指定文件 or all，互斥） | body: `{files:[…]}` \| `{all:true}` |
| `/spcode/git-commit` | POST | git commit（严格最小，仅 message） | body: `{message:"…"}` |
| `/spcode/file-browser` | GET | 读取文件内容 / 列出单层目录 | `umo?`, `path`, `worktree?`, `if_none_match?` |
| `/spcode/file-search` | POST | 项目内按内容搜索（python_ripgrep） | body: `{pattern, case_sensitive?, regex?, max_results?, path_filter?, glob_filter?, umo?, worktree?}` |
| `/spcode/file-name-search` | POST | 项目内按文件名（basename）匹配 | body: `{pattern, case_sensitive?, regex?, max_results?, path_filter?, glob_filter?, umo?, worktree?}` |
| `/spcode/file-restore` | POST | 恢复文件相对 index/HEAD 的改动（scope 自动检测） | body: `{file, umo?, worktree?}` |
| `/spcode/file-discard-hunk` | POST | 按 hunk 丢弃工作区改动（unified diff 文本入参，`git apply --reverse`） | body: `{file, patch_text, umo?, worktree?}` |
| `/spcode/git-worktree-add` | POST | 新建 git worktree（CLI 旗标平铺） | body: `{path, branch?, create?, force?, detach?, base?}` |
| `/spcode/git-worktree-remove` | POST | 删除 git worktree（硬禁 main，locked 拒，`force=true` 跳过 dirty） | body: `{path, force?}` |
| `/spcode/git-worktree-lock` | POST | 锁定 git worktree（可选 `--reason`），main 允许但 git 自身拒绝 | body: `{path, reason?}` |
| `/spcode/git-worktree-unlock` | POST | 解锁 git worktree，main 允许但 git 自身拒绝 | body: `{path}` |
| `/spcode/codegraph-status` | GET | codegraph MCP 运行状态 | - |
| `/spcode/docs` | POST | 创建 / 覆盖 docs 文件（upsert 到工作区） | body: `{umo?, worktree?, path, content}` |
| `/spcode/docs` | PATCH | 重命名 docs 文件（纯文件系统 mv） | body: `{umo?, worktree?, path, new_path}` |
| `/spcode/docs` | DELETE | 从工作区删除 docs 文件（unlink） | body: `{umo?, worktree?, path}` |

> 完整的请求/返回字段与 ReasonCode 前端消费文档见 `docs/api/` 与 `docs/webapi_endpoints_report.md`。

### 统一响应 envelope

```json
{
  "success": true,
  "reason": null,
  "elapsed_ms": 42,
  "data": { /* 端点专有字段 */ }
}
```

失败时 `success=false` + `reason=<ReasonCode 字符串>`。

### ReasonCode 集中表（`tools/webapi/_helpers.py`）

| 类别 | 码 | 含义 |
|------|------|------|
| 通用前置 | `feature_disabled` / `no_project_loaded` / `worktree_invalid` | 5 步 preflight 中止 |
| 通用前置 | `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` | 仓库上下文无效 |
| body 校验 | `invalid_body` / `invalid_files` / `invalid_all` | POST 输入结构错 |
| body 校验 | `invalid_message` / `empty_message`(legacy) / `message_too_long`(legacy) | message 校验失败 |
| 路径安全 | `path_unsafe` | 路径含 `..` / `.git/` / 绝对路径 / symlink |
| 业务结果 | `nothing_to_commit` / `nothing_staged`(legacy) | 无 staged 改动 |
| 业务结果 | `hook_rejected` / `pre_commit_hook_failed`(legacy) | pre-commit / commit-msg 失败 |
| 业务结果 | `identity_not_set` | user.email/name 未设 |
| 参数校验 | `invalid_param` | ref / max_files / content 等通用参数非法 |
| ref/仓库 | `empty_repository` / `ref_not_found` / `commit_too_large` | 空仓库 / ref 不存在 / 输出超上限（git-show/git-file） |
| file-search | `invalid_pattern` / `pattern_too_long` / `path_unsafe_filter` | pattern 非法 / >256 / path_filter 越界 |
| file-search | `search_timeout` / `search_unavailable` | 5s 超时 / python_ripgrep 库缺失或失败 |
| git-file/docs | `file_missing_at_ref` / `file_too_large` / `file_not_found` / `file_exists` | ref 下无此 path / blob 超 1MB / 目标不存在 / rename 目标已存在 |
| patch 输入 | `patch_empty` / `patch_too_large` / `patch_malformed` | patch_text 空 / 超 256 KB / 不是合法 unified diff（v2.16.0） |
| patch 路径 | `patch_unsafe_path` / `multi_file_patch` / `patch_file_mismatch` / `patch_binary` | patch 路径含 `..`/绝对/`.git/` / 多文件 / 文件不匹配 / binary（v2.16.0） |
| patch apply | `patch_check_failed` / `patch_apply_failed` | `git apply --check --reverse` 失败 / `git apply --reverse` 失败（v2.16.0） |
| worktree-mgmt | `invalid_branch` / `path_exists_nonempty` / `cannot_create_existing` / `cannot_checkout_missing` | ADD: branch 格式非法 / 目标已存在非空 / branch 已存在 / branch 不存在（v2.14.0） |

### `?worktree=` 参数防御链（2026-06-18 引入）

- 完全可选，缺省 = primary worktree，行为与 v1 完全一致
- 6 步防御链（`_validate_worktree_param`，位于 `tools/_helpers.py`）：
  1. 长度 & `..` 段检查
  2. `Path.resolve()` symlink 解析
  3. `os.path.isdir()` 存在性
  4. 隐藏目录组件（`.git/...`）拒绝
  5. `realpath != 原路径` -> 拒绝 symlink 越界
  6. **`git-common-dir` 与 primary 匹配**（最后兜底）
- 即便前 5 步全部误配，跨仓库攻击仍会被步骤 6 拦下

### 写端点共享约束

**git-stage / git-unstage / git-commit / file-restore / file-discard-hunk / git-revert / git-branch-* / git-worktree-***：
- 除 `git-init` 外全部共享 5 步前置校验 `_git_endpoint_preflight`
- 单次请求文件数 ≤ 100
- 文件路径必须经过 4 步防御（`_validate_repo_relative_file`）：含 `..` / 绝对路径 / `.git/` 段 / symlink 越界 -> `path_unsafe` 直接拒绝
- `worktree` 参数沿用 6 步防御链，git-common-dir 不匹配 -> `worktree_invalid`
- commit message 上限 8192 字符；空 / 超长 / 非 str -> `invalid_message`
- commit 失败按 stderr 关键字符串映射为 4 类：`hook_rejected` / `identity_not_set` / `nothing_to_commit` / `git_error`
- 名称校验复用 `tools._helpers._is_valid_ref_name`（与 worktree-* 一致）
- body 校验统一 `invalid_body` / `invalid_param` / `invalid_branch`（git-ref-name 非法）

**git-init（v2.17.0, PR-B）**：
- **唯一**完全豁免 `_git_endpoint_preflight` 的写端点，走独立的 `_git_init_preflight`
- `already_a_git_repo` 是 hard-ban，`force` 不绕过
- v2.17.1 `force=true` 跳过 `directory_not_empty` 检查，允许在非空目录 init git 仓库

**git-repo-check（v2.18.0）**：
- 只读端点，直接接受 `path` 参数（与 `file-browser` / `git-init` 一致），不经过 `_git_endpoint_preflight` 的 umo 解析链
- 判断依据：`git rev-parse --is-inside-work-tree`

### 关键不变量

- **file-discard-hunk**：`diff --git` 头中 `b/` 路径必须 = body `file` 字段，否则 `patch_file_mismatch`
- **file-discard-hunk**：`patch` 必须只含 1 个 `diff --git` 段，否则 `multi_file_patch`
- **git-worktree-remove**：硬禁删 main（`force=true` 不绕过）+ locked 拒（`force=true` 不绕过）
- **git-branch-delete**：硬禁删 current branch（`force=true` 仅影响 `-d` vs `-D`，不绕过 `branch_is_current` 检查）
- **git-branch-switch**：跨字段互斥：`create` + `detach` 互斥，`start_point` 仅 `create=true` 时合法，`force` 仅 `create=false` 时附加 `-f` 旗标

### 相关测试

- `tests/test_git_diff_worktree.py` - 10 个 `?worktree=` 攻击向量
- `tests/test_git_worktrees.py` - `git-worktrees` endpoint
- `tests/test_helpers_git.py` - `_resolve_git_common_dir` / `_parse_git_worktree_porcelain`
- `tests/test_git_log.py` - `git-log` endpoint 单元测试（23 cases）
- `tests/test_git_stage.py` - `git-stage` endpoint 单元测试（15 cases）
- `tests/test_git_unstage.py` - `git-unstage` endpoint 单元测试（15 cases）
- `tests/test_git_commit.py` - `git-commit` endpoint 单元测试（15 cases）
- `tests/test_git_init*.py` - git-init 路径测试（空目录、已有 .git、path 不存在等）
- `tests/test_git_branches.py` - git-branches 单元测试（12 cases）
- `tests/test_git_branch_create.py` / `_delete.py` / `_switch.py` - PR-D/E/F
- `tests/test_git_revert.py` - git-revert 单元测试（18 cases）
- `tests/test_git_branches_lifecycle.py` - E2E lifecycle（init->commit->branch->switch->revert->delete）
- `tests/test_git_repo_check.py` - git-repo-check 单元测试
- `tests/test_file_discard_hunk.py` - file-discard-hunk 单元测试（~30 cases）
- `tests/test_webapi_end_to_end.py` - 路由表 + `_wrap` + `register_webapi_routes` smoke
- `tests/test_reason_code.py` - ReasonCode literal 定义校验

## 设计依据文档索引

- `docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md` - worktree 防御链
- `docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md` - git workflow 4 端点
- `docs/superpowers/specs/2026-06-23-main-py-refactor-design.md` - main.py PR-0~PR-7 拆分
- `docs/superpowers/specs/2026-06-23-webapi-extraction-design.md` - webapi 提取
- `docs/superpowers/specs/2026-06-26-git-worktree-management-design.md` - v2.14.0 worktree management 4 端点
- `docs/superpowers/specs/2026-07-06-file-discard-hunk-design.md` - v2.16.0 file-discard-hunk
- `docs/superpowers/specs/2026-07-11-document-manager-backend-design.md` - spec B docs/git-file
- `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md` - v2.17.0 git-init/branch/revert
- `docs/superpowers/specs/2026-07-16-git-init-force-design.md` - v2.17.1 git-init force 标志

## pytest 速查

```bash
pytest tests/                                # 全部
pytest tests/test_<module>.py                # 单文件
pytest tests/test_<module>.py::test_<name>   # 单用例（函数）
pytest tests/test_<module>.py::Cls::test_<m> # 单用例（类方法）
pytest tests/ -k "<keyword>"                 # 按关键字
pytest tests/ -v -x                          # 详细 + 遇错即停
pytest tests/ -s                             # 显示 print/logging
pytest tests/ --cov=tools                    # 覆盖率
```

---

> Author: elecvoid243 · 本文档同步至 v2.20 (2026-07-17)
