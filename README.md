# spcode 工具箱

> **当前版本: v2.20** · 作者: elecvoid243

AstrBot 插件，为 LLM Agent 提供一组面向 C/C++/Python 开发的实用工具，并附带一套供 Dashboard 消费的 Web API。

部分工具实现参考自 [Irmia DevKit](https://github.com/irmia2026/irmia_devkit_open)。

## 功能概览

| 类别 | 能力 |
|------|------|
| 代码质量 | `code_check` 语法+风格合并检查；`code_format` 源码自动格式化 |
| 代码理解 | 通过 codegraph 官方 MCP server 注入 8 个 `codegraph_*` 语义搜索/调用链工具 |
| 文件操作 | `astrbot_file_remove` 沙箱化删除；`astrbot_file_compare` 结构化差异比较 |
| 文件搜索 | `es_search` Everything/locate/fd/Python 兜底文件名搜索 |
| 任务管理 | `todo_*` 6 工具，LLM 自我管理跨会话持久化任务清单 |
| 交互式 Shell | `astrbot_inta_shell_*` 5 工具，多轮双向 Shell 会话管理 |
| 项目加载 | `/project load|unload|status` 一键组合 agentsmd + codegraph |
| Plan/Build 模式 | `/plan` 过滤写工具进入只读调研；`/build` 还原 |
| Web API | 31 条 `/spcode/*` 路由，供 Dashboard 实时拉取项目状态/文件树/git 信息 |

> **L1 鉴权**: spcode 工具箱是**管理员工具集**。非管理员用户看不到上述任何工具；管理员可见全部。codegraph MCP 工具同样受此鉴权约束。

## 工具清单

### 本地 LLM 工具（16 个）

通过 `enabled_tools` 配置项按需启用（默认全部禁用）。勾选快捷组名会自动展开为一组子工具。

| 工具名 | 类别 | 说明 |
|--------|------|------|
| `code_check` | 代码质量（只读） | Python (ruff) / C·C++ (cppcheck + cpplint) 语法+风格合并检查，结构化 issues |
| `code_format` | 代码质量（写入） | 源码自动格式化：`.py` → ruff format；C/C++/Java/JS/TS/C# → AStyle (stdin/stdout)。v2.14 引入 |
| `es_search` | 文件搜索 | Windows Everything (es.exe) 毫秒级搜索；Linux/macOS `locate`→`fd`→Python `os.walk` 三层 fallback |
| `astrbot_file_remove` | 文件操作（写入） | 沙箱化文件/目录删除，双层黑名单 + 批量确认提案机制 |
| `astrbot_file_compare` | 文件操作（只读） | 结构化文件差异（added/removed 行数 + unified diff），UTF-8/GBK 自适应 |
| `todo_create` / `todo_query` / `todo_add` / `todo_update` / `todo_delete` / `todo_clear` | 任务管理（6 个，组别名 `todo_list`） | LLM 自我管理任务清单；v2.12 把 `todo_modify` 拆为 3 个独立工具，消除 `mode` 误用 |
| `astrbot_inta_shell_start` / `_send` / `_read` / `_stop` / `_list` | 交互式 Shell（5 个，组别名 `inta_shell`） | 持久子进程多轮双向通信 |

### MCP 工具（codegraph，8 个）

由 codegraph 官方 MCP server 自动注入：`codegraph_search` / `_callers` / `_callees` / `_impact` / `_node` / `_explore` / `_status` / `_files`

### vivado-mcp 集成 (v2.21+)

将 FPGA 开发助手 vivado-mcp（21 个 `mcp_vivado__*` 工具）集成到 spcode。

**安装**: `pip install vivado-mcp`

**配置**: `vivado.enabled=true`，可选 `vivado.executable`（Vivado 工具链路径，留空走自动检测）。

**chat 命令** (`/vivado`): `status` / `start [name]` / `stop <name>` / `path`

**Web API**: `GET /spcode/vivado-status` — 完整快照 (enabled / mcp_running / vivado_path / sessions)

**注意**:
- 10 个写工具（含烧板 `program_device`）在 plan 模式下被隐藏
- 管理员鉴权: 非管理员看不到 `mcp_vivado__*` 工具（含只读）
- 烧板操作后果不可逆，务必在 plan 模式调研后切到 build 模式执行

## 命令清单

### `/codegraph`（别名 `/cg`）

| 命令 | 描述 |
|------|------|
| `/codegraph init <文件夹路径>` | 在目标项目目录初始化 codegraph（创建 `.codegraph/`） |
| `/codegraph uninit <文件夹路径>` | 删除目标项目目录的 codegraph 索引 |
| `/codegraph set <文件夹路径>` | 修改默认项目根目录；若 MCP 正在运行则自动重启以应用新路径 |

### `/agentsmd`

| 命令 | 描述 |
|------|------|
| `/agentsmd init <文件夹路径>` | 扫描目录结构并调用 LLM 自动生成 AGENTS.md |
| `/agentsmd load <文件夹路径>` | 加载 AGENTS.md 到当前会话系统提示词（后续 LLM 请求自动注入） |
| `/agentsmd unload` | 卸载当前会话的 AGENTS.md 注入 |
| `/agentsmd update` | 基于现有 AGENTS.md + 最新目录结构，调 LLM 重新生成 |

### `/project`（v2.7+）

| 命令 | 描述 |
|------|------|
| `/project load <文件夹路径>` | 一键组合 `agentsmd init+load` + `codegraph init+set` + 注入 codegraph 优先使用指引。需同时启用 agentsmd 和 codegraph |
| `/project unload` | 清掉 AGENTS.md 注入，把 codegraph 默认项目指回配置值 |
| `/project status` | 查看当前会话已加载项目信息 |

### `/plan` 与 `/build`（v2.8）

| 命令 | 描述 |
|------|------|
| `/plan` | 进入 plan 模式：按 `plan_mode_blocked_tools` 配置从 LLM 工具列表过滤写工具，并在首轮 LLM 调用时注入 plan 模式 reminder |
| `/build` | 退出 plan 模式，恢复全部工具可用（默认状态） |

> `/project`、`/agentsmd`、`/codegraph` 命令均受 L1 鉴权约束，非管理员调用会被拦截。

## 安装

将插件文件夹放入 AstrBot 的 `data/plugins/` 目录，重启 AstrBot。本地开发可用符号链接而非复制。

## 配置

进入 **AstrBot WebUI -> 插件管理 -> astrbot_plugin_spcode_toolkit -> 配置**。

> 配置 schema 定义在 `_conf_schema.json`，采用分组结构。`main.py._flatten_config()` 会在加载时把嵌套分组拍平为顶层键（如 `codegraph.codegraph_enabled` → `codegraph_enabled`）。

### 全局配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `enabled_tools` | 多选列表 | `[]` | 启用的工具名（含快捷组名）。勾选 `inta_shell` 自动启用 5 个 shell 工具；勾选 `todo_list` 自动启用 6 个 todo 工具。修改后需重启 AstrBot。默认全部禁用（安全默认） |
| `allowed_ids` | 字符串 | `""` | 额外允许使用本插件的用户 ID（逗号分隔），与 AstrBot 全局管理员并集生效 |

### code_check 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `cppcheck_path` | 字符串 | `""` | cppcheck.exe 路径（C/C++ 正确性检查）。留空时按 `CPPCHECK_PATH` 环境变量 → `shutil.which("cppcheck")` → 常见安装路径的顺序查找 |
| `cppcheck_shortcircuit` | 单选 | `"error"` | cppcheck 短路策略：`error`=有 error 时跳过 cpplint（默认）/ `warning`=有 error 或 warning 时跳过 / `never`=两个工具都跑 |

### code_format 配置（v2.14+）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `default_style` | 单选 | `"allman"` | AStyle 默认风格（仅 C/C++/Java/JS/TS/C# 生效）：allman / google / kr / linux / stroustrup / whitesmith / horstmann / ratliff / vtk / java / none |
| `default_indent` | 整数 | `4` | AStyle 默认缩进空格数（1-16）。ruff 用自身默认配置 |

> `code_format` 是**写入工具**（可能修改文件），plan 模式下默认被过滤。默认不在 `enabled_tools` 中勾选，需显式启用。

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

### plan_mode 配置（v2.8+）

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `plan_mode_blocked_tools` | 字符串列表 | 见下 | `/plan` 激活时从 LLM 工具列表中过滤的工具名。默认走 strict 模式，过滤所有 spcode 写工具 + AstrBot 内建文件写编辑工具 |
| `plan_mode_reminder` | 文本 | 内置英文模板 | 切换到 plan 模式时给 LLM 的提醒文本，作为 `<system-reminder>` 注入。支持 `{blocked}` 占位符替换为实际黑名单列表。留空则不注入 |

默认 `plan_mode_blocked_tools`：

```json
[
  "astrbot_file_remove",
  "code_format",
  "todo_create",
  "todo_add",
  "todo_update",
  "todo_delete",
  "todo_clear",
  "astrbot_inta_shell_start",
  "astrbot_inta_shell_send",
  "astrbot_inta_shell_stop",
  "astrbot_file_write_tool",
  "astrbot_file_edit_tool"
]
```

### git_diff 配置

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `git_path` | 字符串 | `""` | git 可执行文件绝对路径。留空时自动从 PATH 查找 `git`。影响所有 `/spcode/git-*` 端点 |

### `enabled_tools` 快捷组名

`enabled_tools` 多选列表除独立工具外，还支持**快捷组名**（group alias）-- 勾选后系统一次性给 LLM 注入该组全部子工具，避免漏勾导致功能残缺：

- `inta_shell` - 自动展开为 `astrbot_inta_shell_start`、`_send`、`_read`、`_stop`、`_list` 共 5 个工具
- `todo_list` (v2.6.1+) - 自动展开为 `todo_create` / `todo_query` / `todo_add` / `todo_update` / `todo_delete` / `todo_clear` 共 6 个工具
- `file_compare` - 展开为 `astrbot_file_compare`
- `file_remove` - 展开为 `astrbot_file_remove`

> v2.12 起 `todo_modify` 工具已删除，老 config 中显式列出的 `todo_modify` 会被 `filter_enabled_tools()` 报告为 `unknown` 提示升级，不会静默忽略。

## 前置依赖

| 工具 | 可选依赖 | 缺失时行为 |
|------|----------|-----------|
| `code_check` (Python) | ruff (`pip install ruff`) | 返回安装提示 |
| `code_check` (C/C++) | cppcheck（先跑正确性检查）+ cpplint（后跑风格检查） | 返回安装提示 |
| `code_format` (Python) | ruff | 返回安装提示 |
| `code_format` (C/C++/Java/JS/TS/C#) | AStyle | 返回安装提示 |
| `codegraph_*` (8 个) | `@colbymchenry/codegraph` (npm) + MCP server 已配置 | 8 个工具全部不在 LLM 工具列表；插件其它工具照常 |
| `es_search` (Windows) | Everything + es.exe | 返回安装提示 |
| `es_search` (Linux/macOS) | locate / fd / Python 兜底 | 自动降级到 Python `os.walk` |

`requirements.txt`（lint + 运行时依赖）：`ruff`、`cpplint`、`astyle`、`send2trash`。

## 工具详解

### code_check - 语法+风格合并检查

对单个 Python 或 C/C++ 源文件执行一次性检查，同时覆盖**语法错误**和**风格问题**：

- **Python (`.py`)**：使用 ruff（PEP 8 + 常用 lint 规则）
- **C/C++ (`.c/.cpp/.cc/.cxx/.h/.hpp/.hxx`)**：先跑 cppcheck（正确性检查），根据 `cppcheck_shortcircuit` 策略决定是否继续跑 cpplint（Google C++ Style Guide）
- 返回结构化 issues 列表，前 5 条带上下文源码（`->` 标记违规行）
- 其他扩展名（`.js/.ts/.go/.nim` 等）**不支持**

### code_format - 源码自动格式化（v2.14+）

与 `code_check` 的关系：`code_check` 是**只读**检查，`code_format` 是**写**工具（可能修改文件）。LLM 在 plan 模式下不应调用本工具。

- **formatter = "auto" 路由**：`.py` → ruff format；`.c/.cpp/.cc/.cxx/.h/.hpp/.hxx/.hh/.java/.js/.jsx/.ts/.tsx/.mjs/.cjs/.cs` → AStyle
- **AStyle 稳定性策略**：永远 stdin/stdout 调用（不原地修改、不创建 `.orig` 备份），用 stdlib `difflib` 比对判断是否 changed，只有 changed 才写回
- **ruff 调用**：`check=False` → `ruff format <file>`（直接写回）；`check=True` → `ruff format --check --diff <file>`（不写，只报告）
- **幂等语义**：第二次格式化同一文件 → `changed=False`
- 支持干跑模式（`check=true`，不写回，仅报告是否有改动）

### es_search - 文件名极速搜索

不搜索文件内容，仅搜索文件名：

- **Windows**：通过 Everything CLI (`es.exe`) 毫秒级搜索，支持 wildcard、regex、扩展名/路径过滤、大小写/全词、排序
- **Linux/macOS**：`locate` → `fd` → Python `os.walk` 三层自动降级

### astrbot_file_remove - 文件删除

删除文件或目录时有两层黑名单保护：

| 层级 | 来源 | 说明 |
|------|------|------|
| 内置系统目录 | 代码常量 | 拦截 `C:/Windows`、`/etc`、`/System` 等，不可绕过 |
| 用户自定义 | 插件配置 `file_remove_blacklist` | 拦截用户指定的关键路径 |

**匹配规则**：目标路径 `resolve()` → 归一化（`\` 转 `/` → `lower()`）→ 与黑名单前缀"完全相等"或"以 `前缀/` 开头"即拦截。

**提案协议**：
- 目录删除需 `confirm=true`
- 目录内文件数超过 `max_items`（默认 50）时，不直接删除，返回 proposal 要求二次确认
- 黑名单拦截时返回 `evidence.blocked_by`（用户黑名单带 `user:` 前缀），LLM 看到后不应重试，应向用户解释路径被保护
- 删除走系统回收站（`send2trash`），可恢复

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

### astrbot_file_compare - 文件差异比较

比较两个文本文件，返回结构化 diff（added/removed 行数 + unified diff，最多 100 行）。超过 50MB 的文件会被拒绝。编码自动检测：UTF-8 优先，GBK 兜底。

### todo_create / todo_query / todo_add / todo_update / todo_delete / todo_clear - LLM 自我管理任务清单

跨会话持久化的任务清单系统。**v2.6** 起拆分为独立 LLM 工具；**v2.12** 进一步把 `todo_modify(mode=...)` 拆为 3 个独立工具（`todo_add` / `todo_update` / `todo_delete`），消除 LLM 误用 `mode` 字段的高发错误。

**6 工具分工：**

- `todo_create(items?, title?, from_path?)` - 创建/覆盖当前用户的列表（v2.9+ 新增 `from_path` 从 .md 领养）
- `todo_query()` - 读取当前列表（含 `stats` + `attention_items`）
- `todo_add(items=[{...}, ...])` - **v2.12 新增** 追加一个或多个 item 到现有列表
- `todo_update(item_ids, status?, notes?)` - **v2.12 新增** 更新一个或多个 item 的 status / notes
- `todo_delete(item_ids)` - **v2.12 新增** 删除一个或多个 item
- `todo_clear()` - 删除整个列表

**通用语义：**
- 4 种状态：`pending [ ]` / `in_progress [~]` / `done [x]` / `cancelled [-]`
- 按 umo（unified_msg_origin）隔离：私聊/群聊/不同群各自独立一份（v2.11 起切断"会话接力"）
- 批量操作任意失败 → 全量回滚
- `in_progress` + `notes` → 在 query 结果中标记为 `attention`
- 总计超过 100 项 → 拒绝添加

**notes 三态**（仅 `todo_update`）：未传=保留旧值 / `""`=清空 / `"xxx"`=覆盖。注意不要传 `null`（当前实现按"未传"处理）。

**v2.9+ 从 .md 文件领养清单（`from_path`）：** 参数与 `items` 互斥；路径必须是绝对路径；文件大小上限 1MB；解析后 0 个 item 报错；领养（adopt）语义：保留 `status` / `notes`，但重排 ID 从 1、改写 `umo` 为当前会话、`updated_at = now()`。

```python
# A) items 模式
todo_create(title="重构模块", items=[
    {"title": "分析代码"},
    {"title": "切分函数"},
    {"title": "补测试"},
])

# B) from_path 模式 - 从 .md 领养
todo_create(from_path="/abs/path/to/webchat_plan.md", title="我的 v1.0 计划")

# C) 查询当前清单（含 stats + attention_items）
todo_query()

# D) v2.12 批量追加（独立工具，不再需要 mode="add"）
todo_add(items=[
    {"title": "写测试", "status": "in_progress"},
    {"title": "补文档", "status": "pending"},
])

# E) v2.12 更新状态（item_ids 可为单个 int 或 list[int]）
todo_update(item_ids=[1, 3], status="done")
todo_update(item_ids=2, status="in_progress",
            notes="等待用户确认函数切分边界")

# F) v2.12 删除单个 / 批量
todo_delete(item_ids=1)
todo_delete(item_ids=[2, 3])

# G) 清空整个列表
todo_clear()
```

**迁移说明：** v2.6 之前的单工具 `todo_list(action=...)` 已废弃为 stub。`todo_modify` 仅在 v2.6 ~ v2.11 之间存在，v2.12 起彻底删除。升级到 v2.12 后，老 config 中显式列出的 `todo_modify` 会被 `filter_enabled_tools()` 报告为 `unknown`，引导用户改用 `todo_list` 组别名。

### /project load & /project unload & /project status - 项目一键加载/卸载（v2.7+）

把"加载项目"从 4 步压缩为 1 步。需在配置中**同时**启用 agentsmd 和 codegraph。

```text
# 一键加载：自动执行 agentsmd init+load、codegraph init+set、注入 codegraph 优先使用指引
/project load C:/Users/me/projects/myapp

# 卸载：清掉 AGENTS.md 注入，把 codegraph 默认项目指回配置中的 codegraph_project
/project unload

# 查看状态
/project status
```

**前置条件**：`agentsmd_enabled = true` **AND** `codegraph_enabled = true`。任一关闭时命令会拒绝执行并提示。

**重复 load 防护**：当前会话已加载项目时再次 `/project load` 会被拒绝（需先 `/project unload`），避免半残状态。

**注入行为**：加载成功后，system_prompt 末尾会追加"优先使用 codegraph 工具组"指引，引导 LLM 在该项目中调用 `codegraph_search` / `codegraph_explore` / `codegraph_callers` 等高级语义搜索，而不是退而使用低效的 `astrbot_file_grep_tool`。

### /plan 与 /build - Plan/Build 模式（v2.8）

借鉴 opencode 的 plan/build 模式：

- `/plan` 激活后，`plan_mode_blocked_tools` 列出的工具从 LLM 工具列表中过滤掉，LLM 只能使用只读工具调研；并在第一轮 LLM 调用时向 user message 注入 plan 模式 reminder（prefix cache 友好）
- `/build` 退出 plan 模式，恢复全部工具可用。默认状态下完全不修改 LLM 请求，与 AstrBot 默认行为一致
- plan 模式状态严格 per-session（按 umo 隔离），不回退

### astrbot_inta_shell_* - 交互式 Shell

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
codegraph index       # 可选--首次 explore 调用会懒加载
```

### 配置 MCP

进入 **WebUI -> 插件管理 -> astrbot_plugin_spcode_toolkit -> 配置**，填写：

| 字段 | 说明 |
|------|------|
| `codegraph_enabled` | 设为 `true`（默认） |
| `codegraph_install_dir` | codegraph 包根目录（含 `node.exe` 的目录） |

**Windows**：如 `F:\tools\codegraph-win32-x64` - 需要里面同时含 `node.exe` 和 `lib\dist\bin\codegraph.js`（或根目录的 `codegraph.js`）。

**Linux/macOS**：指向含 `node` 和 `codegraph.js` 的目录。

填好后**重启 AstrBot**，spcode 插件会启动 MCP server 并把 8 个 `codegraph_*` 工具注入到 LLM。

> **如果未配置 `codegraph_install_dir`**：MCP server 不会启动（不会自动扫描系统），LLM 看不到 8 个 `codegraph_*` 工具。`/codegraph init|uninit` 命令仍可通过 auto-detect 工作（前提是 `codegraph` 在系统 PATH 中）。

### 管理员命令

```
/codegraph init D:/projects/my-app         # 初始化 codegraph 项目
/codegraph uninit D:/projects/my-app       # 反初始化（删除 .codegraph/ 目录）
/codegraph set D:/projects/my-app          # 修改默认项目根目录
```

`/codegraph set` 的作用：把 `codegraph_project` 改为新目录。如果当前 MCP server 在跑，spcode 会自动重启它以应用新 `--path` 参数；之后所有 LLM 调用的 `codegraph_*` 工具默认在新目录下操作。

## AGENTS.md 管理

AGENTS.md 是 OpenCode 提出的项目级 LLM 指令文件，功能类似 Cursor 的 Rules 或 Claude Code 的 CLAUDE.md。

### 工作原理

1. `/agentsmd init <dir>` - 扫描目录结构 + 关键文件，调 LLM 生成 AGENTS.md
2. `/agentsmd load <dir>` - 读取文件内容到内存，标记当前会话
3. 后续 LLM 请求中，`@filter.on_llm_request()` 钩子自动将内容追加到 system_prompt
4. 文件变更自动检测（mtime），缓存自动刷新；`/agentsmd update` 则调 LLM 基于最新目录结构**重新生成**内容
5. 可使用 `agentsmd_enabled=false` 完全关闭此功能

### init_template 自定义

在配置中填写 `init_template` 可自定义生成 AGENTS.md 时使用的 prompt 模板。留空则使用内置默认模板。

## Web API (Dashboard 端点)

`v3.6+` 起插件向 AstrBot 注册 Dashboard 消费的 HTTP 端点（挂载前缀 `/spcode`），供前端 Dashboard 实时拉取项目状态、文件树、git 信息。

Web 路由由 `tools/webapi/register_webapi_routes(plugin)` 在 `main.py.initialize()` 中注册，挂载前缀 `/spcode`。当前共 **32 条路由记录**（30 个唯一路径，`/spcode/docs` 一路径复用 POST/PATCH/DELETE 三方法）：

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
| `/spcode/git-repo-check` | GET | 判断给定目录是否为 git 仓库（`git rev-parse --is-inside-work-tree`，独立 preflight） | `path` |
| `/spcode/git-init` | POST | 在目录上 git init（**唯一**豁免 preflight；`force=true` 允许非空目录） | body: `{path, initial_branch?="main", bare?=false, force?=false}` |
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
| `/spcode/btw` | POST | 一次性独立 LLM 请求（顺便问问）：复用当前会话历史命中 prefix cache，不回写历史，无工具，纯文本输出 | body: `{prompt, umo?}` |
| `/spcode/docs` | POST | 创建 / 覆盖 docs 文件（upsert 到工作区） | body: `{umo?, worktree?, path, content}` |
| `/spcode/docs` | PATCH | 重命名 docs 文件（纯文件系统 mv） | body: `{umo?, worktree?, path, new_path}` |
| `/spcode/docs` | DELETE | 从工作区删除 docs 文件（unlink） | body: `{umo?, worktree?, path}` |

**统一响应 envelope**：

```json
{
  "success": true,
  "reason": null,
  "elapsed_ms": 42,
  "data": { /* 端点专有字段 */ }
}
```

失败时 `success=false` + `reason=<ReasonCode 字符串>`，见 `AGENTS.md` 的 ReasonCode 集中表。

**共享约束：**
- 所有写端点（除 `git-init`）共享 5 步前置校验 `_git_endpoint_preflight`（feature flag / project loaded / worktree 安全 / directory 存在 / git repo）
- `git-init` 是**唯一**完全豁免 preflight 的写端点（需在空目录/非空目录上 `git init`，走独立的 `_git_init_preflight`）
- 文件路径必须经过 4 步防御 `_validate_repo_relative_file`：含 `..` / 绝对路径 / `.git/` 段 / symlink 越界 → `path_unsafe`
- `?worktree=` 参数沿用 6 步防御链，git-common-dir 不匹配 → `worktree_invalid`
- commit message 上限 8192 字符；空 / 超长 / 非 str → `invalid_message`
- 单次请求文件数 ≤ 100
- commit 失败按 stderr 关键字符串映射为 4 类：`hook_rejected` / `identity_not_set` / `nothing_to_commit` / `git_error`

> 完整的请求/返回字段与 ReasonCode 前端消费文档见 `docs/api/` 与 `docs/webapi_endpoints_report.md`。

## 架构

```text
astrbot_plugin_spcode_toolkit/
├── main.py                       # 插件入口：注册工具/命令/事件 + L1 鉴权 + 多个 @filter.on_llm_request 钩子
├── metadata.yaml                 # 插件元信息（AstrBot 加载识别）
├── _conf_schema.json             # WebUI 配置 schema（分组结构）
├── requirements.txt              # lint + 运行时依赖：ruff, cpplint, astyle, send2trash
├── README.md                     # 本文件
├── AGENTS.md                     # 供 coding agent 使用的项目规范
│
├── data/                         # 数据/资源（随插件分发）
│   ├── t2i_templates/            # HTML 模板
│   ├── temp/                     # 临时文件
│   └── workspaces/               # 沙箱化工作区
│
├── docs/                         # 项目文档 / 评审记录
│   ├── api/                      # Web API 前端消费参考
│   ├── webapi_endpoints_report.md
│   └── superpowers/              # specs/（设计）+ plans/（实施计划）+ reviews/ + handoffs/
│
├── tests/                        # pytest 测试（75+ 文件，与 tools/ 对应）
│   ├── conftest.py               # 共享 fixtures
│   ├── test_*.py
│   └── fixtures/                 # 测试夹具（样本文件/目录）
│
└── tools/                        # 工具实现层（子包化，PR-0~PR-7 拆分自 main.py）
    ├── __init__.py
    ├── _helpers.py               # [内部] run_cmd / unwrap / proposal_reply / run_sync / _is_valid_ref_name / _validate_worktree_param
    ├── _config.py                # [内部] 配置读取辅助
    ├── _config_filter.py         # [内部] enabled_tools 过滤逻辑 + 快捷组名展开（ALL_TOOL_NAMES / filter_enabled_tools）
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
    ├── codegraph/                # codegraph MCP + 命令管理子系统
    │   ├── __init__.py           #   CodegraphManager / bootstrap_mcp / shutdown_mcp / state
    │   ├── bootstrap.py          #   MCP server 启动 + 配置构造
    │   ├── manager.py            #   /codegraph init/uninit/set handler
    │   └── state.py              #   模块级状态（task 引用 + per-dir lock）
    │
    ├── project/                  # /project load/unload/status 子系统
    │   ├── __init__.py           #   ProjectManager facade
    │   ├── manager.py            #   命令分发 + 状态查询
    │   ├── pipeline.py           #   load/unload 流水线
    │   └── state.py              #   per-umo 已加载项目 state
    │
    ├── security/                 # 鉴权 + plan/build 模式
    │   ├── __init__.py           #   check_is_admin / PlanModeController
    │   ├── admin.py              #   L1 管理员鉴权
    │   └── plan_mode.py          #   plan/build 模式控制器（过滤工具 + reminder 注入）
    │
    ├── function_tools/           # 16 个 LLM FunctionTool 类（一文件一工具）
    │   ├── __init__.py           #   ALL_TOOL_CLASSES 集中注册表
    │   ├── _common.py            #   record_and_run 模板
    │   ├── code_check.py
    │   ├── code_format.py        #   v2.14
    │   ├── es_search.py
    │   ├── file_diff.py          #   astrbot_file_compare
    │   ├── file_remove.py
    │   ├── todo_base.py          #   _TodoToolBase 共用基类
    │   ├── todo_create.py / todo_query.py / todo_add.py / todo_update.py / todo_delete.py / todo_clear.py
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
    └── webapi/                   # Web API 层（31 条路由，每端点一文件）
        ├── __init__.py           #   ROUTES 路由表 + HANDLERS 别名 + _wrap() 适配器 + register_webapi_routes()
        ├── _helpers.py           #   ReasonCode / _make_envelope / _git_endpoint_preflight / _validate_repo_relative_file / _run_git_async / _JSONResponseCompat / _compute_git_etag / _compute_porcelain_diffs / _git_init_preflight
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
        └── docs_crud.py          #   POST/PATCH/DELETE /spcode/docs     (spec B)
```

### 架构分层

1. **入口层** `main.py`：在 AstrBot 启动时被加载；注册 AstrBot 工具（供 LLM 调用）+ 命令（`/codegraph`、`/agentsmd`、`/project`、`/plan`、`/build`）+ 多个 `@filter.on_llm_request()` 钩子（AGENTS.md 注入、codegraph 指引、todo/file_remove/code_check/code_format 指引、L1 鉴权、plan 模式过滤）；读取 `_conf_schema.json` 配置；**L1 鉴权**：整个 spcode 工具箱为管理员工具集，非管理员不可见任何工具

2. **工具层** `tools/`（PR-0~PR-7 拆分自 main.py，子包化）：
   - `function_tools/` - 16 个 LLM FunctionTool 类，一文件一工具，`ALL_TOOL_CLASSES` 集中注册表
   - `inta_shell/` - 交互式 Shell 复合工具集（component + tools + session_models + paths + runtime 单例）
   - `agentsmd/` / `codegraph/` / `project/` / `security/` / `llm_inject/` - 各业务子系统
   - 下划线前缀模块（`_xxx.py`）：内部模块，不直接注册为 AstrBot 工具
   - 顶层 `xxx.py`（如 `code_check.py`、`file_remove.py`）：legacy 业务实现入口，被 function_tools/ 引用

3. **Web API 层** `tools/webapi/`（v3.6+ 自 main.py 拆出；当前 31 条路由记录 / 29 个唯一路径）：
   - 每个端点一个文件，handler 命名固定为 `async def handle(plugin, ...) -> dict`（`docs_crud.py` 例外，一文件承载三方法）
   - `__init__.py` 拥有 `ROUTES` 路由表 + `HANDLERS` 别名表 + `_wrap()` 适配器 + `register_webapi_routes()`
   - `main.py.initialize()` 调用一次 `register_webapi_routes(self)` 注册全部路由
   - `_wrap()` 通过 `inspect.signature(handler)` 自动注入 `umo` / `worktree` / `scope` / `path` / `if_none_match` / `body` 形参（handler 必须显式声明才注入；GET 从 query/header 取，POST/PATCH/DELETE 从 JSON body 取）

4. **测试层** `tests/`（75+ 文件）：与 `tools/` 模块对应；`conftest.py` 提供共享 fixtures；`tests/fixtures/` 存放静态样本；`tests/test_webapi_end_to_end.py` 跨端点烟囱测试

5. **数据层** `data/`：`t2i_templates/`（HTML 模板）、`workspaces/`（沙箱化操作根目录）、`temp/`（临时文件）

6. **文档层** `docs/`：`api/`（前端消费参考）、`webapi_endpoints_report.md`、`superpowers/`（`specs/` 设计 + `plans/` 实施计划 + `reviews/` + `handoffs/`）

## 开发

本项目为 Python AstrBot 插件，**不使用 msbuild**，无需独立构建步骤。打包/加载由 AstrBot 宿主在启动时读取 `main.py` 完成。

```bash
# Lint
ruff check .
ruff check . --fix

# 测试
pytest tests/                       # 全部
pytest tests/ -v -x                 # 详细 + 遇错即停
pytest tests/ -k "todo"             # 按关键字
pytest tests/ --cov=tools           # 覆盖率
```

加载到 AstrBot：将项目根目录放入 AstrBot 插件目录，启动 AstrBot 即可加载 `main.py`。本地开发可用符号链接。

---

> Author: elecvoid243 · 本文档同步至 v2.20 (2026-07-17)
