# spcode 静默操作 + chip 实时进度 设计文档

- Author: elecvoid243
- Date: 2026-08-06
- Status: Approved (brainstorming 2026-08-06, 用户逐项确认: 进度展示=chip / 范围=三操作全改 / 失败呈现=chip 红态+popover)
- 涉及仓库:
  - 插件: `F:\github\astrbot_plugin_spcode_toolkit`(本仓库,分支 `feat-silent-ops-progress`)
  - 前端: `F:\github\Astrbot`(dashboard,分支另建 `feat-silent-ops-progress`)

## 1. 背景与问题

Dashboard ChatUI 输入区的 spcode project chip(`SpcodeProjectIndicator`)点击后打开
`ProjectLoadDialog`,用户确认后 dialog 拼出 `/project load <dir> [flags]` 文本命令,
经 `ChatInput.handleProjectLoadSubmit` 注入聊天框发送。问题:

1. **聊天框污染** —— 每次加载/卸载/codegraph set 都在会话里留下一条用户命令消息 +
   一条 bot 流水线回复,纯 UI 操作不该进对话历史。
2. **无实时进度** —— 加载流水线有 4 个子步骤(agentsmd init/load、codegraph init/set),
   codegraph set 含 MCP 重启最长 180s,用户在命令发出后只能干等 bot 回复。

插件侧已有 `POST /spcode/project-load`(2026-07-28 引入,`tools/webapi/project_load.py`)
提供静默加载,但:
- 它是一次性请求-响应,`substep_messages` 只在流水线结束后整体返回,**无进度通道**;
- unload / codegraph set **没有**对应静默端点。

前端已有 `useSpcodeProjectAutoLoad.ts` 调 `POST /spcode/project-load`(仅用于 project
类型会话的自动加载),证明静默链路可行。

## 2. 设计决策(用户已确认)

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 进度展示位置 | **chip 上**:dialog 立即关闭,chip 进入 loading 态(旋转图标 + 当前步骤文本),失败细节通过 chip 红态 popover 查看 |
| 2 | 改造范围 | **三个操作全改**:project load / project unload / codegraph set 全部静默化 |
| 3 | 失败呈现 | chip 红色失败态 + 下拉箭头 popover 展示完整 `messages` 日志 + snackbar 失败摘要;点 chip 本体重开 dialog 重试;失败态在下一次成功操作或会话切换后清除 |

## 3. 后端设计(插件仓库)

### 3.1 通用操作进度存储 —— 新模块 `tools/webapi/_operation_progress.py`

模块级 dict 按 umo 键控,记录**每个 umo 最近一次操作**的进度:

```python
{
    "<umo>": {
        "operation": "project_load" | "project_unload" | "codegraph_set",
        "status": "running" | "done" | "failed",
        "messages": list[str],       # 子步骤日志(同 substep_messages 语义)
        "current_step": str,          # 最后一条 ⏳/🔄 开头的行;无则 ""
        "started_at": float,          # time.time()
        "finished_at": float | None,
        "reason": str | None,         # 失败码(silent 层业务 reason 串)
    }
}
```

公开 API(全部同步函数,操作的是内存 dict):

- `begin(umo: str, operation: str) -> bool`
  - 该 umo 已有 `status == "running"` 的记录时返回 `False`(拒绝并发),
    否则写入新记录并返回 `True`。
- `append(umo: str, message: str) -> None`
  - 追加到 `messages`;若 message 以 `⏳` 或 `🔄` 开头则更新 `current_step`。
- `finish(umo: str, ok: bool, reason: str | None = None) -> None`
  - 置 `status` / `finished_at` / `reason`。
- `query(umo: str) -> dict | None`
  - 返回记录副本;无记录返回 `None`。

生命周期:finished 条目保留 **300s TTL**,`begin()` / `query()` 时惰性清理
(遍历删除 `finished_at` 超过 300s 的条目)。不引入后台线程。

### 3.2 三个静默执行路径

