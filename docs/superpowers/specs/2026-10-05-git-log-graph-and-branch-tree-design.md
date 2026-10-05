# git-log 图形参数 + Dashboard 侧边栏分支树 — 设计

> 2026-10-05 · elecvoid243 · 状态：定稿
> 实现计划：`docs/superpowers/plans/2026-10-05-git-log-graph-and-branch-tree.md`
> 涉及仓库：`astrbot_plugin_spcode_toolkit`（后端）+ `AstrBot_for_spzx`（dashboard 前端）

## 1. 目标

在 `GitDiffSidebar.vue` 的历史视图里画出**分支树**（commit DAG + lane），且**不新增 Web API 端点**、**不新增用户可见控件**。

非目标：服务端计算 lane（`git log --graph` ASCII 解析）、独立的图形 tab / 图形面板、lane 硬上限与溢出折叠 UI。

## 2. 数据契约（现状核对结论）

| 图形所需输入 | 现有来源 | 状态 |
|---|---|---|
| 节点 sha / subject / 时间 / shortstat | `GET /spcode/git-log` | 已有 |
| **边：parents** | `git-log` 的 `LOG_FORMAT` 第 11 字段 `%P` → `commits[].parents`（`tools/webapi/git_log.py`），前端 `parseSpcodeGitWorkflow.ts` 已解析保留 | 已有 |
| 分支 tip / current / detached / remote | `GET /spcode/git-branches` → `branches[].sha`（**缩写 sha**，`%(objectname:short)`） | 已有，按前缀 join |
| tag 标签 | `git-log` 的 `commits[].tags`（附注 tag 已剥到 commit sha） | 已有 |
| worktree HEAD | `GET /spcode/git-worktrees` 的 `head_sha` | 已有 |

**结论**：DAG 与 ref 锚点齐备，lane 分配是纯前端计算。缺失的只有 `git-log` 的**跨 ref 抓取**与**拓扑排序**两个能力。

## 3. 后端契约（插件仓库）

### 3.1 新增 query 参数

| 参数 | 取值 | 行为 |
|---|---|---|
| `all` | `1` / `true`（大小写不敏感）→ `--all`；`0` / `false` / 缺省 / 空串 → 不加旗标；**其它值 → `invalid_param`** | 跨 ref 抓取；与显式 `ref` 是 git 原生的并集语义 |
| `topo` | 同上解析 | 追加 `--topo-order`；多 ref 下默认 commit-date 顺序会把 parent 排到 child 之前，破坏 lane |

安全不变量保持：`ref` 以 `-` 开头仍然 `invalid_param`（禁止选项注入）。`--all` / `--topo-order` 只能由**白名单布尔**映射，绝不接受裸字符串。

### 3.2 ETag

`query_fingerprint` 必须追加 `all` / `topo` 维度（否则两种模式共享一个 ETag → 假 304 回放另一模式的快照）。

### 3.3 前端不发的组合

`all=true` 时前端**不发** `ref` / `rev`（保持 query 元组干净，避免"换分支但内容相同"的无谓 ETag 变化）。

## 4. 前端契约（主仓库 dashboard）

### 4.1 位置（唯一落点）

分支树 = `GitLogView.vue` 的 `.git-log-item` **行内左侧固定 gutter**，复用现有那条"只在选择模式出现"的左栏（`.git-log-item-select`，`left: 8px`）。

明确不放在：顶层 `viewMode` 第 6 个 tab、列表上方的独立图形面板、files / diff 视图。

### 4.2 视觉规格

| 项 | 值 |
|---|---|
| LANE_W | 13px |
| PADX | 4px |
| 节点 cy | 17px（与 `.git-log-item-select` 的 `top:9px` + 16px 盒同心） |
| 曲线/节点 SVG 层 | 高 34px（仅覆盖首行 commit 行） |
| 竖线 | 1.5px 绝对定位元素（`top:0;bottom:0`）→ 展开行时自然拉伸，不重算 lane |
| gutter 宽 `--gw` | `lanes > 1 ? 8 + lanes*13 : 0`（线性历史零占用） |
| dangling（父在窗口外） | 下半段竖线 0.32 不透明度 |
| lane 调色板 | 6 色，`--spcode-graph-l0..l5`，浅色主题覆盖块单独定义 |

lane 颜色是**装饰**（颜色 = 列，可复用），分支身份由 chip 文本承载。

### 4.3 布局算法（`layoutGraph`，纯函数）

输入 topo 序的 `{sha, parents[]}[]`，逐行 5 步：收敛（`ins`）→ 贯穿（`passIn`）→ 占列（`lane`）→ 分叉/汇出（`outs`，含"父已被别列占用 → 本列释放"）→ 悬空（`dangling`）。列只复用、不重排。

### 4.4 入口

「所有分支」作为 `branchPickerItems` 的**首项**（哨兵值 `__spcode_all_refs__`），选中即 `allRefs=true` → 请求带 `all=true&topo=true`。新增控件 0 个。

## 5. 已知限制

- `MAX_LOG_BYTES`（1MB）截断时，窗口内所有指向窗口外的父提交都渲染为 dangling 淡化线；配合现有 `truncated` 横幅表达，不做额外 UI。
- lane 无硬上限；并发列数由 lane 复用自然压到历史峰值。列数极多时 gutter 会变宽（未折叠），记录为后续项。
- `activeBranch` 在"所有分支"模式下等于哨兵值（≠ 当前分支名），因此 revert / amend / reset 隐藏、cherry-pick 可见 —— 与 `__all__` 的语义一致，不是 bug。
