# spcode 工具箱

AstrBot 插件，为 LLM 提供工具组，用于 C/C++/Python/Verilog（后续支持) 开发。

部分工具实现参考自 [Irmia DevKit](https://github.com/irmia2026/irmia_devkit_open)。

## 工具清单

| 工具 | 类别 | 说明 |
|------|------|------|
| `code_check` | 代码质量 | Python (ruff) / C·C++ (cppcheck + cpplint) 语法+风格合并检查，结构化 issues |
| `codegraph_*` (8 个) | 代码理解 | 通过 codegraph 官方 MCP server 注入：`codegraph_search` / `_callers` / `_callees` / `_impact` / `_node` / `_explore` / `_status` / `_files` |
| `astrbot_file_remove` | 文件操作 | 沙箱化文件/目录删除，双层黑名单 + 批量确认提案机制 |
| `astrbot_file_compare` | 文件操作 | 结构化文件差异（added/removed/unified diff），UTF-8/GBK 自适应 |
| `es_search` | 文件搜索 | Windows Everything (es.exe) 毫秒级搜索；Linux/macOS 三层 fallback |
| `todo_list` | 任务管理 | LLM 自我管理 todo list，4 态 checklist，按用户隔离持久化到 .md，支持批量操作 |
| `astrbot_inta_shell_*` (5 个) | 交互式 Shell | 持久子进程的多轮双向通信：`_start` / `_send` / `_read` / `_stop` / `_list` |

> **L1 鉴权**：spcode 工具箱是**管理员工具集**。非管理员用户看不到上述任何工具；管理员可见全部。codegraph MCP 工具同样受此鉴权约束。

### 本地工具（10 个）

| 工具名 | 说明 |
|--------|------|
| `code_check` | Python/C/C++ 语法+风格合并检查 |
| `es_search` | 文件名极速搜索 |
| `astrbot_file_remove` | 文件/目录删除 |
| `astrbot_file_compare` | 文件差异比较 |
| `todo_list` | LLM 自我管理任务清单 |
| `astrbot_inta_shell_start` | 启动交互式 Shell 会话 |
| `astrbot_inta_shell_send` | 向会话发送输入 |
| `astrbot_inta_shell_read` | 读取会话输出 |
| `astrbot_inta_shell_stop` | 终止会话 |
| `astrbot_inta_shell_list` | 列出所有活跃会话 |

### MCP 工具（codegraph，8 个）

由 codegraph 官方 MCP server 自动注入：`codegraph_search` / `_callers` / `_callees` / `_impact` / `_node` / `_explore` / `_status` / `_files`

## 命令清单

### /codegraph

| 命令 | 描述 |
|------|------|
| `/codegraph init <文件夹路径>` | 在目标项目目录初始化 codegraph（创建 `.codegraph/`） |
| `/codegraph uninit <文件夹路径>` | 删除目标项目目录的 codegraph 索引 |
| `/codegraph set <文件夹路径>` | 修改默认项目根目录；若 MCP 正在运行则自动重启以应用新路径 |

别名：`/cg`（三个子命令均可用）

### /agentsmd

| 命令 | 描述 |
|------|------|
| `/agentsmd init <文件夹路径>` | 扫描目录结构并调用 LLM 自动生成 AGENTS.md |
| `/agentsmd load <文件夹路径>` | 加载 AGENTS.md 到当前会话系统提示词（后续 LLM 请求自动注入） |
| `/agentsmd unload` | 卸载当前会话的 AGENTS.md 注入 |
| `/agentsmd update` | 基于现有 AGENTS.md 内容 + 最新目录结构，调 LLM 重新生成 |

> 非管理员用户调用这些命令会被 L1 鉴权拦截。

## 安装

将插件文件夹放入 AstrBot 的 `data/plugins/` 目录，重启 AstrBot。

## 配置

进入 **AstrBot WebUI → 插件管理 → astrbot_plugin_spcode_toolkit → 配置**。

### 全局配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `enabled_tools` | 多选列表 | `[]` | 启用的工具名。勾选 `inta_shell` 自动启用全部 5 个 shell 工具（快捷组名）。修改后需重启 AstrBot |
| `allowed_ids` | 字符串 | `""` | 额外允许使用本插件的用户 ID（逗号分隔），与 AstrBot 全局管理员并集生效 |

### code_check 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `cppcheck_path` | 字符串 | `""` | cppcheck.exe 路径（C/C++ 正确性检查）。留空时按 `CPPCHECK_PATH` 环境变量 → `shutil.which("cppcheck")` → 常见安装路径的顺序查找 |
| `cppcheck_shortcircuit` | 单选 | `"error"` | cppcheck 短路策略：`error`=有 error 时跳过 cpplint（默认）/ `warning`=有 error 或 warning 时跳过 cpplint / `never`=两个工具都跑，合并输出 |

### file_remove 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `file_remove_blacklist` | 字符串列表 | `[]` | 用户自定义黑名单，每行一个绝对路径前缀，与内置系统目录黑名单叠加生效 |

### es_search 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `es_path` | 字符串 | `""` | Everything CLI (es.exe) 路径，留空时自动从 PATH 查找 |

### codegraph 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `codegraph_enabled` | 布尔 | `true` | codegraph MCP 总开关。必须同时配置 `codegraph_install_dir` 才会启动 MCP server |
| `codegraph_install_dir` | 字符串 | `""` | codegraph 安装目录（含 `node.exe` 的目录）。**必须显式配置才会启动 MCP**，留空则不启动 |
| `codegraph_project` | 字符串 | `""` | codegraph daemon 默认操作的工程根目录，以 `--path` 参数传给 codegraph |

### AGENTS.md 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `agentsmd_enabled` | 布尔 | `true` | AGENTS.md 管理总开关。设为 false 则 `/agentsmd` 命令和 LLM 注入均不生效 |
| `init_template` | 文本 | `""` | AGENTS.md 初始生成模板。用于 `/agentsmd init` 和 `/agentsmd update` 时调 LLM 的 prompt。留空使用默认模板 |

### inta_shell 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `inta_shell_max_sessions` | 整数 | `10` | 最大并发会话数（1-100）。超过此数量的新会话请求会被拒绝 |
| `inta_shell_session_timeout` | 整数 | `1800` | 会话空闲超时（秒，60-86400）。该时长内无 send/read 操作将被强制终止 |
| `inta_shell_block_unsafe` | 布尔 | `true` | 阻止危险命令（`rm -rf`、`shutdown`、`mkfs`、`sudo`、`kill -9`、fork 炸弹等） |
| `inta_shell_default_cwd` | 字符串 | `""` | 默认工作目录（绝对路径）。留空则用 AstrBot workspace |

### enabled_tools 快捷组名

从 v2.5 起，`enabled_tools` 多选列表中除 5 个独立工具外，还支持快捷组名：

- `inta_shell` — 勾选后自动展开为 `astrbot_inta_shell_start`、`_send`、`_read`、`_stop`、`_list` 共 5 个工具

## 前置依赖

| 工具 | 可选依赖 | 缺失时行为 |
|------|----------|-----------|
| `code_check` (Python) | ruff (`pip install ruff`) | 返回安装提示 |
| `code_check` (C/C++) | cppcheck（先跑正确性检查）+ cpplint（后跑风格检查） | 返回安装提示 |
| `codegraph_*` (8 个) | `@colbymchenry/codegraph` (npm) + MCP server 已配置 | 8 个工具全部不在 LLM 工具列表；插件其它工具照常 |
| `es_search` (Windows) | Everything + es.exe | 返回安装提示 |
| `es_search` (Linux/macOS) | locate / fd / Python 兜底 | 自动降级到 Python `os.walk` |

## 工具详解

### code_check — 语法+风格合并检查

对单个 Python 或 C/C++ 源文件执行一次性检查，同时覆盖**语法错误**和**风格问题**：

- **Python (`.py`)**：使用 ruff（PEP 8 + 常用 lint 规则）
- **C/C++ (`.c/.cpp/.cc/.cxx/.h/.hpp/.hxx`)**：先跑 cppcheck（正确性检查），根据 `cppcheck_shortcircuit` 策略决定是否继续跑 cpplint（Google C++ Style Guide）
- 返回结构化 issues 列表，前 5 条带上下文源码（`→` 标记违规行）
- 其他扩展名（`.js/.ts/.go/.nim` 等）**不支持**

### es_search — 文件名极速搜索

不搜索文件内容，仅搜索文件名：

- **Windows**：通过 Everything CLI (`es.exe`) 毫秒级搜索，支持 wildcard、regex、扩展名/路径过滤、大小写/全词、排序
- **Linux/macOS**：`locate` → `fd` → Python `os.walk` 三层自动降级

### astrbot_file_remove — 文件删除

删除文件或目录时有两层黑名单保护：

| 层级 | 来源 | 说明 |
|------|------|------|
| 内置系统目录 | 代码常量 | 拦截 `C:/Windows`、`/etc`、`/System` 等 19 项，不可绕过 |
| 用户自定义 | 插件配置 `file_remove_blacklist` | 拦截用户指定的关键路径 |

**匹配规则**：目标路径 `resolve()` → 归一化（`\` 转 `/` → `lower()`）→ 与黑名单前缀"完全相等"或"以 `前缀/` 开头"即拦截。

**提案协议**：
- 目录删除需 `confirm=true`
- 目录内文件数超过 `max_items`（默认 50）时，不直接删除，返回 proposal 要求二次确认
- 黑名单拦截时返回 `evidence.blocked_by`（用户黑名单带 `user:` 前缀），LLM 看到后不应重试，应向用户解释路径被保护

**配置示例**：

```json
{
  "file_remove_blacklist": [
    "C:/important",
    "/home/me/secret",
    "D:/projects/secret"
  ]
}
```

### astrbot_file_compare — 文件差异比较

比较两个文本文件，返回结构化 diff（added/removed 行数 + unified diff，最多 100 行）。超过 50MB 的文件会被拒绝。编码自动检测：UTF-8 优先，GBK 兜底。

### todo_list — LLM 自我管理任务清单

跨会话持久化的任务清单系统：

- 6 种操作：`create` / `query` / `add` / `update` / `delete` / `clear`
- 4 种状态：`pending [ ]` / `in_progress [~]` / `done [x]` / `cancelled [-]`
- 按用户 sender_id 隔离，同一用户跨群/跨私聊/跨平台共享（"会话接力"）
- 支持批量操作（`item` 和 `item_id` 可传列表）
- `in_progress` + `notes` → 在 query 结果中标记为 `attention`
- `clear_notes=true` 才能清空 notes（空字符串是保留旧值）

```python
# 创建任务清单
todo_list(action="create", title="重构模块",
          items=[{"title": "分析代码"}, {"title": "切分函数"}, {"title": "补测试"}])

# 推进中更新状态
todo_list(action="update", item_id=1, status="done")
todo_list(action="update", item_id=2, status="in_progress",
          notes="等待用户确认函数切分边界")

# 批量操作
todo_list(action="add", item=[
  {"title": "写测试", "status": "in_progress"},
  {"title": "补文档", "status": "pending"},
])
todo_list(action="update", item_id=[1, 3], status="done")

# 查询当前清单
todo_list(action="query")

# 清空 notes（重要：notes="" 是保留旧值）
todo_list(action="update", item_id=2, clear_notes=true)
```

**注意**：
- `action="create"` 会**直接覆盖**已有清单，先 `query` 确认
- 批量操作任意失败 → 全量回滚
- 总计超过 100 项 → 拒绝添加

### astrbot_inta_shell_* — 交互式 Shell

支持多轮双向通信的持久子进程管理：

- **`_start`**：启动交互式进程（如 `npm init`、`python REPL`、`git add -p`），返回 `session_id`
- **`_send`**：向会话发送输入（自动补换行），支持 `send_eof` 关闭 stdin
- **`_read`**：阻塞读取输出，支持 timeout 和 max_chars 控制
- **`_stop`**：终止会话（默认 Ctrl+C 优雅退出，超时后强制 kill；`force=true` 立即 kill）
- **`_list`**：列出所有活跃会话

安全特性：
- 启动时检查高危命令（`rm -rf`、`shutdown`、`mkfs`、`sudo`、`kill -9`、fork 炸弹等），可通过 `inta_shell_block_unsafe=false` 关闭
- 后台清理协程周期性回收已退出/空闲超时会话
- Windows 兼容（UTF-8 代码页，GBK 降级解码）

> 不支持完整 TTY 程序（如 vim、nano、less）。对于一次性非交互命令，优先使用 AstrBot 内置的 `astrbot_execute_shell` 工具。

## codegraph 集成

[codegraph](https://github.com/colbymchenry/codegraph) 是基于 SQLite 知识图谱的代码智能工具。spcode 插件自动启动 codegraph 官方 MCP server，把 8 个 `codegraph_*` 工具注入到 LLM。

### 安装

```bash
# 1. 全局安装 codegraph CLI
npm install -g @colbymchenry/codegraph

# 2. 初始化你想让 LLM 理解的项目
cd /path/to/your/project
codegraph init
codegraph index       # 可选——首次 explore 调用会懒加载
```

### 配置 MCP

进入 **WebUI → 插件管理 → astrbot_plugin_spcode_toolkit → 配置**，填写：

| 字段 | 说明 |
|------|------|
| `codegraph_enabled` | 设为 `true`（默认） |
| `codegraph_install_dir` | codegraph 包根目录（含 `node.exe` 的目录） |

**Windows**：`F:\tools\codegraph-win32-x64` — 需要里面同时含 `node.exe` 和 `lib\dist\bin\codegraph.js`（或根目录的 `codegraph.js`）。

**Linux/macOS**：指向含 `node` 和 `codegraph.js` 的目录。

填好后**重启 AstrBot**，spcode 插件会启动 MCP server 并把 8 个 `codegraph_*` 工具注入到 LLM。

> **如果未配置 `codegraph_install_dir`**：MCP server 不会启动（不会自动扫描系统），LLM 看不到 8 个 `codegraph_*` 工具。`/codegraph init|uninit` 命令仍可通过 auto-detect 工作（前提是 `codegraph` 在系统 PATH 中）。

### 管理员命令

在 AstrBot 对话里使用：

```
/codegraph init D:/projects/my-app         # 初始化 codegraph 项目
/codegraph uninit D:/projects/my-app       # 反初始化（删除 .codegraph/ 目录）
/codegraph set D:/projects/my-app          # 修改默认项目根目录
```

`/codegraph set` 的作用：把 `codegraph_project` 改为新目录。如果当前 MCP server 在跑，spcode 会自动重启它以应用新 `--path` 参数；之后所有 LLM 调用的 `codegraph_*` 工具默认在新目录下操作。

## AGENTS.md 管理

AGENTS.md 是 OpenCode 提出的项目级 LLM 指令文件，功能类似 Cursor 的 Rules 或 Claude Code 的 CLAUDE.md。

### 工作原理

1. `/agentsmd init <dir>` — 扫描目录结构 + 关键文件，调 LLM 生成 AGENTS.md
2. `/agentsmd load <dir>` — 读取文件内容到内存，标记当前会话
3. 后续 LLM 请求中，`@filter.on_llm_request()` 钩子自动将内容追加到 system_prompt
4. 文件变更自动检测（mtime），缓存自动刷新；`/agentsmd update` 则调 LLM 基于最新目录结构**重新生成**内容
5. 可使用 `agentsmd_enabled=false` 完全关闭此功能

### init_template 自定义

在配置中填写 `init_template` 可自定义生成 AGENTS.md 时使用的 prompt 模板。留空则使用内置默认模板。

## 架构

```
astrbot_plugin_spcode_toolkit/
├── main.py                    # 插件入口（10 工具 + L1 鉴权 + codegraph/agentsmd 命令）
├── _conf_schema.json          # WebUI 配置 schema
├── metadata.yaml              # 插件元信息
├── requirements.txt           # 依赖说明
├── README.md                  # 本文件
├── tools/
│   ├── __init__.py
│   ├── _helpers.py            # run_cmd / unwrap / proposal_reply / run_sync
│   ├── _config_filter.py      # enabled_tools 过滤逻辑 + 快捷组名展开
│   ├── _path_safety.py        # 统一路径安全校验（系统黑名单 + 用户黑名单）
│   ├── _codegraph_mcp.py      # codegraph 启动器检测 + CLI 构造 + 项目路径解析
│   ├── agentsmd.py            # AGENTS.md 生成/扫描/注入逻辑
│   ├── code_check.py          # Python (ruff) / C·C++ (cppcheck+cpplint) 合并检查
│   ├── es_search.py           # 文件名搜索（Everything / locate / fd / Python 兜底）
│   ├── file_remove.py         # 沙箱化删除（双层黑名单 + 提案协议）
│   ├── file_compare.py        # 结构化文件差异比较
│   ├── todo_list.py           # LLM 自我管理任务清单
│   └── inta_shell/            # 交互式 Shell 模块
│       ├── __init__.py
│       ├── component.py       # 持久子进程管理组件
│       ├── tools.py           # 5 个 Shell 工具的纯函数实现
│       ├── session_models.py  # 会话数据模型
│       └── paths.py           # workspace 路径解析
├── tests/                     # 单元测试
│   ├── test_agentsmd.py
│   ├── test_codegraph_cmd.py
│   ├── test_codegraph_lifecycle.py
│   ├── test_codegraph_mcp.py
│   ├── test_file_remove.py
│   ├── test_inta_shell.py
│   ├── test_path_safety.py
│   ├── test_todo_list.py
│   └── fixtures/
```
