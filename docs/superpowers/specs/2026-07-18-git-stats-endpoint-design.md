# git-stats 端点设计 — 变更统计服务端聚合

> Author: elecvoid243 · Created: 2026-07-18 · Spec for v2.21
> Related: 主仓库 `docs/superpowers/specs/2026-07-18-git-stats-heatmap-design.md`（前端面板）, `docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md`（git 端点通用约定）, `docs/superpowers/specs/2026-06-24-git-log-shortstat-alignment-fix-design.md`（哨兵对齐先例）

## 1. 背景与动机

Dashboard GitDiffSidebar 的 History 子页要新增「变更热力图与统计」面板（日历热力图 + 热点文件 Top10 + 汇总行）。前端聚合方案有两个硬上限：

- `/spcode/git-log` 的 `MAX_N=500`，覆盖不了全量历史；
- git-log 响应只有 per-commit shortstat 汇总，**没有按文件的行级统计**，热点文件做不出来。

因此新增 `GET /spcode/git-stats`：服务端跑**单次** `git log --numstat`，在 Python 侧聚合出按日统计与热点文件排行，前端拿到即可渲染。

## 2. 需求摘要

- 新增 `GET /spcode/git-stats` Web API 端点（模块 `tools/webapi/git_stats.py`）
- 全链路复用现有 git 端点约定：`_git_endpoint_preflight` 5 步前置、`_make_envelope`、`_run_git_async`、`ReasonCode`、ETag 缓存（HEAD sha + TTL）
- 聚合维度：`days`（按日 commits/+/-）、`hot_files`（按触及 commit 数排行）、`totals`、`range`
- 扫描上限 `max_commits`（默认 5000，硬顶 20000）+ `truncated` 标志，防大仓库超时
- 纯只读，不写工作区、不写 index

## 3. 端点契约

### 3.1 请求

```
GET /spcode/git-stats?ref=HEAD&since=&until=&max_commits=5000&top_files=10
```

`umo` / `worktree` 走标准 handler kwargs（同 git-log）。

| 参数 | 默认 | 校验 | 说明 |
|------|------|------|------|
| `ref` | `HEAD` | 长度 ≤512，禁止以 `-` 开头（防选项注入，同 git-log 的 ref 校验） | 统计起点引用 |
| `since` / `until` | 空 | 长度 ≤512；非空时透传 `git log --since/--until` | 时间窗（ISO 日期/时间） |
| `max_commits` | 5000 | int，1..20000，否则 `invalid_param` | 扫描上限 |
| `top_files` | 10 | int，1..50，否则 `invalid_param` | 热点文件条数 |

### 3.2 响应

envelope 沿用 `_make_envelope`；成功标志沿用 `deriveSuccess`（`reason === null`）：

```jsonc
{
  "status": "ok",
  "data": {
    "loaded": true,
    "umo": "...", "worktree": null, "directory": "...",
    "ref": "HEAD",
    "resolved_sha": "abc123...",
    "days": [
      {"date": "2026-07-18", "commits": 5, "additions": 320, "deletions": 41}
    ],
    "hot_files": [
      {"path": "astrbot/core/pipeline.py", "commits": 12, "additions": 800, "deletions": 120}
    ],
    "totals": {"commits": 132, "additions": 9200, "deletions": 3100, "files_changed": 47},
    "range": {"first": "2026-05-01", "last": "2026-07-18"},
    "truncated": false,
    "max_commits": 5000,
    "reason": null, "stderr": "", "elapsed_ms": 230
  }
}
```

| 字段 | 说明 |
|------|------|
| `days` | **稀疏**（只含有 commit 的天）、按日期升序；`date` 为作者本地日期（`%aI` 前 10 字符） |
| `hot_files` | 排序：`commits` 降序 → `(additions+deletions)` 降序 → `path` 升序；截断到 `top_files` |
| `totals.files_changed` | 触及过的去重文件数 |
| `range` | 实际纳入统计的首/末 commit 日期（截断时反映的是已统计部分） |
| `truncated` | 扫描被 `max_commits` 截断时为 true（见 §4.4 的 max+1 判定） |

**失败 reason**（全部复用 `ReasonCode`，无新增）：`feature_disabled` / `no_project_loaded` / `worktree_invalid` / `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` / `empty_repository`（无任何 commit，同 git-log 空仓库路径）/ `invalid_param`。

## 4. git 调用与解析

### 4.1 命令（单次调用）

```
git log --pretty=tformat:@@STATS@@%x00%aI --numstat --no-renames -n <max_commits+1> [--since=...] [--until=...] <ref>
```

