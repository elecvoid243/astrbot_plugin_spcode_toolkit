# AGENTS.md — spcode 工具箱

本文件供在本仓库工作的编程代理(coding agent / LLM agent)使用,描述项目结构、构建/测试命令与代码规范。

## 项目信息

- **项目名称**: spcode 工具箱 (astrbot_plugin_spcode_toolkit)
- **项目绝对路径**: `F:\github\astrbot_plugin_spcode_toolkit`
- **项目类型**: AstrBot 插件(Python)
- **Python 版本**: 3.10+(建议;用到 `list[str]` 等内置泛型)
- **依赖管理**: `requirements.txt`(含 lint 工具与运行时依赖,如 `send2trash`;AstrBot 宿主环境装运行时依赖)
- **测试框架**: pytest
- **目标平台**: 以 Windows 为首选(es.exe / PowerShell 路径);Linux/macOS 有 fallback

## 构建 / Lint / 测试命令

> 本项目为 Python AstrBot 插件,**不使用 msbuild**,无需独立构建步骤。打包/加载由 AstrBot 宿主在启动时读取 `main.py` 完成。

### Lint

```bash
# Python 代码 lint(项目根目录)
ruff check .

# Python 代码自动修复(可选,提交前请审阅 diff)
ruff check . --fix

# C/C++ lint(单文件)
cpplint path/to/file.cpp
cpplint path/to/file.h
```

### 测试(pytest)

```bash
# 运行全部测试
pytest tests/

# 全部测试 + 详细输出
pytest tests/ -v

# 单个测试文件
pytest tests/test_agentsmd.py
pytest tests/test_codegraph_cmd.py
pytest tests/test_codegraph_lifecycle.py
pytest tests/test_codegraph_mcp.py
pytest tests/test_config_filter.py
pytest tests/test_file_remove.py
pytest tests/test_inta_shell.py
pytest tests/test_path_safety.py
pytest tests/test_project_cmd.py
pytest tests/test_smoke_todo_refactor.py
pytest tests/test_todo_list.py
pytest tests/test_git_worktree_add.py
pytest tests/test_git_worktree_remove.py
pytest tests/test_git_worktree_lock.py
pytest tests/test_git_worktree_unlock.py
pytest tests/test_git_worktree_e2e.py

# 单个测试用例(按函数名)
pytest tests/test_file_remove.py::test_specific_function_name

# 单个测试用例(按类+方法)
pytest tests/test_inta_shell.py::TestClassName::test_method_name

# 按关键字筛选
pytest tests/ -k "todo"
pytest tests/ -k "codegraph"
pytest tests/ -k "path_safety"

# 首个失败即停止,便于定位
pytest tests/ -x

# 显示 print / logging 输出
pytest tests/ -s

# 覆盖率(可选)
pytest tests/ --cov=tools --cov-report=term-missing
```

### 加载到 AstrBot

将项目根目录放入 AstrBot 插件目录,启动 AstrBot 即可加载 `main.py`。本地开发可用符号链接而非复制。

## 目录结构与架构

