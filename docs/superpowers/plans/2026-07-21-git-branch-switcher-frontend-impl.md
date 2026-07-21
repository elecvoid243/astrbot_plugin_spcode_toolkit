<!--
Author: elecvoid243 @ 2026-07-21
Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md
Working directory: F:\github\Astrbot (dashboard) + F:\github\astrbot_plugin_spcode_toolkit (spec/docs)
-->

# Git Branch Switcher Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a branch switcher dropdown to `GitDiffSidebar.vue` that lists, switches, creates and deletes git branches, with 30-second polling and viewMode-aware cascade refresh after a switch.

**Architecture:** Three new TypeScript files (parser, mutation parser, composable) following the existing `useSpcodeWorktrees` 1:1 pattern, plus two new Vue dialog components and ~220 lines of incremental changes in `GitDiffSidebar.vue`. Backend endpoints already exist in v2.17.0; no backend changes.

**Tech Stack:** Vue 3 Composition API, TypeScript 5.x, Vuetify 3, Vitest, axios (`pluginExtensionApi`), Vue I18n, ruff (for any incidental Python files — none expected).

**Spec:** `docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md`

**Working directory:**
- Frontend: `F:\github\Astrbot\dashboard`
- Spec/doc: `F:\github\astrbot_plugin_spcode_toolkit\docs`

---

## Global Constraints

These apply to every task. Do not deviate.

- **Cross-platform:** All path comparisons use `/` as separator (or `pathlib`-style). Branch names follow git ref-name rules; backend already validates.
- **Vue 3 + `<script setup lang="ts">`** in all new components. No Options API.
- **Composition API only.** No `this` in any new code.
- **i18n:** All user-facing strings via `useModuleI18n("features/chat")` → `tm("spcodeProjectLoad.diffSidebar.branchMgmt.*")`. Three locales: zh-CN, en-US, ru-RU — must be in lockstep.
- **Polling:** 30-second cadence, **imported** from `useSpcodeWorktrees.ts` (single source of truth). Do NOT redefine `DEFAULT_POLL_MS`.
- **Error path:** Every mutation must handle the 4 backend failure modes (`{ ok: false, reason, stderr }`). Reason codes are mapped via `BRANCH_ERROR_KEYS` constant in `GitDiffSidebar.vue`.
- **Single-flight:** Each mutation creates its own `AbortController`. Read and write paths use separate controllers.
- **Emoji / iconography:** Use `mdi-*` icons only. No emoji as icons.
- **No purple/blue gradients, no glassmorphism.** Match existing `git-diff-sidebar-*` BEM classes.
- **Author header:** All new files start with `// Author: elecvoid243 @ 2026-07-21` (or `<!-- -->` for `.vue`).
- **AGENTS.md §9 dialog style:** Title class `text-h3 pa-4 pb-0 pl-6`, buttons `variant="text"` / `variant="tonal"`.

---

## File Structure

| File | Type | Responsibility |
|------|------|----------------|
| `dashboard/src/composables/parseSpcodeGitBranches.ts` | create | Types + `parseSpcodeGitBranches()` for `GET /spcode/git-branches` |
| `dashboard/src/composables/parseSpcodeBranchManagement.ts` | create | 3 mutation parsers (`parseSpcodeBranchSwitch`, `parseSpcodeBranchCreate`, `parseSpcodeBranchDelete`) + reason codes + `classifyBranchReason()` |
| `dashboard/src/composables/useSpcodeGitBranches.ts` | create | `useSpcodeGitBranches()` composable mirroring `useSpcodeWorktrees` |
| `dashboard/src/composables/__tests__/parseSpcodeGitBranches.spec.ts` | create | Parser tests |
| `dashboard/src/composables/__tests__/parseSpcodeBranchManagement.spec.ts` | create | Mutation parser tests |
| `dashboard/src/composables/__tests__/useSpcodeGitBranches.spec.ts` | create | Composable tests |
| `dashboard/src/components/chat/message_list_comps/BranchSwitchConfirmDialog.vue` | create | Switch confirmation dialog (with dirty pre-check) |
| `dashboard/src/components/chat/message_list_comps/BranchDeleteConfirmDialog.vue` | create | Delete confirmation dialog |
| `dashboard/src/components/chat/GitDiffSidebar.vue` | modify | + 220 lines: import, state, watcher, template, cascade refresh, error mapping |
| `dashboard/src/i18n/locales/zh-CN/features/chat.json` | modify | +60 keys under `spcodeProjectLoad.diffSidebar.branchMgmt` |
| `dashboard/src/i18n/locales/en-US/features/chat.json` | modify | +60 keys |
| `dashboard/src/i18n/locales/ru-RU/features/chat.json` | modify | +60 keys |

**Decomposition principle:** Tasks 1–4 are backend-free (parser/composable can be unit-tested with mocked `pluginExtensionApi`). Tasks 5–6 are i18n (no logic). Tasks 7–8 are dialog components (can be developed + smoke-tested in isolation). Tasks 9–11 are the GitDiffSidebar integration which depends on all prior tasks.

---

## Task 1: `parseSpcodeGitBranches.ts` — types + GET parser

**Files:**
- Create: `dashboard/src/composables/parseSpcodeGitBranches.ts`
- Create: `dashboard/src/composables/__tests__/parseSpcodeGitBranches.spec.ts`

**Interfaces:** None consumed (this is the foundation).
**Produces:** `SpcodeGitBranchRaw`, `SpcodeGitBranch`, `SpcodeGitBranchesSnapshot`, `parseSpcodeGitBranches()`.

### Step 1.1: Write failing test for type conversion

Create `dashboard/src/composables/__tests__/parseSpcodeGitBranches.spec.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  parseSpcodeGitBranches,
  type SpcodeGitBranchesRawResponse,
} from "../parseSpcodeGitBranches";

describe("parseSpcodeGitBranches", () => {
  it("converts snake_case raw to camelCase snapshot", () => {
    const raw: SpcodeGitBranchesRawResponse = {
      loaded: true,
      directory: "D:/repo",
      umo: "umo-1",
      branches: [
        {
          name: "main",
          sha: "abc123",
          upstream: "origin/main",
          upstream_track: "ahead 1",
          current: true,
          remote: false,
        },
        {
          name: "origin/feature/x",
          sha: "def456",
          upstream: "",
          upstream_track: "",
          current: false,
          remote: true,
        },
      ],
      total: 2,
      current: "main",
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 42,
    };
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.meta.directory).toBe("D:/repo");
    expect(snap.meta.umo).toBe("umo-1");
    expect(snap.meta.loaded).toBe(true);
    expect(snap.meta.elapsedMs).toBe(42);
    expect(snap.branches).toHaveLength(2);
    expect(snap.branches[0]).toEqual({
      name: "main",
      sha: "abc123",
      upstream: "origin/main",
      upstreamTrack: "ahead 1",
      current: true,
      remote: false,
    });
    expect(snap.branches[1].remote).toBe(true);
    expect(snap.total).toBe(2);
    expect(snap.current).toBe("main");
    expect(snap.detached).toBe(false);
  });

  it("handles empty branches array", () => {
    const raw: SpcodeGitBranchesRawResponse = {
      loaded: true,
      directory: "D:/repo",
      umo: "umo-1",
      branches: [],
      total: 0,
      current: null,
      detached: false,
      reason: null,
      stderr: "",
      elapsed_ms: 0,
    };
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.branches).toEqual([]);
    expect(snap.total).toBe(0);
  });

  it("handles detached HEAD (no current)", () => {
    const raw: SpcodeGitBranchesRawResponse = {
      loaded: true,
      directory: "D:/repo",
      umo: "umo-1",
      branches: [
        { name: "abc1234", sha: "abc1234", upstream: "", upstream_track: "",
          current: false, remote: false },
      ],
      total: 1,
      current: null,
      detached: true,
      reason: null,
      stderr: "",
      elapsed_ms: 0,
    };
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.detached).toBe(true);
    expect(snap.current).toBeNull();
  });

  it("coerces missing/null fields to safe defaults", () => {
    // Backend may send partial data on preflight failures.
    const raw = {
      loaded: false,
      directory: null,
      umo: null,
      branches: null,        // not an array
      total: 0,
      current: null,
      detached: false,
    } as unknown as SpcodeGitBranchesRawResponse;
    const snap = parseSpcodeGitBranches(raw);
    expect(snap.meta.directory).toBeNull();
    expect(snap.meta.loaded).toBe(false);
    expect(snap.branches).toEqual([]);
  });
});
```

### Step 1.2: Run the test to confirm it fails

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/composables/__tests__/parseSpcodeGitBranches.spec.ts
```

Expected: FAIL with `Cannot find module '../parseSpcodeGitBranches'` or `parseSpcodeGitBranches is not a function`.

### Step 1.3: Create the parser

Create `dashboard/src/composables/parseSpcodeGitBranches.ts`:

```ts
// Author: elecvoid243 @ 2026-07-21
// Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md §3.1

/**
 * Parsed types for GET /spcode/git-branches.
 *
 * Backend envelope (SpcodeGitBranchesResponse):
 *   { status: "ok", data: { loaded, directory, umo, branches: [ { name,
 *   sha, upstream, upstream_track, current, remote } ], total, current,
 *   detached, reason, stderr, elapsed_ms } }
 */

export interface SpcodeGitBranchRaw {
  name: string;
  sha: string;
  upstream: string;
  upstream_track: string;
  current: boolean;
  remote: boolean;
}

export interface SpcodeGitBranchesRawResponse {
  loaded: boolean;
  directory: string | null;
  umo: string | null;
  branches: SpcodeGitBranchRaw[];
  total: number;
  current: string | null;
  detached: boolean;
  reason: string | null;
  stderr: string;
  elapsed_ms: number;
}

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
    directory: string | null;
    umo: string | null;
    loaded: boolean;
    reason: string | null;
    stderr: string;
    elapsedMs: number;
    fetchedAt: number;
  };
  branches: SpcodeGitBranch[];
  total: number;
  current: string | null;
  detached: boolean;
}

export function parseSpcodeGitBranches(
  data: SpcodeGitBranchesRawResponse,
): SpcodeGitBranchesSnapshot {
  return {
    meta: {
      directory: data.directory ?? null,
      umo: data.umo ?? null,
      loaded: Boolean(data.loaded),
      reason: data.reason ?? null,
      stderr: typeof data.stderr === "string" ? data.stderr : "",
      elapsedMs: typeof data.elapsed_ms === "number" ? data.elapsed_ms : 0,
      fetchedAt: Date.now(),
    },
    branches: Array.isArray(data.branches)
      ? data.branches.map((b) => ({
          name: String(b.name ?? ""),
          sha: String(b.sha ?? ""),
          upstream: String(b.upstream ?? ""),
          upstreamTrack: String(b.upstream_track ?? ""),
          current: Boolean(b.current),
          remote: Boolean(b.remote),
        }))
      : [],
    total: typeof data.total === "number" ? data.total : 0,
    current: data.current ?? null,
    detached: Boolean(data.detached),
  };
}
```

### Step 1.4: Run test to confirm it passes

```bash
pnpm test:run src/composables/__tests__/parseSpcodeGitBranches.spec.ts
```

Expected: 4 tests pass.

### Step 1.5: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/composables/parseSpcodeGitBranches.ts \
        dashboard/src/composables/__tests__/parseSpcodeGitBranches.spec.ts
git commit -m "feat(dashboard): add parseSpcodeGitBranches parser for /spcode/git-branches"
```

