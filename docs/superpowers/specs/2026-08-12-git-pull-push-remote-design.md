# Git Pull / Push / Remote Set-URL Endpoints 设计

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后，下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已分段通过（用户确认日期 2026-08-12，3 段全部确认：§2 架构与请求契约 → §3 执行流程与错误模型 → §4 文件拆分与测试策略）
**作者**: elecvoid243 @ 2026-08-12 12:51 CST
**目标插件 / 仓库**: `astrbot_plugin_spcode_toolkit`
**前置版本**: spcode plugin v2.23.2
**目标版本**: 下一个 minor 版本（建议 v2.24.0；本任务不修改 `metadata.yaml`）
**前端消费者**: AstrBot Dashboard / WebUI

---

## 用户决策记录（4 轮澄清）

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: 设置远端地址语义** | **B** — remote 存在则 `set-url`，不存在则 `add` | `/spcode/git-remote-set-url` 是 upsert 端点，适配新仓库首次配置场景 |
| **Q2: pull 能力范围** | **B** — 完整 pull，可选 `ff_only` / `rebase` | 需要处理 merge 与 rebase 两类冲突状态 |
| **Q3: push 能力范围** | **C** — 普通 push + 自动设置 upstream | 不暴露 force；未配置 upstream 时自动执行 `git push -u` |
| **Q4: 端点架构** | **A** — 3 个原子 POST 端点，一文件一端点 | 与现有 `git-merge` / `git-revert` / `git-branch-*` 风格一致 |

---

## §1 背景与动机

### 1.1 当前状态

spcode 工具箱当前 Web API 已覆盖：

- 本地工作区状态：`git-status` / `git-diff` / `git-log` / `git-show` / `git-stats`
- 本地提交工作流：`git-stage` / `git-unstage` / `git-commit` / `git-revert` / `git-squash`
- 分支管理：`git-branches` / `git-branch-create` / `git-branch-delete` / `git-branch-switch`
- 合并与冲突：`git-merge` / `git-cherry-pick` / `git-conflict-*`
- 仓库初始化与探测：`git-init` / `git-repo-check`
- worktree 管理：`git-worktree-add/remove/lock/unlock`

但 Dashboard 无法完成远端同步闭环：

1. 新初始化的仓库无法配置 remote URL。
2. 已配置 remote 的仓库无法从 Dashboard 触发 `git pull`。
3. 本地提交无法从 Dashboard 触发 `git push` 或首次 `git push -u`。

### 1.2 目标

