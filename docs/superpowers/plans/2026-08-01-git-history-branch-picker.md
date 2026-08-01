# Git 历史页跨分支浏览 + 操作按钮可见性规则 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 GitDiffSidebar「Git 历史」页可通过 combobox 查看任意分支/sha/tag 的历史，并让行内 revert / cherry-pick 按钮按「当前分支视图 ⇄ 非当前分支视图」互补显隐。

**Architecture:** 零后端改动。GitLogView 的 ref 过滤框由 v-text-field 升级为 v-combobox（分支列表来自既有 branchesComposable），按钮显隐由新 prop `activeRef`（激活 filter）与 `currentBranch` 比较派生。GitDiffSidebar 仅新增一个排序 computed 并传 3 个 prop。

**Tech Stack:** Vue 3 `<script setup>` + Vuetify 3 + TypeScript + Vitest (@vue/test-utils) + pnpm

**Spec:** `docs/superpowers/specs/2026-08-01-git-history-branch-picker-design.md`（commit `fa54d49`）

## Global Constraints

- 工作目录：AstrBot 主仓 `F:\github\Astrbot`，dashboard 代码在 `dashboard/`；命令一律在 `dashboard/` 下执行
- 所有修改**仅限本地提交**，禁止推送远端、禁止发起 PR
- 代码注释与日志一律用**英文**；新文件头标注 `Author: elecvoid243` + 日期
- 提交信息用 conventional commits（如 `feat(dashboard): ...`）
- **零 i18n 改动**：复用既有 key `spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.ref` / `.refPlaceholder`
- **零新 MDI 图标**：mdi-undo-variant / mdi-source-branch-plus 已在 mdi 子集中
- 已知基线失败（非本次引入，修复目标不包括它们）：`DocumentManager.spec.ts` ×3（fullscreen 状态）；主检出环境下 `SpSegmentedControl.spec.ts` ×2
- 验证命令：`pnpm test`（vitest）、`pnpm typecheck`（vue-tsc）；`pnpm lint` 无 eslint 配置（上游既存），跳过

---

### Task 0: git worktree 隔离工作区

**Files:**
- Create: worktree `F:\github\Astrbot-worktrees\history-branch-picker`

- [ ] **Step 1: 创建 worktree**

```bash
cd /d F:\github\Astrbot
git worktree add F:\github\Astrbot-worktrees\history-branch-picker -b feat/git-history-branch-picker
```

Expected: `Preparing worktree (new branch 'feat/git-history-branch-picker')`

- [ ] **Step 2: 安装 dashboard 依赖**

```bash
cd /d F:\github\Astrbot-worktrees\history-branch-picker\dashboard
pnpm install
```

Expected: 依赖安装完成（约 5 分钟，可后台执行并轮询日志）

- [ ] **Step 3: 基线验证**

```bash
pnpm test
```

Expected: 全绿，除已知基线失败（见 Global Constraints）

---

### Task 1: GitLogView — combobox + 可见性规则（TDD）

**Files:**
- Modify: `dashboard/src/components/chat/message_list_comps/GitLogView.vue`（props ~L46-88、filter bar ~L482-500、revert 按钮 ~L690、cherry-pick 按钮 ~L712）
- Test: `dashboard/src/components/chat/message_list_comps/GitLogView.branchPicker.spec.ts`（新建）

**Interfaces:**
- Consumes: `SpcodeLogSnapshot` / `SpcodeLogCommit`（`@/composables/parseSpcodeGitWorkflow:104-130`）；既有 props `state/hasMore/isLoading/gitShow/focusedCommitSha/gitStats/statsOpen/range/topFilesLimit`
- Produces（Task 2 依赖的 props 签名）:

```ts
branchItems: string[];        // combobox 下拉项（current 置顶 → local → remote）
currentBranch: string | null; // 当前分支名；detached/未加载时为 null
activeRef: string | null;     // 激活 filter 的 ref（""/HEAD/<current> = 当前分支视图）
```

- [ ] **Step 1: 写失败测试**

新建 `dashboard/src/components/chat/message_list_comps/GitLogView.branchPicker.spec.ts`：

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-history-branch-picker-design.md
// Branch-picker combobox + complementary revert/cherry-pick visibility.
//
// Mounts the real GitLogView with the heavy-stub strategy (Vuetify and
// GitStatsPanel stubbed), mirroring GitRepoInitPrompt.spec.ts /
// DocumentManager.spec.ts. The point is to assert the new props drive
// the combobox items and the per-row button visibility — not to render
// the full Vuetify tree.

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import GitLogView from "./GitLogView.vue";
import type { SpcodeLogSnapshot } from "@/composables/parseSpcodeGitWorkflow";

