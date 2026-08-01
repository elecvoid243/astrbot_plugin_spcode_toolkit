# Git Merge / Cherry-pick / Conflict Resolution — 端点设计

> **Spec ID**: 2026-07-28-git-merge-cherrypick-conflict
> **Author**: elecvoid243
> **Created**: 2026-07-28 18:02 CST
> **Updated**: 2026-07-28 19:24 CST（brainstorming 确认后重写）
> **Status**: Approved
> **Target Version**: v2.22.0
> **前置依赖**: v2.17.0 git-init/branch/revert 端点体系、`_git_endpoint_preflight` 5 步防御链
> **Frontend wiring**: [2026-08-01-git-merge-cherrypick-conflict-frontend-design.md](2026-08-01-git-merge-cherrypick-conflict-frontend-design.md)

---

## §0 Brainstorming 需求确认记录

| # | 问题 | 决策 |
|---|------|------|
| Q1 | 主要消费者 | Dashboard 前端为主 |
| Q2 | 冲突解决粒度 | Hunk 级（逐冲突块选择） |
| Q3 | 参数暴露范围 | 常用选项（merge: no_ff / squash / ff_only；cherry-pick: mainline） |
| Q4 | revert 冲突兼容 | 通用化（merge + cherry-pick + revert 三种操作） |
| Q5 | 冲突解析架构 | 混合（结构化 hunk + 三路原文） |
| Q6 | cherry-pick 粒度 | v1 仅单 commit |
| Q7 | git-status 集成 | 追加 operation + in_conflict 字段 |
| 方案 | 端点架构 | 方案 A：6 个原子端点，一文件一端点 |

---

## §1 背景与动机

当前 spcode 工具箱已覆盖 git 工作流的「创建 → 暂存 → 提交 → 回滚 → 分支管理」链路，
但缺少两个高频操作：

1. **合并（merge）**：将功能分支合回主干，是 PR 工作流的核心动作。
2. **拣选（cherry-pick）**：将特定 commit 跨分支搬运，hotfix 场景刚需。

两者都可能产生 **冲突（conflict）**。现有 `git-revert` 端点虽已处理 `revert_conflict`，
但 revert 冲突只需 `git revert --abort` 即可回退，用户无需逐文件解决。
merge / cherry-pick 冲突则不同——用户通常需要 **逐文件选择 ours/theirs/手动编辑**，
然后 **continue** 完成操作。因此需要一套完整的冲突生命周期管理端点。

### §1.1 设计原则

| 原则 | 说明 |
|------|------|
| 复用 preflight | 所有新端点共享 `_git_endpoint_preflight` 5 步防御链 |
| 复用 envelope | `_make_envelope` + `ReasonCode` + `_JSONResponseCompat` |
| 一文件一端点 | `tools/webapi/<name>.py` 内 `async def handle(plugin, ...)` |
| headless 安全 | 不开编辑器（`--no-edit` / `-F -`），不弹交互 prompt |
| 冲突状态机 | merge/cherry-pick/revert 冲突 → 逐文件 resolve → continue/abort |
| 最小参数 | 严格最小 body，不暴露 `--strategy-option` 等高级旗标（v1） |

---

## §2 端点总览与冲突状态机

### §2.1 端点清单

| # | 路由 | 方法 | 文件 | 用途 |
|---|------|------|------|------|
| 1 | `/spcode/git-merge` | POST | `git_merge.py` | 发起 merge |
| 2 | `/spcode/git-cherry-pick` | POST | `git_cherry_pick.py` | 发起 cherry-pick（单 commit） |
| 3 | `/spcode/git-conflict-status` | GET | `git_conflict_status.py` | 冲突状态 + hunk 详情 + 三路原文 |
| 4 | `/spcode/git-conflict-resolve` | POST | `git_conflict_resolve.py` | 解决冲突（按 hunk 选择 / 整文件 / custom） |
| 5 | `/spcode/git-conflict-continue` | POST | `git_conflict_continue.py` | 继续完成操作 |
| 6 | `/spcode/git-conflict-abort` | POST | `git_conflict_abort.py` | 中止操作 |

附加变更：`GET /spcode/git-status` 响应追加 `operation` + `in_conflict` 字段。

### §2.2 冲突状态机

```
NORMAL ──POST git-merge──────→ MERGE_CONFLICT (.git/MERGE_HEAD)
NORMAL ──POST git-cherry-pick→ CP_CONFLICT   (.git/CHERRY_PICK_HEAD)
NORMAL ──POST git-revert─────→ RV_CONFLICT   (.git/REVERT_HEAD)  ← 现有端点
                                        │
                    GET conflict-status ←┤  (查询冲突文件 + hunk)
                    POST conflict-resolve←┤  (逐文件/逐 hunk 解决)
                                        │
                    ┌───────────────────┴──────────────────┐
                    │                                      │
          POST conflict-continue                  POST conflict-abort
          (git commit / --continue)               (git merge/cp/revert --abort)
                    │                                      │
                    ▼                                      ▼
              NORMAL (已提交)                        NORMAL (已回退)
```

### §2.3 操作类型探测

通过 `.git/` 下哨兵文件判断（权威来源，优于 porcelain 状态码）：

```python
async def _detect_conflict_operation(git_bin: str, directory: str) -> str | None:
    """探测当前冲突操作类型。

    Returns:
        "merge" | "cherry_pick" | "revert" | None
    """
    git_dir_result = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--git-dir"],
        encoding="utf-8",
    )
    if not git_dir_result.get("ok"):
        return None
    git_dir = Path(git_dir_result["stdout"].strip())
    if not git_dir.is_absolute():
        git_dir = Path(directory) / git_dir

    if (git_dir / "MERGE_HEAD").exists():
        return "merge"
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        return "cherry_pick"
    if (git_dir / "REVERT_HEAD").exists():
        return "revert"
    return None
```

> **WHY**: `git status --porcelain` 的 `UU`/`AA` 标记也能检测冲突，但无法区分
> merge vs cherry-pick vs revert。哨兵文件是 git 内部状态机的权威来源。

### §2.4 冲突文件列表

porcelain 中冲突相关的 XY 状态码：

