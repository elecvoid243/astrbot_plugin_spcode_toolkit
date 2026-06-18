# GitDiffSidebar — Worktree Switcher 设计

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后，下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已通过（用户确认日期 2026-06-18，Q1=A, Q2=A, Q3=A, Q4=B, Q5=A）
**作者**: elecvoid243 @ 2026-06-18 18:38
**目标插件 / 仓库**:
- `astrbot_plugin_spcode_toolkit`（后端）
- `Astrbot/dashboard`（前端 chatui）

**前置版本**:
- spcode plugin: 当前 HEAD（v2.x，具体版本号 writing-plans 阶段确认）
- dashboard: 当前 HEAD（具体版本号 writing-plans 阶段确认）

**跨仓库协作说明**: 本设计同时修改 2 个仓库。后端 spec 写在本文件；前端实现细节见 §3 / §4 / §5 中"前端"小节，dashboard 仓库不另写 spec 文件。

---

## 用户决策记录

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: 切换 worktree 的范围** | **A** — 仅 dashboard 视角切换；spcode 的 `_loaded_projects` 永不被新代码修改 | 最小侵入；Q1=A 是不变量，所有后续设计都基于此 |
| **Q2: endpoint 拆分** | **A** — 拆成 2 个 endpoint：`/spcode/git-worktrees`（列表）+ `/spcode/git-diff?worktree=<path>`（diff 扩展） | 职责清晰；worktree list 独立缓存 / 懒加载 |
| **Q3: 默认选中的 worktree** | **A** — 默认 = 主 worktree（`_loaded_projects[umo].directory`）；无 localStorage 记忆 | 行为可预测；项目切换时自动重置 |
| **Q4: 切换 UI 控件** | **B** — 横向 tabs，溢出时自动多行换行 | 可见性高；worktree 数量无上限 |
| **Q5: 轮询策略** | **A** — 只轮询当前激活的 worktree（10s） | 性能最低；切 tab 立即 fetch 新 worktree diff |

---

## §1 架构总览

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend (Dashboard)                                                │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  GitDiffSidebar.vue                                             │ │
│  │  ┌───────────────────────────────────────────────────────────┐  │ │
│  │  │ Header: 标题  目录  [refresh]  [close]                    │  │ │
│  │  ├───────────────────────────────────────────────────────────┤  │ │
│  │  │ Tabs (新):  [● main ]  [ feat-x ]  [ feat-y ]   ← Q4=B    │  │ │
│  │  │   ● = 主 worktree (primary, loaded via /project load)     │  │ │
│  │  ├───────────────────────────────────────────────────────────┤  │ │
│  │  │ Body:  GitDiffBodyContent(diff for selectedWorktree)      │  │ │
│  │  └───────────────────────────────────────────────────────────┘  │ │
│  │  Refs:                                                          │ │
│  │    selectedWorktree: Ref<string|null>  ← 默认 = primary (Q3=A) │ │
│  │    worktrees:        Ref<Worktree[]>   ← /spcode/git-worktrees  │ │
│  └───────────────────────────────────────────────────────────┬────┘ │
│  │           fetch diff(worktree=selectedWorktree) ───┐            │ │
│  └────────────────────────────────────────────────────┼────────────┘ │
│                                                       │              │
│   useSpcodeGitDiff(worktree: MaybeRef<string|null>)   │  Q5=A        │
│     - 接受 worktree 参数                              │  只 poll     │
│     - 每次 fetch / poll 都带 worktree=<path>          │  激活的      │
│                                                       │              │
└───────────────────────────────────────────────────────┼──────────────┘
                                                        │ HTTP
┌───────────────────────────────────────────────────────┼──────────────┐
│  Backend (spcode plugin)                              │              │
│                                                        ▼              │
│   GET /spcode/git-worktrees?umo=xxx                                  │
│     → 读 _loaded_projects[umo].directory                             │
│     → 调 `git -C <primary> worktree list --porcelain`                │
│     → 返回 [{path, branch, head_sha, is_main}, ...]                  │
│                                                                       │
│   GET /spcode/git-diff?umo=xxx&worktree=<path>      ← 新增 query     │
│     → 读 _loaded_projects[umo].directory   (作为 git repo root)      │
│     → 如 ?worktree 缺省/==primary → 走原逻辑                         │
│     → 否则  调 `git -C <worktree> diff ...` (其他参数不变)            │
│                                                                       │
│   _loaded_projects[umo] 永不变更  ← Q1=A 约束                        │
└──────────────────────────────────────────────────────────────────────┘
```

**关键不变量（来自 Q1=A 决定）**:
- spcode 的 `_loaded_projects` dict 永不被新代码修改
- spcode 的"已加载项目"概念 = "主 worktree"，由用户显式 `/project load` 控制
- Dashboard 持有"激活 worktree"状态，纯粹用于决定 `git -C` 哪个路径

---

## §2 后端 API 契约

### 2.1 新 endpoint：`GET /spcode/git-worktrees`

**Request**:

| 维度 | 值 |
|------|---|
| Method | `GET` |
| Path | `/spcode/git-worktrees` |
| Query | `umo` (可选)；省略时回退到"最近载入"（与 `git-diff` / `project-status` 一致） |
| 其他参数 | 无 |

**Response — 成功（已载入项目，且为 git 仓库）**:

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:\\github\\astrbot_plugin_spcode_toolkit",
    "umo": "qq_official:xxx",
    "worktrees": [
      {
        "path":     "F:\\github\\astrbot_plugin_spcode_toolkit",
        "branch":   "main",
        "head_sha": "5d4fa623...",
        "is_main":  true
      },
      {
        "path":     "F:\\github\\astrbot_plugin_spcode_toolkit-inta",
        "branch":   "feat/inta-shell-result-view",
        "head_sha": "148ba41b...",
        "is_main":  false
      }
    ],
    "elapsed_ms": 18,
    "reason": null
  }
}
```

**Response — 失败**（reason 集合与 `git-diff` 完全对称）:

| `reason` | 触发条件 | 其它字段 |
|----------|----------|----------|
| `no_project_loaded` | `_loaded_projects` 为空 | `worktrees: null, directory: null, umo: null` |
| `directory_missing` | primary 路径不存在 | `worktrees: null, directory: <原值>` |
| `not_a_git_repo` | `git rev-parse --is-inside-work-tree` 返回非 0 且 stderr 含 "not a git repository" | 同上 |
| `git_unavailable` | git 未安装 | 同上 |
| `git_error` | 其他 git 错误 | `stderr: "..."` |
| `feature_disabled` | agentsmd_enabled 或 codegraph_enabled 任一为 false | `worktrees: null` |

**所有失败都返回 HTTP 200**（与 `git-diff` / `project-status` 风格一致），dashboard 通过 `data.loaded` + `data.reason` 决策。

**实现要点**:
- 调 `git -C <primary> worktree list --porcelain` 一次性拿到所有 worktree
- 解析逻辑：
  - `worktree <path>` → 新条目
  - `HEAD <sha>` → 写入上一条
  - `branch refs/heads/<name>` → `branch = name`，`detached` → `branch = null`
- 第一条（按 git 定义永远是 main worktree）→ `is_main = true`；其余 `false`
- 用 UTF-8 解码（沿用上一轮 fix，不退回 cp936）

### 2.2 扩展 endpoint：`GET /spcode/git-diff?worktree=<path>`

**Query 参数变更**:

| 参数 | 旧 | 新 |
|------|------|------|
| `umo` | 可选 | 不变 |
| `worktree` | — | **新增**，可选；缺省/空 → 行为与今天完全一致（用 primary） |

**Response — 变化**: 响应结构**完全兼容 v1**，仅多一个 `data.worktree` 字段（实际生效的 worktree 路径）。

> **为什么 `data.directory` 与 `data.worktree` 值相同？**
> 保留双字段是为了:
> 1. **v1 客户端兼容** — 旧 dashboard 仍可读 `data.directory` 不受影响
> 2. **语义清晰** — `directory` 在 v1 是"项目根目录"，新加 `worktree` 是"实际生效的 worktree"，未来可能发散（例如某个 diff 模式按 staged / HEAD 比对时，directory 仍是 primary，worktree 才是请求的工作树）
> 3. **API 演进安全** — 未来可独立改两个字段语义而不破坏 client

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:\\...\\feat-inta",
    "worktree":  "F:\\...\\feat-inta",  // ★ 新增；与 directory 相同值；缺省 = primary
    "umo":       "qq_official:xxx",
    "diff":      "...",
    "stat":      "...",
    "files_changed": [...],
    "truncated": false,
    "truncated_at_bytes": 0,
    "max_bytes": 1048576,
    "elapsed_ms": 142,
    "reason": null
  }
}
```

**新增 reason**（仅当 `?worktree` 提供且非空时可能触发）:

| `reason` | 触发条件 |
|----------|----------|
| `worktree_path_invalid` | 含 `..`、超长（>4096 字符）、全空白 |
| `worktree_path_unsafe` | 路径命中 `_is_path_safe` 黑名单（`file_remove_blacklist` 等） |
| `worktree_missing` | 路径不在了（`Path.is_dir() == False`） |
| `not_a_git_repo` | 路径存在但不是 git worktree |
| `worktree_not_in_repo` | 是 git repo，但 `git-common-dir` 与主 worktree 不一致（**核心防越权检查**） |
| `git_error` | `git -C <worktree> rev-parse` 自身失败 |

### 2.3 核心防越权机制：基于 `git-common-dir` 的"同仓库"校验

不走"必须在 worktree list 中"的白名单路线（那要每次 diff 多跑一次 `worktree list`），改为**内禀校验**:

```python
import os

