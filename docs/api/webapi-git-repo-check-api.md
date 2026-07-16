# WebAPI v2.18.0: Git Repo Check API Reference

> Dashboard 消费本文档;后端实现见 `tools/webapi/git_repo_check.py`
> 测试覆盖见 `tests/test_git_repo_check.py`
> Author: elecvoid243 @ 2026-07-16

## 概述

v2.18.0 (2026-07-16) 新增 1 个只读端点,将原先耦合在所有 git-* 端点 `_git_endpoint_preflight`
第 5 步中的"是否是 git 仓库"判断能力**解耦**为独立的结构化返回。

| 端点 | 方法 | 用途 |
|------|------|------|
| `/spcode/git-repo-check` | GET | 判断给定目录是否为 git 仓库(结构化返回 `is_git_repo` / `git_available`) |

**端点总数演进**:30 (v2.17.0) -> **31 (v2.18.0)**。

**设计原则**:

一个目录是否是 git 仓库是 `path` 的纯函数,与 umo(会话)、worktree、项目加载状态等
运行时上下文完全无关。因此本端点**直接接受 `path` 参数**(与 `file-browser` / `git-init`
一致),不经过 `_git_endpoint_preflight` 的 umo 解析链。

此前 Dashboard 若需判断某目录是否支持 Git 功能,只能调用 `git-branches` 或 `git-status`
等端点,从其 `reason` 码间接推断(`not_a_git_repo` / `git_unavailable` / `git_error`)。
这带来两个问题:

1. **错误耦合** - 必须先通过某个会话加载项目(umo)才能探测,而"是否是 git 仓库"本身与
   会话无关
2. **字段缺失** - error envelope 不含 `is_git_repo` / `git_available` 布尔字段,前端需自行映射

新端点将 git repo probe 作为**核心职责**,以结构化 envelope 返回判断结果,
供 Dashboard 在任意时刻对任意目录做 Git 可用性探测。

---

## 端点详细规格

### GET /spcode/git-repo-check

判断给定目录是否为 git 仓库。

**判断依据**: 执行 `git rev-parse --is-inside-work-tree`

- 命令成功(stdout = `true`) -> `is_git_repo = true`
- 命令失败且 stderr 含 `"not a git repository"` -> `is_git_repo = false`
- git 可执行文件不存在 -> `git_available = false`
- 其他失败 -> `reason = git_error`

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | **是** | 要探测的目录绝对路径 |

> **不依赖 `umo` / `worktree`**: 本端点不经过 `_git_endpoint_preflight`,不解析会话,
> 不加载项目状态。`path` 是唯一的输入参数。

**校验流程**:

1. `path` 非空校验 -> `invalid_param`
2. 路径安全校验(`is_path_safe`:系统黑名单 + 用户黑名单) -> `path_unsafe`
3. 目录存在性校验(`Path.is_dir()`) -> `path_not_directory`
4. git repo 探测(`git rev-parse --is-inside-work-tree`)

**成功响应 - 是 git 仓库 (HTTP 200)**:

```json
{
  "status": "ok",
  "data": {
    "is_git_repo": true,
    "git_available": true,
    "directory": "D:/Users/dev/projects/myapp",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 12.34
  }
}
```

**成功响应 - 非 git 仓库 (HTTP 200)**:

```json
{
  "status": "ok",
  "data": {
    "is_git_repo": false,
    "git_available": true,
    "directory": "D:/Users/dev/projects/plain-folder",
    "reason": "not_a_git_repo",
    "stderr": "fatal: not a git repository (or any of the parent directories): .git",
    "elapsed_ms": 15.67
  }
}
```

> **注意**: "非 git 仓库"是 HTTP 200 的成功响应(`success: false` +
> `reason: "not_a_git_repo"`),不是 HTTP 错误。这是预期内的业务结果,
> 而非前置校验失败。

**失败响应 - git 不可用 (HTTP 200)**:

```json
{
  "status": "ok",
  "data": {
    "is_git_repo": null,
    "git_available": false,
    "directory": "D:/Users/dev/projects/myapp",
    "reason": "git_unavailable",
    "stderr": "git 未安装或不在 PATH 中",
    "elapsed_ms": 3.21
  }
}
```

**前置失败响应 (HTTP 200)**:

前置失败(path 为空 / 不安全 / 不是目录)时,`is_git_repo` 和
`git_available` 均为 `null`(未执行探测):

```json
{
  "status": "ok",
  "data": {
    "is_git_repo": null,
    "git_available": null,
    "directory": "",
    "reason": "invalid_param",
    "stderr": "",
    "elapsed_ms": 0.12
  }
}
```

---

## 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 固定 `"ok"`(envelope 外层) |
| `data.is_git_repo` | boolean \| null | `true` = 是 git 仓库;`false` = 非 git 仓库;`null` = 未探测(前置失败或 git 不可用) |
| `data.git_available` | boolean \| null | `true` = git 可执行文件可用;`false` = git 未安装;`null` = 未探测(前置失败) |
| `data.directory` | string | 探测目标目录的绝对路径(已 resolve) |
| `data.reason` | string \| null | `null` = 是 git 仓库;其他值见下表 |
| `data.stderr` | string | git 子进程的 stderr 输出(截断);前置失败时为空或安全原因 |
| `data.elapsed_ms` | number | 端点端到端耗时(毫秒) |

---

## ReasonCode 一览