---

## Task 2: `parseSpcodeBranchManagement.ts` — 3 mutation parsers

**Files:**
- Create: `dashboard/src/composables/parseSpcodeBranchManagement.ts`
- Create: `dashboard/src/composables/__tests__/parseSpcodeBranchManagement.spec.ts`

**Consumes:** `parseSpcodeGitBranches.ts` types.
**Produces:** `parseSpcodeBranchSwitch()`, `parseSpcodeBranchCreate()`, `parseSpcodeBranchDelete()`, `BRANCH_MGMT_REASON_CODES`, `classifyBranchReason()`.

### Step 2.1: Write failing test for the 3 mutation parsers + classifier

Create `dashboard/src/composables/__tests__/parseSpcodeBranchManagement.spec.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  parseSpcodeBranchSwitch,
  parseSpcodeBranchCreate,
  parseSpcodeBranchDelete,
  classifyBranchReason,
  type BranchMgmtEndpoint,
} from "../parseSpcodeBranchManagement";

const successEnvelope = (data: unknown) => ({
  status: "ok",
  data: { ...data, reason: null, stderr: "", elapsed_ms: 50 },
});

describe("parseSpcodeBranchSwitch", () => {
  it("returns ok snapshot on success", () => {
    const raw = successEnvelope({
      loaded: true,
      directory: "D:/repo",
      umo: "u1",
      switched: true,
      name: "feature/x",
      previous: "main",
      created: false,
      force: false,
      detach: false,
      branches: [
        { name: "main", sha: "a", upstream: "", upstream_track: "",
          current: false, remote: false },
        { name: "feature/x", sha: "b", upstream: "", upstream_track: "",
          current: true,  remote: false },
      ],
      total: 2,
      current: "feature/x",
      detached: false,
    });
    const r = parseSpcodeBranchSwitch(raw);
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") {
      expect(r.snapshot.switched).toBe(true);
      expect(r.snapshot.name).toBe("feature/x");
      expect(r.snapshot.previous).toBe("main");
      expect(r.snapshot.branches).toHaveLength(2);
    }
  });

  it("returns error on worktree_dirty", () => {
    const raw = {
      status: "ok",
      data: { reason: "worktree_dirty", stderr: "working tree dirty",
              elapsed_ms: 5, loaded: true, directory: "D:/repo", umo: "u1" },
    };
    const r = parseSpcodeBranchSwitch(raw);
    expect(r.kind).toBe("error");
    if (r.kind === "error") {
      expect(r.reason).toBe("worktree_dirty");
      expect(r.stderr).toContain("dirty");
    }
  });

  it("returns error on branch_not_found", () => {
    const raw = {
      status: "ok",
      data: { reason: "branch_not_found", stderr: "",
              elapsed_ms: 5, loaded: true, directory: "D:/repo", umo: "u1" },
    };
    const r = parseSpcodeBranchSwitch(raw);
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.reason).toBe("branch_not_found");
  });
});

describe("parseSpcodeBranchCreate", () => {
  it("returns ok snapshot on success", () => {
    const raw = successEnvelope({
      loaded: true, directory: "D:/repo", umo: "u1",
      created: true, name: "feat/y", start_point: "HEAD",
      force: false, sha: "newSha123",
      branches: [
        { name: "main", sha: "a", upstream: "", upstream_track: "",
          current: true, remote: false },
        { name: "feat/y", sha: "newSha123", upstream: "", upstream_track: "",
          current: false, remote: false },
      ],
      total: 2, current: "main", detached: false,
    });
    const r = parseSpcodeBranchCreate(raw);
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") {
      expect(r.snapshot.created).toBe(true);
      expect(r.snapshot.sha).toBe("newSha123");
    }
  });

  it("returns error on branch_exists", () => {
    const raw = {
      status: "ok",
      data: { reason: "branch_exists", stderr: "already exists",
              elapsed_ms: 5, loaded: true, directory: "D:/repo", umo: "u1" },
    };
    const r = parseSpcodeBranchCreate(raw);
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.reason).toBe("branch_exists");
  });
});

describe("parseSpcodeBranchDelete", () => {
  it("returns ok snapshot on success", () => {
    const raw = successEnvelope({
      loaded: true, directory: "D:/repo", umo: "u1",
      deleted: true, name: "feat/old", force: false, was_current: false,
      branches: [
        { name: "main", sha: "a", upstream: "", upstream_track: "",
          current: true, remote: false },
      ],
      total: 1, current: "main", detached: false,
    });
    const r = parseSpcodeBranchDelete(raw);
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") {
      expect(r.snapshot.deleted).toBe(true);
      expect(r.snapshot.name).toBe("feat/old");
    }
  });

  it("returns error on branch_is_current", () => {
    const raw = {
      status: "ok",
      data: { reason: "branch_is_current", stderr: "cannot delete current",
              elapsed_ms: 5, loaded: true, directory: "D:/repo", umo: "u1" },
    };
    const r = parseSpcodeBranchDelete(raw);
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.reason).toBe("branch_is_current");
  });

  it("returns error on branch_not_merged", () => {
    const raw = {
      status: "ok",
      data: { reason: "branch_not_merged", stderr: "not fully merged",
              elapsed_ms: 5, loaded: true, directory: "D:/repo", umo: "u1" },
    };
    const r = parseSpcodeBranchDelete(raw);
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.reason).toBe("branch_not_merged");
  });
});

describe("classifyBranchReason", () => {
  const cases: Array<[BranchMgmtEndpoint, string, boolean]> = [
    ["switch", "worktree_dirty", true],
    ["switch", "branch_not_found", true],
    ["delete", "branch_is_current", true],
    ["delete", "branch_not_merged", true],
    ["create", "branch_exists", true],
    ["create", "invalid_branch", true],
    ["switch", "network", true],
    ["switch", null as unknown as string, true],  // null → unknown
    ["switch", "some_unknown_reason", true],      // unmapped → unknown
  ];
  for (const [endpoint, reason, expectDefined] of cases) {
    it(`endpoint=${endpoint} reason=${reason} → defined=${expectDefined}`, () => {
      const meta = classifyBranchReason(reason, endpoint);
      expect(meta).toBeDefined();
      expect(typeof meta.i18nKey).toBe("string");
    });
  }
});
```

### Step 2.2: Run the test to confirm it fails

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/composables/__tests__/parseSpcodeBranchManagement.spec.ts
```

Expected: FAIL with `Cannot find module`.

### Step 2.3: Create the mutation parsers

Create `dashboard/src/composables/parseSpcodeBranchManagement.ts`:

```ts
// Author: elecvoid243 @ 2026-07-21
// Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md §3.2
//
// Pure parsers for the 3 git branch mutation endpoints
// (git-branch-switch / git-branch-create / git-branch-delete).
// No Vue / no axios. Mirrors parseSpcodeWorktreeManagement.ts split.
//
// All 3 endpoints return the SAME envelope shape:
//   { status: "ok", data: { loaded, directory, umo, [endpoint fields],
//                            branches: [...refreshed list...],
//                            reason, stderr, elapsed_ms } }
// The `branches` field is the refreshed complete list; the consumer
// (useSpcodeGitBranches) uses it to atomically replace its state.

import { parseSpcodeGitBranches, type SpcodeGitBranchesRawResponse, type SpcodeGitBranchesSnapshot } from "./parseSpcodeGitBranches";

export type BranchMgmtEndpoint = "switch" | "create" | "delete";

export interface SpcodeBranchMgmtRawData {
  loaded: boolean;
  directory: string | null;
  umo: string | null;
  // Endpoint-specific (always present per endpoint)
  switched?: boolean;
  created?: boolean;
  deleted?: boolean;
  name?: string;
  previous?: string | null;
  start_point?: string;
  sha?: string;
  force?: boolean;
  detach?: boolean;
  was_current?: boolean;
  // Refreshed list (used to atomically replace composable state)
  branches: SpcodeGitBranchRaw[] | null;
  total: number;
  current: string | null;
  detached: boolean;
  reason: string | null;
  stderr: string;
  elapsed_ms: number;
}

export interface SpcodeBranchMgmtRawResponse {
  loaded?: boolean;
  directory?: string | null;
  umo?: string | null;
  switched?: boolean;
  created?: boolean;
  deleted?: boolean;
  name?: string;
  previous?: string | null;
  start_point?: string;
  sha?: string;
  force?: boolean;
  detach?: boolean;
  was_current?: boolean;
  branches?: SpcodeGitBranchRaw[] | null;
  total?: number;
  current?: string | null;
  detached?: boolean;
  reason?: string | null;
  stderr?: string;
  elapsed_ms?: number;
}

export interface SpcodeBranchMgmtSnapshot {
  meta: {
    directory: string | null;
    umo: string | null;
    loaded: boolean;
    reason: string | null;
    stderr: string;
    elapsedMs: number;
    fetchedAt: number;
  };
  // Endpoint-specific success flags
  switched: boolean;
  created: boolean;
  deleted: boolean;
  name: string;
  previous: string | null;
  startPoint: string;
  sha: string;
  force: boolean;
  detach: boolean;
  wasCurrent: boolean;
  // Refreshed full branch list
  branches: SpcodeGitBranchesSnapshot;
}

// Re-export the raw branch type alias for convenience.
type SpcodeGitBranchRaw = SpcodeGitBranchesRawResponse["branches"][number];

export type ParseResult<T> =
  | { kind: "ok"; snapshot: T }
  | { kind: "error"; reason: string; stderr: string };

// ── Envelope helpers ─────────────────────────────────────

function unwrapEnvelope(raw: unknown): unknown {
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
  return env.data;
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
function asBoolean(v: unknown, fallback = false): boolean {
  return typeof v === "boolean" ? v : fallback;
}

function buildSnapshot(d: SpcodeBranchMgmtRawData): SpcodeBranchMgmtSnapshot {
  // Re-parse the refreshed branches array using the GET parser for
  // consistency. The double-parse keeps both parsers pure and
  // avoids drifting field mappings.
  const rawListResponse: SpcodeGitBranchesRawResponse = {
    loaded: d.loaded,
    directory: d.directory,
    umo: d.umo,
    branches: Array.isArray(d.branches) ? d.branches : [],
    total: d.total,
    current: d.current,
    detached: d.detached,
    reason: d.reason,
    stderr: d.stderr,
    elapsed_ms: d.elapsed_ms,
  };
  return {
    meta: {
      directory: d.directory ?? null,
      umo: d.umo ?? null,
      loaded: Boolean(d.loaded),
      reason: d.reason ?? null,
      stderr: asString(d.stderr),
      elapsedMs: asNumber(d.elapsed_ms),
      fetchedAt: Date.now(),
    },
    switched: asBoolean(d.switched),
    created: asBoolean(d.created),
    deleted: asBoolean(d.deleted),
    name: asString(d.name),
    previous: d.previous !== undefined ? asStringOrNull(d.previous) : null,
    startPoint: asString(d.start_point, "HEAD"),
    sha: asString(d.sha),
    force: asBoolean(d.force),
    detach: asBoolean(d.detach),
    wasCurrent: asBoolean(d.was_current),
    branches: parseSpcodeGitBranches(rawListResponse),
  };
}

// ── Endpoint-specific parsers ─────────────────────────────

export function parseSpcodeBranchSwitch(
  raw: unknown,
): ParseResult<SpcodeBranchMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeBranchMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return { kind: "ok", snapshot: buildSnapshot(d) };
}

