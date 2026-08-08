# AI 生成 Provider 可选 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Git Diff Sidebar 提交对话框的「AI生成」支持选择 LLM provider（含「自动（跟随会话）」默认项）。

**Architecture:** 后端 `POST /spcode/btw` 新增可选 `provider_id`（空/缺失 = 现有默认逻辑，向后兼容）；前端 `GitCommitDialog` 打开时经官方 `GET /api/v1/providers?capability=chat_completion&enabled=true` 拉取 provider 列表，渲染 v-select 选择器（首项「自动」），选择持久化 localStorage，生成时透传 `provider_id`。

**Tech Stack:** Python 3.10+ / pytest (asyncio) · Vue 3 `<script setup lang="ts">` / Vuetify 3 / vue-tsc · i18n JSON。

**涉及两个仓库：**
- 后端 + 文档：`F:\github\astrbot_plugin_spcode_toolkit`（分支 `main`）
- 前端：`F:\github\Astrbot\dashboard`（分支 `all`）

## Global Constraints

- 所有注释与日志使用英文（遵循 AstrBot AGENTS.md）；插件侧沿用既有中文 docstring 惯例不强制翻新。
- 提交信息使用 Conventional Commits（`feat:` / `docs:` 等）。
- 后端改动不得破坏现有测试：`cd F:\github\astrbot_plugin_spcode_toolkit && uv run pytest tests/test_btw.py -v` 全绿。
- 前端验证：`cd F:\github\AstrBot\dashboard && pnpm typecheck` 零错误。
- 不修改 AstrBot 核心代码（`astrbot/` 目录零改动）；不新增 spcode 后端端点。
- 向后兼容硬约束：不传 `provider_id` 时 btw 行为与现状完全一致。
- 参考规格：`F:\github\astrbot_plugin_spcode_toolkit\docs\superpowers\specs\2026-08-08-ai-generate-provider-selector-design.md`

---

### Task 1: 后端 — btw 端点 provider_id 支持（TDD）

**Files:**
- Modify: `F:\github\astrbot_plugin_spcode_toolkit\tools\webapi\_helpers.py`（ReasonCode 增补）
- Modify: `F:\github\astrbot_plugin_spcode_toolkit\tools\webapi\btw.py`（provider 解析逻辑）
- Test: `F:\github\astrbot_plugin_spcode_toolkit\tests\test_btw.py`（追加 4 用例）

**Interfaces:**
- Consumes: `plugin.context.get_provider_by_id(provider_id: str) -> Providers | None`（AstrBot 核心 API，`inst_map` 查找）
- Produces: `ReasonCode.PROVIDER_NOT_FOUND = "provider_not_found"`、`ReasonCode.PROVIDER_TYPE_INVALID = "provider_type_invalid"`；btw body 新增可选 `provider_id: str`

- [ ] **Step 1: 写失败测试（追加到 test_btw.py 类内末尾）**

在 `TestBtwEndpoint` 类内追加以下 4 个方法（文件末尾 `test_with_umo_history_parse_failure_fallback` 之后）：

