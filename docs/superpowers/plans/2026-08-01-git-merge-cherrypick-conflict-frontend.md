# Git Merge / Cherry-pick / Conflict Resolution Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the six spcode v2.22.0 git endpoints (merge / cherry-pick / conflict-status / conflict-resolve / conflict-abort / conflict-continue) into the ChatUI Git Diff Sidebar.

**Architecture:** Pure parsers + composables + extracted child components, mirroring the branch-switcher precedent (`docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md`). `useSpcodeGitMerge` owns the two history mutations; `useSpcodeGitConflict` owns the polled conflict-status resource plus resolve/continue/abort; three new child components (`GitMergeDialog`, `GitCherryPickDialog`, `GitConflictPanel`) keep `GitDiffSidebar.vue` as orchestration only.

**Tech Stack:** Vue 3 (`<script setup>`), Vuetify 3, TypeScript, vitest + @vue/test-utils, axios via `pluginExtensionApi` (`@/api/v1`).

**Spec:** `F:\github\astrbot_plugin_spcode_toolkit\docs\superpowers\specs\2026-08-01-git-merge-cherrypick-conflict-frontend-design.md`
**Backend (final, do not change):** `F:\github\astrbot_plugin_spcode_toolkit\tools\webapi\git_merge.py` etc. (v2.22.0)

## Global Constraints

- All code changes happen in the **AstrBot repo worktree** (`F:\github\Astrbot`, dashboard at `dashboard/`), on a dedicated feature branch in a git worktree (Task 0). Local commits only — **never push, never open PRs**.
- Dashboard verification commands: `pnpm test` (vitest), `pnpm typecheck` (vue-tsc), `pnpm lint` (eslint --fix). Run from `dashboard/`.
- Commit messages: conventional commits, English (`feat: ...`, `test: ...`, `i18n: ...`).
- Code comments in English; file header comment `// Author: elecvoid243 @ 2026-08-01` + spec reference on every new file.
- New dialogs: title `class="text-h3 pa-4 pb-0 pl-6"`, buttons `variant="text"` or `variant="tonal"` (AGENTS.md §9). Do NOT copy the older revert dialog's `text-h6`/`flat` style.
- i18n namespace: `spcodeProjectLoad.diffSidebar.*` in all three locales `zh-CN` / `en-US` / `ru-RU` (`dashboard/src/i18n/locales/<loc>/features/chat.json`). Insert new groups as siblings of the existing `branchMgmt` group.
- Backend envelope for all six endpoints: `{ status: "ok", data: { ...fields, reason: string | null, stderr: string, elapsed_ms: number } }`. **Failure is signalled by `reason !== null`; HTTP stays 200.** Success-flag fields: merge → `merged`, cherry-pick → `picked`, resolve → `resolved`, continue → `continued`, abort → `aborted`.
- `umo` comes from `useSpcodeProjectStatus().status.value.umo`; the active worktree path comes from the sidebar's `selectedWorktree` ref and is sent as `worktree` in the POST body (mutation precedent: `useSpcodeGitCommit`).
- Reason codes the UI must map (backend `ReasonCode`, `_helpers.py`): universal `feature_disabled | no_project_loaded | worktree_invalid | directory_missing | not_a_git_repo | git_unavailable | git_error | invalid_body | invalid_param`; merge `merge_conflict | merge_already_up_to_date | unrelated_histories | worktree_dirty | operation_in_progress | invalid_branch`; cherry-pick `cherry_pick_conflict | cherry_pick_empty | commit_not_found | worktree_dirty | operation_in_progress`; conflict lifecycle `no_conflict_in_progress | file_not_conflicted | unresolved_conflicts_remain | path_unsafe | hook_rejected | identity_not_set`; frontend-only `network | aborted | unknown`.
- MDI icons are auto-subset by scanning source files (`scripts/subset-mdi-font.mjs`) — new `mdi-*` classes need no manual registration.

---

### Task 0: Isolated worktree setup

REQUIRED SUB-SKILL: superpowers:using-git-worktrees.

**Files:** none (environment only).

- [ ] **Step 1: Detect existing isolation and create the worktree**

```cmd
cd /d F:\github\Astrbot
git rev-parse --git-dir
git rev-parse --git-common-dir
```

If the two paths differ you are already in a linked worktree — skip Step 2 and reuse it. Otherwise:

```cmd
cd /d F:\github\Astrbot
git worktree add F:\github\Astrbot-worktrees\merge-conflict-ui -b feat/git-merge-cherrypick-conflict-ui
```

- [ ] **Step 2: Install dashboard deps and verify the test baseline**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm install
pnpm test -- src/composables/__tests__/useSpcodeGitBranches.spec.ts
```

Expected: install succeeds; the one baseline spec passes (all tests green). All later tasks run inside `F:\github\Astrbot-worktrees\merge-conflict-ui`.

> Paths below are written relative to the worktree root `F:\github\Astrbot-worktrees\merge-conflict-ui`. If Step 1 detected an existing worktree, substitute that path everywhere.

---

### Task 1: `parseSpcodeGitMerge.ts` — merge & cherry-pick parsers

**Files:**
- Create: `dashboard/src/composables/parseSpcodeGitMerge.ts`
- Test: `dashboard/src/composables/__tests__/parseSpcodeGitMerge.spec.ts`

**Interfaces:**
- Consumes: nothing (pure).
- Produces (used by Tasks 3, 5, 6): `SpcodeMergeParams`, `SpcodeMergeResult`, `SpcodeCherryPickParams`, `SpcodeCherryPickResult`, `GitOpReasonMeta`, `parseSpcodeGitMerge(raw: unknown): SpcodeMergeResult`, `parseSpcodeCherryPick(raw: unknown): SpcodeCherryPickResult`, `buildMergeBody(p: SpcodeMergeParams): Record<string, unknown>`, `buildCherryPickBody(p: SpcodeCherryPickParams): Record<string, unknown>`, `classifyMergeReason(reason: string | null | undefined): GitOpReasonMeta`, `classifyCherryPickReason(reason: string | null | undefined): GitOpReasonMeta`.

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/composables/__tests__/parseSpcodeGitMerge.spec.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.1
import { describe, it, expect } from "vitest";
import {
  parseSpcodeGitMerge,
  parseSpcodeCherryPick,
  buildMergeBody,
  buildCherryPickBody,
  classifyMergeReason,
  classifyCherryPickReason,
} from "../parseSpcodeGitMerge";

function env(data: Record<string, unknown>) {
  return { status: "ok", data };
}

describe("parseSpcodeGitMerge", () => {
  it("parses a fast-forward merge success", () => {
    const r = parseSpcodeGitMerge(
      env({
        merged: true,
        source: "feature/x",
        merge_sha: "abc123",
        merge_message: "Merge branch 'feature/x'",
        fast_forward: true,
        squash: false,
        files_touched: ["a.ts", "b.ts"],
        reason: null,
        stderr: "",
        elapsed_ms: 12,
      }),
    );
    expect(r).toEqual({
      ok: true,
      merged: true,
      mergeSha: "abc123",
      mergeMessage: "Merge branch 'feature/x'",
      fastForward: true,
      squash: false,
      filesTouched: ["a.ts", "b.ts"],
    });
  });

  it("parses a squash success (merged=false, staged only)", () => {
    const r = parseSpcodeGitMerge(
      env({
        merged: false,
        source: "feature/x",
        squash: true,
        files_touched: ["a.ts"],
        reason: null,
        stderr: "",
        elapsed_ms: 9,
      }),
    );
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.merged).toBe(false);
      expect(r.squash).toBe(true);
      expect(r.mergeSha).toBe("");
    }
  });

  it("parses merge_conflict with conflicted files", () => {
    const r = parseSpcodeGitMerge(
      env({
        merged: false,
        source: "feature/x",
        reason: "merge_conflict",
        conflict: true,
        operation: "merge",
        conflicted_files: ["src/a.ts"],
        stderr: "CONFLICT (content)",
        elapsed_ms: 30,
      }),
    );
    expect(r).toEqual({
      ok: false,
      reason: "merge_conflict",
      conflict: true,
      conflictedFiles: ["src/a.ts"],
      stderr: "CONFLICT (content)",
    });
  });

  it("parses already-up-to-date and dirty rejections", () => {
    const upToDate = parseSpcodeGitMerge(
      env({ merged: false, source: "main", reason: "merge_already_up_to_date", stderr: "", elapsed_ms: 5 }),
    );
    expect(upToDate.ok).toBe(false);
    if (!upToDate.ok) expect(upToDate.conflict).toBe(false);

    const dirty = parseSpcodeGitMerge(
      env({ merged: false, source: "main", reason: "worktree_dirty", stderr: "", elapsed_ms: 5 }),
    );
    expect(dirty.ok).toBe(false);
    if (!dirty.ok) expect(dirty.reason).toBe("worktree_dirty");
  });

  it("throws on malformed envelopes", () => {
    expect(() => parseSpcodeGitMerge(null)).toThrow();
    expect(() => parseSpcodeGitMerge({ status: "error" })).toThrow();
  });
});

describe("parseSpcodeCherryPick", () => {
  it("parses a cherry-pick success", () => {
    const r = parseSpcodeCherryPick(
      env({
        picked: true,
        ref: "deadbeef",
        new_sha: "cafe01",
        original_message: "fix: thing",
        files_touched: ["x.ts"],
        reason: null,
        stderr: "",
        elapsed_ms: 20,
      }),
    );
    expect(r).toEqual({
      ok: true,
      newSha: "cafe01",
      originalMessage: "fix: thing",
      filesTouched: ["x.ts"],
    });
  });

  it("parses cherry_pick_conflict / cherry_pick_empty / commit_not_found", () => {
    const conflict = parseSpcodeCherryPick(
      env({ picked: false, ref: "a", reason: "cherry_pick_conflict", conflict: true, conflicted_files: ["f.ts"], stderr: "", elapsed_ms: 1 }),
    );
    expect(conflict.ok).toBe(false);
    if (!conflict.ok) {
      expect(conflict.reason).toBe("cherry_pick_conflict");
      expect(conflict.conflict).toBe(true);
      expect(conflict.conflictedFiles).toEqual(["f.ts"]);
    }
    const empty = parseSpcodeCherryPick(
      env({ picked: false, ref: "a", reason: "cherry_pick_empty", stderr: "", elapsed_ms: 1 }),
    );
    expect(empty.ok).toBe(false);
    if (!empty.ok) expect(empty.reason).toBe("cherry_pick_empty");
    const notFound = parseSpcodeCherryPick(
      env({ picked: false, ref: "zzz", reason: "commit_not_found", stderr: "", elapsed_ms: 1 }),
    );
    expect(notFound.ok).toBe(false);
    if (!notFound.ok) expect(notFound.reason).toBe("commit_not_found");
  });
});

describe("body builders", () => {
  it("buildMergeBody maps camelCase flags to snake_case", () => {
    expect(
      buildMergeBody({ source: "f/x", noFf: true, message: "msg" }),
    ).toEqual({ source: "f/x", no_ff: true, ff_only: false, squash: false, message: "msg" });
    expect(buildMergeBody({ source: "f/x" })).toEqual({
      source: "f/x", no_ff: false, ff_only: false, squash: false,
    });
  });

  it("buildCherryPickBody omits mainline unless it is a number", () => {
    expect(buildCherryPickBody({ ref: "abc" })).toEqual({ ref: "abc" });
    expect(buildCherryPickBody({ ref: "abc", mainline: 1 })).toEqual({ ref: "abc", mainline: 1 });
  });
});

describe("reason classification", () => {
  it("maps known merge reasons, falls back to unknown", () => {
    expect(classifyMergeReason("merge_conflict").color).toBe("warning");
    expect(classifyMergeReason("merge_already_up_to_date").color).toBe("info");
    expect(classifyMergeReason("worktree_dirty").i18nKey).toContain("merge.error.worktree_dirty");
    expect(classifyMergeReason("bogus").i18nKey).toContain("merge.error.unknown");
    expect(classifyMergeReason(null).i18nKey).toContain("merge.error.unknown");
  });

  it("maps known cherry-pick reasons", () => {
    expect(classifyCherryPickReason("cherry_pick_conflict").color).toBe("warning");
    expect(classifyCherryPickReason("cherry_pick_empty").color).toBe("info");
    expect(classifyCherryPickReason("commit_not_found").i18nKey).toContain("cherryPick.error.commit_not_found");
    expect(classifyCherryPickReason(undefined).i18nKey).toContain("cherryPick.error.unknown");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm test -- src/composables/__tests__/parseSpcodeGitMerge.spec.ts
```

Expected: FAIL — `Failed to resolve import "../parseSpcodeGitMerge"`.

- [ ] **Step 3: Write the implementation**

