# WebAPI v2.17.0: Git Init / Branch / Revert API Reference

> Dashboard 消费本文档;后端实现见 `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md`
> 测试覆盖见 `tests/test_git_init_*.py` / `tests/test_git_branch_*.py` / `tests/test_git_revert.py`
> Author: elecvoid243 @ 2026-07-15

## 概述

v2.17.0 (2026-07-15) 新增 6 个 git 相关端点,涵盖 **仓库生命周期 → 分支管理 → 历史回滚** 的完整链路。

| PR | 端点 | 方法 | 用途 |
|----|------|------|------|
| PR-B | `/spcode/git-init` | POST | 在已存在空目录上 `git init` |
| PR-C | `/spcode/git-branches` | GET | 列出分支(local/remote)+ 当前 + 默认 |
| PR-D | `/spcode/git-branch-create` | POST | 从当前 HEAD 拉新分支 |
| PR-E | `/spcode/git-branch-delete` | POST | git branch -d/-D(硬禁 current branch) |
| PR-F | `/spcode/git-branch-switch` | POST | git switch \<name\>(支持 create/detach/force 跨字段) |
| PR-G | `/spcode/git-revert` | POST | 自动生成回滚 commit |

**端点总数演进**:24 (spec B 2026-07-11) → **30 (v2.17.0)**。

**关键不变量**:
- `git-init` 是 webapi 中**唯一**完全豁免 `_git_endpoint_preflight` 的写端点(需要初始化空目录)
- `git-branch-delete` 硬禁删 current branch,`force=true` 不绕过
- `git-revert` 复用 `git_commit._classify_commit_error` + `_build_git_env`(env passthrough)

---

## 各端点详细规格

### 1. POST /spcode/git-init

在已存在目录上执行 `git init`,**不检查**项目加载(因为"未加载" = "你要 new")。

> **v2.17.1 更新**:新增 `force: bool` 字段(默认 `false`)。`force=true` 时允许在**非空目录**
> 上 init git 仓库(跳过 `directory_not_empty` 检查)。`already_a_git_repo` 是 hard-ban,
> `force` 不绕过。详见 `docs/superpowers/specs/2026-07-16-git-init-force-design.md`。

**Body (JSON, 必传)**:
```json
{
  "path": "D:/Users/dev/projects/myapp",     // 必填,必须存在且为目录
  "initial_branch": "main",                    // 可选,默认 "main"
  "bare": false,                               // 可选,默认 false
  "force": false                               // 可选,默认 false(v2.17.1);true 时允许非空目录
}
```

**成功响应 (HTTP 200)**:
```json
{
  "success": true,
  "reason": null,
  "elapsed_ms": 234,
  "data": {
    "initialized": true,
    "path": "D:/Users/dev/projects/myapp",
    "initial_branch": "main",
    "bare": false,
    "force": false,
    "git_dir": "D:/Users/dev/projects/myapp/.git"
  }
}
```

**错误 reason**:
| reason | 含义 |
|--------|------|
| `path_not_directory` | path 不存在或不是目录 |
| `directory_not_empty` | 目录非空(且 `force=false`);**v2.17.1: `force=true` 可豁免** |
| `already_a_git_repo` | 目录已是 git repo(.git 已存在,**hard-ban,`force` 不绕过**) |
| `path_unsafe` | path 含 `..` 或黑名单命中 |
| `init_failed` | `git init` 子进程失败 |
| `worktree_blacklisted` | path 命中 file_remove_blacklist |

**`force=true` 行为矩阵(v2.17.1)**:
| 目录状态 | `force=false`(默认) | `force=true` |
|----------|----------------------|--------------|
| 空目录 | ✅ init | ✅ init |
| 非空目录(无 `.git/`) | ❌ `directory_not_empty` | ✅ **跳过检查,直接 init** |
| 已有 `.git/` | ❌ `already_a_git_repo` | ❌ `already_a_git_repo`(hard-ban) |
| 路径不存在 | ❌ `path_not_directory` | ❌ `path_not_directory`(force 不绕过) |
| 路径不安全 | ❌ `path_unsafe` | ❌ `path_unsafe`(force 不绕过) |

---

### 2. GET /spcode/git-branches

列出所有 local + remote 分支,标记当前 + 默认。**支持 ETag/304 缓存**。

**Query 参数**:
- `umo?` — 用户会话 id
- `worktree?` — worktree 路径(可选,默认 primary)

