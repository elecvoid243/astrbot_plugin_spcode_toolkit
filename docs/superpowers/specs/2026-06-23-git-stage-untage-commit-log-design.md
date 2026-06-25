# /spcode/git-stage · /spcode/git-unstage · /spcode/git-commit · /spcode/git-log — Git 工作流端点集

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后,下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已通过(用户确认日期 2026-06-23,Q1=A, Q2=B, Q3=A, Q4=B, Q5=扩展 unstage, Q6=方案 A 独立端点)
**作者**: elecvoid243 @ 2026-06-23 23:12
**目标插件**: `astrbot_plugin_spcode_toolkit`(主干)
**前置版本**: 当前 HEAD `d7c7b92 fix(project_status): 修复import异常`(`/spcode/git-diff` / `/spcode/git-worktrees` / `/spcode/file-restore` 均已发布)
**配套 UI**: 前端 dashboard "Git 工作流" 标签页(由前端 agent 另行实现,本文档为其 API 契约)

## 摘要

新增 4 个 `/spcode/git-*` HTTP 端点,完成 dashboard 端"查看变更 → 暂存 → 提交 → 浏览历史"的完整 git 工作流:

| 端点 | 方法 | 对应 git 命令 | 性质 |
|------|------|--------------|------|
| `/spcode/git-stage` | POST | `git add -f [--] <files>` / `git add -A` | 写 |
| `/spcode/git-unstage` | POST | `git reset HEAD [--] <files>` / `git reset HEAD` | 写 |
| `/spcode/git-commit` | POST | `git commit -m <message>` | 写(严格最小) |
| `/spcode/git-log` | GET | `git log --pretty=... --shortstat` | 读(标准粒度) |

**架构选择**:4 个独立端点(方案 A)。理由:URL 清晰、HTTP method 语义化、单个 handler 复杂度低、与既有 webapi 风格一致。

**关键约束**:
- 写操作不做二次确认(API 层),由 dashboard 弹窗确认后调用
- `commit` 严格最小:不支持 amend / allow-empty / GPG / 作者覆盖 / 跳过 hooks
- `stage` / `unstage` 支持 `files` 列表 + `all:true` 两种互斥模式
- `log` 支持 8 字段标准粒度(sha/author/date/subject + body/committer/parents/shortstat)+ 多维过滤参数 + 1.5s TTL ETag 缓存

## 用户环境约束

4 个端点均在**服务端**(AstrBot 进程所在机器)通过 `subprocess` 调 git 命令行,要求:

| 依赖 | 要求 | 缺失时的行为 |
|------|------|--------------|
| `git` 命令行工具 | 安装在系统中(任意平台) | 端点返回 `reason: "git_unavailable"`;不影响插件加载 |
| `git` 在 PATH 中(默认) | `git --version` 可执行 | 同上 |
| 已知 `git` 绝对路径(非标准安装) | 配置 `git_path` 字段 | 端点优先使用该路径;未设置时回退 PATH |

> 与既有 4 个 git-* 端点共用 git 可用性探测,启动期 WARNING 一次即可。

## 用户决策记录

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: "git 暂存"语义** | **A** — `git add`(放入暂存区) | 三端点对应"选→暂→提"标准 git 流程 |
| **Q2: git-log 字段粒度** | **B** — 标准(sha / author / date / subject + body / committer / parents / shortstat 共 8 字段组) | 4-in-1 git 调用;无需分页就能展示 Dashboard 90% 场景 |
| **Q3: git-commit 能力** | **A** — 严格最小(仅 commit staged,无 amend/empty/GPG) | 安全面小;hook 正常触发;高危操作全用显式 body 字段 |
| **Q4: git-stage 粒度** | **B** — `files` 列表 + `all:true` 两种互斥模式 | 精细 stage 与全量快捷并存;UI 灵活 |
| **Q5: 范围扩展** | 新增 `git-unstage` 端点 | 与 stage 对称;`git reset HEAD` 实现 |
| **Q6: 端点架构** | **A** — 4 个独立端点 | URL 清晰,handler 复杂度低,与既有模式一致 |
| **Q7: 严格最小 commit 的具体范围** | 不接受 `amend` / `allow_empty` / `no_verify` / `signoff` / `gpgsign` / `author` / `date` 字段;出现即 `invalid_body` | 防止 API 表面增长失控;未来加版本再加 |
| **Q8: gitignore 文件的处理(2026-06-24 增补)** | `files` 模式 → `git add -f -- <files>`;`all` 模式 → `git add -A`(不加 `-f`) | 用户在 Dashboard 逐行点击 = 显式意图,`all=true` 批量操作必须保守,避免把未被注意的 ignored 文件(如 `.env`)一并暂存。典型场景:`dashboard/src/assets/mdi-subset/*` 既被 `dashboard/.gitignore` 忽略又已 commit,工作区改版后 `git add` 默认拒绝更新 index,导致前端 red error snackbar;`-f` 让 userspace 工作流与 git semantics 对齐 |

