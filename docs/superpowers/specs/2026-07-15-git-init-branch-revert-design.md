# Git Init / Branch / Revert Endpoints 设计

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后,下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已通过(用户确认日期 2026-07-15,3 段全部确认:§2 架构总览 → §3 各端点详细规格 → §6 测试策略)
**作者**: elecvoid243 @ 2026-07-15 23:30
**目标插件 / 仓库**:
- `astrbot_plugin_spcode_toolkit`(后端,本 spec 范围)
- `Astrbot/dashboard`(前端,前端实现在 v3.10+ dashboard 迭代跟进)

**前置版本**: spcode plugin v3.8.x(git-show / git-worktree-management 之后);dashboard 当前 HEAD。
**目标版本**: **v2.17.0**(6 个新 endpoint,纯 additive)

---

## 用户决策记录(3 轮澄清)

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: git-init 目录语义** | **A** — 在已存在目录上 init(LLM/调用方负责 mkdir) | 复用路径安全防御,init 端点必须**豁免** `_git_endpoint_preflight` |
| **Q2: 分支 endpoint 粒度** | **B** — 3 个独立端点(create/delete/switch) + 1 个 list | 与 `git-worktree-*` 4 端点 1:1 对齐 |
| **Q3: git-revert 能力边界** | **A** — 裸 revert + 自动 commit | 复用 git-commit 的 4 类 stderr 分类,新增 4 个 revert 专属 reason |

---

## §1 背景与动机

### 1.1 当前状态(v3.8.x)

git 相关 endpoint 共 **13 个**(10 写 + 3 读相关的扩展 worktree),覆盖了:

- **只读**:`git-diff` / `git-status` / `git-log` / `git-show` / `git-worktrees` / `git-file` / `codegraph-status` / `file-browser`(部分)
- **写 - 文件**:`git-stage` / `git-unstage` / `git-commit` / `file-restore` / `file-discard-hunk`
- **写 - worktree**:`git-worktree-add/remove/lock/unlock`
- **写 - 文档**:`docs` POST/PATCH/DELETE

**缺失 3 类核心 git 操作**:
1. **分支控制**:`git branch <name>` / `git branch -d` / `git switch` 在 dashboard 全部无对应后端
2. **仓库初始化**:从 0 创建新 git 仓库的能力缺失
3. **回滚提交**:`git revert <ref>` 在 dashboard 全部无对应后端

前端 Dashboard 上"新建分支 / 切换分支 / 删分支 / 新建仓库 / 回滚 commit"按钮无对应后端支持,只能引导用户去终端手动跑 git 命令,UX 割裂。

### 1.2 目标(v2.17.0)

新增 **6 个端点**(5 写 + 1 读),覆盖 git 分支与回滚的全部核心场景(95% 实际使用):

- **仓库生命周期**:`git-init` (从 0 创建)
- **分支控制**:`git-branches` (列) / `git-branch-create` / `git-branch-delete` / `git-branch-switch`
- **回滚**:`git-revert`

**显式排除**(留 v3.10+ 迭代):
- merge / rebase / cherry-pick(高风险,需专门 spec)
- `--no-commit` revert 模式(等价于手动 `git revert && git commit`,与 git-commit 端点链路重复)
- merge commit revert (`-m mainline`,高级用法)
- remote branch delete(留待 `git-branch-delete-remote` 单独端点)
- push / pull / fetch(与远程交互,安全模型不同,留网络层 spec)
- stash(语义独立,留 v3.11+ 迭代)

### 1.3 范围与非目标

**范围内**:
- 6 个 endpoint + 共享 helpers + ReasonCode 扩展
- `_git_init_preflight`(新)替代标准 5 步 preflight
- 后端完整实现 + 单元/E2E 测试(~102 cases)

**范围外**:
- 前端 Dashboard UI 改动(在 dashboard 仓库独立 spec)
- 修改 `_loaded_projects` 状态(init 端点不自动 load,需 LLM 后续 `project.load` 动作)
- 远程仓库交互(push/pull/fetch/clone)
- 高风险操作(merge/rebase/cherry-pick)

---

## §2 架构总览

### 2.1 端点清单(总数 24 → 30)

| 端点 | 方法 | 用途 | 走 preflight? | 走 worktree? |
|------|------|------|-------------|-------------|
| `/spcode/git-init` | POST | 在已存在目录上 `git init` | **NO** (init-only) | **NO** |
| `/spcode/git-branches` | GET | 列出 local/remote 分支 + 当前 + 默认 | YES (5 步) | optional |
| `/spcode/git-branch-create` | POST | `git branch <name>` (从当前 HEAD 拉新分支) | YES (5 步) | optional |
| `/spcode/git-branch-delete` | POST | `git branch -d/-D <name>` | YES (5 步) | optional |
| `/spcode/git-branch-switch` | POST | `git switch <name>` (创建 + 切换一体) | YES (5 步) | optional |
| `/spcode/git-revert` | POST | `git revert <ref> --no-edit` | YES (5 步) | optional |

