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
| `todo_create` / `todo_query` / `todo_add` / `todo_update` / `todo_delete` / `todo_clear` (6 个) | 任务管理 | v2.6+ LLM 自我管理 todo list；v2.12 进一步把 v2.6 的 `todo_modify(mode=...)` 拆为 3 个独立工具 `todo_add` / `todo_update` / `todo_delete`(消除 mode 误用)。旧 `todo_list` / `todo_modify` 已删除 |
| `astrbot_inta_shell_*` (5 个) | 交互式 Shell | 持久子进程的多轮双向通信：`_start` / `_send` / `_read` / `_stop` / `_list` |

> **L1 鉴权**：spcode 工具箱是**管理员工具集**。非管理员用户看不到上述任何工具；管理员可见全部。codegraph MCP 工具同样受此鉴权约束。

### 本地工具（10 个）

| 工具名 | 说明 |
|--------|------|
| `code_check` | Python/C/C++ 语法+风格合并检查 |
| `es_search` | 文件名极速搜索 |
| `astrbot_file_remove` | 文件/目录删除 |
| `astrbot_file_compare` | 文件差异比较 |
| `todo_create` / `todo_query` / `todo_add` / `todo_update` / `todo_delete` / `todo_clear` (6 个) | LLM 自我管理任务清单（v2.6+ 拆分 API；v2.12 进一步拆分 modify） |
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
| `enabled_tools` | 多选列表 | `[]` | 启用的工具名（含快捷组名）。勾选 `inta_shell` 自动启用 5 个 shell 工具；勾选 `todo_list` 自动启用 4 个 todo 工具。修改后需重启 AstrBot |
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

`enabled_tools` 多选列表除独立工具外，还支持**快捷组名**（group alias）—— 勾选后系统一次性给 LLM 注入该组全部子工具，避免漏勾导致功能残缺：

- `inta_shell` — 勾选后自动展开为 `astrbot_inta_shell_start`、`_send`、`_read`、`_stop`、`_list` 共 5 个工具
- `todo_list` (v2.6.1+) — 勾选后自动展开为 `todo_create` / `todo_query` / `todo_add` / `todo_update` / `todo_delete` / `todo_clear` 共 6 个工具（6 个强相关，缺一不可）

> 旧版（v2.6.0 之前）配置中可能含独立的 `todo_create` / `todo_query` / `todo_modify` / `todo_clear` 条目，升级后仍可正常工作。v2.6.1 起配置 UI 只暴露 `todo_list` 组别名，引导用户使用推荐方式。**v2.12 起 `todo_modify` 工具已删除**，老 config 中显式列出的 `todo_modify` 会被 `filter_enabled_tools()` 报告为 `unknown` 提示升级，不会静默忽略。

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

### todo_create / todo_query / todo_add / todo_update / todo_delete / todo_clear — LLM 自我管理任务清单（v2.6+ 拆分 API；v2.12 进一步拆分 modify）

跨会话持久化的任务清单系统，**v2.6 起拆分为独立 LLM 工具**；**v2.12 进一步把 `todo_modify(mode=...)` 拆为 3 个独立工具**(`todo_add` / `todo_update` / `todo_delete`)，消除 LLM 误用 `mode` 字段的高发错误。

**6 工具分工：**

- `todo_create(items?, title?, from_path?)` — 创建/覆盖当前用户的列表（v2.9+ 新增 `from_path` 从 .md 领养）
- `todo_query()` — 读取当前列表（含 `stats` + `attention_items`）
- `todo_add(items=[{...}, ...])` — **v2.12 新增** 追加一个或多个 item 到现有列表（替代 v2.6 的 `todo_modify(mode='add', ...)`）
- `todo_update(item_ids, status?, notes?)` — **v2.12 新增** 更新一个或多个 item 的 status / notes（替代 `todo_modify(mode='update', ...)`）
- `todo_delete(item_ids)` — **v2.12 新增** 删除一个或多个 item（替代 `todo_modify(mode='delete', ...)`）
- `todo_clear()` — 删除整个列表

**v2.12 拆分动机：** v2.6 的 `todo_modify` 是 3-in-1 工具，LLM 经常把 `mode` 字段传错（漏传、拼错、用单数等），
导致调用失败。拆成 3 个独立工具后，每个工具的 schema 极简、参数互不重叠，LLM 不再需要选 `mode`，
误调用率从约 10% 降至接近 0%。

**v2.12 迁移：** 老 `todo_modify(mode='add', ...)` 改用 `todo_add(items=[...])`；
`todo_modify(mode='update', ...)` 改用 `todo_update(item_ids=..., status=..., notes=...)`；
`todo_modify(mode='delete', ...)` 改用 `todo_delete(item_ids=...)`。
**`notes` 三态语义保持不变**（仅在 `todo_update` 适用）：未传=保留旧值 / `""`=清空 / `"xxx"`=覆盖。

**v2.9+ 从 .md 文件领养清单（`from_path`）：**