```python
    @pytest.mark.asyncio
    async def test_provider_id_valid(self, mock_plugin):
        """provider_id 有效 -> 使用指定 provider,不触碰默认 provider"""
        from tools.webapi.btw import handle
        from astrbot.core.provider import Provider

        mock_provider = MagicMock(spec=Provider)
        mock_provider.text_chat = AsyncMock(
            return_value=MagicMock(completion_text="指定模型回答", tools_call_args=None)
        )
        mock_plugin.context.get_provider_by_id = MagicMock(return_value=mock_provider)

        resp = await handle(
            mock_plugin,
            body={"prompt": "生成提交信息", "provider_id": "p1"},
        )
        assert resp["data"]["reason"] is None
        assert resp["data"]["reply"] == "指定模型回答"
        mock_plugin.context.get_provider_by_id.assert_called_once_with("p1")
        mock_plugin.context.get_using_provider.assert_not_called()
        mock_provider.text_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_id_not_found(self, mock_plugin):
        """provider_id 不存在 -> provider_not_found"""
        from tools.webapi.btw import handle

        mock_plugin.context.get_provider_by_id = MagicMock(return_value=None)

        resp = await handle(
            mock_plugin,
            body={"prompt": "生成提交信息", "provider_id": "ghost"},
        )
        assert resp["data"]["reason"] == "provider_not_found"
        assert "reply" not in resp["data"]
        mock_plugin.context.get_using_provider.assert_not_called()

    @pytest.mark.asyncio
    async def test_provider_id_type_invalid(self, mock_plugin):
        """provider_id 指向非聊天类型 provider -> provider_type_invalid"""
        from tools.webapi.btw import handle

        # 普通 MagicMock 不是 Provider 实例,模拟 TTS/STT 等非聊天 provider
        mock_plugin.context.get_provider_by_id = MagicMock(return_value=MagicMock())

        resp = await handle(
            mock_plugin,
            body={"prompt": "生成提交信息", "provider_id": "tts1"},
        )
        assert resp["data"]["reason"] == "provider_type_invalid"

    @pytest.mark.asyncio
    async def test_provider_id_blank_falls_back(self, mock_plugin):
        """provider_id 为空白串 -> 走默认 provider(向后兼容)"""
        from tools.webapi.btw import handle

        mock_plugin.context.get_provider_by_id = MagicMock()
        mock_plugin.context.get_using_provider.return_value = self._mock_provider(
            "默认回答"
        )

        resp = await handle(
            mock_plugin,
            body={"prompt": "生成提交信息", "provider_id": "   "},
        )
        assert resp["data"]["reply"] == "默认回答"
        mock_plugin.context.get_provider_by_id.assert_not_called()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
uv run pytest tests/test_btw.py -v -k "provider_id"
```

Expected: 4 个 `provider_id*` 用例 FAIL（`attributeerror` 或断言失败——`get_provider_by_id` 尚未被 handler 调用、ReasonCode 不存在）；其余用例 PASS 或含失败均为预期（失败原因须与新增字段相关）。

- [ ] **Step 3: 实现 `_helpers.py` — 追加 ReasonCode**

在 `tools/webapi/_helpers.py` 的 `/spcode/btw(2026-07-17)` 分区（`NO_PROVIDER` 等三行附近）追加：

```python
    PROVIDER_NOT_FOUND = "provider_not_found"  # btw: provider_id 指定实例不存在
    PROVIDER_TYPE_INVALID = "provider_type_invalid"  # btw: provider_id 非聊天类型
```

- [ ] **Step 4: 实现 `btw.py` — provider 解析逻辑**

修改 `tools/webapi/btw.py`：

1. 模块级导入追加（放在现有 `from ._helpers import ...` 之后）：

```python
from astrbot.core.provider import Provider  # noqa: E402
```

2. 将 handler 中「── 2. 获取 provider ──」整段（现有 `provider = plugin.context.get_using_provider(umo=umo)` 及其后的 None 检查）替换为：

```python
    # ── 2. 获取 provider ──
    # 可选 provider_id: 指定运行时 provider 实例;空白/缺失则跟随会话默认。
    provider_id_raw = body.get("provider_id")
    if isinstance(provider_id_raw, str) and provider_id_raw.strip():
        provider = plugin.context.get_provider_by_id(provider_id_raw.strip())
        if provider is None:
            logger.warning(
                "[btw] provider_id=%r not found in inst_map, returning "
                "provider_not_found",
                provider_id_raw.strip(),
            )
            return _make_envelope(
                success=False,
                reason=ReasonCode.PROVIDER_NOT_FOUND,
                elapsed_ms=_elapsed(),
            )
        if not isinstance(provider, Provider):
            logger.warning(
                "[btw] provider_id=%r is not a chat-completion provider "
                "(type=%s), returning provider_type_invalid",
                provider_id_raw.strip(),
                type(provider).__name__,
            )
            return _make_envelope(
                success=False,
                reason=ReasonCode.PROVIDER_TYPE_INVALID,
                elapsed_ms=_elapsed(),
            )
    else:
        provider = plugin.context.get_using_provider(umo=umo)
    if provider is None:
        logger.warning(
            "[btw] no LLM provider available (umo=%r), returning no_provider",
            umo,
        )
        return _make_envelope(
            success=False,
            reason=ReasonCode.NO_PROVIDER,
            elapsed_ms=_elapsed(),
        )
```