新增 3 个 POST 端点：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/spcode/git-pull` | POST | 从 remote/upstream 拉取并集成更新，可选 `--ff-only` / `--rebase` |
| `/spcode/git-push` | POST | 推送当前或指定分支；无 upstream 时自动设置 upstream |
| `/spcode/git-remote-set-url` | POST | upsert remote URL：存在则修改，不存在则添加 |

### 1.3 范围与非目标

**范围内**：

- 3 个 endpoint + 1 个内部共享模块
- pull / push / remote URL 的 body 校验、preflight、执行、错误分类与测试
- conflict lifecycle 对 rebase 的探测、continue、abort 支持
- Dashboard API 消费文档

**范围外**：

- 真实远端账号、token、SSH key 管理
- force push（含 `--force` 与 `--force-with-lease`）
- tag push、delete remote branch、fetch/prune 独立端点
- pull `--autostash`
- 交互式凭据输入
- 前端 Dashboard UI 实现
- 修改当前已有未提交改动的 `README.md` / `AGENTS.md` / `metadata.yaml`

---

## §2 架构与请求契约

### 2.1 端点架构

采用方案 A：**3 个原子 POST 端点，一文件一端点**。

新增文件：

| 文件 | 职责 |
|------|------|
| `tools/webapi/git_pull.py` | `/spcode/git-pull` handler |
| `tools/webapi/git_push.py` | `/spcode/git-push` handler |
| `tools/webapi/git_remote_set_url.py` | `/spcode/git-remote-set-url` handler |
| `tools/webapi/_git_remote.py` | remote / upstream / URL / network error 共享内部逻辑 |

修改文件：

| 文件 | 职责 |
|------|------|
| `tools/webapi/__init__.py` | import、ROUTES、HANDLERS、`__all__` 注册 |
| `tools/webapi/_helpers.py` | 新增 ReasonCode；`_detect_conflict_operation` 支持 rebase |
| `tools/webapi/git_conflict_continue.py` | 支持 `git rebase --continue` |
| `tools/webapi/git_conflict_abort.py` | 支持 `git rebase --abort` |

### 2.2 共享约束

所有 3 个端点：

1. body 必须是 JSON object；否则 `invalid_body`。
2. 复用 `_git_endpoint_preflight` 5 步防御链。
3. 支持标准 `umo` / `worktree` 参数。
4. 复用 `_make_envelope` / `_JSONResponseCompat` / `_run_git_async`。
5. 网络相关命令统一 60 秒超时。
6. 禁止交互式认证提示：
   - `GIT_TERMINAL_PROMPT=0`
   - `GIT_SSH_COMMAND=ssh -o BatchMode=yes`
   - 环境变量基于 `os.environ.copy()` 合并，避免丢失 `PATH` 与系统配置。
7. URL 不以原文写入 INFO 日志；日志只写 masked URL。
8. 所有 git 子进程禁用颜色：`-c color.ui=never`。

### 2.3 `POST /spcode/git-pull`

请求体：

```json
{
  "remote": "origin",
  "branch": "main",
  "ff_only": false,
  "rebase": false,
  "umo": "webchat:...",
  "worktree": "F:/repo-or-worktree"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `remote` | str | 否 | 当前 upstream 对应 remote | 显式指定时 remote 必须已存在 |
| `branch` | str | 否 | 当前 upstream 对应 branch；显式 remote 时为当前分支 | 必须是合法 ref name |
| `ff_only` | bool | 否 | `false` | 添加 `--ff-only` |
| `rebase` | bool | 否 | `false` | 添加 `--rebase` |
| `umo` | str | 否 | 最近加载项目 | 标准 preflight 参数 |
| `worktree` | str | 否 | primary worktree | 走 6 步防御链 |

跨字段约束：

- `ff_only=true` 且 `rebase=true` → `invalid_body`。
- 未传 `remote` 时使用当前 upstream；无 upstream → `no_upstream`。
- 传 `remote` 但未传 `branch` 时，branch 默认当前本地分支；detached HEAD → `detached_head`。

### 2.4 `POST /spcode/git-push`

请求体：

```json
{
  "remote": "origin",
  "branch": "main",
  "umo": "webchat:...",
  "worktree": "F:/repo-or-worktree"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `remote` | str | 否 | 当前 upstream remote；否则 `origin` | remote 必须已存在 |
| `branch` | str | 否 | 当前本地分支 | detached HEAD → `detached_head` |
| `umo` | str | 否 | 最近加载项目 | 标准 preflight 参数 |
| `worktree` | str | 否 | primary worktree | 走 6 步防御链 |

行为：

- 当前分支已有 upstream 且未显式指定 remote/branch：执行普通 `git push`。
- 当前分支已有 upstream 但显式指定 remote/branch：执行 `git push <remote> <branch>`，不改变既有 upstream。
- 当前分支无 upstream：执行 `git push -u <remote> <branch>`，成功后 upstream 自动建立。
- 不推送 tags，不支持 force。

### 2.5 `POST /spcode/git-remote-set-url`

请求体：

```json
{
  "remote": "origin",
  "url": "https://example.com/org/repo.git",
  "umo": "webchat:...",
  "worktree": "F:/repo-or-worktree"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `url` | str | 是 | — | 目标 remote URL；支持 HTTPS / SSH / Git 协议 / 本地路径 |
| `remote` | str | 否 | `"origin"` | remote 名称 |
| `umo` | str | 否 | 最近加载项目 | 标准 preflight 参数 |
| `worktree` | str | 否 | primary worktree | 走 6 步防御链 |

行为：

- remote 不存在 → `git remote add <remote> <url>`，`action="added"`。
- remote 已存在且 URL 不同 → `git remote set-url <remote> <url>`，`action="updated"`。
- remote 已存在且 URL 相同 → 不执行写命令，`action="unchanged"`。

---

## §3 执行流程、响应与错误模型

### 3.1 `git-pull` 流程

```text
POST /spcode/git-pull
  ├─ body 类型 / bool 类型 / 互斥校验
  ├─ remote / branch 格式校验
  ├─ _git_endpoint_preflight
  ├─ 冲突状态检查（merge / cherry-pick / revert / rebase）
  ├─ worktree 脏检查（任何 porcelain 输出均拒绝）
  ├─ upstream / remote / branch 解析
  ├─ 记录 before_sha
  ├─ git pull --no-edit [--ff-only | --rebase] [remote] [branch]
  ├─ 成功：记录 after_sha、upstream、files_touched
  └─ 失败：分类为 merge_conflict / rebase_conflict / non_fast_forward /
           auth_required / network_error / remote_not_found / git_error
```

成功响应示例（有更新）：

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
    "before_sha": "1111111...",
    "after_sha": "2222222...",
    "fast_forward": true,
    "files_touched": ["src/a.py"],
    "upstream": "origin/main",
    "directory": "F:/repo",
    "umo": "webchat:...",
    "worktree": "F:/repo",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 230
  }
}
```

成功响应示例（已最新）：

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

冲突响应：

- merge 冲突：`success=false`、`reason="merge_conflict"`、`operation="merge"`、`conflicted_files=[...]`。
- rebase 冲突：`success=false`、`reason="rebase_conflict"`、`operation="rebase"`、`conflicted_files=[...]`。

### 3.2 `git-push` 流程

```text
POST /spcode/git-push
  ├─ body / remote / branch 校验
  ├─ _git_endpoint_preflight
  ├─ 冲突状态检查
  ├─ detached HEAD 检查
  ├─ upstream / remote / branch 解析
  ├─ git remote get-url <remote> 确认 remote 存在
  ├─ 已有 upstream：git push [remote branch]
  ├─ 无 upstream：git push -u <remote> <branch>
  └─ 成功：回读 HEAD SHA 与 upstream
