# Git Pull / Push / Remote Set-URL Web API

> Author: elecvoid243
> Created: 2026-08-12 13:45 CST
> Backend spec: `docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md`

本文档供 AstrBot Dashboard / WebUI 消费 3 个 Git 远端同步端点：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/spcode/git-pull` | POST | 从 upstream 或显式 remote/branch 拉取更新 |
| `/spcode/git-push` | POST | 推送分支；未配置 upstream 时自动 `push -u` |
| `/spcode/git-remote-set-url` | POST | upsert remote URL：存在则修改，不存在则添加 |

---

## 1. 通用约定

### 1.1 请求参数

3 个端点都支持以下标准字段：

| 字段 | 类型 | 必传 | 说明 |
|------|------|------|------|
| `umo` | `string` | 否 | 会话 ID；缺省时使用最近加载的项目 |
| `worktree` | `string` | 否 | 可选 worktree 路径；走现有 6 步安全防御链 |

### 1.2 响应 envelope

成功：

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "reason": null,
    "stderr": "",
    "elapsed_ms": 42
  }
}
```

失败：

```json
{
  "status": "ok",
  "data": {
    "success": false,
    "reason": "network_error",
    "stderr": "fatal: unable to connect ...",
    "elapsed_ms": 42
  }
}
```

前端应优先读取 `data.success` 与 `data.reason`，不要只依赖 HTTP 状态码。

### 1.3 网络与认证约束

- 后端禁用交互式凭据提示，不会在 API 请求中等待账号、密码或 SSH passphrase。
- 网络操作超时时间为 60 秒。
- 认证或权限失败返回 `auth_required`。
- DNS、连接失败或超时返回 `network_error`。
- 后端不会保存 token、password 或 SSH key。
- URL 不会以原文写入 INFO 日志；响应仍返回调用方提交的 URL。

---

## 2. `POST /spcode/git-pull`

从当前 upstream 或显式 remote/branch 拉取并集成更新。

### 2.1 Request body

```json
{
  "remote": "origin",
  "branch": "main",
  "ff_only": false,
  "rebase": false,
  "umo": "webchat:FriendMessage:...",
  "worktree": "F:/workspace/project"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `remote` | `string` | 否 | 当前 upstream remote | 显式指定时 remote 必须已存在 |
| `branch` | `string` | 否 | 当前 upstream branch；显式 remote 时为当前分支 | 必须是合法 Git ref |
| `ff_only` | `boolean` | 否 | `false` | 使用 `git pull --ff-only` |
| `rebase` | `boolean` | 否 | `false` | 使用 `git pull --rebase` |
| `umo` | `string` | 否 | 最近加载项目 | 标准参数 |
| `worktree` | `string` | 否 | primary worktree | 标准参数 |

约束：

- `ff_only` 与 `rebase` 互斥。
- 未传 `remote` 时使用当前 upstream；没有 upstream 返回 `no_upstream`。
- 工作区有任何未提交改动时返回 `worktree_dirty`。
- 已有 merge/cherry-pick/revert/rebase 操作时返回 `operation_in_progress`。

### 2.2 成功：有更新

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "pulled": true,
    "updated": true,
    "mode": "merge",
    "remote": "origin",
    "branch": "main",
    "before_sha": "1111111111111111111111111111111111111111",
    "after_sha": "2222222222222222222222222222222222222222",
    "fast_forward": true,
    "files_touched": ["src/app.py"],
    "upstream": "origin/main",
    "directory": "F:/workspace/project",
    "umo": "webchat:FriendMessage:...",
    "worktree": "F:/workspace/project",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 230
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `pulled` | `boolean` | 是否产生了本地 HEAD 更新 |
| `updated` | `boolean` | 与 `pulled` 相同；保留给前端表达“是否更新” |
| `mode` | `"merge" \| "ff_only" \| "rebase"` | 本次 pull 集成模式 |
| `before_sha` / `after_sha` | `string` | pull 前后 HEAD SHA |
| `fast_forward` | `boolean` | Git 输出中是否识别到 fast-forward |
| `files_touched` | `string[]` | `before_sha..after_sha` 涉及文件 |
| `upstream` | `string \| null` | 操作后 upstream，例如 `origin/main` |

### 2.3 成功：已经是最新

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "pulled": false,
    "updated": false,
    "mode": "merge",
    "remote": "origin",
    "branch": "main",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 180
  }
}
```