- **哨兵对齐**：每条 commit 的 format 块以 `@@STATS@@\x00<aI日期>` 开头，后续 numstat 行归属该 commit——沿用 git-log shortstat 的哨兵对齐先例（2026-06-24 设计），关键不变量一致：**merge commit 后面没有 numstat 行**。
- `--no-renames`：rename 按「删旧 + 增新」处理，numstat 行恒为 `<adds>\t<dels>\t<path>`，解析最简；对聚合统计语义可接受（前端 spec 决策 Q4 的代价说明）。
- 不传 `--first-parent` / `-m`：走全父提交遍历，merge 合入的提交计入活跃度（与 GitHub contributions 口径一致）。

### 4.2 解析规则

1. 按 `@@STATS@@\x00` 切分原始输出；每个块首行 = ISO 日期，其余非空行 = numstat 行。
2. numstat 行解析：`\t` 三段切分；`adds`/`dels` 为 `-`（二进制）→ 记 0，但**仍计入该文件的 commits 触及数**。
3. merge commit 块（无 numstat 行）→ `commits += 1`，行数 0。
4. 日期聚合键：`aI[:10]`（作者本地日期），JS 侧不再二次分桶。
5. 防御：块首行不匹配 ISO 日期格式（`YYYY-MM-DDT...`）→ 整块跳过（同 git-log 的 SHA 校验防御思路）。

### 4.3 聚合产出

- `days`：`dict[date] -> {commits, additions, deletions}`，排序转数组
- `hot_files`：`dict[path] -> {commits, additions, deletions}`，按 §3.2 排序规则截断
- `totals.files_changed`：hot_files 聚合的**完整** key 数（截断前）
- `range`：聚合集合内首/末 commit 日期

### 4.4 truncated 判定（max+1 技巧）

向 git 请求 `max_commits + 1` 条；若实际拿到 max+1 条 → 丢弃末条、置 `truncated=true`。与 git-log 的 hasMore 判定同构，无近似误差。

## 5. 防护与性能

| 项 | 值 | 说明 |
|---|---|---|
| `MAX_STATS_BYTES` | 8 MB | git stdout 硬顶，超出按 `git_error` 处理（stderr 截断附带回显） |
| 参数长度 | 512 | ref/since/until 同 git-log |
| git 超时 | 复用 `_run_git_async` 默认 | 不新增配置 |
| ETag | resolved HEAD sha，TTL 1.5s，缓存 64 项 | key = `(directory, ref, since, until, max_commits, top_files)`；复用 `file_browser` 的 `_common_cache_headers` / `_get_if_none_match` / `_make_304_response` |

性能基线：5000 commit × 平均 5 文件 ≈ 1.3 MB stdout，单次进程调用 + O(N) Python 聚合，亚秒级；大仓库由 `max_commits` + `truncated` 兜底。

## 6. Preflight 与注册

- preflight：`_git_endpoint_preflight(plugin, umo=umo, worktree_param=worktree)`，5 步与其他 git-* 端点完全一致（feature flag → umo 解析 → worktree 校验 → 目录存在 → git 仓库探测）。
- 注册：`tools/webapi/__init__.py` 的 `ROUTES` 追加：

```python
(
    "/spcode/git-stats",
    ["GET"],
    git_stats.handle,
    "获取已加载项目的变更统计(按日聚合 + 热点文件,供 stats 面板调用)",
),
```

- `tests/test_webapi_end_to_end.py` 路由计数断言 35 → 36。

## 7. 测试计划（`tests/test_git_stats.py`）

沿用现有 git 端点测试风格（fixture 仓库 + 真实 git 调用）：

1. **聚合正确性**：已知 commit 序列（不同日期/作者/文件），断言 days/hot_files/totals/range 精确匹配
2. **merge commit**：构造合并提交 → commit 计数 +1、行数 0
3. **二进制文件**：numstat `- -` → 行数 0、文件触及计数 +1
4. **rename**：`--no-renames` 下按删+增计入（旧路径 del、新路径 add）
5. **max+1 截断**：`max_commits=2` 对 3 条 commit → `truncated=true`、统计只含前 2 条
6. **since/until 透传**：时间窗外 commit 不纳入
7. **参数校验**：非法 max_commits/top_files/ref → `invalid_param`
8. **preflight**：未加载项目 / 非法 worktree / 非 git 目录 → 对应 reason
9. **空仓库**：`empty_repository`
10. **ETag**：同 key 二次请求 → 304；HEAD 移动后 → 200 新数据

## 8. 明确不做（YAGNI）

- `path` 过滤参数（面板 v1 恒为整仓库统计）
- 作者维度聚合（前端 spec §明确不做作者分布图）
- 按周/按月聚合粒度（按日足够，前端可自行按周汇总）
- 服务端分页（一次返回全部聚合结果，体积由 max_commits 间接约束）