路径命名遵循现有模式:
- 复数 + 列表 = 读端点(`git-worktrees` / `git-branches`)
- 单数 + 操作动词 = 写端点(`git-worktree-add` / `git-branch-create`)

### 2.2 为什么 `git-init` 必须豁免 `_git_endpoint_preflight`

`_git_endpoint_preflight` 的第 5 步会跑 `git rev-parse --is-inside-work-tree`,对**非仓库**必然返回 `not_a_git_repo` 兜底,导致 init 端点无法工作。

`git-init` 改走新的 `_git_init_preflight` (4 步,无 umo 解析、无 git repo 探测):

1. **L1** body 类型 → `invalid_body`
2. **L2** `path` 必传 + str → `invalid_param`
3. **L3** `path` 路径安全(`_is_path_safe` 复用 `tools/_path_safety.py`) → `path_unsafe`
4. **L4** `Path(path).is_dir()` → `path_not_directory`
5. **L5** `Path(path).exists() && not_empty` → `directory_not_empty`
6. **L6** `Path(path)/".git".exists()` → `already_a_git_repo`
7. **L7** 跑 `git init -b <branch> <path>`,失败 → `init_failed` + stderr 截断

### 2.3 文件变更清单

| 文件 | 类型 | 行数预估 | 职责 |
|------|------|---------|------|
| `tools/webapi/git_init.py` | 新 | ~150 | INIT handler + 4 步 init-only preflight + git init 调用 |
| `tools/webapi/git_branches.py` | 新 | ~180 | GET 列表 + ETag 缓存 + for-each-ref 解析 |
| `tools/webapi/git_branch_create.py` | 新 | ~130 | CREATE handler + ref-format 校验 + git branch |
| `tools/webapi/git_branch_delete.py` | 新 | ~150 | DELETE handler + current 分支硬禁 + stderr 分类 |
| `tools/webapi/git_branch_switch.py` | 新 | ~180 | SWITCH handler + create/detach/force 跨字段校验 + worktree_dirty 探测 |
| `tools/webapi/git_revert.py` | 新 | ~170 | REVERT handler + 复用 git_commit._build_git_env + 扩展 _classify_commit_error |
| `tools/webapi/_helpers.py` | 改 | +80 | 新增 `_git_init_preflight` / `_classify_switch_stderr` / `_classify_revert_stderr`;`ReasonCode` 加 12 个新码字面量 |
| `tools/_helpers.py` | 改 | +30 | 新增 `_is_commit_ref`(给 revert 用)|
| `tools/webapi/__init__.py` | 改 | +36 | ROUTES +6 / HANDLERS +6 / `__all__` +6 |
| `tests/conftest.py` | 改 | +60 | 新增 `empty_dir` / `existing_repo` / `dirty_repo` / `loaded_umo` fixtures |
| `tests/test_git_init.py` | 新 | ~280 | INIT 单元测试(~18 cases) |
| `tests/test_git_branches.py` | 新 | ~250 | GET 单元测试(~14 cases) |
| `tests/test_git_branch_create.py` | 新 | ~220 | CREATE 单元测试(~12 cases) |
| `tests/test_git_branch_delete.py` | 新 | ~280 | DELETE 单元测试(~16 cases) |
| `tests/test_git_branch_switch.py` | 新 | ~400 | SWITCH 单元测试(~22 cases) |
| `tests/test_git_revert.py` | 新 | ~350 | REVERT 单元测试(~20 cases) |
| `tests/test_git_branches_lifecycle.py` | 新 | ~150 | E2E 生命周期(从 init 到 revert 全链路) |
| `tests/test_webapi_end_to_end.py` | 改 | +25 | 路由计数 24 → 30,新增 6 路由注册验证 + 6 端点 smoke |
| `tests/test_reason_code.py` | 改 | +12 | 12 个新 reason 字面量 + 兜底 |
| `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md` | 新 | — | 本 spec |
| `docs/webapi-git-init-branch-revert-api.md` | 新 | ~350 | 公开 API 文档(供 dashboard 消费) |
| `README.md` | 改 | +6 | endpoint 表格 +6 行 |
| `AGENTS.md` | 改 | +8 | endpoint 表格 +1 行 + §3 增量说明 |

### 2.4 共享基础设施复用

