<!--
Author: elecvoid243 @ 2026-08-01
Spec: Git Diff Sidebar — Merge / Cherry-pick / Conflict Resolution Frontend
Status: Draft (awaiting user review)
Backend: docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md (v2.22.0, shipped)
Frontend precedent: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md
-->

# Git Diff Sidebar — Merge / Cherry-pick / Conflict Resolution Frontend

> Wire the six v2.22.0 backend endpoints (`/spcode/git-merge`,
> `/spcode/git-cherry-pick`, `/spcode/git-conflict-status`,
> `/spcode/git-conflict-resolve`, `/spcode/git-conflict-abort`,
> `/spcode/git-conflict-continue`) into the ChatUI Git Diff Sidebar.
> The backend is final; this spec is the frontend wiring only.

---

## 1. Context & Goals

### 1.1 Context

`GitDiffSidebar.vue` (in `F:\github\Astrbot\dashboard\src\components\chat\`)
already exposes worktree tabs, a diff viewer, stage/unstage/commit, log,
branch switcher, and revert. The spcode plugin shipped the merge /
cherry-pick / conflict-lifecycle endpoints in v2.22.0 (2026-07-28), but
**no frontend calls them yet**: `grep spcode/git-merge|cherry|conflict`
across `dashboard/src` returns zero hits. Users can only resolve
conflicts in a terminal today.

### 1.2 Goals

- Merge a branch into the current HEAD from the branch menu, with
  `--no-ff` / `--ff-only` / `--squash` and an optional commit message.
- Cherry-pick any commit, triggered both from a log-row hover button
  and from a standalone dialog accepting an arbitrary ref (+ mainline).
- Full conflict-resolution lifecycle inside the sidebar:
  - Persistent banner + expandable panel whenever a merge /
    cherry-pick / revert conflict is in progress (including conflicts
    triggered externally by the agent in a terminal — detected via
    30 s polling of `git-conflict-status`).
  - Per-hunk resolution (ours / theirs / base when diff3 present),
    whole-file ours/theirs shortcuts, and a manual-edit (custom) mode.
  - Batch resolve-all (ours/theirs), Continue (with optional commit
    message for merge), and Abort with confirmation.
- Follow the established layered pattern: pure parsers (unit-testable
  without Vue) + composables (state machine, ETag, polling,
  single-flight mutations) + extracted child components.

### 1.3 Non-Goals

- No rebase, push/pull/fetch, stash-conflict UI, or merge into a
  non-current branch.
- No three-way merge editor (custom mode is a plain monospace
  `v-textarea`, per KISS).
- No backend changes (v2.22.0 is final).
- No changes to existing diff/stage/commit/log behaviour except the
  cascading-refresh hooks after the new mutations.

---

## 2. Architecture Overview

### 2.1 New files (7) and modified files (4)

```
NEW (dashboard = F:\github\Astrbot\dashboard):
  dashboard/src/composables/parseSpcodeGitMerge.ts              (~ 200 lines)
  dashboard/src/composables/parseSpcodeGitConflict.ts           (~ 260 lines)
  dashboard/src/composables/useSpcodeGitMerge.ts                (~ 230 lines)
  dashboard/src/composables/useSpcodeGitConflict.ts             (~ 330 lines)
  dashboard/src/components/chat/GitMergeDialog.vue              (~ 200 lines)
  dashboard/src/components/chat/GitCherryPickDialog.vue         (~ 180 lines)
  dashboard/src/components/chat/GitConflictPanel.vue            (~ 550 lines)

NEW tests:
  dashboard/src/composables/__tests__/parseSpcodeGitMerge.spec.ts
  dashboard/src/composables/__tests__/parseSpcodeGitConflict.spec.ts
  dashboard/src/composables/__tests__/useSpcodeGitConflict.spec.ts

MODIFIED:
  dashboard/src/components/chat/GitDiffSidebar.vue              (+ ~ 300 lines)
  dashboard/src/i18n/locales/{zh-CN,en-US,ru-RU}/features/chat.json
                                                                (each + ~ 90 keys)
  docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md
                                                  (append link to this spec)
```

### 2.2 Layering (mirrors the branch-switcher precedent 1:1)

```
┌────────────────────────────────────────────────────────────────┐
│ GitDiffSidebar.vue (orchestration only)                        │
│  - gitMerge = useSpcodeGitMerge()                              │
│  - gitConflict = useSpcodeGitConflict(selectedWorktree)        │
│  - branch-menu row action → <GitMergeDialog>                   │
│  - log-row hover button / toolbar → <GitCherryPickDialog>      │
│  - inConflict banner → <GitConflictPanel>                      │
│  - cascading refresh after every successful mutation           │
└───────────────┬──────────────────────────────┬─────────────────┘
                │                              │
        useSpcodeGitMerge.ts           useSpcodeGitConflict.ts
          merge() / cherryPick()        state / refresh / polling
                │                       resolve() / abort() / continue()
        parseSpcodeGitMerge.ts          parseSpcodeGitConflict.ts
                │                              │
                └──────────┬───────────────────┘
                           ▼
                  pluginExtensionApi
                  ('spcode/git-merge', 'spcode/git-cherry-pick',
                   'spcode/git-conflict-status', 'spcode/git-conflict-resolve',
                   'spcode/git-conflict-abort', 'spcode/git-conflict-continue')
```

### 2.3 Why two composables (not one)

`useSpcodeGitMerge` owns the two *fire-and-forget mutations* that can
trigger a conflict. `useSpcodeGitConflict` owns the *stateful polling
resource* (conflict status) plus the three lifecycle mutations that
operate on it. This mirrors `useSpcodeGitBranches` (polling list +
mutations) vs `useSpcodeGitCommit` (single mutation) and keeps each
unit independently testable. Cherry-pick lives in `useSpcodeGitMerge`
because both are "apply history onto HEAD" mutations with identical
result/conflict envelopes.

---

## 3. Data Model

### 3.1 `parseSpcodeGitMerge.ts`

```ts
/** POST /spcode/git-merge request body. */
export interface SpcodeMergeParams {
  source: string;            // branch/ref to merge into HEAD
  noFf?: boolean;            // default false
  ffOnly?: boolean;          // default false (mutex with noFf/squash)
  squash?: boolean;          // default false (mutex with noFf/ffOnly)
  message?: string;          // optional -m
}

export type SpcodeMergeResult =
  | {
      ok: true;
      merged: boolean;         // false when squash (staged, not committed)
      mergeSha: string;        // "" for squash
      mergeMessage: string;
      fastForward: boolean;
      squash: boolean;
      filesTouched: string[];
    }
  | {
      ok: false;
      reason: string;          // ReasonCode, see §3.4
      conflict?: boolean;      // true when reason === "merge_conflict"
      conflictedFiles?: string[];
      stderr?: string;
    };

/** POST /spcode/git-cherry-pick request body. */
export interface SpcodeCherryPickParams {
  ref: string;               // any commit-ish
  mainline?: number | null;  // >= 1 for merge commits
}

export type SpcodeCherryPickResult =
  | { ok: true; newSha: string; originalMessage: string; filesTouched: string[] }
  | { ok: false; reason: string; conflict?: boolean;
      conflictedFiles?: string[]; stderr?: string };
```

### 3.2 `parseSpcodeGitConflict.ts`

```ts
/** One parsed conflict hunk from GET /spcode/git-conflict-status. */
export interface ConflictHunk {
  index: number;
  startLine: number;           // 1-based, inclusive
  endLine: number;             // 1-based, inclusive
  ours: string;
  theirs: string;
  base: string | null;         // non-null only with diff3 markers
  oursLabel: string;
  theirsLabel: string;
}

export interface ConflictedFile {
  path: string;
  status: string;              // e.g. "UU", "AA", "DU"
  hunks: ConflictHunk[];       // [] for binary/truncated/beyond-cap files
  threeWay: {                  // full-stage contents from the backend
    base: string | null;       // (:1:) null when the side does not exist
    ours: string | null;       // (:2:)
    theirs: string | null;     // (:3:)
  };
  binary: boolean;
  truncated: boolean;
}

export interface ConflictSnapshot {
  inConflict: boolean;
  operation: "merge" | "cherry_pick" | "revert" | null;
  operationRef: string | null;
  operationSubject: string | null;
  conflictedFiles: ConflictedFile[];
  resolvedFiles: string[];
  totalConflicted: number;
  totalResolved: number;
  allResolved: boolean;
  directory: string;
}

/** POST /spcode/git-conflict-resolve — four mutually exclusive modes. */
export type SpcodeResolveParams =
  | { mode: "hunks"; file: string;
      hunks: { index: number; choice: "ours" | "theirs" | "base" }[] }
  | { mode: "whole"; file: string; resolution: "ours" | "theirs" }
  | { mode: "custom"; file: string; content: string }
  | { mode: "all"; resolution: "ours" | "theirs" };

export type SpcodeResolveResult =
  | { ok: true; resolved: boolean; partial: boolean;
      hunksResolved: number | null; hunksTotal: number | null;
      unresolvedHunks: { index: number; startLine: number; endLine: number }[];
      remainingConflicts: { path: string; status: string }[];
      allResolved: boolean }
  | { ok: false; reason: string; stderr?: string };

export type SpcodeConflictActionResult =   // abort / continue
  | { ok: true; operation: "merge" | "cherry_pick" | "revert";
      commitSha?: string;                  // continue only
      commitMessage?: string }
  | { ok: false; reason: string; stderr?: string;
      remainingConflicts?: { path: string; status: string }[] };
```

### 3.3 Composable state machines

`useSpcodeGitMerge` — stateless mutations (like `useSpcodeGitCommit`):
each call creates its own `AbortController`, aborts the in-flight
mutation of the same composable, and returns the parsed result.
No cached state; callers handle toasts.

`useSpcodeGitConflict` — stateful resource (like `useSpcodeGitBranches`):

```ts
export type ConflictFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; snapshot: ConflictSnapshot; notModified?: boolean }
  | { kind: "error"; reason: string; previousSnapshot?: ConflictSnapshot };