| 操作 | 实现位置 | 手法 |
|------|----------|------|
| project load | `ProjectManager.load_impl_silent`(已有,`tools/project/manager.py`) | 在收集 `project_load_step` yield 消息的循环里插入 `append(umo, msg)`;入口 `begin()`,所有 return 路径 `finish()` |
| project unload | 新增 `ProjectManager.unload_impl_silent`(同文件) | MagicMock silent_event(`plain_result=lambda x: x`)迭代现有 `unload_impl` generator,逐条收集 yield |
| codegraph set | 新增 `CodegraphManager.set_project_silent`(`tools/codegraph/manager.py`) | 同法迭代现有 `set_project` generator |

三个 silent 方法统一返回 `dict`:`{ok, directory, messages, reason}`(load 沿用其现有
返回 schema 不变,仅内部加进度钩子)。

并发约束:同一 umo 同时只允许一个操作。端点层先调 `begin()`,返回 `False` 时
直接返回 `success=False, reason=ReasonCode.OPERATION_IN_PROGRESS`。

### 3.3 三个新/改端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/spcode/project-load` | POST | **已有**,仅内部加进度钩子,请求/响应协议不变 |
| `/spcode/project-unload` | POST | 新。`umo` 必传,作为 query 参数(与 project-load 的 `?umo=` 注入方式一致,由 `_wrap` 适配器转发)。响应 envelope 复用 `_make_envelope`:`data{unloaded, directory, umo, substep_messages}` |
| `/spcode/codegraph-set` | POST | 新。body: `directory`(必传)+ `umo`(必传)。响应:`data{set, directory, umo, mcp_restarted, substep_messages}` |
| `/spcode/operation-progress` | GET | 新。query: `umo`(必传)。响应:`data{operation, status, current_step, messages, started_at, finished_at, reason}`;无记录返回 `data{status: "idle"}` |

失败 reason 映射沿用 `project_load.py` 的 `_SILENT_REASON_TO_ENVELOPE` 模式:
- unload:`feature_disabled`(agentsmd/codegraph 关闭)/ `no_project_loaded`(未加载,幂等成功处理见前端)/ `operation_in_progress`
- codegraph-set:`path_unsafe` / `path_not_directory` / `git_error`(MCP 重启失败)/ `operation_in_progress`

路由注册:ROUTES 表 46 → 49,`test_webapi_end_to_end.py` 的路由集合断言与
`register_web_api.call_count` 断言同步更新。

### 3.4 不做的事(后端 YAGNI)

- 不做 SSE/WebSocket 推送;
- 不做操作队列/取消接口;
- `unload_impl` / `set_project` 的聊天命令路径保持不变(用户在聊天框手输命令行为不变)。

## 4. 前端设计(AstrBot dashboard)

### 4.1 进度轮询 composable —— 新 `useSpcodeOperationProgress.ts`

模块级单例 ref:

```typescript
{
  status: "idle" | "running" | "done" | "failed",
  operation: "project_load" | "project_unload" | "codegraph_set" | null,
  currentStep: string,
  messages: string[],
  reason: string | null,
}
```

行为:
- 任何静默 POST 发出前调用 `startPolling(umo)`;500ms 间隔轮询
  `GET spcode/operation-progress?umo=...`;
- 停止条件:进度端点报告 `done`/`failed` 时停止;若 POST 先 resolve 而进度
  尚未到终态(竞态窗口),额外做一次性终态补拉后停止;
- 终态时调用 `useSpcodeProjectStatus().refresh(umo)` 拿权威 chip 状态;
- `failed` 终态保留在 ref 中(供 chip 红态 + popover 消费),直到下一次
  `startPolling` 或会话切换清零;
- 超时:轮询最长 200s 兜底(codegraph set 的 MCP 重启 timeout 180s + 余量),
  超时按 `failed` + `reason="network_timeout"` 处理。

### 4.2 静默 API 客户端 —— 扩展 `useSpcodeProjectAutoLoad.ts`