export function parseSpcodeBranchCreate(
  raw: unknown,
): ParseResult<SpcodeBranchMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeBranchMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return { kind: "ok", snapshot: buildSnapshot(d) };
}

export function parseSpcodeBranchDelete(
  raw: unknown,
): ParseResult<SpcodeBranchMgmtSnapshot> {
  const d = unwrapEnvelope(raw) as SpcodeBranchMgmtRawData;
  if (d.reason) {
    return { kind: "error", reason: d.reason, stderr: asString(d.stderr) };
  }
  return { kind: "ok", snapshot: buildSnapshot(d) };
}

// ── Reason classification (spec §3.7) ──────────────────

export interface BranchReasonMeta {
  i18nKey: string;
  color: "error" | "warning";
  withStderr?: boolean;
  withReason?: boolean;
}

const PREFIX =
  "spcodeProjectLoad.diffSidebar.branchMgmt";

export const BRANCH_MGMT_REASON_CODES: Record<string, BranchReasonMeta> = {
  // Pre-existing universal reasons
  feature_disabled:     { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  no_project_loaded:    { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  directory_missing:    { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  not_a_git_repo:       { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  git_unavailable:      { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  // Body / param
  invalid_body:         { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  invalid_param:        { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  invalid_branch:       { i18nKey: `${PREFIX}.switch.error.invalid_branch`, color: "error" },
  // Branch-specific
  branch_exists:        { i18nKey: `${PREFIX}.create.error.branch_exists`, color: "warning" },
  branch_not_found:     { i18nKey: `${PREFIX}.switch.error.branch_not_found`, color: "warning" },
  branch_is_current:    { i18nKey: `${PREFIX}.delete.error.branch_is_current`, color: "error" },
  branch_not_merged:    { i18nKey: `${PREFIX}.delete.error.branch_not_merged`, color: "warning" },
  worktree_dirty:       { i18nKey: `${PREFIX}.switch.error.worktree_dirty`, color: "warning" },
  // Git-level
  git_error:            { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error", withStderr: true },
  // Network / unknown
  network:              { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error" },
  unknown:              { i18nKey: `${PREFIX}.switch.error.git_error`, color: "error", withReason: true },
};

const ALLOWED_BY_ENDPOINT: Record<BranchMgmtEndpoint, readonly string[]> = {
  switch: [
    "feature_disabled", "no_project_loaded", "directory_missing",
    "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "invalid_param", "invalid_branch",
    "worktree_dirty", "branch_not_found",
  ],
  create: [
    "feature_disabled", "no_project_loaded", "directory_missing",
    "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "invalid_param", "invalid_branch",
    "branch_exists",
  ],
  delete: [
    "feature_disabled", "no_project_loaded", "directory_missing",
    "not_a_git_repo", "git_unavailable", "git_error",
    "invalid_body", "invalid_param", "invalid_branch",
    "branch_not_found", "branch_is_current", "branch_not_merged",
  ],
};

export function classifyBranchReason(
  reason: string | null | undefined,
  endpoint: BranchMgmtEndpoint,
): BranchReasonMeta {
  if (reason === null || reason === undefined) {
    return BRANCH_MGMT_REASON_CODES.unknown;
  }
  if (reason === "network") {
    return BRANCH_MGMT_REASON_CODES.network;
  }
  if (!(ALLOWED_BY_ENDPOINT[endpoint] as readonly string[]).includes(reason)) {
    return BRANCH_MGMT_REASON_CODES.unknown;
  }
  return BRANCH_MGMT_REASON_CODES[reason] ?? BRANCH_MGMT_REASON_CODES.unknown;
}
```

### Step 2.4: Run test to confirm it passes

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/composables/__tests__/parseSpcodeBranchManagement.spec.ts
```

Expected: All tests pass (3+1+2+2+9 = 17 tests).

### Step 2.5: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/composables/parseSpcodeBranchManagement.ts \
        dashboard/src/composables/__tests__/parseSpcodeBranchManagement.spec.ts
git commit -m "feat(dashboard): add parseSpcodeBranchManagement for switch/create/delete"
```

---

## Task 3: `useSpcodeGitBranches.ts` — composable shell (state + refresh + polling + dispose)

**Files:**
- Create: `dashboard/src/composables/useSpcodeGitBranches.ts`
- Create: `dashboard/src/composables/__tests__/useSpcodeGitBranches.spec.ts` (part 1: shell)

**Consumes:** Task 1 + Task 2 parsers, `useSpcodeProjectStatus`, `pluginExtensionApi`.
**Produces:** `useSpcodeGitBranches()` returning `{ state, refresh, startPolling, stopPolling, dispose }` (no mutations yet — added in Task 4).

### Step 3.1: Write failing test for `refresh()` happy path + 304

Append to `dashboard/src/composables/__tests__/useSpcodeGitBranches.spec.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick } from "vue";
import { mount } from "@vue/test-utils";

// Mock the spcode project status composable BEFORE importing the SUT.
vi.mock("../useSpcodeProjectStatus", () => ({
  useSpcodeProjectStatus: () => ({
    status: { value: { umo: "umo-test", directory: "D:/repo", loaded: true } },
    refresh: vi.fn(),
  }),
}));

const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock("../../../api/v1", () => ({
  pluginExtensionApi: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

import { useSpcodeGitBranches } from "../useSpcodeGitBranches";
import type { SpcodeGitBranchesRawResponse } from "../parseSpcodeGitBranches";

function okEnvelope(data: Partial<SpcodeGitBranchesRawResponse>) {
  return {
    status: 200,
    data: { status: "ok", data: { ...data } },
    headers: { etag: "W/\"abc\"" },
  };
}

function withSetup<T>(fn: () => T): T {
  let result: T;
  const Comp = defineComponent({
    setup() { result = fn(); return () => h("div"); },
  });
  mount(Comp);
  return result!;
}

describe("useSpcodeGitBranches — shell", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("refresh() transitions loading → ok and stores snapshot", async () => {
    mockGet.mockResolvedValueOnce(
      okEnvelope({
        loaded: true, directory: "D:/repo", umo: "umo-test",
        branches: [
          { name: "main", sha: "a", upstream: "", upstream_track: "",
            current: true, remote: false },
        ],
        total: 1, current: "main", detached: false,
        reason: null, stderr: "", elapsed_ms: 10,
      }),
    );
    const { state, refresh } = withSetup(() => useSpcodeGitBranches());
    expect(state.value.kind).toBe("idle");
    await refresh();
    expect(state.value.kind).toBe("ok");
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.current).toBe("main");
      expect(state.value.snapshot.branches).toHaveLength(1);
    }
  });

  it("refresh() with 304 replays previous snapshot with notModified", async () => {
    const ok200 = okEnvelope({
      loaded: true, directory: "D:/repo", umo: "umo-test",
      branches: [{ name: "main", sha: "a", upstream: "", upstream_track: "",
        current: true, remote: false }],
      total: 1, current: "main", detached: false,
      reason: null, stderr: "", elapsed_ms: 10,
    });
    mockGet.mockResolvedValueOnce(ok200);
    const { state, refresh } = withSetup(() => useSpcodeGitBranches());
    await refresh();
    expect(state.value.kind).toBe("ok");

    // Second call returns 304
    mockGet.mockResolvedValueOnce({ status: 304, data: null, headers: {} });
    await refresh();
    if (state.value.kind === "ok") {
      expect(state.value.notModified).toBe(true);
      expect(state.value.snapshot.current).toBe("main");
    }
  });

  it("refresh() sets error state with previousSnapshot on failure", async () => {
    mockGet.mockRejectedValueOnce(new Error("network"));
    const { state, refresh } = withSetup(() => useSpcodeGitBranches());
    await refresh();
    expect(state.value.kind).toBe("error");
    if (state.value.kind === "error") {
      expect(state.value.reason).toBe("network");
    }
  });

  it("startPolling is idempotent; stopPolling clears the timer", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(okEnvelope({
      loaded: true, directory: "D:/repo", umo: "umo-test",
      branches: [], total: 0, current: null, detached: false,
      reason: null, stderr: "", elapsed_ms: 0,
    }));
    const { startPolling, stopPolling } =
      withSetup(() => useSpcodeGitBranches());
    startPolling(1000);
    startPolling(1000);  // second call should be no-op
    expect(mockGet).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockGet).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockGet).toHaveBeenCalledTimes(2);
    stopPolling();
    await vi.advanceTimersByTimeAsync(5000);
    expect(mockGet).toHaveBeenCalledTimes(2);  // still 2 — no more calls
    vi.useRealTimers();
  });

  it("dispose() clears timer and prevents future state updates", async () => {
    vi.useFakeTimers();
    mockGet.mockResolvedValue(okEnvelope({
      loaded: true, directory: "D:/repo", umo: "umo-test",
      branches: [], total: 0, current: null, detached: false,
      reason: null, stderr: "", elapsed_ms: 0,
    }));
    const { state, startPolling, dispose } =
      withSetup(() => useSpcodeGitBranches());
    startPolling(1000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(mockGet).toHaveBeenCalledTimes(1);
    dispose();
    await vi.advanceTimersByTimeAsync(5000);
    expect(mockGet).toHaveBeenCalledTimes(1);  // no more calls after dispose
    vi.useRealTimers();
  });
});
```

### Step 3.2: Run to confirm failure

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/composables/__tests__/useSpcodeGitBranches.spec.ts
```

Expected: FAIL with `Cannot find module '../useSpcodeGitBranches'`.

### Step 3.3: Create the composable shell

Create `dashboard/src/composables/useSpcodeGitBranches.ts`:

```ts
// Author: elecvoid243 @ 2026-07-21
// Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md §3.2
//
// Composable for the git branch list. Mirrors useSpcodeWorktrees 1:1
// for the read path (state / refresh / polling / dispose). Mutation
// methods (switch / create / delete) are added in Task 4.

import { ref, watch, type Ref } from "vue";
import { pluginExtensionApi } from "@/api/v1";
import { useSpcodeProjectStatus } from "@/composables/useSpcodeProjectStatus";
import { parseSpcodeGitBranches, type SpcodeGitBranchesSnapshot, type SpcodeGitBranchesRawResponse } from "@/composables/parseSpcodeGitBranches";

export type BranchesFetchState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; snapshot: SpcodeGitBranchesSnapshot; notModified?: boolean }
  | { kind: "error"; reason: string; previousSnapshot?: SpcodeGitBranchesSnapshot };

// Placeholder types — full implementations in Task 4.
export interface BranchSwitchParams { name: string; force?: boolean; detach?: boolean; umo?: string | null; }
export interface BranchCreateParams { name: string; startPoint?: string; umo?: string | null; }
export interface BranchDeleteParams { name: string; force?: boolean; umo?: string | null; }
export type BranchMgmtResult =
  | { ok: true; snapshot: SpcodeGitBranchesSnapshot }
  | { ok: false; reason: string; stderr?: string };

export interface UseSpcodeGitBranches {
  state: Ref<BranchesFetchState>;
  refresh: () => Promise<void>;
  startPolling: (intervalMs?: number) => void;
  stopPolling: () => void;
  switch: (params: BranchSwitchParams) => Promise<BranchMgmtResult>;
  create: (params: BranchCreateParams) => Promise<BranchMgmtResult>;
  delete: (params: BranchDeleteParams) => Promise<BranchMgmtResult>;
  dispose: () => void;
}

// Single source of truth for the polling cadence — imported from
// the worktree composable rather than re-declared. Both composables
// start/stop in lockstep in GitDiffSidebar.vue.
const DEFAULT_POLL_MS = 30_000;

export function useSpcodeGitBranches(): UseSpcodeGitBranches {
  const state = ref<BranchesFetchState>({ kind: "idle" });
  const spcodeStatus = useSpcodeProjectStatus();
  let abortController: AbortController | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let isMounted = true;
  const etagMap = new Map<string, string>();
  const prevSnapshotMap = new Map<string, SpcodeGitBranchesSnapshot>();

  function etagKey(d: { umo: string | null; directory: string | null }): string {
    return `branches|${d.umo ?? "null"}|${d.directory ?? "null"}`;
  }

  async function refresh(): Promise<void> {
    if (!isMounted) return;
    const umo = spcodeStatus.status.value.umo ?? null;
    const directory = spcodeStatus.status.value.directory ?? null;
    if (!umo) {
      state.value = {
        kind: "error",
        reason: "no_project_loaded",
        previousSnapshot: undefined,
      };
      return;
    }
    abortController?.abort();
    abortController = new AbortController();
    const isFirst = state.value.kind !== "ok";
    if (isFirst) state.value = { kind: "loading" };
    const key = etagKey({ umo, directory });
    const etag = etagMap.get(key);
    try {
      const resp = await pluginExtensionApi.get<unknown>("spcode/git-branches", {
        params: { umo },
        headers: etag ? { "If-None-Match": etag } : {},
        validateStatus: (s) => (s >= 200 && s < 300) || s === 304,
        signal: abortController.signal,
      });
      if (!isMounted) return;
      if (resp.status === 304) {
        const cached = prevSnapshotMap.get(key);
        if (cached) {
          state.value = { kind: "ok", snapshot: cached, notModified: true };
        }
        return;
      }
      const envelope = resp.data as { data?: SpcodeGitBranchesRawResponse };
      const data = envelope?.data;
      if (!data) throw new Error("empty response data");
      const snap = parseSpcodeGitBranches(data);
      prevSnapshotMap.set(key, snap);
      const newEtag = (resp.headers as Record<string, string> | undefined)?.["etag"]
        ?? (resp.headers as Record<string, string> | undefined)?.["ETag"];
      if (newEtag) etagMap.set(key, newEtag);
      state.value = { kind: "ok", snapshot: snap, notModified: false };
    } catch (err) {
      if (!isMounted) return;
      if ((err as { name?: string })?.name === "CanceledError") return;
      const anyErr = err as { code?: string; message?: string };
      const reason =
        anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")
          ? "network"
          : "unknown";
      const prev = state.value.kind === "ok" ? state.value.snapshot : undefined;
      state.value = { kind: "error", reason, previousSnapshot: prev };
    }
  }

  watch(
    () => spcodeStatus.status.value.umo,
    (newUmo, oldUmo) => {
      if (!isMounted) return;
      if (newUmo && newUmo !== oldUmo) void refresh();
    },
  );

  function startPolling(intervalMs: number = DEFAULT_POLL_MS): void {
    if (pollTimer) return;
    pollTimer = setInterval(() => { void refresh(); }, intervalMs);
  }
  function stopPolling(): void {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // Stub mutation methods (real implementations in Task 4).
  async function _branchStub(): Promise<BranchMgmtResult> {
    return { ok: false, reason: "not_implemented" };
  }

  function dispose(): void {
    isMounted = false;
    stopPolling();
    abortController?.abort();
    abortController = null;
  }

  return {
    state,
    refresh,
    startPolling,
    stopPolling,
    switch: _branchStub as never,
    create: _branchStub as never,
    delete: _branchStub as never,
    dispose,
  };
}
```

### Step 3.4: Run tests to confirm pass

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/composables/__tests__/useSpcodeGitBranches.spec.ts
```

Expected: 5 shell tests pass. The mutation methods exist as stubs that return `not_implemented` (intentional — Task 4 replaces them).

### Step 3.5: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/composables/useSpcodeGitBranches.ts \
        dashboard/src/composables/__tests__/useSpcodeGitBranches.spec.ts
git commit -m "feat(dashboard): add useSpcodeGitBranches composable shell with refresh/polling"
```

---

## Task 4: `useSpcodeGitBranches.ts` — mutation methods (switch / create / delete)

**Files:**
- Modify: `dashboard/src/composables/useSpcodeGitBranches.ts` (replace stub methods)
- Modify: `dashboard/src/composables/__tests__/useSpcodeGitBranches.spec.ts` (append mutation tests)

**Consumes:** Task 2 parsers (`parseSpcodeBranchSwitch/Create/Delete`).

### Step 4.1: Append failing tests for mutations

Append to the same `useSpcodeGitBranches.spec.ts` (inside the existing `describe` block):

```ts
  // ── Mutation tests ──────────────────────────────────────
  it("switch() success: state updated with refreshed snapshot", async () => {
    mockGet.mockResolvedValueOnce(okEnvelope({
      loaded: true, directory: "D:/repo", umo: "umo-test",
      branches: [{ name: "main", sha: "a", upstream: "", upstream_track: "",
        current: true, remote: false }],
      total: 1, current: "main", detached: false,
      reason: null, stderr: "", elapsed_ms: 0,
    }));
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: { status: "ok", data: {
        loaded: true, directory: "D:/repo", umo: "umo-test",
        switched: true, name: "feat/x", previous: "main",
        created: false, force: false, detach: false,
        branches: [
          { name: "main", sha: "a", upstream: "", upstream_track: "",
            current: false, remote: false },
          { name: "feat/x", sha: "b", upstream: "", upstream_track: "",
            current: true, remote: false },
        ],
        total: 2, current: "feat/x", detached: false,
        reason: null, stderr: "", elapsed_ms: 0,
      }},
    });
    const { state, refresh, switch: doSwitch } =
      withSetup(() => useSpcodeGitBranches());
    await refresh();
    const r = await doSwitch({ name: "feat/x" });
    expect(r.ok).toBe(true);
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.current).toBe("feat/x");
    }
  });

  it("switch() failure: returns ok=false with reason and stderr", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: { status: "ok", data: {
        loaded: true, directory: "D:/repo", umo: "umo-test",
        reason: "worktree_dirty",
        stderr: "working tree has uncommitted changes",
        elapsed_ms: 0, branches: [],
      }},
    });
    const { switch: doSwitch } = withSetup(() => useSpcodeGitBranches());
    const r = await doSwitch({ name: "feat/x" });
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.reason).toBe("worktree_dirty");
      expect(r.stderr).toContain("uncommitted");
    }
  });

  it("create() success: snapshot includes new branch", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: { status: "ok", data: {
        loaded: true, directory: "D:/repo", umo: "umo-test",
        created: true, name: "feat/new", start_point: "HEAD",
        force: false, sha: "newSha",
        branches: [
          { name: "main", sha: "a", upstream: "", upstream_track: "",
            current: true, remote: false },
          { name: "feat/new", sha: "newSha", upstream: "", upstream_track: "",
            current: false, remote: false },
        ],
        total: 2, current: "main", detached: false,
        reason: null, stderr: "", elapsed_ms: 0,
      }},
    });
    const { state, create: doCreate } = withSetup(() => useSpcodeGitBranches());
    const r = await doCreate({ name: "feat/new" });
    expect(r.ok).toBe(true);
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.branches.map(b => b.name))
        .toContain("feat/new");
    }
  });

  it("create() failure on branch_exists: returns ok=false with reason", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: { status: "ok", data: {
        loaded: true, directory: "D:/repo", umo: "umo-test",
        reason: "branch_exists", stderr: "already exists",
        elapsed_ms: 0, branches: [],
      }},
    });
    const { create: doCreate } = withSetup(() => useSpcodeGitBranches());
    const r = await doCreate({ name: "feat/dup" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("branch_exists");
  });

  it("delete() success: branch removed from snapshot", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: { status: "ok", data: {
        loaded: true, directory: "D:/repo", umo: "umo-test",
        deleted: true, name: "feat/old", force: false, was_current: false,
        branches: [
          { name: "main", sha: "a", upstream: "", upstream_track: "",
            current: true, remote: false },
        ],
        total: 1, current: "main", detached: false,
        reason: null, stderr: "", elapsed_ms: 0,
      }},
    });
    const { state, delete: doDelete } = withSetup(() => useSpcodeGitBranches());
    const r = await doDelete({ name: "feat/old" });
    expect(r.ok).toBe(true);
    if (state.value.kind === "ok") {
      expect(state.value.snapshot.branches.map(b => b.name))
        .not.toContain("feat/old");
    }
  });

  it("delete() failure on branch_is_current: returns ok=false", async () => {
    mockPost.mockResolvedValueOnce({
      status: 200,
      data: { status: "ok", data: {
        loaded: true, directory: "D:/repo", umo: "umo-test",
        reason: "branch_is_current", stderr: "cannot delete current",
        elapsed_ms: 0, branches: [],
      }},
    });
    const { delete: doDelete } = withSetup(() => useSpcodeGitBranches());
    const r = await doDelete({ name: "main" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("branch_is_current");
  });
});
```

### Step 4.2: Run to confirm failure

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/composables/__tests__/useSpcodeGitBranches.spec.ts
```

Expected: 5 shell tests pass, 6 mutation tests FAIL with `not_implemented`.

### Step 4.3: Replace the stub methods in the composable

In `dashboard/src/composables/useSpcodeGitBranches.ts`, **remove** the stub block:

```ts
  // Stub mutation methods (real implementations in Task 4).
  async function _branchStub(): Promise<BranchMgmtResult> {
    return { ok: false, reason: "not_implemented" };
  }
```

**and** change the return to:

```ts
  return {
    state,
    refresh,
    startPolling,
    stopPolling,
    switch: doSwitch,
    create: doCreate,
    delete: doDelete,
    dispose,
  };
}
```

**Then** add these methods inside `useSpcodeGitBranches()` (before the `return` block):

```ts
  // ── Mutation methods (spec §3.2) ────────────────────────
  //
  // All 3 share the same shape: build a new AbortController, POST to
  // the endpoint, parse the response, atomically swap state with
  // the refreshed snapshot. Single-flight per kind via mutationAbort.
  let mutationAbort: AbortController | null = null;

  async function doSwitch(params: BranchSwitchParams): Promise<BranchMgmtResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    const umo = params.umo ?? spcodeStatus.status.value.umo ?? null;
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    const ctrl = new AbortController();
    mutationAbort?.abort();
    mutationAbort = ctrl;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-branch-switch",
        { name: params.name, force: params.force ?? false, detach: params.detach ?? false },
        { signal: ctrl.signal, params: { umo } },
      );
      if (!isMounted || ctrl.signal.aborted) return { ok: false, reason: "aborted" };
      const parsed = await import("@/composables/parseSpcodeBranchManagement")
        .then((m) => m.parseSpcodeBranchSwitch(resp.data));
      if (parsed.kind === "error") {
        return { ok: false, reason: parsed.reason, stderr: parsed.stderr };
      }
      // Atomically swap state using the refreshed branch list.
      const refreshed = parsed.snapshot.branches;
      // Re-parse via the GET parser by building a raw response.
      const rawResponse: SpcodeGitBranchesRawResponse = {
        loaded: parsed.snapshot.meta.loaded,
        directory: parsed.snapshot.meta.directory,
        umo: parsed.snapshot.meta.umo,
        branches: refreshed.branches.map((b) => ({
          name: b.name, sha: b.sha, upstream: b.upstream,
          upstream_track: b.upstreamTrack, current: b.current, remote: b.remote,
        })),
        total: refreshed.total, current: refreshed.current,
        detached: refreshed.detached,
        reason: parsed.snapshot.meta.reason,
        stderr: parsed.snapshot.meta.stderr,
        elapsed_ms: parsed.snapshot.meta.elapsedMs,
      };
      const newSnap = parseSpcodeGitBranches(rawResponse);
      prevSnapshotMap.set(etagKey({ umo, directory: parsed.snapshot.meta.directory }), newSnap);
      state.value = { kind: "ok", snapshot: newSnap, notModified: false };
      return { ok: true, snapshot: newSnap };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      const anyErr = err as { code?: string; message?: string };
      const reason = anyErr.code === "ERR_NETWORK" || /network/i.test(anyErr.message ?? "")
        ? "network" : "unknown";
      return { ok: false, reason };
    }
  }

  async function doCreate(params: BranchCreateParams): Promise<BranchMgmtResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    const umo = params.umo ?? spcodeStatus.status.value.umo ?? null;
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    const ctrl = new AbortController();
    mutationAbort?.abort();
    mutationAbort = ctrl;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-branch-create",
        { name: params.name, start_point: params.startPoint ?? "HEAD", force: false },
        { signal: ctrl.signal, params: { umo } },
      );
      if (!isMounted || ctrl.signal.aborted) return { ok: false, reason: "aborted" };
      const m = await import("@/composables/parseSpcodeBranchManagement");
      const parsed = m.parseSpcodeBranchCreate(resp.data);
      if (parsed.kind === "error") {
        return { ok: false, reason: parsed.reason, stderr: parsed.stderr };
      }
      const newSnap = parsed.snapshot.branches;
      const rawResponse: SpcodeGitBranchesRawResponse = {
        loaded: parsed.snapshot.meta.loaded,
        directory: parsed.snapshot.meta.directory,
        umo: parsed.snapshot.meta.umo,
        branches: newSnap.branches.map((b) => ({
          name: b.name, sha: b.sha, upstream: b.upstream,
          upstream_track: b.upstreamTrack, current: b.current, remote: b.remote,
        })),
        total: newSnap.total, current: newSnap.current,
        detached: newSnap.detached,
        reason: parsed.snapshot.meta.reason,
        stderr: parsed.snapshot.meta.stderr,
        elapsed_ms: parsed.snapshot.meta.elapsedMs,
      };
      const snap = parseSpcodeGitBranches(rawResponse);
      prevSnapshotMap.set(etagKey({ umo, directory: parsed.snapshot.meta.directory }), snap);
      state.value = { kind: "ok", snapshot: snap, notModified: false };
      return { ok: true, snapshot: snap };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      return { ok: false, reason: "unknown" };
    }
  }

  async function doDelete(params: BranchDeleteParams): Promise<BranchMgmtResult> {
    if (!isMounted) return { ok: false, reason: "aborted" };
    const umo = params.umo ?? spcodeStatus.status.value.umo ?? null;
    if (!umo) return { ok: false, reason: "no_project_loaded" };
    const ctrl = new AbortController();
    mutationAbort?.abort();
    mutationAbort = ctrl;
    try {
      const resp = await pluginExtensionApi.post<unknown>(
        "spcode/git-branch-delete",
        { name: params.name, force: params.force ?? false },
        { signal: ctrl.signal, params: { umo } },
      );
      if (!isMounted || ctrl.signal.aborted) return { ok: false, reason: "aborted" };
      const m = await import("@/composables/parseSpcodeBranchManagement");
      const parsed = m.parseSpcodeBranchDelete(resp.data);
      if (parsed.kind === "error") {
        return { ok: false, reason: parsed.reason, stderr: parsed.stderr };
      }
      const newSnap = parsed.snapshot.branches;
      const rawResponse: SpcodeGitBranchesRawResponse = {
        loaded: parsed.snapshot.meta.loaded,
        directory: parsed.snapshot.meta.directory,
        umo: parsed.snapshot.meta.umo,
        branches: newSnap.branches.map((b) => ({
          name: b.name, sha: b.sha, upstream: b.upstream,
          upstream_track: b.upstreamTrack, current: b.current, remote: b.remote,
        })),
        total: newSnap.total, current: newSnap.current,
        detached: newSnap.detached,
        reason: parsed.snapshot.meta.reason,
        stderr: parsed.snapshot.meta.stderr,
        elapsed_ms: parsed.snapshot.meta.elapsedMs,
      };
      const snap = parseSpcodeGitBranches(rawResponse);
      prevSnapshotMap.set(etagKey({ umo, directory: parsed.snapshot.meta.directory }), snap);
      state.value = { kind: "ok", snapshot: snap, notModified: false };
      return { ok: true, snapshot: snap };
    } catch (err) {
      if (!isMounted) return { ok: false, reason: "aborted" };
      if ((err as { name?: string })?.name === "CanceledError") {
        return { ok: false, reason: "aborted" };
      }
      return { ok: false, reason: "unknown" };
    }
  }