```
astrbot_plugin_spcode_toolkit/
├── main.py                       # 插件入口:注册工具/命令/事件
├── metadata.yaml                 # 插件元信息(AstrBot 加载识别)
├── _conf_schema.json             # 配置 schema
├── requirements.txt              # lint + 运行时依赖:ruff, cpplint, send2trash
├── README.md                     # 项目说明
│
├── data/                         # 数据/资源(随插件分发)
│   ├── cmd_config.json
│   ├── t2i_templates/            # HTML 模板
│   │   ├── base.html
│   │   ├── astrbot_powershell.html
│   │   └── astrbot_vitepress.html
│   ├── temp/                     # 临时文件
│   └── workspaces/               # 沙箱化工作区
│
├── docs/                         # 项目文档 / 评审记录
│   ├── file_remove_review_2026-06-07.md
│   └── superpowers/
│
├── tests/                        # pytest 测试(与 tools/ 一一对应)
│   ├── __init__.py
│   ├── conftest.py               # 共享 fixtures
│   ├── test_agentsmd.py
│   ├── test_codegraph_cmd.py
│   ├── test_codegraph_lifecycle.py
│   ├── test_codegraph_mcp.py
│   ├── test_config_filter.py
│   ├── test_file_remove.py
│   ├── test_inta_shell.py
│   ├── test_path_safety.py
│   ├── test_project_cmd.py
│   ├── test_smoke_todo_refactor.py
│   ├── test_todo_list.py
│   ├── test_git_worktree_add.py
│   ├── test_git_worktree_remove.py
│   ├── test_git_worktree_lock.py
│   ├── test_git_worktree_unlock.py
│   ├── test_git_worktree_e2e.py
│   ├── test_helpers_worktree.py
│   └── fixtures/                 # 测试夹具(样本文件/目录)
│
└── tools/                        # 工具实现层
    ├── __init__.py
    ├── _codegraph_mcp.py         # [内部] codegraph MCP 封装
    ├── _config_filter.py         # [内部] 配置过滤
    ├── _helpers.py               # [内部] 通用辅助
    ├── _path_safety.py           # [内部] 路径安全(沙箱)
    ├── agentsmd.py               # [工具] AGENTS.md 生成/解析
    ├── code_check.py             # [工具] ruff/cppcheck/cpplint 合并检查
    ├── es_search.py              # [工具] Everything / fallback 搜索
    ├── file_compare.py           # [工具] 结构化文件差异
    ├── file_remove.py            # [工具] 沙箱化文件/目录删除
    ├── todo_list.py              # [工具-stub] v2.6+ 已拆分,保留兼容入口
    ├── inta_shell/               # 交互式 Shell 复合工具集
    │   ├── __init__.py
    │   ├── component.py          # 组件主逻辑
    │   ├── paths.py              # 路径与会话工作目录
    │   ├── session_models.py     # 数据模型(Session 等)
    │   └── tools.py              # 5 个工具入口(start/send/read/stop/list)
    └── webapi/                   # v3.6+ Dashboard HTTP 端点(自 main.py 拆出;当前 24 条路由)
        ├── __init__.py           #   ROUTES 路由表 + HANDLERS 别名 + _wrap() 适配层 + register_webapi_routes()
        ├── _helpers.py           #   [内部] _run_git_async / _JSONResponseCompat /
        │                         #            ReasonCode / _git_endpoint_preflight /
        │                         #            _validate_repo_relative_file / _make_envelope /
        │                         #            _compute_git_etag / _compute_porcelain_diffs
        ├── project_status.py     #   GET    /spcode/project-status
        ├── plan_mode.py          #   GET    /spcode/plan-mode
        ├── git_worktrees.py      #   GET    /spcode/git-worktrees
        ├── git_diff.py           #   GET    /spcode/git-diff
        ├── git_status.py         #   GET    /spcode/git-status          (v2.13, 2026-06-24)
        ├── git_log.py            #   GET    /spcode/git-log             (v3.7)
        ├── git_show.py           #   GET    /spcode/git-show            (v3.8, 2026-06-25)
        ├── git_file.py           #   GET    /spcode/git-file            (spec B, 2026-07-11)
        ├── codegraph_status.py   #   GET    /spcode/codegraph-status    (v2.14.x, 2026-06-28)
        ├── file_browser.py       #   GET    /spcode/file-browser
        ├── git_stage.py          #   POST   /spcode/git-stage           (v3.7)
        ├── git_unstage.py        #   POST   /spcode/git-unstage         (v3.7)
        ├── git_commit.py         #   POST   /spcode/git-commit          (v3.7)
        ├── file_restore.py       #   POST   /spcode/file-restore
        ├── file_discard_hunk.py  #   POST   /spcode/file-discard-hunk   (v2.16.0, 2026-07-06)
        ├── file_search.py        #   POST   /spcode/file-search         (v2.15.0, 2026-07-02)
        ├── file_name_search.py   #   POST   /spcode/file-name-search    (v2.15.0, 2026-07-02)
        ├── git_worktree_add.py   #   POST   /spcode/git-worktree-add    (v2.14.0 PR-B)
        ├── git_worktree_remove.py #  POST   /spcode/git-worktree-remove  (v2.14.0 PR-C)
        ├── git_worktree_lock.py   #  POST   /spcode/git-worktree-lock    (v2.14.0 PR-D)
        ├── git_worktree_unlock.py #  POST   /spcode/git-worktree-unlock  (v2.14.0 PR-D)
        └── docs_crud.py          #   POST/PATCH/DELETE /spcode/docs      (spec B, 2026-07-11)
```