```

成功响应示例：

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
    "local_sha": "2222222...",
    "upstream": "origin/main",
    "directory": "F:/repo",
    "umo": "webchat:...",
    "worktree": "F:/repo",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 320
  }
}
```

“Everything up-to-date” 也视为成功：`success=true`、`pushed=false`、`reason=null`。

### 3.3 `git-remote-set-url` 流程

```text
POST /spcode/git-remote-set-url
  ├─ body / remote / URL 校验
  ├─ _git_endpoint_preflight
  ├─ git remote get-url <remote>
  │   ├─ 不存在 → git remote add
  │   ├─ URL 相同 → unchanged
  │   └─ URL 不同 → git remote set-url
  └─ 成功：git remote get-url <remote> 回读确认
```

成功响应示例：

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "configured": true,
    "action": "updated",
    "remote": "origin",
    "url": "https://example.com/org/repo.git",
    "directory": "F:/repo",
    "umo": "webchat:...",
    "worktree": "F:/repo",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 45
  }
}
```

### 3.4 remote / URL 校验

remote 名称：

- 必须是非空字符串，长度 ≤ 128。
- 禁止控制字符、空白字符、以 `-` 开头、包含 `..`。
- 推荐字符集：字母、数字、`.`、`_`、`-`、`/`。

URL：

- 必须是非空字符串，长度 ≤ 2048。
- 禁止 `\r`、`\n`、NUL 与其他控制字符。
- 不限制 host 或协议，以支持 HTTPS、SSH、Git 协议与本地路径 remote。
- 响应可返回调用方提交的 URL；日志必须脱敏，例如 `https://user:***@example.com/org/repo.git`。

### 3.5 新增 ReasonCode

| 常量 | 字面量 | 使用场景 |
|------|--------|----------|
| `INVALID_REMOTE` | `invalid_remote` | remote 名称为空或格式非法 |
| `INVALID_URL` | `invalid_url` | remote URL 为空、超长或含控制字符 |
| `REMOTE_NOT_FOUND` | `remote_not_found` | 显式 remote 不存在 |
| `NO_UPSTREAM` | `no_upstream` | pull 未显式指定 remote 且当前分支无 upstream |
| `DETACHED_HEAD` | `detached_head` | 需要当前分支名但 HEAD detached |
| `AUTH_REQUIRED` | `auth_required` | remote 要求认证或权限不足 |
| `NETWORK_ERROR` | `network_error` | DNS、连接、网络超时等 |
| `NON_FAST_FORWARD` | `non_fast_forward` | pull `--ff-only` 或 push 被拒绝 |
| `PUSH_REJECTED` | `push_rejected` | 非 non-fast-forward 的远端拒绝 |
| `REBASE_CONFLICT` | `rebase_conflict` | `git pull --rebase` 冲突 |

### 3.6 rebase conflict lifecycle 扩展

现有 `_detect_conflict_operation` 只识别：

- `.git/MERGE_HEAD` → `merge`
- `.git/CHERRY_PICK_HEAD` → `cherry_pick`
- `.git/REVERT_HEAD` → `revert`

本设计增加：

- `.git/rebase-merge/` → `rebase`
- `.git/rebase-apply/` → `rebase`

`git-conflict-continue` 增加：

```bash
git rebase --continue
```

`git-conflict-abort` 增加：

```bash
git rebase --abort
```

这样 `git pull --rebase` 冲突后，Dashboard 能复用现有 conflict 页面完成恢复，不会把仓库留在 API 无法处理的状态。