- 参数与 `items` 互斥：必须且只能传其中一个
- 路径必须是**绝对路径**，相对路径直接拒绝（防沙箱逃逸）
- 文件大小上限 1MB（与 `MAX_FILE_SIZE` 共享）
- 解析后 **0 个 item** → 报错（避免导入空文件）
- 领养（adopt）语义：保留文件中的 `status` / `notes`，但**重排 ID 从 1**、**改写 `umo` 为当前会话**、`updated_at = now()`
- `title` 行为：非空 = 覆盖文件中的 title；空 = 保留文件中的 title（whitespace-only 视为有值）
- 失败时**不创建任何文件**，调用方拿到 `{ok: false, error: ...}` 即可重试

```python
# A) items 模式（v2.6 原始行为，向后兼容）
todo_create(title="重构模块", items=[
    {"title": "分析代码"},
    {"title": "切分函数"},
    {"title": "补测试"},
])

# B) from_path 模式（v2.9 新增）— 从 webchat 用户写好的计划领养
todo_create(from_path="/abs/path/to/webchat_plan.md", title="我的 v1.0 计划")
# 或纯领养、不改 title:
todo_create(from_path="/abs/path/to/webchat_plan.md")
```

**通用语义：**

- 4 种状态：`pending [ ]` / `in_progress [~]` / `done [x]` / `cancelled [-]`
- 按 umo（unified_msg_origin）隔离：私聊/群聊/不同群各自独立一份（v2.11 起切断“会话接力”）
- 批量操作任意失败 → 全量回滚
- `in_progress` + `notes` → 在 query 结果中标记为 `attention`
- 总计超过 100 项 → 拒绝添加

**notes 三态**（仅 `todo_update`）：未传=保留旧值 / `""`=清空 / `"xxx"`=覆盖。
注意：`todo_update` 工具的 `notes` 参数是**可选**的，LLM 调用时如果完全不传 `notes` key，框架层会保留旧值；
如果显式传 `""` 则清空；如果传 `"xxx"` 则覆盖。**不要传 `null`**，当前实现会按"未传"处理。

```python
# 1. 创建任务清单
todo_create(title="重构模块", items=[
    {"title": "分析代码"},
    {"title": "切分函数"},
    {"title": "补测试"},
])

# 2. 查询当前清单（含 stats + attention_items）
todo_query()

# 3. v2.12 批量追加（独立工具，不再需要 mode="add"）
todo_add(items=[
    {"title": "写测试", "status": "in_progress"},
    {"title": "补文档", "status": "pending"},
])

# 4. v2.12 更新状态（独立工具，item_ids 可为单个 int 或 list[int]）
todo_update(item_ids=[1, 3], status="done")
todo_update(item_ids=2, status="in_progress",
            notes="等待用户确认函数切分边界")

# 5. v2.12 删除单个 / 批量（独立工具）
todo_delete(item_ids=1)
todo_delete(item_ids=[2, 3])

# 6. 清空整个列表（独立工具，区别于 delete）
todo_clear()
```

**迁移说明：**
- v2.6 之前的单工具 `todo_list(action=...)` 已废弃，转为 stub 工具（保留一个版本周期）。
- **v2.12 起 `todo_modify` 工具彻底删除**。`todo_modify` 仅在 v2.6 ~ v2.11 之间存在（约 6 个版本）。
  升级到 v2.12 后，老 config 中显式列出的 `todo_modify` 条目会被 `filter_enabled_tools()` 报告为 `unknown`（不会静默忽略），
  引导用户从 enabled_tools 移除后改用 `todo_list` 组别名。
- 新代码应使用上述 6 个工具之一。

### /project load & /project unload — 项目一键加载/卸载（v2.7+）

把"加载项目"从 4 步压缩为 1 步。需在配置中**同时**启用 agentsmd 和 codegraph。

```text
# 一键加载：自动执行 agentsmd init+load、codegraph init+set、注入 codegraph 优先使用指引
/project load C:/Users/me/projects/myapp

# 卸载：清掉 AGENTS.md 注入，把 codegraph 默认项目指回配置中的 codegraph_project
/project unload
```

**前置条件**：`agentsmd_enabled = true` **AND** `codegraph_enabled = true`。任一关闭时命令会拒绝执行并提示。

**重复 load 防护**：当前会话已加载项目时再次 `/project load` 会被拒绝（需先 `/project unload`），避免半残状态。

**注入行为**：加载成功后，system_prompt 末尾会追加"优先使用 codegraph 工具组"指引，引导 LLM 在该项目中调用 `codegraph_search` / `codegraph_explore` / `codegraph_callers` 等高级语义搜索，而不是退而使用低效的 `astrbot_file_grep_tool`。

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

## Web API (Dashboard 端点)

`v3.6+` 起插件向 AstrBot 注册 Dashboard 消费的 HTTP 端点(挂载前缀 `/spcode`),
供前端 Dashboard 实时拉取项目状态、文件树、git 信息。

`v3.7+ (2026-06-24)` 新增 4 个 git workflow 端点:
- `git-log` (GET) — git 历史查询
- `git-stage` (POST) — git add
- `git-unstage` (POST) — git reset HEAD
- `git-commit` (POST) — git commit(严格最小)

合计 **10 端点**(6 GET + 4 POST),完整列表:

| 端点 | 方法 | 用途 | 关键参数 |
|------|------|------|---------|
| `/spcode/project-status` | GET | 当前加载项目状态 | `umo?` |
| `/spcode/plan-mode` | GET | 当前 plan-mode 状态 | `umo?` |
| `/spcode/git-diff` | GET | 工作区 diff | `umo`, `worktree?` |
| `/spcode/git-worktrees` | GET | 列出 worktree | `umo` |
| `/spcode/git-log` | GET | git 历史(8 字段) | `umo`, `n?`, `ref?`, `path?`, `author?`, `since?`, `until?` |
| `/spcode/git-stage` | POST | git add(files 或 all,互斥) | body: `{files:[…]}` \| `{all:true}` |
| `/spcode/git-unstage` | POST | git reset HEAD(files 或 all,互斥) | body: `{files:[…]}` \| `{all:true}` |
| `/spcode/git-commit` | POST | git commit(仅 message) | body: `{message:"…"}` |
| `/spcode/git-init` | POST | 在空目录上 git init(**唯一**豁免 preflight) | body: `{path, initial_branch?="main", bare?=false}` |
| `/spcode/git-branches` | GET | 列出所有 branch(local+remote)+current+default(ETag/304) | `umo`, `worktree?` |
| `/spcode/git-branch-create` | POST | 从 HEAD/指定 start_point 创建 branch | body: `{name, start_point?, force?}` |
| `/spcode/git-branch-delete` | POST | 删除 branch(`-d` 仅 merged,`-D` 含 unmerged;硬禁 current/main) | body: `{name, force?=false}` |
| `/spcode/git-branch-switch` | POST | 切换 current branch(支持 `create=true` / `detach=true` / `force=true` 跨字段) | body: `{name, create?=false, start_point?, force?=false, detach?=false}` |
| `/spcode/git-revert` | POST | 创建回滚 commit(`--no-edit`,复用 `git_commit` env passthrough) | body: `{ref?="HEAD", no_edit?=true}` |
| `/spcode/file-browser` | GET | 读文件内容 / 列单层目录 | `umo`, `path`, `worktree?`, `if_none_match?` |
| `/spcode/file-restore` | POST | 从快照恢复文件 | body: `{path:"…"}` |
| `/spcode/git-worktree-add` | POST | 新建 git worktree(`-b/-B/--detach/--force` 平铺) | body: `{path, branch?, create?, force?, detach?, base?}` |
| `/spcode/git-worktree-remove` | POST | 删除 git worktree(硬禁 main,locked 拒,`force=true` 跳过 dirty) | body: `{path, force?}` |
| `/spcode/git-worktree-lock` | POST | 锁定 git worktree(可选 `--reason`),main 允许但 git 自身拒绝 | body: `{path, reason?}` |
| `/spcode/git-worktree-unlock` | POST | 解锁 git worktree,main 允许但 git 自身拒绝 | body: `{path}` |

**所有写端点 (git-stage / git-unstage / git-commit / file-restore) 共享约束**:
- 单次请求文件数 ≤ 100
- 文件路径必须经过 4 步防御:含 `..` / 绝对路径 / `.git/` 段 / symlink 越界 → `path_unsafe`
- `worktree` 参数沿用 6 步防御链,git-common-dir 不匹配 → `worktree_invalid`
- commit message 上限 8192 字符;空 / 超长 / 非 str → `invalid_message`
- commit 失败按 stderr 关键字符串映射为 4 类:`hook_rejected` / `identity_not_set` /
  `nothing_to_commit` / `git_error`

**统一响应 envelope**:

```json
{
  "success": true,
  "reason": null,
  "elapsed_ms": 42,
  "data": { /* 端点专有字段 */ }
}
```

失败时 `success=false` + `reason=<ReasonCode 字符串>`,见 `AGENTS.md` 的 ReasonCode 集中表。

**实现位置**: `tools/webapi/`(每个端点一个文件)
- `__init__.py` — `ROUTES` 表 + `_wrap()` 适配器 + `register_webapi_routes()`
- `_helpers.py` — `ReasonCode` / `_make_envelope` / `_git_endpoint_preflight` /
  `_validate_repo_relative_file` / `_run_git_async` / `_JSONResponseCompat`

## v2.16.0 (2026-07-06)

- **New endpoint** `POST /spcode/file-discard-hunk` — 按 hunk 粒度丢弃工作区 / 已暂存改动(unified diff 文本入参,`git apply --reverse` 语义)
- **Reason codes** +9 — `patch_empty` / `patch_too_large` / `patch_malformed` / `patch_unsafe_path` / `multi_file_patch` / `patch_file_mismatch` / `patch_binary` / `patch_check_failed` / `patch_apply_failed`
- **Refactor** 提取 `X_TRULY_STAGED` / `Y_WORKTREE` 常量至 `tools/webapi/_helpers.py`,供 `file-restore` 与 `file-discard-hunk` 共享
- **Tests** +30 — 单元测试覆盖 5 大类(parser / envelope / body 校验 / file 安全 / git ops)+ 端到端路由计数 19→20

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