### 架构分层

1. **入口层** `main.py`
   - 在 AstrBot 启动时被加载
   - 注册 AstrBot **工具**(供 LLM 调用)
   - 注册 AstrBot **命令**(`/codegraph`、`/cg`、`/spcode` 等)
   - 读取 `_conf_schema.json` 配置
   - 加载 `tools/` 下各模块
   - **L1 鉴权**:整个 spcode 工具箱为管理员工具集,非管理员不可见任何工具

2. **工具层** `tools/`
   - **下划线前缀** 模块(`_xxx.py`):内部模块,不直接注册为 AstrBot 工具,供其他工具复用
   - **无下划线前缀** 模块(`xxx.py`):直接注册为 AstrBot 工具
   - **复合工具子目录**(如 `inta_shell/`):内部按职责再拆分为多个文件

3. **Web API 层** `tools/webapi/`(v3.6+ 自 main.py 拆出;当前 24 条路由记录)
   - 每个端点一个文件,handler 命名固定为 `async def handle(plugin, ...) -> dict`
     (`docs_crud.py` 例外:一个文件承载 `handle_post_docs` / `handle_patch_docs` /
     `handle_delete_docs` 三个方法,复用同一 `/spcode/docs` 路径)
   - `__init__.py` 拥有 `ROUTES` 路由表 + `HANDLERS` 别名表 + `_wrap()` 适配器 +
     `register_webapi_routes()`
   - `main.py.initialize()` 调用一次 `register_webapi_routes(self)` 注册全部 24 条路由
   - `_wrap()` 通过 `inspect.signature(handler)` 自动注入 `umo` / `worktree` /
     `scope` / `path` / `if_none_match` / `body` 形参(handler 必须显式声明才注入;
     GET 从 query/header 取,POST/PATCH/DELETE 从 JSON body 取)
   - **共享基础设施**(`tools/webapi/_helpers.py`):
     - `ReasonCode` — 集中所有 reason 码字面量(读/写端点统一引用)
     - `_make_envelope(**fields)` — 统一 envelope 工厂(success/reason/data/elapsed_ms)
     - `_git_endpoint_preflight(plugin, *, umo, worktree_param)` — 5 步前置校验
       (feature flag / project loaded / worktree 安全 / directory 存在 / git repo)
     - `_validate_repo_relative_file(path, repo_root)` — 4 步路径防御
     - `_run_git_async(..., input_text=None, env=None)` — 异步子进程(支持 stdin / env 覆盖)
     - `_JSONResponseCompat` — 同时是 Response + dict-like,兼容 framework + 测试
     - `_compute_git_etag` / `_compute_porcelain_diffs` — 只读端点弱 ETag(3 路 porcelain
       探测,解决 staleness),供 git-diff / git-status / git-log / git-show 共享
   - **新增 webapi 端点**流程:`tools/webapi/<name>.py` 写 `handle()` →
     在 `ROUTES` 添加 `(route, methods, handler, desc)` → 在 `HANDLERS` 添加别名 →
     `tests/test_webapi_end_to_end.py` 更新 routes/handlers 计数断言 →
     `tests/test_<name>.py` 单元测试

4. **测试层** `tests/`
   - 与 `tools/` 模块一一对应,命名 `test_<模块名>.py`
   - `conftest.py` 提供共享 fixtures(workspace、临时目录等)
   - `tests/fixtures/` 存放静态样本
   - `tests/test_webapi_end_to_end.py` 跨端点烟囱测试(24 条路由表 + `_wrap` 注入 +
     `register_webapi_routes` 注册流程 + handler smoke)

5. **数据层** `data/`
   - `t2i_templates/`:HTML 模板资源
   - `workspaces/`:沙箱化操作的目标根目录(可由配置重定向)
   - `temp/`:临时文件存放

6. **文档层** `docs/`
   - 设计评审、变更记录(如 `file_remove_review_2026-06-07.md`)
   - 子模块 `superpowers/`(含 `specs/` 设计与 `plans/` 实施计划)

## 代码风格指南

### 导入 (Imports)