**成功响应 (HTTP 200)**:
```json
{
  "success": true,
  "reason": null,
  "elapsed_ms": 45,
  "data": {
    "branches": [
      {
        "name": "main",
        "is_local": true,
        "is_remote": false,
        "current": true,
        "upstream": "origin/main",
        "upstream_tracking": "ahead"
      },
      {
        "name": "origin/feature/x",
        "is_local": false,
        "is_remote": true,
        "current": false
      }
    ],
    "default": "main",
    "detached": false,
    "total": 5
  }
}
```

**响应头**: `ETag`, `Cache-Control: private, max-age=1`

**错误 reason**: 通用 preflight 失败(`no_project_loaded` / `not_a_git_repo` / `worktree_invalid` / `git_error`)

---

### 3. POST /spcode/git-branch-create

从当前 HEAD(或指定 start_point)创建新分支。

**Body (JSON, 必传)**:
```json
{
  "name": "feature/x",          // 必填,符合 git ref-name 规范
  "start_point": "main",         // 可选,默认 HEAD
  "force": false                 // 可选,默认 false(若已存在则报错)
}
```

**成功响应 (HTTP 200)**:
```json
{
  "success": true,
  "reason": null,
  "elapsed_ms": 87,
  "data": {
    "created": true,
    "name": "feature/x",
    "start_point": "main",
    "forced": false,
    "sha": "a1b2c3d4..."         // SHA of the new branch tip
  }
}
```

**错误 reason**: `invalid_branch` (name 非法) / `branch_exists` / `ref_not_found` (start_point 不存在) / 通用

---

### 4. POST /spcode/git-branch-delete

删除分支(`git branch -d` 仅 merged,`-D` 含 unmerged)。**硬禁 current branch**。

**Body (JSON, 必传)**:
```json
{
  "name": "feature/x",
  "force": false    // 默认 false → 仅删已合并;true → git branch -D 强制删
}
```

**成功响应**:
```json
{
  "success": true,
  "data": {
    "deleted": true,
    "name": "feature/x",
    "force": false,
    "merged": true,
    "sha": "a1b2c3d4..."
  }
}
```

**错误 reason**:
- `branch_is_current` — **硬禁删**当前分支,`force=true` 不绕过
- `branch_not_merged` — `-d` 模式但分支未合并
- `branch_not_found` — 分支不存在
- `worktree_dirty` — worktree 有未提交改动(并 `force=false` 时)
- `worktree_missing_local` — 当前 worktree 缺少本地分支
- `branch_checked_out_other_worktree` — 该分支被其他 worktree 检出

---

### 5. POST /spcode/git-branch-switch

切换当前分支或创建+切换。支持 `create` + `detach` + `force` 跨字段组合。

**Body (JSON, 必传)**:
```json
{
  "name": "feature/x",
  "create": false,             // 默认 false
  "start_point": "main",       // 仅 create=true 时使用
  "force": false,              // 强制覆盖 worktree dirty
  "detach": false              // 切到 SHA 后 detached HEAD
}
```

**跨字段校验**:
- `create=true AND detach=true` → **互斥** → `invalid_body`

**成功响应**:
```json
{
  "success": true,
  "data": {
    "switched": true,
    "name": "feature/x",
    "previous": "main",
    "created": false,
    "force": false,
    "detach": false
  }
}
```

**错误 reason**: `invalid_branch` / `branch_not_found` / `branch_exists` (创建模式 + 已存在) / `worktree_dirty` / `invalid_body` (跨字段)

---

### 6. POST /spcode/git-revert

生成回滚 commit。复用 git_commit 的 env passthrough + stderr 分类。

**Body (JSON, 必传)**:
```json
{
  "ref": "HEAD",       // 默认 "HEAD"
  "no_edit": true      // 必须 true(headless 服务不开编辑器)
}
```

**前置条件**:
- ref 必须能解析为 commit(通过 `^{commit}` 检验)
- worktree 必须 clean

**成功响应**:
```json
{
  "success": true,
  "data": {
    "reverted": true,
    "ref": "a1b2c3d4...",
    "revert_sha": "e5f6g7h8...",
    "revert_message": "Revert \"commit title\"",
    "files_touched": ["src/file.py"],
    "directory": "...",
    "umo": "...",
    "worktree": "..."
  }
}
```

