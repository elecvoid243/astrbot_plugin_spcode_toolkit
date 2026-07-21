<!--
Author: elecvoid243 @ 2026-07-21
Spec: Git Diff Sidebar — Branch Switcher Frontend
Status: Draft (awaiting user review)
-->

# Git Diff Sidebar — Branch Switcher Frontend

> Add a branch switcher to the Git Diff Sidebar so users can list, switch,
> create and delete git branches without leaving the ChatUI. Backend
> endpoints (`/spcode/git-branches`, `/spcode/git-branch-create`,
> `/spcode/git-branch-delete`, `/spcode/git-branch-switch`) already
> exist (v2.17.0 PR-C/D/E/F, 2026-07-15); this spec is the frontend
> wiring only.

---

## 1. Context & Goals

### 1.1 Context

`GitDiffSidebar.vue` (in `dashboard/src/components/chat/`) currently
exposes a "Worktrees" tab switcher (paths) and a workflow panel (stage /
commit / log). The project's branch state is only visible indirectly:
each worktree tab shows its `wt.branch` label, and `git-status` shows
the *current* branch name. But there is **no way to switch branches
from the sidebar** — the only path today is `git checkout`/`git switch`
in a terminal.

This spec closes that gap. It mirrors the existing worktree-management
flow (composable + dropdown + dialogs) and uses the four branch
endpoints that already shipped in v2.17.0.

### 1.2 Goals

- Show a small button next to the "Worktrees" label that opens a
  dropdown listing every local + remote branch.
- Allow switching to a non-current local branch with a confirmation
  dialog.
- Allow deleting a non-current branch (with `×` on each row) via
  confirmation.
- Allow creating a new branch with an inline form inside the dropdown.
- Poll the branch list on a 30-second cadence (matching the worktree
  polling cadence) so the dropdown reflects external `git branch`
  invocations (e.g. from the agent).
- After a successful switch, refresh the worktree list and the
  currently visible view (diff / status / log) so the UI stays
  consistent without waiting for the next polling tick.

### 1.3 Non-Goals

- Not adding branch-level graph visualisation (e.g. `git log --graph`).
- Not adding branch rename (`git branch -m`) — out of scope.
- Not adding branch protection / push / fetch — those are separate
  features.
- Not changing the backend APIs (v2.17.0 is final).
- Not supporting per-worktree branch filter (the list is project-level;
  branches inside other worktrees are not enumerated separately).

---

## 2. Architecture Overview

### 2.1 New files (4) and modified files (4)

```
NEW:
  dashboard/src/composables/parseSpcodeGitBranches.ts           (~ 100 lines)
  dashboard/src/composables/parseSpcodeBranchManagement.ts      (~ 150 lines)
  dashboard/src/composables/useSpcodeGitBranches.ts             (~ 280 lines)
  dashboard/src/composables/__tests__/useSpcodeGitBranches.spec.ts (~ 200 lines)

MODIFIED:
  dashboard/src/components/chat/GitDiffSidebar.vue              (+ ~ 220 lines)
  dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/features/chat.json (each + 60 keys)
  dashboard/src/components/chat/Chat.vue                        (export registration if needed)
  docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md
                                                                   (link to this spec)
```

### 2.2 Layering (mirrors `useSpcodeWorktrees` 1:1)

```
┌─────────────────────────────────────────────────────────────┐
│ GitDiffSidebar.vue                                          │
│  - branchesComposable = useSpcodeGitBranches()               │
│  - <BranchMenuButton> (new) + <v-menu> with branch list     │
│  - <BranchSwitchConfirmDialog> (new)                        │
│  - <BranchDeleteConfirmDialog> (new)                        │
│  - Cascading refresh hook in onBranchSwitchConfirm()         │
└──────────────────┬──────────────────────────────────────────┘
                   │  imports
       ┌───────────┴───────────┐
       ▼                       ▼
useSpcodeGitBranches.ts    parseSpcodeGitBranches.ts
  - state, refresh,        parseSpcodeBranchManagement.ts
    startPolling, dispose     (4 parsers, ok/failure)
  - switch, create, delete
       │                       │
       └───────┬───────────────┘
               ▼
        pluginExtensionApi
        ('spcode/git-branches',
         'spcode/git-branch-create',
         'spcode/git-branch-delete',
         'spcode/git-branch-switch')
```

