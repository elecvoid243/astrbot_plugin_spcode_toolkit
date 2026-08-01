# Git 历史页跨分支浏览 + 操作按钮可见性规则 — 前端设计

> **Spec ID**: 2026-08-01-git-history-branch-picker
> **Author**: elecvoid243
> **Created**: 2026-08-01 23:50 CST
> **Status**: Approved
> **前置依赖**: v2.22.0 merge/cherry-pick/conflict 端点体系；dashboard `all` 分支
>   已合并的 merge/cherry-pick/conflict 前端（`f3a49cc6d..46104e10f`）
> **改动范围**: 仅 dashboard 前端（GitLogView + GitDiffSidebar），**零后端改动**

---

## §0 问题陈述与需求确认

### 问题

GitDiffSidebar「Git 历史」页当前只能便捷查看**当前分支**（HEAD）的历史。
cherry-pick 行内按钮（2026-08-01 合并的 cherry-pick 功能）在该视图下无条件
渲染——但 cherry-pick 的语义是「把**其他**分支的 commit 拿到当前分支」，
对当前分支历史里的 commit 执行 cherry-pick 等于「把自己的历史拣给自己」，
无意义且容易产生 empty/dirty 错误。

### 需求（brainstorming 确认记录）

| # | 问题 | 决策 |
|---|------|------|
| Q1 | 分支选择器形态 | **A：filter bar 的 ref 文本框升级为 v-combobox**（下拉列出分支，保留自由输入，与 Apply/Reset 流程一致） |
| Q2 | cherry-pick 可见性边界 | **宽松规则**：激活 ref 非空/HEAD 且 ≠ 当前分支名即显示（含手输 sha/tag） |
| Q3 | revert 可见性 | **互补规则**：revert 仅在查看当前分支（空/HEAD/当前分支名）时显示；cherry-pick 仅在查看非当前分支时显示 |

### 技术可行性（已核实）

- 后端 `GET /spcode/git-log` 已接受任意 `ref` 参数（`git log <ref>`，
  `tools/webapi/git_log.py:427,529-530`），响应回显 `ref`；**无需后端改动**
- 前端 `useSpcodeGitLog.filter` 已支持 `ref` 字段；ETag 按完整 filter 元组
  （含 ref）分 key 缓存，切换分支互不污染（既有机制）
- GitLogView 的 filter bar 已有 ref 文本输入框（仅手输，无分支提示）
- `GitLogView` 仅被 `GitDiffSidebar.vue` 挂载（`GitDiffSidebar.vue:4508`），
  改动封闭；`DocumentHistoryPanel` 等仅使用 composable，不挂载该组件

---

## §1 架构与数据流

```
branchesComposable（GitDiffSidebar 已有，30s 轮询）
    │ branchList / currentBranchName（既有 computed，直接复用）
    ▼ 三个新 prop
GitLogView
    ├─ ref 过滤框：v-text-field → v-combobox（分支列表 + 自由输入）
    └─ 行内按钮可见性：activeRef prop（父组件传入 gitLog.filter.value.ref）
       对比 currentBranch prop
```

关键决策：**可见性判定基于「激活」filter（点「应用」后的值），而非输入中的
草稿**。注意 GitLogView **并不持有 gitLog handle**（props 只有
`state / hasMore / isLoading / gitShow / gitStats` 等，激活 filter 存于
父组件的 `gitLog.filter.value`），因此需要父组件把激活 ref 作为新 prop
`activeRef` 传入。

## §2 GitLogView 变更

### 2a. ref 框升级为 v-combobox

- 新 prop：`branchItems: string[]`（由 GitDiffSidebar 派生，见 §3）
- `v-text-field` → `v-combobox`，`:items="branchItems"`，其余属性
  （label/placeholder i18n key、density、variant、hide-details、class）
  保持不变
- 保留自由输入：sha/tag/任意 revision 仍可手输
- 交互流程不变：改值后仍需点「应用」生效；「重置」回到
  `{ ref: "HEAD", n: 20 }`（`onReset` 现有逻辑不变）
- **降级**：`branchItems` 为空（非 git 仓库 / branches composable 未加载）
  时 combobox 无下拉项，退化为普通文本输入，无破坏

### 2b. 行内按钮可见性（规则已确认）

新 props：`currentBranch: string | null`、`activeRef: string | null`

```ts
const viewingCurrent = computed(() => {
  const r = props.activeRef;
  return !r || r === "HEAD" || r === props.currentBranch;
});
```

| 按钮 | 规则 | 模板 |
|------|------|------|
| revert | 仅查看当前分支时显示 | `v-if="viewingCurrent"` |
| cherry-pick | 仅查看非当前分支时显示 | `v-if="!viewingCurrent"` |

- 空 / `HEAD` / `=== currentBranch` → 视为「查看当前分支」
- 手输 sha/tag → `viewingCurrent = false`（宽松规则，Q2）
- `localFilter` 初始化与现有逻辑不变（`{ ref: "HEAD", n: 20 }`）

## §3 GitDiffSidebar 变更

零新状态：`currentBranchName` computed **已存在**（`GitDiffSidebar.vue:441`，
返回 `s.snapshot.current`），`branchList` computed 也已存在（`:437`）。
只需新增一个排序 computed 并传 prop：

```ts
const branchPickerItems = computed<string[]>(() => {
  const cur = branchList.value.filter((b) => b.current && !b.remote);
  const local = branchList.value.filter((b) => !b.current && !b.remote);
  const remote = branchList.value.filter((b) => b.remote);
  return [...cur, ...local, ...remote].map((b) => b.name);
});
```

模板 `<GitLogView>` 挂载处追加：
`:current-branch="currentBranchName"`、
`:branch-items="branchPickerItems"`、
`:active-ref="gitLog.filter.value.ref"`。

> 分支元素类型为 `SpcodeGitBranch`（`parseSpcodeGitBranches.ts:35`：
> `name / sha / upstream / upstreamTrack / current / remote`）；
> snapshot 顶层另有 `current: string | null`。

## §4 边界与错误处理

| 场景 | 行为 |
|------|------|
| 分离 HEAD（currentBranch 为 null） | 查看 HEAD → 显示 revert；选任意分支/sha → 显示 cherry-pick |
| 手输不存在的 ref | 后端 `git_error` → 现有 error banner 路径（无新增处理） |
| 切 worktree / 切分支 | gitLog ETag 按 ref 分 key（既有机制），互不污染 |
| 10s 轮询 | 沿用激活 ref 刷新，查看其他分支时同样生效（既有机制） |
| 工具栏 blank cherry-pick 按钮 | **不受影响**，始终显示（对话框内可输任意 ref + mainline） |
| merge 冲突面板 | 不受影响（独立 composable / 模板位置） |

## §5 测试

新增 `dashboard/src/components/chat/message_list_comps/__tests__/GitLogView.branchPicker.spec.ts`
（若已有 GitLogView spec 则并入）：

1. combobox 渲染传入的 `branchItems`
2. `viewingCurrent` 三态 × 按钮显隐：
   - active ref = `HEAD` → revert 显示，cherry-pick 隐藏
   - active ref = 当前分支名 → 同上
   - active ref = 其他分支名 → cherry-pick 显示，revert 隐藏
   - active ref = 手输 sha → cherry-pick 显示，revert 隐藏
3. 分支列表为空时 combobox 可自由输入（降级行为）
4. 验证：`pnpm test` + `pnpm typecheck`

## §6 非目标（YAGNI）

- 分支对比视图（`git log A..B`）
- log 图（graph）可视化
- 在 history 页内直接切换当前分支（branch switcher 已有此能力）
- 后端任何改动