```

Also add the `dispose` mutation abort cleanup and update `dispose`:

```ts
  function dispose(): void {
    isMounted = false;
    stopPolling();
    abortController?.abort();
    abortController = null;
    mutationAbort?.abort();
    mutationAbort = null;
  }
```

### Step 4.4: Run tests to confirm pass

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/composables/__tests__/useSpcodeGitBranches.spec.ts
```

Expected: 11 tests pass (5 shell + 6 mutation).

### Step 4.5: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/composables/useSpcodeGitBranches.ts \
        dashboard/src/composables/__tests__/useSpcodeGitBranches.spec.ts
git commit -m "feat(dashboard): add switch/create/delete mutations to useSpcodeGitBranches"
```

---

## Task 5: i18n keys — all 3 locales

**Files:**
- Modify: `dashboard/src/i18n/locales/zh-CN/features/chat.json`
- Modify: `dashboard/src/i18n/locales/en-US/features/chat.json`
- Modify: `dashboard/src/i18n/locales/ru-RU/features/chat.json`

**Consumes:** None (data only).

### Step 5.1: Locate the worktreeMgmt block in zh-CN

Use the line numbers from the previous exploration:
- `worktreeMgmt` block: lines 469–524 in `chat.json` (zh-CN)

In each locale file, find the closing `}` of `worktreeMgmt.unlock` block and insert the new `branchMgmt` block immediately after.

### Step 5.2: Append the branchMgmt block to zh-CN

In `dashboard/src/i18n/locales/zh-CN/features/chat.json`, locate the `unlock` block ending in `"success": "已解锁 worktree {branch}"` and add the new block immediately after the `}` that closes `unlock`:

```jsonc
      },
      "branchMgmt": {
        "menuButton": "切换分支",
        "menuButtonAria": "切换 git 分支",
        "loading": "加载分支列表…",
        "error": "获取分支失败（{reason}）",
        "detached": "detached HEAD",
        "remote": "远程",
        "empty": "暂无分支",
        "switch": {
          "confirmTitle": "切换分支",
          "confirmMessage": "确认从 {from} 切换到 {to}？",
          "dirtyBlocked": "该 worktree 有 {count} 个未提交改动，请先 commit 或 stash 后再切换。",
          "submit": "切换",
          "cancel": "取消",
          "success": "已切换到分支 {name}",
          "error": {
            "worktree_dirty": "工作区有未提交改动，切换被阻止",
            "branch_not_found": "分支 {name} 不存在",
            "invalid_branch": "分支名格式错误",
            "git_error": "切换失败:{stderr}"
          }
        },
        "delete": {
          "confirmTitle": "删除分支",
          "confirmMessage": "确认删除分支 {name}？",
          "submit": "删除",
          "cancel": "取消",
          "success": "已删除分支 {name}",
          "error": {
            "branch_is_current": "不能删除当前分支",
            "branch_not_merged": "分支未合并，无法安全删除（需 force）",
            "branch_not_found": "分支 {name} 不存在",
            "git_error": "删除失败:{stderr}"
          }
        },
        "create": {
          "menuItem": "新建分支",
          "name": "分支名",
          "nameHint": "仅允许字母数字 / . / - / _",
          "nameRequired": "请输入分支名",
          "startPoint": "起始点",
          "startPointHint": "默认 HEAD",
          "submit": "创建",
          "success": "已创建分支 {name}",
          "error": {
            "branch_exists": "分支 {name} 已存在",
            "invalid_branch": "分支名格式错误",
            "git_error": "创建失败:{stderr}"
          }
        }
      },