- **遵循 ruff 默认规则**(≈ PEP 8 + pyflakes + isort)
- 顺序:标准库 → 第三方 → 本地模块,组间空一行
- 默认绝对导入;仅在循环依赖等必要时使用相对导入
- 禁止 `from x import *`
- 内部模块用下划线前缀(如 `_helpers`),不在 `__init__.py` 中导出
- 跨子包导入使用 `from tools.xxx import yyy`(显式包名)

### 格式化 (Formatting)

- **缩进**:4 空格(PEP 8)
- **行宽**:遵循 ruff 配置(默认 88)
- **字符串**:统一使用双引号 `"`;docstring 同样
- **空行**:模块级函数/类之间 2 行;类内方法之间 1 行
- **文件末尾**:保留单个换行符
- **编码**:UTF-8

### 类型 (Typing)

- **强制使用类型注解**:函数参数、返回值均需标注
- 优先 Python 3.10+ 内置泛型:`list[str]`、`dict[str, int]`、`X | None`
- 复杂结构优先使用 `@dataclass`,避免裸 `dict` 传递
- 工具返回结构必须是 AstrBot 可序列化的(`dict` / `@dataclass` / 基本类型)

### 命名约定 (Naming)

| 类别 | 规范 | 示例 |
|------|------|------|
| 模块 | `snake_case`,内部模块下划线前缀 | `file_remove.py`、`_helpers.py` |
| 类 | `PascalCase` | `FileRemoveTool`、`TodoItem` |
| 函数/方法 | `snake_case` | `delete_file`、`parse_config` |
| 变量 | `snake_case` | `workspace_root`、`max_depth` |
| 常量 | `UPPER_SNAKE_CASE` | `MAX_FILE_SIZE`、`DEFAULT_TIMEOUT` |
| 私有成员 | 下划线前缀 | `_internal_state`、`_validate()` |
| AstrBot 工具名 | 业务前缀 + 功能 | `astrbot_file_remove`、`astrbot_inta_shell_start` |

### 错误处理 (Error Handling)

- **禁止裸 `except:`**,必须指定异常类型
- 优先捕获具体异常;`except Exception` 仅用于顶层兜底
- 工具层错误:**返回结构化错误给 LLM**,而非抛出异常中断会话
- 路径操作**必须经过 `tools/_path_safety.py` 校验**,防止沙箱逃逸
- 高危操作(文件删除等)实现**双层黑名单 + 批量确认**机制(参考 `file_remove.py`)
- 外部进程调用(cppcheck / cpplint / es.exe / codegraph MCP):**必须**处理退出码与 stderr
- 资源清理:`try/finally` 或 `contextlib` 确保子进程、文件句柄、网络连接关闭
- 自定义异常:为每个工具模块定义专属异常类(如 `FileRemoveError`)

### 日志

- 使用 `logger = logging.getLogger(__name__)` 模块级 logger
- **禁止**使用 `print()` 输出调试信息(测试代码例外)
- 关键操作(文件删除、Shell 执行、MCP 调用)必须记录 INFO 级别日志
- 错误捕获处必须记录 ERROR + 堆栈

### 注释与文档

- 模块/类/公开函数使用 docstring
- 关键逻辑前简短说明意图:`# WHY: ...` 而非 `# WHAT: ...`
- TODO/FIXME 标注:`# TODO(author): description`

### 工具实现约定

- 工具函数返回值 = AstrBot 可序列化的结构
- 大文件/目录操作必须有**超时**与**进度反馈**
- 涉及外部进程:`subprocess.run(..., check=False, timeout=...)`,**禁止** `shell=True`(除非绝对必要)
- 用户可见的工具名以 `astrbot_` 开头,与 MCP 工具(无前缀)区分

## 编程代理工作须知

1. **修改前先阅读**对应 `tools/` 模块与 `tests/` 内现有测试,理解架构后再动手
2. **新增工具**:在 `tools/` 添加文件 → 在 `main.py` 注册 → 在 `tests/` 添加对应测试,三步缺一不可
3. **修改工具行为**:**必须同步更新** `tests/` 内测试用例
4. **不要修改** `data/workspaces/`、`data/temp/` 下的实际数据 — 这是用户/运行时数据
5. **提交前**必须保证:`ruff check .` 无 error,`pytest tests/` 全部 PASS
6. **新功能原则**:任何供 LLM 调用的能力都应作为 `tools/` 下的独立工具实现,并附带测试;避免把逻辑塞进 `main.py`
7. **变更前**可参考 `docs/` 下的评审记录(如 `file_remove_review_2026-06-07.md`)
8. **不要**修改 `_conf_schema.json` 的字段名而不迁移既有用户配置
9. **路径安全**:任何涉及用户输入路径的代码,先调用 `_path_safety` 校验,**不要**自己实现路径判断
10. **Web API 参数安全**:`?worktree=` 等用户控制的路径参数,必须经过 `_validate_worktree_param`
    (位于 `tools/_helpers.py`)的 6 步防御链:**关键不变量 — git-common-dir 不匹配 = 直接拒绝**