同时更新 handler docstring 的 Body schema 注释，追加 `"provider_id": str | None  # 可选, 指定 provider 实例 ID`。

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run pytest tests/test_btw.py -v
```

Expected: 全部用例 PASS（原有 11 个 + 新增 4 个 = 15 个）。

- [ ] **Step 6: ruff 检查**

```bash
uv run ruff check tools/webapi/btw.py tools/webapi/_helpers.py tests/test_btw.py
```

Expected: `All checks passed!`（或先 `uv run ruff check . --fix` 自动修）

- [ ] **Step 7: Commit**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/_helpers.py tools/webapi/btw.py tests/test_btw.py
git commit -m "feat(webapi): support provider_id in POST /spcode/btw"
```

---

### Task 2: 后端 — README / metadata 文档同步

**Files:**
- Modify: `F:\github\astrbot_plugin_spcode_toolkit\README.md`（`/spcode/btw` 行）
- Modify: `F:\github\astrbot_plugin_spcode_toolkit\metadata.yaml`（如有 btw 端点描述）

**Interfaces:**
- Consumes: Task 1 产出的 `provider_id` 参数语义

- [ ] **Step 1: 更新 README 端点表**

定位 README 中 `/spcode/btw` 行（约 L493，形如 `| `/spcode/btw` | POST | 一次性独立 LLM 请求... | body: `{prompt, umo?}` |`），将 body 说明改为：

```
body: `{prompt, umo?, provider_id?}`  # provider_id 可选,指定 LLM provider 实例
```

- [ ] **Step 2: 检查 metadata.yaml**

grep `btw` 定位描述行；若有 body 说明则同步追加 `provider_id?`；无则跳过（零改动）。

- [ ] **Step 3: Commit**

```bash
git add README.md metadata.yaml
git commit -m "docs: document provider_id param of /spcode/btw"
```

---

### Task 3: 前端 — useSpcodeBtw 透传 providerId

**Files:**
- Modify: `F:\github\AstrBot\dashboard\src\composables\useSpcodeBtw.ts`

**Interfaces:**
- Consumes: 无
- Produces: `BtwParams.providerId?: string | null`；`ask()` body 在 providerId 非空且非 `"__auto__"` 时带 `provider_id`

- [ ] **Step 1: 修改 BtwParams 接口**

`useSpcodeBtw.ts` 的 `BtwParams` 接口（约 L18-26）追加字段：

```ts
export interface BtwParams {
  prompt: string;
  umo?: string | null;
  /** Provider instance id; "__auto__" / empty → backend default provider. */
  providerId?: string | null;
}
```

- [ ] **Step 2: 修改 ask() body 构建**

`ask()` 内 `pluginExtensionApi.post` 的 body 对象（现为 `{ prompt, ...(params.umo ? { umo: params.umo } : {}) }`）追加一行：

```ts
      {
        prompt: params.prompt,
        ...(params.umo ? { umo: params.umo } : {}),
        ...(params.providerId && params.providerId !== "__auto__"
          ? { provider_id: params.providerId }
          : {}),
      },
```

- [ ] **Step 3: 类型检查**

```bash
cd F:\github\AstrBot\dashboard
pnpm typecheck
```

Expected: 零错误。

- [ ] **Step 4: Commit**

```bash
git add src/composables/useSpcodeBtw.ts
git commit -m "feat(chat): support providerId passthrough in useSpcodeBtw"
```

---

### Task 4: 前端 — i18n 文案

**Files:**
- Modify: `F:\github\AstrBot\dashboard\src\i18n\locales\zh-CN\features\chat.json`
- Modify: `F:\github\AstrBot\dashboard\src\i18n\locales\en-US\features\chat.json`

**Interfaces:**
- Consumes: 无
- Produces: `spcodeProjectLoad.diffSidebar.gitWorkflow.commit.dialog.providerLabel / providerAuto / generateError.provider_not_found / generateError.provider_type_invalid`

- [ ] **Step 1: 更新 zh-CN**

`zh-CN/features/chat.json` 的 `spcodeProjectLoad.diffSidebar.gitWorkflow.commit.dialog` 对象（约 L965-990）中：
- 在 `"langToggleAria"` 之后、`"generating"` 之前插入：

```json
            "providerLabel": "生成模型",
            "providerAuto": "自动（跟随会话）",
```

- 在 `"generateError"` 对象内追加：

```json
              "provider_not_found": "所选 provider 不存在，请切换回自动",
              "provider_type_invalid": "所选 provider 不是聊天类型，请切换回自动"
```

- [ ] **Step 2: 更新 en-US**

`en-US/features/chat.json` 对应位置：