Create `dashboard/src/composables/parseSpcodeGitMerge.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.1
//
// Pure parsers for POST /spcode/git-merge and POST /spcode/git-cherry-pick
// (spcode plugin v2.22.0). No Vue / no axios — unit-testable in isolation.
//
// Both endpoints share the envelope:
//   { status: "ok", data: { ...fields, reason: string | null, stderr, elapsed_ms } }
// Failure is signalled by reason !== null (HTTP stays 200).

// ── Envelope + coercion helpers ──────────────────────────

function unwrapData(raw: unknown): Record<string, unknown> {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("missing status envelope");
  }
  const env = raw as { status?: unknown; data?: unknown };
  if (env.status !== "ok") {
    throw new Error("unexpected status envelope");
  }
  if (typeof env.data !== "object" || env.data === null) {
    throw new Error("missing data in response");
  }
  return env.data as Record<string, unknown>;
}

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}
function asBoolean(v: unknown): boolean {
  return v === true;
}
function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

// ── Types ────────────────────────────────────────────────

/** POST /spcode/git-merge parameters (UI side, camelCase). */
export interface SpcodeMergeParams {
  source: string;
  noFf?: boolean;
  ffOnly?: boolean;
  squash?: boolean;
  message?: string;
  worktree?: string | null;
}

export type SpcodeMergeResult =
  | {
      ok: true;
      merged: boolean; // false when squash (staged, not committed)
      mergeSha: string; // "" for squash
      mergeMessage: string;
      fastForward: boolean;
      squash: boolean;
      filesTouched: string[];
    }
  | {
      ok: false;
      reason: string;
      conflict: boolean;
      conflictedFiles: string[];
      stderr?: string;
    };

/** POST /spcode/git-cherry-pick parameters (UI side, camelCase). */
export interface SpcodeCherryPickParams {
  ref: string;
  mainline?: number | null;
  worktree?: string | null;
}

export type SpcodeCherryPickResult =
  | { ok: true; newSha: string; originalMessage: string; filesTouched: string[] }
  | {
      ok: false;
      reason: string;
      conflict: boolean;
      conflictedFiles: string[];
      stderr?: string;
    };

// ── Parsers ──────────────────────────────────────────────

export function parseSpcodeGitMerge(raw: unknown): SpcodeMergeResult {
  const d = unwrapData(raw);
  const reason = typeof d.reason === "string" ? d.reason : null;
  if (reason !== null) {
    return {
      ok: false,
      reason,
      conflict: asBoolean(d.conflict),
      conflictedFiles: asStringArray(d.conflicted_files),
      stderr: asString(d.stderr) || undefined,
    };
  }
  return {
    ok: true,
    merged: asBoolean(d.merged),
    mergeSha: asString(d.merge_sha),
    mergeMessage: asString(d.merge_message),
    fastForward: asBoolean(d.fast_forward),
    squash: asBoolean(d.squash),
    filesTouched: asStringArray(d.files_touched),
  };
}

export function parseSpcodeCherryPick(raw: unknown): SpcodeCherryPickResult {
  const d = unwrapData(raw);
  const reason = typeof d.reason === "string" ? d.reason : null;
  if (reason !== null) {
    return {
      ok: false,
      reason,
      conflict: asBoolean(d.conflict),
      conflictedFiles: asStringArray(d.conflicted_files),
      stderr: asString(d.stderr) || undefined,
    };
  }
  return {
    ok: true,
    newSha: asString(d.new_sha),
    originalMessage: asString(d.original_message),
    filesTouched: asStringArray(d.files_touched),
  };
}

// ── Body builders (camelCase params → snake_case wire) ───

export function buildMergeBody(p: SpcodeMergeParams): Record<string, unknown> {
  return {
    source: p.source,
    no_ff: p.noFf ?? false,
    ff_only: p.ffOnly ?? false,
    squash: p.squash ?? false,
    ...(p.message ? { message: p.message } : {}),
  };
}

export function buildCherryPickBody(
  p: SpcodeCherryPickParams,
): Record<string, unknown> {
  return {
    ref: p.ref,
    ...(typeof p.mainline === "number" ? { mainline: p.mainline } : {}),
  };
}

// ── Reason classification (spec §3.4) ────────────────────

export interface GitOpReasonMeta {
  i18nKey: string;
  color: "error" | "warning" | "info";
  withStderr?: boolean;
  withReason?: boolean;
}

const MERGE_PREFIX = "spcodeProjectLoad.diffSidebar.merge";
const CP_PREFIX = "spcodeProjectLoad.diffSidebar.cherryPick";

const MERGE_REASON_CODES: Record<string, GitOpReasonMeta> = {
  merge_conflict: { i18nKey: `${MERGE_PREFIX}.conflictWarning`, color: "warning" },
  merge_already_up_to_date: { i18nKey: `${MERGE_PREFIX}.alreadyUpToDate`, color: "info" },
  worktree_dirty: { i18nKey: `${MERGE_PREFIX}.error.worktree_dirty`, color: "error" },
  operation_in_progress: { i18nKey: `${MERGE_PREFIX}.error.operation_in_progress`, color: "error" },
  invalid_branch: { i18nKey: `${MERGE_PREFIX}.error.invalid_branch`, color: "error" },
  unrelated_histories: { i18nKey: `${MERGE_PREFIX}.error.unrelated_histories`, color: "error" },
  git_error: { i18nKey: `${MERGE_PREFIX}.error.git_error`, color: "error", withStderr: true },
  network: { i18nKey: `${MERGE_PREFIX}.error.network`, color: "error" },
  unknown: { i18nKey: `${MERGE_PREFIX}.error.unknown`, color: "error", withReason: true },
};

// Universal preflight/body reasons share the generic git_error surface.
for (const r of [
  "feature_disabled",
  "no_project_loaded",
  "worktree_invalid",
  "directory_missing",
  "not_a_git_repo",
  "git_unavailable",
  "invalid_body",
  "invalid_param",
]) {
  MERGE_REASON_CODES[r] = { i18nKey: `${MERGE_PREFIX}.error.git_error`, color: "error", withStderr: true };
}

const CP_REASON_CODES: Record<string, GitOpReasonMeta> = {
  cherry_pick_conflict: { i18nKey: `${CP_PREFIX}.conflictWarning`, color: "warning" },
  cherry_pick_empty: { i18nKey: `${CP_PREFIX}.emptyInfo`, color: "info" },
  commit_not_found: { i18nKey: `${CP_PREFIX}.error.commit_not_found`, color: "error" },
  worktree_dirty: { i18nKey: `${CP_PREFIX}.error.worktree_dirty`, color: "error" },
  operation_in_progress: { i18nKey: `${CP_PREFIX}.error.operation_in_progress`, color: "error" },
  git_error: { i18nKey: `${CP_PREFIX}.error.git_error`, color: "error", withStderr: true },
  network: { i18nKey: `${CP_PREFIX}.error.network`, color: "error" },
  unknown: { i18nKey: `${CP_PREFIX}.error.unknown`, color: "error", withReason: true },
};

for (const r of [
  "feature_disabled",
  "no_project_loaded",
  "worktree_invalid",
  "directory_missing",
  "not_a_git_repo",
  "git_unavailable",
  "invalid_body",
  "invalid_param",
]) {
  CP_REASON_CODES[r] = { i18nKey: `${CP_PREFIX}.error.git_error`, color: "error", withStderr: true };
}

export function classifyMergeReason(
  reason: string | null | undefined,
): GitOpReasonMeta {
  if (!reason) return MERGE_REASON_CODES.unknown;
  return MERGE_REASON_CODES[reason] ?? MERGE_REASON_CODES.unknown;
}

export function classifyCherryPickReason(
  reason: string | null | undefined,
): GitOpReasonMeta {
  if (!reason) return CP_REASON_CODES.unknown;
  return CP_REASON_CODES[reason] ?? CP_REASON_CODES.unknown;
}
```

- [ ] **Step 4: Run test to verify it passes**

```cmd
pnpm test -- src/composables/__tests__/parseSpcodeGitMerge.spec.ts
```

Expected: PASS (all 11 tests).

- [ ] **Step 5: Commit**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui
git add dashboard/src/composables/parseSpcodeGitMerge.ts dashboard/src/composables/__tests__/parseSpcodeGitMerge.spec.ts
git commit -m "feat(dashboard): add spcode git merge/cherry-pick response parsers"
```

---

### Task 2: `parseSpcodeGitConflict.ts` — conflict lifecycle parsers

**Files:**
- Create: `dashboard/src/composables/parseSpcodeGitConflict.ts`
- Test: `dashboard/src/composables/__tests__/parseSpcodeGitConflict.spec.ts`

**Interfaces:**
- Consumes: nothing (pure).
- Produces (used by Tasks 4, 7): `ConflictOperation`, `ConflictHunk`, `ConflictedFile`, `ConflictSnapshot`, `SpcodeResolveParams`, `SpcodeResolveResult`, `SpcodeConflictActionResult`, `RemainingConflict`, `parseSpcodeConflictStatus(raw: unknown): ConflictSnapshot` (throws on malformed envelope, like `parseSpcodeGitBranches`), `parseSpcodeConflictResolve(raw: unknown): SpcodeResolveResult`, `parseSpcodeConflictContinue(raw: unknown): SpcodeConflictActionResult`, `parseSpcodeConflictAbort(raw: unknown): SpcodeConflictActionResult`, `buildResolveBody(p: SpcodeResolveParams): Record<string, unknown>`, `classifyConflictReason(reason: string | null | undefined): GitOpReasonMeta` (reuses the `GitOpReasonMeta` shape from Task 1 — redefine locally to keep parsers independent).

Backend wire shapes (from `tools/webapi/git_conflict_*.py`):
- status data: `{ in_conflict, operation: "merge"|"cherry_pick"|"revert"|null, operation_ref, operation_subject, conflicted_files: [{ path, status, hunks: [{ index, start_line, end_line, ours, theirs, base, ours_label, theirs_label }], three_way: { base, ours, theirs }, binary, truncated }], resolved_files: string[], total_conflicted, total_resolved, all_resolved, directory, umo, worktree, reason: null, stderr, elapsed_ms }`. When `in_conflict` is false the list fields are empty and `operation*` are null.
- resolve data ok: `{ resolved, file, mode, hunks_resolved, hunks_total, partial, unresolved_hunks?: [{ index, start_line, end_line }], remaining_conflicts: [{ path, status }], all_resolved, ... }`. Note `hunks_resolved`/`hunks_total` may be `null` for non-hunk modes. Partial hunk resolution returns `resolved: false, partial: true` **with `reason: null`** — it is a 200/success envelope.
- continue ok: `{ continued, operation, commit_sha, commit_message, files_touched, ... }`; continue failure `unresolved_conflicts_remain` carries `remaining_conflicts`.
- abort ok: `{ aborted, operation, operation_ref, ... }`.

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/composables/__tests__/parseSpcodeGitConflict.spec.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.2
import { describe, it, expect } from "vitest";
import {
  parseSpcodeConflictStatus,
  parseSpcodeConflictResolve,
  parseSpcodeConflictContinue,
  parseSpcodeConflictAbort,
  buildResolveBody,
  classifyConflictReason,
} from "../parseSpcodeGitConflict";

function env(data: Record<string, unknown>) {
  return { status: "ok", data };
}

const STATUS_OK = {
  in_conflict: true,
  operation: "merge",
  operation_ref: "abc123",
  operation_subject: "feat: x",
  conflicted_files: [
    {
      path: "src/a.ts",
      status: "UU",
      hunks: [
        {
          index: 0,
          start_line: 3,
          end_line: 9,
          ours: "const x = 1;\n",
          theirs: "const x = 2;\n",
          base: "const x = 0;\n",
          ours_label: "HEAD",
          theirs_label: "feature/x",
        },
        {
          index: 1,
          start_line: 20,
          end_line: 24,
          ours: "a\n",
          theirs: "b\n",
          base: null,
          ours_label: "HEAD",
          theirs_label: "feature/x",
        },
      ],
      three_way: { base: "base-content", ours: "ours-content", theirs: null },
      binary: false,
      truncated: false,
    },
    {
      path: "logo.png",
      status: "AA",
      hunks: [],
      three_way: { base: null, ours: null, theirs: null },
      binary: true,
      truncated: false,
    },
  ],
  resolved_files: ["src/done.ts"],
  total_conflicted: 2,
  total_resolved: 1,
  all_resolved: false,
  directory: "D:/repo",
  umo: "umo",
  worktree: "D:/repo",
  reason: null,
  stderr: "",
  elapsed_ms: 15,
};

describe("parseSpcodeConflictStatus", () => {
  it("parses a full in-conflict snapshot", () => {
    const s = parseSpcodeConflictStatus(env(STATUS_OK));
    expect(s.inConflict).toBe(true);
    expect(s.operation).toBe("merge");
    expect(s.operationRef).toBe("abc123");
    expect(s.operationSubject).toBe("feat: x");
    expect(s.conflictedFiles).toHaveLength(2);
    const f = s.conflictedFiles[0];
    expect(f.path).toBe("src/a.ts");
    expect(f.hunks).toHaveLength(2);
    expect(f.hunks[0]).toMatchObject({
      index: 0, startLine: 3, endLine: 9,
      ours: "const x = 1;\n", theirs: "const x = 2;\n", base: "const x = 0;\n",
      oursLabel: "HEAD", theirsLabel: "feature/x",
    });
    expect(f.hunks[1].base).toBeNull();
    expect(f.threeWay).toEqual({ base: "base-content", ours: "ours-content", theirs: null });
    expect(s.conflictedFiles[1].binary).toBe(true);
    expect(s.resolvedFiles).toEqual(["src/done.ts"]);
    expect(s.totalConflicted).toBe(2);
    expect(s.totalResolved).toBe(1);
    expect(s.allResolved).toBe(false);
  });

  it("parses the no-conflict shape", () => {
    const s = parseSpcodeConflictStatus(
      env({
        in_conflict: false, operation: null, operation_ref: null, operation_subject: null,
        conflicted_files: [], resolved_files: [], total_conflicted: 0, total_resolved: 0,
        all_resolved: true, directory: "D:/repo", umo: "umo", worktree: "D:/repo",
        reason: null, stderr: "", elapsed_ms: 3,
      }),
    );
    expect(s).toMatchObject({
      inConflict: false, operation: null, operationRef: null, operationSubject: null,
      conflictedFiles: [], resolvedFiles: [], totalConflicted: 0, totalResolved: 0,
      allResolved: true,
    });
  });

  it("maps unknown operation strings to null", () => {
    const s = parseSpcodeConflictStatus(env({ ...STATUS_OK, operation: "rebase" }));
    expect(s.operation).toBeNull();
  });

  it("throws on malformed envelopes", () => {
    expect(() => parseSpcodeConflictStatus(undefined)).toThrow();
    expect(() => parseSpcodeConflictStatus({ status: "ok" })).toThrow();
  });
});

describe("parseSpcodeConflictResolve", () => {
  it("parses whole-file success", () => {
    const r = parseSpcodeConflictResolve(
      env({
        resolved: true, file: "src/a.ts", mode: "whole_file",
        hunks_resolved: null, hunks_total: null, partial: false,
        remaining_conflicts: [], all_resolved: true,
        reason: null, stderr: "", elapsed_ms: 8,
      }),
    );
    expect(r).toMatchObject({
      ok: true, resolved: true, partial: false,
      hunksResolved: null, hunksTotal: null, remainingConflicts: [], allResolved: true,
    });
  });

  it("parses partial hunk resolution (success envelope, resolved=false)", () => {
    const r = parseSpcodeConflictResolve(
      env({
        resolved: false, file: "src/a.ts", mode: "hunks",
        hunks_resolved: 1, hunks_total: 2, partial: true,
        unresolved_hunks: [{ index: 1, start_line: 20, end_line: 24 }],
        remaining_conflicts: [{ path: "src/a.ts", status: "UU" }],
        all_resolved: false, reason: null, stderr: "", elapsed_ms: 8,
      }),
    );
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.resolved).toBe(false);
      expect(r.partial).toBe(true);
      expect(r.hunksResolved).toBe(1);
      expect(r.hunksTotal).toBe(2);
      expect(r.unresolvedHunks).toEqual([{ index: 1, startLine: 20, endLine: 24 }]);
      expect(r.allResolved).toBe(false);
    }
  });

  it("parses failures", () => {
    const r = parseSpcodeConflictResolve(
      env({ resolved: false, reason: "file_not_conflicted", file: "x.ts", stderr: "", elapsed_ms: 2 }),
    );
    expect(r).toEqual({ ok: false, reason: "file_not_conflicted", stderr: undefined });
  });
});

describe("parseSpcodeConflictContinue / parseSpcodeConflictAbort", () => {
  it("parses continue success", () => {
    const r = parseSpcodeConflictContinue(
      env({
        continued: true, operation: "merge", commit_sha: "feed99",
        commit_message: "Merge branch 'f/x'", files_touched: ["a.ts"],
        reason: null, stderr: "", elapsed_ms: 40,
      }),
    );
    expect(r).toEqual({
      ok: true, operation: "merge", commitSha: "feed99", commitMessage: "Merge branch 'f/x'",
    });
  });

  it("parses continue blocked by unresolved conflicts", () => {
    const r = parseSpcodeConflictContinue(
      env({
        continued: false, operation: "merge", reason: "unresolved_conflicts_remain",
        remaining_conflicts: [{ path: "a.ts", status: "UU" }], stderr: "", elapsed_ms: 4,
      }),
    );
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toBe("unresolved_conflicts_remain");
      expect(r.remainingConflicts).toEqual([{ path: "a.ts", status: "UU" }]);
    }
  });

  it("parses abort success and no-conflict failure", () => {
    const ok = parseSpcodeConflictAbort(
      env({ aborted: true, operation: "cherry_pick", operation_ref: "abc", reason: null, stderr: "", elapsed_ms: 6 }),
    );
    expect(ok).toEqual({ ok: true, operation: "cherry_pick", commitSha: "", commitMessage: "" });
    const fail = parseSpcodeConflictAbort(
      env({ aborted: false, reason: "no_conflict_in_progress", stderr: "", elapsed_ms: 2 }),
    );
    expect(fail.ok).toBe(false);
  });
});

describe("buildResolveBody", () => {
  it("serializes all four modes", () => {
    expect(buildResolveBody({ mode: "hunks", file: "a.ts", hunks: [{ index: 0, choice: "ours" }] }))
      .toEqual({ file: "a.ts", hunks: [{ index: 0, choice: "ours" }] });
    expect(buildResolveBody({ mode: "whole", file: "a.ts", resolution: "theirs" }))
      .toEqual({ file: "a.ts", resolution: "theirs" });
    expect(buildResolveBody({ mode: "custom", file: "a.ts", content: "hello" }))
      .toEqual({ file: "a.ts", resolution: "custom", content: "hello" });
    expect(buildResolveBody({ mode: "all", resolution: "ours" }))
      .toEqual({ all: true, resolution: "ours" });
  });
});

describe("classifyConflictReason", () => {
  it("maps known reasons, falls back to unknown", () => {
    expect(classifyConflictReason("unresolved_conflicts_remain").i18nKey)
      .toContain("conflict.error.unresolved_conflicts_remain");
    expect(classifyConflictReason("no_conflict_in_progress").color).toBe("warning");
    expect(classifyConflictReason("bogus").i18nKey).toContain("conflict.error.unknown");
    expect(classifyConflictReason(null).i18nKey).toContain("conflict.error.unknown");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm test -- src/composables/__tests__/parseSpcodeGitConflict.spec.ts
```