const vuetifyStubs = {
  "v-icon": { template: "<i />" },
  "v-text-field": {
    name: "v-text-field",
    props: ["modelValue", "label", "placeholder"],
    template: "<div />",
  },
  "v-combobox": {
    name: "v-combobox",
    props: ["modelValue", "items", "label", "placeholder", "hideNoData"],
    template: "<div />",
  },
  "v-progress-circular": { template: "<i />" },
  GitStatsPanel: { template: "<div />" },
};

function makeCommit(sha: string) {
  return {
    sha,
    shaShort: sha.slice(0, 7),
    author: { name: "alice", email: "alice@example.com" },
    committer: { name: "alice", email: "alice@example.com" },
    date: "2026-08-01T10:00:00+08:00",
    subject: `commit ${sha.slice(0, 7)}`,
    body: null,
    parents: [],
    shortstat: { files: 1, additions: 2, deletions: 3 },
  };
}

function makeSnapshot(): SpcodeLogSnapshot {
  return {
    success: true,
    reason: null,
    loaded: true,
    elapsedMs: 1,
    umo: "u",
    worktree: "w",
    directory: "d",
    ref: "HEAD",
    count: 1,
    hasMore: false,
    truncated: false,
    maxBytes: 1024,
    commits: [makeCommit("a".repeat(40))],
  };
}

function mountView(props: Record<string, unknown> = {}) {
  return mount(GitLogView as never, {
    props: {
      state: { kind: "ok", snapshot: makeSnapshot() },
      hasMore: false,
      isLoading: false,
      gitShow: {
        getState: () => ({ kind: "idle" }),
        getData: () => null,
        getFileState: () => ({ kind: "idle" }),
        fetch: () => Promise.resolve(),
        fetchFile: () => Promise.resolve(),
      },
      focusedCommitSha: null,
      gitStats: { state: { value: { kind: "idle" } }, refresh: () => {} },
      statsOpen: false,
      range: null,
      topFilesLimit: 10,
      // New props under test (spec §2):
      branchItems: ["main", "dev", "origin/dev"],
      currentBranch: "main",
      activeRef: "HEAD",
      ...props,
    },
    global: { stubs: vuetifyStubs },
  });
}