- 抽出通用 `postEnvelope<T>(path, body)` 辅助(复用现有 envelope reshape 逻辑);
- 新增 `silentUnload(umo)` → `POST spcode/project-unload`;
- 新增 `silentCodegraphSet(umo, directory)` → `POST spcode/codegraph-set`;
- `silentLoad` 保持现有签名;三个函数都接 `useSpcodeOperationProgress` 的
  `startPolling` / 终态刷新。

### 4.3 chip 改造

**`SpcodeProjectIndicator.vue`(输入区 project chip)**:

- 新增 loading 态:`Loader2` 旋转图标 + `currentStep` 文本(截断 48 字符);
  loading 期间点击 chip **不**打开 dialog(防并发);
- 新增 failed 态:红色 `XCircle` + "加载失败"文案 + 右侧 chevron 下拉按钮,
  点开 popover 展示 `messages` 完整日志(样式复用 `SpcodeProjectStatusChip`
  的 `.spcode-chip-popover*` 类);同时 snackbar 显示失败摘要(取 messages
  末尾的 ❌ 行);failed 态下点击 chip 本体正常打开 dialog 供重试;
- 状态优先级:progress ref 的 running/failed > 现有 loaded/unloaded。

**`SpcodeCodegraphChip.vue`**:

- codegraph set 期间显示 loading 态(旋转图标 + currentStep);
- 失败 snackbar + chip 红态(不加 popover,点击 chip 重开 codegraph dialog)。

### 4.4 dialog 出口改造

**`ProjectLoadDialog.vue`**:

- `emit("submit", ...)` 的载荷从命令字符串改为结构化对象:
  ```typescript
  {
    mode: "project" | "codegraph" | "unload",
    path?: string,
    noAgentsmd?: boolean, noCodegraph?: boolean,
    create?: boolean, gitInit?: boolean, force?: boolean,
  }
  ```
- overwrite 确认逻辑保留,确认后 `force: true`(替代原 `replace` 文本标志)。

**`ChatInput.vue` 的 `handleProjectLoadSubmit`**:

- 按 `mode` 分发到 `silentLoad` / `silentUnload` / `silentCodegraphSet`;
- 调用后立即关闭 dialog,不再写 prompt、不再 emit `send`;
- umo 取当前会话的 unified_msg_origin(与 auto-load 路径同源)。

**i18n**:新增 key(chip loading/failed 文案、snackbar 摘要、progress 步骤回退文案),
`zh-CN` / `en` 双语,放在 `features/chat` 模块的 `spcodeProjectLoad` 命名空间下。

### 4.5 不做的事(前端 YAGNI)

- `ProjectView.vue` 的 `SpcodeProjectStatusChip` 不动;
- 聊天框手输 `/project load` 命令的路径不动;
- 不做进度的 toast 通知(done 态不打断用户,chip 变绿即可)。

## 5. 测试计划

### 插件(pytest)

- `_operation_progress.py` 单测:begin 并发拒绝 / append 提取 current_step /
  finish 置终态 / TTL 惰性清理 / query 返回副本;
- `POST project-unload`:未加载幂等、成功路径、feature_disabled、进度钩子被调用;
- `POST codegraph-set`:成功、path_unsafe、operation_in_progress;
- `POST project-load` 回归:响应协议不变 + 进度记录被写入;
- 路由计数 46 → 49。

### dashboard(vitest)

- `ProjectLoadDialog.spec.ts`:命令文本断言 → payload 对象断言重写;
- `useSpcodeOperationProgress` 轮询单测(fake timers + mock api);
- `SpcodeProjectIndicator` loading/failed 态渲染与点击行为测试。

## 6. 风险与备注

- **进度粒度**:进度来自 silent generator 的 yield 消息,与聊天命令路径共享同一
  流水线,步骤文案天然一致;若未来流水线改文案(⏳/🔄 前缀),`append()` 的
  current_step 提取逻辑需同步(已在模块 docstring 注明)。
- **双仓库提交**:插件与 dashboard 分别在各自仓库的 `feat-silent-ops-progress`
  分支提交;全部本地 commit,不推送、不发 PR。
- **兼容性**:`POST /spcode/project-load` 协议不变,`useSpcodeProjectAutoLoad`
  的现有调用方零改动。