前端建议显示“已是最新”，不要显示为失败。

### 2.4 冲突响应

merge 冲突：

```json
{
  "status": "ok",
  "data": {
    "success": false,
    "reason": "merge_conflict",
    "operation": "merge",
    "conflicted_files": [
      {"path": "src/app.py", "status": "UU"}
    ],
    "pulled": false,
    "updated": false,
    "stderr": "Auto-merging src/app.py\nCONFLICT ...",
    "elapsed_ms": 260
  }
}
```

rebase 冲突：

```json
{
  "status": "ok",
  "data": {
    "success": false,
    "reason": "rebase_conflict",
    "operation": "rebase",
    "conflicted_files": [
      {"path": "src/app.py", "status": "UU"}
    ],
    "pulled": false,
    "updated": false,
    "stderr": "error: could not apply ...",
    "elapsed_ms": 260
  }
}
```

冲突后可继续使用现有端点：

1. `GET /spcode/git-conflict-status`
2. `POST /spcode/git-conflict-resolve`
3. `POST /spcode/git-conflict-continue` 或 `POST /spcode/git-conflict-abort`

### 2.5 fetch 示例

```ts
async function pullProject(options: {
  umo?: string;
  worktree?: string;
  ffOnly?: boolean;
  rebase?: boolean;
}) {
  const response = await fetch("/spcode/git-pull", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      umo: options.umo,
      worktree: options.worktree,
      ff_only: options.ffOnly ?? false,
      rebase: options.rebase ?? false,
    }),
  });
  const envelope = await response.json();
  return envelope.data;
}
```

---

## 3. `POST /spcode/git-push`

推送当前分支或显式分支。当前分支没有 upstream 时自动设置 upstream。

### 3.1 Request body

```json
{
  "remote": "origin",
  "branch": "main",
  "umo": "webchat:FriendMessage:...",
  "worktree": "F:/workspace/project"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `remote` | `string` | 否 | 当前 upstream remote；否则 `origin` | remote 必须已存在 |
| `branch` | `string` | 否 | 当前本地分支 | detached HEAD 返回 `detached_head` |
| `umo` | `string` | 否 | 最近加载项目 | 标准参数 |
| `worktree` | `string` | 否 | primary worktree | 标准参数 |

行为：

- 已有 upstream 且未显式指定 remote/branch：执行普通 `git push`。
- 显式指定 remote/branch：执行 `git push <remote> <branch>`，不改变既有 upstream。
- 没有 upstream：执行 `git push -u <remote> <branch>`。
- 不支持 force push，不推送 tags。

### 3.2 成功：首次推送并设置 upstream

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "pushed": true,
    "set_upstream": true,
    "remote": "origin",
    "branch": "main",
    "remote_branch": "origin/main",
    "local_sha": "2222222222222222222222222222222222222222",
    "upstream": "origin/main",
    "directory": "F:/workspace/project",
    "umo": "webchat:FriendMessage:...",
    "worktree": "F:/workspace/project",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 320
  }
}
```

