# AstrBot Git 提交历史编辑功能 前端设计

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后，如需落地，可调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已分段通过（用户确认日期 2026-08-13，3 段全部确认：§2 组件与交互 → §3 数据流与刷新 → §4 文件拆分与验证）
**作者**: elecvoid243 @ 2026-08-13 11:30 CST
**目标仓库**: `F:\github\Astrbot`（`dashboard/` 前端）
**后端依赖**: spcode 插件 `POST /spcode/git-commit-amend`
**后端 spec**: `docs/superpowers/specs/2026-08-13-git-commit-amend-design.md`

---

## 用户决策记录（3 轮澄清）

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: 可编辑提交范围** | **A** — 仅 HEAD 提交 | 只在当前分支历史顶部行显示编辑入口 |
| **Q2: 编辑交互形式** | **A** — 模态对话框 | 新增 `GitCommitAmendDialog`，镜像 revert/squash 对话框 |
| **Q3: 数据流架构** | **A** — 新 composable + 独立 dialog | 新增 `useSpcodeGitCommitAmend`，不复用 `useSpcodeGitCommit` |

---

## §1 背景与目标

`GitLogView.vue` 已有 per-commit 的 revert / cherry-pick 操作，也有 squash / changelog 等历史操作。当前缺少“修改最近一次提交信息”的能力。

spcode 后端已提供：

```text
POST /spcode/git-commit-amend
body: { message }
```

该端点只修改当前 HEAD 提交信息，并在以下情况拒绝：空仓库、merge commit、staged 改动、冲突操作中。

前端设计目标：在 `GitDiffSidebar` 的 Git 历史页面中，为当前分支 HEAD 提交提供编辑信息入口，交互与现有写操作保持一致。

### 范围外

- 编辑非 HEAD 历史提交（需要 rebase，超出后端能力）
- 修改 author / committer
- 修改提交内容或 staged 改动
- 行内编辑或侧边抽屉

---

## §2 组件与交互

### 2.1 入口与可见性

在 `GitLogView.vue` 的提交行 hover 操作区新增「编辑信息」按钮（图标 `mdi-pencil-outline`）。

仅当以下条件同时满足时渲染：

1. `viewingCurrent === true`（正在查看当前分支）。
2. 该提交是列表第一条（`commits[0].sha === c.sha`），即 HEAD。

点击后：

```ts
emit("amend", { sha: c.sha, subject: c.subject, body: c.body });
```

### 2.2 新增组件 `GitCommitAmendDialog.vue`

Props：

| 字段 | 类型 | 说明 |
|------|------|------|
| `modelValue` | boolean | v-model 打开状态 |
| `commit` | `{ sha, subject, body } \| null` | 被编辑的 HEAD 提交 |
| `loading` | boolean | amend 进行中 |

表单：

- 单个多行 `v-textarea`，label 为“提交信息”。
- 打开时预填 `subject + "\n\n" + body`；`body` 为空则不追加空段。
- 必填、长度 ≤ 8192；空或超长禁用提交并提示。
- 提交按钮触发 `emit("submit", { sha, message })`。

### 2.3 `GitLogView.vue` 事件扩展

```ts
const emit = defineEmits<{
  // ...existing events...
  (e: "amend", commit: { sha: string; subject: string; body: string | null }): void;
}>();
```

### 2.4 `GitDiffSidebar.vue` 职责

- 新增状态：

```ts
const amendDialogOpen = ref(false);
const pendingAmend = ref<{ sha: string; subject: string; body: string | null } | null>(null);
```

- 监听：

```vue
@amend="onLogAmendRequest"
```

- `onLogAmendRequest` 填入 `pendingAmend` 并打开对话框。
- 对话框提交时调用 `useSpcodeGitCommitAmend.amend(...)`。

### 2.5 关键约定

- 不在 `GitLogView` 内直接发请求，保持“历史视图只 emit，写操作归 sidebar”的既有模式。
- 后端返回 `staged_changes_present` / `cannot_amend_merge_commit` / `operation_in_progress` 时，前端用 toast 解释，而不是在按钮层提前隐藏。

---

## §3 数据流、错误映射与刷新

### 3.1 新增 composable

`dashboard/src/composables/useSpcodeGitCommitAmend.ts`

```ts
export interface AmendParams {
  message: string;
  worktree?: string | null;
  umo?: string | null;
}

export type AmendResult =
  | { ok: true; snapshot: SpcodeAmendSnapshot }
  | { ok: false; reason: string; stderr?: string };

export interface UseSpcodeGitCommitAmend {
  isAmending: Ref<boolean>;
  amend: (params: AmendParams) => Promise<AmendResult>;
  dispose: () => void;
}
```