---

## 核心设计

### 1. 路由表更新

现有 6 端点 → 新 10 端点。`ROUTES` 顺序按"读 → 写"分组,git_log 紧邻 git_diff:

```python
ROUTES = [
    ("/spcode/project-status", ["GET"],  project_status.handle,  "..."),
    ("/spcode/plan-mode",      ["GET"],  plan_mode.handle,       "..."),
    ("/spcode/git-worktrees",  ["GET"],  git_worktrees.handle,  "..."),
    ("/spcode/git-diff",       ["GET"],  git_diff.handle,       "..."),
    ("/spcode/git-log",        ["GET"],  git_log.handle,        "..."),       # 新
    ("/spcode/file-browser",   ["GET"],  file_browser.handle,   "..."),
    ("/spcode/file-restore",   ["POST"], file_restore.handle,   "..."),
    ("/spcode/git-stage",      ["POST"], git_stage.handle,      "..."),       # 新
    ("/spcode/git-unstage",    ["POST"], git_unstage.handle,    "..."),       # 新
    ("/spcode/git-commit",     ["POST"], git_commit.handle,     "..."),       # 新
]
```

### 2. 共享前置流程(`_git_endpoint_preflight`)

把 git-diff / file-restore 现有的 6 步抽取为 `tools/webapi/_helpers.py` 的 `_git_endpoint_preflight()`:

```python
async def _git_endpoint_preflight(
    plugin,
    umo: str | None,
    worktree_param: str | None,
    *,
    feature_flags: tuple[str, ...] = ("agentsmd_enabled", "codegraph_enabled"),
) -> tuple[dict | None, dict | None]:
    """Return (error_envelope, ctx):
    - 成功: (None, {"directory": <str>, "umo": <str|None>, "worktree": <str>})
    - 失败: ({...failure envelope...}, None)
    """
    # 1. feature flag
    # 2. umo 解析 + 回退最近 loaded project
    # 3. worktree 6 步防御
    # 4. directory is_dir() 检查
    # 5. git repo probe (rev-parse --is-inside-work-tree)
    # 失败原因: feature_disabled / no_project_loaded / worktree_invalid /
    #          directory_missing / not_a_git_repo / git_unavailable / git_error
```

每个 handler 第 1~5 步调一次,拿到 `(err, ctx)`:
- `err is not None` → 直接 `return err`
- `err is None` → `ctx["directory"]` 即 `git -C` 的目标目录

### 3. 共享 envelope 工厂

每个端点都有 success / failure 两种 envelope,差异仅在 `data` 字段。新增 `_make_envelope()`:

```python
def _make_envelope(
    *, success: bool, reason: str | None = None, stderr: str = "",
    elapsed_ms: int = 0, **data_fields,
) -> dict:
    """统一 envelope 工厂。
    - success=True  → {"status": "ok", "data": {**data_fields, "reason": None, "stderr": ""}}
    - success=False → {"status": "ok", "data": {**data_fields, "reason": <reason>, "stderr": <...>}}
    """
```

- 4 端点共用此工厂
- 写操作额外回显 `directory` / `umo` / `worktree`
- 读操作额外回显 `directory` / `umo` / `count` / `has_more`

### 4. ReasonCode 常量集中

在 `tools/webapi/_helpers.py` 集中所有 reason 码字面量,4 端点统一 import,避免分散硬编码:

```python
class ReasonCode:
    # 通用前置类
    FEATURE_DISABLED = "feature_disabled"
    NO_PROJECT_LOADED = "no_project_loaded"
    WORKTREE_INVALID = "worktree_invalid"
    DIRECTORY_MISSING = "directory_missing"
    NOT_A_GIT_REPO = "not_a_git_repo"
    GIT_UNAVAILABLE = "git_unavailable"
    GIT_ERROR = "git_error"
    # body / param 校验类
    INVALID_BODY = "invalid_body"
    INVALID_FILES = "invalid_files"
    INVALID_ALL = "invalid_all"
    EMPTY_MESSAGE = "empty_message"
    MESSAGE_TOO_LONG = "message_too_long"
    INVALID_PARAM = "invalid_param"
    # 文件路径 / 范围类
    PATH_UNSAFE = "path_unsafe"
    NOTHING_STAGED = "nothing_staged"   # git commit 无 staged 改动时(dashboard 友好文案)
    PRE_COMMIT_HOOK_FAILED = "pre_commit_hook_failed"
    EMPTY_REPOSITORY = "empty_repository"
```

### 5. 共享路径校验提升

把 `file_restore.py` 中的 `_validate_restore_file` 提升到 `tools/webapi/_helpers.py`:

- 新名: `_validate_repo_relative_file(file_path, worktree) -> tuple[Path | None, str | None]`
- 行为:完全相同(4 步防御)
- `file_restore.py` 内部 import 它并保留旧函数作为薄壳委托(向后兼容,零外部影响)
- `git_stage` / `git_unstage` / `git_log(path 参数)` 直接 import 这个新共享函数

---

## 端点契约

### 端点 A:`POST /spcode/git-stage`

#### URL 契约

| 维度 | 值 |
|------|---|
| Method | **POST** |
| Path | `/spcode/git-stage` |
| Content-Type | `application/json` |
| Body | `{"files": ["a.py", "b.md"], "umo": "...", "worktree": "..."}` **或** `{"all": true, "umo": "...", "worktree": "..."}` |
| 路由注册位置 | `tools/webapi/__init__.py` 的 `ROUTES` 列表 |
| 鉴权 | 沿用 AstrBot 框架统一鉴权 |

#### body 校验规则

- `files` 和 `all` 互斥;都不传 / 都传 → `invalid_body`
- `files` 必须是非空字符串列表;任一元素空 / 非字符串 → `invalid_files`
- `files` 长度 ≤ 1000(防滥发,**硬上限**;`all` 模式无 files 数组因此不受此限)→ 超长 `invalid_files`
- `all` 必须是严格 `true`(不接受 1 / "true" / "yes" 字符串)→ 否则 `invalid_all`
- 任何未知字段 → `invalid_body`(v1 严格白名单)
- `files` 模式:每个 file 走 `_validate_repo_relative_file` 4 步防御 → 任一失败 `path_unsafe`
- `all` 模式:跳过 file 路径校验(但 worktree 6 步防御仍生效)

#### 响应信封

```json
// 成功
{
  "status": "ok",
  "data": {
    "staged": true,
    "files": ["src/a.py", "docs/b.md"],   // all 模式时为 null
    "all": false,                          // files 模式时为 false
    "directory": "...",
    "umo": "...",
    "worktree": "...",
    "staged_count": 2,                    // 实际 stage 成功的文件数(files 模式 = len(files);all 模式 = 0 或实际数量,见下方说明)
    "rejected": [],                        // files 模式时为空;all 模式始终 []
    "elapsed_ms": 45,
    "reason": null,
    "stderr": ""
  }
}

// 失败
{ "status": "ok", "data": { "staged": false, "reason": "invalid_body", ... } }
```

#### git 调用

- **files 模式**:`git -C <dir> -c color.ui=never add -- <files...>`(一次调用;`--` 防 file 名撞 -x 选项)
- **all 模式**:`git -C <dir> -c color.ui=never add -A`(含未跟踪 + 含删除)

#### 审计日志

```
[git-stage] staged: files=[a.py, b.md] all=false worktree=<dir> umo=<umo> elapsed_ms=<n>
[git-stage] staged: all=true worktree=<dir> umo=<umo> elapsed_ms=<n>   # all 模式
[git-stage] failed: reason=<r> worktree=<dir> umo=<umo> elapsed_ms=<n> # 失败路径
```

---

### 端点 B:`POST /spcode/git-unstage`

#### URL 契约

| 维度 | 值 |
|------|---|
| Method | **POST** |
| Path | `/spcode/git-unstage` |
| Body | 与 stage 完全对称:`{"files": [...]}` 或 `{"all": true}` |
| Content-Type | `application/json` |
| 鉴权 | 沿用 AstrBot 框架统一鉴权 |

#### body 校验规则

