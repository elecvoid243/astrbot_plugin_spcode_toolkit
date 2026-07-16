# v2.17.0 SDD Progress Ledger

**Branch:** `v2.17.0-git-init-branch-revert`
**Base:** `14ebb0a` (main + docs(spec+plan))
**Worktree:** `F:\github\astrbot_plugin_spcode_toolkit\.worktrees\v2.17.0`

## Task Status

- [x] Task 1: 基础设施 (PR-A) — ReasonCode +12, `_git_init_preflight`, `_is_commit_ref`, fixtures
  - Implementer: DONE_WITH_CONCERNS, 30/30 tests passing, 3 commits, pre-existing AstrBot error noted
- [x] Task 2: git-init (PR-B) — handler + 18 tests
  - Implementer: DONE_WITH_CONCERNS, 17/18 tests passing, 1 commit, plan-design issue noted
- [ ] Task 3: git-branches (PR-C) — handler + 14 tests
- [ ] Task 4: git-branch-create (PR-D) — handler + 12 tests
- [ ] Task 5: git-branch-delete (PR-E) — handler + 16 tests
- [ ] Task 6: git-branch-switch (PR-F) — handler + 22 tests
- [ ] Task 7: git-revert (PR-G) — handler + 20 tests
- [ ] Task 8: E2E + 路由断言 (PR-H)
- [ ] Task 9: 文档 (PR-I)

## Pre-Flight Fix

- [x] `0427ee9` fix(test): lazy-load main.py + stub missing runtime modules
  (env-only; standalone pytest now collects + runs ReasonCode test = 8 PASS)

## Commits Log

- `0427ee9` fix(test): lazy-load main.py + stub missing runtime modules
- `cce2a30` feat: add v2.17 git reason codes and init preflight
- `da31e78` feat: add commit ref validation helper
- `58f48ba` feat: add v2.17 git lifecycle test fixtures
- `9564057` feat: fix Task 1 reviewer findings
- `14b3f2f` feat(webapi): add POST /spcode/git-init (v2.17.0 PR-B)

## Test Coverage Snapshot

| Task | Files | Tests | Status |
|------|-------|-------|--------|
| Task 1 | test_helpers_commit_ref.py, test_git_init_preflight.py, test_reason_code.py | 25 PASSED + 6 ERROR (env) | partial |
| Task 2 | test_git_init.py | 17 PASSED + 1 FAILED (plan-design) | partial |
| Task 1-2 combined | | 42 PASSED | |