```json
            "providerLabel": "Provider",
            "providerAuto": "Auto (follow session)",
```

```json
              "provider_not_found": "Selected provider was not found. Switch back to Auto.",
              "provider_type_invalid": "Selected provider is not a chat provider. Switch back to Auto."
```

- [ ] **Step 3: 校验 JSON**

```bash
python -c "import json; json.load(open('src/i18n/locales/zh-CN/features/chat.json', encoding='utf-8')); json.load(open('src/i18n/locales/en-US/features/chat.json', encoding='utf-8')); print('JSON OK')"
```

Expected: `JSON OK`

- [ ] **Step 4: Commit**

```bash
git add src/i18n/locales/zh-CN/features/chat.json src/i18n/locales/en-US/features/chat.json
git commit -m "feat(chat): add provider selector i18n strings"
```

---

### Task 5: 前端 — GitCommitDialog provider 选择器

**Files:**
- Modify: `F:\github\AstrBot\dashboard\src\components\chat\message_list_comps\GitCommitDialog.vue`

**Interfaces:**
- Consumes: Task 3 的 `btw.ask({ prompt, providerId })`；Task 4 的 i18n keys；官方 sdk `listProviders`（`@/api/generated/openapi-v1`，已配置认证）
- Produces: 对话框内「自动 + provider」v-select；`onGenerate` 传入 `providerId`

- [ ] **Step 1: script 部分 — 导入 + 状态**

在 `GitCommitDialog.vue` 的 `<script setup>` 中：

1. 追加 import（`useSpcodeBtw` import 行之后）：

```ts
import { listProviders } from "@/api/generated/openapi-v1";
```

2. 在 `const msgLanguage = ref<MsgLang>(loadMsgLang());` 与 `watch(msgLanguage, ...)` 之后追加：

```ts
// ── Provider selection (2026-08-08) ─────────────────────────────
// Persisted like commitMsgLang: safeGet/safeSet localStorage, invalid
// values fall back to AUTO. "__auto__" keeps the pre-existing
// "follow the session default provider" behaviour.
const COMMIT_PROVIDER_KEY = "astrbot.spcode.gitDiffSidebar.commitProviderId";
const AUTO_PROVIDER = "__auto__";

function loadProviderId(): string {
  try {
    const v = localStorage.getItem(COMMIT_PROVIDER_KEY);
    if (v && v !== AUTO_PROVIDER) return v;
  } catch {
    /* localStorage unavailable — fall through to AUTO */
  }
  return AUTO_PROVIDER;
}

const providerId = ref<string>(loadProviderId());
watch(providerId, (v) => {
  try {
    localStorage.setItem(COMMIT_PROVIDER_KEY, v);
  } catch {
    /* no-op */
  }
});

interface ProviderOption {
  id: string;
  label: string;
}

const providerOptions = ref<ProviderOption[]>([]);

async function loadProviders(): Promise<void> {
  try {
    const resp = await listProviders({
      query: { capability: "chat_completion", enabled: true },
    });
    const providers = resp.data?.data?.providers;
    if (!Array.isArray(providers)) {
      providerOptions.value = [];
      return;
    }
    providerOptions.value = providers
      .filter((p) => p && typeof p.id === "string")
      .map((p) => ({
        id: p.id,
        label: `${p.name || p.id}${typeof p.model === "string" && p.model ? ` (${p.model})` : ""}`,
      }));
  } catch {
    // API failure degrades to "Auto" only; existing behaviour unchanged.
    providerOptions.value = [];
  }
}
```

3. 在打开对话框的 `watch(() => props.modelValue, (open) => { if (open) { ... } })` 回调内（`message.value = ""` 之后）追加：

```ts
      void loadProviders();
```

- [ ] **Step 2: script 部分 — onGenerate 传参 + 错误映射**

`onGenerate()` 中 `btw.ask({ prompt })` 调用改为：

```ts
  const result = await btw.ask({ prompt, providerId: providerId.value });
```

`onGenerate()` 的错误分支（`generateErrorKey.value = result.reason === ... ? result.reason : "unknown"`）追加两个 reason：

```ts
  generateErrorKey.value =
    result.reason === "no_provider" ||
    result.reason === "empty_response" ||
    result.reason === "llm_error" ||
    result.reason === "network" ||
    result.reason === "provider_not_found" ||
    result.reason === "provider_type_invalid"
      ? result.reason
      : "unknown";
```