---

## §4 文件拆分、测试策略与交付范围

### 4.1 测试文件

新增：

| 文件 | 覆盖 |
|------|------|
| `tests/test_git_pull.py` | body 校验、preflight、upstream、dirty、no-op、fast-forward、merge conflict、ff-only diverged、rebase conflict、错误分类 |
| `tests/test_git_push.py` | body 校验、preflight、auto upstream、普通 push、no-op、non-fast-forward、remote 不存在、detached HEAD、错误分类 |
| `tests/test_git_remote_set_url.py` | add / update / unchanged、remote 与 URL 校验、preflight、回读确认 |
| `tests/test_git_remote_helpers.py` | remote / URL 校验、URL 脱敏、网络环境变量、stderr 分类 |

修改：

| 文件 | 覆盖 |
|------|------|
| `tests/test_reason_code.py` | 10 个新增 reason 字面量 |
| `tests/test_webapi_end_to_end.py` | 路由总数 50 → 53，POST 数 31 → 34，新路由注册 smoke |
| conflict lifecycle 相关测试 | rebase detect / continue / abort |

### 4.2 集成测试方式

不访问真实互联网：

1. `git init --bare` 创建本地裸仓库。
2. clone 出两个临时工作仓库。
3. 通过两个 clone 分别制造：
   - 本地落后远端（pull fast-forward）
   - 本地领先远端（push）
   - 双方分叉（ff-only failure / push non-fast-forward）
   - 同文件不同内容（merge / rebase conflict）
4. remote URL 使用本地路径，覆盖完整 git remote 行为。
5. auth / network 错误用 stderr 分类单元测试覆盖，不依赖外部网络。

### 4.3 文档交付

新增：

- `docs/superpowers/specs/2026-08-12-git-pull-push-remote-design.md`
- `docs/superpowers/plans/2026-08-12-git-pull-push-remote.md`
- `docs/api/webapi-git-pull-push-remote-api.md`

修改：

- `docs/webapi_endpoints_report.md`

不修改：

- `README.md`
- `AGENTS.md`
- `metadata.yaml`

原因：上述 3 个文件在工作区已有未提交改动，本任务避免覆盖或混入无关变更；版本号发布可另行处理。

### 4.4 验证门槛

- 每个新行为先写失败测试，再实现最小代码。
- `code_check` / ruff 无错误。
- 新增与受影响测试全部通过。
- 最终运行 `pytest tests/`；若存在与本次无关的既有失败，必须如实报告。
- 只提交本次明确新增/修改的文件。
- 不推送远端，不创建 PR。

---

## §5 安全与兼容性说明

1. **凭据安全**：端点不接受 token / password 字段，不提供交互式凭据输入；认证失败返回 `auth_required`。
2. **命令注入安全**：所有参数以 argv list 传递，不使用 `shell=True`。
3. **路径安全**：`worktree` 仍走 `_validate_worktree_param` 6 步防御链，最终以 `git-common-dir` 匹配兜底。
4. **URL 安全**：URL 只作为 git remote 配置值，不在 shell 中展开；日志脱敏。
5. **行为兼容**：不修改既有 50 条路由的请求/响应契约；仅扩展 conflict lifecycle 的 `operation` 值集合，新增 `rebase`。
6. **no-op 语义**：pull/push 已同步视为成功操作，与 git 返回码一致；前端可用 `updated` / `pushed` 区分是否产生实际变更。

---

## §6 验收标准

- [ ] `POST /spcode/git-remote-set-url` 可在无 remote 仓库添加 `origin`，可在已有 remote 仓库更新 URL，相同 URL 返回 `unchanged`。
- [ ] `POST /spcode/git-push` 可在新仓库首次推送时自动执行 `-u` 并设置 upstream。
- [ ] `POST /spcode/git-push` 对已同步仓库返回 `success=true` + `pushed=false`。
- [ ] `POST /spcode/git-pull` 可拉取远端 fast-forward 更新并返回 before/after SHA 与文件列表。
- [ ] `POST /spcode/git-pull` 对已同步仓库返回 `success=true` + `updated=false`。
- [ ] `ff_only` / `rebase` 互斥校验生效。
- [ ] pull merge 冲突可由现有 `git-conflict-*` API 处理。
- [ ] pull rebase 冲突可由扩展后的 `git-conflict-*` API continue / abort。
- [ ] 网络、认证、non-fast-forward、remote 不存在等失败均返回明确 ReasonCode。
- [ ] 路由表、handler 别名、ReasonCode、Dashboard API 文档同步更新。