## Web API 端点(供 Dashboard 消费)

Web 路由由 `tools/webapi/register_webapi_routes(plugin)` 在 `main.py.initialize()`
中注册,挂载前缀 `/spcode`。当前共 **24 条路由记录**(`/spcode/docs` 一路径复用
POST/PATCH/DELETE 三方法):

| 端点 | 方法 | 用途 | 关键参数 |
|------|------|------|---------|
| `/spcode/project-status` | GET | 当前加载项目状态 | `umo?` |
| `/spcode/plan-mode` | GET | 当前 plan-mode 状态(严格 per-session,不回退) | `umo?` |
| `/spcode/git-worktrees` | GET | 列出 worktree | `umo?` |
| `/spcode/git-diff` | GET | 工作区 diff(ETag/304) | `umo?`, `worktree?` |
| `/spcode/git-status` | GET | 工作区状态(branch/upstream/staged/unstaged/untracked,ETag/304) | `umo?`, `worktree?` |
| `/spcode/git-log` | GET | git 历史(8 字段标准粒度,ETag/304) | `umo?`, `worktree?`, `n?`, `ref?`, `path?`, `author?`, `since?`, `until?` |
| `/spcode/git-show` | GET | 某 ref 修改的文件列表(name-status+numstat),可选单文件 patch | `umo?`, `worktree?`, `ref`(默认 `HEAD`), `max_files?`(≤2000), `path?` |
| `/spcode/git-file` | GET | 给定 ref 下某文件的完整内容(blob,≤1MB,no-store) | `umo?`, `worktree?`, `ref`(默认 `HEAD`), `path` |
| `/spcode/codegraph-status` | GET | codegraph MCP 运行状态 | — |
| `/spcode/file-browser` | GET | 读取文件内容 / 列出单层目录 | `umo?`, `path`, `worktree?`, `if_none_match?` |
| `/spcode/git-stage` | POST | git add(指定文件 or all,互斥) | body: `{files:[…]}` \| `{all:true}` |
| `/spcode/git-unstage` | POST | git reset HEAD(指定文件 or all,互斥) | body: `{files:[…]}` \| `{all:true}` |
| `/spcode/git-commit` | POST | git commit(严格最小,仅 message) | body: `{message:"…"}` |
| `/spcode/file-restore` | POST | 恢复文件相对 index/HEAD 的改动(scope 自动检测) | body: `{file, umo?, worktree?}` |
| `/spcode/file-discard-hunk` | POST | 按 hunk 丢弃工作区改动(unified diff 文本入参,`git apply --reverse`) | body: `{file, patch_text, umo?, worktree?}` |
| `/spcode/file-search` | POST | 项目内按内容搜索(python_ripgrep) | body: `{pattern, case_sensitive?, regex?, max_results?, path_filter?, glob_filter?, umo?, worktree?}` |
| `/spcode/file-name-search` | POST | 项目内按文件名(basename)匹配 | body: `{pattern, case_sensitive?, regex?, max_results?, path_filter?, glob_filter?, umo?, worktree?}` |
| `/spcode/git-worktree-add` | POST | 新建 git worktree(CLI 旗标平铺) | body: `{path, branch?, create?, force?, detach?, base?}` |
| `/spcode/git-worktree-remove` | POST | 删除 git worktree(硬禁 main,locked 拒,`force=true` 跳过 dirty) | body: `{path, force?}` |
| `/spcode/git-worktree-lock` | POST | 锁定 git worktree(可选 `--reason`),main 允许但 git 自身拒绝 | body: `{path, reason?}` |
| `/spcode/git-worktree-unlock` | POST | 解锁 git worktree,main 允许但 git 自身拒绝 | body: `{path}` |
| `/spcode/git-init` | POST | 在空目录上 git init(**唯一**豁免 preflight) | body: `{path, initial_branch?="main", bare?=false}` |
| `/spcode/git-branches` | GET | 列出 branch(local+remote)+current+default(ETag/304) | `umo?`, `worktree?` |
| `/spcode/git-branch-create` | POST | 从 HEAD/指定 start_point 创建 branch | body: `{name, start_point?, force?}` |
| `/spcode/git-branch-delete` | POST | 删除 branch(`-d` 仅 merged,`-D` 含 unmerged;硬禁 current/main) | body: `{name, force?=false}` |
| `/spcode/git-branch-switch` | POST | git switch <name>(支持 create/detach/force 跨字段) | body: `{name, create?=false, start_point?, force?=false, detach?=false}` |
| `/spcode/git-revert` | POST | 创建回滚 commit(`--no-edit`,复用 `git_commit` env passthrough) | body: `{ref?="HEAD", no_edit?=true}` |
| `/spcode/docs` | POST | 创建 / 覆盖 docs 文件(upsert 到工作区) | body: `{umo?, worktree?, path, content}` |
| `/spcode/docs` | PATCH | 重命名 docs 文件(纯文件系统 mv) | body: `{umo?, worktree?, path, new_path}` |
| `/spcode/docs` | DELETE | 从工作区删除 docs 文件(unlink) | body: `{umo?, worktree?, path}` |