### 3.3 成功：远端已同步

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "pushed": false,
    "set_upstream": false,
    "remote": "origin",
    "branch": "main",
    "remote_branch": "origin/main",
    "upstream": "origin/main",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 160
  }
}
```

### 3.4 失败：远端包含本地没有的提交

```json
{
  "status": "ok",
  "data": {
    "success": false,
    "reason": "non_fast_forward",
    "pushed": false,
    "remote": "origin",
    "branch": "main",
    "stderr": "! [rejected] main -> main (fetch first)",
    "elapsed_ms": 210
  }
}
```

前端建议提示用户先执行 `git-pull`，不要提供 force push 按钮。

### 3.5 fetch 示例

```ts
async function pushProject(options: { umo?: string; worktree?: string }) {
  const response = await fetch("/spcode/git-push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      umo: options.umo,
      worktree: options.worktree,
    }),
  });
  const envelope = await response.json();
  return envelope.data;
}
```

---

## 4. `POST /spcode/git-remote-set-url`

配置 remote URL。remote 存在则修改，不存在则添加。

### 4.1 Request body

```json
{
  "remote": "origin",
  "url": "https://example.com/org/repo.git",
  "umo": "webchat:FriendMessage:...",
  "worktree": "F:/workspace/project"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `url` | `string` | 是 | — | 支持 HTTPS、SSH、Git 协议、本地路径 |
| `remote` | `string` | 否 | `"origin"` | remote 名称 |
| `umo` | `string` | 否 | 最近加载项目 | 标准参数 |
| `worktree` | `string` | 否 | primary worktree | 标准参数 |

URL 校验：

- 非空，最长 2048 字符。
- 禁止换行、NUL 与其他控制字符。
- 不限制 host 或协议。

### 4.2 成功响应

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "configured": true,
    "action": "updated",
    "remote": "origin",
    "url": "https://example.com/org/repo.git",
    "directory": "F:/workspace/project",
    "umo": "webchat:FriendMessage:...",
    "worktree": "F:/workspace/project",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 45
  }
}
```

`action` 取值：

| 值 | 说明 |
|----|------|
| `added` | remote 不存在，已执行 `git remote add` |
| `updated` | remote 已存在且 URL 不同，已执行 `git remote set-url` |
| `unchanged` | remote 已存在且 URL 相同，未执行写命令 |

### 4.3 fetch 示例

```ts
async function setRemoteUrl(options: {
  url: string;
  remote?: string;
  umo?: string;
  worktree?: string;
}) {
  const response = await fetch("/spcode/git-remote-set-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      remote: options.remote ?? "origin",
      url: options.url,
      umo: options.umo,
      worktree: options.worktree,
    }),
  });
  const envelope = await response.json();
  return envelope.data;
}
```

---

## 5. TypeScript 类型

```ts
type SpcodeEnvelope<T> = {
  status: "ok";
  data: T & {
    success: boolean;
    reason: string | null;
    stderr: string;
    elapsed_ms: number;
  };
};

type GitPullData = {
  pulled: boolean;
  updated: boolean;
  mode: "merge" | "ff_only" | "rebase";
  remote?: string;
  branch?: string;
  before_sha?: string;
  after_sha?: string;
  fast_forward?: boolean;
  files_touched?: string[];
  upstream?: string | null;
  operation?: "merge" | "cherry_pick" | "revert" | "rebase" | null;
  conflicted_files?: Array<{ path: string; status: string }>;
};

type GitPushData = {
  pushed: boolean;
  set_upstream: boolean;
  remote?: string;
  branch?: string;
  remote_branch?: string;
  local_sha?: string;
  upstream?: string | null;
};

type GitRemoteSetUrlData = {
  configured: boolean;
  action?: "added" | "updated" | "unchanged";
  remote: string;
  url?: string;
};
```

---

## 6. ReasonCode 前端处理

| ReasonCode | 建议 UI 文案 / 动作 |
|------------|-------------------|
| `invalid_body` | 请求参数格式错误 |
| `invalid_remote` | remote 名称不合法 |
| `invalid_url` | remote URL 不合法 |
| `invalid_branch` | 分支名不合法 |
| `remote_not_found` | remote 不存在；引导先设置 remote URL |
| `no_upstream` | 当前分支未设置 upstream；可显式 remote/branch 或先 push |
| `detached_head` | 当前处于 detached HEAD；引导先切换分支 |
| `worktree_dirty` | 工作区有未提交改动；引导先提交或清理 |
| `operation_in_progress` | 已有冲突操作；跳转冲突处理页 |
| `merge_conflict` | pull merge 冲突；跳转冲突处理页 |
| `rebase_conflict` | pull rebase 冲突；跳转冲突处理页 |
| `non_fast_forward` | 远端与本地分叉；提示先 pull |
| `push_rejected` | 远端拒绝推送；展示 stderr |
| `auth_required` | 需要认证或权限不足；提示到系统凭据管理器配置 |
| `network_error` | 网络连接或超时失败；允许重试 |
| `git_error` | 未分类 Git 错误；展示 stderr |

通用 preflight ReasonCode（如 `feature_disabled`、`no_project_loaded`、`worktree_invalid`、`directory_missing`、`not_a_git_repo`、`git_unavailable`）沿用现有 Dashboard 处理方式。

---

## 7. 安全说明

- 3 个端点均复用 `_git_endpoint_preflight` 与 `worktree` 防御链。
- 所有 Git 参数通过 argv 传递，不经过 shell。
- 不支持 `--force` 与 `--force-with-lease`。
- 不支持交互式凭据输入。
- 若 URL 包含用户名或 token，后端日志只记录脱敏形式；前端仍应避免把含凭据 URL 持久化到浏览器日志或分析系统。