Expected: FAIL — `Failed to resolve import "../parseSpcodeGitConflict"`.

- [ ] **Step 3: Write the implementation**

Create `dashboard/src/composables/parseSpcodeGitConflict.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.2
//
// Pure parsers for the conflict lifecycle endpoints (spcode plugin v2.22.0):
//   GET  /spcode/git-conflict-status
//   POST /spcode/git-conflict-resolve
//   POST /spcode/git-conflict-continue
//   POST /spcode/git-conflict-abort
// No Vue / no axios — unit-testable in isolation.

function unwrapData(raw: unknown): Record<string, unknown> {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("missing status envelope");
  }
  const env = raw as { status?: unknown; data?: unknown };
  if (env.status !== "ok") {
    throw new Error("unexpected status envelope");
  }
  if (typeof env.data !== "object" || env.data === null) {
    throw new Error("missing data in response");
  }
  return env.data as Record<string, unknown>;
}

function asString(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}
function asStringOrNull(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function asNumber(v: unknown, fallback = 0): number {
  return typeof v === "number" ? v : fallback;
}
function asNumberOrNull(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}
function asBoolean(v: unknown): boolean {
  return v === true;
}
function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

// ── Types ────────────────────────────────────────────────

export type ConflictOperation = "merge" | "cherry_pick" | "revert";

export interface ConflictHunk {
  index: number;
  startLine: number;
  endLine: number;
  ours: string;
  theirs: string;
  base: string | null; // non-null only with diff3 markers
  oursLabel: string;
  theirsLabel: string;
}

export interface ConflictedFile {
  path: string;
  status: string; // e.g. "UU", "AA", "DU"
  hunks: ConflictHunk[]; // [] for binary/truncated/beyond-cap files
  threeWay: { base: string | null; ours: string | null; theirs: string | null };
  binary: boolean;
  truncated: boolean;
}

export interface ConflictSnapshot {
  inConflict: boolean;
  operation: ConflictOperation | null;
  operationRef: string | null;
  operationSubject: string | null;
  conflictedFiles: ConflictedFile[];
  resolvedFiles: string[];
  totalConflicted: number;
  totalResolved: number;
  allResolved: boolean;
  directory: string;
}

export interface RemainingConflict {
  path: string;
  status: string;
}

/** POST /spcode/git-conflict-resolve — four mutually exclusive modes. */
export type SpcodeResolveParams =
  | { mode: "hunks"; file: string; hunks: { index: number; choice: "ours" | "theirs" | "base" }[]; worktree?: string | null }
  | { mode: "whole"; file: string; resolution: "ours" | "theirs"; worktree?: string | null }
  | { mode: "custom"; file: string; content: string; worktree?: string | null }
  | { mode: "all"; resolution: "ours" | "theirs"; worktree?: string | null };

export type SpcodeResolveResult =
  | {
      ok: true;
      resolved: boolean;
      partial: boolean;
      hunksResolved: number | null;
      hunksTotal: number | null;
      unresolvedHunks: { index: number; startLine: number; endLine: number }[];
      remainingConflicts: RemainingConflict[];
      allResolved: boolean;
    }
  | { ok: false; reason: string; stderr?: string };

export type SpcodeConflictActionResult =
  | { ok: true; operation: ConflictOperation | null; commitSha: string; commitMessage: string }
  | { ok: false; reason: string; stderr?: string; remainingConflicts?: RemainingConflict[] };

// ── Field parsers ────────────────────────────────────────

function toOperation(v: unknown): ConflictOperation | null {
  return v === "merge" || v === "cherry_pick" || v === "revert" ? v : null;
}

function toHunks(v: unknown): ConflictHunk[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((h): h is Record<string, unknown> => typeof h === "object" && h !== null)
    .map((h) => ({
      index: asNumber(h.index),
      startLine: asNumber(h.start_line),
      endLine: asNumber(h.end_line),
      ours: asString(h.ours),
      theirs: asString(h.theirs),
      base: asStringOrNull(h.base),
      oursLabel: asString(h.ours_label),
      theirsLabel: asString(h.theirs_label),
    }));
}

function toConflictedFiles(v: unknown): ConflictedFile[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((f): f is Record<string, unknown> => typeof f === "object" && f !== null)
    .map((f) => {
      const tw = (typeof f.three_way === "object" && f.three_way !== null
        ? f.three_way
        : {}) as Record<string, unknown>;
      return {
        path: asString(f.path),
        status: asString(f.status),
        hunks: toHunks(f.hunks),
        threeWay: {
          base: asStringOrNull(tw.base),
          ours: asStringOrNull(tw.ours),
          theirs: asStringOrNull(tw.theirs),
        },
        binary: asBoolean(f.binary),
        truncated: asBoolean(f.truncated),
      };
    });
}

function toRemainingConflicts(v: unknown): RemainingConflict[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((c): c is Record<string, unknown> => typeof c === "object" && c !== null)
    .map((c) => ({ path: asString(c.path), status: asString(c.status) }));
}

function toUnresolvedHunks(v: unknown): { index: number; startLine: number; endLine: number }[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((h): h is Record<string, unknown> => typeof h === "object" && h !== null)
    .map((h) => ({
      index: asNumber(h.index),
      startLine: asNumber(h.start_line),
      endLine: asNumber(h.end_line),
    }));
}

// ── Endpoint parsers ─────────────────────────────────────

/** GET /spcode/git-conflict-status. Throws on malformed envelope. */
export function parseSpcodeConflictStatus(raw: unknown): ConflictSnapshot {
  const d = unwrapData(raw);
  return {
    inConflict: asBoolean(d.in_conflict),
    operation: toOperation(d.operation),
    operationRef: asStringOrNull(d.operation_ref),
    operationSubject: asStringOrNull(d.operation_subject),
    conflictedFiles: toConflictedFiles(d.conflicted_files),
    resolvedFiles: asStringArray(d.resolved_files),
    totalConflicted: asNumber(d.total_conflicted),
    totalResolved: asNumber(d.total_resolved),
    allResolved: asBoolean(d.all_resolved),
    directory: asString(d.directory),
  };
}

export function parseSpcodeConflictResolve(raw: unknown): SpcodeResolveResult {
  const d = unwrapData(raw);
  const reason = typeof d.reason === "string" ? d.reason : null;
  if (reason !== null) {
    return { ok: false, reason, stderr: asString(d.stderr) || undefined };
  }
  return {
    ok: true,
    resolved: asBoolean(d.resolved),
    partial: asBoolean(d.partial),
    hunksResolved: asNumberOrNull(d.hunks_resolved),
    hunksTotal: asNumberOrNull(d.hunks_total),
    unresolvedHunks: toUnresolvedHunks(d.unresolved_hunks),
    remainingConflicts: toRemainingConflicts(d.remaining_conflicts),
    allResolved: asBoolean(d.all_resolved),
  };
}

function parseAction(raw: unknown): SpcodeConflictActionResult {
  const d = unwrapData(raw);
  const reason = typeof d.reason === "string" ? d.reason : null;
  if (reason !== null) {
    return {
      ok: false,
      reason,
      stderr: asString(d.stderr) || undefined,
      remainingConflicts: toRemainingConflicts(d.remaining_conflicts),
    };
  }
  return {
    ok: true,
    operation: toOperation(d.operation),
    commitSha: asString(d.commit_sha),
    commitMessage: asString(d.commit_message),
  };
}

export const parseSpcodeConflictContinue = parseAction;
export const parseSpcodeConflictAbort = parseAction;

// ── Body builder ─────────────────────────────────────────

export function buildResolveBody(p: SpcodeResolveParams): Record<string, unknown> {
  switch (p.mode) {
    case "hunks":
      return { file: p.file, hunks: p.hunks };
    case "whole":
      return { file: p.file, resolution: p.resolution };
    case "custom":
      return { file: p.file, resolution: "custom", content: p.content };
    case "all":
      return { all: true, resolution: p.resolution };
  }
}

// ── Reason classification (spec §3.4) ────────────────────

export interface GitOpReasonMeta {
  i18nKey: string;
  color: "error" | "warning" | "info";
  withStderr?: boolean;
  withReason?: boolean;
}

const PREFIX = "spcodeProjectLoad.diffSidebar.conflict";

const CONFLICT_REASON_CODES: Record<string, GitOpReasonMeta> = {
  no_conflict_in_progress: { i18nKey: `${PREFIX}.error.no_conflict_in_progress`, color: "warning" },
  file_not_conflicted: { i18nKey: `${PREFIX}.error.file_not_conflicted`, color: "error" },
  unresolved_conflicts_remain: { i18nKey: `${PREFIX}.error.unresolved_conflicts_remain`, color: "warning" },
  path_unsafe: { i18nKey: `${PREFIX}.error.path_unsafe`, color: "error" },
  invalid_param: { i18nKey: `${PREFIX}.error.invalid_param`, color: "error", withStderr: true },
  hook_rejected: { i18nKey: `${PREFIX}.error.hook_rejected`, color: "error", withStderr: true },
  identity_not_set: { i18nKey: `${PREFIX}.error.identity_not_set`, color: "error" },
  git_error: { i18nKey: `${PREFIX}.error.git_error`, color: "error", withStderr: true },
  network: { i18nKey: `${PREFIX}.error.network`, color: "error" },
  unknown: { i18nKey: `${PREFIX}.error.unknown`, color: "error", withReason: true },
};

for (const r of [
  "feature_disabled",
  "no_project_loaded",
  "worktree_invalid",
  "directory_missing",
  "not_a_git_repo",
  "git_unavailable",
  "invalid_body",
]) {
  CONFLICT_REASON_CODES[r] = { i18nKey: `${PREFIX}.error.git_error`, color: "error", withStderr: true };
}

export function classifyConflictReason(
  reason: string | null | undefined,
): GitOpReasonMeta {
  if (!reason) return CONFLICT_REASON_CODES.unknown;
  return CONFLICT_REASON_CODES[reason] ?? CONFLICT_REASON_CODES.unknown;
}
```

- [ ] **Step 4: Run test to verify it passes**

```cmd
pnpm test -- src/composables/__tests__/parseSpcodeGitConflict.spec.ts
```

Expected: PASS (all 13 tests).

- [ ] **Step 5: Commit**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui
git add dashboard/src/composables/parseSpcodeGitConflict.ts dashboard/src/composables/__tests__/parseSpcodeGitConflict.spec.ts
git commit -m "feat(dashboard): add spcode git conflict lifecycle parsers"
```

---

### Task 3: `useSpcodeGitMerge.ts` — merge & cherry-pick mutations

**Files:**
- Create: `dashboard/src/composables/useSpcodeGitMerge.ts`
- Test: `dashboard/src/composables/__tests__/useSpcodeGitMerge.spec.ts`

**Interfaces:**
- Consumes: `parseSpcodeGitMerge`, `parseSpcodeCherryPick`, `buildMergeBody`, `buildCherryPickBody`, `SpcodeMergeParams`, `SpcodeMergeResult`, `SpcodeCherryPickParams`, `SpcodeCherryPickResult` (Task 1); `pluginExtensionApi` (`@/api/v1`); `useSpcodeProjectStatus`.
- Produces (used by Tasks 5, 6): `useSpcodeGitMerge(): UseSpcodeGitMerge` where `UseSpcodeGitMerge = { isMerging: Ref<boolean>; isCherryPicking: Ref<boolean>; merge(p: SpcodeMergeParams): Promise<SpcodeMergeResult>; cherryPick(p: SpcodeCherryPickParams): Promise<SpcodeCherryPickResult>; dispose(): void }`.

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/composables/__tests__/useSpcodeGitMerge.spec.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.3
import { describe, it, expect, vi, beforeEach } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

vi.mock("../useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: { value: { umo: "umo-test", directory: "D:/repo", loaded: true } },
    refresh: vi.fn(),
  }),
}));

const mockPost = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

import { useSpcodeGitMerge } from "../useSpcodeGitMerge";

function withSetup<T>(fn: () => T): T {
  let result: T;
  const Comp = defineComponent({
    setup() {
      result = fn();
      return () => h("div");
    },
  });
  mount(Comp);
  return result!;
}

describe("useSpcodeGitMerge", () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it("merge() posts snake_case body and parses success", async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: "ok",
        data: {
          merged: true, source: "f/x", merge_sha: "abc", merge_message: "m",
          fast_forward: false, squash: false, files_touched: ["a.ts"],
          reason: null, stderr: "", elapsed_ms: 5,
        },
      },
    });
    const { merge, isMerging } = withSetup(() => useSpcodeGitMerge());
    const p = merge({ source: "f/x", noFf: true, worktree: "D:/repo" });
    expect(isMerging.value).toBe(true);
    const r = await p;
    expect(isMerging.value).toBe(false);
    expect(r.ok).toBe(true);
    const [endpoint, body] = mockPost.mock.calls[0];
    expect(endpoint).toBe("spcode/git-merge");
    expect(body).toMatchObject({
      umo: "umo-test", worktree: "D:/repo",
      source: "f/x", no_ff: true, ff_only: false, squash: false,
    });
    expect(body).not.toHaveProperty("message");
  });

  it("merge() surfaces conflict failures with conflicted files", async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: "ok",
        data: {
          merged: false, source: "f/x", reason: "merge_conflict", conflict: true,
          conflicted_files: ["a.ts"], stderr: "CONFLICT", elapsed_ms: 5,
        },
      },
    });
    const { merge } = withSetup(() => useSpcodeGitMerge());
    const r = await merge({ source: "f/x" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toBe("merge_conflict");
      expect(r.conflict).toBe(true);
      expect(r.conflictedFiles).toEqual(["a.ts"]);
    }
  });

  it("cherryPick() posts ref + mainline and parses success", async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: "ok",
        data: {
          picked: true, ref: "abc", new_sha: "def", original_message: "s",
          files_touched: [], reason: null, stderr: "", elapsed_ms: 5,
        },
      },
    });
    const { cherryPick } = withSetup(() => useSpcodeGitMerge());
    const r = await cherryPick({ ref: "abc", mainline: 1 });
    expect(r.ok).toBe(true);
    const [endpoint, body] = mockPost.mock.calls[0];
    expect(endpoint).toBe("spcode/git-cherry-pick");
    expect(body).toMatchObject({ umo: "umo-test", ref: "abc", mainline: 1 });
    expect(body).not.toHaveProperty("worktree");
  });

  it("maps network errors to reason network", async () => {
    mockPost.mockRejectedValueOnce({ code: "ERR_NETWORK", message: "Network Error" });
    const { merge } = withSetup(() => useSpcodeGitMerge());
    const r = await merge({ source: "f/x" });
    expect(r).toMatchObject({ ok: false, reason: "network" });
  });

  it("returns aborted after dispose()", async () => {
    const api = withSetup(() => useSpcodeGitMerge());
    api.dispose();
    const r = await api.merge({ source: "f/x" });
    expect(r).toMatchObject({ ok: false, reason: "aborted" });
    expect(mockPost).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm test -- src/composables/__tests__/useSpcodeGitMerge.spec.ts
```