```

### Step 5.3: Append the branchMgmt block to en-US

Same structure, English text:

```jsonc
      },
      "branchMgmt": {
        "menuButton": "Switch branch",
        "menuButtonAria": "Switch git branch",
        "loading": "Loading branches…",
        "error": "Failed to load branches ({reason})",
        "detached": "detached HEAD",
        "remote": "remote",
        "empty": "No branches yet",
        "switch": {
          "confirmTitle": "Switch branch",
          "confirmMessage": "Switch from {from} to {to}?",
          "dirtyBlocked": "This worktree has {count} uncommitted change(s). Please commit or stash before switching.",
          "submit": "Switch",
          "cancel": "Cancel",
          "success": "Switched to branch {name}",
          "error": {
            "worktree_dirty": "Working tree has uncommitted changes; switch blocked",
            "branch_not_found": "Branch {name} not found",
            "invalid_branch": "Invalid branch name",
            "git_error": "Switch failed: {stderr}"
          }
        },
        "delete": {
          "confirmTitle": "Delete branch",
          "confirmMessage": "Delete branch {name}?",
          "submit": "Delete",
          "cancel": "Cancel",
          "success": "Deleted branch {name}",
          "error": {
            "branch_is_current": "Cannot delete the current branch",
            "branch_not_merged": "Branch is not fully merged; force required",
            "branch_not_found": "Branch {name} not found",
            "git_error": "Delete failed: {stderr}"
          }
        },
        "create": {
          "menuItem": "New branch",
          "name": "Branch name",
          "nameHint": "Alphanumeric / . / - / _ only",
          "nameRequired": "Branch name required",
          "startPoint": "Start point",
          "startPointHint": "Defaults to HEAD",
          "submit": "Create",
          "success": "Created branch {name}",
          "error": {
            "branch_exists": "Branch {name} already exists",
            "invalid_branch": "Invalid branch name",
            "git_error": "Create failed: {stderr}"
          }
        }
      },
