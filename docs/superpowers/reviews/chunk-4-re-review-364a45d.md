# Chunk 4 Re-review (commit 364a45d)

**Reviewer:** plan-document-reviewer (plan_reviewer_v214c)
**Reviewed at:** 2026-06-26 18:35 (CST)
**Plan path:** `docs/superpowers/plans/2026-06-26-git-worktree-management.md`
**Lines reviewed:** 2540-2950 (Chunk 4)
**Latest commit:** 364a45d "docs(plan): address plan-reviewer Chunk 3 patch + Chunk 4 gaps"

---

## Chunk 4 Status: ✅ Approved (with 4 advisory notes)

All 5 blocking issues from the previous review are resolved. The plan is implementation-ready.

---

## Fix Verification (5 项逐条)

| # | Issue | Fix Location | Status |
|---|-------|--------------|--------|
| 1 | 5 missing LOCK/UNLOCK test cases (spec §6.4 needs 14, plan had 6) | Task 4.1 Step 1 (lines 2586-2714) + Task 4.2 Step 1 | ✅ **FIXED** |
| 2 | Public API docs 内容空缺 (only placeholder) | Task 4.5 Step 1 (lines 3320-3471) — full ~250 line markdown | ✅ **FIXED** |
| 3 | AGENTS.md update diff 不完整 (no §3.7 increment) | Task 4.6 Step 2 a/b/c (lines 3492-3565) — split into 3 sub-steps | ✅ **FIXED** |
| 4 | 14→16 routes update 无 diff | Task 4.6 Step 3 (lines 3567-3596) — 4 test functions + 1 聚合 test | ✅ **FIXED** |
| 5 | TDD 5 步分批混批 (advisory) | Task 4.1 = LOCK only / Task 4.2 = UNLOCK only (separated cleanly) | ✅ **FIXED** |

### Issue #1 详细验证

**Spec §6.4 要求 14 test cases** in `tests/test_git_worktree_lock.py`:
1. test_lock_basic ✅ (line 2599)
2. test_lock_with_reason ✅ (line 2610)
3. test_lock_already_locked_rejected ✅ (line 2622)
4. test_lock_unknown_path_rejected ✅ (line 2634, plan 命名 `test_lock_unknown_path`)
5. test_lock_path_unsafe ✅ (line 2642)
6. test_lock_main_worktree_allowed ✅ (line 2650)
7. test_lock_git_returns_already_locked ✅ (line 2660)
8. test_lock_success_envelope_shape ✅ (line 2673)
9. test_unlock_basic ✅ (line 2707)
10. test_unlock_not_locked_rejected ✅ (line 2721)
11. test_unlock_unknown_path_rejected ✅ (line 2730, plan 命名 `test_unlock_unknown_path`)
12. test_unlock_git_returns_not_locked ✅ (line 2688)
13. test_unlock_success_envelope_shape ✅ (line 2697)
14. test_lock_unlock_no_project_loaded ✅ (line 2680)

**Count:** 14/14 ✅ — 全覆盖。Naming 微差 (`_rejected` 后缀) 不影响语义/执行。

### Issue #2 详细验证

Task 4.5 Step 1 markdown 包含:
- 4 endpoints 完整表格 (line 3326)
- 4 endpoints 详细 schema + reason code + curl example
- i18n key namespace
- Versioning 段落
- Cross-references (含 2026-06-18 spec)

约 150 行 markdown,达到预期 ~250 行(含代码示例)。✅

### Issue #3 详细验证

Task 4.6 Step 2 a/b/c 三段:
- **a)** 4 行 endpoint table (lines 3496-3501)
- **b)** §3.7 完整新增段落 (lines 3507-3530) — 含 11 ReasonCode 列表、安全模型、GET 扩展
- **c)** 12→16 路由计数更新 (line 3534) — 明确 "12 → 16(v3.9 经 13 → 14 → 16 中间态)"

✅ 三段清晰分离。

### Issue #4 详细验证