| XY | 含义 |
|----|------|
| `UU` | 双方都修改了同一文件 |
| `AA` | 双方都新增了同名文件 |
| `DD` | 双方都删除了同一文件 |
| `AU` | 我方新增，对方修改 |
| `UA` | 我方修改，对方新增 |
| `DU` | 我方删除，对方修改 |
| `UD` | 我方修改，对方删除 |

---

## §3 POST `/spcode/git-merge`

### §3.1 Body

```json
{
  "source": "feature/login",
  "message": "",
  "no_ff": false,
  "ff_only": false,
  "squash": false,
  "umo": "...",
  "worktree": "..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `source` | str | ✅ | — | 要合并的 ref（branch / tag / SHA） |
| `message` | str | ❌ | `""` | 自定义 merge commit message（空 = git 默认） |
| `no_ff` | bool | ❌ | `false` | `--no-ff`：强制生成 merge commit |
| `ff_only` | bool | ❌ | `false` | `--ff-only`：仅允许 fast-forward，否则失败 |
| `squash` | bool | ❌ | `false` | `--squash`：压缩为 staged 改动，不自动提交 |
| `umo` / `worktree` | str | ❌ | — | 标准 preflight 参数 |

**跨字段互斥**：
- `no_ff` + `ff_only` → `invalid_body`（语义矛盾）
- `squash` + `no_ff` → `invalid_body`（squash 不产生 commit，no_ff 无意义）
- `squash` + `ff_only` → `invalid_body`（同理）

### §3.2 处理流程（8 步）

```
1. body 校验
   - source 非空 str
   - bool 字段类型检查
   - 跨字段互斥检查
   - message 长度 ≤ 8192

2. ref-format 校验（_is_valid_ref_name(source)）
   → 失败: invalid_branch

3. _git_endpoint_preflight（5 步防御链）
   → 失败: feature_disabled / no_project_loaded / worktree_invalid /
           directory_missing / not_a_git_repo / git_unavailable

4. 冲突状态前置检查
   _detect_conflict_operation ≠ None → operation_in_progress
   （防止在已有 merge/cp/revert 冲突未解决时发起新操作）

5. worktree 脏检查
   git status --porcelain 非空 → worktree_dirty
   例外：squash=true 时允许脏工作区（squash 只 stage 不 commit）

6. 执行 git merge
   args = [git, -C, dir, -c, color.ui=never, merge, --no-edit]
   + [--no-ff | --ff-only | --squash]
   + [-m, message]（message 非空时）
   + [source]
   timeout = 30s

7. 结果分类
   a. returncode=0:
      - stdout 含 "Already up to date" → merge_already_up_to_date（success=true 但 merged=false）
      - stdout 含 "Fast-forward" → 成功，fast_forward=true
      - 否则 → 成功，fast_forward=false（生成了 merge commit）
   b. returncode≠0:
      - _classify_merge_stderr → merge_conflict / unrelated_histories /
        worktree_dirty / ref_not_found / git_error
      - 若 merge_conflict：附带 conflicted_files 列表

8. 成功回读
   - rev-parse HEAD → merge_sha
   - log -1 --pretty=%s → merge_message
   - show --name-only --pretty= HEAD → files_touched
   - _read_post_mutation_branch_state → current / detached / branches / total
```

### §3.3 响应

**成功（clean merge）**：

```json
{
  "status": "ok",
  "data": {
    "merged": true,
    "source": "feature/login",
    "merge_sha": "a1b2c3d4...",
    "merge_message": "Merge branch 'feature/login'",
    "fast_forward": false,
    "squash": false,
    "files_touched": ["src/auth.py", "src/login.py"],
    "current": "main",
    "detached": false,
    "branches": [],
    "total": 0,
    "reason": null,
    "stderr": "",
    "elapsed_ms": 120
  }
}
```

**已最新（无需合并）**：

```json
{
  "status": "ok",
  "data": {
    "merged": false,
    "source": "feature/login",
    "reason": "merge_already_up_to_date",
    "stderr": "",
    "elapsed_ms": 45
  }
}
```

> `success=False` + `reason=merge_already_up_to_date`（与 `nothing_to_commit` /
> `nothing_to_revert` 模式一致：操作未产生预期效果 → success=False + 具体 reason）。
> git 正常退出（returncode=0），不是错误。前端可 toast 提示。

**成功（squash）**：

```json
{
  "status": "ok",
  "data": {
    "merged": false,
    "source": "feature/login",
    "squash": true,
    "files_touched": ["src/auth.py"],
    "reason": null,
    "elapsed_ms": 80
  }
}
```

> squash 不产生 commit，`merged=false`。改动已 staged，
> 前端应引导用户走 `POST /spcode/git-commit` 完成提交。

**冲突**：

```json
{
  "status": "ok",
  "data": {
    "merged": false,
    "source": "feature/login",
    "conflict": true,
    "operation": "merge",
    "conflicted_files": [
      {"path": "src/auth.py", "status": "UU"},
      {"path": "src/config.py", "status": "AU"}
    ],
    "reason": "merge_conflict",
    "stderr": "CONFLICT (content): Merge conflict in src/auth.py...",
    "elapsed_ms": 95
  }
}
```

### §3.4 `_classify_merge_stderr` 分类器

```python
def _classify_merge_stderr(stderr: str, stdout: str = "") -> str:
    combined = (stderr + stdout).lower()
    if "conflict" in combined:
        return ReasonCode.MERGE_CONFLICT
    if "unrelated histories" in combined:
        return ReasonCode.UNRELATED_HISTORIES
    if "not something we can merge" in combined or "unknown revision" in combined:
        return ReasonCode.REF_NOT_FOUND
    if "your local changes" in combined or "would be overwritten" in combined:
        return ReasonCode.WORKTREE_DIRTY
    # 复用 commit 分类器处理 hook/identity 错误
    from .git_commit import _classify_commit_error
    classified = _classify_commit_error(stderr, returncode=-1)
    if classified != ReasonCode.GIT_ERROR:
        return classified
    return ReasonCode.GIT_ERROR