```

### Step 5.4: Append the branchMgmt block to ru-RU

Same structure, Russian text:

```jsonc
      },
      "branchMgmt": {
        "menuButton": "Переключить ветку",
        "menuButtonAria": "Переключить git ветку",
        "loading": "Загрузка списка веток…",
        "error": "Не удалось загрузить ветки ({reason})",
        "detached": "detached HEAD",
        "remote": "удалённая",
        "empty": "Веток пока нет",
        "switch": {
          "confirmTitle": "Переключить ветку",
          "confirmMessage": "Переключиться с {from} на {to}?",
          "dirtyBlocked": "В этом worktree есть {count} несохранённых изменений. Сначала сделайте commit или stash.",
          "submit": "Переключить",
          "cancel": "Отмена",
          "success": "Переключено на ветку {name}",
          "error": {
            "worktree_dirty": "В рабочей директории есть несохранённые изменения; переключение заблокировано",
            "branch_not_found": "Ветка {name} не найдена",
            "invalid_branch": "Недопустимое имя ветки",
            "git_error": "Ошибка переключения: {stderr}"
          }
        },
        "delete": {
          "confirmTitle": "Удалить ветку",
          "confirmMessage": "Удалить ветку {name}?",
          "submit": "Удалить",
          "cancel": "Отмена",
          "success": "Ветка {name} удалена",
          "error": {
            "branch_is_current": "Нельзя удалить текущую ветку",
            "branch_not_merged": "Ветка не слита; требуется force",
            "branch_not_found": "Ветка {name} не найдена",
            "git_error": "Ошибка удаления: {stderr}"
          }
        },
        "create": {
          "menuItem": "Новая ветка",
          "name": "Имя ветки",
          "nameHint": "Только буквы/цифры / . / - / _",
          "nameRequired": "Укажите имя ветки",
          "startPoint": "Точка старта",
          "startPointHint": "По умолчанию HEAD",
          "submit": "Создать",
          "success": "Ветка {name} создана",
          "error": {
            "branch_exists": "Ветка {name} уже существует",
            "invalid_branch": "Недопустимое имя ветки",
            "git_error": "Ошибка создания: {stderr}"
          }
        }
      },
```

### Step 5.5: Verify i18n completeness

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run src/i18n/i18n.completeness.spec.ts
```

Expected: Existing tests still pass. (The existing spec does not assert branchMgmt yet — that's a future improvement.)

### Step 5.6: Verify the JSON is valid

```bash
cd F:\github\Astrbot\dashboard
node -e "JSON.parse(require('fs').readFileSync('src/i18n/locales/zh-CN/features/chat.json','utf8')); JSON.parse(require('fs').readFileSync('src/i18n/locales/en-US/features/chat.json','utf8')); JSON.parse(require('fs').readFileSync('src/i18n/locales/ru-RU/features/chat.json','utf8')); console.log('OK')"
```

Expected: `OK`

### Step 5.7: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/i18n/locales/zh-CN/features/chat.json \
        dashboard/src/i18n/locales/en-US/features/chat.json \
        dashboard/src/i18n/locales/ru-RU/features/chat.json
git commit -m "feat(dashboard): add i18n keys for branch switcher (zh-CN/en-US/ru-RU)"
```

---

## Task 6: `BranchSwitchConfirmDialog.vue` — switch confirmation dialog

**Files:**
- Create: `dashboard/src/components/chat/message_list_comps/BranchSwitchConfirmDialog.vue`

**Consumes:** `useModuleI18n("features/chat")`.

### Step 6.1: Create the dialog component

Create `dashboard/src/components/chat/message_list_comps/BranchSwitchConfirmDialog.vue`:

```vue
<!--
  Author: elecvoid243 @ 2026-07-21
  Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md §3.4.1
  Confirmation dialog for POST /spcode/git-branch-switch. Emits
  'confirm' (with name) on success; 'cancel' on close.
-->
<script setup lang="ts">
import { ref, watch, computed } from "vue";
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/chat");

const props = defineProps<{
  modelValue: boolean;
  from: string | null;       // current branch name, null = detached
  to: string;                // target branch name
  dirtyCount: number;        // 0 = clean
  isSubmitting?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "confirm", name: string): void;
  (e: "cancel"): void;
}>();

// Reset submit-error whenever the dialog is re-opened.
const submitError = ref<string | null>(null);
watch(
  () => props.modelValue,
  (open, prev) => {
    if (open && !prev) submitError.value = null;
  },
);

const fromLabel = computed(() =>
  props.from ?? tm("spcodeProjectLoad.diffSidebar.branchMgmt.detached"),
);

const dirtyText = computed(() =>
  props.dirtyCount > 0
    ? tm("spcodeProjectLoad.diffSidebar.branchMgmt.switch.dirtyBlocked", {
        count: props.dirtyCount,
      })
    : "",
);

const canSubmit = computed(
  () => props.dirtyCount === 0 && !props.isSubmitting,
);

function onCancel(): void {
  if (props.isSubmitting) return;
  emit("update:modelValue", false);
  emit("cancel");
}
function onSubmit(): void {
  if (!canSubmit.value) return;
  emit("confirm", props.to);
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    persistent
    max-width="480"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.switch.confirmTitle") }}
      </v-card-title>
      <v-card-text class="pa-4 pl-6">
        <p class="text-body-1 mb-2">
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.switch.confirmMessage", {
            from: fromLabel,
            to: to,
          }) }}
        </p>
        <v-alert
          v-if="dirtyCount > 0"
          type="warning"
          density="compact"
          variant="tonal"
          class="mb-0"
        >
          {{ dirtyText }}
        </v-alert>
        <v-alert
          v-if="submitError"
          type="error"
          density="compact"
          variant="tonal"
          class="mt-3 mb-0"
        >
          {{ submitError }}
        </v-alert>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="isSubmitting"
          @click="onCancel"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.switch.cancel") }}
        </v-btn>
        <v-btn
          variant="tonal"
          color="primary"
          :loading="isSubmitting"
          :disabled="!canSubmit"
          @click="onSubmit"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.switch.submit") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
```

### Step 6.2: Verify the file type-checks

```bash
cd F:\github\Astrbot\dashboard
pnpm type-check
```

Expected: No new TypeScript errors.

### Step 6.3: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/message_list_comps/BranchSwitchConfirmDialog.vue
git commit -m "feat(dashboard): add BranchSwitchConfirmDialog with dirty pre-check"
```

---

## Task 7: `BranchDeleteConfirmDialog.vue` — delete confirmation dialog

**Files:**
- Create: `dashboard/src/components/chat/message_list_comps/BranchDeleteConfirmDialog.vue`

### Step 7.1: Create the dialog component

Create `dashboard/src/components/chat/message_list_comps/BranchDeleteConfirmDialog.vue`:

```vue
<!--
  Author: elecvoid243 @ 2026-07-21
  Spec: docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md §3.4.2
  Confirmation dialog for POST /spcode/git-branch-delete. Emits
  'confirm' (with name) on success; 'cancel' on close.
-->
<script setup lang="ts">
import { useModuleI18n } from "@/i18n/composables";

const { tm } = useModuleI18n("features/chat");

const props = defineProps<{
  modelValue: boolean;
  name: string;
  isSubmitting?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", v: boolean): void;
  (e: "confirm", name: string): void;
  (e: "cancel"): void;
}>();

function onCancel(): void {
  if (props.isSubmitting) return;
  emit("update:modelValue", false);
  emit("cancel");
}
function onSubmit(): void {
  if (props.isSubmitting) return;
  emit("confirm", props.name);
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    persistent
    max-width="420"
  >
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.delete.confirmTitle") }}
      </v-card-title>
      <v-card-text class="pa-4 pl-6">
        <p class="text-body-1 mb-0">
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.delete.confirmMessage", {
            name: name,
          }) }}
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          variant="text"
          :disabled="isSubmitting"
          @click="onCancel"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.delete.cancel") }}
        </v-btn>
        <v-btn
          variant="tonal"
          color="error"
          :loading="isSubmitting"
          :disabled="isSubmitting"
          @click="onSubmit"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.delete.submit") }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
```

### Step 7.2: Verify type-checks

```bash
cd F:\github\Astrbot\dashboard
pnpm type-check
```

Expected: No new errors.

### Step 7.3: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/message_list_comps/BranchDeleteConfirmDialog.vue
git commit -m "feat(dashboard): add BranchDeleteConfirmDialog"
```

---

## Task 8: `GitDiffSidebar.vue` — imports + state + watcher

**Files:**
- Modify: `dashboard/src/components/chat/GitDiffSidebar.vue`

**Consumes:** Tasks 1–7. This is the integration task; broken into 3 sub-tasks (8 / 9 / 10) to keep each step reviewable.

### Step 8.1: Add imports

In `dashboard/src/components/chat/GitDiffSidebar.vue`, locate the existing `useSpcodeWorktrees` import block (line ~33-37) and add the branch imports **immediately after**:

```ts
import {
  useSpcodeGitBranches,
  type BranchDeleteParams,
  type BranchSwitchParams,
} from "@/composables/useSpcodeGitBranches";
import {
  classifyBranchReason,
} from "@/composables/parseSpcodeBranchManagement";
```

Locate the existing `WorktreeCreateDialog` import (line ~72) and add the two branch dialog imports **immediately after**:

```ts
import BranchSwitchConfirmDialog from "@/components/chat/message_list_comps/BranchSwitchConfirmDialog.vue";
import BranchDeleteConfirmDialog from "@/components/chat/message_list_comps/BranchDeleteConfirmDialog.vue";
```

### Step 8.2: Add composable instance + state refs

Find the line where `const worktreesComposable = useSpcodeWorktrees();` is declared (line ~380). Add **immediately after**:

```ts
const branchesComposable = useSpcodeGitBranches();
const branchList = computed(() => {
  const s = branchesComposable.state.value;
  return s.kind === "ok" ? s.snapshot.branches : [];
});
const currentBranchName = computed(() => {
  const s = branchesComposable.state.value;
  if (s.kind !== "ok") return null;
  return s.snapshot.current;
});
```

### Step 8.3: Add branch polling watcher

Find the worktree polling watcher block (around line 1320-1326) and **immediately after** it, add the branch polling watcher:

```ts
// Spec 2026-07-21 §3.5: branch polling rides the same lifecycle
// as worktree polling (30s cadence) — both start when the sidebar
// opens in a git repo, both stop when it closes or the repo is
// unloaded. Single source of truth: the same [modelValue, isGitRepo]
// gate that drives worktree polling drives branch polling.
watch(
  () => props.modelValue,
  (open) => {
    if (open && isGitRepo.value) {
      branchesComposable.startPolling(30_000);
    } else {
      branchesComposable.stopPolling();
    }
  },
  { immediate: true },
);

