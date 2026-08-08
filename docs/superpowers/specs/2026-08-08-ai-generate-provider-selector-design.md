# AI 生成提交信息的 Provider 可选设计

- 日期: 2026-08-08
- 作者: elecvoid243
- 状态: 已批准（用户确认，2026-08-08 15:45 CST）
- 关联: `POST /spcode/btw`（v2.20，2026-07-17）

## 1. 背景与动机

Git Diff Sidebar「Git变更」子页面的提交对话框提供「AI生成」按钮，调用 `POST /spcode/btw`
生成 Conventional Commits 提交信息。当前 btw 端点固定使用会话默认 provider
（`plugin.context.get_using_provider(umo=umo)`），用户无法选择用哪个模型生成。

部分用户希望「生成提交信息」与「日常聊天」使用不同模型（如聊天用轻量模型、
生成用更智能的模型），故增加 provider 选择能力。

## 2. 目标

1. 提交对话框的「AI生成」支持选择 LLM provider（粒度：仅 provider，不含模型级选择）。
2. 选择器首项固定为「自动（跟随会话）」，代表现有行为；选择持久化到 localStorage。
3. 向后兼容：不传 provider_id 时 btw 行为与现状完全一致。

## 3. 非目标

- 不做 provider + model 两级选择（`get_models()` 异步加载，复杂度收益比低）。
- 不改动 AstrBot 核心代码（`astrbot/` 目录零改动）。
- 不新增 spcode 后端端点（provider 列表走官方 dashboard API）。

## 4. 方案（用户已确认：方案 B）

provider 列表来源为 AstrBot 官方 dashboard API（配置层），btw 端点新增可选
`provider_id` 参数。

### 4.1 关键事实（方案取舍依据）

- `POST /spcode/btw` 必须新增可选 `provider_id`，否则前端选择无效——后端
  `get_using_provider(umo)` 不会感知前端选择。
- 排除「前端切换会话 provider」方案：会污染会话默认模型，副作用不可接受。
- 官方 API `GET /api/v1/providers?capability=chat_completion` 返回配置层 provider
  列表（`{providers, model_metadata}`），经 `require_provider_scope` 认证；
  dashboard 前端 axios 自动携带 `Authorization: Bearer <token>`（`http.ts`），
  同源调用可行。
- 配置层数据可能包含 `enable=false` 或实例化失败的 provider（运行时
  `inst_map` 中不存在），选中后 btw 调用将返回 `provider_not_found`，由前端降级提示。
  - 缓解：前端拉取时传 `enabled: true` 过滤；仍无法保证运行时实例化成功，
    属可接受降级（方案 B 的已知取舍）。

## 5. 后端改动（插件仓库 F:\github\astrbot_plugin_spcode_toolkit）

### 5.1 `tools/webapi/btw.py`

Body schema 扩展（增量）:

```jsonc
{
  "prompt": "str",        // 必填（不变）
  "umo": "str|null",      // 可选（不变）
  "provider_id": "str"    // 可选（新增）；空字符串 / 缺失 = 跟随会话默认
}
```

Handler 第 2 步「获取 provider」逻辑改为：

```
provider_id_raw = body.get("provider_id")
if 是 str 且 strip() 非空:
    provider = plugin.context.get_provider_by_id(provider_id_raw.strip())
    if provider is None:
        return envelope(provider_not_found)
    if not isinstance(provider, Provider):   # TTS/STT/Embedding 等
        return envelope(provider_type_invalid)
else:
    provider = plugin.context.get_using_provider(umo=umo)   # 现有逻辑不变
if provider is None:
    return envelope(no_provider)                            # 不变
```

实现注意：

- `isinstance(provider, Provider)` 需要真实导入 `Provider` 类型
  （`from astrbot.core.provider import Provider`；当前 btw.py 仅有
  `TYPE_CHECKING` 下的 `SPCodeToolkit` 导入）。导入放在 handler 内部或
  模块级均可，需与插件现有 import 风格一致（模块级 + noqa 注释）。