Expected: FAIL — `Failed to resolve import "../useSpcodeGitMerge"`.

- [ ] **Step 3: Write the implementation**

Create `dashboard/src/composables/useSpcodeGitMerge.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.3
//
// Vue composable wrapping POST /spcode/git-merge and
// POST /spcode/git-cherry-pick. Stateless-mutation pattern mirroring
// useSpcodeGitCommit: one in-flight call per operation, a busy ref per
// operation, reason codes surfaced verbatim for the caller to classify.

import { ref, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import {
  parseSpcodeGitMerge,
  parseSpcodeCherryPick,
  buildMergeBody,
  buildCherryPickBody,
  type SpcodeMergeParams,
  type SpcodeMergeResult,
  type SpcodeCherryPickParams,
  type SpcodeCherryPickResult,
} from "./parseSpcodeGitMerge";

export interface UseSpcodeGitMerge {
  isMerging: Ref<boolean>;
  isCherryPicking: Ref<boolean>;
  merge: (params: SpcodeMergeParams) => Promise<SpcodeMergeResult>;
  cherryPick: (params: SpcodeCherryPickParams) => Promise<SpcodeCherryPickResult>;
  dispose: () => void;
}

function classifyThrown(err: unknown): string {
  if ((err as { name?: string })?.name === "CanceledError") return "aborted";
  const anyErr = err as { code?: string; message?: string };
  if (anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")) {
    return "network";
  }
  return "unknown";
}

export function useSpcodeGitMerge(): UseSpcodeGitMerge {
  const isMerging = ref(false);
  const isCherryPicking = ref(false);
  const spcodeStatus = useSpcodeProjectStatus();
  let mergeAbort: AbortController | null = null;
  let pickAbort: AbortController | null = null;
  let isMounted = true;

  async function merge(params: SpcodeMergeParams): Promise<SpcodeMergeResult> {
    if (!isMounted) return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
    const umo = spcodeStatus.status.value.umo;
    if (!umo) return { ok: false, reason: "no_project_loaded", conflict: false, conflictedFiles: [] };
    mergeAbort?.abort();
    mergeAbort = new AbortController();
    isMerging.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-merge",
        {
          umo,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...buildMergeBody(params),
        },
        { signal: mergeAbort.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      return parseSpcodeGitMerge(resp.data);
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      const reason = classifyThrown(err);
      return { ok: false, reason, conflict: false, conflictedFiles: [] };
    } finally {
      if (isMounted) isMerging.value = false;
    }
  }

  async function cherryPick(
    params: SpcodeCherryPickParams,
  ): Promise<SpcodeCherryPickResult> {
    if (!isMounted) return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
    const umo = spcodeStatus.status.value.umo;
    if (!umo) return { ok: false, reason: "no_project_loaded", conflict: false, conflictedFiles: [] };
    pickAbort?.abort();
    pickAbort = new AbortController();
    isCherryPicking.value = true;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-cherry-pick",
        {
          umo,
          ...(params.worktree ? { worktree: params.worktree } : {}),
          ...buildCherryPickBody(params),
        },
        { signal: pickAbort.signal },
      );
      if (!isMounted) return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      return parseSpcodeCherryPick(resp.data);
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted", conflict: false, conflictedFiles: [] };
      const reason = classifyThrown(err);
      return { ok: false, reason, conflict: false, conflictedFiles: [] };
    } finally {
      if (isMounted) isCherryPicking.value = false;
    }
  }

  function dispose(): void {
    isMounted = false;
    mergeAbort?.abort();
    mergeAbort = null;
    pickAbort?.abort();
    pickAbort = null;
  }

  return { isMerging, isCherryPicking, merge, cherryPick, dispose };
}
```

Note: the no-`umo` early return uses reason `no_project_loaded`, which Task 1's `classifyMergeReason`/`classifyCherryPickReason` already map.

- [ ] **Step 4: Run test to verify it passes**

```cmd
pnpm test -- src/composables/__tests__/useSpcodeGitMerge.spec.ts
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui
git add dashboard/src/composables/useSpcodeGitMerge.ts dashboard/src/composables/__tests__/useSpcodeGitMerge.spec.ts
git commit -m "feat(dashboard): add useSpcodeGitMerge composable for merge/cherry-pick"
```

---

### Task 4: `useSpcodeGitConflict.ts` — conflict status polling + lifecycle mutations

**Files:**
- Create: `dashboard/src/composables/useSpcodeGitConflict.ts`
- Test: `dashboard/src/composables/__tests__/useSpcodeGitConflict.spec.ts`

**Interfaces:**
- Consumes: everything from Task 2; `pluginExtensionApi`; `useSpcodeProjectStatus`.
- Produces (used by Tasks 5, 6, 7): `ConflictFetchState`, `UseSpcodeGitConflict`, `useSpcodeGitConflict(worktreeRef: MaybeRef<string | null>): UseSpcodeGitConflict` with members `state`, `refresh()`, `startPolling(intervalMs?)`, `stopPolling()`, `resolve(p: SpcodeResolveParams)`, `continueOp(message?: string)`, `abort()`, `dispose()` (exact shapes in code below).

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/composables/__tests__/useSpcodeGitConflict.spec.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.3
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { defineComponent, h, ref } from "vue";
import { mount } from "@vue/test-utils";

vi.mock("../useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: { value: { umo: "umo-test", directory: "D:/repo", loaded: true } },
    refresh: vi.fn(),
  }),
}));

const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock("@/api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

import { useSpcodeGitConflict } from "../useSpcodeGitConflict";

const NO_CONFLICT = {
  in_conflict: false, operation: null, operation_ref: null, operation_subject: null,
  conflicted_files: [], resolved_files: [], total_conflicted: 0, total_resolved: 0,
  all_resolved: true, directory: "D:/repo", umo: "umo-test", worktree: "D:/repo",
  reason: null, stderr: "", elapsed_ms: 2,
};

const IN_CONFLICT = {
  ...NO_CONFLICT,
  in_conflict: true, operation: "merge", operation_ref: "abc", operation_subject: "s",
  conflicted_files: [
    {
      path: "a.ts", status: "UU", hunks: [],
      three_way: { base: null, ours: "x", theirs: "y" },
      binary: false, truncated: false,
    },
  ],
  total_conflicted: 1, all_resolved: false,
};

function okGet(data: Record<string, unknown>) {
  return { status: 200, data: { status: "ok", data }, headers: { etag: 'W/"e1"' } };
}

function withSetup<T>(fn: () => T): { result: T; unmount: () => void } {
  let result: T;
  const Comp = defineComponent({
    setup() {
      result = fn();
      return () => h("div");
    },
  });
  const wrapper = mount(Comp);
  return { result: result!, unmount: () => wrapper.unmount() };
}

describe("useSpcodeGitConflict", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("refresh() transitions idle → ok and parses snapshot", async () => {
    mockGet.mockResolvedValueOnce(okGet(IN_CONFLICT));
    const { result } = withSetup(() => useSpcodeGitConflict(ref("D:/repo")));
    expect(result.state.value.kind).toBe("idle");
    await result.refresh();
    expect(result.state.value.kind).toBe("ok");
    if (result.state.value.kind === "ok") {
      expect(result.state.value.snapshot.inConflict).toBe(true);
      expect(result.state.value.snapshot.operation).toBe("merge");
      expect(result.state.value.snapshot.conflictedFiles[0].threeWay.ours).toBe("x");
    }
    const [endpoint, cfg] = mockGet.mock.calls[0];
    expect(endpoint).toBe("spcode/git-conflict-status");
    expect(cfg.params).toMatchObject({ umo: "umo-test", worktree: "D:/repo" });
  });

  it("304 replays the cached snapshot with notModified", async () => {
    mockGet.mockResolvedValueOnce(okGet(IN_CONFLICT));
    const { result } = withSetup(() => useSpcodeGitConflict(ref(null)));
    await result.refresh();
    mockGet.mockResolvedValueOnce({ status: 304, data: null, headers: {} });
    await result.refresh();
    if (result.state.value.kind === "ok") {
      expect(result.state.value.notModified).toBe(true);
      expect(result.state.value.snapshot.inConflict).toBe(true);
    } else {
      throw new Error("expected ok state");
    }
  });

  it("error keeps previousSnapshot", async () => {
    mockGet.mockResolvedValueOnce(okGet(NO_CONFLICT));
    const { result } = withSetup(() => useSpcodeGitConflict(ref(null)));
    await result.refresh();
    mockGet.mockRejectedValueOnce({ code: "ERR_NETWORK", message: "Network Error" });
    await result.refresh();
    expect(result.state.value.kind).toBe("error");
    if (result.state.value.kind === "error") {
      expect(result.state.value.reason).toBe("network");
      expect(result.state.value.previousSnapshot?.inConflict).toBe(false);
    }
  });

  it("startPolling fires refresh on the interval", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(okGet(NO_CONFLICT));
    const { result, unmount } = withSetup(() => useSpcodeGitConflict(ref(null)));
    result.startPolling(30_000);
    expect(mockGet).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(mockGet).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(mockGet).toHaveBeenCalledTimes(2);
    result.stopPolling();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(mockGet).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("resolve() posts the serialized body and refreshes state", async () => {
    mockGet.mockResolvedValue(okGet(IN_CONFLICT));
    mockPost.mockResolvedValueOnce({
      data: {
        status: "ok",
        data: {
          resolved: true, file: "a.ts", mode: "whole_file",
          hunks_resolved: null, hunks_total: null, partial: false,
          remaining_conflicts: [], all_resolved: true,
          reason: null, stderr: "", elapsed_ms: 4,
        },
      },
    });
    const { result } = withSetup(() => useSpcodeGitConflict(ref("D:/repo")));
    const r = await result.resolve({ mode: "whole", file: "a.ts", resolution: "ours" });
    expect(r.ok).toBe(true);
    const [endpoint, body] = mockPost.mock.calls[0];
    expect(endpoint).toBe("spcode/git-conflict-resolve");
    expect(body).toMatchObject({ umo: "umo-test", worktree: "D:/repo", file: "a.ts", resolution: "ours" });
    // success triggers an immediate status refresh
    expect(mockGet).toHaveBeenCalledTimes(1);
  });

  it("continueOp() posts optional message; abort() posts empty body", async () => {
    mockGet.mockResolvedValue(okGet(NO_CONFLICT));
    mockPost
      .mockResolvedValueOnce({
        data: { status: "ok", data: { continued: true, operation: "merge", commit_sha: "c1", commit_message: "m", files_touched: [], reason: null, stderr: "", elapsed_ms: 4 } },
      })
      .mockResolvedValueOnce({
        data: { status: "ok", data: { aborted: true, operation: "merge", operation_ref: "abc", reason: null, stderr: "", elapsed_ms: 4 } },
      });
    const { result } = withSetup(() => useSpcodeGitConflict(ref(null)));
    const rc = await result.continueOp("my message");
    expect(rc).toMatchObject({ ok: true, operation: "merge", commitSha: "c1" });
    expect(mockPost.mock.calls[0][0]).toBe("spcode/git-conflict-continue");
    expect(mockPost.mock.calls[0][1]).toMatchObject({ umo: "umo-test", message: "my message" });
    const ra = await result.abort();
    expect(ra.ok).toBe(true);
    expect(mockPost.mock.calls[1][0]).toBe("spcode/git-conflict-abort");
    expect(mockPost.mock.calls[1][1]).not.toHaveProperty("message");
  });

  it("mutations return aborted after dispose()", async () => {
    const { result } = withSetup(() => useSpcodeGitConflict(ref(null)));
    result.dispose();
    const r = await result.resolve({ mode: "all", resolution: "ours" });
    expect(r).toMatchObject({ ok: false, reason: "aborted" });
    expect(mockPost).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm test -- src/composables/__tests__/useSpcodeGitConflict.spec.ts
```

Expected: FAIL — `Failed to resolve import "../useSpcodeGitConflict"`.

- [ ] **Step 3: Write the implementation**

Create `dashboard/src/composables/useSpcodeGitConflict.ts`:

```ts
// Author: elecvoid243 @ 2026-08-01
// Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §3.3
//
// Vue composable for the conflict lifecycle. Read path mirrors
// useSpcodeGitBranches (state machine / ETag / 30 s polling / dispose);
// mutations (resolve / continue / abort) are single-flight and trigger an
// immediate refresh on success so the banner stays truthful.

import { ref, watch, toValue, type MaybeRef, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import {
  parseSpcodeConflictStatus,
  parseSpcodeConflictResolve,
  parseSpcodeConflictContinue,
  parseSpcodeConflictAbort,
  buildResolveBody,
  type ConflictSnapshot,
  type SpcodeResolveParams,
  type SpcodeResolveResult,
  type SpcodeConflictActionResult,
} from "./parseSpcodeGitConflict";

export type ConflictFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; snapshot: ConflictSnapshot; notModified?: boolean }
  | { kind: "error"; reason: string; previousSnapshot?: ConflictSnapshot };

export interface UseSpcodeGitConflict {
  state: Ref<ConflictFetchState>;
  refresh: () => Promise<void>;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  resolve: (params: SpcodeResolveParams) => Promise<SpcodeResolveResult>;
  continueOp: (message?: string) => Promise<SpcodeConflictActionResult>;
  abort: () => Promise<SpcodeConflictActionResult>;
  dispose: () => void;
}

const DEFAULT_POLL_MS = 30_000;

function classifyThrown(err: unknown): string {
  if ((err as { name?: string })?.name === "CanceledError") return "aborted";
  const anyErr = err as { code?: string; message?: string };
  if (anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")) {
    return "network";
  }
  return "unknown";
}

export function useSpcodeGitConflict(
  worktreeRef: MaybeRef<string | null> = null,
): UseSpcodeGitConflict {
  const state = ref<ConflictFetchState>({ kind: "idle" });
  const spcodeStatus = useSpcodeProjectStatus();
  let abortController: AbortController | null = null;
  let mutationAbort: AbortController | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let isMounted = true;
  const etagMap = new Map<string, string>();
  const prevSnapshotMap = new Map<string, ConflictSnapshot>();

  function etagKey(): string {
    const umo = spcodeStatus.status.value.umo ?? "null";
    return `conflict|${umo}|${toValue(worktreeRef) ?? "null"}`;
  }

  async function refresh(): Promise<void> {
    if (!isMounted) return;
    const umo = spcodeStatus.status.value.umo ?? null;
    if (!umo) {
      state.value = { kind: "error", reason: "no_project_loaded" };
      return;
    }
    abortController?.abort();
    abortController = new AbortController();
    const isFirst = state.value.kind !== "ok";
    if (isFirst) state.value = { kind: "loading" };
    const key = etagKey();
    const etag = etagMap.get(key);
    const worktree = toValue(worktreeRef);
    try {
      const resp = await pluginExtensionApi.get<unknown>(
        "spcode/git-conflict-status",
        {
          params: { umo, ...(worktree ? { worktree } : {}) },
          headers: etag ? { "If-None-Match": etag } : {},
          validateStatus: (s) => (s >= 200 && s < 300) || s === 304,
          signal: abortController.signal,
        },
      );
      if (!isMounted) return;
      if (resp.status === 304) {
        const cached = prevSnapshotMap.get(key);
        if (cached) {
          state.value = { kind: "ok", snapshot: cached, notModified: true };
        }
        return;
      }
      const snap = parseSpcodeConflictStatus(resp.data);
      prevSnapshotMap.set(key, snap);
      const headers = resp.headers as Record<string, string> | undefined;
      const newEtag = headers?.["etag"] ?? headers?.["ETag"];
      if (newEtag) etagMap.set(key, newEtag);
      state.value = { kind: "ok", snapshot: snap, notModified: false };
    } catch (err) {
      if (!isMounted) return;
      if ((err as { name?: string })?.name === "CanceledError") return;
      const prev = state.value.kind === "ok" ? state.value.snapshot : undefined;
      state.value = { kind: "error", reason: classifyThrown(err), previousSnapshot: prev };
    }
  }

  watch(
    [() => spcodeStatus.status.value.umo, () => toValue(worktreeRef)],
    () => {
      if (!isMounted) return;
      etagMap.clear();
      void refresh();
    },
  );

  function startPolling(intervalMs: number = DEFAULT_POLL_MS): void {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      void refresh();
    }, intervalMs);
  }
  function stopPolling(): void {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ── Mutations (single-flight) ──────────────────────────

  async function runMutation(
    endpoint: string,
    extraBody: Record<string, unknown>,
    parser: (raw: unknown) => SpcodeResolveResult | SpcodeConflictActionResult,
  ): Promise<SpcodeResolveResult | SpcodeConflictActionResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    const umo = spcodeStatus.status.value.umo ?? null;
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    mutationAbort?.abort();
    mutationAbort = new AbortController();
    const worktree = toValue(worktreeRef);
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        endpoint,
        { umo, ...(worktree ? { worktree } : {}), ...extraBody },
        { signal: mutationAbort.signal },
      );
      if (!isMounted || mutationAbort.signal.aborted) {
        return { ok: false, reason: "aborted" };
      }
      const parsed = parser(resp.data);
      if (parsed.ok) void refresh();
      return parsed;
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      return { ok: false, reason: classifyThrown(err) };
    }
  }

  async function resolve(params: SpcodeResolveParams): Promise<SpcodeResolveResult> {
    return (await runMutation(
      "spcode/git-conflict-resolve",
      buildResolveBody(params),
      parseSpcodeConflictResolve,
    )) as SpcodeResolveResult;
  }

  async function continueOp(message?: string): Promise<SpcodeConflictActionResult> {
    return (await runMutation(
      "spcode/git-conflict-continue",
      message ? { message } : {},
      parseSpcodeConflictContinue,
    )) as SpcodeConflictActionResult;
  }

  async function abort(): Promise<SpcodeConflictActionResult> {
    return (await runMutation(
      "spcode/git-conflict-abort",
      {},
      parseSpcodeConflictAbort,
    )) as SpcodeConflictActionResult;
  }

  function dispose(): void {
    isMounted = false;
    stopPolling();
    abortController?.abort();
    abortController = null;
    mutationAbort?.abort();
    mutationAbort = null;
  }

  return { state, refresh, startPolling, stopPolling, resolve, continueOp, abort, dispose };
}
```

- [ ] **Step 4: Run test to verify it passes**

```cmd
pnpm test -- src/composables/__tests__/useSpcodeGitConflict.spec.ts
```

Expected: PASS (7 tests).

- [ ] **Step 5: Typecheck + lint the composables so far, then commit**

```cmd
pnpm typecheck
pnpm lint -- src/composables/parseSpcodeGitMerge.ts src/composables/parseSpcodeGitConflict.ts src/composables/useSpcodeGitMerge.ts src/composables/useSpcodeGitConflict.ts
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui
git add dashboard/src/composables/useSpcodeGitConflict.ts dashboard/src/composables/__tests__/useSpcodeGitConflict.spec.ts
git commit -m "feat(dashboard): add useSpcodeGitConflict composable with polling"
```

Expected: typecheck clean; eslint auto-fixes at most style nits; commit created.

---

### Task 5: `GitMergeDialog.vue` + branch-menu merge action

**Files:**
- Create: `dashboard/src/components/chat/GitMergeDialog.vue`
- Modify: `dashboard/src/components/chat/GitDiffSidebar.vue` (script imports/refs/handlers; branch-menu row template; dialog mount; CSS)
- Modify: `dashboard/src/i18n/locales/zh-CN/features/chat.json`, `.../en-US/features/chat.json`, `.../ru-RU/features/chat.json` (add `merge` group under `spcodeProjectLoad.diffSidebar`)

**Interfaces:**
- Consumes: `useSpcodeGitMerge` (Task 3), `classifyMergeReason` (Task 1), `useSpcodeGitConflict.refresh` (Task 4 — instantiated in the sidebar in this task), existing `refreshAfterBranchChange()`, `showSnackbar()`, `selectedWorktree`, `tm`.
- Produces: `GitMergeDialog` component with props `{ modelValue: boolean; source: string; loading: boolean }` and emits `(e:"update:modelValue", v: boolean)` / `(e:"submit", p: { source: string; noFf: boolean; ffOnly: boolean; squash: boolean; message: string })`. Sidebar handlers `onBranchMergeClick`, `onMergeSubmit`, and the `gitMerge` / `gitConflict` composable instances consumed by Tasks 6-7.

- [ ] **Step 1: Create `GitMergeDialog.vue`**

```vue
<!-- Author: elecvoid243 @ 2026-08-01
     Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §4.1
     Merge options dialog. Dumb component: collects strategy + message and
     emits submit; the parent (GitDiffSidebar) owns the mutation. -->
<template>
  <v-dialog
    :model-value="modelValue"
    persistent
    max-width="480"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("spcodeProjectLoad.diffSidebar.merge.dialogTitle") }}
      </v-card-title>
      <v-card-text class="pt-4">
        <v-text-field
          :model-value="source"
          :label="tm('spcodeProjectLoad.diffSidebar.merge.sourceLabel')"
          readonly
          density="compact"
          variant="outlined"
        />
        <v-radio-group
          v-model="strategy"
          :label="tm('spcodeProjectLoad.diffSidebar.merge.strategy.label')"
          density="compact"
          hide-details
        >
          <v-radio
            value="default"
            :label="tm('spcodeProjectLoad.diffSidebar.merge.strategy.default')"
          />
          <v-radio
            value="noFf"
            :label="tm('spcodeProjectLoad.diffSidebar.merge.strategy.noFf')"
          />
          <v-radio
            value="ffOnly"
            :label="tm('spcodeProjectLoad.diffSidebar.merge.strategy.ffOnly')"
          />
          <v-radio
            value="squash"
            :label="tm('spcodeProjectLoad.diffSidebar.merge.strategy.squash')"
          />
        </v-radio-group>
        <v-textarea
          v-model="message"
          :label="tm('spcodeProjectLoad.diffSidebar.merge.messageLabel')"
          :disabled="strategy === 'ffOnly'"
          :hint="
            strategy === 'ffOnly'
              ? tm('spcodeProjectLoad.diffSidebar.merge.messageDisabledHint')
              : ''
          "
          rows="2"
          auto-grow
          density="compact"
          variant="outlined"
          class="mt-3"
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="loading"
          @click="emit('update:modelValue', false)"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.merge.cancel") }}
        </v-btn>
        <v-btn
          variant="tonal"
          color="primary"
          :loading="loading"
          @click="onSubmit"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.merge.submit") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  modelValue: boolean;
  source: string;
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (
    e: "submit",
    p: {
      source: string;
      noFf: boolean;
      ffOnly: boolean;
      squash: boolean;
      message: string;
    },
  ): void;
}>();