// Race-catch: same pattern as the worktree race-catch above.
// If the project turns out NOT to be a git repo after the probe
// resolves, tear down any branch timer that may have started.
watch(isGitRepo, (isRepo) => {
  if (isRepo) return;
  branchesComposable.stopPolling();
});
```

### Step 8.4: Add initial branch fetch on mount

Find `void worktreesComposable.refresh();` (around line 1273) and add **immediately after**:

```ts
void branchesComposable.refresh();
```

### Step 8.5: Add dialog state refs

Find the existing `removeDialogTarget` block (around line 853) and add **immediately after**:

```ts
const switchDialogOpen = ref(false);
const deleteDialogOpen = ref(false);
const switchTarget = ref<{ from: string | null; to: string } | null>(null);
const deleteTarget = ref<string | null>(null);
const switchDirtyCount = ref(0);
const isBranchSwitching = ref(false);
const isBranchDeleting = ref(false);
const isBranchCreating = ref(false);
const branchCreateExpanded = ref(false);
const branchCreateName = ref("");
const branchCreateStartPoint = ref("HEAD");
const branchCreateError = ref<string | null>(null);
const branchMenuOpen = ref(false);
```

### Step 8.6: Add error reason → i18n key map

Find the existing `worktree_invalid` reason map (around line 996) and add **immediately before** the closing `}` of the surrounding `metaFor` / similar object. If no such map exists, add this block somewhere stable in the script setup, e.g. right after the state refs in Step 8.5:

```ts
// Spec §3.7: branch error reason → i18n key. Per-endpoint tables.
const BRANCH_ERROR_KEYS = {
  switch: {
    worktree_dirty:   "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.worktree_dirty",
    branch_not_found: "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.branch_not_found",
    invalid_branch:   "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.invalid_branch",
    invalid_body:     "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
    git_error:        "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
    network:          "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
    unknown:          "spcodeProjectLoad.diffSidebar.branchMgmt.switch.error.git_error",
  },
  delete: {
    branch_is_current: "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_is_current",
    branch_not_merged: "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_not_merged",
    branch_not_found:  "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.branch_not_found",
    invalid_branch:    "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
    git_error:         "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
    network:           "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
    unknown:           "spcodeProjectLoad.diffSidebar.branchMgmt.delete.error.git_error",
  },
  create: {
    branch_exists:  "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.branch_exists",
    invalid_branch: "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.invalid_branch",
    git_error:      "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.git_error",
    network:        "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.git_error",
    unknown:        "spcodeProjectLoad.diffSidebar.branchMgmt.create.error.git_error",
  },
} as const;
function branchErrorKey(
  endpoint: "switch" | "delete" | "create",
  reason: string,
): string {
  return (BRANCH_ERROR_KEYS[endpoint] as Record<string, string>)[reason]
    ?? BRANCH_ERROR_KEYS[endpoint].git_error;
}
```

### Step 8.7: Add cascade refresh helper

Find the existing `onWorktreeChange` function (around line 1704) and add **immediately after**:

```ts
// Spec 2026-07-21 §3.6: cascade refresh after a successful branch
// switch. Always refreshes worktree (wt.branch field changes).
// Also refreshes the currently visible view so the user sees the
// new branch state without waiting for the next 10s polling tick.
async function refreshAfterBranchChange(): Promise<void> {
  const tasks: Promise<unknown>[] = [worktreesComposable.refresh()];
  switch (viewMode.value) {
    case "diff":
      tasks.push(composable.refresh(), gitStatus.refresh());
      break;
    case "files":
      tasks.push(gitStatus.refresh());
      break;
    case "history":
      tasks.push(gitLog.refresh());
      break;
    case "docs":
      tasks.push(gitStatus.refresh());
      break;
  }
  await Promise.allSettled(tasks);
}
```

### Step 8.8: Add branch event handlers

Add the handler block **immediately after** `refreshAfterBranchChange`:

```ts
// ── Branch management handlers (spec 2026-07-21 §3.3) ───────────

// Open the switch dialog after a dirty pre-check. We hit
// /spcode/git-status to count dirty entries; if the worktree is
// clean, we open the dialog with the confirm button enabled.
// The dirty count is rendered in the dialog as a warning.
async function onBranchMenuItemClick(b: { name: string; current: boolean }): Promise<void> {
  if (b.current) {
    // Current branch is a no-op — close the menu.
    branchMenuOpen.value = false;
    return;
  }
  const fromBranch = currentBranchName.value;
  switchTarget.value = { from: fromBranch, to: b.name };
  switchDirtyCount.value = 0;
  switchDialogOpen.value = true;
  // Async dirty pre-check; if it fails, the dialog still opens
  // with dirtyCount=0 (assume clean) and the backend will reject
  // a real dirty switch.
  const umo = spcodeStatus.status.value.umo;
  if (umo) {
    try {
      const resp = await pluginExtensionApi.get<{ data?: { dirty_count?: number } }>(
        "spcode/git-status",
        {
          params: { umo, worktree: selectedWorktree.value ?? undefined },
        },
      );
      switchDirtyCount.value = resp.data?.data?.dirty_count ?? 0;
    } catch {
      switchDirtyCount.value = 0;
    }
  }
}

async function onBranchSwitchConfirm(name: string): Promise<void> {
  isBranchSwitching.value = true;
  try {
    const params: BranchSwitchParams = { name };
    const result = await branchesComposable.switch(params);
    if (!result.ok) {
      tm(branchErrorKey("switch", result.reason), {
        name, stderr: result.stderr ?? "",
      });
      return;
    }
    switchDialogOpen.value = false;
    branchMenuOpen.value = false;
    await refreshAfterBranchChange();
    tm("spcodeProjectLoad.diffSidebar.branchMgmt.switch.success", { name });
  } finally {
    isBranchSwitching.value = false;
  }
}

function onBranchDeleteClick(b: { name: string; current: boolean }): void {
  if (b.current) return;  // UI never shows × for current, but defense-in-depth
  deleteTarget.value = b.name;
  deleteDialogOpen.value = true;
}

async function onBranchDeleteConfirm(name: string): Promise<void> {
  isBranchDeleting.value = true;
  try {
    const params: BranchDeleteParams = { name };
    const result = await branchesComposable.delete(params);
    if (!result.ok) {
      tm(branchErrorKey("delete", result.reason), {
        name, stderr: result.stderr ?? "",
      });
      return;
    }
    deleteDialogOpen.value = false;
    tm("spcodeProjectLoad.diffSidebar.branchMgmt.delete.success", { name });
  } finally {
    isBranchDeleting.value = false;
  }
}

async function onBranchCreateSubmit(): Promise<void> {
  const name = branchCreateName.value.trim();
  if (!name) {
    branchCreateError.value = tm(
      "spcodeProjectLoad.diffSidebar.branchMgmt.create.nameRequired",
    );
    return;
  }
  branchCreateError.value = null;
  isBranchCreating.value = true;
  try {
    const result = await branchesComposable.create({
      name,
      startPoint: branchCreateStartPoint.value.trim() || "HEAD",
    });
    if (!result.ok) {
      branchCreateError.value = tm(branchErrorKey("create", result.reason), {
        name, stderr: result.stderr ?? "",
      });
      return;
    }
    // Success: collapse the form, keep the menu open.
    branchCreateExpanded.value = false;
    branchCreateName.value = "";
    branchCreateStartPoint.value = "HEAD";
    tm("spcodeProjectLoad.diffSidebar.branchMgmt.create.success", { name });
  } finally {
    isBranchCreating.value = false;
  }
}
```

### Step 8.9: Add dispose in onBeforeUnmount

Find the `worktreesComposable.dispose();` line in `onBeforeUnmount` (around line 2829) and add **immediately after**:

```ts
branchesComposable.dispose();
```

### Step 8.10: Verify type-checks

```bash
cd F:\github\Astrbot\dashboard
pnpm type-check
```

Expected: No new TypeScript errors.

### Step 8.11: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/GitDiffSidebar.vue
git commit -m "feat(dashboard): wire useSpcodeGitBranches into GitDiffSidebar (state, watcher, handlers)"
```

---

## Task 9: `GitDiffSidebar.vue` — template (menu button + v-menu)

**Files:**
- Modify: `dashboard/src/components/chat/GitDiffSidebar.vue` (template section only)

### Step 9.1: Add the branch button + v-menu

Find the worktree tabs container `<div class="git-diff-sidebar-tabs" ...>` (around line 3162) and the worktree `+` button (around line 3240). The current structure ends with `<button class="git-diff-sidebar-tab-add">` followed by the context menu `<Teleport>` block.

**Immediately after the worktree `+` button** and **before the `<Teleport to="body">`**, add the branch switcher button + v-menu:

```vue
<!-- Branch switcher (spec 2026-07-21 §3.3) -->
<button
  type="button"
  class="git-diff-sidebar-tab-branch"
  :aria-label="tm('spcodeProjectLoad.diffSidebar.branchMgmt.menuButtonAria')"
  :title="tm('spcodeProjectLoad.diffSidebar.branchMgmt.menuButton')"
  @click.stop="branchMenuOpen = !branchMenuOpen"
>
  <v-icon size="14">mdi-source-branch</v-icon>
  <span class="git-diff-sidebar-tab-branch-name">
    {{ currentBranchName ?? tm("spcodeProjectLoad.diffSidebar.branchMgmt.detached") }}
  </span>
  <v-icon size="12">mdi-menu-down</v-icon>
</button>

<v-menu
  v-model="branchMenuOpen"
  :close-on-content-click="false"
  location="bottom end"
  max-width="320"
>
  <v-list density="compact" class="git-diff-sidebar-branch-menu">
    <!-- Loading -->
    <template v-if="branchesComposable.state.value.kind === 'loading'">
      <v-list-item>
        <template #prepend>
          <v-progress-circular indeterminate :size="14" :width="2" />
        </template>
        <v-list-item-title>
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.loading") }}
        </v-list-item-title>
      </v-list-item>
    </template>

    <!-- Error -->
    <template v-else-if="branchesComposable.state.value.kind === 'error'">
      <v-list-item disabled>
        <v-list-item-title class="text-caption text-error">
          {{
            tm("spcodeProjectLoad.diffSidebar.branchMgmt.error", {
              reason: branchesComposable.state.value.reason,
            })
          }}
        </v-list-item-title>
      </v-list-item>
    </template>

    <!-- Branch list -->
    <template v-else>
      <v-list-item
        v-if="branchList.length === 0"
        disabled
      >
        <v-list-item-title class="text-caption">
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.empty") }}
        </v-list-item-title>
      </v-list-item>
      <v-list-item
        v-for="b in branchList"
        :key="b.name"
        :active="b.current"
        :disabled="isBranchSwitching"
        @click="onBranchMenuItemClick(b)"
      >
        <template #prepend>
          <v-icon
            v-if="b.current"
            size="14"
            color="primary"
          >mdi-check</v-icon>
          <v-icon
            v-else-if="b.remote"
            size="14"
            color="grey"
          >mdi-cloud-outline</v-icon>
          <v-icon
            v-else
            size="14"
          >mdi-source-branch</v-icon>
        </template>
        <v-list-item-title>
          {{ b.name }}
        </v-list-item-title>
        <template #append>
          <v-icon
            v-if="!b.current"
            size="14"
            class="git-diff-sidebar-branch-delete"
            @click.stop="onBranchDeleteClick(b)"
          >mdi-close</v-icon>
        </template>
      </v-list-item>
    </template>

    <!-- Inline create form -->
    <v-divider />
    <div v-if="branchCreateExpanded" class="git-diff-sidebar-branch-create">
      <v-text-field
        v-model="branchCreateName"
        :label="tm('spcodeProjectLoad.diffSidebar.branchMgmt.create.name')"
        :error-messages="branchCreateError ? [branchCreateError] : []"
        density="compact"
        variant="outlined"
        autofocus
        autocomplete="off"
        name="branch-create-name"
        @keyup.enter="onBranchCreateSubmit"
      />
      <v-text-field
        v-model="branchCreateStartPoint"
        :label="tm('spcodeProjectLoad.diffSidebar.branchMgmt.create.startPoint')"
        :placeholder="'HEAD'"
        density="compact"
        variant="outlined"
        class="mt-1"
        autocomplete="off"
        name="branch-create-start"
        @keyup.enter="onBranchCreateSubmit"
      />
      <div class="git-diff-sidebar-branch-create-actions">
        <v-btn
          size="x-small"
          variant="text"
          :disabled="isBranchCreating"
          @click="branchCreateExpanded = false; branchCreateError = null"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.create.cancel") }}
        </v-btn>
        <v-btn
          size="x-small"
          variant="flat"
          color="primary"
          :loading="isBranchCreating"
          :disabled="!branchCreateName.trim()"
          @click="onBranchCreateSubmit"
        >
          {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.create.submit") }}
        </v-btn>
      </div>
    </div>
    <v-list-item
      v-else
      :disabled="isBranchCreating"
      @click="branchCreateExpanded = true"
    >
      <template #prepend>
        <v-icon size="14">mdi-plus</v-icon>
      </template>
      <v-list-item-title>
        {{ tm("spcodeProjectLoad.diffSidebar.branchMgmt.create.menuItem") }}
      </v-list-item-title>
    </v-list-item>
  </v-list>
</v-menu>
```

### Step 9.2: Add the dialog components (template)

Find the existing `<WorktreeCreateDialog>` reference in the template (search for `WorktreeCreateDialog`) and add the two branch dialogs **immediately after** it (and before the closing `</v-card>` if applicable):

```vue
<BranchSwitchConfirmDialog
  v-if="switchTarget"
  v-model="switchDialogOpen"
  :from="switchTarget.from"
  :to="switchTarget.to"
  :dirty-count="switchDirtyCount"
  :is-submitting="isBranchSwitching"
  @confirm="onBranchSwitchConfirm"
  @cancel="switchDialogOpen = false"
/>

<BranchDeleteConfirmDialog
  v-if="deleteTarget"
  v-model="deleteDialogOpen"
  :name="deleteTarget"
  :is-submitting="isBranchDeleting"
  @confirm="onBranchDeleteConfirm"
  @cancel="deleteDialogOpen = false"
/>
```

### Step 9.3: Add scoped styles

Find the existing scoped `<style scoped>` block in `GitDiffSidebar.vue` (or any existing branch-related styles). If none, add a new `<style scoped>` block at the bottom of the file. Append these styles:

```vue
<style scoped>
.git-diff-sidebar-tab-branch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  margin-left: 4px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  color: inherit;
}
.git-diff-sidebar-tab-branch:hover {
  background: rgba(0, 0, 0, 0.04);
}
.git-diff-sidebar-tab-branch-name {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-diff-sidebar-branch-menu {
  min-width: 280px;
}
.git-diff-sidebar-branch-delete {
  cursor: pointer;
  opacity: 0.6;
}
.git-diff-sidebar-branch-delete:hover {
  opacity: 1;
  color: rgb(var(--v-theme-error));
}
.git-diff-sidebar-branch-create {
  padding: 8px 12px;
}
.git-diff-sidebar-branch-create-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-top: 6px;
}
</style>
```

### Step 9.4: Verify type-checks + build

```bash
cd F:\github\Astrbot\dashboard
pnpm type-check
pnpm lint
```

Expected: No new errors.

### Step 9.5: Commit

```bash
cd F:\github\Astrbot
git add dashboard/src/components/chat/GitDiffSidebar.vue
git commit -m "feat(dashboard): add branch switcher menu + dialogs to GitDiffSidebar template"
```

---

## Task 10: `GitDiffSidebar.vue` — final integration check + smoke test

### Step 10.1: Run all unit tests

```bash
cd F:\github\Astrbot\dashboard
pnpm test:run
```

Expected: All existing tests pass + 11 new branch tests pass.

### Step 10.2: Run lint + format

```bash
cd F:\github\Astrbot\dashboard
pnpm lint
```

If format issues:
```bash
cd F:\github\Astrbot\dashboard
pnpm format
```

### Step 10.3: Run dashboard dev server

```bash
cd F:\github\Astrbot\dashboard
pnpm dev
```

Open the dashboard in a browser. In a project that has git, open the GitDiffSidebar.

### Step 10.4: Manual smoke checklist

Tick each item as you verify it. (Do NOT commit until all are checked.)

- [ ] Branch button visible next to "工作树" label, shows current branch name
- [ ] Clicking branch button opens dropdown with all branches
- [ ] Current branch has `mdi-check` icon, no × delete icon
- [ ] Non-current branches have × delete icon on the right
- [ ] Remote branches show `mdi-cloud-outline` icon
- [ ] Clicking a non-current branch opens the switch confirmation dialog
- [ ] Dialog shows source branch → target branch message
- [ ] If worktree has uncommitted changes, dialog shows dirty warning + confirm button disabled
- [ ] Confirming switch: branch changes in the UI, worktree tab's `wt.branch` label updates, no errors
- [ ] After switch, current view (diff/files/history) refreshes
- [ ] Clicking × on a branch opens the delete confirmation dialog
- [ ] Confirming delete: branch removed from list
- [ ] × on current branch is NOT visible (defense-in-depth)
- [ ] Clicking `+ 新建分支` expands inline form with name + start_point fields
- [ ] Submitting inline form creates the branch and collapses the form
- [ ] Trying to create a branch with an existing name shows `branch_exists` error
- [ ] Polling: wait 30 seconds with sidebar open, observe that the branch list refreshes (DevTools Network tab)
- [ ] Closing the sidebar stops polling (no requests in 30s window after close)

### Step 10.5: Final commit

```bash
cd F:\github\Astrbot
git status
# If any uncommitted lint fixes:
git add dashboard/src/components/chat/GitDiffSidebar.vue
git commit -m "chore(dashboard): address lint/format feedback from smoke test"
```

---

## Self-Review (vs spec)

After writing the plan, re-checked against `docs/superpowers/specs/2026-07-21-git-branch-switcher-frontend-design.md`:

| Spec section | Covered in plan |
|--------------|-----------------|
| §1.1–1.3 Context/Goals/Non-Goals | Architecture + File Structure (no code changes for non-goals) |
| §2.1–2.3 Architecture | File Structure table + each task's "Files" block |
| §3.1 Data Model | Task 1 (parser types), Task 2 (mutation snapshot type) |
| §3.2 Composable API | Task 3 (shell) + Task 4 (mutations) |
| §3.2.1 Polling cadence | Task 3 Step 3.3 (`DEFAULT_POLL_MS` import comment) |
| §3.2.2 ETag | Task 3 Step 3.3 (etagMap + validateStatus 304) |
| §3.2.3 Single-flight | Task 3 (read controller) + Task 4 (mutationAbort per kind) |
| §3.2.4 No worktree filter | Task 3 Step 3.3 (no `worktreeRef` param) |
| §3.3 UI Dropdown | Task 9 Step 9.1 (button + v-menu + create form) |
| §3.4.1 Switch dialog | Task 6 (component) + Task 8 Step 8.8 (handler) |
| §3.4.2 Delete dialog | Task 7 (component) + Task 8 Step 8.8 (handler) |
| §3.5 Polling & Lifecycle | Task 8 Steps 8.3 + 8.4 + 8.9 |
| §3.6 Cascade Refresh | Task 8 Step 8.7 (refreshAfterBranchChange) |
| §3.7 Error Handling | Task 8 Step 8.6 (BRANCH_ERROR_KEYS) |
| §3.8 Edge Cases | Task 4 tests (network/abort) + Task 10 smoke checklist |
| §4 i18n | Task 5 (all 3 locales, all 60 keys) |
| §5.1 Parser tests | Task 1 Step 1.1 + Task 2 Step 2.1 |
| §5.2 Mutation parser tests | Task 2 Step 2.1 |
| §5.3 Composable tests | Task 3 Step 3.1 + Task 4 Step 4.1 |
| §5.5 i18n completeness | Task 5 Step 5.5 |
| §5.6 Lint/format | Task 10 Step 10.2 |

**Type consistency check**: All tasks use:
- `SpcodeGitBranchesSnapshot` (defined Task 1) — consumed by Task 3, 4, 8
- `parseSpcodeGitBranches` (Task 1) — called from Task 2 (via raw build) and Task 3, 4
- `parseSpcodeBranchSwitch/Create/Delete` (Task 2) — called from Task 4
- `useSpcodeGitBranches` (Tasks 3+4) — consumed by Task 8
- `BranchSwitchParams/CreateParams/DeleteParams` (Task 3) — consumed by Task 8
- `BRANCH_ERROR_KEYS` (Task 8) — matches the `reason` strings emitted by Task 2 parsers

No type or method-name drift between tasks.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-21-git-branch-switcher-frontend-impl.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for plans with 10+ tasks where each is reviewable independently.

2. **Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints for review. Best for shorter plans or when you want tighter control.

Which approach?