| 组件 | 路径 | 复用方式 |
|------|------|---------|
| `_git_endpoint_preflight` | `tools/webapi/_helpers.py` | 5 步前置 — 5 个端点都调用(init 除外) |
| `_make_envelope` | 同上 | 统一信封 — 6 端点共用 |
| `ReasonCode` | 同上 | 失败码(扩展 +12,见 §4) |
| `_JSONResponseCompat` | 同上 | 兼容 framework + 测试 |
| `_run_git_async` | 同上 | 异步 git 子进程调用 |
| `_validate_repo_relative_file` | `tools/_helpers.py` | 4 步文件防御 — 写端点共用 |
| `_validate_worktree_param` | 同上 | 6 步 worktree 防御 — `?worktree=` 参数 |
| `_is_valid_ref_name` | 同上 | 分支 ref-format 校验 |
| `_resolve_git_common_dir` | 同上 | 防越权兜底 — 写端点通用 |
| `_build_git_env` | `tools/webapi/git_commit.py` | 透传 `GIT_AUTHOR_*` — revert 复用 |
| `_classify_commit_error` | 同上 | 4 类 hook/identity/nothing/git_error 分类 — revert 复用并扩展 |
| `_compute_git_etag` | `tools/webapi/_helpers.py` | ETag 计算 — git-branches 复用 |

### 2.5 数据流(以 `git-branch-create` 为例)

```
POST /spcode/git-branch-create
         │
         ▼
  _wrap() 适配器(body = {"name": "feature/x", "start_point": "abc1234"})
         │
         ▼
  handle(plugin, *, umo, worktree, body)
         │
         ├─ L1: body 类型校验
         │      └─ 非 dict → invalid_body
         │
         ├─ L2: name 必传 + str
         │      └─ 失败 → invalid_param
         │
         ├─ L3: _is_valid_ref_name(name)
         │      └─ 失败 → invalid_branch
         │
         ├─ L4: _is_valid_ref_name(start_point) 若传
         │      └─ 失败 → invalid_param
         │
         ├─ L5: _git_endpoint_preflight (5 步)
         │      ├─ 失败 → 通用 envelope(7 个 reason 之一)
         │      └─ 成功 → ctx = {directory, umo, worktree}
         │
         ├─ L6: git branch [--force] <name> [<start_point>]
         │      ├─ "already exists" → branch_exists
         │      ├─ "not a valid branch name" → invalid_branch
         │      └─ 其他 → git_error
         │
         └─ L7: 回读 git rev-parse --verify <name>^{commit}
                ├─ 失败 → git_error
                └─ 成功 → envelope(success=True, created=True, sha=...)
```

### 2.6 通用响应契约(6 端点共用)

```json
{
  "status": "ok",
  "data": {
    "...endpoint_specific...": "...",
    "reason": null | "branch_not_found",
    "stderr": "",
    "elapsed_ms": 12
  }
}
```

- `success=True` → `reason=null`
- `success=False` → `reason` 必填,`stderr` 必填(可能为空)
- 所有数据字段为 `data.*` 嵌套,与现有 24 端点完全一致

---

## §3 各端点详细规格

### 3.1 `POST /spcode/git-init`

**用途**:在已存在且为空的目录上 `git init`,可选 `-b <branch>` 指定初始分支名。

**请求体**:
```json
{
  "path": "C:/Users/me/projects/myapp",
  "initial_branch": "main",
  "bare": false,
  "umo": "...",
  "worktree": "..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `path` | str | ✅ | — | 目标目录绝对路径 |
| `initial_branch` | str | ❌ | `"main"` | 初始分支名(git ≥ 2.28 用 `-b`,旧版 fallback 到 `git init && git symbolic-ref`) |
| `bare` | bool | ❌ | `false` | 是否创建 bare 仓库 |
| `umo` | str | ❌ | — | 调用方 umo(本 spec 范围内 init 不自动 load,umo 仅用于审计日志) |

**7 步防御链**:
1. **L1** body 类型 → `invalid_body`
2. **L2** `path` 必传 + str → `invalid_param`
3. **L3** `path` 路径安全(`_is_path_safe` 复用) → `path_unsafe`
4. **L4** `Path(path).is_dir()` → `path_not_directory`
5. **L5** `Path(path).exists() && not_empty` → `directory_not_empty`(允许 `..` / `.DS_Store` 等单文件,要求目录里没源代码文件)
6. **L6** `Path(path)/".git".exists()` → `already_a_git_repo`
7. **L7** 跑 `git init [-b <branch>] [--bare] <path>`,失败 → `init_failed` + stderr 截断 4KB

**成功响应**:
```json
{
  "status": "ok",
  "data": {
    "initialized": true,
    "path": "C:/Users/me/projects/myapp",
    "initial_branch": "main",
    "git_dir": "C:/Users/me/projects/myapp/.git",
    "bare": false,
    "umo": "...",
    "worktree": "",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 87
  }
}
```

**关键点**:
- **完全不走** `_git_endpoint_preflight`
- **不走** 5 步前端的 umo 回退 —— init 端点必须由调用方显式传 `path`
- 跑完后调用方需要走 `project.load` 链路把新仓库加载(下个 spec 范畴,不在本设计)
- `directory_not_empty` 判定:目录里**没有**任何文件(允许 `.` 和 `..`);若有任何文件(包括 `README.md`、`.gitignore` 等)则拒绝 —— 这是 v2.17.0 的**最严格**语义,避免误覆盖用户已有内容;若用户想"在已有 README 的目录 init",需先手动清空或放到子目录

**ReasonCode 新增(4 个)**:`path_not_directory` / `directory_not_empty` / `already_a_git_repo` / `init_failed`

---

### 3.2 `GET /spcode/git-branches`

**用途**:列出所有 local + remote 分支,标记当前 HEAD。

**Query 参数**:
- `umo?` —— 同其他 GET 端点
- `worktree?` —— 同其他 GET 端点
- `include_remote?` —— `true`/`false`,默认 `false`(dashboard 切换下拉只关心 local)

**3 步处理**:
1. 标准 `_git_endpoint_preflight` 5 步
2. `_compute_git_etag` 走 **1.5s TTL** 缓存
3. ETag 命中 → 直接返 304

**关键 git 命令**:
```bash
git for-each-ref \
  --format='%(HEAD)%09%(refname:short)%09%(objectname:short)%09%(upstream:short)%09%(upstream:track)' \
  refs/heads refs/remotes