> 完整的请求/返回字段与 ReasonCode 前端消费文档见 `docs/webapi_endpoints_report.md`。

**v3.7 (2026-06-24) 新增 4 个端点**:`git-log`(读)、`git-stage`/`git-unstage`/
`git-commit`(写,合称 git workflow)。所有写端点共享 5 步前置校验 +
`_validate_repo_relative_file` 4 步路径防御 + `ReasonCode` 错误分类。

**ReasonCode 集中表**(`tools/webapi/_helpers.py`):

| 类别 | 码 | 含义 |
|------|------|------|
| 通用前置 | `feature_disabled` / `no_project_loaded` / `worktree_invalid` | 5 步 preflight 中止 |
| 通用前置 | `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` | 仓库上下文无效 |
| body 校验 | `invalid_body` / `invalid_files` / `invalid_all` | POST 输入结构错 |
| body 校验 | `invalid_message` / `empty_message` / `message_too_long` | message 校验失败 |
| 路径安全 | `path_unsafe` | 路径含 `..` / `.git/` / 绝对路径 / symlink |
| 业务结果 | `nothing_to_commit` / `nothing_staged` | 无 staged 改动 |
| 业务结果 | `hook_rejected` / `pre_commit_hook_failed` | pre-commit / commit-msg 失败 |
| 业务结果 | `identity_not_set` | user.email/name 未设 |
| 参数校验 | `invalid_param` | ref / max_files / content 等通用参数非法 |
| ref/仓库 | `empty_repository` / `ref_not_found` / `commit_too_large` | 空仓库 / ref 不存在 / 输出超上限(git-show/git-file) |
| file-search | `invalid_pattern` / `pattern_too_long` / `path_unsafe_filter` | pattern 非法 / >256 / path_filter 越界 |
| file-search | `search_timeout` / `search_unavailable` | 5s 超时 / python_ripgrep 库缺失或失败 |
| git-file/docs | `file_missing_at_ref` / `file_too_large` / `file_not_found` / `file_exists` | ref 下无此 path / blob 超 1MB / 目标不存在 / rename 目标已存在 |

**v2.16.0 新增 9 个 (file-discard-hunk 专用)**:

| 类别 | 码 | 含义 |
|------|------|------|
| patch 输入 | `patch_empty` / `patch_too_large` / `patch_malformed` | patch_text 空 / 超 256 KB / 不是合法 unified diff |
| patch 路径 | `patch_unsafe_path` / `multi_file_patch` / `patch_file_mismatch` / `patch_binary` | patch 中路径含 `..` / 绝对 / `.git/` 段 / 多文件 / 文件不匹配 / binary 内容 |
| patch apply | `patch_check_failed` / `patch_apply_failed` | `git apply --check --reverse` 失败 / `git apply --reverse` 失败（极罕见，文件被并发改） |