实现镜像 `useSpcodeGitCommit`：

- `pluginExtensionApi.post("spcode/git-commit-amend", ...)`。
- 单个 `isAmending` 布尔（一次只允许一个 amend）。
- AbortController + `isMounted` 生命周期；`CanceledError` / network 错误处理与现有 composable 一致。

### 3.2 解析器扩展

`dashboard/src/composables/parseSpcodeGitWorkflow.ts` 新增：

```ts
export interface SpcodeAmendSnapshot {
  success: boolean;
  amended: boolean;
  before_sha?: string;
  after_sha?: string;
  subject?: string;
  message?: string;
  reason?: string | null;
  stderr?: string;
}

export function parseSpcodeGitCommitAmend(
  data: unknown,
): ParseResult<SpcodeAmendSnapshot>;
```

### 3.3 错误 ReasonCode → i18n

新增 `AMEND_REASON_I18N_KEYS`，镜像现有 `REVERT_REASON_I18N_KEYS` / `SQUASH_REASON_I18N_KEYS`：

| reason | i18n key | 样式 |
|---|---|---|
| `staged_changes_present` | `diffSidebar.amend.error.staged_changes_present` | warning |
| `cannot_amend_merge_commit` | `diffSidebar.amend.error.cannot_amend_merge_commit` | warning |
| `operation_in_progress` | `diffSidebar.amend.error.operation_in_progress` | warning |
| `empty_repository` | `diffSidebar.amend.error.empty_repository` | error |
| `invalid_message` | `diffSidebar.amend.error.invalid_message` | error |
| `hook_rejected` | `diffSidebar.amend.error.hook_rejected` | warning + stderr |
| `identity_not_set` | `diffSidebar.amend.error.identity_not_set` | error |
| `amend_failed` | `diffSidebar.amend.error.amend_failed` | error + stderr |
| `network` | `diffSidebar.amend.error.network` | error |
| `unknown` | `diffSidebar.amend.error.unknown` | error |

### 3.4 成功后刷新

amend 会改变 HEAD SHA，需要：

1. `gitLog.invalidateEtag()` + `gitLog.refresh()`。
2. `gitStatus.refresh()`（工作区状态，若有实例）。
3. `gitStats.refresh()`（统计面板，若有实例）。
4. 关闭对话框并显示成功 toast（含新 SHA 前 7 位）。

避免直接改本地 snapshot，防止 ETag 缓存与 HEAD 不一致。

---

## §4 文件拆分、i18n 与验证

### 4.1 新增文件

- `dashboard/src/composables/useSpcodeGitCommitAmend.ts`
- `dashboard/src/components/chat/message_list_comps/GitCommitAmendDialog.vue`

### 4.2 修改文件

- `dashboard/src/components/chat/message_list_comps/GitLogView.vue`
  - 新增 `amend` emit、HEAD 行「编辑信息」按钮、`viewingCurrent && isHead` 可见性判断。
- `dashboard/src/components/chat/GitDiffSidebar.vue`
  - 实例化 composable、持有 dialog 状态、监听 `@amend`、调用 amend 与刷新。
- `dashboard/src/composables/parseSpcodeGitWorkflow.ts`
  - 新增 `SpcodeAmendSnapshot` 与 `parseSpcodeGitCommitAmend`。
- `dashboard/src/i18n/...`（features/chat 语言文件）
  - 新增 `diffSidebar.amend.*` 成功/错误/表单文案。

### 4.3 测试

前端现有测试风格以 composable/解析器单测为主：

- `parseSpcodeGitCommitAmend`：成功/失败/缺字段/reason 透传。
- `useSpcodeGitCommitAmend`：mock `pluginExtensionApi.post`，覆盖成功、reason 失败、network、abort。
- 组件交互可用手动验收；若项目已有组件测试框架则补充 dialog 的预填与校验用例。

### 4.4 验收标准

- [ ] 只有当前分支的 HEAD 提交显示「编辑信息」。
- [ ] 对话框预填 subject + body，可修改多行 message。
- [ ] 提交后调用 `/spcode/git-commit-amend`，成功后 toast + 刷新历史/状态/统计。
- [ ] `staged_changes_present` / `cannot_amend_merge_commit` / `operation_in_progress` 等 reason 均有对应提示。
- [ ] amend 期间按钮 loading，禁止重复提交。
- [ ] 组件卸载时 dispose，AbortController 正确取消。

### 4.5 交付文档

- `docs/superpowers/specs/2026-08-13-git-commit-amend-frontend-design.md`

本任务不修改 spcode 后端。若需要实现，可再输出对应 plan。