```

**成功响应**:
```json
{
  "status": "ok",
  "data": {
    "branches": [
      {
        "name": "main",
        "remote": false,
        "current": true,
        "sha": "abc1234",
        "upstream": "origin/main",
        "tracking": ""
      },
      {
        "name": "feature/x",
        "remote": false,
        "current": false,
        "sha": "def5678",
        "upstream": "",
        "tracking": ""
      }
    ],
    "default_branch": "main",
    "detached": false,
    "current": "main",
    "total": 2,
    "umo": "...",
    "worktree": "...",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 12
  }
}
```

**关键点**:
- `default_branch` 由 `git symbolic-ref refs/remotes/origin/HEAD` 探测(失败则用 `init.defaultBranch` 配置或 fallback `main`)
- `detached=true` 时 `current=null`,分支列表仍返回(只是无 `current: true` 标记)
- 解析器对 `for-each-ref` 的输出做防御:任何字段异常 → fallback 整条丢弃,不要因为单条脏数据 500
- 与 `git-status` 的 ETag 模式完全对齐,复用 `_compute_git_etag`

**ReasonCode 新增**:无(读端点失败继承 preflight 7 个通用 reason)

---

### 3.3 `POST /spcode/git-branch-create`

**用途**:`git branch <name>` 从当前 HEAD 拉新分支(不切换)。

**请求体**:
```json
{
  "name": "feature/x",
  "start_point": "abc1234",
  "force": false,
  "umo": "...",
  "worktree": "..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `name` | str | ✅ | — | 新分支名(走 `_is_valid_ref_name`) |
| `start_point` | str | ❌ | `"HEAD"` | 起点 ref(走 `_is_valid_ref_name`) |
| `force` | bool | ❌ | `false` | `true` → `git branch -f`(覆盖已存在分支) |

**6 步防御链**:
1. **L1** body 类型 → `invalid_body`
2. **L2** `name` 必传 + str → `invalid_param`
3. **L3** `_is_valid_ref_name(name)` → `invalid_branch`
4. **L4** `_is_valid_ref_name(start_point)` (若传) → `invalid_param`
5. **L5** 跑 `git branch [--force] <name> [<start_point>]`:
   - 失败 stderr 分类:`"already exists"` → `branch_exists` / `"not a valid branch name"` → `invalid_branch` / 其他 → `git_error`
6. **L6** 成功后回读 `git rev-parse --verify <name>^{commit}` 取 SHA

**成功响应**:
```json
{
  "status": "ok",
  "data": {
    "created": true,
    "name": "feature/x",
    "sha": "abc1234",
    "start_point": "HEAD",
    "force": false,
    "umo": "...",
    "worktree": "...",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 23
  }
}
```

**关键点**:
- 与 `git-worktree-add` 的 `cannot_create_existing` 命名冲突 → 用更具体的 `branch_exists` 避免误读
- **不**自动切到新分支(这是 `git switch -c` 的语义,留给 `git-branch-switch` 的 `create=true` 模式)
- `force=true` 等价于 `git branch -f <name> <start_point>`,允许覆盖现有同名分支(若新 start_point 与现有分支不同)

**ReasonCode 新增(1 个)**:`branch_exists`

---

### 3.4 `POST /spcode/git-branch-delete`

**用途**:`git branch -d/-D <name>` 删除分支。

**请求体**:
```json
{
  "name": "feature/x",
  "force": false,
  "umo": "...",
  "worktree": "..."
}
```

**5 步防御链**:
1. **L1** body 类型 → `invalid_body`
2. **L2** `name` 必传 + str → `invalid_param`
3. **L3** `_is_valid_ref_name(name)` → `invalid_branch`
4. **L4** 跑 `git rev-parse --abbrev-ref HEAD` 取 current,与 name 比较 → `branch_is_current`(即便 `-D` 也禁止,与 `git-worktree-remove` 硬禁 main 同思路)
5. **L5** 跑 `git branch -d/-D <name>`,stderr 分类:
   - `"not fully merged"` → `branch_not_merged` (仅 force=false)
   - `"not found."` → `branch_not_found`
   - `"Cannot delete branch ... checked out"` → `branch_is_current` 兜底
   - 其他 → `git_error`

**成功响应**:
```json
{
  "status": "ok",
  "data": {
    "deleted": true,
    "name": "feature/x",
    "force": false,
    "was_current": false,
    "umo": "...",
    "worktree": "...",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 18
  }
}
```

**关键点**:
- 硬禁删 current branch,**不**用 force 绕过(对齐 `git-worktree-remove` 的"硬禁 main")
- 删 remote branch 不在本端点范畴(留待后续 `git-branch-delete-remote` 单独端点)
- `force=false` 走 `git branch -d`(拒绝未合并),`force=true` 走 `git branch -D`(强制)

**ReasonCode 新增(3 个)**:`branch_not_found` / `branch_is_current` / `branch_not_merged`

---

### 3.5 `POST /spcode/git-branch-switch`

**用途**:`git switch <name>` 切换当前分支;可选创建模式 `git switch -c <name> [start_point]`。

**请求体**:
```json
{
  "name": "feature/x",
  "create": false,
  "start_point": "main",
  "force": false,
  "detach": false,
  "umo": "...",
  "worktree": "..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `name` | str | ✅ | — | 目标分支名 |
| `create` | bool | ❌ | `false` | `true` → `git switch -c <name> [start_point]`(创建并切换) |
| `start_point` | str | ❌ | — | 仅 `create=true` 时有效 |
| `force` | bool | ❌ | `false` | `true` → `git switch -f`(丢弃 worktree 改动,危险) |
| `detach` | bool | ❌ | `false` | `true` → `git switch --detach`(切换到 detached HEAD) |

**8 步防御链**:
1. **L1** body 类型 → `invalid_body`
2. **L2** `name` 必传 + str → `invalid_param`
3. **L3** `_is_valid_ref_name(name)` → `invalid_branch`
4. **L4** `_is_valid_ref_name(start_point)` (若 create) → `invalid_param`
5. **L5** 跨字段校验(`_validate_switch_cross_fields`):`create=true AND detach=true` → `invalid_body`(互斥)
6. **L6** 跑 `git status --porcelain`(若 worktree 有未追踪/未暂存):
   - `create=false` → 报 `worktree_dirty`(除非 force=true 跳过检查,落给 git 自身判定)
   - `create=true AND start_point` → 同上(创建即切换)
7. **L7** 跑 `git switch [-c|-f|--detach] <name> [start_point]`,stderr 分类:
   - `"already exists"` (create=true) → `branch_exists`
   - `"did not match any"` / `"not found"` → `branch_not_found`
   - `"Your local changes"` → `worktree_dirty` (兜底)
   - `"would be overwritten"` → `worktree_dirty` (兜底)
   - `"not a valid branch name"` → `invalid_branch`
   - 其他 → `git_error`
8. **L8** 成功后回读 current branch(`git rev-parse --abbrev-ref HEAD`)

**成功响应**:
```json
{
  "status": "ok",
  "data": {
    "switched": true,
    "name": "feature/x",
    "previous": "main",
    "created": false,
    "force": false,
    "detach": false,
    "umo": "...",
    "worktree": "...",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 34
  }
}
```

**关键点**:
- 用 `git switch` 而非 `git checkout`(语义更明确,git 2.23+ 默认;本项目既有 git ≥ 2.30 假设)
- `force=true` 等价于 `git switch -f`,会丢弃 worktree 改动(必须显式传,默认 false)
- 切换后 `_compute_git_etag` 缓存自动失效(下个 git-status/git-diff 请求会重算,因为 etag 依赖 HEAD SHA)

**ReasonCode 新增**:复用 `branch_exists` / `branch_not_found` / `worktree_dirty` / `invalid_branch`(其中 `worktree_dirty` 为新加 reason)

---

### 3.6 `POST /spcode/git-revert`

**用途**:`git revert <ref> --no-edit` 生成 revert commit。

**请求体**:
```json
{
  "ref": "abc1234",
  "no_edit": true,
  "umo": "...",
  "worktree": "..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `ref` | str | ❌ | `"HEAD"` | 要回滚的 commit ref(走 `<ref>^{commit}` 解析) |
| `no_edit` | bool | ❌ | `true` | `true` → `--no-edit`(自动 commit message),`false` → 打开编辑器(本端点必须 `true`,传 `false` 返回 `invalid_param`) |

**5 步防御链**:
1. **L1** body 类型 → `invalid_body`
2. **L2** `ref` 类型校验(默认 `"HEAD"`)→ `invalid_param`;`no_edit` 必须为 `true` → `invalid_param`(本端点不接编辑器模式)
3. **L3** 跑 `git rev-parse --verify <ref>^{commit}` → `commit_not_found`(同时校验 ref 是 commit 而非 tag/tree/blob)
4. **L4** 跑 `git status --porcelain` → `worktree_dirty`(必须 clean,否则 git 自动拒绝)
5. **L5** 跑 `git revert --no-edit <ref>`,通过 `_classify_revert_stderr` 分类:
   - `"merge conflict"` / `"CONFLICT"` → `revert_conflict`
   - `"nothing to revert"` → `nothing_to_revert`
   - `"Your local changes would be overwritten"` → `worktree_dirty`(兜底)
   - `"pre-commit hook"` / `"commit-msg hook"` → `hook_rejected`(复用)
   - `"Please tell me who you are"` → `identity_not_set`(复用)
   - 其他 → `git_error`

**成功响应**:
```json
{
  "status": "ok",
  "data": {
    "reverted": true,
    "ref": "abc1234",
    "revert_sha": "fff9999",
    "revert_message": "Revert \"feat: xxx\"",
    "files_touched": ["src/a.py"],
    "umo": "...",
    "worktree": "...",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 67
  }
}
```

**关键点**:
- **复用** `git_commit._build_git_env()` 透传 `GIT_AUTHOR_*`(集成测试 / dashboard 注入作者身份)
- **复用** `git_commit._classify_commit_error` 的 4 类 hook/identity/nothing/git_error 分类 —— 实际上 `_classify_revert_stderr` 内部**先调** `_classify_commit_error`,未命中再走 revert 专属 4 类
- 成功后回读 `git rev-parse HEAD` 拿 revert commit 完整 SHA
- `files_touched` 通过 `git show --name-only --pretty="" HEAD` 解析
- 不支持 merge revert(`-m`),不支持 `--no-commit` 模式(留给未来扩展)
- revert 失败时 `.git/REVERT_HEAD` 会留痕,前端需展示给用户(不在端点清理范畴)
- 本 spec **明确不接** `no_edit=false`(打开编辑器),原因:headless 服务无法交互;若用户想要自定义 message,revert 完再 `git commit --amend`

**ReasonCode 新增(4 个)**:`commit_not_found` / `worktree_dirty` / `revert_conflict` / `nothing_to_revert`

---

## §4 ReasonCode 集中表

`tools/webapi/_helpers.py:ReasonCode` 类**新增 12 个字面量**(与现有 60+ 共用),按端点分组:

| 端点 | 码 | 含义 |
|------|------|------|
| **git-init (4)** | `path_not_directory` | 路径存在但不是目录 |
| | `directory_not_empty` | 目录里已有文件(最严格语义:任何文件都拒绝) |
| | `already_a_git_repo` | 目录里已有 `.git/` |
| | `init_failed` | `git init` 自身失败(stderr 不含前 3 类) |
| **git-branch-create (1)** | `branch_exists` | 分支已存在(force=false 兜底) |
| **git-branch-delete (3)** | `branch_not_found` | ref-format OK 但 ref 在仓库不存在 |
| | `branch_is_current` | 试图删当前 HEAD 所在分支(硬禁,force 也不绕过) |
| | `branch_not_merged` | `-d` 但分支未合并(force=false) |
| **git-branch-switch (1)** | `worktree_dirty` | working tree 有未提交改动(创建/切换前探测 + git 兜底) |
| **git-revert (3)** | `commit_not_found` | `<ref>^{commit}` 解析失败 |
| | `revert_conflict` | 反向 patch 与 worktree 冲突 |
| | `nothing_to_revert` | ref = HEAD 且 HEAD 无改动可被反 |

**合计 12 个新 reason**:4 (init) + 1 (create) + 3 (delete) + 1 (switch) + 3 (revert) = 12。

**命名空间审计**:
- 与现有 `worktree_*` 系列(`worktree_invalid` / `worktree_not_in_repo` / `worktree_locked` 等)无重叠
- 与现有 `patch_*` 系列(`patch_empty` / `patch_malformed` 等)无重叠
- 与 `file_*` 系列(`file_missing_at_ref` / `file_too_large` 等)无重叠
- `branch_exists` 与 `cannot_create_existing` 语义相似但用途不同:前者用于 `git branch` 单分支,后者用于 `git worktree add` 多分支 → 故意区分,前端可分别处理

---

## §5 跨端点共享不变量

| 不变量 | 实现位置 |
|--------|---------|
| 所有写端点必须走 `_git_endpoint_preflight`(init 除外) | `tools/webapi/_helpers.py` |
| 所有响应字段顺序:`endpoint_specific` → `reason` → `stderr` → `elapsed_ms` | `_make_envelope` |
| 所有 `body` 参数走类型 + 必传校验 | 各 handler L1-L2 |
| 所有 `name` / `ref` 走 `_is_valid_ref_name`(35 字符限制 + 字符集) | `tools/_helpers.py` |
| 所有 `?worktree=` 走 `_validate_worktree_param`(6 步防御) | `tools/_helpers.py` |
| 所有 git 子进程统一走 `_run_git_async`(支持 stdin / env / timeout) | `tools/webapi/_helpers.py` |
| `init` 端点零依赖(不调 `_git_endpoint_preflight`,自己 4 步) | 新增 `_git_init_preflight` |
| 跨端点 reason code 集中管理 | `tools/webapi/_helpers.py:ReasonCode` |
| 失败路径必填 `reason` + `stderr` | `_make_envelope` 强制 |
| 关键失败写 `logger.warning(...)` | 各 handler |

---

## §6 测试策略

### 6.1 测试文件布局

```
tests/
├── conftest.py                            # 扩展: 4 个新 fixtures
├── test_git_init.py                       # ~18 cases
├── test_git_branches.py                   # ~14 cases
├── test_git_branch_create.py              # ~12 cases
├── test_git_branch_delete.py              # ~16 cases
├── test_git_branch_switch.py              # ~22 cases
├── test_git_revert.py                     # ~20 cases
├── test_git_branches_lifecycle.py         # E2E: init → branches → create → switch → commit → revert
└── test_webapi_end_to_end.py              # 扩展: 24 → 30 路由计数 + 6 端点 smoke
```

**总增量:~102 个新单元测试 case + 1 个 E2E 生命周期 + 6 路由表断言更新**。

### 6.2 共享 fixtures(`conftest.py` 新增)

| Fixture | 用途 |
|---------|------|
| `empty_dir` | 空目录(给 git-init 用) |
| `existing_repo` | 完整 git 仓库:init + 1 commit + 1 branch |
| `dirty_repo` | existing_repo 基础上加未提交改动 |
| `loaded_umo` | 把 existing_repo 注册到 `tools.project.state` 模拟加载 |

### 6.3 各端点测试矩阵

详细 case 列表见附录 A,核心覆盖:

| 端点 | happy path | body 校验 | 路径安全 | preflight | 业务 reason | 集成 |
|------|----------|----------|---------|----------|------------|------|
| git-init | 3 | 4 | 3 | (无) | 4 | 2 |
| git-branches | 4 | 0 | 0 | 3 | 0 | 2 + 2 ETag |
| git-branch-create | 3 | 4 | 0 | 2 | 2 | 1 |
| git-branch-delete | 2 | 3 | 0 | 2 | 5 | 4 |
| git-branch-switch | 5 | 4 | 0 | 3 | 6 | 4 |
| git-revert | 3 | 2 | 0 | 3 | 7 | 5 |

### 6.4 跨端点 E2E 生命周期

`tests/test_git_branches_lifecycle.py::test_init_to_revert_full_flow`:

```
step 1:  POST /spcode/git-init {path=tmp/repo, initial_branch="main"}
step 2:  GET  /spcode/git-branches {umo=test_umo, worktree=tmp/repo}
step 3:  (shell: 写 README + commit)
step 4:  POST /spcode/git-branch-create {name=feature/x, umo=test_umo}
step 5:  GET  /spcode/git-branches
step 6:  POST /spcode/git-branch-switch {name=feature/x, umo=test_umo}
step 7:  (shell: 改文件 + commit on feature/x)
step 8:  POST /spcode/git-branch-switch {name=main, umo=test_umo}
step 9:  POST /spcode/git-revert {ref=feature/x 最后一个 commit, umo=test_umo}
step 10: GET  /spcode/git-log
step 11: POST /spcode/git-branch-delete {name=feature/x, umo=test_umo}
step 12: GET  /spcode/git-branches
```

### 6.5 回归路径

**既有测试回归**(新增 6 端点**不应**触碰现有 24 路由代码):
- `pytest tests/test_webapi_end_to_end.py` 路由计数断言:`assert len(ROUTES) == 30` + 6 个新 entry 检查
- `pytest tests/test_git_endpoint_preflight.py` 不变(新 init 端点绕过 preflight,无新增调用)
- `pytest tests/test_reason_code.py` 扩展 12 个新 reason 字面量

**兼容性**:
- `_make_plugin()` fixture 不需要改(走 `state.get` 路径)
- `_validate_worktree_param` 不改
- `_is_valid_ref_name` 不改
- `_run_git_async` 不改
- 6 个新 handler 全是 `async def handle(plugin, *, umo=None, worktree=None, body=None)` 标准签名

### 6.6 性能预算

| 端点 | 首次调用 | 缓存命中 |
|------|---------|---------|
| git-init | ~50-200ms(创建文件系统级目录树) | N/A |
| git-branches | ~30-50ms(`for-each-ref` 单进程) | <1ms(1.5s TTL ETag) |
| git-branch-create | ~30-100ms | N/A |
| git-branch-delete | ~30-100ms | N/A |
| git-branch-switch | ~30-100ms + worktree 状态探测 | N/A |
| git-revert | ~50-200ms(与 git-commit 同量级) | N/A |

---

## §7 Dashboard 契约约定

| 端点 | 前端应展示 |
|------|-----------|
| `git-init` | 弹窗"选择目录" + 显示 `initialized` 状态;失败显示 `directory_not_empty` / `already_a_git_repo` 等 |
| `git-branches` | 切换下拉(current 标星号);不可切到 detached |
| `git-branch-create` | 弹窗输入框 + 可选 start_point;`branch_exists` 显示现有分支列表 |
| `git-branch-delete` | 列表行操作按钮;current 分支禁用;`branch_not_merged` 弹"强制删除"二次确认 |
| `git-branch-switch` | 切换下拉;`worktree_dirty` 弹"丢弃改动"二次确认 |
| `git-revert` | commit log 行操作按钮;`worktree_dirty` 阻止;成功后刷新 git-log |

---

## §8 风险点 & 缓解

| 风险 | 缓解 |
|------|------|
| `git switch` 在 git 2.23 之前不可用 | 文档要求 git ≥ 2.30(项目既有假设) |
| `git init -b <branch>` 在 git 2.28+ 才支持 | fallback:`git init && git symbolic-ref HEAD refs/heads/<branch>`(代码内两路分支) |
| ETag 在分支切换后未失效 | 复用 `_compute_git_etag` 3 路 porcelain 探测,**自动**捕捉 HEAD SHA 变化 |
| init 后用户忘记调用 `project.load` | spec §7 写明 "init 不自动 load,需 LLM 后续 `project.load` 动作";dashboard 弹窗成功后跳转 |
| revert 失败留 `.git/REVERT_HEAD` 污染 | 不在端点清理范畴(留给用户手动 `git revert --abort` 或 commit);spec 文档化 |
| `directory_not_empty` 判定过严(用户已放 README) | spec §3.1 明确这是 v2.17.0 的最严格语义,后续可加 `force=true` 绕过(留 v3.10+) |
| `git revert` 与 `git revert -m` 混淆 | spec §3.6 明确本端点**不接** `-m` 参数,merge revert 留 v3.10+ |
| 12 个新 reason code 命名空间冲突 | §4 集中表已审计,与 `worktree_*` / `patch_*` 等命名空间无重叠 |

---

## §9 实施计划(摘要)

**PR 拆分建议**(后续 `writing-plans` skill 展开):

| PR | 范围 | 文件数 | 测试数 | 预估 LOC |
|----|------|-------|-------|---------|
| **PR-A: 基础设施** | `_git_init_preflight` / `ReasonCode` 12 个新码 / conftest fixtures | 2 改 | 0 单元 + 2 fixture | ~120 |
| **PR-B: git-init** | `tools/webapi/git_init.py` + `test_git_init.py` + ROUTES | 1 新 + 1 改 | 18 | ~280 |
| **PR-C: git-branches (GET)** | `tools/webapi/git_branches.py` + `test_git_branches.py` + ROUTES | 1 新 + 1 改 | 14 | ~250 |
| **PR-D: git-branch-create** | `tools/webapi/git_branch_create.py` + `test_git_branch_create.py` + ROUTES | 1 新 + 1 改 | 12 | ~220 |
| **PR-E: git-branch-delete** | `tools/webapi/git_branch_delete.py` + `test_git_branch_delete.py` + ROUTES | 1 新 + 1 改 | 16 | ~280 |
| **PR-F: git-branch-switch** | `tools/webapi/git_branch_switch.py` + `test_git_branch_switch.py` + ROUTES | 1 新 + 1 改 | 22 | ~400 |
| **PR-G: git-revert** | `tools/webapi/git_revert.py` + `test_git_revert.py` + ROUTES | 1 新 + 1 改 | 20 | ~350 |
| **PR-H: E2E + 路由断言** | `test_git_branches_lifecycle.py` + `test_webapi_end_to_end.py` 扩展 + `test_reason_code.py` 扩展 | 1 新 + 2 改 | 12 + 6 smoke | ~180 |
| **PR-I: 文档** | `docs/webapi-git-init-branch-revert-api.md` + `README.md` + `AGENTS.md` 增量 | 1 新 + 2 改 | 0 | ~360 |
| **合计** | | 6 新 + 6 改 | ~102 单元 + 12 E2E + 6 smoke | ~2440 |

**PR-A 是基础设施,所有后续 PR 依赖它**;PR-B/C/D/E/F/G 之间**无依赖,可并行**;PR-H 依赖 PR-B~G 全部合并;PR-I 最后做。

---

## §10 开放问题(待实施时澄清)

1. **`directory_not_empty` 判定**采用"任何文件即拒绝",还是"仅 `.git` / `.gitignore` 允许"?spec 暂定前者(最严格),实施时若用户反馈"想保留 README"再放宽
2. **revert 失败后 `.git/REVERT_HEAD` 清理**是否在端点范畴?当前 spec 不清理(由用户手动处理)
3. **branch-switch 的 `force=true` 是否需要二次确认**?当前 spec 直接走 `git switch -f`,由 dashboard UI 加二次确认弹窗

---

## 附录 A:各端点测试矩阵详表

(略,详见 `tests/test_git_*.py` 各文件)

---

**设计完成,等待用户最终 review → 调用 writing-plans skill 制定实施计划。**