const { tm } = useModuleI18n("features/chat");

type Strategy = "default" | "noFf" | "ffOnly" | "squash";
const strategy = ref<Strategy>("default");
const message = ref("");

// Reset the form every time the dialog opens.
watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      strategy.value = "default";
      message.value = "";
    }
  },
);

function onSubmit(): void {
  emit("submit", {
    source: props.source,
    noFf: strategy.value === "noFf",
    ffOnly: strategy.value === "ffOnly",
    squash: strategy.value === "squash",
    message: message.value.trim(),
  });
}
</script>
```

- [ ] **Step 2: Sidebar script wiring** (`GitDiffSidebar.vue`)

Add imports (next to the other composable imports, ~line 69):

```ts
import GitMergeDialog from "@/components/chat/GitMergeDialog.vue";
import { useSpcodeGitMerge } from "@/composables/useSpcodeGitMerge";
import { classifyMergeReason } from "@/composables/parseSpcodeGitMerge";
import { useSpcodeGitConflict } from "@/composables/useSpcodeGitConflict";
```

Add state + handlers (place next to the branch-switch block, after `onBranchDeleteClick`'s sibling handlers):

```ts
// 2026-08-01 git-merge: branch-menu per-row "merge into current" action
// plus the conflict lifecycle composable (banner/panel mount in Task 7;
// instantiated here because merge/cherry-pick conflict paths refresh it).
const gitMerge = useSpcodeGitMerge();
const gitConflict = useSpcodeGitConflict(selectedWorktree);
const mergeDialogOpen = ref(false);
const mergeSource = ref("");

function onBranchMergeClick(b: { name: string; current: boolean; remote: boolean }): void {
  if (b.current || b.remote) return;
  mergeSource.value = b.name;
  mergeDialogOpen.value = true;
}

async function onMergeSubmit(params: {
  source: string;
  noFf: boolean;
  ffOnly: boolean;
  squash: boolean;
  message: string;
}): Promise<void> {
  const result = await gitMerge.merge({
    source: params.source,
    noFf: params.noFf,
    ffOnly: params.ffOnly,
    squash: params.squash,
    message: params.message || undefined,
    worktree: selectedWorktree.value,
  });
  if (result.ok) {
    mergeDialogOpen.value = false;
    await refreshAfterBranchChange();
    if (result.squash) {
      showSnackbar(
        tm("spcodeProjectLoad.diffSidebar.merge.squashSuccess", {
          source: params.source,
          count: result.filesTouched.length,
        }),
        "success",
      );
    } else {
      showSnackbar(
        tm(
          result.fastForward
            ? "spcodeProjectLoad.diffSidebar.merge.successFf"
            : "spcodeProjectLoad.diffSidebar.merge.success",
          { source: params.source, sha: result.mergeSha.slice(0, 7) },
        ),
        "success",
      );
    }
    return;
  }
  const meta = classifyMergeReason(result.reason);
  if (result.conflict) {
    // Conflict: close the dialog, light up the conflict banner
    // immediately instead of waiting for the next 30 s poll.
    mergeDialogOpen.value = false;
    branchMenuOpen.value = false;
    void gitConflict.refresh();
    showSnackbar(
      tm(meta.i18nKey, { source: params.source, count: result.conflictedFiles.length }),
      "warning",
    );
    return;
  }
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}
```

Also dispose both composables: locate `onBeforeUnmount` (it already disposes the other composables) and add `gitMerge.dispose(); gitConflict.dispose();`.

- [ ] **Step 3: Branch-menu row template** (`GitDiffSidebar.vue`)

In the branch list `v-list-item`'s `<template #append>` (~line 3727), add the merge icon BEFORE the delete icon:

```html
<template #append>
  <!-- 2026-08-01 git-merge: per-row merge-into-current action.
       Hidden for the current branch (no-op) and remote branches. -->
  <v-icon
    v-if="!b.current && !b.remote"
    size="14"
    class="git-diff-sidebar-branch-merge"
    :title="tm('spcodeProjectLoad.diffSidebar.merge.rowAction')"
    @click.stop="onBranchMergeClick(b)"
    >mdi-source-merge</v-icon
  >
  <v-icon
    v-if="!b.current"
    size="14"
    class="git-diff-sidebar-branch-delete"
    @click.stop="onBranchDeleteClick(b)"
    >mdi-close</v-icon
  >
</template>
```

- [ ] **Step 4: Mount the dialog** (`GitDiffSidebar.vue`)

Next to the revert `v-dialog` (~line 4453):

```html
<!-- 2026-08-01 git-merge: strategy dialog for the branch-menu merge action. -->
<GitMergeDialog
  v-model="mergeDialogOpen"
  :source="mergeSource"
  :loading="gitMerge.isMerging.value"
  @submit="onMergeSubmit"
/>
```

- [ ] **Step 5: CSS** (`GitDiffSidebar.vue` `<style>`)

Next to the `.git-diff-sidebar-branch-delete` rules (~line 5569):

```css
.git-diff-sidebar-branch-menu .git-diff-sidebar-branch-merge {
  opacity: 0.6;
  margin-right: 4px;
}
.git-diff-sidebar-branch-menu .git-diff-sidebar-branch-merge:hover {
  opacity: 1;
  color: rgb(var(--v-theme-primary));
}
```

- [ ] **Step 6: i18n — add `merge` group**

In each locale's `features/chat.json`, insert as a sibling of `branchMgmt` under `spcodeProjectLoad.diffSidebar` (keep JSON valid — mind the trailing comma on the previous group):

zh-CN:

```json
      "merge": {
        "rowAction": "合并到当前分支",
        "dialogTitle": "合并分支",
        "sourceLabel": "源分支",
        "strategy": {
          "label": "合并策略",
          "default": "默认（fast-forward 或 merge commit）",
          "noFf": "--no-ff（总是创建 merge commit）",
          "ffOnly": "--ff-only（仅 fast-forward）",
          "squash": "--squash（压缩为未提交改动）"
        },
        "messageLabel": "合并提交信息（可选）",
        "messageDisabledHint": "ff-only 模式不产生 merge commit，无需提交信息",
        "submit": "合并",
        "cancel": "取消",
        "success": "已合并 {source}（{sha}）",
        "successFf": "已 fast-forward 合并 {source}（{sha}）",
        "squashSuccess": "已 squash 合并 {source}，{count} 个文件已暂存（未提交）",
        "alreadyUpToDate": "已是最新，无需合并",
        "conflictWarning": "合并 {source} 产生 {count} 个冲突文件，请在冲突面板中解决",
        "error": {
          "worktree_dirty": "工作区有未提交改动，请先 commit 或 stash",
          "operation_in_progress": "已有合并 / cherry-pick / revert 冲突正在进行，请先处理",
          "invalid_branch": "分支名格式错误",
          "unrelated_histories": "两个分支没有共同历史（unrelated histories）",
          "git_error": "合并失败：{stderr}",
          "network": "网络连接失败",
          "unknown": "合并失败（{reason}）"
        }
      },
```

en-US:

```json
      "merge": {
        "rowAction": "Merge into current branch",
        "dialogTitle": "Merge branch",
        "sourceLabel": "Source branch",
        "strategy": {
          "label": "Merge strategy",
          "default": "Default (fast-forward or merge commit)",
          "noFf": "--no-ff (always create a merge commit)",
          "ffOnly": "--ff-only (fast-forward only)",
          "squash": "--squash (stage squashed changes, no commit)"
        },
        "messageLabel": "Merge commit message (optional)",
        "messageDisabledHint": "ff-only never creates a merge commit — no message needed",
        "submit": "Merge",
        "cancel": "Cancel",
        "success": "Merged {source} ({sha})",
        "successFf": "Fast-forwarded to {source} ({sha})",
        "squashSuccess": "Squash-merged {source}: {count} file(s) staged (not committed)",
        "alreadyUpToDate": "Already up to date",
        "conflictWarning": "Merging {source} produced {count} conflicted file(s) — resolve them in the conflict panel",
        "error": {
          "worktree_dirty": "Working tree has uncommitted changes — commit or stash first",
          "operation_in_progress": "A merge / cherry-pick / revert is already in progress — finish or abort it first",
          "invalid_branch": "Invalid branch name",
          "unrelated_histories": "The two branches share no history (unrelated histories)",
          "git_error": "Merge failed: {stderr}",
          "network": "Network error",
          "unknown": "Merge failed ({reason})"
        }
      },
```