export interface UseSpcodeGitConflict {
  state: Ref<ConflictFetchState>;
  refresh: () => Promise<void>;
  startPolling: (intervalMs?: number) => void;   // default 30_000 ms
  stopPolling: () => void;
  resolve: (p: SpcodeResolveParams) => Promise<SpcodeResolveResult>;
  continueOp: (message?: string) => Promise<SpcodeConflictActionResult>;
  abort: () => Promise<SpcodeConflictActionResult>;
  dispose: () => void;
}
```

- ETag + `If-None-Match` on `git-conflict-status`, keyed by
  `conflict|{umo}|{directory}` (same scheme as branches).
- `resolve()` / `continueOp()` / `abort()` share one `mutationAbort`
  controller (single-flight), and each success triggers an immediate
  `refresh()` so the banner/panel updates without waiting for the tick.
- `umo` / worktree come from `useSpcodeProjectStatus` and the
  `selectedWorktree` ref passed in, exactly like `useSpcodeGitStatus`.

### 3.4 Reason-code → i18n mapping

The parsers expose `reason` verbatim; the Vue layer maps it through
`gitDiffSidebar.<group>.error.<reason>` with a `generic` fallback:

| reason | group | UI surface |
|---|---|---|
| `merge_conflict`, `cherry_pick_conflict` | merge / cherryPick | warning toast + conflict banner appears |
| `merge_already_up_to_date` | merge | info toast |
| `worktree_dirty` | merge / cherryPick | error toast |
| `operation_in_progress` | merge / cherryPick | error toast (points at banner) |
| `invalid_branch`, `commit_not_found` | merge / cherryPick | error toast |
| `unrelated_histories` | merge | error toast |
| `cherry_pick_empty` | cherryPick | info toast |
| `no_conflict_in_progress` | conflict | error toast + force refresh |
| `file_not_conflicted`, `path_unsafe`, `invalid_param`, `invalid_body` | conflict | error toast |
| `unresolved_conflicts_remain` | conflict | error toast + scroll to remaining files |
| `identity_not_set`, `hook_rejected`, `git_error`, `network`, `unknown` | all | error toast |

---

## 4. Component Design

### 4.1 `GitMergeDialog.vue`

- **Opened from**: a merge icon-button on each branch-menu row where
  `!branch.current && !branch.remote`. Props: `source: string`.
- **Contents**: source branch (readonly), strategy `v-radio-group`
  （默认 / `--no-ff` / `--ff-only` / `--squash`), optional message
  `v-textarea` (disabled for `--ff-only`, hint explains why).
- **Submit** → `gitMerge.merge(params)`:
  - `ok && !squash` → success toast (`mergeSha` short form), close,
    cascading refresh (§4.5).
  - `ok && squash` → info toast listing staged files, close,
    cascading refresh (staged view will show them).
  - `!ok && conflict` → warning toast, close dialog, immediate
    `gitConflict.refresh()`.
  - `!ok` otherwise → error toast via §3.4 map, dialog stays open.

### 4.2 `GitCherryPickDialog.vue`

- **Two entry points**:
  1. Log-view commit row hover button → dialog pre-filled with the
     commit sha + subject, ref input readonly in this mode.
  2. Toolbar "cherry-pick" button → empty editable ref input.
- **Contents**: ref `v-text-field` (validated non-empty), `mainline`
  `v-text-field` (number, optional, hint explains merge-commit case).
- **Submit** → `gitMerge.cherryPick(params)`; result handling mirrors
  §4.1 (`cherry_pick_conflict` → warning + `gitConflict.refresh()`).

### 4.3 `GitConflictPanel.vue`

- **Banner**: when `gitConflict.state.kind === "ok" &&
  snapshot.inConflict`, a persistent `v-alert`-style banner renders at
  the top of the sidebar body: operation label
  (merge / cherry-pick / revert), `operationSubject`, and progress
  `{totalResolved}/{totalResolved + totalConflicted}`. Click toggles
  the expanded panel.
- **File list**: two groups — unresolved (`conflictedFiles`) and
  resolved (`resolvedFiles`, shown collapsed with a check icon).
- **Per conflicted file** (expanded):
  - Hunk list: each hunk renders ours / theirs stacked code blocks
    (plus base when `hunk.base != null`) with a per-hunk
    `v-radio-group` (ours / theirs / base). File-level "apply"
    requires every hunk chosen → `resolve({mode:"hunks", ...})`.
    Partial backend response (`partial: true`) keeps the file open and
    shows the remaining `unresolvedHunks`.
  - Shortcuts: "采用 ours" / "采用 theirs" → `mode:"whole"`;
    "手动编辑" → monospace `v-textarea` pre-filled with
    `threeWay.ours` (empty string when null — e.g. file deleted on our
    side; the backend payload is the only full-content source, since
    `/spcode/git-file` reads blobs at a ref, not the working tree)
    → `mode:"custom"`.
  - `binary || truncated` files: only the two whole-file shortcuts.
- **Footer**: "全部采用 ours/theirs" (with confirm) → `mode:"all"`;
  `Continue` (enabled only when `allResolved`, optional commit-message
  field shown when `operation === "merge"`) → `continueOp()`;
  `Abort` (confirm dialog naming the operation) → `abort()`.
- After `continueOp` / `abort` success → banner disappears (snapshot
  reports `inConflict: false`) + cascading refresh (§4.5).

### 4.4 `GitDiffSidebar.vue` integration

- Instantiate `gitMerge` and `gitConflict(selectedWorktree)`; start /
  stop conflict polling in the same lifecycle hooks that manage
  branch/worktree polling (30 s cadence, shared constant).
- Branch-menu row action (§4.1), log-row hover button + toolbar
  button (§4.2), banner mount point above the scope bar (§4.3).
- Pass `selectedWorktree` path as the `worktree` param on all six
  endpoints (same as existing git-status calls).

### 4.5 Cascading refresh matrix

| mutation success | refreshes |
|---|---|
| merge / cherry-pick (no conflict) | branches, git-diff, git-status, git-log |
| merge / cherry-pick → conflict | gitConflict (immediate), git-diff, git-status |
| resolve (file or all) | gitConflict (immediate), git-diff |
| continue / abort | gitConflict, branches, git-diff, git-status, git-log |

This reuses the existing `refreshAfterMutation`-style helpers in
`GitDiffSidebar.vue`; no new refresh plumbing is invented.

---

## 5. Error Handling & Edge Cases

- **Network / abort**: `CanceledError` swallowed; `ERR_NETWORK` →
  `reason: "network"`; identical to `useSpcodeGitBranches`.
- **Unmount**: `dispose()` aborts in-flight reads + mutations and
  stops polling; called from the sidebar's `onBeforeUnmount` alongside
  the other composables.
- **External state changes**: the 30 s poll (plus ETag caching) keeps
  the banner truthful when the agent merges/cherry-picks/aborts in a
  terminal or another client does.
- **Conflict during conflict**: `operation_in_progress` from
  merge/cherry-pick is surfaced as an error toast directing the user
  to the open conflict panel; the UI never attempts nested merges.
- **diff3 absence**: `base` choice is hidden for hunks where
  `base === null`; the backend also rejects `choice:"base"` without
  diff3, and the parser surfaces that `invalid_param`.
- **Squash merge**: result has `merged: false, squash: true`; the UI
  explains changes are staged, not committed, and the staged scope
  shows them after refresh.
- **Continue blocked**: `unresolved_conflicts_remain` returns
  `remainingConflicts`; the panel re-expands and highlights those
  files (they are still in `conflictedFiles` after the refresh).

## 6. Testing

All specs live in `dashboard/src/composables/__tests__/` and run
under the dashboard's vitest setup (mirrors
`useSpcodeGitBranches.spec.ts`).

1. `parseSpcodeGitMerge.spec.ts` — wire fixtures for: fast-forward
   merge, merge-commit, squash, `merge_already_up_to_date`,
   `merge_conflict` (with `conflicted_files`), `worktree_dirty`,
   `operation_in_progress`, `unrelated_histories`; cherry-pick ok,
   `cherry_pick_conflict`, `cherry_pick_empty`, `commit_not_found`;
   malformed envelopes → safe fallback.
2. `parseSpcodeGitConflict.spec.ts` — conflict-status with multiple
   files/hunks incl. diff3 `base`, binary, truncated, resolved-files
   computation, `in_conflict: false` shape; resolve results for all
   four modes incl. `partial: true` with `unresolved_hunks`;
   continue ok / `unresolved_conflicts_remain`; abort ok.
3. `useSpcodeGitConflict.spec.ts` — mocked `pluginExtensionApi`:
   idle → loading → ok transitions, 304 `notModified` path, error
   keeps `previousSnapshot`, polling start/stop cadence, single-flight
   mutation abort, `dispose()` safety, immediate refresh after a
   successful resolve.

Manual smoke checklist (recorded in the implementation plan): merge
with conflict end-to-end in a scratch repo, cherry-pick from log row,
abort mid-merge, squash merge, binary-file conflict, agent-triggered
external merge appearing via polling.

## 7. i18n

New keys under the existing `gitDiffSidebar` namespace in
`zh-CN` / `en-US` / `ru-RU` `features/chat.json` (structure mirrors
`branchMgmt`):

```
gitDiffSidebar.merge.{menuItem,dialogTitle,strategy{...},messageLabel,
                      submit,cancel,success,squashSuccess,error.{...}}
gitDiffSidebar.cherryPick.{toolbar,rowAction,dialogTitle,refLabel,
                           mainlineLabel,submit,cancel,success,error.{...}}
gitDiffSidebar.conflict.{banner.{merge,cherry_pick,revert,progress},
                         file.{ours,theirs,base,apply,editOursTheirs,custom},
                         footer.{resolveAllOurs,resolveAllTheirs,continue,
                                 abort,commitMessage},error.{...}}
```

## 8. Rollout Notes

- Purely additive; no breaking change to existing sidebar behaviour.
- Works against any spcode plugin ≥ v2.22.0; against older plugins the
  requests 404 and the toasts surface `network`/`unknown` — acceptable
  because the sidebar already requires a recent plugin for
  branch/worktree features.
- Implementation order (details in the plan): parsers → composables +
  unit tests → dialogs → conflict panel → sidebar wiring → i18n →
  manual smoke.