**`?worktree=` 参数(2026-06-18 引入)**:
- 完全可选,缺省 = primary worktree,行为与 v1 完全一致
- 6 步防御链(`_validate_worktree_param`):
  1. 长度 & `..` 段检查
  2. `Path.resolve()` symlink 解析
  3. `os.path.isdir()` 存在性
  4. 隐藏目录组件(`.git/...`)拒绝
  5. `realpath != 原路径` → 拒绝 symlink 越界
  6. **`git-common-dir` 与 primary 匹配**(最后兜底)
- 即便前 5 步全部误配,跨仓库攻击仍会被步骤 6 拦下

**相关测试**:
- `tests/test_git_diff_worktree.py` — 10 个 `?worktree=` 攻击向量
- `tests/test_git_worktrees.py` — `git-worktrees` endpoint
- `tests/test_helpers_git.py` — `_resolve_git_common_dir` / `_parse_git_worktree_porcelain`
- `tests/test_git_log.py` — `git-log` endpoint 单元测试(23 cases)
- `tests/test_git_stage.py` — `git-stage` endpoint 单元测试(15 cases)
- `tests/test_git_unstage.py` — `git-unstage` endpoint 单元测试(15 cases)
- `tests/test_git_commit.py` — `git-commit` endpoint 单元测试(15 cases)
- `tests/test_webapi_end_to_end.py` — 24 条路由表 + `_wrap` + `register_webapi_routes` smoke

**v3.7+ 写端点 (git-stage / git-unstage / git-commit) 共享约束**:
- 单次请求文件数 ≤ 100
- 文件路径必须经过 4 步防御(`_validate_repo_relative_file`):含 `..` / 绝对路径 /
  `.git/` 段 / symlink 越界 → `path_unsafe` 直接拒绝
- `worktree` 参数沿用 6 步防御链,git-common-dir 不匹配 → `worktree_invalid`
- commit message 上限 8192 字符;空 / 超长 / 非 str → `invalid_message`
- commit 失败按 stderr 关键字符串映射为 4 类:`hook_rejected` / `identity_not_set` /
  `nothing_to_commit` / `git_error`

设计依据见:
- `docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md` — worktree 防御链
- `docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md` — git workflow 4 端点
- `docs/superpowers/plans/2026-06-23-git-stage-untage-commit-log-impl.md` — 6 PR 实施记录
- `docs/superpowers/specs/2026-06-26-git-worktree-management-design.md` —
  v2.14.0 worktree management 4 端点 (ADD / REMOVE / LOCK / UNLOCK)
- `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md` —
  v2.17.0 git-init/branch/revert 6 端点 (PR-B ~ PR-G)
- `docs/superpowers/plans/2026-07-15-git-init-branch-revert.md` —
  v2.17.0 9-Task 实施计划 (PR-B ~ PR-I)
- `docs/webapi-git-init-branch-revert-api.md` —
  v2.17.0 6 端点 dashboard 消费参考 (请求/响应/ReasonCode/best practice)

**v2.17.0 (2026-07-15) git-init/branch/revert 6 端点(PR-B ~ PR-G)**:
- `POST /spcode/git-init` — PR-B(**唯一**完全豁免 `_git_endpoint_preflight` 的写端点,
  需在空目录上 `git init`;与所有其他写端点相反,走 `_git_init_preflight`)
- `GET /spcode/git-branches` — PR-C(支持 ETag/304,3 路 porcelain 弱缓存,_compute_git_etag 共享)
- `POST /spcode/git-branch-create` — PR-D(8 步防御:name 校验 + 创建前分支已存在 +
  start_point ^{commit} 校验 + worktree dirty 检查)
- `POST /spcode/git-branch-delete` — PR-E(**硬禁**删 current branch,`force=true` 仅影响
  `-d` vs `-D`(merged check),**不绕过** `branch_is_current` 检查)
- `POST /spcode/git-branch-switch` — PR-F(跨字段互斥:create+detach 互斥,
  start_point 仅 create=true 时合法,force 仅 create=false 时附加 `-f` 旗标)
- `POST /spcode/git-revert` — PR-G(复用 `git_commit._classify_commit_error` +
  `git_commit._build_git_env`,`no_edit=true` 强制,headless 服务不开编辑器)