与 stage 完全相同(同样走 `_validate_repo_relative_file`,同样的 4 类 reason 码)。

#### 响应信封

```json
{
  "status": "ok",
  "data": {
    "unstaged": true,
    "files": ["src/a.py"],
    "all": false,
    "directory": "...",
    "umo": "...",
    "worktree": "...",
    "unstaged_count": 1,
    "rejected": [],
    "elapsed_ms": 30,
    "reason": null,
    "stderr": ""
  }
}
```

#### git 调用

- **files 模式**:`git -C <dir> -c color.ui=never reset HEAD -- <files...>`
- **all 模式**:`git -C <dir> -c color.ui=never reset HEAD`(无 path args = unstage 全部)

#### 为什么用 `git reset HEAD` 而非 `git restore --staged`

项目既有 `file_restore.py` 已用 `git reset HEAD -- <file>`(line 290 附近);保持一致;`git restore --staged` 是新语法(git 2.23+),不强求兼容性。

#### 审计日志

```
[git-unstage] unstaged: files=[a.py] all=false worktree=<dir> umo=<umo> elapsed_ms=<n>
[git-unstage] failed: reason=<r> worktree=<dir> umo=<umo> elapsed_ms=<n>
```

---

### 端点 C:`POST /spcode/git-commit`(严格最小)

#### URL 契约

| 维度 | 值 |
|------|---|
| Method | **POST** |
| Path | `/spcode/git-commit` |
| Body | `{"message": "fix: handle ...", "umo": "...", "worktree": "..."}` |
| Content-Type | `application/json` |
| 鉴权 | 沿用 AstrBot 框架统一鉴权 |

#### body 校验规则

- `message` 必填;缺失 / 非字符串 / 仅空白 → `empty_message`
- `message` 最大长度 8192 字符(防滥发 / 日志爆炸)→ 超长 `message_too_long`
- **不接受** `amend` / `allow_empty` / `no_verify` / `signoff` / `gpgsign` / `author` / `date` 字段(出现即 `invalid_body`)
- 任何未知字段 → `invalid_body`(v1 严格白名单)
- **不**自动 stage 所有改动(若要一次提交全部,需先调 `/git-stage` `all:true`)

#### 响应信封

```json
// 成功
{
  "status": "ok",
  "data": {
    "committed": true,
    "sha": "a1b2c3d4e5f6...",         // 完整 40 字符 SHA
    "sha_short": "a1b2c3d",            // 前 7 字符(Dashboard 展示用)
    "message": "fix: handle null pointer in foo()\n\nDetailed explanation...",
    "message_first_line": "fix: handle null pointer in foo()",
    "directory": "...",
    "umo": "...",
    "worktree": "...",
    "elapsed_ms": 120,
    "reason": null,
    "stderr": ""
  }
}

// 失败
{ "status": "ok", "data": { "committed": false, "reason": "nothing_staged", ... } }
```

#### git 调用

- `git -C <dir> -c color.ui=never commit -m <message>`(一次性传 message)
- 不传 `--allow-empty` / `-a` / `--amend` / `-S`
- commit 成功后再 `git -C <dir> rev-parse HEAD` 取新 SHA(commit 的 stdout 不直接给 SHA;用 rev-parse 比解析更稳)

#### pre-commit hook 失败处理

- git commit 返回非 0 + stderr 含 "hook" / "pre-commit" → 归类 `pre_commit_hook_failed`
- git commit 返回非 0 + stderr 匹配 "nothing to commit" / "no changes added" → 归类 `nothing_staged`(dashboard 友好文案)
- 其它 git 错误 → `git_error`
- stderr 全文回显 `data.stderr`(截断到 2 KB,超出加 `"\n...[truncated, full=<N> bytes]"` 后缀)

#### 审计日志

```
[git-commit] committed: sha=<sha_short> message="<first_line_80chars>" worktree=<dir> umo=<umo> elapsed_ms=<n>
[git-commit] failed: reason=<r> worktree=<dir> umo=<umo> elapsed_ms=<n>
```

---

### 端点 D:`GET /spcode/git-log`(标准粒度)

#### URL 契约

| 维度 | 值 |
|------|---|
| Method | **GET** |
| Path | `/spcode/git-log` |
| Query | 见下表 |
| 鉴权 | 沿用 AstrBot 框架统一鉴权 |