### 2.3 Why composable (not in-place)

`useSpcodeWorktrees` already encapsulates "list + N mutations + polling
+ ETag" cleanly. Replicating that pattern keeps the diff sidebar
readable (the file is already 3,000+ lines) and lets the branch
operations be unit-tested without mounting the sidebar.

---

## 3. Design

### 3.1 Data Model

```ts
// parseSpcodeGitBranches.ts

/** Raw backend response (snake_case, mirrors /spcode/git-branches). */
export interface SpcodeGitBranchRaw {
  name: string;            // "main" | "origin/main" | "feature/x"
  sha: string;             // "a1b2c3d..."
  upstream: string;        // "" | "origin/main"
  upstream_track: string;  // "" | "ahead 2" | "behind 1, ahead 3"
  current: boolean;
  remote: boolean;
}

/** Camel-cased domain object for UI consumption. */
export interface SpcodeGitBranch {
  name: string;
  sha: string;
  upstream: string;
  upstreamTrack: string;
  current: boolean;
  remote: boolean;
}

export interface SpcodeGitBranchesSnapshot {
  meta: {
    loaded: boolean;
    directory: string;
    umo: string;
    reason: string;     // e.g. "ok" | "no_project_loaded" | "not_a_git_repo"
    stderr: string;
    elapsedMs: number;
  };
  branches: SpcodeGitBranch[];
  total: number;
  current: string | null;   // null when detached
  detached: boolean;
}
```

### 3.2 Composable API

```ts
// useSpcodeGitBranches.ts

export type BranchesFetchState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ok'; snapshot: SpcodeGitBranchesSnapshot; notModified?: boolean }
  | { kind: 'error'; reason: string; previousSnapshot?: SpcodeGitBranchesSnapshot };

export interface BranchMgmtParams {
  /** Session ID. Falls back to the composable's tracked umo if null. */
  umo?: string | null;
}

export interface BranchSwitchParams extends BranchMgmtParams {
  name: string;
  force?: boolean;   // accepted by backend; UI does NOT expose this in v1
  detach?: boolean;  // accepted by backend; UI does NOT expose this in v1
}

export interface BranchCreateParams extends BranchMgmtParams {
  name: string;
  startPoint?: string;   // default "HEAD"
}

export interface BranchDeleteParams extends BranchMgmtParams {
  name: string;
  force?: boolean;       // accepted by backend; UI does NOT expose this in v1
}

export type BranchMgmtResult =
  | { ok: true; snapshot: SpcodeGitBranchesSnapshot }
  | { ok: false; reason: string; stderr?: string };

export interface UseSpcodeGitBranches {
  state: Ref<BranchesFetchState>;
  refresh: () => Promise<void>;
  startPolling: (intervalMs?: number) => void;   // default 30_000
  stopPolling: () => void;
  switch: (params: BranchSwitchParams) => Promise<BranchMgmtResult>;
  create: (params: BranchCreateParams) => Promise<BranchMgmtResult>;
  delete: (params: BranchDeleteParams) => Promise<BranchMgmtResult>;
  dispose: () => void;
}
```

#### 3.2.1 Polling cadence

`DEFAULT_POLL_MS = 30_000`, **imported from `useSpcodeWorktrees.ts`**
rather than duplicated — single source of truth. Both composables are
started / stopped by the same watcher in `GitDiffSidebar.vue` (see
§3.6).

#### 3.2.2 ETag handling

`refresh()` stores the response `ETag` header on success and sends it
back as `If-None-Match` on the next call. 304 is treated as "not
modified" — the previous `notModified: true` snapshot is replayed from
`prevSnapshotMap` (same per-key scope as `useSpcodeGitLog`).

#### 3.2.3 Single-flight policy

Each mutation (`switch` / `create` / `delete`) creates its own
`AbortController`, aborts any in-flight mutation of the same kind (not
the read path), and surfaces cancellation as `{ ok: false, reason:
"aborted" }`. The sidebar translates `aborted` to a silent no-op.

#### 3.2.4 No worktree filtering