**错误 reason**:
- `commit_not_found` — ref 不解析为 commit
- `worktree_dirty` — worktree 有未提交改动
- `empty_repository` — 仓库无任何 commit
- `hook_rejected` — pre-commit hook 失败(由 git_commit classifier 提供)
- `identity_not_set` — `user.email` / `user.name` 未配置
- `revert_conflict` — revert 与当前改动冲突
- `nothing_to_revert` — 该 commit 没有可反向应用的修改
- `git_error` — 其他 git revert 失败

**env passthrough**: 通过 `GIT_AUTHOR_*` / `GIT_COMMITTER_*` 环境变量透传作者身份
(测试或集成方可注入测试 author,不污染全局 git config)。

---

## 12 个新 ReasonCode (v2.17.0)

| 类别 | 码 | 含义 |
|------|----|------|
| **git-init** | `path_not_directory` | path 不存在或不是目录 |
| | `directory_not_empty` | 目录非空(且 `force=false`);v2.17.1: `force=true` 可豁免 |
| | `already_a_git_repo` | .git 已存在 |
| | `init_failed` | git init 子进程失败 |
| **branch** | `branch_exists` | 创建分支时分支已存在 |
| | `branch_not_found` | 切换/删除时分支不存在 |
| | `branch_is_current` | **硬禁**删当前分支 |
| | `branch_not_merged` | -d 模式但分支未合并 |
| | `worktree_dirty` | worktree 有未提交改动 |
| **revert** | `commit_not_found` | ref 不解析为 commit |
| | `revert_conflict` | revert 与当前改动冲突 |
| | `nothing_to_revert` | commit 无可反向应用修改 |

完整 ReasonCode 见 `tools/webapi/_helpers.py`(`class ReasonCode`)。

---

## 错误处理最佳实践 (Dashboard 集成)

| reason 类别 | UI 建议 |
|-------------|---------|
| `branch_is_current` / `worktree_dirty` / `branch_not_merged` | 显示操作约束(切换分支 / commit / merge) |
| `branch_not_found` / `commit_not_found` | 显示 ref 是否存在 |
| `revert_conflict` | 引导用户打开 IDE 解决冲突 |
| `hook_rejected` | 显示 stderr 提示用户 hook 失败原因 |
| `identity_not_set` | 引导用户运行 `git config user.email/name` |
| `path_not_directory` / `directory_not_empty` / `already_a_git_repo` | 高亮用户输入的 path |
| `init_failed` / `git_error` | 显示 stderr,引导用户检查 git 安装 |

---

## dashboard 集成示例 (Vue 3 Composition API)

```typescript
// 在 UI 上 "新建分支" 按钮:
async function createBranch(name: string, startPoint?: string) {
  const resp = await fetch(`/spcode/git-branch-create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, start_point: startPoint }),
  });
  const envelope = await resp.json();
  if (envelope.success) {
    showToast(`✅ 创建分支 ${name}`);
  } else if (envelope.reason === 'branch_exists') {
    showError('该分支已存在,使用 force 选项覆盖?');
  } else if (envelope.reason === 'invalid_branch') {
    showError('分支名非法,仅允许字母数字 / . / - / _');
  }
}

// 切换分支
async function switchBranch(name: string, force = false) {
  // 跨字段组合由后端校验,此处直接按 spec 调用
}

// 取消某 commit
async function revertCommit(sha: string) {
  const resp = await fetch(`/spcode/git-revert`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: sha, no_edit: true }),
  });
  const envelope = await resp.json();
  if (envelope.reason === 'revert_conflict') {
    // 引导用户到 IDE 解决冲突
    openIDESolver(envelope.stderr);
  }
}
```

---

## 常见问题

**Q: 为什么 git-init 不要求已加载项目?**
A: git-init 的语义是 *新建* 仓库,而 spcode 项目加载语义是 *已存在* 仓库。当前设计避免循环约束。

**Q: branch-delete 的 force=true 是否能删 current branch?**
A: **不能**。`branch_is_current` 是硬禁,bypass-locked,`force=true` 仅影响 `-d` vs `-D`(merged check)。

**Q: git-branch-switch 的 create=true + detach=true 会发生什么?**
A: 报 `invalid_body`,互斥组合不被允许。

**Q: git-revert 是否强制无 editor?**
A: 是。`no_edit=false` 会被拒绝(`invalid_param`)。headless 服务不开编辑器。

---

## 版本历史

- **2026-07-15: v2.17.0** — 初始版本,6 端点(PR-B ~ PR-G)