- `get_provider_by_id` 返回 `Providers`（可能为 None），先判 None 再判类型。

### 5.2 `tools/webapi/_helpers.py`

`ReasonCode` 增补（btw 分区内）:

```python
# ── /spcode/btw(2026-07-17) ──
PROVIDER_NOT_FOUND = "provider_not_found"    # provider_id 指定的实例不存在
PROVIDER_TYPE_INVALID = "provider_type_invalid"  # 非 CHAT_COMPLETION 类型
```

### 5.3 `tests/test_btw.py`

新增 4 组用例（mock `plugin.context`）：

| 用例 | 输入 | 期望 |
|---|---|---|
| 有效 provider_id | body 带 `provider_id="p1"`，`get_provider_by_id` 返回 Provider mock | `text_chat` 被调用，envelope ok |
| 无效 id | body 带 `provider_id="ghost"`，返回 None | reason=`provider_not_found`，`text_chat` 不被调用 |
| 类型不符 | 返回 TTS mock（非 Provider） | reason=`provider_type_invalid` |
| 省略参数（向后兼容） | body 无 `provider_id` | 走 `get_using_provider` 现有逻辑 |

### 5.4 文档

- `README.md`：`/spcode/btw` 行的 body 说明追加 `provider_id?`。
- `metadata.yaml`：如有端点描述则同步。

## 6. 前端改动（AstrBot 仓库 dashboard）

### 6.1 `dashboard/src/composables/useSpcodeBtw.ts`

```ts
export interface BtwParams {
  prompt: string;
  umo?: string | null;
  providerId?: string | null;   // 新增；"__auto__"/空 不传
}
```

`ask()` body 构建：`...(params.providerId && params.providerId !== "__auto__" ? { provider_id: params.providerId } : {})`。

### 6.2 `dashboard/src/components/chat/message_list_comps/GitCommitDialog.vue`

**列表拉取**：

- `watch(() => props.modelValue, open => { if (open) { ...; void loadProviders(); } })`
- `loadProviders()` 调官方 sdk `listProviders({ query: { capability: "chat_completion", enabled: true } })`
  （import 自 `@/api/generated/openapi-v1`；axios 已配置认证）。
- 响应解析：`resp.data?.data?.providers`，提取 `{ id, name?, type, model, enable }`。
- 失败（异常 / 非 ok 信封 / 无数据）→ `providerOptions = []`（仅「自动」），
  静默降级，不打断对话框。

**选择器状态**：

```ts
const COMMIT_PROVIDER_KEY = "astrbot.spcode.gitDiffSidebar.commitProviderId";
const AUTO = "__auto__";
const providerId = ref<string>(loadProviderId());   // 默认 AUTO
const providerOptions = ref<{ id: string; label: string }[]>([]);
```

- `loadProviderId()`：localStorage 读取，仅接受非空字符串，否则 `AUTO`；
  catch 异常回退 `AUTO`（与 `loadMsgLang` 同款）。
- `watch(providerId, v => localStorage.setItem(...))`，catch 静默。
- 显示 label：`name || id`，附 `(model)`；例：`OpenAI (gpt-4o-mini)`、`p1 (qwen-max)`。
- 对话框关闭时不清理选择（持久化语义）。

**选择器 UI**（放在「AI生成」按钮左侧的 `commit-generate-controls` 内）：

```html
<v-select
  v-model="providerId"
  :items="[{ id: AUTO, label: tm('...auto') }, ...providerOptions]"
  item-value="id"
  item-title="label"
  density="compact"
  hide-details
  class="commit-provider-select"
/>
```

- `providerOptions.length === 0` 时不渲染选择器（仅自动，等价现状）。
- `:disabled="btw.isGenerating.value"`（生成中锁定，与语言 toggle 一致）。

**onGenerate 传参**：

```ts
const result = await btw.ask({ prompt, providerId: providerId.value });
```

**错误映射**（`generateErrorKey` 白名单追加）：