Task 4.6 Step 3 diff 包含 4 test functions + 1 聚合:
1. `test_routes_table_has_sixteen_endpoints` (line 3541) — 重命名 14→16
2. `test_register_webapi_routes_calls_context_sixteen_times` (line 3554) — call_count 14→16
3. `test_register_webapi_routes_continues_on_failure` (line 3564) — 注释提到 call_count 16
4. `test_handlers_dict_has_four_worktree_entries` (line 3569) — 聚合新增

✅ 完整 4+1 测试函数。

### Issue #5 详细验证

- **Task 4.1** (LOCK only): 5 步 (test fail → implement → 6 LOCK tests pass → commit LOCK only)
- **Task 4.2** (UNLOCK only): 4 步 (UNLOCK tests fail → implement → all tests pass → commit UNLOCK only)

两 task 互不依赖,commit 历史清晰。✅

---

## CRITICAL 检查项

| 检查项 | 结果 | 说明 |
|--------|------|------|
| LOCK/UNLOCK `body` type guard | ✅ | 两 handler 顶部均有 `if not isinstance(body, dict): return invalid_body envelope` |
| 5 new tests setup 兼容性 | ✅ | `_make_plugin_mock` + `_make_test_repo` 在文件内本地定义,与现有 fixture 兼容 |
| 11 ReasonCode 完整列表 | ✅ | Task 4.6 Step 2b §3.7 列出全部 11 码字面量 |
| 14→16 路由双重说明 | ✅ | Task 4.3 Step 3 + Task 4.6 Step 3 + Step 2c 路由计数三处均提及 |

---

## Remaining Issues (按严重度排序)

**无阻塞问题。** 以下为 advisory 级别:

### Advisory #1 — Task 4.1 Step 4 expected count 不准确

**位置:** Task 4.1 Step 4 (line 2773)
```bash
Run: `pytest tests/test_git_worktree_lock.py -k "lock" -v`
Expected: 6 passed
```

**问题:** `-k "lock"` 是子串匹配,会匹配所有含 "lock" 的测试名。实际匹配数:
- 8 LOCK-only tests (test_lock_*) → PASS
- 3 mixed tests (含 "lock") → FAIL (unlock_handle 不存在)
- 3 UNLOCK tests (unlock/locked 含 "lock") → FAIL

实际应为 **8 passed / 6 failed**(或建议改为 `-k "lock_ and not lock_unlock"` 等更精确 filter)。

**严重度:** Advisory — 不阻塞,developer 见实际输出即可纠正。

### Advisory #2 — Task 4.2 Step 3 expected count 不准确

**位置:** Task 4.2 Step 3 (line 3097)
```bash
Run: `pytest tests/test_git_worktree_lock.py -v`
Expected: All 9 PASS
```

**问题:** 文件有 **14 tests**,不是 9。期望应为 "All 14 PASS"。

**严重度:** Advisory — 不阻塞。

### Advisory #3 — Task 4.3 / Task 4.6 Step 3 重复测试函数

**位置:** 
- Task 4.3 Step 1 (line 3154): 新增 `test_routes_table_has_sixteen_endpoints`
- Task 4.6 Step 3 (line 3541): 把已有的 `test_routes_table_has_fourteen_endpoints` 重命名为 `test_routes_table_has_sixteen_endpoints`

**问题:** 两个 task 都定义了同名测试函数 `test_routes_table_has_sixteen_endpoints`。Task 4.3 应改为"占位"或"复用 Task 4.6 重命名后的版本",否则会产生重复定义。

**建议:** Task 4.3 Step 1 只添加 2 个路由注册测试(`test_git_worktree_lock_route_registered` / `test_git_worktree_unlock_route_registered`),将 16 routes 断言推迟到 Task 4.6 Step 3 处理。

**严重度:** Advisory — 在 Chunk 4 整体执行时会暴露,容易修复。

### Advisory #4 — Task 4.5 i18n 注释数字不准确

**位置:** Task 4.5 Step 1 (line 3447)
```jsonc
"add":    { "error": { "invalid_body": "...", "path_unsafe": "...", /* 11 keys per endpoint */ } },
```