#### Query 参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `n` | int | 20 | 返回 commit 上限;范围 [1, 200];越界自动截断(不报错) |
| `ref` | string | `HEAD` | 分支名 / 标签 / commit SHA / `main..feature` 范围 |
| `path` | string | (无) | 只返回影响此路径的 commits |
| `author` | string | (无) | 作者邮箱/名字匹配(传给 `git log --author=<a>`) |
| `since` | ISO date | (无) | 起始时间(含) |
| `until` | ISO date | (无) | 截止时间(含) |
| `umo` | string | (无) | 会话路由;省略时回退到最近 loaded project |
| `worktree` | string | (无) | 6 步防御 |

#### 参数校验

- `n` 越界 → 自动截到 [1, 200],不报错(前端传 1000 不会崩)
- `ref` / `path` / `author` 长度 > 512 → `invalid_param`
- `since` / `until` 不是 ISO 8601 格式 → `invalid_param`
- `path` 走 `_validate_repo_relative_file` 4 步防御 → `path_unsafe`
- 任何未知 query 参数 → 忽略(v1 宽松;POST 端点严格)

#### 响应信封

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "...",
    "umo": "...",
    "worktree": "...",
    "ref": "HEAD",
    "count": 3,                         // 实际返回 commit 数
    "has_more": false,                  // 是否还有未取的 commits
    "commits": [
      {
        "sha": "a1b2c3d4e5f6...",
        "sha_short": "a1b2c3d",
        "author": { "name": "elecvoid243", "email": "x@y.com" },
        "committer": { "name": "elecvoid243", "email": "x@y.com" },
        "date": "2026-06-23T22:00:00+08:00",   // ISO 8601
        "subject": "fix: handle null pointer in foo()",
        "body": "Detailed explanation...",  // 无 body 时为 null(不是空字符串)
        "parents": ["9f8e7d6c..."],
        "shortstat": { "files": 2, "additions": 10, "deletions": 3 }
      }
    ],
    "elapsed_ms": 35,
    "reason": null
  }
}
```

#### git 调用(4 合 1 模式)

- 单次 `git log --pretty=format:... --shortstat -n <N+1> <ref> [-- <path>] [--author=<a>] [--since=<s>] [--until=<u>]`
- `pretty=format` 模板: `%H%x00%h%x00%an%x00%ae%x00%cn%x00%ce%x00%aI%x00%cI%x00%s%x00%b%x00%P%x00`(11 字段 NUL 分隔)
- 解析在 Python 侧做(`_parse_log_format` + `_parse_log_shortstat`)
- `has_more` 判定:实际 `git log -n <N+1>` 拿 N+1 条,如果返回 N+1 → `has_more=True`,丢弃第 N+1 条
- 走 `_run_git_async`,asyncio 直接管理子进程,不占 worker

#### HTTP 缓存

- 复用 git-diff 的 1.5s TTL in-memory ETag 缓存(按 `directory` 维度)
- ETag 信号:`git rev-parse HEAD` + worktree 根目录 mtime + `.git/index` mtime
- 304 短路逻辑与 git-diff 完全相同

#### 审计日志(只读,不记录成功路径;失败 WARNING)

```
[git-log] failed: reason=<r> worktree=<dir> umo=<umo> elapsed_ms=<n>
```

---

## 安全防御层级

| 层 | 名称 | 实现位置 | 作用 |
|----|------|---------|------|
| L0 | Feature flag | handler 入口 | `agentsmd_enabled` ∧ `codegraph_enabled` 全局开关 |
| L1 | umo 解析 | handler | 关联到已加载项目;无项目 → no_project_loaded |
| L2 | worktree 6 步防御 | `_validate_worktree_param` | 防跨 worktree / 跨仓库 / 越界 symlink / 隐藏目录 |
| L3 | 文件路径 4 步防御 | `_validate_repo_relative_file` | 防 path traversal / .git 内部 / symlink 越界(仅 stage/unstage files 模式与 log path 参数触发) |
| L4 | git 命令白名单 | handler 内部 | 4 端点只调规定的子命令集;不接受任意 git 参数注入 |
| L5 | 审计日志 | handler 末尾 | 写操作(stage/unstage/commit)INFO 1 行;失败 WARNING + reason |
| L6 | 资源上限 | handler | log 的 `n` ≤ 200;message ≤ 8192 字符;files ≤ 1000 个 |

### git 参数注入防御

#### 路径参数(files / path / worktree)

- 4 步防御确保所有路径都是相对 worktree 的合法子路径
- 拼接到 `git -C <dir> ... -- <files>` 时**强制 `--` 分隔符**,防止 file 名撞 -x 类选项
- 例如:恶意 file 名 `--upload-pack=/tmp/x` → `--` 后变 path 参数,不会被解释为 git 选项

#### 标量参数(message / ref / author)

- `message`:直接走 `git commit -m "<message>"`;shell 元字符由 `_run_git_async` 的 `asyncio.create_subprocess_exec`(非 shell)天然免疫
- `ref` / `author`:长度 ≤ 512 字符;通过 `git log <ref> --author=<a>` 拼装
- `n`:整数类型校验,越界截断而非抛错

#### body JSON 注入

- POST body 由 `_wrap` 用 `await request.json() or {}` 解析,try/except 兜底
- 字段类型检查用 `isinstance(field, list)` / `isinstance(field, str)` / `isinstance(field, bool)`
- **不接受未知字段**(v1 严格):出现未知字段 → `invalid_body`

### stderr 截断规则

- 写操作(stage / unstage / commit):stderr 完整回显,**截断到 2 KB**(超出加 `"\n...[truncated, full=<N> bytes]"` 后缀)
- 读操作(log):stderr 不回显到 data(git 错误时仅 reason + 内部日志)
- 防止:恶意大 stderr 占满 dashboard 内存

### 错误响应格式

所有失败响应遵循同一 envelope:
```json
{
  "status": "ok",                  // 永远 "ok"(HTTP 层是 200)
  "data": {
    "<boolean_flag>": false,       // staged / unstaged / committed / loaded
    "reason": "<reason_code>",     // 必填
    "stderr": "<truncated stderr>",// 可选,写操作类必有
    "directory": "...",
    "umo": "...",
    "worktree": "...",
    "elapsed_ms": 12
  }
}
```

- 端点**永远**返回 HTTP 200
- 唯一例外:framework 内部错误(如 handler 自身崩了)→ 500

---

## 性能优化策略

| 端点 | 优化 |
|------|------|
| git-stage | 无缓存(写必须真打);单次 `git add` 调用 |
| git-unstage | 无缓存;单次 `git reset HEAD` 调用 |
| git-commit | 无缓存;2 次 git 调用(commit + rev-parse HEAD) |
| git-log | **1.5s TTL in-memory ETag 缓存**;1 次 git log 调用(N+1 trick 拿 has_more) |

所有 git 子进程走 `_run_git_async`(asyncio 直接管子进程,不占 worker 线程)。

---

## 测试策略

### 单元测试矩阵

| 文件 | 用例数 | 关键场景 |
|------|--------|---------|
| `tests/test_git_stage.py` | ~12 | 1) 精细 files 模式成功 2) all 模式成功 3) 两者都传→invalid_body 4) 都不传→invalid_body 5) invalid_files 6) invalid_all 7) path_unsafe(`.` 段、绝对路径、.git 内部)8) no_project_loaded 9) worktree_invalid 10) feature_disabled 11) not_a_git_repo 12) elapsed_ms 存在 |
| `tests/test_git_unstage.py` | ~10 | 与 stage 对称,少 1~2 个(git 行为差异点) |
| `tests/test_git_commit.py` | ~10 | 1) 成功 + sha/sha_short 正确 2) empty_message 3) message_too_long 4) nothing_staged 5) pre_commit_hook_failed 6) invalid_body(带未知字段)7) feature_disabled 8) no_project_loaded 9) worktree_invalid 10) git_error |
| `tests/test_git_log.py` | ~14 | 1) 默认参数 20 条 2) n=5 3) n 超界截断 4) ref=branch 5) path 过滤 6) path_unsafe 7) author 过滤 8) since/until 9) invalid_param 10) empty_repository 11) ETag 304 12) 1.5s TTL 缓存命中 13) worktree 6 步防御 14) SHA 解析正确(含 11 字段 NUL 分隔) |

### 集成测试

`tests/test_webapi_end_to_end.py` 新增:
- `test_routes_table_has_ten_endpoints`:ROUTES 集合断言(6 → 10)
- `test_handler_callable_returns_dict` 自动覆盖新 4 handler
- `_wrap` 现有 4 个测试不动(新 handler 自动复用)

### 测试基础设施

- **真实 git + tmp_path**(与 git-diff / file-restore 模式一致,**不** mock subprocess)
- `_init_git_repo(path)` helper 复用(已有于 conftest 或 test_git_diff)
- `_load_project(plugin, umo, directory)` helper:直接写 `tools.project.state.put`
- `_make_plugin()` from conftest 复用

### 关键测试代码示例

```python
# tests/test_git_stage.py 关键场景示例
async def test_stage_files_mode_calls_git_add_with_double_dash(plugin, tmp_path):
    """files 模式必须用 `git add -- <files>` 防参数注入。"""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "a.py").write_text("x")
    (repo / "b.md").write_text("y")
    _load_project(plugin, "test:umo", str(repo))
    
    result = await git_stage.handle(
        plugin,
        body={"files": ["a.py", "b.md"]},
    )
    assert result["data"]["staged"] is True
    assert result["data"]["staged_count"] == 2
    # 二次确认:git status 应显示两个文件 staged
    status = await _run_git_async(["git", "-C", str(repo), "status", "--porcelain"])
    assert "A  a.py" in status["stdout"]
    assert "A  b.md" in status["stdout"]