```ts
result.reason === "provider_not_found" ||
result.reason === "provider_type_invalid" → generateErrorKey = result.reason
```

`provider_not_found` 时额外提示「该 provider 可能已被移除，请切换回自动」。

### 6.3 i18n（`dashboard/src/i18n/locales/{en-US,zh-CN}/features/chat.json`）

`spcodeProjectLoad.diffSidebar.gitWorkflow.commit.dialog` 下新增：

| key | en-US | zh-CN |
|---|---|---|
| `providerLabel` | "Provider" | "生成模型" |
| `providerAuto` | "Auto (follow session)" | "自动（跟随会话）" |
| `generateError.provider_not_found` | "Selected provider was not found. Switch back to Auto." | "所选 provider 不存在，请切换回自动。" |
| `generateError.provider_type_invalid` | "Selected provider is not a chat provider." | "所选 provider 不是聊天类型。" |

### 6.4 前端测试

选择器 / 持久化逻辑为薄 UI 状态，不新增独立测试文件。若在实现中发现
需要解析层（如提取 label 的逻辑），则抽 `parseSpcodeLlmProviders.ts` 并配
vitest（仿 `parseInteractiveChoice.test.ts` 模式）。

## 7. 数据流

```
打开对话框
  → loadProviders() → GET /api/v1/providers?capability=chat_completion&enabled=true
  → 渲染 v-select（自动 + provider 列表；失败仅自动）
点 AI 生成
  → btw.ask({ prompt, providerId }) → POST /spcode/btw { prompt, umo, provider_id }
  → 后端: provider_id → get_provider_by_id + 类型校验 → text_chat
         （无 provider_id → get_using_provider 原逻辑）
  → parseCommitMessageReply → 填入 textarea
```

## 8. 错误处理与降级汇总

| 场景 | 行为 |
|---|---|
| 官方 API 拉取失败 / 超时 | 选择器仅「自动」，功能等同现状 |
| 选中 provider 已删除 / 实例化失败 | `provider_not_found`，提示切回自动 |
| 选中 provider 非聊天类型 | `provider_type_invalid` 提示 |
| 会话默认 provider 变化 | 不影响持久化选择；可手动切回「自动」 |
| localStorage 不可用（隐私模式） | 回退「自动」，不抛错 |

## 9. 测试计划

1. 后端：`test_btw.py` 4 组新用例（§5.3），`uv run pytest tests/test_btw.py`。
2. 前端：`cd dashboard && pnpm test`（如有新增解析层）或人工验收
   （两种语言切换 + 选择器持久化 + 错误路径）。
3. 回归：不传 `provider_id` 的现有用例必须全绿（向后兼容）。

## 10. 文件清单

| 仓库 | 文件 | 改动 |
|---|---|---|
| 插件 | `tools/webapi/btw.py` | provider_id 解析 + 校验 |
| 插件 | `tools/webapi/_helpers.py` | +2 ReasonCode |
| 插件 | `tests/test_btw.py` | +4 用例 |
| 插件 | `README.md` / `metadata.yaml` | 文档同步 |
| AstrBot | `dashboard/src/composables/useSpcodeBtw.ts` | BtwParams.providerId |
| AstrBot | `dashboard/src/components/chat/message_list_comps/GitCommitDialog.vue` | 选择器 + 拉取 + 传参 + 错误映射 |
| AstrBot | `dashboard/src/i18n/locales/{en-US,zh-CN}/features/chat.json` | 5 组文案 |

## 11. 风险与备注

- 方案 B 已知取舍：官方 API 为配置层数据，无法保证「列表即可用」；
  不可用 provider 以 `provider_not_found` 降级呈现，不阻塞主流程。
- `provider_id` 语义为运行时实例 ID（`inst_map` key），与官方 API 返回的
  provider 配置 `id` 字段一致（同一来源），无映射层。
- 不涉及 AstrBot 核心代码改动，无 API client 重新生成需求
  （前端调用已有 `listProviders` sdk 函数）。