def _resolve_git_common_dir(git_bin: str, worktree_path: str) -> str:
    """Resolve --git-common-dir to an absolute, case-normalized path.

    CRITICAL: `git rev-parse --git-common-dir` returns a RELATIVE path
    (e.g. ".git") regardless of the input. Two completely unrelated
    repos both return ".git" as a string, so a naive equality check
    would falsely match them. We must resolve to an absolute path
    and normcase for Windows.
    """
    raw = subprocess.run(
        [git_bin, "-C", worktree_path, "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()
    return os.path.normcase(os.path.abspath(os.path.join(worktree_path, raw)))


# Use it:
common_primary = _resolve_git_common_dir(git_bin, primary)
common_requested = _resolve_git_common_dir(git_bin, requested_worktree)
if common_primary != common_requested:
    return _make_git_diff_empty_envelope(
        umo=umo, reason="worktree_not_in_repo", directory=requested_worktree,
        elapsed_ms=_elapsed(),
    )
```

**验证**: spec review 阶段已用 git 2.43.0 (Windows) 实测：
- 两个不相关 repo `repoA` / `repoB`：`--git-common-dir` 都返回 `.git`（相对路径，字符串相同 → 朴素 `==` 会误判放行）
- 加 `abspath + normcase` 后：`c:\...\repoa\.git` ≠ `c:\...\repob\.git`（正确拒绝）
- 同一 repo 的主 worktree 与 linked worktree：均解析到 `c:\...\主目录\.git`（正确放行）

**为什么这样设计（理由）**:
- 1 个 git 调用替代 1 次 `worktree list` 调用（同样 1 个 subprocess）
- 不需要 cache、不需要 list 上下文
- 即使 frontend 被人为篡改 / 写错路径，**后端仍能拦截跨 repo 读取**
- 与 `_is_path_safe` 互为补充：前者防"跨 repo"，后者防"系统目录/黑名单"

**已知陷阱（实现时必须注意）**:
- ❌ **不能**用朴素 `==` 比较 `rev-parse --git-common-dir` 的原始输出（会误判放行跨 repo）
- ❌ **不能**依赖 `--absolute-git-common-dir`（仅 git ≥ 2.45 支持，2026-06 当前 git 2.43 仍会回显 flag 名本身）
- ✅ 必须用 `os.path.normcase(os.path.abspath(os.path.join(path, raw)))` 三步组合

### 2.4 `handle_get_git_diff` 修改后流程

```
1. 读 umo
2. 查 _loaded_projects[umo] → info.directory (= primary)
3. primary Path.is_dir() 检查
4. primary git probe (rev-parse --is-inside-work-tree)
   ↓
5. 读 ?worktree query param（trim 后空 → 视同缺省）
   ↓
6. 若 worktree 提供（trim 后非空）：
   ① length & format 检查（≤4096 字符；不含 `..` 段）
      失败 → reason="worktree_path_invalid"
   ② **worktree_resolved = Path.resolve(worktree)**
      解析 symlink / 相对路径 / 驱动器号，**后续 ③-⑥ 全部用 worktree_resolved**
      （防御 §5.2 row 9：防 `/safe` → `/unsafe` 的 symlink 绕过黑名单）
   ③ _is_path_safe(worktree_resolved) 黑名单检查
      失败 → reason="worktree_path_unsafe"
   ④ Path(worktree_resolved).is_dir() 检查
      失败 → reason="worktree_missing"
   ⑤ git -C worktree_resolved rev-parse --is-inside-work-tree
      失败 → reason="not_a_git_repo"
   ⑥ _resolve_git_common_dir(git_bin, worktree_resolved) 与 primary 比对（§2.3 abs-path 算法）
      不一致 → reason="worktree_not_in_repo"
   全过则 resolved_worktree = worktree_resolved
   ↓
7. resolved_worktree 缺省 → 用 primary
8. git_prefix = [git_bin, "-C", resolved_worktree, "-c", "color.ui=never"]
9. (与今天完全一样的 4 个并发 git diff 调用，encoding="utf-8")
10.响应里 data.directory = data.worktree = resolved_worktree
```

---

## §3 前端状态模型 + Composable 改造

### 3.1 状态归属：`selectedWorktree` 在哪？

| 候选 | 评价 | 选 |
|------|------|------|
| A. `GitDiffSidebar.vue` 内 local ref | 局部状态、组件卸载即释放；符合 Q3=A 无持久化的要求 | **✅** |
| B. 挂到 `useSpcodeProjectStatus` | 混入 dashboard-only 的"激活 worktree"会污染语义 | ❌ |
| C. 新建 `useSpcodeWorktreeSelection` composable | 多余的间接层 | ❌ |

`selectedWorktree` 写在 `GitDiffSidebar.vue` 内: `ref<string | null>(null)`
- `null` = 主 worktree（初始 / 项目切换后重置）
- 字符串 = 用户已切到的副 worktree 路径

### 3.2 改造 `useSpcodeGitDiff` 接受 worktree 参数

```ts
// 旧
function useSpcodeGitDiff(): UseSpcodeGitDiff

// 新
function useSpcodeGitDiff(
  worktreeRef: MaybeRef<string | null>,   // ← 新增
): UseSpcodeGitDiff
```

**关键实现点**:

```ts
async function refresh(): Promise<void> {
  // ... existing no_project_loaded branch
  const worktree = toValue(worktreeRef)   // ★ 每次读最新值
  const resp = await pluginExtensionApi.get<SpcodeGitDiffRawResponse>(
    'spcode/git-diff',
    {
      params: {
        umo,
        ...(worktree ? { worktree } : {}),  // ★ 仅当非空时附加
      },
      signal: abortController.signal,
    },
  )
  // ... existing parse / state assignment
}

// ★ 新增：worktree 变化时自动 refresh（Q5=A：只 poll 激活的）。
// flush: 'post' 避免在同一 tick 内多次状态更新引发的 cascade；
// immediate: false（默认）确保首次 mount 时不重复 fetch（首次由 modelValue watch 触发）。
watch(worktreeRef, () => {
  if (isMounted) void refresh()
}, { flush: 'post' })
```

**关键不变量**:
- `refresh()` 总是从 `toValue(worktreeRef)` 读最新值 → polling 永远 poll 激活的那个
- 切换 worktree（`worktreeRef` 变化）→ `watch` 触发 → `refresh()` 自动跑一次新 worktree 的 fetch
- `params: { umo, ...(worktree ? { worktree } : {}) }` 保持向后兼容：旧调用方（不传 worktree）的代码行为完全不变

### 3.3 新建微型 composable：`useSpcodeWorktrees`

只做一件事：拉 worktree 列表。**不**做 polling（Q5=A + 列表变化频率极低）:

```ts
// composables/useSpcodeWorktrees.ts (新文件)
export interface UseSpcodeWorktrees {
  worktrees: Ref<SpcodeWorktree[]>
  isLoading: Ref<boolean>
  error: Ref<string | null>   // 'no_project_loaded' | 'not_a_git_repo' | 'network' | 'unknown'
  refresh: () => Promise<void>
}

export function useSpcodeWorktrees(): UseSpcodeWorktrees {
  const worktrees = ref<SpcodeWorktree[]>([])
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const spcodeStatus = useSpcodeProjectStatus()

  async function refresh(): Promise<void> {
    if (isLoading.value) return
    const umo = spcodeStatus.status.value.umo
    if (!umo) {
      worktrees.value = []
      error.value = 'no_project_loaded'
      return
    }
    isLoading.value = true
    error.value = null
    try {
      const resp = await pluginExtensionApi.get<SpcodeGitWorktreesRawResponse>(
        'spcode/git-worktrees',
        { params: { umo } },
      )
      const data = resp.data?.data
      if (data?.loaded && data.worktrees) {
        worktrees.value = data.worktrees
        error.value = null
      } else {
        worktrees.value = []
        error.value = data?.reason ?? 'unknown'
      }
    } catch (err) {
      worktrees.value = []
      error.value = classifyError(err)
    } finally {
      isLoading.value = false
    }
  }

  return { worktrees, isLoading, error, refresh }
}
```

**已知行为（实现时保留）**: `useSpcodeWorktrees` **不**主动监听 `umo` 变化。理由：
- 变化频率极低（只在 `/project load` / umo 切换时）
- 调用方（`GitDiffSidebar`）已经在监听 `spcodeStatus.status.value.directory`（§3.4），会主动调用 `wt.refresh()`
- 加 `watch(umo)` 会引入双重 fetch（自身 watch + 父组件 watch）

**与 `useSpcodeProjectStatus` / `useSpcodeGitDiff` 完全对称的形状**，方便后续维护。

### 3.4 `GitDiffSidebar.vue` 的状态机

```ts
const wt = useSpcodeWorktrees()
const selectedWorktree = ref<string | null>(null)   // null = primary
const composable = useSpcodeGitDiff(selectedWorktree)  // 传入 ref

// 当项目变更（load 切换 / umo 变化）：清空选择、重拉 worktree + diff
watch(
  () => spcodeStatus.status.value.directory,    // primary 路径
  async (newDir, oldDir) => {
    if (newDir === oldDir) return
    selectedWorktree.value = null               // 重置到 primary (Q3=A)
    expandedSet.value = new Set()               // 折叠状态也清空
    await Promise.all([wt.refresh(), composable.refresh()])
  },
)

// 首次打开 sidebar 时：先拉 worktree 列表（顺序），再选 primary，最后拉 diff
//   顺序而非并行的原因：避免"先 diff primary (worktree=null) 再 diff primary (worktree=path)"
//   的重复请求——后者是前者的同内容重发，浪费一次 RTT
watch(() => props.modelValue, async (open) => {
  if (open) {
    await wt.refresh()                                        // 1) 拉 worktrees
    if (wt.worktrees.value.length > 0) {
      const primary = wt.worktrees.value.find(w => w.is_main) ?? wt.worktrees.value[0]
      selectedWorktree.value = primary.path                    // 2) 默认 = primary (Q3=A)
    }
    await composable.refresh()                                // 3) 拉 primary diff（1 次）
    if (props.modelValue) composable.startPolling(10_000)
  } else {
    composable.stopPolling()
  }
}, { immediate: true })

// 用户手动 Refresh 按钮：同时刷新 worktree list + diff
async function onManualRefresh(): Promise<void> {
  if (isFetching.value) return
  isFetching.value = true
  try {
    await Promise.all([wt.refresh(), composable.refresh()])
  } finally {
    isFetching.value = false
  }
}
```

### 3.5 `parseSpcodeGitDiff.ts` 新增类型

```ts
export interface SpcodeWorktree {
  path: string
  branch: string | null    // null = detached HEAD
  head_sha: string
  is_main: boolean
}

export interface SpcodeGitWorktreesRawResponse {
  loaded: boolean
  directory: string | null
  umo: string | null
  worktrees: SpcodeWorktree[] | null
  reason: string | null
  stderr?: string
  elapsed_ms: number
}

// SpcodeGitDiffRawResponse 新增字段：
//   worktree: string | null   // 实际生效的 worktree 路径；缺省 = primary
```

### 3.6 状态变化表

| 触发事件 | selectedWorktree | worktrees | 备注 |
|----------|------------------|-----------|------|
| Sidebar 首次打开 | `null` → primary.path | 已加载 | 顺序流程（§3.4），0 重复 fetch |
| 用户切到 worktree B | B | 不变 | watch → composable 自动 fetch B |
| 用户按 Refresh 按钮 | 不变 | reload | `onManualRefresh` 并行打 2 个 endpoint（见 §3.4） |
| `/project load newPath` | `null` → newPrimary | reload | directory watch 触发 |
| `/project unload` | `null` | `[]` | sidebar 自动关闭（沿用现有行为） |
| umo 变化（罕见） | `null` | reload | directory watch 触发 |
| polling tick | 不变 | 不变 | refresh 走当前 worktree |

---

## §4 UI 布局：Tabs 视觉 + 多行换行

### 4.1 Tabs 出现条件

| 条件 | Tabs 是否显示 |
|------|---------------|
| `wt.worktrees.length >= 2` | ✅ 显示 |
| `wt.worktrees.length <= 1` | ❌ 隐藏（沿用 v1 视觉） |
| `wt.isLoading === true` | ❌ 隐藏（避免骨架闪烁） |
| `wt.error !== null` | ❌ 隐藏，diff 区域显示 `state.reason` 错误 |
| `wt.worktrees.length === 0` | ❌ 隐藏（防御性兜底） |

### 4.2 布局结构

```
┌────────────────────────────────────────────────────────┐
│ Project changes  📁 F:\...\feat-inta  [↻] [×]         │  ← header
├────────────────────────────────────────────────────────┤
│ [🏠 main]  [ feat-x ]  [ feat-y ]  [ long-feat-z ]     │  ← Tabs 行 (新增)
│          [ another-long-name ]                         │  ← 溢出时自动换行
├────────────────────────────────────────────────────────┤
│ [diff body ...]                                        │  ← 已有
│   diff --git a/x.py b/x.py                             │
│   @@ -1,1 +1,2 @@                                      │
│    # 你好                                              │
│   +新加一行中文                                        │
└────────────────────────────────────────────────────────┘
```

DOM 位置：在现有 `<div class="git-diff-sidebar-warning">` 之后、`<div class="git-diff-sidebar-body">` 之前。

### 4.3 Tab 视觉规范

| 元素 | 规则 |
|------|------|
| **容器** | `inline-flex`, `align-items: center`, `gap: 4px` |
| **高度** | 26px |
| **横向 padding** | 8px / 10px |
| **字号** | 12px |
| **字体** | 分支名 monospace；`v-icon` Material Icons |
| **圆角** | 6px |
| **Hover** | 背景 alpha 0.04 → 0.08 |
| **Active** | 背景 `rgba(primary, 0.12)` + 边框 `rgba(primary, 0.3)` + 文字色 primary |
| **截断** | `max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap` |
| **Focus ring** | 沿用 Vuetify 默认（无障碍要求） |

### 4.4 图标

| Worktree 状态 | 图标 | 含义 |
|---------------|------|------|
| `is_main: true` | `mdi-home` (desktop 12px / mobile 14px) | 用户 `/project load` 的主 worktree |
| `is_main: false`，有 branch | `mdi-source-branch` (12px) | 普通 linked worktree |
| `is_main: false`，detached | `mdi-source-branch` (12px) | detached HEAD worktree |

### 4.5 Label

| `branch` 状态 | 显示 |
|---------------|------|
| 字符串 | `w.branch` |
| `null` (detached) | `w.head_sha.slice(0, 7)`（如 `148ba41`），加 `v-tooltip` 显示完整 SHA |

### 4.6 多行换行实现

```css
.worktree-tabs {
  display: flex;
  flex-wrap: wrap;          /* ★ Q4=B: 横向溢出自动多行 */
  gap: 4px 6px;             /* row-gap col-gap */
  padding: 6px 14px 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  /* 防御: 避免极端多的 worktree 撑爆 sidebar */
  max-height: 30%;
  overflow-y: auto;
}
```

**为什么 `max-height: 30%`**: 防御 N>20 worktree 的极端场景，超出部分内部滚动（不横向滚动、保持换行后整体可读）。30% 足够容纳 5-6 行 tabs（26px / 行）。

**gap 规则**: 横向间距 6px（视觉舒适），纵向间距 4px（多行时紧凑）。

### 4.7 Header 目录显示同步

`directoryPath` 现有的 computed 不变，但其值现在来自 `data.worktree ?? data.directory`（后端保证这两个字段在 v1 里相同值）。当用户切到副 worktree 时，header 的 📁 tooltip 自动更新到该 worktree 的路径。

无需新代码，纯靠后端响应。

### 4.8 切换 worktree 时的过渡

```ts
function onSelectWorktree(path: string | null) {
  if (selectedWorktree.value === path) return
  selectedWorktree.value = path
  // 不需要手动 refresh — useSpcodeGitDiff 的 watch 会自动触发
  expandedSet.value = new Set()  // 折叠状态清空（不同 worktree 的 file 列表不同）
}
```

### 4.9 Mobile 适配

沿用现有 `@media (max-width: 760px)` 块，tabs 自动适配:
- 容器 `padding` 调小（左右 12px）
- `max-height: 30%` 在全屏模式下变成约 30vh，仍然合理

无需新增 mobile-specific 样式。

### 4.10 i18n 键

新增到 `dashboard/src/i18n/locales/{zh-CN,en-US,...}/features/chat.json`:

```json
{
  "spcodeProjectLoad.diffSidebar.worktreeTabs": {
    "ariaLabel": "Select worktree to view diff"
  }
}
```

**4 个 locale 文件**（实现时必须全部更新）:
- `dashboard/src/i18n/locales/zh-CN/features/chat.json`
- `dashboard/src/i18n/locales/en-US/features/chat.json`
- `dashboard/src/i18n/locales/ru-RU/features/chat.json`
- `dashboard/src/i18n/locales/ja-JP/features/chat.json`

detached SHA 的 tooltip 不需要 i18n——是技术数据。

---

## §5 错误处理 + 边界情况

### 5.1 错误 reason 完整映射表

| reason | 触发 | HTTP | 兜底行为 |
|--------|------|------|----------|
| `feature_disabled` | agentsmd/codegraph 任意关闭 | 200 | 隐藏 tabs，仅显示错误态 |
| `no_project_loaded` | `_loaded_projects` 为空 | 200 | 关闭 sidebar（沿用现有 watch） |
| `directory_missing` | primary 路径不存在 | 200 | 隐藏 tabs，显示错误态 |
| `not_a_git_repo` | primary 不是 git repo | 200 | 隐藏 tabs，显示错误态 |
| `git_unavailable` | git 未安装 | 200 | 隐藏 tabs，显示错误态 |
| `git_error` | 其他 git 错误 | 200 | 隐藏 tabs，显示 stderr |
| `worktree_path_invalid` | `?worktree` 包含 `..` / 超长 / 全空白 | 200 | **保留** 用户已选项，但 `selectedWorktree` 强制回退 `null`（= primary），显示错误态 |
| `worktree_path_unsafe` | 命中 `_is_path_safe` 黑名单 | 200 | 同上 |
| `worktree_missing` | worktree 路径已不在磁盘上 | 200 | **保留** 用户已选项（视觉），显示错误态；用户可手动切其他 tab |
| `worktree_not_in_repo` | `git-common-dir` 与 primary 不一致（最关键的防越权） | 200 | 同上 |

**关键决策：失败时不自动回弹到上一个 worktree**
- 自动回弹会让用户困惑（"我明明点了 B 怎么又变 A 了"）
- 失败态显示明确文案 + 提示用户重新选择，更可预测

### 5.2 边界情况 × 预期行为

#### 后端（spcode plugin）

| # | 场景 | 预期 |
|---|------|------|
| 1 | `?worktree=`（空字符串） | 视同缺省，行为 = v1（用 primary） |
| 2 | `?worktree=%20%20`（全空白） | trim 后为空，视同缺省 |
| 3 | `?worktree=../../../etc/passwd` | `worktree_path_invalid` |
| 4 | `?worktree=<5000 字符>` | `worktree_path_invalid` |
| 5 | `?worktree=C:\Windows\System32` | `worktree_path_unsafe`（黑名单） |
| 6 | `?worktree=/path/that/was/once/a/worktree` | `worktree_missing` |
| 7 | `?worktree=/path/to/totally/different/repo` | `worktree_not_in_repo`（git-common-dir 不一致） |
| 8 | `?worktree=/path/with/中文/dir` | 正常返回（依赖 utf-8，**已回归**） |
| 9 | `?worktree` 在 path 上是 symlink | `Path.resolve()` 后再校验（§2.4 步骤 ②） |
| 10 | 同一 umo 并发 2 个不同 worktree 的 diff 请求 | 各自独立，互不干扰（无共享 mutable state） |

#### 前端（dashboard）

| # | 场景 | 预期 |
|---|------|------|
| 1 | 首次打开 sidebar | 顺序流程（§3.4）：`wt.refresh()` → set primary → `composable.refresh()`。0 重复 fetch |
| 2 | 用户快速切换 tab A→B→A | 前一次 fetch 被 `abortController.abort()` 取消；最终状态一致 |
| 3 | 切换 tab 中途关闭 sidebar | `dispose()` 取消所有 in-flight 请求；停止 polling |
| 4 | 切换 tab 中途 `/project load` 新路径 | directory watch 触发 → `selectedWorktree=null` → 新 worktrees 加载 → 重新选 primary |
| 5 | worktree list 加载失败（network） | `wt.error` 非空 → tabs 隐藏；diff 仍按 v1 显示（用 primary） |
| 6 | diff 切换到 worktree B 后 B 不存在了 | `worktree_missing` reason → 显示错误态；用户可手动切回 A |
| 7 | umo 变化 | directory watch 触发 → 重置 + 重拉 |
| 8 | 50+ worktree 极端 case | tabs `max-height: 30%` 滚动；切换性能无下降 |
| 9 | 浏览器刷新 / 关闭再开 | Q3=A 决定 → 无持久化；新会话 `selectedWorktree=null`，从 primary 开始 |
| 10 | 切到 detached worktree | 显示 `head_sha.slice(0, 7)`，hover 显示完整 SHA + `mdi-source-branch` 图标（**无错误**） |

### 5.3 错误展示（前端）

复用 `GitDiffBodyContent` 现有的 `REASON_I18N_KEYS` 机制，**只追加 4 个新键**:

```ts
// GitDiffBodyContent.vue (扩展示意)
const REASON_I18N_KEYS: Record<string, string> = {
  // ... 现有 7 个
  worktree_path_invalid: 'spcodeProjectLoad.diffSidebar.error.reason.worktree_path_invalid',
  worktree_path_unsafe:  'spcodeProjectLoad.diffSidebar.error.reason.worktree_path_unsafe',
  worktree_missing:      'spcodeProjectLoad.diffSidebar.error.reason.worktree_missing',
  worktree_not_in_repo:  'spcodeProjectLoad.diffSidebar.error.reason.worktree_not_in_repo',
  // 未知 reason 走 'generic' 兜底
}
```

i18n 文本（4 locale × 4 键 = 16 条）:

| key | zh-CN | en-US |
|-----|-------|-------|
| `worktree_path_invalid` | 无效的 worktree 路径 | Invalid worktree path |
| `worktree_path_unsafe` | worktree 路径被安全策略拒绝 | Worktree path blocked by security policy |
| `worktree_missing` | Worktree 已不存在（可能已被外部 `git worktree remove`） | Worktree no longer exists (possibly removed externally) |
| `worktree_not_in_repo` | 该路径不属于当前项目的 git 仓库 | Path does not belong to current project's git repository |

### 5.4 安全 & 越权防护的纵深防御

```
  Frontend                                          Backend

  Tab 点击 ─→ selectedWorktree = path
                    │
                    │   HTTP GET ?worktree=path
                    └──────────────────────────────→ ① length & format check
                                                     ② Path.resolve() 解析 symlink
                                                     ③ _is_path_safe(path)
                                                     ④ Path.is_dir()
                                                     ⑤ git rev-parse --is-inside-work-tree
                                                     ⑥ _resolve_git_common_dir(...)
                                                        与 primary 的 common-dir 比对
                                                        (绝对路径 + normcase，见 §2.3)
                                                     ↓
                                                  ✓ 全部通过才执行 git diff
```

**为什么 6 道关**:
1. **format** — 防协议级误用（空串、`..`、超长）
2. **symlink resolve** — 防止通过 symlink 绕过黑名单（`/safe` 是 symlink → `/unsafe`）
3. **safety** — 复用现有黑名单，与 AGENTS.md / codegraph 行为对齐
4. **is_dir** — 路径存在性，避免把"路径已删"的请求继续往下传
5. **is-inside-work-tree** — 确认是 git worktree
6. **git-common-dir (abs-path + normcase)** — 核心防越权：跨 repo 的 worktree 路径必须被拒

后端**不信任**前端传的任何 `?worktree` 值，每条路径都重新校验。Frontend 拿到拒绝 → 显示错误态（**不**自动回弹，per §5.1 决策）。

### 5.5 监控与日志

| 层 | 日志 |
|----|------|
| 后端 (spcode) | `logger.debug(f"[git-worktrees] listed {n} worktrees in {ms}ms")` <br> `logger.warning(f"[git-diff] worktree rejected: path={path!r} reason={reason}")` |
| 前端 (dashboard) | `console.debug` 级别记录 worktree 切换事件（开发期）<br>**不**记录成功 poll（避免刷屏） |

---

## §6 部署 / 迁移 / 回滚

### 6.1 向后兼容性矩阵

| 组合 | 行为 | 是否可工作 |
|------|------|------------|
| **旧 dashboard × 新 backend** | dashboard 请求 `/spcode/git-diff`（无 `?worktree`）→ backend 视同 `worktree=null` → 返回 primary diff；dashboard 永远不调 `/spcode/git-worktrees` | ✅ 完全兼容 |
| **新 dashboard × 旧 backend** | dashboard 请求 `/spcode/git-worktrees` → backend 路由不存在 → 404 → 前端 `wt.error` 非空 → **tabs 隐藏**；diff 请求 `/spcode/git-diff?worktree=...` → 旧 backend 忽略未知 query param → 返回 primary diff | ✅ 降级但不报错 |

**降级路径必须测试**: 附录 A §前端测试 增补 `it('hides tabs when /spcode/git-worktrees returns 404 (legacy backend)')` 验证 `useSpcodeWorktrees` 正确把 404 归类为 `error='unknown'`、tabs 不渲染、diff 仍按 v1 正常显示。
| **新 dashboard × 新 backend** | 完整功能 | ✅ 目标状态 |

**结论**: **两个 repo 独立部署无顺序依赖**。任意一边先上线都不破坏另一边。

### 6.2 部署顺序建议

| 步骤 | 操作 | 风险 |
|------|------|------|
| 1 | 在 `astrbot_plugin_spcode_toolkit` 仓库 commit 后端改动 | 0（dashboard 还没用新端点） |
| 2 | 同步到 `F:\github\Astrbot\data\plugins\astrbot_plugin_spcode_toolkit` 运行副本 | 0 |
| 3 | **重启 AstrBot**（让 spcode 插件重载，注册新 endpoint） | 低（重启窗口期不影响 chat 业务） |
| 4 | 在 `Astrbot/dashboard` 仓库 commit 前端改动 | 0（用户还没刷新页面） |
| 5 | 浏览器刷新 dashboard | 触发新代码加载 |
| 6 | 打开 GitDiffSidebar → tabs 出现 | — |

**总停机**: 仅步骤 3 的 AstrBot 重启（~5s）。
**dashboard 不需要单独 build**（Vite dev server，文件保存即生效；生产部署走 CI 即可）。

### 6.3 版本号

| Repo | 建议变更 |
|------|----------|
| `astrbot_plugin_spcode_toolkit` | **minor bump**（新 endpoint 是 additive，但跨多个 subfeature） |
| `Astrbot/dashboard` | **minor bump**（新 UI feature） |

按 SemVer 严格规则：新功能 = minor bump。

### 6.4 回滚策略

| 场景 | 回滚操作 | 恢复时间 |
|------|----------|----------|
| 后端 bug | `git checkout <prev-commit> -- main.py` + 同步运行副本 + 重启 AstrBot | ~2 min |
| 前端 bug | `git checkout <prev-commit> -- <files>` + 浏览器刷新 | ~10s |
| 两者都有 | 两个 repo 同时 revert | ~5 min |

**回滚后行为**:
- 后端回滚到 v1 → dashboard 调 `/spcode/git-worktrees` 收 404 → tabs 隐藏（降级）
- 前端回滚到 v1 → 永远不调 `/spcode/git-worktrees` → 跟今天完全一样

**无需数据迁移**（新代码不改 `_loaded_projects`、不写 DB、不动 localStorage）。

### 6.5 Feature Flag?

**不加 flag**。理由:
- 5 个 Q 答案全指向"always on"（Q1=A, Q3=A 无记忆意味着无"先关再开"路径）
- 改动纯 additive，无破坏性
- 加 flag = 多写 8-10 个测试 + 1 个配置项 + 1 个 i18n 键 + 文档说明
- YAGNI: 如果想灰度，直接控制"是否部署到运行副本"就够了

### 6.6 文档交付清单

| 文档 | 路径 | 内容 |
|------|------|------|
| 设计 spec | `astrbot_plugin_spcode_toolkit/docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md` | 本设计全文 |
| 实现 plan | `astrbot_plugin_spcode_toolkit/docs/superpowers/plans/2026-06-18-git-worktree-switcher-impl.md` | writing-plans 阶段产出 |
| spcode `README.md` (主 README) | 修改 | 1-2 行：API 表格新增 `/spcode/git-worktrees`，`/spcode/git-diff` 新增 `?worktree` |
| spcode `_conf_schema.json` | **不修改** | 纯 endpoint 不需新 config 字段；feature flag 已 §6.5 论证不加 |
| dashboard AGENTS.md | `dashboard/AGENTS.md` | `## Worktree Switcher` 章节：API 路径、composable、tab UI 规则 |
| 注释 | 各源文件 | 已在本设计 §3 / §4 中明确 |

### 6.7 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Backend 改 `/spcode/git-diff` 响应破坏旧 dashboard | **低** | 低 | 响应纯 additive（旧代码忽略新 `worktree` 字段） |
| 50+ worktree 导致 sidebar 慢 | 极低 | 低 | `max-height: 30%` + 内部滚动；渲染 50 按钮仍是 O(1) |
| `git-common-dir` 校验在 sparse-checkout 场景误判 | 极低 | 中 | sparse-checkout 也共享 `git-common-dir`，**不会**误判 |
| 并发切换 tab 导致状态错乱 | 低 | 低 | 现有 `abortController?.abort()` 机制已覆盖 |
| 用户把 worktree 删除后 dashboard 仍持有引用 | 低 | 低 | 切到该 worktree → `worktree_missing` 错误态；用户手动切回；无自动恢复 |

### 6.8 整体总结

**这是一个 2-repo 协同的 additive feature**:
- 1 个新 endpoint（`/spcode/git-worktrees`）
- 1 个现有 endpoint 的扩展（`/spcode/git-diff?worktree=<path>`，1 个新 query param + 1 个响应字段）
- 1 个新 composable（`useSpcodeWorktrees`）
- 1 个现有 composable 的扩展（`useSpcodeGitDiff` 接受 `worktreeRef`）
- 1 个现有组件的扩展（`GitDiffSidebar.vue` 加 tabs 行）

**总代码量预估**（writing-plans 阶段会精化）:
- 后端: ~150 行新代码 + ~50 行新测试
- 前端: ~120 行新代码 + ~80 行新测试 + 16 条 i18n 字符串
- 总计: ~400 行（含测试）

**核心不变量**（再次强调，避免实现时漂移）:
1. `_loaded_projects` 永不被新代码修改（Q1=A）
2. **`handle_get_git_diff` 内所有 git subprocess 调用都用 `encoding="utf-8"`**（覆盖上一轮 fix 改动的 5 处：line 1750-1751 探测 + line 1788-1791 四个 diff 调用）。**模块级 `_GIT_DIFF_ENCODING = detect_console_encoding()` 常量保持不变**（仅被 `__init__` 启动期 `git --version` 探测使用，输出 ASCII 不受影响）。注意不要在本次实现中"顺手"删常量或改默认值
3. worktree list 永远从 **primary 的 git dir** 派生（保证同一份 git 仓库的工作树集合）
4. diff 永远只 poll **激活的** worktree（Q5=A）
5. 默认选中 = **primary**（Q3=A，无持久化）
6. **`_resolve_git_common_dir` 必须用 abs-path + normcase 比较**（§2.3），不能直接比较 `rev-parse` 的原始输出（跨 repo 误判）

---



---

## 附录 C: Spec Review Q&A（v1 决策的明确化）

> 此节回应 spec reviewer 子代理提出的 5 个问题。**所有 v1 答案**已在前面章节落实，此处集中显式记录以便未来 review 时不必重新讨论。

| # | Reviewer 问题 | v1 决策 | 落实位置 |
|---|---------------|---------|----------|
| 1 | Q5=A（只 poll 激活的）vs Q5=C（hover-to-refresh）哪个更好？ | **A**: Q5=A 维持 v1 决策。理由：(a) 复杂 tab 交互引入新的 bug 维度；(b) 当前 `refresh()` 1 RTT 已够快；(c) 用户主动按 Refresh 按钮可获得即时刷新 | §3.2 §3.4 |
| 2 | localStorage 持久化 `selectedWorktree` (Q3=B) 是显式拒绝还是没提？ | **显式拒绝**。理由：(a) 跨 umo/浏览器/隐私模式不一致；(b) stale state 需要额外失效逻辑；(c) Q3=A 是"项目切换时重置到 primary"的最简心智模型 | §3.1 |
| 3 | 外部 `git worktree add` 后 sidebar 不会自动感知（要按 Refresh）是有意的吗？ | **有意**。理由：(a) worktree list 变化频率极低；(b) Q5=A + 列表无 polling 是设计选择；(c) 不引入新的"auto-refresh on focus"机制，保持 polling 行为可预测 | §3.4 §3.6 |
| 4 | 为什么不用 `/spcode/project-status` 加 `worktrees: [...]` 字段而要新建 endpoint？ | Q2=A 决策：(a) 职责分离（project-status 关心 session state，git-worktrees 关心 git repo state）；(b) worktree list 极低频，独立 endpoint 便于懒加载/缓存；(c) project-status 与 git 强耦合会污染其语义 | §2.1 |
| 5 | `?worktree` 将来会变成必选吗（破坏性变更）？ | **不会**。Q1=A + 5 关防御要求 `?worktree` 始终可选。v1/v2 保持纯 additive | §2.2 §5.1 |

**Reviewer 在 v1 中提出的"Suggestions"处理记录**：
- S1 ✅: §2.2 补充 `directory` 与 `worktree` 双字段的演进理由
- S2 ✅: §4.4 主 worktree 图标在 mobile 上 14px（与 v1 desktop 12px 共用 `v-icon`，仅 mobile CSS 覆盖）
- S3 ✅: §3.3 显式说明 `useSpcodeWorktrees` 不监听 umo
- S4 ✅: §2.4 步骤 ② 增加 `Path.resolve()` symlink 处理
- S5 ✅: §3.2 worktree watcher 加 `flush: 'post'`
- S6 ✅: §6.1 增补"新 dashboard × 旧 backend"404 降级测试
- S7 ✅: §6.6 文档目标改为 `README.md`，删除 `_conf_schema.json` 行（无新 config 字段）
- S8 ✅: §4.10 显式列出 4 个 locale（zh-CN, en-US, ru-RU, ja-JP）作为实现 checklist 必备项

## 附录 A: 测试计划

### 后端（`tests/test_git_worktrees.py` 新建 + `test_git_diff.py` 扩展）

```python
# 新建 test_git_worktrees.py
class TestGitWorktreesEndpoint:
    async def test_returns_single_main_worktree(self)
    async def test_returns_multiple_worktrees(self)
    async def test_detached_worktree_branch_is_none(self)
    async def test_marks_main_worktree_correctly(self)
    async def test_chinese_path_in_worktree_works(self)        # utf-8 回归
    async def test_no_project_loaded(self)
    async def test_directory_missing(self)
    async def test_not_a_git_repo(self)
    async def test_git_unavailable(self)
    async def test_git_error(self)
    async def test_feature_disabled(self)
    async def test_umo_fallback_to_most_recent(self)
    async def test_porcelain_parser_handles_malformed(self)     # 防御性

# 扩展 test_git_diff.py
class TestGitDiffWithWorktree:
    async def test_no_worktree_param_uses_primary(self)        # v1 兼容
    async def test_empty_worktree_param_uses_primary(self)
    async def test_whitespace_worktree_param_uses_primary(self)
    async def test_specific_worktree_returns_its_diff(self)
    async def test_response_includes_worktree_field(self)
    async def test_worktree_path_invalid_dotdot(self)
    async def test_worktree_path_invalid_too_long(self)
    async def test_worktree_path_unsafe_blacklisted(self)
    async def test_worktree_missing(self)
    async def test_worktree_not_in_repo(self)
    async def test_chinese_path_worktree_diff(self)             # utf-8 回归
    async def test_does_not_modify_loaded_projects(self)        # Q1=A 不变量
```

### 前端（Vitest）

```ts
// useSpcodeWorktrees.test.ts (新)
describe('useSpcodeWorktrees', () => {
  it('fetches and stores worktree list')
  it('returns empty list and no_project_loaded when no umo')
  it('exposes git error reason when backend returns not_a_git_repo')
  it('classifies network errors')
})

// useSpcodeGitDiff.test.ts (扩展)
describe('useSpcodeGitDiff with worktree ref', () => {
  it('omits ?worktree param when ref is null')                // v1 兼容
  it('includes ?worktree param when ref is set')
  it('refetches when worktree ref changes')                    // Q5=A
  it('polling uses current worktree ref value')                // Q5=A
  it('aborts previous fetch when worktree changes rapidly')    // 边界 2
})

// GitDiffSidebar.test.ts (扩展)
describe('GitDiffSidebar with worktree tabs', () => {
  it('hides tabs when worktree count < 2')
  it('hides tabs while worktree list is loading')
  it('hides tabs when worktree list errors')
  it('shows primary worktree active by default')               // Q3=A
  it('clicking tab updates selectedWorktree and refetches')
  it('resets to primary when project changes')
  it('directory tooltip reflects active worktree path')
  it('supports multi-row wrap (CSS class check)')
})
```

## 附录 B: 文件变更摘要

### spcode 仓库

| 文件 | 变更类型 | 行数预估 |
|------|----------|----------|
| `main.py` | 修改 | +120 行（endpoint 注册、handler、parser） |
| `tools/_helpers.py` | 可能微调 | ±5 行（若要抽出 worktree parser） |
| `tests/test_git_worktrees.py` | 新增 | +150 行 |
| `tests/test_git_diff.py` | 修改 | +100 行（worktree 扩展测试） |
| `docs/superpowers/specs/2026-06-18-...` | 新增 | 本文件 |

> 注：§6.6 已明确：纯 endpoint 改动，**无新 config 字段**，`_conf_schema.json` 不修改。

### dashboard 仓库

| 文件 | 变更类型 | 行数预估 |
|------|----------|----------|
| `src/composables/useSpcodeWorktrees.ts` | 新增 | +60 行 |
| `src/composables/useSpcodeGitDiff.ts` | 修改 | +15 行 |
| `src/composables/parseSpcodeGitDiff.ts` | 修改 | +20 行（types） |
| `src/components/chat/GitDiffSidebar.vue` | 修改 | +60 行（tabs 模板 + CSS） |
| `src/components/chat/message_list_comps/GitDiffBodyContent.vue` | 修改 | +8 行（REASON_I18N_KEYS） |
| `src/i18n/locales/{zh-CN,en-US,...}/features/chat.json` | 修改 | +3 行 × 4 locale |
| `tests/composables/useSpcodeWorktrees.test.ts` | 新增 | +60 行 |
| `tests/composables/useSpcodeGitDiff.test.ts` | 修改 | +40 行 |
| `tests/components/GitDiffSidebar.test.ts` | 修改 | +60 行 |