ru-RU:

```json
      "merge": {
        "rowAction": "Слить в текущую ветку",
        "dialogTitle": "Слияние ветки",
        "sourceLabel": "Исходная ветка",
        "strategy": {
          "label": "Стратегия слияния",
          "default": "По умолчанию (fast-forward или merge commit)",
          "noFf": "--no-ff (всегда создавать merge commit)",
          "ffOnly": "--ff-only (только fast-forward)",
          "squash": "--squash (сжать в индекс, без коммита)"
        },
        "messageLabel": "Сообщение merge-коммита (необязательно)",
        "messageDisabledHint": "ff-only не создаёт merge commit — сообщение не требуется",
        "submit": "Слить",
        "cancel": "Отмена",
        "success": "Ветка {source} слита ({sha})",
        "successFf": "Fast-forward до {source} ({sha})",
        "squashSuccess": "Squash-слияние {source}: файлов в индексе: {count} (без коммита)",
        "alreadyUpToDate": "Уже актуально",
        "conflictWarning": "Слияние {source} вызвало конфликты в {count} файл(ах) — разрешите их в панели конфликтов",
        "error": {
          "worktree_dirty": "Есть незакоммиченные изменения — сначала commit или stash",
          "operation_in_progress": "Уже выполняется merge / cherry-pick / revert — завершите или прервите его",
          "invalid_branch": "Некорректное имя ветки",
          "unrelated_histories": "Ветки не имеют общей истории (unrelated histories)",
          "git_error": "Слияние не удалось: {stderr}",
          "network": "Ошибка сети",
          "unknown": "Слияние не удалось ({reason})"
        }
      },
```

- [ ] **Step 7: Verify + commit**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm typecheck
pnpm lint -- src/components/chat/GitMergeDialog.vue src/components/chat/GitDiffSidebar.vue
pnpm test
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui
git add dashboard/src/components/chat/GitMergeDialog.vue dashboard/src/components/chat/GitDiffSidebar.vue dashboard/src/i18n/locales/zh-CN/features/chat.json dashboard/src/i18n/locales/en-US/features/chat.json dashboard/src/i18n/locales/ru-RU/features/chat.json
git commit -m "feat(dashboard): add branch-menu merge action with strategy dialog"
```

Expected: typecheck clean, full vitest suite green, eslint clean.

---

### Task 6: `GitCherryPickDialog.vue` + log-row & toolbar entries

**Files:**
- Create: `dashboard/src/components/chat/GitCherryPickDialog.vue`
- Modify: `dashboard/src/components/chat/message_list_comps/GitLogView.vue` (new emit + per-row button + toolbar button + CSS)
- Modify: `dashboard/src/components/chat/GitDiffSidebar.vue` (dialog mount + handlers + `@cherry-pick` listeners)
- Modify: the three `features/chat.json` locales (add `cherryPick` group)

**Interfaces:**
- Consumes: `useSpcodeGitMerge` (Task 3, `gitMerge` instance from Task 5), `classifyCherryPickReason` (Task 1), `gitConflict.refresh` (Task 5), `refreshAfterBranchChange()`, `showSnackbar()`, `tm`.
- Produces: `GitCherryPickDialog` with props `{ modelValue: boolean; preset: { sha: string; subject: string } | null; loading: boolean }`, emits `(e:"update:modelValue", v: boolean)` / `(e:"submit", p: { ref: string; mainline: number | null })`. `GitLogView` gains emits `(e:"cherry-pick", commit: { sha: string; subject: string })` and `(e:"cherry-pick-blank")`.

- [ ] **Step 1: Create `GitCherryPickDialog.vue`**

```vue
<!-- Author: elecvoid243 @ 2026-08-01
     Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §4.2
     Cherry-pick dialog. Two modes: preset (from a log row — ref readonly)
     and blank (toolbar — editable ref). -->
<template>
  <v-dialog
    :model-value="modelValue"
    persistent
    max-width="480"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("spcodeProjectLoad.diffSidebar.cherryPick.dialogTitle") }}
      </v-card-title>
      <v-card-text class="pt-4">
        <v-text-field
          v-model="refInput"
          :label="tm('spcodeProjectLoad.diffSidebar.cherryPick.refLabel')"
          :hint="tm('spcodeProjectLoad.diffSidebar.cherryPick.refHint')"
          :readonly="preset !== null"
          :error-messages="refError ? [refError] : []"
          density="compact"
          variant="outlined"
          autocomplete="off"
          name="cherry-pick-ref"
        />
        <v-text-field
          v-model="mainlineInput"
          :label="tm('spcodeProjectLoad.diffSidebar.cherryPick.mainlineLabel')"
          :hint="tm('spcodeProjectLoad.diffSidebar.cherryPick.mainlineHint')"
          :error-messages="mainlineError ? [mainlineError] : []"
          density="compact"
          variant="outlined"
          class="mt-2"
          autocomplete="off"
          name="cherry-pick-mainline"
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="loading"
          @click="emit('update:modelValue', false)"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.cherryPick.cancel") }}
        </v-btn>
        <v-btn
          variant="tonal"
          color="primary"
          :loading="loading"
          @click="onSubmit"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.cherryPick.submit") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";

const props = defineProps<{
  modelValue: boolean;
  preset: { sha: string; subject: string } | null;
  loading: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "submit", p: { ref: string; mainline: number | null }): void;
}>();

const { tm } = useModuleI18n("features/chat");

const refInput = ref("");
const mainlineInput = ref("");
const refError = ref("");
const mainlineError = ref("");

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      refInput.value = props.preset?.sha ?? "";
      mainlineInput.value = "";
      refError.value = "";
      mainlineError.value = "";
    }
  },
);

function onSubmit(): void {
  refError.value = "";
  mainlineError.value = "";
  const refValue = refInput.value.trim();
  if (!refValue) {
    refError.value = tm("spcodeProjectLoad.diffSidebar.cherryPick.refRequired");
    return;
  }
  let mainline: number | null = null;
  const raw = mainlineInput.value.trim();
  if (raw) {
    const n = Number(raw);
    if (!Number.isInteger(n) || n < 1) {
      mainlineError.value = tm(
        "spcodeProjectLoad.diffSidebar.cherryPick.mainlineInvalid",
      );
      return;
    }
    mainline = n;
  }
  emit("submit", { ref: refValue, mainline });
}
</script>
```

- [ ] **Step 2: `GitLogView.vue` — emit + per-row button**

In `defineEmits` (~line 93, after the `revert` entry):

```ts
  // 2026-08-01 git-cherry-pick: per-row affordance mirroring "revert";
  // the sidebar owns the dialog + the /spcode/git-cherry-pick call.
  (e: "cherry-pick", commit: { sha: string; subject: string }): void;
  // Toolbar-level entry: open the cherry-pick dialog with an empty ref.
  (e: "cherry-pick-blank"): void;
```

In the commit-row meta line, immediately after the revert `</button>` (~line 693):

```html
          <!-- 2026-08-01 git-cherry-pick: per-row action, hover-revealed
               like the revert button beside it. -->
          <button
            type="button"
            class="git-log-item-cherry-pick"
            :title="
              tm(
                'spcodeProjectLoad.diffSidebar.cherryPick.rowActionTitle',
              )
            "
            :aria-label="
              tm('spcodeProjectLoad.diffSidebar.cherryPick.rowActionAria', {
                sha: c.sha.slice(0, 7),
              })
            "
            @click="emit('cherry-pick', { sha: c.sha, subject: c.subject })"
          >
            <v-icon size="13">mdi-source-branch-plus</v-icon>
            {{
              tm("spcodeProjectLoad.diffSidebar.cherryPick.rowAction")
            }}
          </button>
```

CSS (next to `.git-log-item-revert` rules, ~line 1059). Note: the revert
button owns `margin-left: auto`; the cherry-pick button sits immediately
after it, so it must NOT repeat that declaration:

```css
/* 2026-08-01 git-cherry-pick: per-row action mirroring .git-log-item-revert
   (hover-reveal). No margin-left:auto — the revert button already pushes
   the pair to the right edge. */
.git-log-item-cherry-pick {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  padding: 1px 8px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: 4px;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.65);
  cursor: pointer;
  opacity: 0;
  transition:
    opacity 0.12s,
    color 0.12s,
    border-color 0.12s;
}
.git-log-item:hover .git-log-item-cherry-pick,
.git-log-item-cherry-pick:focus-visible {
  opacity: 1;
}
.git-log-item-cherry-pick:hover {
  color: rgb(var(--v-theme-primary));
  border-color: rgba(var(--v-theme-primary), 0.4);
}
```

- [ ] **Step 3: `GitLogView.vue` — toolbar button**

Inside the `.git-log-filter-actions` div (~line 556), after the apply/reset
`v-btn`s, add:

```html
        <!-- 2026-08-01 git-cherry-pick: standalone entry (blank ref). -->
        <v-btn
          size="small"
          variant="text"
          :title="tm('spcodeProjectLoad.diffSidebar.cherryPick.toolbarAria')"
          @click="emit('cherry-pick-blank')"
        >
          <v-icon size="14" start>mdi-source-branch-plus</v-icon>
          {{ tm("spcodeProjectLoad.diffSidebar.cherryPick.toolbar") }}
        </v-btn>
```

- [ ] **Step 4: Sidebar wiring** (`GitDiffSidebar.vue`)

Script:

```ts
import GitCherryPickDialog from "@/components/chat/GitCherryPickDialog.vue";
import { classifyCherryPickReason } from "@/composables/parseSpcodeGitMerge";

// 2026-08-01 git-cherry-pick: dialog state. preset != null → log-row
// mode (readonly ref); preset == null → toolbar mode (editable ref).
const cherryPickDialogOpen = ref(false);
const cherryPickPreset = ref<{ sha: string; subject: string } | null>(null);

function onLogCherryPickRequest(commit: { sha: string; subject: string }): void {
  cherryPickPreset.value = commit;
  cherryPickDialogOpen.value = true;
}

function onToolbarCherryPickRequest(): void {
  cherryPickPreset.value = null;
  cherryPickDialogOpen.value = true;
}

async function onCherryPickSubmit(params: { ref: string; mainline: number | null }): Promise<void> {
  const result = await gitMerge.cherryPick({
    ref: params.ref,
    mainline: params.mainline,
    worktree: selectedWorktree.value,
  });
  if (result.ok) {
    cherryPickDialogOpen.value = false;
    await refreshAfterBranchChange();
    showSnackbar(
      tm("spcodeProjectLoad.diffSidebar.cherryPick.success", {
        ref: params.ref.slice(0, 12),
        sha: result.newSha.slice(0, 7),
      }),
      "success",
    );
    return;
  }
  const meta = classifyCherryPickReason(result.reason);
  if (result.conflict) {
    cherryPickDialogOpen.value = false;
    void gitConflict.refresh();
    showSnackbar(
      tm(meta.i18nKey, { ref: params.ref.slice(0, 12), count: result.conflictedFiles.length }),
      "warning",
    );
    return;
  }
  showSnackbar(
    tm(meta.i18nKey, { reason: result.reason, ref: params.ref.slice(0, 12), stderr: result.stderr ?? "" }),
    meta.color,
    meta.withStderr ? result.stderr : undefined,
  );
}
```

Template — update the `GitLogView` mount (add two listeners after `@revert`):

```html
            @revert="onLogRevertRequest"
            @cherry-pick="onLogCherryPickRequest"
            @cherry-pick-blank="onToolbarCherryPickRequest"
```

Mount the dialog next to `GitMergeDialog`:

```html
<!-- 2026-08-01 git-cherry-pick: dialog for log-row + toolbar entries. -->
<GitCherryPickDialog
  v-model="cherryPickDialogOpen"
  :preset="cherryPickPreset"
  :loading="gitMerge.isCherryPicking.value"
  @submit="onCherryPickSubmit"
/>
```

- [ ] **Step 5: i18n — add `cherryPick` group** (sibling of `merge`)

zh-CN:

```json
      "cherryPick": {
        "toolbar": "cherry-pick",
        "toolbarAria": "cherry-pick 一个提交",
        "rowAction": "cherry-pick",
        "rowActionTitle": "cherry-pick 此提交到当前分支",
        "rowActionAria": "cherry-pick 提交 {sha}",
        "dialogTitle": "cherry-pick 提交",
        "refLabel": "提交引用",
        "refHint": "sha / 分支 / tag",
        "refRequired": "请输入提交引用",
        "mainlineLabel": "mainline（合并提交必填）",
        "mainlineHint": "正整数，通常为 1",
        "mainlineInvalid": "mainline 必须是正整数",
        "submit": "cherry-pick",
        "cancel": "取消",
        "success": "已 cherry-pick {ref}（{sha}）",
        "emptyInfo": "该提交没有可应用的改动（empty）",
        "conflictWarning": "cherry-pick {ref} 产生 {count} 个冲突文件，请在冲突面板中解决",
        "error": {
          "worktree_dirty": "工作区有未提交改动，请先 commit 或 stash",
          "operation_in_progress": "已有合并 / cherry-pick / revert 冲突正在进行，请先处理",
          "commit_not_found": "无法解析为提交：{ref}",
          "git_error": "cherry-pick 失败：{stderr}",
          "network": "网络连接失败",
          "unknown": "cherry-pick 失败（{reason}）"
        }
      },
```

en-US:

```json
      "cherryPick": {
        "toolbar": "cherry-pick",
        "toolbarAria": "Cherry-pick a commit",
        "rowAction": "cherry-pick",
        "rowActionTitle": "Cherry-pick this commit onto the current branch",
        "rowActionAria": "Cherry-pick commit {sha}",
        "dialogTitle": "Cherry-pick commit",
        "refLabel": "Commit ref",
        "refHint": "sha / branch / tag",
        "refRequired": "Enter a commit ref",
        "mainlineLabel": "mainline (required for merge commits)",
        "mainlineHint": "Positive integer, usually 1",
        "mainlineInvalid": "mainline must be a positive integer",
        "submit": "Cherry-pick",
        "cancel": "Cancel",
        "success": "Cherry-picked {ref} ({sha})",
        "emptyInfo": "Nothing to apply from this commit (empty)",
        "conflictWarning": "Cherry-picking {ref} produced {count} conflicted file(s) — resolve them in the conflict panel",
        "error": {
          "worktree_dirty": "Working tree has uncommitted changes — commit or stash first",
          "operation_in_progress": "A merge / cherry-pick / revert is already in progress — finish or abort it first",
          "commit_not_found": "Ref does not resolve to a commit: {ref}",
          "git_error": "Cherry-pick failed: {stderr}",
          "network": "Network error",
          "unknown": "Cherry-pick failed ({reason})"
        }
      },
```

ru-RU:

```json
      "cherryPick": {
        "toolbar": "cherry-pick",
        "toolbarAria": "Перенести коммит (cherry-pick)",
        "rowAction": "cherry-pick",
        "rowActionTitle": "Перенести этот коммит в текущую ветку",
        "rowActionAria": "Перенести коммит {sha}",
        "dialogTitle": "Перенос коммита",
        "refLabel": "Ссылка на коммит",
        "refHint": "sha / ветка / тег",
        "refRequired": "Введите ссылку на коммит",
        "mainlineLabel": "mainline (обязателен для merge-коммитов)",
        "mainlineHint": "Положительное целое, обычно 1",
        "mainlineInvalid": "mainline должен быть положительным целым",
        "submit": "Перенести",
        "cancel": "Отмена",
        "success": "Коммит {ref} перенесён ({sha})",
        "emptyInfo": "В этом коммите нечего применять (empty)",
        "conflictWarning": "Перенос {ref} вызвал конфликты в {count} файл(ах) — разрешите их в панели конфликтов",
        "error": {
          "worktree_dirty": "Есть незакоммиченные изменения — сначала commit или stash",
          "operation_in_progress": "Уже выполняется merge / cherry-pick / revert — завершите или прервите его",
          "commit_not_found": "Ссылка не указывает на коммит: {ref}",
          "git_error": "Перенос не удался: {stderr}",
          "network": "Ошибка сети",
          "unknown": "Перенос не удался ({reason})"
        }
      },