Unlike `useSpcodeGitDiff(selectedWorktree)`, branches are project-level.
The list is independent of the active worktree. **The composable does
NOT take a `worktreeRef`** — branches are not per-worktree in the git
data model; switching branches inside a worktree changes that
worktree's `wt.branch` field, which the worktree composable will pick
up on its next poll.

### 3.3 UI: Dropdown Menu

A new button is appended to the right of the existing worktree tabs
(label = current branch name, icon = `mdi-source-branch`). Clicking it
opens a `v-menu` (Vuetify) anchored `location="bottom end"`,
`max-width="320"`. The menu has three sections:

1. **Loading / error row** — single item, no actions
2. **Branch list** — one `v-list-item` per branch, with:
   - Leading icon: `mdi-check` (current) / `mdi-cloud-outline` (remote)
     / `mdi-source-branch` (local non-current)
   - Title: `b.name`
   - Trailing icon: `mdi-close` (× delete) — hidden for `b.current`
3. **Inline create form** — collapsed by default; clicking the
   `+ 新建分支` item expands an inline form with two `v-text-field`s
   (name + start point) and two action buttons (Cancel / Submit).
   Submit on Enter.

The menu does NOT close on content click (`close-on-content-click="false"`)
so users can interact with the inline form. It closes on:
- Click outside
- Escape
- Successful switch / create / delete (explicitly set
  `branchMenuOpen.value = false`)

#### 3.3.1 Button placement

```
+-------------------------------------------------------------+
| ⎇ Worktrees  [ main  v  feat/x  v  release  v ]  +   🌿 main  ⌄ |
+-------------------------------------------------------------+
                                  ↑                          ↑
                            existing + button            NEW branch button
```

The branch button sits **after** the worktree `+` button, inside the
same `git-diff-sidebar-tabs` flex container.

### 3.4 Dialogs

#### 3.4.1 `BranchSwitchConfirmDialog.vue`

| Field          | Value / Source                                     |
|----------------|----------------------------------------------------|
| Title          | "切换分支" / "Switch branch"                       |
| Body line 1    | "确认从 {from} 切换到 {to}？"                       |
| Body line 2    | (conditional) "该 worktree 有 {N} 个未提交改动"   |
| Confirm button | "切换" — disabled if `dirtyCount > 0`              |
| Cancel button  | "取消"                                              |

**Dirty pre-check**: before opening the dialog, the sidebar calls
`GET /spcode/git-status?umo=...&worktree={current}`. If the returned
snapshot has any non-empty `porcelain` output, the dialog opens with
the dirty line AND the confirm button disabled. **No force checkbox**
(per design decision: `block_strict`).

The source branch name is read from
`worktreeList.find(w => w.path === selectedWorktree)?.branch` (or
`null` for detached main worktree).

#### 3.4.2 `BranchDeleteConfirmDialog.vue`

| Field          | Value / Source                                     |
|----------------|----------------------------------------------------|
| Title          | "删除分支" / "Delete branch"                       |
| Body line 1    | "确认删除分支 {name}？"                             |
| Confirm button | "删除" — variant `tonal`                           |
| Cancel button  | "取消" — variant `text`                            |

The sidebar prevents opening this dialog for `b.current` branches
(no × button is rendered in the menu), so the
`branch_is_current` reason from the backend is a defense-in-depth
fallback (logged but not user-facing in v1).

The dialog does **not** show an "unmerged" warning — the backend
returns `branch_not_merged` on `-d` failure, which the sidebar
translates to an error toast.

### 3.5 Polling & Lifecycle

```ts
// GitDiffSidebar.vue (excerpt)

// 1. Instantiate composable
const branchesComposable = useSpcodeGitBranches();

// 2. Initial fetch on mount
onMounted(() => {
  void worktreesComposable.refresh();
  void branchesComposable.refresh();
});

// 3. Single watcher starts/stops BOTH pollers in lockstep
watch(
  () => [isGitRepo.value, showNotGitRepoChip.value, modelValue.value] as const,
  ([git, chip, open]) => {
    if (open && (git || chip)) {
      worktreesComposable.startPolling(30_000);
      branchesComposable.startPolling(30_000);
    } else {
      worktreesComposable.stopPolling();
      branchesComposable.stopPolling();
    }
  },
  { immediate: true },
);

// 4. umo / directory change → refetch (branches don't need selectedWorktree)
watch(
  () => spcodeStatus.status.value.umo,
  (newUmo, oldUmo) => {
    if (newUmo && newUmo !== oldUmo) {
      void branchesComposable.refresh();
    }
  },
);

// 5. Dispose on unmount
onBeforeUnmount(() => {
  branchesComposable.dispose();
  // ...existing dispose
});
```

