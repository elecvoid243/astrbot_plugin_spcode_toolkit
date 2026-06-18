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
    └── inta_shell/               # 交互式 Shell 复合工具集
        ├── __init__.py
        ├── component.py          # 组件主逻辑
        ├── paths.py              # 路径与会话工作目录
        ├── session_models.py     # 数据模型(Session 等)
        └── tools.py              # 5 个工具入口(start/send/read/stop/list)
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

3. **测试层** `tests/`
   - 与 `tools/` 模块一一对应,命名 `test_<模块名>.py`
   - `conftest.py` 提供共享 fixtures(workspace、临时目录等)
   - `tests/fixtures/` 存放静态样本

4. **数据层** `data/`
   - `t2i_templates/`:HTML 模板资源
   - `workspaces/`:沙箱化操作的目标根目录(可由配置重定向)
   - `temp/`:临时文件存放

5. **文档层** `docs/`
   - 设计评审、变更记录(如 `file_remove_review_2026-06-07.md`)
   - 子模块 `superpowers/`

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

`main.py` 暴露的 Web 路由(注册在 `_register_routes` 中,挂载前缀 `/spcode`):

| 端点 | 方法 | 用途 | 关键参数 |
|------|------|------|---------|
| `/spcode/project-status` | GET | 当前加载项目状态 | `umo?` |
| `/spcode/git-diff` | GET | 工作区 diff | `umo`, `worktree?` |
| `/spcode/git-worktrees` | GET | 列出 worktree | `umo` |

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

设计依据见 `docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md`。

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