```

- [ ] **Step 6: Verify + commit**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm typecheck
pnpm lint -- src/components/chat/GitCherryPickDialog.vue src/components/chat/GitDiffSidebar.vue src/components/chat/message_list_comps/GitLogView.vue
pnpm test
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui
git add dashboard/src/components/chat/GitCherryPickDialog.vue dashboard/src/components/chat/GitDiffSidebar.vue dashboard/src/components/chat/message_list_comps/GitLogView.vue dashboard/src/i18n/locales/zh-CN/features/chat.json dashboard/src/i18n/locales/en-US/features/chat.json dashboard/src/i18n/locales/ru-RU/features/chat.json
git commit -m "feat(dashboard): add cherry-pick dialog with log-row and toolbar entries"
```

Expected: typecheck clean, tests green, lint clean.

---

### Task 7: `GitConflictPanel.vue` + banner wiring

**Files:**
- Create: `dashboard/src/components/chat/GitConflictPanel.vue`
- Modify: `dashboard/src/components/chat/GitDiffSidebar.vue` (panel mount + handlers + polling lifecycle)
- Modify: the three `features/chat.json` locales (add `conflict` group)

**Interfaces:**
- Consumes: `UseSpcodeGitConflict` (Task 4 — the `gitConflict` instance created in Task 5), `classifyConflictReason` (Task 2), existing `composable.refresh()` / `gitStatus.refresh()` / `refreshAfterBranchChange()` / `showSnackbar()` in the sidebar.
- Produces: `GitConflictPanel` with prop `{ conflict: UseSpcodeGitConflict }`, emits `(e:"resolved")` (a file/all resolve succeeded → parent refreshes diff+status), `(e:"completed")` (continue/abort succeeded → parent runs the full cascade), `(e:"failed", payload: { reason: string; stderr?: string; count?: number })` (parent maps through `classifyConflictReason` into the snackbar).

- [ ] **Step 1: Create `GitConflictPanel.vue`**

```vue
<!-- Author: elecvoid243 @ 2026-08-01
     Spec: docs/superpowers/specs/2026-08-01-git-merge-cherrypick-conflict-frontend-design.md §4.3
     Conflict banner + resolution panel. Renders only while the polled
     conflict-status snapshot reports inConflict. Mutations go through the
     useSpcodeGitConflict handle passed as a prop (same pattern as
     GitLogView receiving the gitShow handle). -->
<template>
  <!-- The computed below already filters to inConflict-only snapshots, so
       a bare `v-if="snapshot"` is the full guard (also keeps vue-tsc
       narrowing happy for the children). -->
  <div v-if="snapshot" class="git-conflict-panel">
    <!-- Banner: operation + subject + progress; click toggles the panel. -->
    <button type="button" class="git-conflict-banner" @click="expanded = !expanded">
      <v-icon size="14" color="warning">mdi-alert-outline</v-icon>
      <span class="git-conflict-banner-text">
        {{ tm(`spcodeProjectLoad.diffSidebar.conflict.banner.${snapshot.operation ?? "merge"}`) }}
        <template v-if="snapshot.operationSubject">
          · {{ snapshot.operationSubject }}
        </template>
      </span>
      <span class="git-conflict-banner-progress">
        {{
          tm("spcodeProjectLoad.diffSidebar.conflict.banner.progress", {
            resolved: snapshot.totalResolved,
            total: snapshot.totalResolved + snapshot.totalConflicted,
          })
        }}
      </span>
      <v-icon size="14">{{ expanded ? "mdi-chevron-up" : "mdi-chevron-down" }}</v-icon>
    </button>

    <div v-if="expanded" class="git-conflict-body">
      <!-- Unresolved files -->
      <div class="git-conflict-group-label">
        {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.unresolvedGroup", { count: snapshot.totalConflicted }) }}
      </div>
      <div v-for="f in snapshot.conflictedFiles" :key="f.path" class="git-conflict-file">
        <button type="button" class="git-conflict-file-head" @click="toggleFile(f.path)">
          <v-icon size="13">mdi-file-alert-outline</v-icon>
          <span class="git-conflict-file-path">{{ f.path }}</span>
          <span class="git-conflict-file-status">{{ f.status }}</span>
          <v-icon size="13">{{ openFile === f.path ? "mdi-chevron-up" : "mdi-chevron-down" }}</v-icon>
        </button>

        <div v-if="openFile === f.path" class="git-conflict-file-body">
          <!-- Binary / oversized: whole-file only -->
          <div v-if="f.binary || f.truncated" class="git-conflict-note">
            {{ tm(f.binary
              ? "spcodeProjectLoad.diffSidebar.conflict.file.binary"
              : "spcodeProjectLoad.diffSidebar.conflict.file.truncated") }}
          </div>

          <!-- Hunk list -->
          <template v-else>
            <div v-for="h in f.hunks" :key="h.index" class="git-conflict-hunk">
              <div class="git-conflict-hunk-head">
                <span>L{{ h.startLine }}–{{ h.endLine }}</span>
                <v-radio-group
                  :model-value="choices[f.path]?.[h.index]"
                  density="compact"
                  hide-details
                  inline
                  @update:model-value="setChoice(f.path, h.index, $event)"
                >
                  <v-radio value="ours" :label="tm('spcodeProjectLoad.diffSidebar.conflict.file.hunkChoiceOurs')" />
                  <v-radio value="theirs" :label="tm('spcodeProjectLoad.diffSidebar.conflict.file.hunkChoiceTheirs')" />
                  <v-radio v-if="h.base !== null" value="base" :label="tm('spcodeProjectLoad.diffSidebar.conflict.file.hunkChoiceBase')" />
                </v-radio-group>
              </div>
              <div class="git-conflict-hunk-cols">
                <div class="git-conflict-hunk-col">
                  <div class="git-conflict-hunk-label">
                    {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.oursLabel", { label: h.oursLabel }) }}
                  </div>
                  <pre>{{ h.ours }}</pre>
                </div>
                <div class="git-conflict-hunk-col">
                  <div class="git-conflict-hunk-label">
                    {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.theirsLabel", { label: h.theirsLabel }) }}
                  </div>
                  <pre>{{ h.theirs }}</pre>
                </div>
              </div>
            </div>
          </template>

          <!-- Custom editor (prefilled with the full ours content) -->
          <div v-if="customEditing === f.path" class="git-conflict-custom">
            <v-textarea
              v-model="customContent"
              rows="12"
              auto-grow
              density="compact"
              variant="outlined"
              class="git-conflict-custom-editor"
              name="conflict-custom-content"
            />
            <div class="git-conflict-actions">
              <v-btn size="x-small" variant="text" @click="customEditing = null">
                {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.customCancel") }}
              </v-btn>
              <v-btn size="x-small" variant="tonal" color="primary" :disabled="!customContent" @click="applyCustom(f.path)">
                {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.customApply") }}
              </v-btn>
            </div>
          </div>

          <!-- File-level actions -->
          <div v-else class="git-conflict-actions">
            <v-btn
              v-if="!f.binary && !f.truncated && f.hunks.length > 0"
              size="x-small"
              variant="tonal"
              color="primary"
              :disabled="chosenCount(f) < f.hunks.length"
              @click="applyHunks(f)"
            >
              {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.applyHunks", { chosen: chosenCount(f), total: f.hunks.length }) }}
            </v-btn>
            <v-btn size="x-small" variant="text" @click="applyWhole(f.path, 'ours')">
              {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.useOurs") }}
            </v-btn>
            <v-btn size="x-small" variant="text" @click="applyWhole(f.path, 'theirs')">
              {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.useTheirs") }}
            </v-btn>
            <v-btn v-if="!f.binary" size="x-small" variant="text" @click="startCustom(f)">
              {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.customEdit") }}
            </v-btn>
          </div>
        </div>
      </div>

      <!-- Resolved files -->
      <template v-if="snapshot.resolvedFiles.length > 0">
        <div class="git-conflict-group-label">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.file.resolvedGroup", { count: snapshot.totalResolved }) }}
        </div>
        <div v-for="p in snapshot.resolvedFiles" :key="p" class="git-conflict-resolved-file">
          <v-icon size="13" color="success">mdi-check</v-icon>
          <span class="git-conflict-file-path">{{ p }}</span>
        </div>
      </template>

      <!-- Footer: batch / abort / continue -->
      <div class="git-conflict-footer">
        <v-btn size="x-small" variant="text" @click="confirmAll('ours')">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.resolveAllOurs") }}
        </v-btn>
        <v-btn size="x-small" variant="text" @click="confirmAll('theirs')">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.resolveAllTheirs") }}
        </v-btn>
        <v-spacer />
        <v-btn size="x-small" variant="text" color="error" @click="abortOpen = true">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.abort", { operation: opLabel }) }}
        </v-btn>
        <v-btn
          size="x-small"
          variant="tonal"
          color="primary"
          :disabled="!snapshot.allResolved"
          :title="snapshot.allResolved ? '' : tm('spcodeProjectLoad.diffSidebar.conflict.footer.continueBlocked')"
          @click="continueOpen = true"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.continue", { operation: opLabel }) }}
        </v-btn>
      </div>
    </div>

    <!-- Continue confirm (message field only for merge) -->
    <v-dialog v-model="continueOpen" persistent max-width="440">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.continue", { operation: opLabel }) }}
        </v-card-title>
        <v-card-text class="pt-4">
          <v-textarea
            v-if="snapshot.operation === 'merge'"
            v-model="continueMessage"
            :label="tm('spcodeProjectLoad.diffSidebar.conflict.footer.continueMessageLabel')"
            rows="2"
            auto-grow
            density="compact"
            variant="outlined"
            name="conflict-continue-message"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="continueOpen = false">
            {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.cancel") }}
          </v-btn>
          <v-btn variant="tonal" color="primary" @click="doContinue">
            {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.continue", { operation: opLabel }) }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Abort confirm -->
    <v-dialog v-model="abortOpen" persistent max-width="440">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.abortConfirmTitle", { operation: opLabel }) }}
        </v-card-title>
        <v-card-text class="pt-4">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.abortConfirmMessage", { operation: opLabel }) }}
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="abortOpen = false">
            {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.cancel") }}
          </v-btn>
          <v-btn variant="tonal" color="error" @click="doAbort">
            {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.abortConfirmAction") }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Resolve-all confirm -->
    <v-dialog v-model="allOpen" persistent max-width="440">
      <v-card>
        <v-card-title class="text-h3 pa-4 pb-0 pl-6">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.resolveAllConfirmTitle") }}
        </v-card-title>
        <v-card-text class="pt-4">
          {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.resolveAllConfirm", { count: snapshot.totalConflicted, side: allSide }) }}
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="allOpen = false">
            {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.cancel") }}
          </v-btn>
          <v-btn variant="tonal" color="primary" @click="doResolveAll">
            {{ tm("spcodeProjectLoad.diffSidebar.conflict.footer.resolveAllAction") }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useModuleI18n } from "@/i18n/composables";
import type { UseSpcodeGitConflict } from "@/composables/useSpcodeGitConflict";
import type {
  ConflictedFile,
  ConflictSnapshot,
  SpcodeResolveResult,
  SpcodeConflictActionResult,
} from "@/composables/parseSpcodeGitConflict";

const props = defineProps<{ conflict: UseSpcodeGitConflict }>();

const emit = defineEmits<{
  (e: "resolved"): void;
  (e: "completed"): void;
  (e: "failed", payload: { reason: string; stderr?: string; count?: number }): void;
}>();

const { tm } = useModuleI18n("features/chat");

const snapshot = computed<ConflictSnapshot | null>(() => {
  const s = props.conflict.state.value;
  return s.kind === "ok" && s.snapshot.inConflict ? s.snapshot : null;
});

const opLabel = computed(() =>
  tm(`spcodeProjectLoad.diffSidebar.conflict.op.${snapshot.value?.operation ?? "merge"}`),
);

const expanded = ref(false);
const openFile = ref<string | null>(null);
const choices = ref<Record<string, Record<number, "ours" | "theirs" | "base">>>({});
const customEditing = ref<string | null>(null);
const customContent = ref("");
const continueOpen = ref(false);
const continueMessage = ref("");
const abortOpen = ref(false);
const allOpen = ref(false);
const allSide = ref<"ours" | "theirs">("ours");

// Reset per-file UI state when a NEW conflict operation starts (identity
// = operation + ref); mid-operation status refreshes keep the choices.
watch(
  () => [snapshot.value?.operation, snapshot.value?.operationRef],
  () => {
    choices.value = {};
    openFile.value = null;
    customEditing.value = null;
  },
);

function toggleFile(path: string): void {
  openFile.value = openFile.value === path ? null : path;
}

function setChoice(path: string, index: number, choice: unknown): void {
  if (choice !== "ours" && choice !== "theirs" && choice !== "base") return;
  const fileChoices = { ...(choices.value[path] ?? {}), [index]: choice };
  choices.value = { ...choices.value, [path]: fileChoices };
}

function chosenCount(f: ConflictedFile): number {
  return Object.keys(choices.value[f.path] ?? {}).length;
}

// Shared result fan-out: ok → tell the parent which refresh tier to run;
// failure → parent surfaces the reason via the snackbar.
function handleResult(
  r: SpcodeResolveResult | SpcodeConflictActionResult,
  kind: "resolve" | "complete",
): void {
  if (r.ok) {
    emit(kind === "resolve" ? "resolved" : "completed");
    return;
  }
  const remaining = "remainingConflicts" in r ? r.remainingConflicts : undefined;
  emit("failed", { reason: r.reason, stderr: r.stderr, count: remaining?.length });
}

async function applyHunks(f: ConflictedFile): Promise<void> {
  const fileChoices = choices.value[f.path] ?? {};
  const hunks = Object.entries(fileChoices).map(([idx, choice]) => ({
    index: Number(idx),
    choice,
  }));
  const r = await props.conflict.resolve({ mode: "hunks", file: f.path, hunks });
  if (r.ok && !r.partial) {
    const next = { ...choices.value };
    delete next[f.path];
    choices.value = next;
  }
  handleResult(r, "resolve");
}

async function applyWhole(path: string, side: "ours" | "theirs"): Promise<void> {
  handleResult(
    await props.conflict.resolve({ mode: "whole", file: path, resolution: side }),
    "resolve",
  );
}

function startCustom(f: ConflictedFile): void {
  // Prefill with the full ours-side stage content from the status payload
  // (/spcode/git-file reads blobs at a ref, not the working tree).
  customContent.value = f.threeWay.ours ?? "";
  customEditing.value = f.path;
}

async function applyCustom(path: string): Promise<void> {
  const r = await props.conflict.resolve({
    mode: "custom",
    file: path,
    content: customContent.value,
  });
  if (r.ok) customEditing.value = null;
  handleResult(r, "resolve");
}

function confirmAll(side: "ours" | "theirs"): void {
  allSide.value = side;
  allOpen.value = true;
}

async function doResolveAll(): Promise<void> {
  allOpen.value = false;
  handleResult(
    await props.conflict.resolve({ mode: "all", resolution: allSide.value }),
    "resolve",
  );
}

async function doContinue(): Promise<void> {
  continueOpen.value = false;
  const msg = continueMessage.value.trim();
  continueMessage.value = "";
  handleResult(await props.conflict.continueOp(msg || undefined), "complete");
}

async function doAbort(): Promise<void> {
  abortOpen.value = false;
  handleResult(await props.conflict.abort(), "complete");
}
</script>

<style scoped>
.git-conflict-panel {
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.git-conflict-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 12px;
  font-size: 12px;
  background: rgba(var(--v-theme-warning), 0.12);
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
  border: none;
  text-align: left;
}
.git-conflict-banner-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-conflict-banner-progress {
  flex-shrink: 0;
  opacity: 0.8;
  font-variant-numeric: tabular-nums;
}
.git-conflict-body {
  padding: 8px 12px;
  font-size: 12px;
}
.git-conflict-group-label {
  font-weight: 600;
  opacity: 0.7;
  margin: 6px 0 4px;
}
.git-conflict-file {
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 4px;
  margin-bottom: 6px;
}
.git-conflict-file-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 8px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: inherit;
  text-align: left;
}
.git-conflict-file-path {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
}
.git-conflict-file-status {
  opacity: 0.6;
  font-family: monospace;
}
.git-conflict-file-body {
  padding: 4px 8px 8px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
.git-conflict-hunk {
  margin-top: 6px;
}
.git-conflict-hunk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: monospace;
  opacity: 0.8;
}
.git-conflict-hunk-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-top: 2px;
}
.git-conflict-hunk-col pre {
  font-size: 11px;
  font-family: monospace;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 4px;
  padding: 4px 6px;
  overflow: auto;
  max-height: 160px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
.git-conflict-hunk-label {
  font-size: 11px;
  opacity: 0.6;
}
.git-conflict-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.git-conflict-resolved-file {
  display: flex;
  align-items: center;
  gap: 4px;
  opacity: 0.7;
  padding: 1px 8px;
}
.git-conflict-footer {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  border-top: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  padding-top: 8px;
}
.git-conflict-note {
  opacity: 0.7;
  padding: 4px 0;
}
.git-conflict-custom-editor :deep(textarea) {
  font-family: monospace;
  font-size: 11px;
}
</style>
```