**Project unload**: when `spcodeStatus.status.value.loaded` flips to
`false`, the composable's next `refresh()` sees no umo and sets
`state.kind = 'error', reason = 'no_project_loaded'`. The button
hides (covered by the same `v-if` gate as the worktree tabs).

### 3.6 Cascade Refresh (post-switch)

```ts
async function onBranchSwitchConfirm(name: string): Promise<void> {
  isBranchSwitching.value = true;
  try {
    const result = await branchesComposable.switch({ name });
    if (!result.ok) {
      const key = branchErrorKey('switch', result.reason);
      tm(key, { stderr: result.stderr, name });
      return;
    }
    branchMenuOpen.value = false;

    // 1) Always refresh worktree list (wt.branch field changes)
    const tasks: Promise<unknown>[] = [worktreesComposable.refresh()];

    // 2) viewMode-aware: refresh only the views the user is currently seeing
    switch (viewMode.value) {
      case 'diff':
        tasks.push(composable.refresh(), gitStatus.refresh());
        break;
      case 'files':
        tasks.push(gitStatus.refresh());
        break;
      case 'history':
        tasks.push(gitLog.refresh());
        break;
      case 'docs':
        // Status badge in FileBrowserView header may reference branch.
        tasks.push(gitStatus.refresh());
        break;
    }

    await Promise.allSettled(tasks);

    tm('spcodeProjectLoad.diffSidebar.branchMgmt.switch.success', { name });
  } finally {
    isBranchSwitching.value = false;
  }
}
```

`switch` and `delete` are no-ops when targeting the current branch
(client-side shortcut — the menu's current branch item is rendered
without a click handler that would open the dialog).

### 3.7 Error Handling

Reason → i18n key mapping, all defined in `GitDiffSidebar.vue`:

```ts
const BRANCH_ERROR_KEYS = {
  switch: {
    worktree_dirty:  'spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.worktree_dirty',
    branch_not_found:'spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.branch_not_found',
    invalid_branch:  'spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.invalid_branch',
    invalid_body:    'spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error',
    git_error:       'spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error',
  },
  delete: {
    branch_is_current:'spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_is_current',
    branch_not_merged:'spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_not_merged',
    branch_not_found: 'spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_not_found',
    invalid_branch:   'spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.invalid_branch',
    git_error:        'spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error',
  },
  create: {
    branch_exists:  'spcodeProjectLoad.diffSidebar.branchMgmt.create.error.branch_exists',
    invalid_branch: 'spcodeProjectLoad.diffSidebar.branchMgmt.create.error.invalid_branch',
    git_error:      'spcodeProjectLoad.diffSidebar.branchMgmt.create.error.git_error',
  },
} as const;
```

Fallback: any unmapped reason renders the stderr (truncated to 200
chars) inline as the toast message, prefixed with the raw reason
(for debug-ability).

### 3.8 Edge Cases

