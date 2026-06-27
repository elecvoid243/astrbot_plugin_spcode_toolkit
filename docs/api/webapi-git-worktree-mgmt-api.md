# spcode 工具箱 — Git Worktree Management API(v2.14.0)

> **目标读者**: 前端开发 Agent(Dashboard / 工作区面板 / worktree 管理 UI)
> **版本**: 对应 `astrbot_plugin_spcode_toolkit` v2.14.0(`2026-06-26`)
> **作者**: elecvoid243
> **更新时间**: 2026-06-27
> **适用范围**:本文档覆盖 v2.14.0 新增的 **4 个 worktree 管理端点**。其他端点(`/spcode/git-diff`、`/spcode/git-worktrees`、`/spcode/git-status`、`/spcode/project-status` 等)请参考 [`web-api.md`](./web-api.md) 与 [`webapi-git-workflow-api.md`](./webapi-git-workflow-api.md)。

本文档描述 4 个用于**管理 git worktree** 的 POST 端点。**注意**:这 4 个端点是**写端点**(修改 git 状态),与已存在的读端点 `/spcode/git-worktrees` 互补;前者负责列,后者负责改。

---

## 1. 快速导航

| 端点 | 方法 | 用途 | 典型 UI 场景 |
|------|------|------|-------------|
| [`/spcode/git-worktree-add`](#31-post-spcodegit-worktree-add) | POST | 新建一个 worktree(`-b` / `-B` / `--detach` / `--force` 平铺) | "新建分支 worktree" 对话框 |
| [`/spcode/git-worktree-remove`](#32-post-spcodegit-worktree-remove) | POST | 删除一个 worktree(硬禁 main,locked 拒,`force=true` 跳过 dirty) | worktree 列表行右键菜单 / 危险确认弹窗 |
| [`/spcode/git-worktree-lock`](#33-post-spcodegit-worktree-lock) | POST | 锁定一个 worktree(可选 `--reason`,main 可 lock) | worktree 列表行"锁"图标 |
| [`/spcode/git-worktree-unlock`](#34-post-spcodegit-worktree-unlock) | POST | 解锁一个 worktree(无 idempotent 检查,git 自身拒绝 main) | worktree 列表行"锁"图标(解锁态) |

---

## 2. 通用约定

### 2.0 Base URL

所有路由以 `/spcode/` 为前缀。完整 base URL 由 AstrBot 主进程配置决定,通常形如:

```
http://<astrbot-host>:<port>/spcode/
```

请向部署侧确认实际 host / port / 反代前缀。

### 2.1 鉴权

本插件 **不实现独立鉴权**。所有 Web API 都由 AstrBot 框架统一鉴权层(Q-Auth / Token / IP 白名单,取决于部署配置)前置。

前端调用时:

- 从 AstrBot 框架侧获取鉴权凭据(Token / Cookie)
- 在请求头中按 AstrBot 约定附加(如 `Authorization: Bearer <token>`)

### 2.2 Content-Type

| 方向 | Content-Type |
|------|-------------|
| 请求(POST) | `application/json; charset=utf-8` |
| 响应 | `application/json; charset=utf-8`,UTF-8 编码 |

即使是 Windows 中文系统,`stderr` 字段中**非 ASCII 字符保持原始 UTF-8**,前端按 UTF-8 解码。

### 2.3 公共响应信封

**所有端点**都返回以下统一形状:

```json
{
  "status": "ok",
  "data": { /* 端点特定字段 */ }
}
```

- `status`: 恒为 `"ok"`。本插件 **不使用 HTTP 状态码表达业务错误**;即使发生"无项目"、"非法参数"、"git 未安装"等错误,仍返回 `200 OK` + `status: "ok"`,错误信息放在 `data.reason` 字段中。
- **前端判错必须看 `data.reason`,不要用 `status` 判断。**

### 2.4 `umo` 参数(unified message origin)

`umo` 是 AstrBot 给每条消息分配的会话 ID,形如 `webchat:astrbot`、`aiocqhttp:group:123456` 等。

**前端应**:

- 在登录 / 接入 AstrBot 后,从某次交互中获取当前会话的 `umo`。
- 把 `umo` 保存到本地(LocalStorage / Pinia / Vuex / 内存皆可)。
- 每次调用本插件的端点时,把 `umo` 放到 query string。

**后端行为**:

- 传了 `umo` → 查 `_loaded_projects[umo]`。
- 未传 `umo` → 退回到 **最近一次加载** 的项目。

**异常值**:

- `umo=` 空字符串 → 视同未传。
- `umo` 值不存在于 `_loaded_projects` → 返回 `reason: "no_project_loaded"`。

### 2.5 `worktree` query 参数(管理端点不推荐使用)

4 个管理端点都接受 `worktree` query 参数(可选),含义与 [`/spcode/git-diff?worktree=`](./web-api.md#22-get-spcodegit-diff) 一致:在哪个 worktree 的上下文中执行该操作(用于"在 worktree A 中新建 worktree B"这种嵌套场景)。

**6 步防御链**:长度检查 / `..` 段拒绝 / symlink 解析 / 隐藏目录拒绝 / `realpath != 原路径` 拒绝 / `git-common-dir` 必须与主仓库匹配。非法值 → `reason: "worktree_invalid"`。

**前端最佳实践**:

- **常规场景**:不传 `worktree`(默认主 worktree,与 v1 行为一致)。
- **嵌套场景**:从 `/spcode/git-worktrees` 的 `data.worktrees[].path` 中选取合法值传入。

### 2.6 POST 请求 body 通用形状

```ts
interface WorktreeMgmtRequest {
  path: string;       // 必填:worktree 绝对路径(ADD 表示"新位置",其余表示"目标")
  // 端点特定字段见各端点章节
  [key: string]: unknown;
}
```

`path` 字段含义因端点而异:

| 端点 | `path` 含义 |
|------|------------|
| `git-worktree-add` | **新建** worktree 的绝对路径(目录可不存在,父目录必须存在) |
| `git-worktree-remove` | **已存在** worktree 的绝对路径(必须出现在 `git worktree list`) |
| `git-worktree-lock` | **已存在** worktree 的绝对路径(可包含 main,git 允许 lock main) |
| `git-worktree-unlock` | **已存在** worktree 的绝对路径(同上) |

### 2.7 `reason` 错误分类

4 个端点共享同一组 `reason` 字段分类。详细列表见 [§4 错误码参考](#4-错误码参考)。

| 类别 | reason 前缀 | 含义 |
|------|------------|------|
| 通用前置 | `feature_disabled` / `no_project_loaded` / `worktree_invalid` | 5 步 preflight 中止 |
| 通用前置 | `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` | 仓库上下文无效 |
| body 校验 | `invalid_body` / `invalid_branch` / `invalid_param` | 输入结构或格式错 |
| 路径安全 | `path_unsafe` | `path` 字段含 `..` / 绝对路径越界 / 黑名单命中 |
| 业务结果 | `path_exists_nonempty` / `cannot_create_existing` / `cannot_checkout_missing` | ADD 路径 / 分支 / 起始点冲突 |
| 业务结果 | `cannot_remove_main` / `worktree_locked` / `worktree_dirty` / `worktree_not_found` | REMOVE 业务闸拒绝 |
| 业务结果 | `already_locked` / `not_locked` | LOCK / UNLOCK 业务闸 |

---

## 3. 端点详情

### 3.1 `POST /spcode/git-worktree-add`

新建一个 git worktree,等价于 CLI 的 `git worktree add [-b|-B|--detach] <path> [<branch>]`。

**7 层防御链**:

1. body type guard(非 dict → `invalid_body`)
2. `_validate_new_worktree_path`(4 步格式防御 + 黑名单)
3. `_validate_add_cross_fields`(create / force / detach / base / branch 互斥)
4. `_is_valid_ref_name`(branch / base ref 格式)
5. 路径存在非空预检
6. `_run_git_async(git worktree add ...)`
7. post-create `git-common-dir` 兜底校验

#### 3.1.1 请求

| Header / Body 字段 | 必填 | 类型 | 默认 | 说明 |
|-------------------|------|------|------|------|
| `Content-Type` | ✓ | header | — | `application/json` |
| `umo` (query) | 否 | string | (无) | 会话 ID |
| `worktree` (query) | 否 | string | (无) | 操作上下文(罕见,见 §2.5) |
| `path` (body) | ✓ | string | — | 新 worktree 绝对路径 |
| `branch` (body) | 条件 | string | `null` | 分支名。`detach=true` 时可省略,其余场景必填 |
| `create` (body) | 否 | boolean | `false` | `true` → 用 `-b <branch>`(不存在则新建分支) |
| `force` (body) | 否 | boolean | `false` | `true` → 用 `-B <branch>`(强制覆盖已存在分支) |
| `detach` (body) | 否 | boolean | `false` | `true` → 用 `--detach`(分离 HEAD 模式) |
| `base` (body) | 否 | string | `null` | 起始点(commit SHA / 已有分支 / tag)。仅当 `create=true` 时生效 |

**Body 示例**(JSON):

```json
{
  "path": "F:/github/myrepo/.worktrees/feat-foo",
  "branch": "feat/foo",
  "create": true,
  "base": "main"
}
```

#### 3.1.2 CLI 旗标映射

| `create` | `force` | `detach` | 等价 CLI |
|----------|---------|----------|---------|
| `true` | `false` | `false` | `git worktree add -b <branch> <path> [<base>]` |
| `false` | `true` | `false` | `git worktree add -B <branch> <path> [<base>]` |
| `false` | `false` | `true` | `git worktree add --detach <path> [<branch>]` |
| `false` | `false` | `false` | `git worktree add <path> <branch>`(基本签出) |
| 其他组合 | | | **互斥校验失败 → `invalid_body`** |

#### 3.1.3 互斥规则(快速参考)

| 规则 | 触发 |
|------|------|
| ❌ `create=true` AND `force=true` | 拒绝(`-b` 与 `-B` 互斥) |
| ❌ `detach=true` AND `create=true` | 拒绝(detach 不支持 create) |
| ❌ `detach=true` AND `force=true` | 拒绝(detach 不支持 force) |
| ❌ `base != null` AND `create=false` | 拒绝(base 仅在 create 模式有效) |
| ❌ `detach=false` AND `branch` 为空 / `null` | 拒绝(非 detach 模式必须指定 branch) |

#### 3.1.4 响应字段

**成功**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `loaded` | boolean | `true` |
| `directory` | string | 主仓库目录(操作执行上下文) |
| `umo` | string \| null | 实际匹配到的 umo |
| `worktree` | string | **新建**的 worktree 绝对路径(= 请求 `path`) |
| `branch` | string \| null | 实际使用的分支名(`detach` 模式时为 `null`) |
| `worktrees` | array\<object\> | **刷新后**的完整 worktree 列表(同 [`/spcode/git-worktrees`](./web-api.md#23-get-spcodegit-worktrees)) |
| `reason` | string \| null | 成功时为 `null` |
| `stderr` | string | 成功时为 `""` |
| `elapsed_ms` | integer | 服务端处理耗时(ms) |

**成功响应示例**:

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:/github/astrbot_plugin_spcode_toolkit",
    "umo": "webchat:astrbot",
    "worktree": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo",
    "branch": "feat/foo",
    "worktrees": [
      { "path": "F:/github/astrbot_plugin_spcode_toolkit", "branch": "main", "head_sha": "ff051f2...", "is_main": true, "locked": false, "locked_reason": null },
      { "path": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo", "branch": "feat/foo", "head_sha": "abc1234...", "is_main": false, "locked": false, "locked_reason": null }
    ],
    "reason": null,
    "stderr": "",
    "elapsed_ms": 124
  }
}
```

**失败响应示例 1 — `base` 指向不存在的分支**(git 2.43 实测):

请求:
```json
{
  "path": "F:/github/myrepo/.worktrees/feat-foo",
  "branch": "feat/foo",
  "create": true,
  "base": "nonexistent-branch"
}
```

响应:
```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:/github/myrepo",
    "umo": "webchat:astrbot",
    "worktree": "F:/github/myrepo",
    "reason": "cannot_checkout_missing",
    "stderr": "fatal: invalid reference: nonexistent-branch",
    "elapsed_ms": 47
  }
}
```

> **关键点**:
> - `reason: "cannot_checkout_missing"` 表示分支/起始点不存在(L6 git stderr 映射)
> - `stderr` **完整保留** git 原始输出,前端可直接展示给开发者或在 telemetry 中记录
> - 注意:即便 `create=true`,L4 只检查 `base` 的**字符串格式**,**不**做存在性预检;存在性检查完全交给 git,这就是为什么错误必然走 L6

**失败响应示例 2 — `base` 格式无效**(L4 预检,git 都不调用):

请求:
```json
{
  "path": "F:/github/myrepo/.worktrees/feat-foo",
  "branch": "feat/foo",
  "create": true,
  "base": "ba..d"
}
```

响应:
```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:/github/myrepo",
    "umo": "webchat:astrbot",
    "worktree": "F:/github/myrepo",
    "reason": "invalid_param",
    "stderr": "invalid base ref: 'ba..d'",
    "elapsed_ms": 3
  }
}
```

> **关键点**:
> - `elapsed_ms: 3` — 仅几毫秒,因为 L4 在 git 调用前就拦截了(对比例 1 的 47ms)
> - 前端可借此区分"格式错"(快速失败)与"git 报错"(需等待子进程)

**失败响应示例 3 — 目标路径已存在非空**(L5 预检):

请求:
```json
{
  "path": "F:/github/myrepo/.worktrees/already-exists",
  "branch": "feat/foo",
  "create": true,
  "base": "main"
}
```

响应:
```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:/github/myrepo",
    "umo": "webchat:astrbot",
    "worktree": "F:/github/myrepo",
    "reason": "path_exists_nonempty",
    "stderr": "target path already exists and is non-empty: F:/github/myrepo/.worktrees/already-exists",
    "elapsed_ms": 8
  }
}
```

#### 3.1.5 失败 reason 枚举

| `reason` | 触发条件 | 拦截层 | 前端处理建议 |
|----------|---------|--------|-------------|
| `invalid_body` | body 非 dict / 必填字段缺失 / 字段互斥冲突(`create`+`force`、`detach`+`create`、`detach`+`force`、`base` 非空但 `create=false`、非 detach 模式但 `branch` 为空) | L1 / L3 | 高亮表单字段;展示 `stderr` 解释 |
| `path_unsafe` | `path` 含 `..` / 绝对路径越界 / 黑名单命中 | L2 | 阻断提交;提示"路径非法或命中黑名单" |
| `invalid_branch` | `branch` 不符合 git ref 格式(包含 `..` / 空格 / `-` 开头 / 长度 > 1024 等) | L4 | 阻止提交;提示"分支名格式错" |
| `invalid_param` | 两种触发:① L4 `base` 格式无效(如 `ba..d`);② L6 git 拒绝 `path` 作为 worktree 名称(如含 `:`) | L4 / L6 | 阻止提交;展示 `stderr` 区分两种原因 |
| `cannot_checkout_missing` | **`branch` 不存在**(`create=false`) **或 `base` 指向不存在的 ref / SHA / tag** | **L6** (git stderr: `fatal: invalid reference: <X>`) | 提示"分支/起始点不存在,请检查 `branch` 与 `base`";提供分支下拉补全(可调 `git for-each-ref`);**不动** `force` 字段 |
| `path_exists_nonempty` | 目标 `path` 已存在且目录非空 | L5 | 提示"目标路径已被占用";询问是否换路径 |
| `cannot_create_existing` | `create=true` 但分支已存在(且未传 `force`) | L6 | 提示"分支已存在,请勾选强制覆盖或改用 `force=true`" |
| `feature_disabled` / `no_project_loaded` / `worktree_invalid` / `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` | 见 [§4 错误码参考](#4-错误码参考) | L2 / L7 | 见 [§4](#4-错误码参考) |

#### 3.1.6 前端 UX 建议

- **对话框表单字段**:
  - 路径(必填,默认建议 `.worktrees/<branch-sanitized>`)
  - 分支名(必填,`detach=true` 时禁用)
  - 复选框:`创建新分支` / `强制覆盖` / `分离 HEAD`(三者互斥,建议用单选按钮组)
  - 起始点(可选,默认 `main`,`create=true` 才显示)
- **错误处理**:
  - `path_exists_nonempty` → 弹窗询问"是否删除旧目录后重试"(二次确认后才再次提交)
  - `cannot_create_existing` → 提示用户切到 `force=true`(即 `-B` 语义)
  - `cannot_checkout_missing` → 提供分支下拉补全弹窗(调 `git for-each-ref`);区分是 `branch` 不存在还是 `base` 不存在;**不要**自动勾选 `force`
- **成功后续**:刷新 worktree 列表(直接用响应里的 `data.worktrees`,无需再调 GET);跳转至 worktree 文件浏览器。

#### 3.1.7 前端处理矩阵(ADD 专用)

> 本矩阵为 ADD 端点的 **7 种业务错误 reason** 提供精确的 UI 操作建议,用于表单校验联动。

| `reason` | 表单 UI 改动 | 用户提示(首个交互) | `force` 字段 | branch / base / path 高亮 |
|----------|------------|------------------|-------------|--------------------------|
| `invalid_body` | 阻断提交 | `stderr` 直接展示(已含人类可读英文) | 不动 | 无(多字段冲突,整体禁用) |
| `path_unsafe` | 阻断提交 | "路径命中黑名单或格式非法" | 不动 | 🔴 **path** 输入框红框 |
| `invalid_branch` | 阻提交 | "分支名格式错(不能含空格 / `..` / `-` 开头)" | 不动 | 🔴 **branch** 输入框红框 |
| `invalid_param` (L4: base 格式) | 阻提交 | "起始点格式错" | 不动 | 🔴 **base** 输入框红框 |
| `invalid_param` (L6: path 名称) | 阻提交 | "目标路径不可作为 worktree 名称" | 不动 | 🔴 **path** 输入框红框 |
| `cannot_checkout_missing` | **不**阻断,提供补全 | "分支/起始点不存在" + 弹出分支选择器(调 `git for-each-ref`) | **不动**(force 对此无效) | 🟡 **base** + **branch** 黄框(两者都可能) |
| `path_exists_nonempty` | **不**阻断,提供选项 | 弹窗询问:"目标路径已被占用。是否换路径,或删除后重试?" | 不动 | 🟡 **path** 黄框 |
| `cannot_create_existing` | **不**阻断,提供选项 | 弹窗提示:"分支已存在。是否强制覆盖?" + 自动勾选 `force=true` | ✅ **自动勾选** `force=true` (映射到 `-B` 语义) | 🟢 **branch** 绿框(已解决) |

**关键设计原则**:

1. **`cannot_checkout_missing` 是唯一不需要 `force` 参与的**:起点不存在,`-B` 不会帮助;应该让用户选正确的 branch/base。
2. **预检层(L1-L5)失败快,毫秒级**;L6 失败需要 git 子进程退出,通常 30-50ms。前端可用 `elapsed_ms` 区分。
3. **`stderr` 区分**:`invalid_param` 的 `stderr` 内容决定了高亮的是 `base` 还是 `path` 输入框,前端必须按 `stderr` 关键词路由。

---

### 3.2 `POST /spcode/git-worktree-remove`

删除一个 git worktree,等价于 CLI 的 `git worktree remove [--force] <path>`。

**8 层防御链**:

1. body type guard(非 dict → `invalid_body`)
2. `_git_endpoint_preflight`(5 步)
3. `_resolve_target_worktree`(格式 + 列表查找)
4. **main worktree 硬禁**(`force=true` 不绕过 → `cannot_remove_main`)
5. **locked 检查**(`force=true` 不绕过,需先 unlock → `worktree_locked`)
6. dirty 检查(除非 `force=true` → `worktree_dirty`)
7. `_run_git_async(git worktree remove [--force] <path>)`
8. `_list_worktrees_safe` 刷新

#### 3.2.1 请求

| Header / Body 字段 | 必填 | 类型 | 默认 | 说明 |
|-------------------|------|------|------|------|
| `Content-Type` | ✓ | header | — | `application/json` |
| `umo` (query) | 否 | string | (无) | 会话 ID |
| `worktree` (query) | 否 | string | (无) | 操作上下文(罕见,见 §2.5) |
| `path` (body) | ✓ | string | — | **已存在** worktree 的绝对路径 |
| `force` (body) | 否 | boolean | `false` | `true` → 跳过 dirty 检查(等价 `git worktree remove --force`) |

**Body 示例**(JSON):

```json
{
  "path": "F:/github/myrepo/.worktrees/feat-foo",
  "force": false
}
```

#### 3.2.2 关键安全语义

| 业务闸 | `force=true` 是否绕过 | reason |
|--------|---------------------|--------|
| main worktree(`is_main=true`) | ❌ 不绕过 | `cannot_remove_main` |
| locked(`locked=true`) | ❌ 不绕过 | `worktree_locked`(需先调 UNLOCK) |
| dirty(未提交改动) | ✅ 跳过 dirty 检查 | `worktree_dirty`(仅在 `force=false` 时触发) |

> **设计意图**:前两个闸是**结构性保护**,不允许前端用 UI 开关绕过;dirty 闸是**便利性优化**,仅跳过"是否脏"的预检,**不**跳过 main / locked 检查。

#### 3.2.3 响应字段

**成功**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `loaded` | boolean | `true` |
| `directory` | string | 主仓库目录 |
| `umo` | string \| null | 实际匹配到的 umo |
| `worktree` | string | **被删除**的 worktree 绝对路径 |
| `removed_path` | string | 同 `worktree`(冗余字段,便于前端识别) |
| `worktrees` | array\<object\> | 刷新后的 worktree 列表(不包含已删除项) |
| `reason` | string \| null | `null` |
| `stderr` | string | `""` |
| `elapsed_ms` | integer | 服务端处理耗时(ms) |

**成功响应示例**:

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:/github/astrbot_plugin_spcode_toolkit",
    "umo": "webchat:astrbot",
    "worktree": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo",
    "removed_path": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo",
    "worktrees": [
      { "path": "F:/github/astrbot_plugin_spcode_toolkit", "branch": "main", "head_sha": "ff051f2...", "is_main": true, "locked": false, "locked_reason": null }
    ],
    "reason": null,
    "stderr": "",
    "elapsed_ms": 87
  }
}
```

#### 3.2.4 失败 reason 枚举

| `reason` | 触发条件 | 前端处理建议 |
|----------|---------|-------------|
| `worktree_not_found` | `path` 不在 `git worktree list` 中(拼写错 / 已被删除 / 跨仓库) | 提示"该 worktree 不存在";刷新列表 |
| `cannot_remove_main` | 尝试删除主 worktree(`is_main=true`) | **不可绕过**;前端必须禁用 main 的"删除"按钮 |
| `worktree_locked` | 目标已 locked(`locked_reason` 字段可读) | 提示"请先解锁";按钮直接跳到 LOCK/UNLOCK UI |
| `worktree_dirty` | 目标有未提交改动,未传 `force=true` | 弹窗询问"是否强制删除(将丢失未提交改动)" |
| `path_unsafe` | `path` 含 `..` / 黑名单 | 阻断;提示"路径非法" |
| `invalid_body` | body 非 dict / `path` 缺失 | 高亮表单 |
| `feature_disabled` / `no_project_loaded` / `worktree_invalid` / `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` | 见 [§4](#4-错误码参考) | 见 [§4](#4-错误码参考) |

#### 3.2.5 前端 UX 建议

- **危险操作二次确认**:**始终**弹窗确认(即使 dirty=false);显示 worktree 路径 + branch + dirty 文件数。
- **disabled 规则**:
  - `is_main=true` → 删除按钮 **禁用**(永远)
  - `locked=true` → 删除按钮禁用,hover 显示"locked: <reason>"
  - 列表项 hover 时如未禁用,展示 tooltip "删除 worktree(若有未提交改动需确认)"
- **dirty 计数显示**:在列表项右侧调用 `/spcode/git-status?worktree=<path>` 拿 `files_changed` 字段(同步或 lazy),如 > 0 则自动勾选 `force=true`(但仍需二次确认)。

---

### 3.3 `POST /spcode/git-worktree-lock`

锁定一个 git worktree,等价于 CLI 的 `git worktree lock [--reason <text>] <path>`。

**6 层防御链**:

1. body type guard(非 dict → `invalid_body`)
2. `_git_endpoint_preflight`(5 步)
3. `_resolve_target_worktree`(格式 + 列表查找)
4. already_locked 检查(无 business bypass;main 也可被 lock)
5. `git worktree lock [--reason] <path>`
6. `_list_worktrees_safe` 刷新

#### 3.3.1 请求

| Header / Body 字段 | 必填 | 类型 | 默认 | 说明 |
|-------------------|------|------|------|------|
| `Content-Type` | ✓ | header | — | `application/json` |
| `umo` (query) | 否 | string | (无) | 会话 ID |
| `worktree` (query) | 否 | string | (无) | 操作上下文(罕见) |
| `path` (body) | ✓ | string | — | **已存在** worktree 的绝对路径 |
| `reason` (body) | 否 | string | `null` | 锁定原因(可选,**git 2.30+** 支持);UI 通常作为"备注"展示 |

**Body 示例**(JSON):

```json
{
  "path": "F:/github/myrepo/.worktrees/feat-foo",
  "reason": "WIP; DO NOT DELETE until PR #123 merged"
}
```

#### 3.3.2 关键安全语义

| 项 | 说明 |
|----|------|
| main worktree | **可以** lock(设计哲学:"不强制业务闸,让 git 自身决定")。handler **不**做 `is_main` 硬禁;若 lock main,git 会返回成功,但日常 git 操作可能受影响。建议前端**仍禁用** main 的 lock 按钮作为 UI 防护层。 |
| 重复 lock | 已 locked 的 worktree 再次 lock → `already_locked`(避免无意义 git 调用)。 |
| `reason` 长度 | **无显式上限**(git 接受任意字符串);建议前端限制 200 字符以内。 |

#### 3.3.3 响应字段

**成功**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `loaded` | boolean | `true` |
| `directory` | string | 主仓库目录 |
| `umo` | string \| null | 实际匹配到的 umo |
| `worktree` | string | **被锁定**的 worktree 绝对路径 |
| `locked` | boolean | 恒为 `true` |
| `lock_reason` | string \| null | 实际写入的 reason(=`body.reason` 或 `null`) |
| `worktrees` | array\<object\> | 刷新后的 worktree 列表(目标项 `locked=true`、`locked_reason=<reason>`) |
| `reason` | string \| null | `null` |
| `stderr` | string | `""` |
| `elapsed_ms` | integer | 服务端处理耗时(ms) |

**成功响应示例**:

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:/github/astrbot_plugin_spcode_toolkit",
    "umo": "webchat:astrbot",
    "worktree": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo",
    "locked": true,
    "lock_reason": "WIP; DO NOT DELETE until PR #123 merged",
    "worktrees": [
      { "path": "F:/github/astrbot_plugin_spcode_toolkit", "branch": "main", "head_sha": "ff051f2...", "is_main": true, "locked": false, "locked_reason": null },
      { "path": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo", "branch": "feat/foo", "head_sha": "abc1234...", "is_main": false, "locked": true, "locked_reason": "WIP; DO NOT DELETE until PR #123 merged" }
    ],
    "reason": null,
    "stderr": "",
    "elapsed_ms": 42
  }
}
```

#### 3.3.4 失败 reason 枚举

| `reason` | 触发条件 | 前端处理建议 |
|----------|---------|-------------|
| `worktree_not_found` | `path` 不在 worktree 列表中 | 提示"不存在";刷新列表 |
| `already_locked` | 目标已 locked | 禁用按钮;hover 显示当前 `locked_reason` |
| `path_unsafe` | `path` 非法 | 阻断;提示"路径非法" |
| `invalid_body` | body 非 dict / `path` 缺失 | 高亮表单 |
| `feature_disabled` / `no_project_loaded` / `worktree_invalid` / `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` | 见 [§4](#4-错误码参考) | 见 [§4](#4-错误码参考) |

#### 3.3.5 前端 UX 建议

- **列表项状态指示**:`locked=true` 的 worktree 显示锁图标 + tooltip `"locked: <locked_reason>"`。
- **lock 按钮交互**:点击后弹窗输入 `reason`(可空),确认后提交。
- **乐观更新**:提交后无需轮询,直接用响应里的 `data.worktrees` 替换本地缓存。

---

### 3.4 `POST /spcode/git-worktree-unlock`

解锁一个 git worktree,等价于 CLI 的 `git worktree unlock <path>`。

**5 层防御链**:

1. body type guard(非 dict → `invalid_body`)
2. `_git_endpoint_preflight`(5 步)
3. `_resolve_target_worktree`(格式 + 列表查找)
4. `git worktree unlock <path>` + stderr → `not_locked` / `worktree_not_found` / `git_error`
5. `_list_worktrees_safe` 刷新

> **与 LOCK 的设计差异**:UNLOCK **没有**显式 `not_locked` 业务闸,依赖 git stderr 映射("fatal: '/target' is not locked" → `not_locked`)。这样保持与 LOCK 的设计哲学一致("无业务闸,让 git 决定"),避免冗余的列表查询。

#### 3.4.1 请求

| Header / Body 字段 | 必填 | 类型 | 默认 | 说明 |
|-------------------|------|------|------|------|
| `Content-Type` | ✓ | header | — | `application/json` |
| `umo` (query) | 否 | string | (无) | 会话 ID |
| `worktree` (query) | 否 | string | (无) | 操作上下文(罕见) |
| `path` (body) | ✓ | string | — | **已存在** worktree 的绝对路径 |

**Body 示例**(JSON):

```json
{
  "path": "F:/github/myrepo/.worktrees/feat-foo"
}
```

> **注意**:UNLOCK 不接 `reason` 字段。Lock 时写入的 reason 在 unlock 后会被丢弃。

#### 3.4.2 关键安全语义

| 项 | 说明 |
|----|------|
| main worktree | **可以** unlock(同上,handler 不做硬禁;git 自身允许)。 |
| 重复 unlock | 已 unlocked 的 worktree 再次 unlock → `not_locked`(非 idempotent,会被 git 拒绝)。 |
| `worktree_locked` reason | **不可**通过 UNLOCK 修改或清空(写入即固定);如需修改,需先 unlock → lock(传新 reason)。 |

#### 3.4.3 响应字段

**成功**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `loaded` | boolean | `true` |
| `directory` | string | 主仓库目录 |
| `umo` | string \| null | 实际匹配到的 umo |
| `worktree` | string | **被解锁**的 worktree 绝对路径 |
| `locked` | boolean | 恒为 `false` |
| `worktrees` | array\<object\> | 刷新后的 worktree 列表(目标项 `locked=false`、`locked_reason=null`) |
| `reason` | string \| null | `null` |
| `stderr` | string | `""` |
| `elapsed_ms` | integer | 服务端处理耗时(ms) |

**成功响应示例**:

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:/github/astrbot_plugin_spcode_toolkit",
    "umo": "webchat:astrbot",
    "worktree": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo",
    "locked": false,
    "worktrees": [
      { "path": "F:/github/astrbot_plugin_spcode_toolkit", "branch": "main", "head_sha": "ff051f2...", "is_main": true, "locked": false, "locked_reason": null },
      { "path": "F:/github/astrbot_plugin_spcode_toolkit/.worktrees/feat-foo", "branch": "feat/foo", "head_sha": "abc1234...", "is_main": false, "locked": false, "locked_reason": null }
    ],
    "reason": null,
    "stderr": "",
    "elapsed_ms": 38
  }
}
```

#### 3.4.4 失败 reason 枚举

| `reason` | 触发条件 | 前端处理建议 |
|----------|---------|-------------|
| `worktree_not_found` | `path` 不在工作树列表中 | 提示"不存在";刷新列表 |
| `not_locked` | 目标未锁定 | UI 应**提前**禁用 unlock 按钮(基于 `locked=false`);若用户绕过,展示"该 worktree 未锁定"提示 |
| `path_unsafe` | `path` 非法 | 阻断;提示"路径非法" |
| `invalid_body` | body 非 dict / `path` 缺失 | 高亮表单 |
| `feature_disabled` / `no_project_loaded` / `worktree_invalid` / `directory_missing` / `not_a_git_repo` / `git_unavailable` / `git_error` | 见 [§4](#4-错误码参考) | 见 [§4](#4-错误码参考) |

#### 3.4.5 前端 UX 建议

- **按钮禁用规则**:`locked=false` 的 worktree 直接禁用 unlock 按钮(避免 `not_locked` 错误污染 UX)。
- **乐观更新**:成功后用响应 `data.worktrees` 替换本地缓存。

---

## 4. 错误码参考

### 4.1 错误码完整列表

下表涵盖 4 个端点的所有失败 `reason`(按类别排序)。

| 类别 | reason | 含义 | 触发端点 |
|------|--------|------|---------|
| **前置** | `feature_disabled` | 插件 `agentsmd_enabled` / `codegraph_enabled` 任一为 `false` | ALL |
| **前置** | `no_project_loaded` | `umo` 未传且无最近项目,或 `umo` 无已加载项目 | ALL |
| **前置** | `worktree_invalid` | `?worktree=` 参数未通过 6 步防御链 | ALL(若传了 worktree) |
| **前置** | `directory_missing` | 主项目目录不存在 | ALL |
| **前置** | `not_a_git_repo` | 主项目目录非 git 仓库 | ALL |
| **前置** | `git_unavailable` | git 未安装或不可执行 | ALL |
| **前置** | `git_error` | git 命令执行失败(非业务闸捕获) | ALL |
| **body** | `invalid_body` | body 非 dict / 必填字段缺失 / 互斥字段冲突 | ADD / REMOVE / LOCK / UNLOCK |
| **body** | `invalid_branch` | `branch` 不符合 git ref 格式(含 `..` / 空格 / `-` 开头 / 超长) | ADD |
| **body** | `invalid_param` | 两种触发:① L4 `base` 格式无效;② L6 `path` 不可作为 worktree 名称(如含 `:`) | ADD |
| **路径安全** | `path_unsafe` | `path` 含 `..` / 越界绝对路径 / 黑名单命中 | ALL |
| **业务结果** | `path_exists_nonempty` | ADD 目标路径已存在且非空 | ADD |
| **业务结果** | `cannot_checkout_missing` | ADD `branch` 不存在(`create=false`) 或 `base` 指向不存在的 ref / SHA / tag | ADD |
| **业务结果** | `cannot_create_existing` | ADD `create=true` 但分支已存在 | ADD |
| **业务结果** | `worktree_not_found` | `path` 不在工作树列表中 | REMOVE / LOCK / UNLOCK |
| **业务结果** | `cannot_remove_main` | REMOVE 目标是主 worktree | REMOVE |
| **业务结果** | `worktree_locked` | REMOVE 目标已 locked(需先 unlock) | REMOVE |
| **业务结果** | `worktree_dirty` | REMOVE 目标有未提交改动(未传 `force=true`) | REMOVE |
| **业务结果** | `already_locked` | LOCK 目标已 locked | LOCK |
| **业务结果** | `not_locked` | UNLOCK 目标未 locked | UNLOCK |

### 4.2 错误响应统一形状

```json
{
  "status": "ok",
  "data": {
    "loaded": false,
    "directory": "<string | null>",
    "umo": "<string | null>",
    "worktree": "<string>",
    "reason": "<reason_code>",
    "stderr": "<git / handler error message>",
    "elapsed_ms": <int>
  }
}
```

- `directory`: 若前置校验失败,可能是 `""`(L1 body guard)或真实目录(L2 之后)。
- `worktree`: 前置失败时是 `?worktree=` 原值;L1 失败时为 `""`。
- `stderr`: **永远返回**,无论成功失败(成功时为 `""`)。前端可用于调试或展示给开发者。

### 4.3 前端全局错误处理矩阵

| reason 类别 | 推荐 UI 处理 |
|------------|------------|
| `feature_disabled` / `no_project_loaded` / `directory_missing` / `not_a_git_repo` | Toast 顶部提示;**禁用所有 worktree 操作按钮** |
| `worktree_invalid` | Toast 提示"路径非法";前端 bug,通常不该发生 |
| `git_unavailable` / `git_error` | 红色 banner;操作按钮全禁用;展示 `stderr` 详情(开发者面板) |
| `invalid_body` / `invalid_branch` | **表单字段高亮**(根据 `stderr` 推断);阻塞提交 |
| `invalid_param` | 两种处理:若 `stderr` 含 `invalid base ref` → 高亮 base 输入框;若含 `cannot be used as a worktree name` → 高亮 path 输入框 |
| `path_unsafe` | Toast 提示"路径命中黑名单";**不展示** stderr 细节(避免暴露黑名单) |
| `cannot_checkout_missing` | 分支下拉补全弹窗(调 `git for-each-ref`);区分 branch / base 哪个不存在;提示"分支/起始点不存在" |
| `path_exists_nonempty` / `cannot_create_existing` | 内联提示;提供"换路径"或"切到 `force=true`"按钮 |
| `worktree_not_found` | 列表项标红"已不存在";自动刷新列表 |
| `cannot_remove_main` | **永远不该到达前端**(前端应禁用 main 删除按钮);若发生,Toast 报错并记录到监控 |
| `worktree_locked` | 列表项展示锁图标 + reason;按钮跳转到 UNLOCK UI |
| `worktree_dirty` | 弹窗确认"将丢弃 X 个未提交改动";用户确认后**前端自动重提交**并带 `force=true` |
| `already_locked` / `not_locked` | Toast 提示当前状态;刷新按钮 |

---

## 5. 常见工作流

### 5.1 新建 worktree 并跳转

```ts
// 1. POST 新建
const res = await fetch(`/spcode/git-worktree-add?umo=${umo}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  body: JSON.stringify({
    path: `.worktrees/${branch.replace(/\//g, '-')}`,
    branch,
    create: true,
    base: 'main',
  }),
});
const { data } = await res.json();
if (!data.reason) {
  // 2. 替换本地 worktree 缓存
  worktreesStore.set(data.worktrees);
  // 3. 跳转到文件浏览器
  router.push({ name: 'file-browser', query: { worktree: data.worktree } });
}
```

### 5.2 二次确认后强制删除

```ts
async function removeWorktree(path: string) {
  // dirty check(可选;UI 也能直接尝试删除,失败时再弹窗)
  const dirtyCount = await getDirtyFileCount(path);
  let force = false;
  if (dirtyCount > 0) {
    const confirmed = await confirmDialog(
      `Worktree 有 ${dirtyCount} 个未提交改动,确认强制删除?`,
    );
    if (!confirmed) return;
    force = true;
  } else {
    const confirmed = await confirmDialog(`确认删除 ${path}?`);
    if (!confirmed) return;
  }

  const res = await fetch(`/spcode/git-worktree-remove?umo=${umo}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ path, force }),
  });
  const { data } = await res.json();
  if (data.reason === 'worktree_locked') {
    // 引导用户先 unlock
    showToast(`Worktree 已锁定: ${data.stderr};请先解锁`, 'warning');
  } else if (data.reason) {
    showToast(`删除失败: ${data.reason}`, 'error');
  } else {
    worktreesStore.set(data.worktrees);
    showToast('已删除', 'success');
  }
}
```

### 5.3 Lock / Unlock 切换

```ts
async function toggleLock(wt: Worktree) {
  const endpoint = wt.locked ? 'git-worktree-unlock' : 'git-worktree-lock';
  const body: Record<string, unknown> = { path: wt.path };
  if (!wt.locked) {
    const reason = await promptDialog('锁定原因(可选)');
    if (reason === null) return; // user cancelled
    body.reason = reason;
  }
  const res = await fetch(`/spcode/${endpoint}?umo=${umo}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  const { data } = await res.json();
  if (data.reason) {
    showToast(`操作失败: ${data.reason}`, 'error');
  } else {
    worktreesStore.set(data.worktrees);
  }
}
```

---

## 6. TypeScript 类型参考

```ts
// ── 通用信封 ──
interface ApiEnvelope<T> {
  status: 'ok';
  data: T;
}

// ── Worktree 项(与 GET /spcode/git-worktrees 一致) ──
interface WorktreeItem {
  path: string;
  branch: string | null;          // detached HEAD 时为 null
  head_sha: string;
  is_main: boolean;               // 第一个元素固定为 true
  locked: boolean;                // v2.14.0 起(v3.0 之前为 false)
  locked_reason: string | null;   // v2.14.0 起;locked=false 时为 null
}

// ── 失败 reason 联合类型 ──
type WorktreeMgmtReason =
  // 前置
  | 'feature_disabled' | 'no_project_loaded' | 'worktree_invalid'
  | 'directory_missing' | 'not_a_git_repo' | 'git_unavailable' | 'git_error'
  // body
  | 'invalid_body' | 'invalid_branch' | 'invalid_param'
  // 路径安全
  | 'path_unsafe'
  // 业务
  | 'path_exists_nonempty' | 'cannot_checkout_missing' | 'cannot_create_existing'
  | 'worktree_not_found'
  | 'cannot_remove_main' | 'worktree_locked' | 'worktree_dirty'
  | 'already_locked' | 'not_locked';

// ── ADD ──
interface GitWorktreeAddRequest {
  path: string;
  branch?: string;
  create?: boolean;
  force?: boolean;
  detach?: boolean;
  base?: string;
}
interface GitWorktreeAddResponse {
  loaded: true;
  directory: string;
  umo: string | null;
  worktree: string;
  branch: string | null;
  worktrees: WorktreeItem[];
  reason: null;
  stderr: '';
  elapsed_ms: number;
}

// ── REMOVE ──
interface GitWorktreeRemoveRequest {
  path: string;
  force?: boolean;
}
interface GitWorktreeRemoveResponse {
  loaded: true;
  directory: string;
  umo: string | null;
  worktree: string;
  removed_path: string;
  worktrees: WorktreeItem[];
  reason: null;
  stderr: '';
  elapsed_ms: number;
}

// ── LOCK ──
interface GitWorktreeLockRequest {
  path: string;
  reason?: string;
}
interface GitWorktreeLockResponse {
  loaded: true;
  directory: string;
  umo: string | null;
  worktree: string;
  locked: true;
  lock_reason: string | null;
  worktrees: WorktreeItem[];
  reason: null;
  stderr: '';
  elapsed_ms: number;
}

// ── UNLOCK ──
interface GitWorktreeUnlockRequest {
  path: string;
}
interface GitWorktreeUnlockResponse {
  loaded: true;
  directory: string;
  umo: string | null;
  worktree: string;
  locked: false;
  worktrees: WorktreeItem[];
  reason: null;
  stderr: '';
  elapsed_ms: number;
}

// ── 失败响应(所有端点共用) ──
interface WorktreeMgmtFailureResponse {
  loaded: false;
  directory: string;
  umo: string | null;
  worktree: string;
  reason: WorktreeMgmtReason;
  stderr: string;
  elapsed_ms: number;
}
```

---

## 7. 兼容性 / 迁移

| 版本 | 变更 | 兼容性 |
|------|------|--------|
| v2.14.0 (2026-06-26) | 新增 4 端点;`GET /spcode/git-worktrees` 响应新增 `locked` / `locked_reason` 字段 | 向后兼容(新字段为可选) |
| 未来 | — | 本节会按 CHANGELOG 顺序追加 |

`locked` / `locked_reason` 字段在更早版本中不存在,**前端必须容忍缺失**:

```ts
const locked = wt.locked ?? false;
const lockedReason = wt.locked_reason ?? null;
```

---

## 8. 相关文档

- [`web-api.md`](./web-api.md) — 通用 Web API 总览(含 `GET /spcode/git-worktrees` 读端点)
- [`webapi-git-workflow-api.md`](./webapi-git-workflow-api.md) — git workflow 端点(`/git-status` / `/git-stage` / `/git-unstage` / `/git-commit` / `/git-log`)
- 设计规格:`docs/superpowers/specs/2026-06-26-git-worktree-management-design.md`
- 实施计划:`docs/superpowers/plans/2026-06-26-git-worktree-management-impl.md`

---

**作者**: elecvoid243 · **生成时间**: 2026-06-27 19:20 (CST)