- [ ] **Step 2: Sidebar wiring** (`GitDiffSidebar.vue`)

Script — add import and handlers:

```ts
import GitConflictPanel from "@/components/chat/GitConflictPanel.vue";
import { classifyConflictReason } from "@/composables/parseSpcodeGitConflict";

// 2026-08-01 git-conflict: panel event fan-out (spec §4.5 refresh matrix).
function onConflictFailed(payload: { reason: string; stderr?: string; count?: number }): void {
  const meta = classifyConflictReason(payload.reason);
  showSnackbar(
    tm(meta.i18nKey, {
      reason: payload.reason,
      stderr: payload.stderr ?? "",
      count: payload.count ?? 0,
    }),
    meta.color,
    meta.withStderr ? payload.stderr : undefined,
  );
}

async function onConflictResolved(): Promise<void> {
  // A file was rewritten + staged on disk → refresh the diff/status views.
  await Promise.allSettled([composable.refresh(), gitStatus.refresh()]);
}

async function onConflictCompleted(): Promise<void> {
  // continue/abort moved or restored HEAD → full cascade.
  await refreshAfterBranchChange();
}
```

Template — mount the panel immediately ABOVE the scope bar (search anchor: the element carrying `spcodeProjectLoad.diffSidebar.scopeBar.ariaLabel`):

```html
<!-- 2026-08-01 git-conflict: banner + resolution panel, visible only
     while a merge/cherry-pick/revert conflict is in progress. -->
<GitConflictPanel
  :conflict="gitConflict"
  @resolved="onConflictResolved"
  @completed="onConflictCompleted"
  @failed="onConflictFailed"
/>
```

Polling lifecycle — in the `modelValue` watch that manages polling (~lines 1520–1540): add `gitConflict.startPolling(30_000);` immediately after `branchesComposable.startPolling(30_000);`, and `gitConflict.stopPolling();` immediately after **each** `branchesComposable.stopPolling();` call (the watch's else-branch AND the cleanup block at ~line 1569). Disposal was already handled in Task 5 Step 2 (`gitConflict.dispose()` in `onBeforeUnmount`).

- [ ] **Step 3: i18n — add `conflict` group** (sibling of `cherryPick`)

zh-CN:

```json
      "conflict": {
        "banner": {
          "merge": "合并冲突",
          "cherry_pick": "cherry-pick 冲突",
          "revert": "revert 冲突",
          "progress": "已解决 {resolved}/{total}"
        },
        "file": {
          "unresolvedGroup": "待解决（{count}）",
          "resolvedGroup": "已解决（{count}）",
          "binary": "二进制文件，仅支持整文件解决",
          "truncated": "文件过大，仅支持整文件解决",
          "oursLabel": "当前（ours · {label}）",
          "theirsLabel": "传入（theirs · {label}）",
          "hunkChoiceOurs": "ours",
          "hunkChoiceTheirs": "theirs",
          "hunkChoiceBase": "base",
          "applyHunks": "应用选择（{chosen}/{total}）",
          "useOurs": "整文件用 ours",
          "useTheirs": "整文件用 theirs",
          "customEdit": "手动编辑",
          "customApply": "保存并标记已解决",
          "customCancel": "取消编辑"
        },
        "footer": {
          "resolveAllOurs": "全部采用 ours",
          "resolveAllTheirs": "全部采用 theirs",
          "resolveAllConfirmTitle": "批量解决冲突",
          "resolveAllConfirm": "确认对剩余 {count} 个冲突文件全部采用 {side}？",
          "resolveAllAction": "全部应用",
          "continue": "完成{operation}",
          "continueMessageLabel": "提交信息（可选）",
          "continueBlocked": "仍有未解决的冲突文件",
          "abort": "中止{operation}",
          "abortConfirmTitle": "中止{operation}",
          "abortConfirmMessage": "确认中止当前{operation}？所有冲突改动将回滚到操作前状态。",
          "abortConfirmAction": "中止",
          "cancel": "取消"
        },
        "op": { "merge": "合并", "cherry_pick": "cherry-pick", "revert": "revert" },
        "error": {
          "no_conflict_in_progress": "当前没有进行中的冲突操作",
          "file_not_conflicted": "该文件不在冲突列表中",
          "unresolved_conflicts_remain": "仍有 {count} 个冲突文件未解决",
          "path_unsafe": "文件路径不安全",
          "invalid_param": "参数错误：{stderr}",
          "hook_rejected": "提交被 hook 拒绝：{stderr}",
          "identity_not_set": "未配置 git user.name / user.email",
          "git_error": "操作失败：{stderr}",
          "network": "网络连接失败",
          "unknown": "操作失败（{reason}）"
        }
      },
```

en-US:

```json
      "conflict": {
        "banner": {
          "merge": "Merge conflict",
          "cherry_pick": "Cherry-pick conflict",
          "revert": "Revert conflict",
          "progress": "Resolved {resolved}/{total}"
        },
        "file": {
          "unresolvedGroup": "Unresolved ({count})",
          "resolvedGroup": "Resolved ({count})",
          "binary": "Binary file — whole-file resolution only",
          "truncated": "File too large — whole-file resolution only",
          "oursLabel": "Current (ours · {label})",
          "theirsLabel": "Incoming (theirs · {label})",
          "hunkChoiceOurs": "ours",
          "hunkChoiceTheirs": "theirs",
          "hunkChoiceBase": "base",
          "applyHunks": "Apply choices ({chosen}/{total})",
          "useOurs": "Use ours (whole file)",
          "useTheirs": "Use theirs (whole file)",
          "customEdit": "Edit manually",
          "customApply": "Save & mark resolved",
          "customCancel": "Cancel edit"
        },
        "footer": {
          "resolveAllOurs": "All → ours",
          "resolveAllTheirs": "All → theirs",
          "resolveAllConfirmTitle": "Resolve all conflicts",
          "resolveAllConfirm": "Resolve the remaining {count} conflicted file(s) using {side}?",
          "resolveAllAction": "Apply to all",
          "continue": "Complete {operation}",
          "continueMessageLabel": "Commit message (optional)",
          "continueBlocked": "Conflicted files remain",
          "abort": "Abort {operation}",
          "abortConfirmTitle": "Abort {operation}",
          "abortConfirmMessage": "Abort the current {operation}? All conflicted changes roll back to the pre-operation state.",
          "abortConfirmAction": "Abort",
          "cancel": "Cancel"
        },
        "op": { "merge": "merge", "cherry_pick": "cherry-pick", "revert": "revert" },
        "error": {
          "no_conflict_in_progress": "No conflict operation is in progress",
          "file_not_conflicted": "That file is not in the conflict list",
          "unresolved_conflicts_remain": "{count} conflicted file(s) remain",
          "path_unsafe": "Unsafe file path",
          "invalid_param": "Invalid parameter: {stderr}",
          "hook_rejected": "Commit rejected by hook: {stderr}",
          "identity_not_set": "git user.name / user.email is not configured",
          "git_error": "Operation failed: {stderr}",
          "network": "Network error",
          "unknown": "Operation failed ({reason})"
        }
      },
```

ru-RU:

```json
      "conflict": {
        "banner": {
          "merge": "Конфликт слияния",
          "cherry_pick": "Конфликт cherry-pick",
          "revert": "Конфликт revert",
          "progress": "Разрешено {resolved}/{total}"
        },
        "file": {
          "unresolvedGroup": "Ожидают ({count})",
          "resolvedGroup": "Разрешены ({count})",
          "binary": "Бинарный файл — только разрешение целиком",
          "truncated": "Файл слишком большой — только разрешение целиком",
          "oursLabel": "Текущая (ours · {label})",
          "theirsLabel": "Входящая (theirs · {label})",
          "hunkChoiceOurs": "ours",
          "hunkChoiceTheirs": "theirs",
          "hunkChoiceBase": "base",
          "applyHunks": "Применить выбор ({chosen}/{total})",
          "useOurs": "Весь файл ours",
          "useTheirs": "Весь файл theirs",
          "customEdit": "Редактировать вручную",
          "customApply": "Сохранить и отметить разрешённым",
          "customCancel": "Отменить правку"
        },
        "footer": {
          "resolveAllOurs": "Все → ours",
          "resolveAllTheirs": "Все → theirs",
          "resolveAllConfirmTitle": "Разрешить все конфликты",
          "resolveAllConfirm": "Разрешить оставшиеся {count} файл(ов) выбором {side}?",
          "resolveAllAction": "Применить ко всем",
          "continue": "Завершить {operation}",
          "continueMessageLabel": "Сообщение коммита (необязательно)",
          "continueBlocked": "Остались неразрешённые файлы",
          "abort": "Прервать {operation}",
          "abortConfirmTitle": "Прервать {operation}",
          "abortConfirmMessage": "Прервать текущий {operation}? Все конфликтные изменения откатятся к состоянию до операции.",
          "abortConfirmAction": "Прервать",
          "cancel": "Отмена"
        },
        "op": { "merge": "слияние", "cherry_pick": "cherry-pick", "revert": "revert" },
        "error": {
          "no_conflict_in_progress": "Нет активной конфликтной операции",
          "file_not_conflicted": "Этого файла нет в списке конфликтов",
          "unresolved_conflicts_remain": "Осталось неразрешённых файлов: {count}",
          "path_unsafe": "Небезопасный путь к файлу",
          "invalid_param": "Некорректный параметр: {stderr}",
          "hook_rejected": "Коммит отклонён хуком: {stderr}",
          "identity_not_set": "Не настроены git user.name / user.email",
          "git_error": "Операция не удалась: {stderr}",
          "network": "Ошибка сети",
          "unknown": "Операция не удалась ({reason})"
        }
      },
```

- [ ] **Step 4: Verify + commit**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm typecheck
pnpm lint -- src/components/chat/GitConflictPanel.vue src/components/chat/GitDiffSidebar.vue
pnpm test
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui
git add dashboard/src/components/chat/GitConflictPanel.vue dashboard/src/components/chat/GitDiffSidebar.vue dashboard/src/i18n/locales/zh-CN/features/chat.json dashboard/src/i18n/locales/en-US/features/chat.json dashboard/src/i18n/locales/ru-RU/features/chat.json
git commit -m "feat(dashboard): add conflict resolution panel with hunk-level UI"
```

Expected: typecheck clean, tests green, lint clean.

---

### Task 8: Full verification + spec cross-link + smoke checklist

**Files:**
- Modify: `F:\github\astrbot_plugin_spcode_toolkit\docs\superpowers\specs\2026-07-28-git-merge-cherrypick-conflict-design.md` (append frontend link; **separate repo** — plugin repo, commit with `git add -f` since `docs/` is gitignored there)

**Interfaces:**
- Consumes: everything.
- Produces: green CI-equivalent local run; cross-linked specs.

- [ ] **Step 1: Full dashboard verification**

```cmd
cd /d F:\github\Astrbot-worktrees\merge-conflict-ui\dashboard
pnpm test
pnpm typecheck
pnpm lint
```

Expected: entire vitest suite green (new + pre-existing), `vue-tsc` clean, eslint clean (auto-fix allowed). If `pnpm lint` touches unrelated pre-existing files, revert those hunks — only our files may change.

- [ ] **Step 2: Cross-link the backend spec** (plugin repo)

In `F:\github\astrbot_plugin_spcode_toolkit\docs\superpowers\specs\2026-07-28-git-merge-cherrypick-conflict-design.md`, add directly under the title:

```markdown
> Frontend wiring: [2026-08-01-git-merge-cherrypick-conflict-frontend-design.md](2026-08-01-git-merge-cherrypick-conflict-frontend-design.md)
```

Then:

```cmd
cd /d F:\github\astrbot_plugin_spcode_toolkit
git add -f docs/superpowers/specs/2026-07-28-git-merge-cherrypick-conflict-design.md
git commit -m "docs: link frontend spec for merge/cherry-pick/conflict"
```

- [ ] **Step 3: Manual smoke checklist** (record results in the final summary; needs a running AstrBot + spcode ≥ v2.22.0 and a scratch repo with two conflicting branches)

1. Merge (no conflict): branch menu → merge icon on a feature branch → default strategy → success toast; history shows the merge/ff.
2. Merge with conflict: merge a conflicting branch → warning toast → banner appears without waiting 30 s → resolve one file per-hunk (ours/theirs), one file whole-file theirs → Continue with a custom message → banner clears, history shows the merge commit.
3. Squash merge: strategy `--squash` → toast says staged-not-committed → staged scope shows the files.
4. Merge dirty-blocked: dirty worktree + merge → `worktree_dirty` toast.
5. Cherry-pick from log row: history view → hover a commit → cherry-pick → success toast; log refreshes.
6. Cherry-pick blank: history toolbar → cherry-pick → enter a sha manually → success. Enter garbage → `commit_not_found` toast.
7. Cherry-pick conflict → abort: pick a conflicting commit → banner → Abort → confirm → worktree clean, banner clears.
8. External conflict detection: run `git merge <conflicting>` in a terminal → banner appears within 30 s (polling).
9. Binary conflict: conflict on a `.png` → only whole-file buttons shown → resolve → Continue.
10. Revert conflict: revert a commit whose inverse conflicts → banner shows "revert 冲突" → resolve → Continue.

- [ ] **Step 4: Final summary**

Report: commit list (`git log --oneline master..HEAD` in the worktree), test/typecheck/lint evidence, smoke results. Do NOT push, do NOT open a PR (per project rules). Hand off to the `finishing-a-development-branch` skill for merge/cleanup decisions.