| Scenario                                     | Handling |
|----------------------------------------------|----------|
| Non-git project                              | Button hidden by `v-if="isGitRepo || showNotGitRepoChip"` |
| Detached HEAD (no current branch)            | Button label = "detached HEAD"; menu has no `current` row |
| Click on current branch item                 | No-op (handler returns early) |
| Branch name contains `/` (e.g. `feature/x`)  | Backend validates via `_is_valid_ref_name`; UI only trims whitespace |
| Remote-only branch                           | × delete button rendered (allowed); no local `wt.branch` would point at it |
| `branch_not_merged` on delete                | Toast error, suggest force in v2 (not v1) |
| Double-click on a branch item                | `isBranchSwitching` flag locks the menu until response |
| Polling after sidebar closed                 | `stopPolling` called on `modelValue=false`; no requests sent |
| Polling after project unload                 | Composable sees no umo → `no_project_loaded` error state; menu hidden |
| Branch name with leading/trailing whitespace | UI trims in both the create form and the row click; backend validator catches any bypass |
| Empty branch list (newly init'd repo)        | Menu shows an empty-state row "暂无分支" (i18n: "No branches yet") |
| `branchExists: branch_exists` after create   | Toast "分支 {name} 已存在"; inline input not cleared (user can edit name and retry) |

---

## 4. i18n

Three locales, ~60 keys each, all under
`spcodeProjectLoad.diffSidebar.branchMgmt.*`. The full key tree:

```
branchMgmt:
  menuButton          # "切换分支" / "Switch branch" / "Переключить ветку"
  menuButtonAria      # aria-label
  loading             # "加载分支列表…"
  error               # "获取分支失败（{reason}）"
  detached            # "detached HEAD"
  remote              # "远程" (badge for remote-only rows)
  empty               # "暂无分支"
  switch:
    confirmTitle      # "切换分支"
    confirmMessage    # "确认从 {from} 切换到 {to}？"
    dirtyBlocked      # "该 worktree 有 {count} 个未提交改动,请先 commit 或 stash。"
    submit            # "切换"
    cancel            # "取消"
    success           # "已切换到分支 {name}"
    error:
      worktree_dirty  # "工作区有未提交改动,切换被阻止"
      branch_not_found# "分支 {name} 不存在"
      invalid_branch  # "分支名格式错误"
      git_error       # "切换失败:{stderr}"
  delete:
    confirmTitle      # "删除分支"
    confirmMessage    # "确认删除分支 {name}？"
    submit            # "删除"
    cancel            # "取消"
    success           # "已删除分支 {name}"
    error:
      branch_is_current    # "不能删除当前分支"
      branch_not_merged    # "分支未合并,无法安全删除（需 force）"
      branch_not_found     # "分支 {name} 不存在"
      git_error            # "删除失败:{stderr}"
  create:
    menuItem          # "新建分支"
    name              # "分支名"
    nameHint          # "仅允许字母数字 / . / - / _"
    nameRequired      # "请输入分支名"
    startPoint        # "起始点"
    startPointHint    # "默认 HEAD"
    submit            # "创建"
    success           # "已创建分支 {name}"
    error:
      branch_exists   # "分支 {name} 已存在"
      invalid_branch  # "分支名格式错误"
      git_error       # "创建失败:{stderr}"
```

`ru-RU` strings are translated by following the existing ru-RU tone in
`chat.json` (e.g. "Переключить ветку", "Создать ветку", "Удалить
ветку").

---

## 5. Testing

### 5.1 Parser unit tests (`parseSpcodeGitBranches.spec.ts`)

- Envelope success → `kind: 'ok'`
- Envelope `success: false` → `kind: 'failure'`, preserves `reason` + `stderr`
- `current: true` correctly migrated to the camelCase snapshot
- `total` field
- `detached: true` when no branch has `current: true`
- Empty branches array
- Malformed envelope (missing `data.branches`) → `kind: 'failure', reason: 'malformed'`

### 5.2 Mutation parser tests (`parseSpcodeBranchManagement.spec.ts`)

Each of the 4 parsers (`switch`, `create`, `delete`, plus a generic
`update`-like envelope variant for `git-branches`):

- Success path → returns `{ kind: 'ok', snapshot: ... }`
- Failure with `worktree_dirty` reason → `kind: 'failure', reason: 'worktree_dirty'`
- Network error → `kind: 'failure', reason: 'network'`
- Aborted request → `kind: 'failure', reason: 'aborted'`

### 5.3 Composable unit tests (`useSpcodeGitBranches.spec.ts`)

- `refresh()` transitions through `loading → ok`
- `refresh()` with 304 replays previous snapshot (`notModified: true`)
- `startPolling` is idempotent (second call while running is a no-op)
- `stopPolling` clears the interval
- `dispose()` clears the interval, aborts in-flight requests, sets
  `isMounted = false`
- `switch` ok path: `state.value.kind === 'ok'`, snapshot updated
- `switch` failure: `state.value.kind !== 'ok'`, returns
  `{ ok: false, reason: ... }` with stderr
- `create` / `delete` parallel to `switch`
- `umo` watcher triggers a refresh when umo changes
- After `dispose`, no further state updates occur

### 5.4 E2E (optional, deferred to v2)

- Open sidebar → open dropdown → click "feature/x" → confirm
- Verify worktree list shows updated `wt.branch`
- Verify diff panel reflects the new branch

### 5.5 i18n completeness

`i18n.completeness.spec.ts` already enforces zh-CN ↔ en-US ↔ ru-RU
parity. The 60 new keys are added in lockstep across all three files.

### 5.6 Lint / format

- `code_check` on all new `.ts` files (ruff)
- `code_format` on all new `.ts` files
- `pnpm generate:api` only if backend OpenAPI changes (none expected
  in v1; all 4 endpoints already exist)

---

## 6. Risks & Open Questions

### 6.1 Risks

| Risk                                                                        | Mitigation |
|-----------------------------------------------------------------------------|------------|
| GitDiffSidebar.vue grows to 3,500+ lines                                    | Branch composable is fully self-contained; `parseSpcode*Branch*` and `useSpcodeGitBranches` keep all logic out of the component |
| Race between branch polling and switch mutation                            | Single-flight AbortController per mutation kind; read path never cancels writes |
| User creates a branch then immediately switches — fast polling can confuse  | After create, `refresh()` is called explicitly (parser returns refreshed snapshot) |
| Inline create form accidentally triggering switch on Enter                  | `v-text-field`s inside the create form have `@keyup.enter` bound to `onBranchCreateSubmit`, NOT to the parent menu; Enter does not bubble |
| Deleting a remote branch with `git branch -d` is a no-op (git refuses)     | UI shows a toast with the backend stderr ("not found" or similar) |
| `branch_not_merged` UX is poor without force option                        | v1 shows clear error; v2 may add force checkbox if user feedback demands |

### 6.2 Open questions

- **Q1**: Should the branch list also be filterable (text search for
  long branch lists)? **Decision: defer to v2** (no need at 5-20
  branches per project).
- **Q2**: Should the create form support `force` to overwrite an
  existing branch? **Decision: v1 = no**, v2 = add a checkbox.
- **Q3**: Should `BranchDeleteConfirmDialog` show a "merged into main"
  indicator (would need an extra `git branch --merged` call)? **Decision:
  defer to v2** — backend `branch_not_merged` error is sufficient.

---

## 7. Implementation Tasks (high-level, to be expanded in plan)

1. Create `parseSpcodeGitBranches.ts` + tests
2. Create `parseSpcodeBranchManagement.ts` + tests
3. Create `useSpcodeGitBranches.ts` + tests
4. Add i18n keys (zh-CN, en-US, ru-RU)
5. Run `i18n.completeness.spec.ts` to verify parity
6. Create `BranchSwitchConfirmDialog.vue` + `BranchDeleteConfirmDialog.vue`
7. Modify `GitDiffSidebar.vue`:
   - Import composable + types
   - Add state refs
   - Add watcher for polling lifecycle
   - Add cascade refresh helper
   - Add menu button + v-menu template
   - Add dialog templates
   - Wire error reason → i18n key
8. Run `code_check` + `code_format` on all changed files
9. Manual smoke test in dashboard dev mode:
   - Open sidebar in a git repo with multiple branches
   - Switch / create / delete
   - Verify cascade refresh in each viewMode
   - Verify polling on tab focus / blur

---

## 8. References

- Backend spec: `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md`
- Backend API doc: `docs/api/webapi-git-init-branch-revert-api.md`
- Worktree composable reference: `dashboard/src/composables/useSpcodeWorktrees.ts`
- Worktree dialog reference: `dashboard/src/components/chat/message_list_comps/WorktreeCreateDialog.vue`
- Existing error key pattern: `spcodeProjectLoad.diffSidebar.worktreeMgmt.*` keys in `chat.json`

---

**End of spec** — please review and request changes if any section is
incomplete or wrong. After approval, the next step is to invoke
`writing-plans` to expand §7 into a step-by-step implementation plan
with file paths, line numbers, and test checkpoints.