describe("GitLogView branch picker (spec 2026-08-01)", () => {
  beforeEach(() => {
    // GitLogView derives isDark from the customizer store via
    // storeToRefs — a pinia instance must be active before mount.
    setActivePinia(createPinia());
  });

  it("passes branchItems through to the ref combobox", () => {
    const w = mountView();
    const combo = w.findComponent({ name: "v-combobox" });
    expect(combo.exists()).toBe(true);
    expect(combo.props("items")).toEqual(["main", "dev", "origin/dev"]);
  });

  it("reverts to free-input combobox when branchItems is empty", () => {
    const w = mountView({ branchItems: [] });
    const combo = w.findComponent({ name: "v-combobox" });
    expect(combo.exists()).toBe(true);
    expect(combo.props("items")).toEqual([]);
    expect(combo.props("hideNoData")).toBe(true);
  });

  it("shows revert and hides cherry-pick when activeRef is HEAD", () => {
    const w = mountView({ activeRef: "HEAD" });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });

  it("treats the current branch name as the current view", () => {
    const w = mountView({ activeRef: "main", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });

  it("shows cherry-pick and hides revert when viewing another branch", () => {
    const w = mountView({ activeRef: "dev", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(false);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(true);
  });

  it("shows cherry-pick for a free-typed sha (loose rule, spec Q2)", () => {
    const w = mountView({ activeRef: "abc1234", currentBranch: "main" });
    expect(w.find(".git-log-item-revert").exists()).toBe(false);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(true);
  });

  it("treats null/empty activeRef as the current view", () => {
    const w = mountView({ activeRef: null });
    expect(w.find(".git-log-item-revert").exists()).toBe(true);
    expect(w.find(".git-log-item-cherry-pick").exists()).toBe(false);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d F:\github\Astrbot-worktrees\history-branch-picker\dashboard
pnpm test GitLogView.branchPicker
```

Expected: FAIL — 全部 7 个用例失败（`branchItems`/`currentBranch`/`activeRef` prop 尚不存在，combobox 未渲染，按钮无显隐控制）

- [ ] **Step 3: GitLogView 实现 — 新增 props**

`dashboard/src/components/chat/message_list_comps/GitLogView.vue`，在 `topFilesLimit: number;`（props 定义末尾，`}>();` 之前）追加：

```ts
  /** 2026-08-01 branch-picker (spec 2026-08-01-git-history-branch-picker
   *  §2a): items offered by the ref filter combobox — current branch
   *  first, then locals, then remotes. Empty array degrades the
   *  combobox to free input (branches not loaded / non-git dir). */
  branchItems: string[];
  /** Spec §2b: name of the checked-out branch, or null (detached HEAD /
   *  branches not loaded). Drives the revert ⇄ cherry-pick visibility
   *  split below. */
  currentBranch: string | null;
  /** Spec §2b: the APPLIED filter ref (parent passes
   *  gitLog.filter.value.ref), NOT the in-progress draft in
   *  localFilter. ""/HEAD/<currentBranch> all mean "viewing the
   *  current branch". */
  activeRef: string | null;
```

- [ ] **Step 4: GitLogView 实现 — viewingCurrent computed**

在 `const { isDark } = storeToRefs(useCustomizerStore());`（~L113）之后追加：

```ts
/** Spec 2026-08-01 §2b: complementary visibility — revert only makes
 *  sense on the current branch's own history; cherry-pick only when
 *  viewing a DIFFERENT ref (another branch, a sha, or a tag). The
 *  comparison uses the APPLIED ref (prop), so typing-but-not-applying
 *  a branch name never flips the buttons prematurely. */
const viewingCurrent = computed(() => {
  const r = props.activeRef;
  return !r || r === "HEAD" || r === props.currentBranch;
});
```

（`computed` 已在该文件 import 中，无需新增 import。）

- [ ] **Step 5: GitLogView 实现 — ref 框升级为 v-combobox**

filter bar 中 ref 的 `v-text-field`（第一个过滤字段，绑定 `localFilter.ref`，~L483-497）整体替换为：

```html
      <!-- 2026-08-01 branch-picker: v-text-field → v-combobox so the
           ref filter offers known branches (current first, then local,
           then remote) while keeping free input for sha/tag. Apply /
           Reset flow unchanged. -->
      <v-combobox
        v-model="localFilter.ref"
        :items="branchItems"
        :hide-no-data="branchItems.length === 0"
        :label="
          tm('spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.ref')
        "
        :placeholder="
          tm(
            'spcodeProjectLoad.diffSidebar.gitWorkflow.history.filter.refPlaceholder',
          )
        "
        density="compact"
        variant="outlined"
        hide-details
        class="git-log-filter-field"
      />
```

- [ ] **Step 6: GitLogView 实现 — 按钮显隐**

revert 按钮（`class="git-log-item-revert"`，~L690）的 `<button` 开标签加 `v-if`：

```html
          <button
            v-if="viewingCurrent"
            type="button"
            class="git-log-item-revert"
```

cherry-pick 按钮（`class="git-log-item-cherry-pick"`，~L712）：

```html
          <button
            v-if="!viewingCurrent"
            type="button"
            class="git-log-item-cherry-pick"
```

其余属性（title/aria/click/icon）保持不变。

- [ ] **Step 7: 运行测试确认通过**

```bash
pnpm test GitLogView.branchPicker
```

Expected: 7 个用例全部 PASS

- [ ] **Step 8: typecheck**

```bash
pnpm typecheck
```

Expected: 无错误（若报 `range: null` 类型问题，仅存在于测试文件——确认 `mount(GitLogView as never, ...)` 的 cast 已抑制 props 类型检查）

- [ ] **Step 9: 提交**

```bash
cd /d F:\github\Astrbot-worktrees\history-branch-picker
git add dashboard/src/components/chat/message_list_comps/GitLogView.vue dashboard/src/components/chat/message_list_comps/GitLogView.branchPicker.spec.ts
git commit -m "feat(dashboard): add branch picker and action visibility rules to history view"
```

---

### Task 2: GitDiffSidebar 接线

**Files:**
- Modify: `dashboard/src/components/chat/GitDiffSidebar.vue`（computed ~L437-445 之后；GitLogView 挂载 ~L4508-4529）

**Interfaces:**
- Consumes: 既有 `branchList` computed（`:437`，`SpcodeGitBranch[]`）、既有 `currentBranchName` computed（`:441`，`string | null`）、`gitLog.filter`（`Ref<LogFilter>`）
- Produces: `branchPickerItems: ComputedRef<string[]>`（Task 1 的 `branchItems` prop 数据源）

- [ ] **Step 1: 新增 branchPickerItems computed**

`currentBranchName` computed（~L441-445）之后追加：

```ts
// 2026-08-01 branch-picker (spec 2026-08-01-git-history-branch-picker
// §3): combobox items for the History view's ref filter — current
// branch first, then locals, then remotes. Reuses the existing
// branchList computed; empty while branches are not loaded (the
// combobox then degrades to free input).
const branchPickerItems = computed<string[]>(() => {
  const cur = branchList.value.filter((b) => b.current && !b.remote);
  const local = branchList.value.filter((b) => !b.current && !b.remote);
  const remote = branchList.value.filter((b) => b.remote);
  return [...cur, ...local, ...remote].map((b) => b.name);
});
```

- [ ] **Step 2: GitLogView 挂载处传 3 个 prop**

`<GitLogView` 挂载块（~L4508-4529），在 `:top-files-limit="gitStatsTopFilesLimit"` 一行之后追加：

```html
            :branch-items="branchPickerItems"
            :current-branch="currentBranchName"
            :active-ref="gitLog.filter.value.ref"
```

注意 `:active-ref` 必须写 `.value`：`gitLog` 是 composable 返回的普通对象，其 `filter` 是嵌套 ref，模板自动解包只作用于顶层绑定（与既有 `branchesComposable.state.value.kind` 用法一致，见 `:3862`）。

- [ ] **Step 3: 全量测试 + typecheck**

```bash
cd /d F:\github\Astrbot-worktrees\history-branch-picker\dashboard
pnpm test
pnpm typecheck
```

Expected: 全绿（除已知基线失败）；typecheck 无错误

- [ ] **Step 4: 提交**

```bash
cd /d F:\github\Astrbot-worktrees\history-branch-picker
git add dashboard/src/components/chat/GitDiffSidebar.vue
git commit -m "feat(dashboard): wire branch picker items and active ref into history view"
```

---

### Task 3: 全量验证 + 手动冒烟清单

**Files:** 无新增改动

- [ ] **Step 1: 全量 vitest**

```bash
cd /d F:\github\Astrbot-worktrees\history-branch-picker\dashboard
pnpm test
```

Expected: `Tests` 行失败数不超过基线（`DocumentManager` ×3；worktree 环境下无 `SpSegmentedControl` 失败）

- [ ] **Step 2: typecheck**

```bash
pnpm typecheck
```

Expected: 无错误

- [ ] **Step 3: 生产构建（模板编译 + mdi 子集完整性最终确认）**

```bash
pnpm build
```

Expected: `✓ built in ...`，无错误

- [ ] **Step 4: 手动冒烟清单（需运行中的 AstrBot 实例，用户在本地验证）**

| # | 操作 | 预期 |
|---|------|------|
| 1 | 打开 Git 历史页，点击 ref 过滤框 | 下拉列出分支：当前分支置顶，其后本地分支、远程分支 |
| 2 | 选择另一本地分支 → 应用 | 历史列表刷新为该分支历史；行内只显示 cherry-pick 按钮 |
| 3 | 对某 commit 点 cherry-pick | 对话框预填该 sha；确认后落入当前分支，snackbar 成功提示 |
| 4 | 重置过滤器 | 回到 HEAD 视图；行内只显示 revert 按钮 |
| 5 | ref 框手输一个 sha → 应用 | 显示该 sha 可达历史；行内只显示 cherry-pick |
| 6 | 手输不存在的 ref → 应用 | 现有 error banner 显示 git_error |
| 7 | 查看其他分支时点工具栏 cherry-pick 按钮 | blank 对话框正常打开（不受显隐规则影响） |
| 8 | 切换当前分支后回历史页 | combobox 置顶项随之更新；HEAD 视图按钮显隐正确 |

- [ ] **Step 5: 收尾（finishing-a-development-branch）**

合并回 `all` → 主检出跑 `pnpm test` 验证 → 删除 worktree（`git worktree remove --force` + `git worktree prune`）→ 删除临时分支。禁止推送远端。

---

## Self-Review 记录

- **Spec 覆盖**：§2a combobox → Task 1 Step 5；§2b 可见性 → Task 1 Step 3/4/6；§3 接线 → Task 2；§5 测试 → Task 1 Step 1（7 用例 ≥ spec 要求）；§4 边界中「工具栏 blank 按钮不受影响」由冒烟 #7 覆盖。✅
- **Placeholder 扫描**：无 TBD/TODO；所有代码块完整。✅
- **类型一致性**：`branchItems`/`currentBranch`/`activeRef` 在 Task 1 测试、Task 1 props、Task 2 传参三处签名一致；`SpcodeGitBranch.name/current/remote` 与 `parseSpcodeGitBranches.ts:35-42` 一致；测试用 `.git-log-item-revert` / `.git-log-item-cherry-pick` class 与模板一致。✅