| reason | is_git_repo | git_available | 含义 | 触发条件 |
|--------|-------------|---------------|------|----------|
| `null` | `true` | `true` | ✅ 是 git 仓库 | `git rev-parse --is-inside-work-tree` 成功 |
| `not_a_git_repo` | `false` | `true` | 非 git 仓库 | git 探测失败,stderr 含 "not a git repository" |
| `git_unavailable` | `null` | `false` | git 未安装 | git 可执行文件不存在(FileNotFoundError) |
| `git_error` | `null` | `null` | git 探测异常 | 其他 git 失败(权限/超时/未知错误) |
| `invalid_param` | `null` | `null` | path 为空 | path 未传或仅空白 |
| `path_unsafe` | `null` | `null` | 路径不安全 | 系统黑名单或用户黑名单命中 |
| `path_not_directory` | `null` | `null` | 不是目录 | path 存在但不是目录,或不存在 |

---

## `is_git_repo` × `git_available` 状态矩阵

前端可根据这两个字段的组合快速判定 Git 功能可用性:

| `is_git_repo` | `git_available` | 前端建议 |
|---------------|-----------------|----------|
| `true` | `true` | ✅ Git 功能完全可用,展示所有 Git 面板 |
| `false` | `true` | ⚠️ 目录存在但非 git 仓库,显示"初始化仓库"按钮(调用 `/spcode/git-init`) |
| `null` | `false` | ❌ git 未安装,显示安装提示 |
| `null` | `null` | ❌ 未知错误(`git_error`)或前置失败,显示错误原因 |

---

## 错误处理最佳实践 (Dashboard 集成)

| reason 类别 | UI 建议 |
|-------------|---------|
| `null` | 展示完整 Git 面板(分支/提交/diff 等) |
| `not_a_git_repo` | 显示"当前目录不是 Git 仓库",提供"初始化仓库"按钮 |
| `git_unavailable` | 显示"未检测到 Git,请先安装 Git 并添加到 PATH" |
| `git_error` | 显示 stderr 内容,引导用户检查权限或磁盘状态 |
| `invalid_param` | 提示"path 参数不能为空" |
| `path_unsafe` | 显示"路径不安全:已命中黑名单" |
| `path_not_directory` | 显示"指定路径不是目录,请检查路径" |

---

## Dashboard 集成示例 (Vue 3 Composition API)

```typescript
// 类型定义
interface GitRepoCheckResponse {
  status: 'ok'
  data: {
    is_git_repo: boolean | null
    git_available: boolean | null
    directory: string
    reason: string | null
    stderr: string
    elapsed_ms: number
  }
}

// Git 可用性探测(仅需 path,不需要 umo)
async function checkGitRepo(path: string): Promise<GitRepoCheckResponse> {
  const params = new URLSearchParams({ path })
  const resp = await fetch(`/spcode/git-repo-check?${params}`)
  return await resp.json()
}

// 在项目加载后或用户选择目录时探测 Git 状态
const gitStatus = ref<{
  isGitRepo: boolean
  gitAvailable: boolean
  ready: boolean
}>({ isGitRepo: false, gitAvailable: false, ready: false })

async function checkDirectory(directory: string) {
  const result = await checkGitRepo(directory)
  const data = result.data

  gitStatus.value = {
    isGitRepo: data.is_git_repo === true,
    gitAvailable: data.git_available !== false,
    ready: true,
  }

  if (data.reason === null) {
    // ✅ 是 git 仓库,加载 Git 面板
    await loadGitDashboard(directory)
  } else if (data.reason === 'not_a_git_repo') {
    // ⚠️ 非 git 仓库,显示初始化按钮
    showInitButton.value = true
  } else if (data.reason === 'git_unavailable') {
    // ❌ git 未安装
    showGitInstallNotice.value = true
  } else {
    // 其他错误,显示原因
    showError(data.stderr || data.reason)
  }
}

// 配合 git-init 端点实现"一键初始化"
async function initRepo(directory: string) {
  const resp = await fetch('/spcode/git-init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: directory, initial_branch: 'main' }),
  })
  const result = await resp.json()
  if (result.data?.initialized) {
    // 初始化成功,重新探测
    await checkDirectory(directory)
  }
}
```

---

## 轮询建议

本端点**不支持 ETag/304 缓存**(判断结果不频繁变化,无需缓存机制)。

- Dashboard 在**项目加载后**或**用户选择目录时**调用一次即可
- 若用户手动 `git init` 或删除 `.git/` 目录后需刷新,前端重新调用本端点

---

## 与其他端点的关系

```
用户选择/加载目录 (path)
  │
  ▼
GET /spcode/git-repo-check?path=...  ◄── 本端点(第一步:探测 Git 可用性)
  │
  ├─ is_git_repo=true  ──►  GET /spcode/git-branches       (列出分支)
  │                        GET /spcode/git-status           (工作区状态)
  │                        GET /spcode/git-log              (提交历史)
  │                        POST /spcode/git-stage           (暂存)
  │                        POST /spcode/git-commit          (提交)
  │                        ... (所有 git-* 端点)
  │
  ├─ is_git_repo=false ──►  POST /spcode/git-init           (初始化仓库)
  │
  └─ git_available=false ─► 显示安装提示(无可用 git 端点)
```

**调用时序**: Dashboard 应在调用任何其他 `git-*` 端点之前,先调用本端点
探测 Git 可用性,避免对非 git 仓库的无效请求(虽然其他端点的 preflight
也会拦截,但本端点提供了更清晰的结构化判断)。