async def test_stage_rejects_unsafe_path(plugin, tmp_path):
    """`files` 含 `..` 段 → path_unsafe,不调 git。"""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    _load_project(plugin, "test:umo", str(repo))
    
    result = await git_stage.handle(
        plugin,
        body={"files": ["../escape.py"]},
    )
    assert result["data"]["staged"] is False
    assert result["data"]["reason"] == "path_unsafe"
```

---

## 实施拆分(6 个 PR)

### PR-1:基础设施提升(refactor-only,无新端点)

**目标**:抽取 `_git_endpoint_preflight` + `_make_envelope` + `ReasonCode` + 提升 `_validate_repo_relative_file`,file_restore 改用新共享函数

**文件**:
- 改:`tools/webapi/_helpers.py`(新增 4 个常量 / 函数 / 类)
- 改:`tools/webapi/file_restore.py`(薄壳委托,零行为变化)

**测试**:
- 改:`tests/test_file_restore.py`(确保所有现有用例继续 PASS)
- 改:`tests/test_webapi_end_to_end.py`(注册流程仍 6 端点)

**commit 拆分(4 个)**:
1. `refactor(webapi): add ReasonCode constants and _make_envelope factory`
2. `refactor(webapi): add _git_endpoint_preflight shared helper`
3. `refactor(webapi): promote _validate_restore_file to _helpers as _validate_repo_relative_file`
4. `refactor(file_restore): delegate to shared _validate_repo_relative_file`

### PR-2:`/spcode/git-log`(read-only 端点先上)

**理由**:GET 端点、纯读、最高风险最低,先发可以验证 PR-1 基础设施

**文件**:
- 新:`tools/webapi/git_log.py`
- 改:`tools/webapi/__init__.py`(import + ROUTES +1 + HANDLERS +1)
- 新:`tests/test_git_log.py`

**commit 拆分(4 个,TDD 风格)**:
1. `test(git-log): add failing tests for /spcode/git-log endpoint`
2. `feat(git-log): implement _parse_log_format and _parse_log_shortstat helpers`
3. `feat(git-log): implement handle() with preflight + 4-in-1 git call + 1.5s ETag cache`
4. `feat(git-log): register /spcode/git-log in webapi ROUTES`

### PR-3:`/spcode/git-stage`(第一个写端点)

**文件**:
- 新:`tools/webapi/git_stage.py`
- 改:`tools/webapi/__init__.py`
- 新:`tests/test_git_stage.py`

**commit 拆分(5 个)**:
1. `test(git-stage): add failing tests for files/all modes + invalid_body + path_unsafe`
2. `feat(git-stage): implement body validation (files / all mutex + type check)`
3. `feat(git-stage): implement preflight + git add [--] / git add -A dispatch`
4. `feat(git-stage): add audit log + 2KB stderr truncation`
5. `feat(git-stage): register /spcode/git-stage in webapi ROUTES`

### PR-4:`/spcode/git-unstage`(与 stage 对称,可复用大量代码)

**文件**:
- 新:`tools/webapi/git_unstage.py`
- 改:`tools/webapi/__init__.py`
- 新:`tests/test_git_unstage.py`

**commit 拆分(3 个)**:
1. `test(git-unstage): add failing tests mirroring git-stage`
2. `feat(git-unstage): implement handle() — symmetric to git-stage with git reset HEAD`
3. `feat(git-unstage): register /spcode/git-unstage in webapi ROUTES`

### PR-5:`/spcode/git-commit`(严格最小 + hook 处理)

**文件**:
- 新:`tools/webapi/git_commit.py`
- 改:`tools/webapi/__init__.py`
- 新:`tests/test_git_commit.py`

**commit 拆分(5 个)**:
1. `test(git-commit): add failing tests for happy path + empty/nothing_staged + hooks`
2. `feat(git-commit): implement message validation (empty/too_long + no extra fields)`
3. `feat(git-commit): implement preflight + git commit -m + rev-parse HEAD for sha`
4. `feat(git-commit): implement git error classification (hook vs nothing vs generic)`
5. `feat(git-commit): register /spcode/git-commit in webapi ROUTES`

### PR-6:端到端验证 + 文档

**文件**:
- 改:`tests/test_webapi_end_to_end.py`(更新断言 6→10,新增 4 handler smoke)
- 改:`README.md`(新增 4 端点说明表格)
- 改:`AGENTS.md`(目录结构图更新到 4 个新文件)

**commit 拆分(2 个)**:
1. `test(webapi): update end-to-end smoke for 10 endpoints`
2. `docs(readme): document new git-stage/unstage/commit/log endpoints`

### 依赖关系图

```
PR-1 (refactor infra)  ──┐
                         ├──→ PR-2 (git-log) ──┐
                         │                      ├──→ PR-6 (e2e + docs)
                         ├──→ PR-3 (git-stage) ─┤
                         │                      │
                         ├──→ PR-4 (git-unstage)┤
                         │                      │
                         └──→ PR-5 (git-commit)─┘