```

> **NOTE**: `git merge` 在冲突时 returncode=1，但 "Already up to date" 时
> returncode=0。需要在 `result["ok"]` 为 True 时也检查 stdout 中的
> "Already up to date" 以返回 `merge_already_up_to_date`。

---

## §4 POST `/spcode/git-cherry-pick`

### §4.1 Body

```json
{
  "ref": "a1b2c3d",
  "mainline": null,
  "umo": "...",
  "worktree": "..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `ref` | str | ✅ | — | 要拣选的 commit（SHA / ref） |
| `mainline` | int \| null | ❌ | `null` | 合并 commit 的父编号（`-m <n>`，从 1 开始）；拣选普通 commit 时为 null |
| `umo` / `worktree` | str | ❌ | — | 标准 preflight 参数 |

> 不暴露 `no_edit` 字段——headless 服务**强制** `--no-edit`，
> 与 `git-revert` 端点保持一致。不暴露 `--no-commit`（v1 严格最小，
> 拣选 = 自动产生 commit）。

### §4.2 处理流程（7 步）

```
1. body 校验
   - ref 非空 str
   - mainline: null 或正整数（≥1）；非 int / ≤0 → invalid_param

2. _git_endpoint_preflight（5 步防御链）

3. 冲突状态前置检查
   _detect_conflict_operation ≠ None → operation_in_progress

4. ref 是 commit 校验
   _is_commit_ref(git_bin, directory, ref)
   → 失败: commit_not_found

5. worktree 脏检查
   git status --porcelain 非空 → worktree_dirty

6. 执行 git cherry-pick
   args = [git, -C, dir, -c, color.ui=never, cherry-pick, --no-edit]
   + [-m, str(mainline)]（mainline 非 null 时）
   + [ref]
   timeout = 30s

7. 结果分类 + 成功回读
   a. returncode=0:
      - rev-parse HEAD → new_sha
      - log -1 --pretty=%s → original_message
      - show --name-only --pretty= HEAD → files_touched
   b. returncode≠0:
      - _classify_cherry_pick_stderr → cherry_pick_conflict /
        cherry_pick_empty / commit_not_found / worktree_dirty / git_error
      - 若 cherry_pick_conflict：附带 conflicted_files 列表
```

### §4.3 响应

**成功**：

```json
{
  "status": "ok",
  "data": {
    "picked": true,
    "ref": "a1b2c3d",
    "new_sha": "e5f6a7b8...",
    "original_message": "fix: login timeout",
    "files_touched": ["src/auth.py"],
    "reason": null,
    "stderr": "",
    "elapsed_ms": 80
  }
}
```

**冲突**：

```json
{
  "status": "ok",
  "data": {
    "picked": false,
    "ref": "a1b2c3d",
    "conflict": true,
    "operation": "cherry_pick",
    "conflicted_files": [
      {"path": "src/auth.py", "status": "UU"}
    ],
    "reason": "cherry_pick_conflict",
    "stderr": "CONFLICT (content): Merge conflict in src/auth.py",
    "elapsed_ms": 65
  }
}
```

**空 commit**：

```json
{
  "status": "ok",
  "data": {
    "picked": false,
    "ref": "a1b2c3d",
    "reason": "cherry_pick_empty",
    "stderr": "The previous cherry-pick is now empty, possibly due to conflict resolution.",
    "elapsed_ms": 40
  }
}
```

> 空 commit 时 git 不会留下 `CHERRY_PICK_HEAD`（自动中止），
> 仓库回到 NORMAL 状态。前端 toast 提示即可，无需进入冲突解决流程。

### §4.4 `_classify_cherry_pick_stderr` 分类器

```python
def _classify_cherry_pick_stderr(stderr: str) -> str:
    s = stderr.lower()
    if "conflict" in s:
        return ReasonCode.CHERRY_PICK_CONFLICT
    if "empty commit" in s or "now empty" in s:
        return ReasonCode.CHERRY_PICK_EMPTY
    if "bad object" in s or "unknown revision" in s:
        return ReasonCode.COMMIT_NOT_FOUND
    if "your local changes" in s or "would be overwritten" in s:
        return ReasonCode.WORKTREE_DIRTY
    # 复用 commit 分类器处理 hook/identity 错误
    from .git_commit import _classify_commit_error
    classified = _classify_commit_error(stderr, returncode=-1)
    if classified != ReasonCode.GIT_ERROR:
        return classified
    return ReasonCode.GIT_ERROR
```

---

## §5 GET `/spcode/git-conflict-status`

### §5.1 Query 参数

| 参数 | 类型 | 必传 | 说明 |
|------|------|------|------|
| `umo` | str | ❌ | 会话标识 |
| `worktree` | str | ❌ | worktree 路径 |

### §5.2 处理流程

```
1. _git_endpoint_preflight（5 步防御链）

2. _detect_conflict_operation → operation 类型
   若 None → 返回 in_conflict=false（正常 200，轻量响应）

3. _read_operation_ref → operation_ref（SHA + subject）
   读取 .git/MERGE_HEAD | CHERRY_PICK_HEAD | REVERT_HEAD

4. _list_conflicted_files → 未解决文件列表
   git diff --name-only --diff-filter=U -z
   + git status --porcelain 解析 XY 状态码

5. 已解决文件列表
   git diff --cached --name-only -z（已 staged 的文件）
   排除 unmerged 列表中的路径 → resolved_files

6. 对每个冲突文件（上限 20 个）：
   a. _parse_conflict_hunks(file_path)
      读取工作区文件，解析 <<<<<<< / ======= / >>>>>>> 标记
      → 结构化 hunk 列表
   b. _read_three_way(git_bin, directory, file)
      git show :1:<file>  → base（共同祖先）
      git show :2:<file>  → ours（当前分支）
      git show :3:<file>  → theirs（合入分支）
      某一路不存在时（如 AA 冲突无 base）返回 null

7. 组装响应
```

### §5.3 冲突 Hunk 解析（`_parse_conflict_hunks`）

解析工作区文件中的冲突标记，支持两种格式：

**标准格式**（`merge.conflictStyle=merge`，默认）：
```
<<<<<<< HEAD
ours content
=======
theirs content
>>>>>>> feature/login
```

**diff3 格式**（`merge.conflictStyle=diff3`）：
```
<<<<<<< HEAD
ours content
||||||| merged common ancestors
base content
=======
theirs content
>>>>>>> feature/login
```

解析输出：

```python
@dataclass
class ConflictHunk:
    index: int           # hunk 序号（0-based）
    start_line: int      # 冲突块起始行号（1-based，含 <<<<<<< 行）
    end_line: int        # 冲突块结束行号（1-based，含 >>>>>>> 行）
    ours: str            # ours 侧内容
    theirs: str          # theirs 侧内容
    base: str | None     # base 侧内容（diff3 格式有，标准格式为 None）
    ours_label: str      # <<<<<<< 后的标签（如 "HEAD"）
    theirs_label: str    # >>>>>>> 后的标签（如 "feature/login"）
```

**边界处理**：
- 二进制文件（含 `\x00`）→ 不解析 hunk，`hunks: []`，`binary: true`
- 文件 > 1 MB → 不解析 hunk，`hunks: []`，`truncated: true`
- 无冲突标记（已被手动编辑解决但未 `git add`）→ `hunks: []`
- 嵌套/畸形标记 → 尽力解析，畸形部分作为 `ours` 文本保留

### §5.4 三路原文（`_read_three_way`）

```python
async def _read_three_way(git_bin, directory, file) -> dict:
    """通过 git show :N:<file> 获取三路原文。

    Returns:
        {"base": str|None, "ours": str|None, "theirs": str|None}
    """
    # :1: = base (common ancestor)  — AA 冲突时可能不存在
    # :2: = ours (current branch)
    # :3: = theirs (incoming branch) — 某些冲突类型可能不存在
```

每路内容上限 1 MB，超限返回 `null` + `truncated: true`。

### §5.5 响应

**有冲突**：

```json
{
  "status": "ok",
  "data": {
    "in_conflict": true,
    "operation": "merge",
    "operation_ref": "a1b2c3d4",
    "operation_subject": "feat: add login page",
    "conflicted_files": [
      {
        "path": "src/auth.py",
        "status": "UU",
        "hunks": [
          {
            "index": 0,
            "start_line": 12,
            "end_line": 18,
            "ours": "def login(user):\n    return auth_v1(user)\n",
            "theirs": "def login(user):\n    return auth_v2(user)\n",
            "base": "def login(user):\n    pass\n",
            "ours_label": "HEAD",
            "theirs_label": "feature/login"
          },
          {
            "index": 1,
            "start_line": 45,
            "end_line": 50,
            "ours": "TIMEOUT = 30\n",
            "theirs": "TIMEOUT = 60\n",
            "base": null,
            "ours_label": "HEAD",
            "theirs_label": "feature/login"
          }
        ],
        "three_way": {
          "base": "def login(user):\n    pass\n\n# ... full file ...\n",
          "ours": "def login(user):\n    return auth_v1(user)\n\n# ... full file ...\n",
          "theirs": "def login(user):\n    return auth_v2(user)\n\n# ... full file ...\n"
        },
        "binary": false,
        "truncated": false
      },
      {
        "path": "logo.png",
        "status": "UU",
        "hunks": [],
        "three_way": {"base": null, "ours": null, "theirs": null},
        "binary": true,
        "truncated": false
      }
    ],
    "resolved_files": ["src/utils.py"],
    "total_conflicted": 2,
    "total_resolved": 1,
    "all_resolved": false,
    "reason": null,
    "elapsed_ms": 55
  }
}
```

**无冲突**：

```json
{
  "status": "ok",
  "data": {
    "in_conflict": false,
    "operation": null,
    "operation_ref": null,
    "operation_subject": null,
    "conflicted_files": [],
    "resolved_files": [],
    "total_conflicted": 0,
    "total_resolved": 0,
    "all_resolved": true,
    "reason": null,
    "elapsed_ms": 8
  }
}
```

### §5.6 性能约束

| 约束 | 值 | 说明 |
|------|------|------|
| 冲突文件 hunk 解析上限 | 20 个文件 | 超出部分只返回 path + status，`hunks: []` |
| 单文件 hunk 解析上限 | 1 MB | 超限 `truncated: true`，`hunks: []` |
| 三路原文单路上限 | 1 MB | 超限该路返回 `null` |
| git show 超时 | 5s / 路 | 三路共 15s 最坏 |

> **WHY 20 个文件上限**：大规模冲突（>20 文件）通常意味着分支严重分叉，
> 逐文件 hunk 解析的响应体会膨胀到 MB 级。前端应提示用户考虑 abort + 重新规划合并策略。

---

## §6 POST `/spcode/git-conflict-resolve`

### §6.1 Body（4 种模式）

**模式 1：按 hunk 选择（核心场景）**
```json
{
  "file": "src/auth.py",
  "hunks": [
    {"index": 0, "choice": "ours"},
    {"index": 1, "choice": "theirs"}
  ]
}
```

**模式 2：整文件快速选择**
```json
{
  "file": "src/auth.py",
  "resolution": "theirs"
}
```

**模式 3：自定义内容（前端 merge editor 编辑后提交）**
```json
{
  "file": "src/auth.py",
  "resolution": "custom",
  "content": "def login(user):\n    return auth_v2(user)\n..."
}
```

**模式 4：批量整文件（全部冲突文件统一选择）**
```json
{
  "all": true,
  "resolution": "ours"
}
```

### §6.2 字段说明

| 字段 | 类型 | 必传 | 说明 |
|------|------|------|------|
| `file` | str | 与 `all` 互斥 | 冲突文件的 repo-relative 路径 |
| `all` | bool | 与 `file` 互斥 | 解决所有冲突文件（仅支持 `resolution=ours/theirs`） |
| `hunks` | list[dict] | 模式 1 | 逐 hunk 选择：`{index: int, choice: "ours"\|"theirs"\|"base"}` |
| `resolution` | str | 模式 2/3/4 | `"ours"` / `"theirs"` / `"custom"` |
| `content` | str | `resolution="custom"` 时必传 | 完整文件内容（UTF-8，≤ 1 MB） |
| `umo` / `worktree` | str | ❌ | 标准 preflight 参数 |

**互斥校验**：
- `file` 和 `all` 必须且只能提供一个 → 否则 `invalid_body`
- `hunks` 和 `resolution` 互斥（不能同时提供）→ 否则 `invalid_body`
- `all=true` + `resolution="custom"` → `invalid_body`（批量不支持 custom）
- `all=true` + `hunks` → `invalid_body`（批量不支持逐 hunk）
- `resolution="custom"` + `content` 缺失 → `invalid_body`

### §6.3 处理流程

```
1. body 校验（互斥规则 + 类型检查 + content 大小 ≤ 1MB）

2. _git_endpoint_preflight（5 步防御链）

3. _detect_conflict_operation → 必须非 None
   → 否则 no_conflict_in_progress

4. 单文件模式（file 提供时）：
   a. _validate_repo_relative_file（4 步路径防御）
   b. 确认文件在 unmerged 列表中（git diff --name-only --diff-filter=U）
      → 否则 file_not_conflicted
   c. 根据模式执行解决：

      模式 1（hunks）：
        - 读取工作区文件
        - _parse_conflict_hunks 获取 hunk 列表
        - 校验每个 hunks[i].index 在范围内 → 否则 invalid_param
        - 校验所有 hunk 都有 choice（未提及的 hunk 保持冲突标记不变）
          → 若存在未解决的 hunk → 不执行 git add，返回 remaining hunks
        - 重建文件：非冲突区域原样保留，冲突区域按 choice 替换
          choice="ours" → 保留 ours 文本
          choice="theirs" → 保留 theirs 文本
          choice="base" → 保留 base 文本（diff3 格式下可用）
        - 写回文件（UTF-8）

      模式 2（整文件 ours/theirs）：
        - git checkout --ours -- <file>  或  git checkout --theirs -- <file>

      模式 3（custom）：
        - 将 content 写入文件（UTF-8）

   d. git add <file>

5. 批量模式（all=true）：
   a. 获取所有 unmerged 文件列表
   b. 对每个文件执行 git checkout --ours/--theirs -- <file>
   c. git add --all

6. 回读剩余冲突文件列表
   git diff --name-only --diff-filter=U -z
```

### §6.4 Hunk 重建算法（模式 1 核心）

```python
def _rebuild_file_from_hunks(
    original_lines: list[str],
    hunks: list[ConflictHunk],
    choices: dict[int, str],  # {hunk_index: "ours"|"theirs"|"base"}
) -> str | None:
    """根据逐 hunk 选择重建文件内容。

    算法：
    1. 按 start_line 排序 hunks
    2. 遍历 original_lines：
       - 非冲突区域 → 原样输出
       - 冲突区域（start_line ~ end_line）→ 按 choice 输出对应侧内容
    3. 若某个 hunk 没有 choice → 保留原始冲突标记（未解决）

    Returns:
        重建后的完整文件内容；若存在未解决的 hunk 则返回 None
    """
```

> **关键不变量**：只有当**所有** hunk 都有 choice 时才写回文件 + `git add`。
> 如果只解决了部分 hunk，返回 `partial: true` + 剩余 hunk 列表，
> 前端可以继续调用解决剩余部分。

### §6.5 响应

**全部解决**：

```json
{
  "status": "ok",
  "data": {
    "resolved": true,
    "file": "src/auth.py",
    "mode": "hunks",
    "hunks_resolved": 2,
    "hunks_total": 2,
    "partial": false,
    "remaining_conflicts": [
      {"path": "src/config.py", "status": "AU"}
    ],
    "all_resolved": false,
    "reason": null,
    "elapsed_ms": 45
  }
}
```

**部分解决（模式 1，只解决了部分 hunk）**：

```json
{
  "status": "ok",
  "data": {
    "resolved": false,
    "file": "src/auth.py",
    "mode": "hunks",
    "hunks_resolved": 1,
    "hunks_total": 2,
    "partial": true,
    "unresolved_hunks": [
      {"index": 1, "start_line": 45, "end_line": 50}
    ],
    "remaining_conflicts": [
      {"path": "src/auth.py", "status": "UU"},
      {"path": "src/config.py", "status": "AU"}
    ],
    "all_resolved": false,
    "reason": null,
    "elapsed_ms": 30
  }
}
```

> `partial=true` 时文件**未** `git add`，仍在 unmerged 列表中。
> 前端可以再次调用，传入剩余 hunk 的 choice。

**批量**：

```json
{
  "status": "ok",
  "data": {
    "resolved": true,
    "file": null,
    "mode": "all",
    "resolution": "theirs",
    "files_resolved": 3,
    "remaining_conflicts": [],
    "all_resolved": true,
    "reason": null,
    "elapsed_ms": 120
  }
}
```

### §6.6 安全约束

| 约束 | 说明 |
|------|------|
| `content` 大小 | ≤ 1 MB，超限 → `invalid_param` |
| 路径防御 | `_validate_repo_relative_file` 4 步（`..` / 绝对 / `.git/` / symlink） |
| 二进制文件 | 不支持 hunk 模式（`hunks` + 二进制 → `invalid_param`），仅支持整文件 ours/theirs |
| `choice="base"` | 仅在 diff3 格式（`base` 非 null）时合法，标准格式下 → `invalid_param` |

---

## §7 POST `/spcode/git-conflict-continue`

### §7.1 Body

```json
{
  "message": "",
  "umo": "...",
  "worktree": "..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `message` | str | ❌ | `""` | 自定义 commit message（空 = git 默认） |
| `umo` / `worktree` | str | ❌ | — | 标准 preflight 参数 |

### §7.2 处理流程（7 步）

```
1. body 校验（message 类型 + 长度 ≤ 8192）

2. _git_endpoint_preflight（5 步防御链）

3. _detect_conflict_operation → 必须非 None
   → 否则 no_conflict_in_progress

4. 检查是否还有 unmerged 文件
   git diff --name-only --diff-filter=U -z
   → 非空则 unresolved_conflicts_remain（附带剩余文件列表）

5. 根据 operation 类型执行：
   - merge:       git commit --no-edit [-F -]（message 非空时从 stdin 读）
   - cherry_pick: git cherry-pick --continue --no-edit
   - revert:      git revert --continue --no-edit
   透传 GIT_AUTHOR_* 环境变量（复用 _build_git_env）
   timeout = 30s

6. 结果分类（复用 _classify_commit_error）
   → hook_rejected / identity_not_set / nothing_to_commit / git_error

7. 成功回读
   - rev-parse HEAD → commit_sha
   - log -1 --pretty=%s → commit_message
   - show --name-only --pretty= HEAD → files_touched
   - _read_post_mutation_branch_state → current / detached / branches / total
```

> **NOTE**: `git merge --continue` 等价于 `git commit`（merge 状态下），
> 但 `git cherry-pick --continue` 和 `git revert --continue` 是独立命令，
> 不能替换为 `git commit`。三者必须按 operation 类型分发。

### §7.3 响应

**成功**：

```json
{
  "status": "ok",
  "data": {
    "continued": true,
    "operation": "merge",
    "commit_sha": "c3d4e5f6...",
    "commit_message": "Merge branch 'feature/login'",
    "files_touched": ["src/auth.py", "src/config.py"],
    "current": "main",
    "detached": false,
    "branches": [],
    "total": 0,
    "reason": null,
    "stderr": "",
    "elapsed_ms": 110
  }
}
```

**未解决冲突**：

```json
{
  "status": "ok",
  "data": {
    "continued": false,
    "operation": "merge",
    "remaining_conflicts": [
      {"path": "src/config.py", "status": "AU"}
    ],
    "reason": "unresolved_conflicts_remain",
    "elapsed_ms": 20
  }
}
```

---

## §8 POST `/spcode/git-conflict-abort`

### §8.1 Body

```json
{
  "umo": "...",
  "worktree": "..."
}
```

无业务参数，仅需标准 preflight 参数。

### §8.2 处理流程（5 步）

```
1. _git_endpoint_preflight（5 步防御链）

2. _detect_conflict_operation → 必须非 None
   → 否则 no_conflict_in_progress

3. 根据 operation 类型执行：
   - merge:       git merge --abort
   - cherry_pick: git cherry-pick --abort
   - revert:      git revert --abort
   timeout = 15s

4. 验证哨兵文件已消失（兜底）
   重新 _detect_conflict_operation → 应为 None
   若仍存在 → git_error（abort 未生效，极端情况）

5. 回读分支状态
   _read_post_mutation_branch_state → current / detached / branches / total
```

### §8.3 响应

```json
{
  "status": "ok",
  "data": {
    "aborted": true,
    "operation": "merge",
    "operation_ref": "a1b2c3d4",
    "current": "main",
    "detached": false,
    "branches": [],
    "total": 0,
    "reason": null,
    "stderr": "",
    "elapsed_ms": 55
  }
}
```

### §8.4 幂等性说明

abort 是**非幂等**的：第二次调用时哨兵文件已消失，返回 `no_conflict_in_progress`。
这是正确行为——前端收到此 reason 后应刷新状态，而非重试。

---

## §9 git-status 追加字段

在现有 `GET /spcode/git-status` 的 handler 中，preflight 成功后追加：

```python
# 在组装响应前，轻量探测冲突状态
operation = await _detect_conflict_operation(git_bin, directory)
# ...
return _make_envelope(
    success=True,
    # ... 现有字段 ...
    operation=operation,           # "merge"|"cherry_pick"|"revert"|None
    in_conflict=operation is not None,
)
```

**向后兼容**：旧前端忽略新字段即可。新增字段不影响现有 `branch` /
`upstream` / `staged` / `unstaged` / `untracked` 字段。
探测成本：一次 `git rev-parse --git-dir` + 最多 3 次 `Path.exists()`，< 1ms。

---

## §10 新增 ReasonCode

在 `tools/webapi/_helpers.py` 的 `ReasonCode` 类中追加：

```python
# ── v2.22.0 新增：git-merge / cherry-pick / conflict（2026-07-28）──
# git-merge (3)
MERGE_CONFLICT = "merge_conflict"
MERGE_ALREADY_UP_TO_DATE = "merge_already_up_to_date"
UNRELATED_HISTORIES = "unrelated_histories"

# git-cherry-pick (2)
CHERRY_PICK_CONFLICT = "cherry_pick_conflict"
CHERRY_PICK_EMPTY = "cherry_pick_empty"

# conflict lifecycle (4)
OPERATION_IN_PROGRESS = "operation_in_progress"
NO_CONFLICT_IN_PROGRESS = "no_conflict_in_progress"
FILE_NOT_CONFLICTED = "file_not_conflicted"
UNRESOLVED_CONFLICTS_REMAIN = "unresolved_conflicts_remain"
```

共计 **9 个**新 ReasonCode。

---

## §11 共享 Helper 新增

在 `tools/webapi/_helpers.py` 中追加：

| Helper | 签名 | 用途 |
|--------|------|------|
| `_detect_conflict_operation` | `async (git_bin, directory) -> str \| None` | 探测冲突操作类型 |
| `_list_conflicted_files` | `async (git_bin, directory) -> list[dict]` | 列出 unmerged 文件 + XY 状态 |
| `_parse_conflict_hunks` | `(file_content: str) -> list[ConflictHunk]` | 解析冲突标记为结构化 hunk |
| `_read_three_way` | `async (git_bin, directory, file) -> dict` | git show :1/:2/:3 三路原文 |
| `_read_operation_ref` | `async (git_bin, directory, operation) -> str` | 读取哨兵文件 SHA + subject |
| `_classify_merge_stderr` | `(stderr, stdout) -> str` | merge 失败分类 |
| `_classify_cherry_pick_stderr` | `(stderr) -> str` | cherry-pick 失败分类 |

---

## §12 路由表变更

`tools/webapi/__init__.py` 的 `ROUTES` 列表追加 6 条：

```python
(
    "/spcode/git-merge",  # v2.22.0 (2026-07-28)
    ["POST"],
    git_merge.handle,
    "git merge <source>（合并分支/tag/SHA 到当前 HEAD）",
),
(
    "/spcode/git-cherry-pick",  # v2.22.0 (2026-07-28)
    ["POST"],
    git_cherry_pick.handle,
    "git cherry-pick <ref>（拣选单 commit 到当前 HEAD）",
),
(
    "/spcode/git-conflict-status",  # v2.22.0 (2026-07-28)
    ["GET"],
    git_conflict_status.handle,
    "查询当前冲突状态（操作类型 + 冲突文件 + hunk 详情 + 三路原文）",
),
(
    "/spcode/git-conflict-resolve",  # v2.22.0 (2026-07-28)
    ["POST"],
    git_conflict_resolve.handle,
    "解决冲突文件（按 hunk 选择 / 整文件 ours/theirs / custom content）",
),
(
    "/spcode/git-conflict-continue",  # v2.22.0 (2026-07-28)
    ["POST"],
    git_conflict_continue.handle,
    "冲突全部解决后继续完成 merge/cherry-pick/revert",
),
(
    "/spcode/git-conflict-abort",  # v2.22.0 (2026-07-28)
    ["POST"],
    git_conflict_abort.handle,
    "中止当前 merge/cherry-pick/revert 操作",
),
```

`HANDLERS` 别名表追加：

```python
"handle_post_git_merge": git_merge.handle,
"handle_post_git_cherry_pick": git_cherry_pick.handle,
"handle_get_git_conflict_status": git_conflict_status.handle,
"handle_post_git_conflict_resolve": git_conflict_resolve.handle,
"handle_post_git_conflict_continue": git_conflict_continue.handle,
"handle_post_git_conflict_abort": git_conflict_abort.handle,
```

路由总数：40 → **46**（唯一路径 38 → **44**）。

---

## §13 安全约束汇总

### §13.1 继承的防御链

所有 6 个端点共享：

- **5 步 preflight**（`_git_endpoint_preflight`）：feature flag → umo 解析 → worktree 6 步防御 → 目录存在 → git repo 探测
- **路径 4 步防御**（`_validate_repo_relative_file`）：`..` / 绝对路径 / `.git/` / symlink 越界
- **ref-format 校验**（`_is_valid_ref_name`）：拒绝注入字符
- **commit 校验**（`_is_commit_ref`）：`^{commit}` 拒绝 tree/blob

### §13.2 新增安全约束

| 约束 | 适用端点 | 说明 |
|------|----------|------|
| 冲突状态前置检查 | merge / cherry-pick | 发起前必须无进行中操作 → `operation_in_progress` |
| 冲突状态存在检查 | resolve / continue / abort | 必须有进行中操作 → `no_conflict_in_progress` |
| `content` 大小限制 | resolve（custom 模式） | ≤ 1 MB，超限 → `invalid_param` |
| `all` + `custom` 禁止 | resolve | 批量不支持 custom → `invalid_body` |
| `all` + `hunks` 禁止 | resolve | 批量不支持逐 hunk → `invalid_body` |
| continue 前 unmerged 检查 | continue | 仍有未解决文件 → `unresolved_conflicts_remain`，**不**执行 git commit |
| abort 哨兵文件兜底 | abort | abort 后重新探测，哨兵仍在 → `git_error` |
| squash 不自动提交 | merge | `squash=true` 成功时 `merged=false`，改动已 staged |
| hunk 解析文件上限 | conflict-status | 最多解析 20 个文件的 hunk，超出只返回 path+status |
| 单文件解析上限 | conflict-status | > 1 MB 文件不解析 hunk，`truncated: true` |
| 三路原文单路上限 | conflict-status | > 1 MB 返回 `null` |
| 二进制文件 | resolve | 不支持 hunk 模式，仅整文件 ours/theirs |
| `choice="base"` 限制 | resolve | 仅 diff3 格式（base 非 null）时合法 |

### §13.3 超时配置

| 操作 | 超时 | 说明 |
|------|------|------|
| `git merge` | 30s | 大仓库合并可能较慢 |
| `git cherry-pick` | 30s | |
| `git checkout --ours/--theirs` | 10s | 单文件操作 |
| `git add` | 10s | |
| `git commit` / `--continue` | 30s | 可能触发 hook |
| `git merge/cp/revert --abort` | 15s | |
| `git show :N:<file>`（三路） | 5s / 路 | 三路共 15s 最坏 |
| 状态探测（rev-parse / diff） | 5s | |

---

## §14 文件清单

新增 6 个 handler 文件 + 7 个测试文件：

```
tools/webapi/
├── git_merge.py              # POST /spcode/git-merge
├── git_cherry_pick.py        # POST /spcode/git-cherry-pick
├── git_conflict_status.py    # GET  /spcode/git-conflict-status
├── git_conflict_resolve.py   # POST /spcode/git-conflict-resolve
├── git_conflict_continue.py  # POST /spcode/git-conflict-continue
└── git_conflict_abort.py     # POST /spcode/git-conflict-abort

tests/
├── test_git_merge.py
├── test_git_cherry_pick.py
├── test_git_conflict_status.py
├── test_git_conflict_resolve.py
├── test_git_conflict_continue.py
├── test_git_conflict_abort.py
└── test_git_merge_cherry_pick_e2e.py
```

修改的现有文件：
- `tools/webapi/_helpers.py` — 新增 7 个 helper + 9 个 ReasonCode + `ConflictHunk` dataclass
- `tools/webapi/__init__.py` — ROUTES +6 条、HANDLERS +6 条、import +6 个模块
- `tools/webapi/git_status.py` — 追加 `operation` + `in_conflict` 字段
- `tests/test_webapi_end_to_end.py` — 路由计数断言 40 → 46

---

## §15 测试策略

### §15.1 单元测试

| 测试文件 | 覆盖要点 | 预估 case 数 |
|----------|----------|-------------|
| `test_git_merge.py` | body 校验、跨字段互斥（3 组）、ref 校验、preflight mock、脏检查、squash 豁免、stderr 分类器（6 路）、成功/ff/already-up-to-date/squash/conflict 响应 | ~25 |
| `test_git_cherry_pick.py` | body 校验、mainline 校验、no_edit 强制、commit 校验、脏检查、stderr 分类器（5 路）、成功/conflict/empty 响应 | ~20 |
| `test_git_conflict_status.py` | 无冲突、merge/cp/revert 三路探测、hunk 解析（标准/diff3/二进制/大文件/无标记/畸形）、三路原文、20 文件上限 | ~25 |
| `test_git_conflict_resolve.py` | 4 种模式、file/all 互斥、hunks/resolution 互斥、all+custom 禁止、路径防御、file_not_conflicted、hunk 重建算法、partial 路径、content 大小、二进制+hunks 禁止、base choice 限制 | ~30 |
| `test_git_conflict_continue.py` | 无冲突操作、unmerged 检查、merge/cp/revert 三路 continue、hook 错误、identity 错误、message 校验、成功回读 | ~18 |
| `test_git_conflict_abort.py` | 无冲突操作、merge/cp/revert 三路 abort、哨兵文件兜底、幂等性（二次调用） | ~12 |

### §15.2 E2E 测试

`test_git_merge_cherry_pick_e2e.py`：

```
1. git init + 初始 commit（main）
2. 创建 feature 分支 + 修改同一文件 → commit
3. 切回 main + 修改同一文件 → commit
4. POST git-merge {source: "feature"} → 期望 merge_conflict
5. GET git-conflict-status → 期望 1 个 UU 文件 + hunk 列表
6. POST git-conflict-resolve {file, hunks: [{index:0, choice:"theirs"}]}
7. GET git-conflict-status → 期望 all_resolved=true
8. POST git-conflict-continue → 期望 continued=true + merge commit
9. 验证 git log 包含 merge commit
10. 创建另一个分支 + commit
11. POST git-cherry-pick {ref: <sha>} → 期望 picked=true
12. 验证 git log 包含 cherry-picked commit
13. POST git-merge {source: "main"}（已在 main 上）→ 期望 merge_already_up_to_date
14. 制造 revert 冲突 → conflict-status 探测 operation="revert" → abort
```

### §15.3 路由表断言更新

```python
# test_webapi_end_to_end.py
assert len(ROUTES) == 46  # 40 + 6 新端点
```

---

## §16 实施计划（PR 拆分）

| PR | 内容 | 依赖 | 预估改动 |
|----|------|------|----------|
| PR-1 | `_helpers.py`：7 个 helper + 9 个 ReasonCode + 2 个分类器 + `ConflictHunk` | — | ~350 行 |
| PR-2 | `git_merge.py` + `test_git_merge.py` | PR-1 | ~450 行 |
| PR-3 | `git_cherry_pick.py` + `test_git_cherry_pick.py` | PR-1 | ~350 行 |
| PR-4 | `git_conflict_status.py` + `test_git_conflict_status.py` | PR-1 | ~500 行 |
| PR-5 | `git_conflict_resolve.py` + `test_git_conflict_resolve.py` | PR-1, PR-4 | ~550 行 |
| PR-6 | `git_conflict_continue.py` + `git_conflict_abort.py` + 测试 | PR-1 | ~400 行 |
| PR-7 | `__init__.py` 路由注册 + `git_status.py` 追加字段 + E2E 测试 + 计数更新 | PR-2~6 | ~300 行 |
| PR-8 | `AGENTS.md` / `README.md` 文档更新 + 版本号 → v2.22.0 | PR-7 | ~200 行 |

---

## §17 与现有端点的交互

### §17.1 git-revert 冲突的兼容

现有 `POST /spcode/git-revert` 在冲突时返回 `revert_conflict`，但**没有**
提供 resolve / continue / abort 端点。本设计的 conflict 端点**天然兼容** revert 冲突：

- `_detect_conflict_operation` 检测 `.git/REVERT_HEAD` → `"revert"`
- `git-conflict-resolve` 的 ours/theirs/custom 对 revert 冲突同样适用
- `git-conflict-continue` 执行 `git revert --continue --no-edit`
- `git-conflict-abort` 执行 `git revert --abort`

v2.22.0 之后，`git-revert` 的 `revert_conflict` 不再是死胡同。

### §17.2 git-status 端点

`GET /spcode/git-status` 追加 `operation` + `in_conflict` 字段（§9），
前端已有的轮询逻辑零改动即可感知冲突状态。

### §17.3 git-diff 端点

冲突文件的 diff 包含 `<<<<<<<` / `=======` / `>>>>>>>` 标记。
`GET /spcode/git-diff` 正常展示这些标记，前端可据此渲染冲突 diff 视图。

---

## §18 前端消费建议

### §18.1 Merge 流程

```
用户点击 "Merge branch"
  → POST /spcode/git-merge {source: "feature/x"}
  → 200 + reason=null                    → 刷新 git-log / git-status
  → 200 + reason=merge_conflict          → 进入冲突解决 UI
  → 200 + reason=merge_already_up_to_date → toast 提示
```

### §18.2 冲突解决 UI 流程

```
git-status 轮询发现 in_conflict=true
  → GET /spcode/git-conflict-status（获取冲突文件 + hunk 详情）
  → 渲染冲突文件列表 + 解决进度

对每个冲突文件：
  快速模式：用户选择 "Accept Ours" / "Accept Theirs"
    → POST /spcode/git-conflict-resolve {file, resolution: "theirs"}

  精细模式：用户逐 hunk 选择
    → POST /spcode/git-conflict-resolve {file, hunks: [{index:0, choice:"ours"}, ...]}
    → 若 partial=true → 继续解决剩余 hunk

  编辑模式：用户在 Monaco merge editor 中编辑
    → POST /spcode/git-conflict-resolve {file, resolution: "custom", content: "..."}

  批量模式：用户选择 "Accept All Theirs"
    → POST /spcode/git-conflict-resolve {all: true, resolution: "theirs"}

全部解决后（all_resolved=true）：
  → POST /spcode/git-conflict-continue
  → 刷新 git-log / git-status / git-branches

或放弃：
  → POST /spcode/git-conflict-abort
  → 刷新 git-status
```

---

## §19 开放问题（v2 迭代）

| # | 问题 | v1 处理 | v2 方向 |
|---|------|---------|---------|
| 1 | 多 commit cherry-pick（`refs: list[str]`） | 仅单 commit | 扩展 body + sequencer 进度 |
| 2 | merge strategy / strategy_option | 不暴露 | 按需添加 `strategy` / `strategy_option` 字段 |
| 3 | conflict-status 内联 diff | 返回三路原文，前端自行 diff | 可选 `?inline_diff=true` 返回 unified diff |
| 4 | rebase 冲突 | 不覆盖 | `_detect_conflict_operation` 追加 `REBASE_HEAD` 探测 |
| 5 | 冲突文件 diff 在 git-diff 端点的展示 | 正常展示（含冲突标记） | 可选结构化 conflict diff |

---

> Author: elecvoid243 · 2026-07-28 19:24 CST