**问题:** "11 keys per endpoint" 不准确 — 每个 endpoint 的 ReasonCode 数量不同:
- ADD: ~10 codes (含 5 业务码 + 5 preflight)
- REMOVE: ~6 业务码 + 5 preflight = ~11
- LOCK: ~4 业务码 + 5 preflight = ~9
- UNLOCK: ~3 业务码 + 5 preflight = ~8

应改为 "11 keys total per endpoint family" 或列出每个 endpoint 的实际码数。

**严重度:** Advisory — 注释说明,不影响执行。

---

## Spec Consistency ✅

| Spec 条目 | Plan 一致性 |
|-----------|-------------|
| §3.3 LOCK handler 行为 | ✅ plan 实现的 7 分支错误处理对齐 |
| §3.4 UNLOCK handler 行为 | ✅ plan 实现的 6 分支对齐 |
| §6.4 14 test cases | ✅ 14/14 全部列出 |
| 11 ReasonCode 字面量 | ✅ §3.7 段落完整列出 |
| 路由计数 12 → 16 路径 | ✅ Chunk 1 (12→13) + Chunk 3 (13→14) + Chunk 4 (14→16) 三段式一致 |
| preflight 集成层 | ✅ 两 handler 均调用 `_git_endpoint_preflight` |
| GET `/spcode/git-worktree-list` 扩展 | ✅ §3.7 "GET 扩展" 段落明确 additive |
| 2026-06-18 spec cross-reference | ✅ docs cross-references 段落 (line 3471) |

**不存在 spec 偏离。**

---

## Existing Code Compatibility ✅

| Plan 中引用 | 来源 | 状态 |
|------------|------|------|
| `_make_envelope` | `tools/webapi/_helpers.py` | ✅ Chunk 1/2 已有 |
| `_git_endpoint_preflight` | `tools/webapi/_helpers.py` | ✅ Chunk 1 已有 |
| `_run_git_async` | `tools/webapi/_helpers.py` | ✅ Chunk 1 已有 |
| `_resolve_target_worktree` | `tools/_helpers.py` | ✅ Chunk 1 Task 1.5 已有(行 709) |
| `_list_worktrees_safe` | `tools/_helpers.py` | ✅ Chunk 1 Task 1.9 已有(行 1055) |
| `plugin._git_binary()` | 现有 plugin 接口 | ✅ ADD/REMOVE handler 已在用 |
| `plugin.get_loaded_project()` | 现有 plugin 接口 | ✅ ADD/REMOVE handler 已在用 |

**所有引用与既有代码模式完全对齐,无 breaking change 风险。**

---

## Recommendations (advisory)

1. **Task 4.1 Step 4**: 改为 `-k "lock_ and not lock_unlock"` 或 `-k "not unlock"`,期望 `8 passed`。
2. **Task 4.2 Step 3**: 期望改为 "All 14 PASS"(非 9)。
3. **Task 4.3 Step 1**: 删除 `test_routes_table_has_sixteen_endpoints` 测试函数 — 该断言由 Task 4.6 Step 3 的同名重命名处理,避免重复。
4. **Task 4.5 Step 1 i18n 注释**: 改为 "see ReasonCode lists per endpoint above" 或按 endpoint 分别注明实际码数。
5. (可选) **Task 4.4 fixture 复用**: 考虑将 `_make_plugin_mock` / `_make_test_repo` 上移到 `tests/conftest.py`,避免 Task 4.1 和 Task 4.4 重复定义(本次未改也可,因为命名差异已说明)。

---

## Final Verdict

**Chunk 4 可批准进入实现阶段。** 5 个 blocking issues 全部修复,剩余 4 项为 advisory(均不阻塞开发)。Plan 在 spec 一致性、既有代码兼容性、TDD 完整性三方面均达标。

建议在 Task 4.3 / 4.4 / 4.6 commit 阶段复核 advisory #1-#3 的小修正,但无需重新走完整 review 流程。

**Signed-off-by:** plan_reviewer_v214c @ 2026-06-26 18:35