```

- PR-2/3/4/5 互不依赖,可并行(但建议串行便于 code review)
- PR-1 必须先于 PR-2~5
- 每个 PR 必须 `ruff check .` + `pytest tests/` 全绿

---

## 风险与回退

| 风险 | 影响 | 缓解 |
|------|------|------|
| 改写 file_restore 内部委托时行为漂移 | file_restore 测试失败 | PR-1 完成后完整跑 test_file_restore.py;同时保留旧函数作为 alias 兜底 |
| 4-in-1 git log 解析在 multiline commit message 边界出错 | log 返回错位数据 | 11 字段 NUL 分隔 + parser 强制断言字段数;测试覆盖含 `\n\n` 的 message |
| ETag 缓存让 dashboard 看不到刚 commit 的结果 | 误以为 commit 失败 | ETag 信号含 .git/index mtime,commit 必更新 index → 1 个 poll 周期内自动失效 |
| 性能 regression(4 端点都打 git) | dashboard 轮询开销 | 复用 _run_git_async 释放 worker;log 走 1.5s TTL 缓存;stage/unstage/commit 不缓存(写必须真打) |
| `files` 模式 + `--` 强制分隔符漏写 | 文件名撞 -x 选项被误解释 | 测试覆盖 `--upload-pack` 类恶意文件名;code review 重点 |

---

## 未来工作(超出本 spec 范围)

- **amend / allow-empty / GPG 旗标**:v1 严格最小;若 dashboard 需要"修改上次 commit"UX,后续开新 spec 加旗标
- **sign-off**:Contributor License Agreement 场景需要
- **多文件批量 commit**:目前只能 stage 多个文件 + commit 一次 message;若需要"一次 commit 一文件"模式,开新端点
- **/spcode/git-push**:`git commit` 后,可能需要 push;v1 不包含
- **worktree 内 `git log`**:本 spec 已支持 `?worktree=` 参数;若要"按 worktree 分组显示 history",需前端聚合

---

## 验收清单(全部 6 PR 完成后)

- [ ] `ruff check .` 0 error
- [ ] `pytest tests/` 100% PASS(新 4 文件 ~46 个新用例 + 既有 ~200 个)
- [ ] `tests/test_webapi_end_to_end.py -v` 10/10 handler smoke
- [ ] `README.md` 更新到 10 端点
- [ ] `AGENTS.md` 目录结构图更新到 4 个新文件
- [ ] 每个 PR squash 1 个 commit 后合入 main
- [ ] dashboard 真机 smoke:选文件 → stage → commit → log 看到新 commit
- [ ] 审计日志格式正确(INFO + 写操作结果)
- [ ] stderr 截断 2 KB 验证
- [ ] worktree 6 步防御 + 文件 4 步防御 攻击向量测试全绿