- [ ] **Step 3: template — 选择器 UI**

在 template 的 `commit-generate-controls` div 内（`v-btn-toggle` 之前）插入：

```html
            <v-select
              v-if="providerOptions.length > 0"
              v-model="providerId"
              :items="[{ id: AUTO_PROVIDER, label: tm('spcodeProjectLoad.diffSidebar.gitWorkflow.commit.dialog.providerAuto') }, ...providerOptions]"
              item-value="id"
              item-title="label"
              :label="tm('spcodeProjectLoad.diffSidebar.gitWorkflow.commit.dialog.providerLabel')"
              density="compact"
              hide-details
              variant="outlined"
              class="commit-provider-select"
              :disabled="btw.isGenerating.value"
            />
```

- [ ] **Step 4: style — 选择器宽度**

在 `<style scoped>` 的 `commit-generate-controls` 规则后追加：

```css
.commit-provider-select {
  max-width: 200px;
  min-width: 140px;
}
```

- [ ] **Step 5: 类型检查**

```bash
cd F:\github\AstrBot\dashboard
pnpm typecheck
```

Expected: 零错误。

- [ ] **Step 6: 现有 vitest 回归**

```bash
pnpm test
```

Expected: 全绿（本次无新增前端测试文件，仅为回归）。

- [ ] **Step 7: Commit**

```bash
git add src/components/chat/message_list_comps/GitCommitDialog.vue
git commit -m "feat(chat): add provider selector to commit dialog AI generation"
```

---

### Task 6: 全量验证 + 计划收尾

**Files:** 无新增

- [ ] **Step 1: 后端全量测试**

```bash
cd F:\github\astrbot_plugin_spcode_toolkit
uv run pytest tests/ -q
```

Expected: 全部 PASS（含既有 75+ 文件，不得引入回归）。

- [ ] **Step 2: ruff 全量**

```bash
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 3: 前端 typecheck + 手动验收清单**

```bash
cd F:\github\AstrBot\dashboard
pnpm typecheck
```

手动验收（`pnpm dev` 后操作 Git Diff Sidebar → 提交 → AI 生成区域）：
- [ ] 选择器出现在 AI 生成按钮左侧，首项「自动（跟随会话）」
- [ ] 无 provider 配置时选择器不渲染（仅自动，等同旧版）
- [ ] 选择某 provider 后点 AI 生成，提交信息由该 provider 生成（对比日志/回复差异）
- [ ] 重启对话框后选择保持（localStorage 持久化）
- [ ] 删除所选 provider 后生成 → 显示「所选 provider 不存在」错误
- [ ] 语言切换 zh/en 与 provider 选择互不影响

- [ ] **Step 4: 收尾提交（如手动验收发现问题则修复并追加 commit）**

```bash
git -C F:\github\AstrBot status --short
git -C F:\github\astrbot_plugin_spcode_toolkit status --short
```

Expected: 两仓库均无未提交改动（验收修复另计）。

---

## Self-Review 记录

- **Spec 覆盖**：§5.1 btw provider_id → Task 1；§5.2 ReasonCode → Task 1 Step 3；§5.3 4 组测试 → Task 1 Step 1；§5.4 README/metadata → Task 2；§6.1 useSpcodeBtw → Task 3；§6.2 选择器/拉取/持久化/错误映射 → Task 5；§6.3 i18n → Task 4；§6.4 前端测试（薄层不新增）→ Task 5 Step 6 回归 + Task 6 Step 3 手动验收；§7 数据流 → 各任务接口衔接；§8 错误降级 → Task 5（API 失败降级 AUTO、provider_not_found 映射）；§9 测试计划 → Task 1/6。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`AUTO_PROVIDER`（Task 5）与 `useSpcodeBtw` 的 `"__auto__"` 判断（Task 3 Step 2）字面量一致；`BtwParams.providerId`（Task 3）与 `btw.ask({ prompt, providerId })`（Task 5）一致；ReasonCode 字面量 `provider_not_found` / `provider_type_invalid` 在后端（Task 1）与前端 i18n key / 错误映射（Task 4/5）一致。
- **测试陷阱已规避**：`isinstance(provider, Provider)` 在测试中通过 `MagicMock(spec=Provider)` 通过（Task 1 Step 1 注释已说明）；`provider_type_invalid` 用例用裸 `MagicMock()`（非 Provider 实例）。