**v2.17.0 端点 (git-init / branch-* / git-revert) 共享约束**:
- 名称校验复用 `tools._helpers._is_valid_ref_name`(与 worktree-* 一致)
- body 校验统一 `invalid_body` / `invalid_param` / `invalid_branch`(git-ref-name 非法)
- preflight 5 步 + worktree 6 步防御链(v2.14.0 已建立)
- commit/branch 操作 stderr 通过 `_classify_*_stderr` 集中分类

**v2.17.0 单元测试**:
- `tests/test_git_init_*.py` — git-init 路径测试(空目录、已有 .git、path 不存在等)
- `tests/test_git_branches.py` — git-branches 单元测试(12 cases,PR-C)
- `tests/test_git_branch_create.py` / `_delete.py` / `_switch.py` — PR-D/E/F
- `tests/test_git_revert.py` — git-revert 单元测试(18 cases,PR-G)
- `tests/test_git_branches_lifecycle.py` — E2E lifecycle(init→commit→branch→switch→revert→delete)
- `tests/test_webapi_end_to_end.py::TestV217NewEndpointsSmoke` — 6 个路由 smoke
- `tests/test_reason_code.py::test_v217_git_reason_codes_defined` — 12 个新 reason literal

**v2.14.0 (2026-06-26) worktree management 4 端点(PR-B / C / D)**:
- `POST /spcode/git-worktree-add` — PR-B(`-b/-B/--detach/--force` 旗标平铺,
  `create=true AND force=true` 互斥,`detach` + `create` 互斥)
- `POST /spcode/git-worktree-remove` — PR-C(8 层防御:硬禁 main,locked 拒,
  `force=true` 跳过 dirty)
- `POST /spcode/git-worktree-lock` — PR-D(6 层防御,可选 `--reason`,
  main 允许但 git 自身拒绝)
- `POST /spcode/git-worktree-unlock` — PR-D(5 层防御,二次 unlock → `not_locked`,
  非 idempotent)

**v2.14.0 端点 (git-worktree-*) 共享约束**:
- `_resolve_target_worktree` 4 步路径防御(格式 + list 查找):
  含 `..` 段 / 绝对路径 / `path_unsafe` 直接拒绝
- 业务闸:REMOVE 硬禁 main(force=true 不绕过)+ locked 拒(force=true 不绕过);
  LOCK/UNLOCK 无 handler 层业务闸,git 自身拒绝
- `worktree` 参数沿用 6 步防御链

**v2.14.0 单元测试**:
- `tests/test_git_worktree_add.py` — ADD 单元测试(~26 cases)
- `tests/test_git_worktree_remove.py` — REMOVE 单元测试(~16 cases)
- `tests/test_git_worktree_lock.py` — LOCK 单元测试(14 cases)
- `tests/test_git_worktree_unlock.py` — UNLOCK 单元测试(14 cases)
- `tests/test_helpers_worktree.py` — helpers 单元测试
- `tests/test_git_worktree_e2e.py` — 5 个 E2E 生命周期 smoke 测试
- `tests/test_webapi_end_to_end.py` — 16 路由表 + `_wrap` +
  `register_webapi_routes` smoke (最新路由计数以 `ROUTES` 为准,当前 24)

**v2.16.0 (2026-07-06) file-discard-hunk 端点**:
- `POST /spcode/file-discard-hunk` — 接受 unified diff 文本入参,`git apply --check --reverse` 干跑 + `git apply --reverse` 实际应用
- 单文件单次请求;patch ≤ 256 KB;9 个新 reason code
- worktree 防御链 + 4 步文件防御复用既有实现
- stdin 喂 patch 文本(规避 argv 长度限制 + shell quoting)
- 单元测试 ~30 cases
- **关键不变量**:`diff --git` 头中 `b/` 路径必须 = body `file` 字段,否则 `patch_file_mismatch`
- **关键不变量**:`patch` 必须只含 1 个 `diff --git` 段,否则 `multi_file_patch`

## pytest 速查

```bash
pytest tests/                                # 全部
pytest tests/test_<module>.py                # 单文件
pytest tests/test_<module>.py::test_<name>   # 单用例(函数)
pytest tests/test_<module>.py::Cls::test_<m> # 单用例(类方法)
pytest tests/ -k "<keyword>"                 # 按关键字
pytest tests/ -v -x                          # 详细 + 遇错即停
pytest tests/ -s                             # 显示 print/logging
pytest tests/ --cov=tools                    # 覆盖率
```