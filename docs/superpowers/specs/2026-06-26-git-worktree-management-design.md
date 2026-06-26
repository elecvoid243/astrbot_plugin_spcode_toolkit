# Git Worktree Management Endpoints 设计

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后，下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已通过（用户确认日期 2026-06-26，6 段全部确认:§A 架构 → §B API → §C 防御 → §D ReasonCode → §E 测试 → §F 实施计划）
**作者**: elecvoid243 @ 2026-06-26 17:01
**目标插件 / 仓库**:
- `astrbot_plugin_spcode_toolkit`（后端，本 spec 范围）
- `Astrbot/dashboard`（前端，前端实现在 v3.10+ dashboard 迭代跟进）

**前置版本**: spcode plugin v2.13.x（git-show v3.8 之后）；dashboard 当前 HEAD。

**版本号变化**: v2.13.x → **v2.14.0**（4 个新 endpoint，纯 additive）。

---

## 用户决策记录(3 轮澄清)

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: endpoint 覆盖范围** | **B** — ADD + REMOVE + LOCK/UNLOCK(中等) | 不含 prune/move/repair(留 v3.10+ 迭代) |
| **Q2: ADD 端点 body schema** | **B** — git CLI 旗标平铺(`path/branch/create/force/detach/base`) | 与 `git worktree add` CLI 1:1 对齐 |
| **Q3: REMOVE 端点安全策略** | **A** — 保守(禁删 main + 默认 refuse dirty + force=true 可绕过 dirty + list 预查) | main 是 spcode 的"已加载项目"硬绑定,误删即崩前端 |
| **Q4: 架构方案** | **1** — 4 个独立端点(复用 git-stage/unstage/commit 模式) | 每个端点独立 schema/reason/test |

---

## §1 背景与动机

### 1.1 当前状态(v2.13.x)

worktree 相关的 endpoint 只有 **2 个只读端点**:

| 端点 | 类型 | 用途 |
|------|------|------|
| `GET /spcode/git-worktrees` | 读 | 列出 worktree |
| `GET /spcode/git-diff?worktree=<path>` | 读(扩展) | 切换 worktree 查看 diff |

**写操作完全缺失**。前端 Dashboard 上"新建 / 删除 / 锁定"按钮无对应后端支持,只能引导用户去终端手动跑 `git worktree add/lock/remove`,UX 割裂。

### 1.2 目标(v2.14.0)

新增 4 个写端点,覆盖 git worktree 最常用的 4 个写操作(90% 实际场景):

- 新建(ADD)
- 删除(REMOVE,禁用 main)
- 锁定(LOCK,防后台 agent 误删)
- 解锁(UNLOCK)

`git worktree prune/move/repair` 等罕见操作留 v3.10+ 迭代。

### 1.3 范围与非目标

**范围内**:
- 4 个 POST 端点 + 共享 helpers + ReasonCode 扩展
- 现有 `GET /spcode/git-worktrees` 扩展返回 `locked` / `locked_reason` 字段
- 后端完整实现 + 单元/E2E 测试

**范围外**:
- 前端 Dashboard UI 改动(在 dashboard 仓库独立 spec)
- `prune` / `move` / `repair` / `repair` 子命令(留 v3.10+)
- `add --checkout` 等更多 git CLI 旗标的完整平铺(只平铺 6 个核心旗标)
- 修改 `_loaded_projects` 状态(Q1=A 不变量延伸)

---

## §2 架构总览

### 2.1 端点清单(总数 12 → 16)

```
POST /spcode/git-worktree-add         ← 新建 worktree
POST /spcode/git-worktree-remove      ← 删除 worktree (禁用 main)
POST /spcode/git-worktree-lock        ← 锁定(防后台 agent 误删)
POST /spcode/git-worktree-unlock      ← 解锁
```

路径命名遵循现有模式:`/spcode/git-worktree-*`(单数 + 操作动词)。与现有 `GET /spcode/git-worktrees`(复数 + 列表)区分,无歧义。

### 2.2 文件变更清单

| 文件 | 类型 | 行数预估 | 职责 |
|------|------|---------|------|
| `tools/webapi/git_worktree_add.py` | 新 | ~180 | ADD handler + body 校验 + git 调用 + post-create 校验 |
| `tools/webapi/git_worktree_remove.py` | 新 | ~170 | REMOVE handler + main/dirty/force 判定 + list 预查 |
| `tools/webapi/git_worktree_lock.py` | 新 | ~110 | LOCK handler |
| `tools/webapi/git_worktree_unlock.py` | 新 | ~110 | UNLOCK handler |
| `tools/_helpers.py` | 改 | +60 | 新增 `_validate_new_worktree_path` / `_resolve_target_worktree` / `_is_valid_ref_name`,扩展 `_parse_git_worktree_porcelain` |
| `tools/webapi/_helpers.py` | 改 | +20 | `ReasonCode` 加 11 个新码字面量 |
| `tools/webapi/git_worktrees.py` | 改 | +5 | GET 响应附加 `locked` / `locked_reason`(纯 additive) |
| `tools/webapi/__init__.py` | 改 | +24 | ROUTES +4 / HANDLERS +4 / `__all__` +4 |
| `tests/test_git_worktree_add.py` | 新 | ~400 | ADD 单元测试(~26 cases) |
| `tests/test_git_worktree_remove.py` | 新 | ~280 | REMOVE 单元测试(~16 cases) |
| `tests/test_git_worktree_lock.py` | 新 | ~220 | LOCK + UNLOCK 单元测试(~14 cases) |
| `tests/test_helpers_worktree.py` | 新 | ~200 | helpers 单元测试(~12 cases) |
| `tests/test_git_worktree_porcelain_locked.py` | 新 | ~120 | parser 扩展测试(~6 cases) |
| `tests/test_worktree_mgmt_e2e.py` | 新 | ~120 | 真实 git repo E2E 冒烟(~5 cases) |
| `tests/test_webapi_end_to_end.py` | 改 | +15 | 路由计数 12 → 16,新增 4 路由注册验证 |
| `tests/test_git_worktrees.py` | 改 | +60 | GET 返回 locked 字段(~3 cases) |
| `docs/superpowers/specs/2026-06-26-git-worktree-management-design.md` | 新 | — | 本 spec |
| `docs/webapi-git-worktree-mgmt-api.md` | 新 | ~250 | 公开 API 文档 |
| `README.md` | 改 | +4 | endpoint 表格 +4 行 |
| `AGENTS.md` | 改 | +5 | endpoint 表格 +1 行 + §3.7 增量 |

### 2.3 共享基础设施复用

| 组件 | 路径 | 复用方式 |
|------|------|---------|
| `_git_endpoint_preflight` | `tools/webapi/_helpers.py` | 5 步前置 — 4 个新端点都调用 |
| `_run_git_async` | 同上 | 异步 git 子进程调用 |
| `_make_envelope` | 同上 | 统一信封 |
| `ReasonCode` | 同上 | 失败码(扩展 +11) |
| `_JSONResponseCompat` | 同上 | 兼容 framework + 测试 |
| `_resolve_git_common_dir` | `tools/_helpers.py` | ADD post-create / 通用防越权 |
| `_parse_git_worktree_porcelain` | 同上 | REMOVE/LOCK/UNLOCK list 预查(**扩展**支持 locked) |
| `_validate_worktree_param` | 同上 | 现有 6 步防御(REMOVE/LOCK/UNLOCK 部分复用其格式校验思路) |

### 2.4 数据流(以 ADD 为例)

```
HTTP POST /spcode/git-worktree-add
    body: {"path": "...", "branch": "...", "create": false, ...}
    │
    ↓ _wrap(plugin) 注入 umo / worktree
    ↓ _git_endpoint_preflight(umo=?, worktree_param=None)  [5 步]
    │     ↓ 失败 → envelope + ReasonCode
    ↓ _validate_new_worktree_path(new_path)               [4 步防御]
    │     ↓ 失败 → envelope + reason="path_unsafe"
    ↓ 交叉校验(create/force/detach/base 互斥)
    │     ↓ 失败 → envelope + reason="invalid_body"
    ↓ _is_valid_ref_name(branch) 防御
    │     ↓ 失败 → envelope + reason="invalid_branch"
    ↓ _build_git_worktree_add_args(...) 拼装 git CLI args
    ↓ _run_git_async(git worktree add ...)                 [subprocess]
    │     ↓ 失败 → stderr → reason 映射
    ↓ Post-create: _resolve_git_common_dir(new_path) == primary
    │     ↓ 不匹配 → reason="worktree_not_in_repo" (防越权兜底)
    ↓ 重新调用 git worktree list --porcelain,返回 worktrees 全集
    ↓ _make_envelope(success=True, created={...}, worktrees=[...], ...)
```

---

## §3 API 契约

### 3.1 `POST /spcode/git-worktree-add`

#### 3.1.1 Request body(git CLI 旗标平铺)

```jsonc
{
  "path":   "F:\\repo\\.worktrees\\feature-x",  // required, 绝对路径, ≤4096
  "branch": "feature-x",                        // required iff detach=false
  "create": false,                              // -b : 强制新建分支
  "force":  false,                              // -B : 强制重置已存在分支
  "detach": false,                              // --detach : detached HEAD
  "base":   null                                // create=true 时可选, 起点 ref
}
```

#### 3.1.2 交叉校验(失败 → `invalid_body`)

| 组合 | 失败原因 |
|------|---------|
| `create=true AND force=true` | 二选一 |
| `detach=true AND (create=true OR force=true)` | detach 模式不支持 |
| `base != null AND create=false` | base 仅 create 模式有意义 |
| `branch` 非空 + `detach=true` 时 | detach 模式下 branch 视为 commit ref,可选 |
| `branch` 空 / 非 str | 类型错 |
| `path` 空 / 非 str / 非绝对 | 格式错 |

#### 3.1.3 成功响应

```jsonc
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:\\repo",                              // primary
    "umo": "...",
    "worktree": "F:\\repo\\.worktrees\\feature-x",        // 新增的
    "created": {                                          // ★ 单条新增详情
      "path":     "...",
      "branch":   "feature-x",                           // detached 时为 null
      "head_sha": "abc123...",
      "is_main":  false,
      "locked":   false
    },
    "worktrees": [ ... 全集(含新增) ... ],
    "reason": null,
    "stderr": "",
    "elapsed_ms": 145
  }
}
```

#### 3.1.4 失败 reason(ADD-specific)

| reason | 触发 |
|--------|------|
| `invalid_body` | 必填缺失 / 类型错 / 交叉校验失败 |
| `invalid_branch` | branch 名称格式非法(ref-format 校验失败) |
| `invalid_param` | path/branch 超长(>4096) |
| `path_unsafe` | `_validate_new_worktree_path` 4 步防御失败 |
| `path_exists_nonempty` | target 路径已存在且非空 |
| `cannot_create_existing` | branch 已存在 & create=false (git stderr 映射) |
| `cannot_checkout_missing` | branch 不存在 & create=false (git stderr 映射) |
| `worktree_not_in_repo` | post-create git-common-dir 不匹配 primary(防越权兜底) |
| `git_error` | 其它 git 错误 |

+ 继承 preflight:`feature_disabled` / `no_project_loaded` / `directory_missing` / `not_a_git_repo` / `git_unavailable`

---

### 3.2 `POST /spcode/git-worktree-remove`

#### 3.2.1 Request body

```jsonc
{
  "path":  "F:\\repo\\.worktrees\\feature-x",  // required
  "force": false                               // --force: 跳过 dirty 检查
}
```

#### 3.2.2 业务闸(在 §4 防御链里详细)

| reason | 触发条件 |
|--------|---------|
| `worktree_not_found` | path 不在 `git worktree list` 中 |
| `cannot_remove_main` | `is_main=true`(即使 force=true 也拒) |
| `worktree_locked` | 已 locked(需先 unlock) |
| `worktree_dirty` | 工作树有未提交改动 & force=false |
| `path_unsafe` | path 防御失败 |
| `git_error` | 其它 |

#### 3.2.3 成功响应

```jsonc
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "F:\\repo",
    "umo": "...",
    "removed_path": "F:\\repo\\.worktrees\\feature-x",
    "worktrees": [ ... 全集(已剔除 removed) ... ],
    "reason": null,
    "stderr": "",
    "elapsed_ms": 89
  }
}
```

---

### 3.3 `POST /spcode/git-worktree-lock`

#### 3.3.1 Request body

```jsonc
{
  "path":   "F:\\repo\\.worktrees\\feature-x",
  "reason": "in use by background agent"  // optional, --reason 参数(git 2.30+)
}
```

#### 3.3.2 校验

| reason | 触发 |
|--------|------|
| `worktree_not_found` | path 不在 list 中 |
| `already_locked` | 当前已 locked |
| `path_unsafe` | 防御失败 |
| `git_error` | 其它 |

#### 3.3.3 允许 lock main worktree

无副作用:REMOVE 已硬禁止删 main,lock 是 noop(对 main 的 lock 不影响业务)。前端 dashboard UI 不应在 main 上显示 lock 按钮(由前端决定),但后端宽容允许。

#### 3.3.4 成功响应

```jsonc
{
  "data": {
    "worktree":     "F:\\repo\\.worktrees\\feature-x",
    "locked":       true,
    "lock_reason":  "in use by background agent",   // git 2.30+
    "worktrees":    [ ... 全集(含 locked 字段) ... ],
    ...
  }
}
```

---

### 3.4 `POST /spcode/git-worktree-unlock`

#### 3.4.1 Request body

```jsonc
{ "path": "F:\\repo\\.worktrees\\feature-x" }
```

#### 3.4.2 校验

| reason | 触发 |
|--------|------|
| `worktree_not_found` | path 不在 list 中 |
| `not_locked` | 当前未 locked |
| `path_unsafe` | 防御失败 |
| `git_error` | 其它 |

#### 3.4.3 成功响应

```jsonc
{
  "data": {
    "worktree":  "F:\\repo\\.worktrees\\feature-x",
    "locked":    false,
    "worktrees": [ ... 全集 ... ],
    ...
  }
}
```

---

### 3.5 关键交叉问题:现有 `GET /spcode/git-worktrees` 需要扩展

`locked` 是 worktree 的内禀状态,前端需要在 list 视图就看到(否则 LOCK 后再 GET 仍是 stale)。

git 的 `worktree list --porcelain` 输出本来就有 `locked` 行(及可选 reason 行),我们的解析器 `_parse_git_worktree_porcelain` 此前没捕获。

**修改**:
- `tools/_helpers.py:_parse_git_worktree_porcelain` 增加 `locked: bool` + `locked_reason: str | None` 字段
- `tools/webapi/git_worktrees.py` 返回数据加这 2 字段
- **纯 additive,老 dashboard 忽略新字段零影响**

git porcelain 格式(参考 `git worktree list --porcelain` 文档):

```
worktree /path/to/main
HEAD abc123
branch refs/heads/main

worktree /path/to/linked
HEAD def456
branch refs/heads/feature
locked                                  ← 无 reason
# 或
locked <reason-text-on-same-or-next-line>  ← 有 reason
```

---

## §4 防御链与安全模型

### 4.1 防御总览

| 端点 | 防御层数 | 关键防御点 |
|------|---------|-----------|
| **ADD** | 7 层 | 4 步新路径防御 + 1 步交叉校验 + 1 步 git 调用 + **1 步 post-create git-common-dir 兜底** |
| **REMOVE** | 6 层 | 2 步路径 + 1 步 list 预查 + 3 步业务闸(main/locked/dirty) + git 调用 |
| **LOCK** | 4 层 | 2 步路径 + 1 步 list 预查 + 1 步 already-locked 闸 + git 调用 |
| **UNLOCK** | 4 层 | 2 步路径 + 1 步 list 预查 + 1 步 not-locked 闸 + git 调用 |

### 4.2 新增 helpers(`tools/_helpers.py`)

```python
def _validate_new_worktree_path(
    git_bin: str,
    new_path: str | None,
) -> tuple[str | None, str | None]:
    """ADD 端点专用:目标路径可以不存在(ADD 才创建),但格式必须合法。
    
    4 步防御:
      1. format     — 非空 / ≤4096 字符 / 无 `\\` / 绝对路径 / 无 `..` 段
      2. .git 段    — 任一 path component 不为 `.git`
      3. parent dir — 必须存在且可写
      4. blacklist  — _is_path_safe 黑名单(file_remove_blacklist 配置)
    
    Returns (resolved_absolute_path, None) | (None, "path_unsafe")。
    """


def _resolve_target_worktree(
    git_bin: str,
    primary_dir: str,
    body_path: str,
) -> tuple[dict | None, str | None]:
    """REMOVE/LOCK/UNLOCK 共用:基础路径校验 + 在 list 中查找 target。
    
    步骤:
      1. 基础 format (非空 / ≤4096 / 无 `\\` / 无 `..` / 绝对路径)
      2. 调 git worktree list --porcelain
      3. 在返回的 worktree 列表中按 path 精确匹配(normcase 比较)
    
    Returns (worktree_dict, None) | (None, "worktree_not_found") |
            (None, "path_unsafe")。
    
    与现有 _validate_worktree_param 的区别:
    - 6 步防御里有 git-common-dir 校验,但 list 查找本身已隐含同 repo 校验
    - 6 步防御要求 path 已存在(os.path.isdir(real)),这里直接靠 git list 确认
    - body 校验比 query 校验更早一步,我们需要 worktree dict(state)做后续业务闸
    """


def _is_valid_ref_name(ref: str) -> bool:
    """轻量 git ref-format 校验。允许 branch / tag / commit SHA / HEAD~N 等。"""
```

### 4.3 ADD 端点的 7 层防御(详细)

```python
async def handle(plugin, *, umo=None, worktree=None, body=None):
    body = body or {}

    # 防御 1+2: 路径格式(交给 _validate_new_worktree_path)
    raw_path = body.get("path")
    new_path, err = _validate_new_worktree_path(plugin._git_binary(), raw_path)
    if err:
        return _make_envelope(success=False, reason="path_unsafe", ...)

    # 防御 3: 交叉校验(create / force / detach / base)
    branch, create, force, detach, base = (
        body.get("branch"), body.get("create", False),
        body.get("force", False), body.get("detach", False),
        body.get("base"),
    )
    cross_err = _validate_add_cross_fields(create, force, detach, base, branch)
    if cross_err:
        return _make_envelope(success=False, reason="invalid_body", ...)

    # 防御 4: branch 名称格式(ref-format)
    if branch and not _is_valid_ref_name(branch):
        return _make_envelope(success=False, reason="invalid_branch", ...)

    # 防御 5: target 路径已存在非空(预防性)
    if os.path.exists(new_path) and os.listdir(new_path):
        return _make_envelope(success=False, reason="path_exists_nonempty", ...)

    # 防御 6: git worktree add 调用
    add_args = _build_git_worktree_add_args(
        plugin._git_binary(), directory, new_path, branch,
        create, force, detach, base,
    )
    result = await _run_git_async(add_args, cwd=directory, timeout=30.0)
    if not result["ok"]:
        return _map_add_stderr_to_reason(result.get("stderr", ""), ...)

    # 防御 7(关键): post-create git-common-dir 校验
    new_common = _resolve_git_common_dir(plugin._git_binary(), new_path)
    primary_common = _resolve_git_common_dir(plugin._git_binary(), directory)
    if new_common != primary_common:
        return _make_envelope(success=False, reason="worktree_not_in_repo",
                              stderr="post-create git-common-dir mismatch", ...)

    # 成功:返回 worktree list + created 单条
    updated = await _list_worktrees(plugin._git_binary(), directory)
    return _make_envelope(success=True, created=updated[-1], worktrees=updated, ...)
```

**关键设计**:防御 7(post-create git-common-dir)是**纵深防御的最后一关**。即便前 6 步全部误配,只要新 worktree 实际不在 primary repo,这一步必拦截。

### 4.4 REMOVE 的 3 道业务闸

```python
async def handle(plugin, *, umo=None, worktree=None, body=None):
    body = body or {}

    # ...preflight + _resolve_target_worktree...
    target_wt = resolved["worktree"]

    # 闸 1: main worktree (硬禁止)
    if target_wt["is_main"]:
        return _make_envelope(success=False, reason="cannot_remove_main",
                              stderr=f"main worktree ({target_wt['path']}) cannot be removed", ...)

    # 闸 2: locked (需先 unlock)
    if target_wt.get("locked"):
        return _make_envelope(success=False, reason="worktree_locked",
                              stderr=f"worktree is locked: {target_wt.get('locked_reason') or '<no reason>'}", ...)

    # 闸 3: dirty (除非 force=true)
    if not body.get("force", False):
        dirty_result = await _run_git_async(
            [plugin._git_binary(), "-C", target_wt["path"], "status", "--porcelain"],
            encoding="utf-8",
        )
        if dirty_result["ok"] and dirty_result["stdout"].strip():
            return _make_envelope(success=False, reason="worktree_dirty",
                                  stderr="worktree has uncommitted changes; pass force=true to override", ...)

    # git worktree remove [--force] <path>
    args = [plugin._git_binary(), "-C", directory, "worktree", "remove"]
    if body.get("force", False):
        args.append("--force")
    args.append(target_wt["path"])
    result = await _run_git_async(args, cwd=directory, timeout=30.0)
    ...
```

**为什么 dirty 用 `git status --porcelain` 预查而非依赖 git remove 报错**:
- 预查可给出更明确的 reason(`worktree_dirty`)而非依赖 stderr 关键词匹配
- 让前端 UI 能在调用前显示"工作树有 N 个未提交改动,确认删除?"
- force=true 跳过预查,等同于 git CLI `--force` 语义

### 4.5 LOCK/UNLOCK 闸

```python
# LOCK
target_wt = resolved["worktree"]
if target_wt.get("locked"):
    return _make_envelope(success=False, reason="already_locked", ...)

# 允许 lock main(无副作用:REMOVE 已硬禁止删 main)
args = [git_bin, "-C", directory, "worktree", "lock"]
if body.get("reason"):  # git 2.30+ 支持
    args.extend(["--reason", body["reason"]])
args.append(target_wt["path"])
```

```python
# UNLOCK
target_wt = resolved["worktree"]
if not target_wt.get("locked"):
    return _make_envelope(success=False, reason="not_locked", ...)

args = [git_bin, "-C", directory, "worktree", "unlock", target_wt["path"])
```

### 4.6 路径格式校验的硬约束(4 端点统一)

| 检查项 | 失败 reason |
|-------|-----------|
| 非空 / 非字符串 | `path_unsafe` |
| 长度 > 4096 | `path_unsafe` |
| 包含 `\`(强制 POSIX 风格) | `path_unsafe` |
| 含 `..` 段 | `path_unsafe` |
| 非绝对路径 | `path_unsafe` |
| 含 `.git` 路径 component(ADD 专属) | `path_unsafe` |
| 黑名单命中(ADD 专属) | `path_unsafe` |

**为什么 REMOVE/LOCK/UNLOCK 不强制 `.git` 段拒绝**:这 3 个 path 必然来自 list,而 list 内的合法 worktree 不会包含 `.git` 段。format 检查已足够。

### 4.7 跨仓库防越权 — 终极兜底

| 端点 | 兜底机制 |
|------|---------|
| ADD | post-create git-common-dir == primary(防御 7) |
| REMOVE / LOCK / UNLOCK | target_wt 必然在 primary 的 list 中(§4.3 步骤 2-3) |

**核心不变量**:`_resolve_git_common_dir(git_bin, <target>)` 必须等于 primary 的 git-common-dir。任何攻击路径(用户传 `C:\other-repo\.git` 或 symlink 越界等)都会被这一关拦下。

---

## §5 错误处理 / ReasonCode

### 5.1 新增 reason 码(`tools/webapi/_helpers.py:ReasonCode`)

```python
class ReasonCode:
    # ── 现有(继承) ──
    FEATURE_DISABLED = "feature_disabled"
    NO_PROJECT_LOADED = "no_project_loaded"
    DIRECTORY_MISSING = "directory_missing"
    NOT_A_GIT_REPO = "not_a_git_repo"
    GIT_UNAVAILABLE = "git_unavailable"
    GIT_ERROR = "git_error"
    INVALID_BODY = "invalid_body"
    INVALID_PARAM = "invalid_param"
    PATH_UNSAFE = "path_unsafe"

    # ── v2.14.0 新增(worktree-mgmt 专用) ──
    INVALID_BRANCH         = "invalid_branch"           # ADD: branch 格式非法
    PATH_EXISTS_NONEMPTY   = "path_exists_nonempty"     # ADD: target 已存在非空
    CANNOT_CREATE_EXISTING = "cannot_create_existing"   # ADD: branch 已存在 & create=false
    CANNOT_CHECKOUT_MISSING = "cannot_checkout_missing" # ADD: branch 不存在 & create=false
    WORKTREE_NOT_IN_REPO   = "worktree_not_in_repo"     # ADD: post-create 防越权兜底
    WORKTREE_NOT_FOUND     = "worktree_not_found"       # REMOVE/LOCK/UNLOCK: list 中查不到
    CANNOT_REMOVE_MAIN     = "cannot_remove_main"       # REMOVE: 硬禁止删 main
    WORKTREE_LOCKED        = "worktree_locked"          # REMOVE: target 已 locked
    WORKTREE_DIRTY         = "worktree_dirty"           # REMOVE: 工作树有改动
    ALREADY_LOCKED         = "already_locked"           # LOCK: 已 locked
    NOT_LOCKED             = "not_locked"               # UNLOCK: 未 locked
```

**总新增** 11 个,加现有 9 个 = 20 个 reason 码。

### 5.2 stderr → Reason 映射表

#### ADD 端点

| git stderr 关键字符串 | 映射到 | 备注 |
|----------------------|--------|------|
| `'<branch>' is already checked out at` | `cannot_create_existing` | branch 在另一 worktree 中已 checked out |
| `'<branch>' already exists` | `cannot_create_existing` | create=false & branch 存在 |
| `'<branch>' is not a valid branch name` | `invalid_branch` | ref-format 校验 |
| `'<branch>' is a missing branch name` | `cannot_checkout_missing` | create=false & branch 不存在 |
| `'<path>' already exists` | `path_exists_nonempty` | 防御 5 漏网时的兜底 |
| `<path> cannot be used as a worktree name` | `invalid_param` | path 含非法字符 |
| `invalid start point: <base>` | `invalid_param` | base ref 不存在 |
| 其它非 0 退出 | `git_error` | 兜底 |

#### REMOVE 端点

| git stderr 关键字符串 | 映射到 | 备注 |
|----------------------|--------|------|
| `'<path>' is not a working tree` | `worktree_not_found` | 防御 §4.4 漏网 |
| `'<path>' contains modified or untracked files` | `worktree_dirty` | force=false 但 git 仍 fail |
| `'<path>' is locked` | `worktree_locked` | 防御 §4.4 漏网 |
| 其它非 0 退出 | `git_error` | 兜底 |

#### LOCK 端点

| git stderr 关键字符串 | 映射到 |
|----------------------|--------|
| `'<path>' is not a working tree` | `worktree_not_found` |
| `'<path>' is already locked` | `already_locked` |
| 其它 | `git_error` |

#### UNLOCK 端点

| git stderr 关键字符串 | 映射到 |
|----------------------|--------|
| `'<path>' is not a working tree` | `worktree_not_found` |
| `'<path>' is not locked` | `not_locked` |
| 其它 | `git_error` |

### 5.3 失败响应统一格式(继承现有约定)

```jsonc
{
  "status": "ok",          // 恒为 "ok"(不区分成功/失败)
  "data": {
    "loaded": false,        // 失败时恒 false
    "directory": "...",     // primary
    "umo": "...",
    "worktree": "...",
    "reason": "cannot_remove_main",  // ★ 新 reason 码
    "stderr": "main worktree (...) cannot be removed",  // 截断 ≤2KB
    "elapsed_ms": 12,
    // 端点特定字段(如 created / removed_path)失败时省略或为 null
  }
}
```

### 5.4 成功响应必含字段(各端点)

| 端点 | data 必含字段(成功时) |
|------|----------------------|
| ADD | `loaded, directory, umo, worktree, created{...}, worktrees[...], reason=null, stderr="", elapsed_ms` |
| REMOVE | `loaded, directory, umo, removed_path, worktrees[...], reason=null, stderr="", elapsed_ms` |
| LOCK | `loaded, directory, umo, worktree, locked=true, lock_reason, worktrees[...], reason=null, stderr="", elapsed_ms` |
| UNLOCK | `loaded, directory, umo, worktree, locked=false, worktrees[...], reason=null, stderr="", elapsed_ms` |

**`worktrees` 字段**:4 个端点成功路径**统一返回最新 worktree 列表**(调用 `_list_worktrees()`),前端拿到响应直接更新 UI,无需再调 GET `/spcode/git-worktrees`。失败时 `worktrees=null`。

### 5.5 i18n key 前缀(供 dashboard 翻译)

```jsonc
// dashboard/src/i18n/locales/{zh-CN,en-US,...}/features/chat.json
{
  "spcodeWorktreeMgmt": {
    "add":    { "error": { ... } },     // 11 个新增 reason 各 1 条
    "remove": { "error": { ... } },
    "lock":   { "error": { ... } },
    "unlock": { "error": { ... } }
  }
}
```

> i18n 在 dashboard 仓库实现,本 spec 只约定 key 命名空间。4 个 locale × 11 reason = 44 条文案。

---

## §6 测试策略

### 6.1 测试文件清单(6 新 + 2 改,共 ~87 cases)

| 文件 | 类型 | cases | 覆盖范围 |
|------|------|-------|---------|
| `tests/test_git_worktree_add.py` | 新 | ~26 | ADD handler 全分支 |
| `tests/test_git_worktree_remove.py` | 新 | ~16 | REMOVE handler + 3 道业务闸 |
| `tests/test_git_worktree_lock.py` | 新 | ~14 | LOCK + UNLOCK 共用 |
| `tests/test_helpers_worktree.py` | 新 | ~12 | `_validate_new_worktree_path` + `_resolve_target_worktree` |
| `tests/test_git_worktree_porcelain_locked.py` | 新 | ~6 | `_parse_git_worktree_porcelain` 扩展 locked 字段 |
| `tests/test_worktree_mgmt_e2e.py` | 新 | ~5 | 真实 git repo 端到端冒烟 |
| `tests/test_webapi_end_to_end.py` | 改 | +5 | 路由表 12 → 16,新增 4 路由注册 |
| `tests/test_git_worktrees.py` | 改 | +3 | GET 端点返回 `locked` / `locked_reason` 字段 |
| **合计** | | **~87 cases** | |

### 6.2 各端点测试覆盖矩阵

#### `test_git_worktree_add.py`(~26 cases)

| # | 用例 | 验证 |
|---|------|------|
| 1 | `test_add_basic_checkout_existing_branch` | 标准场景:`add <path> <branch>` |
| 2 | `test_add_create_new_branch_with_b` | `create=true` → `-b <branch>` |
| 3 | `test_add_create_new_branch_with_base` | `create=true + base="main"` |
| 4 | `test_add_force_reset_existing_branch` | `force=true` → `-B <branch>` |
| 5 | `test_add_detached_mode_at_HEAD` | `detach=true, branch=null` |
| 6 | `test_add_detached_mode_at_commit` | `detach=true, branch="abc123"` |
| 7 | `test_add_relative_path_rejected` | `path_unsafe` |
| 8 | `test_add_dotdot_path_rejected` | `path_unsafe` |
| 9 | `test_add_dot_git_component_rejected` | `path_unsafe` |
| 10 | `test_add_too_long_path_rejected` | `path_unsafe` |
| 11 | `test_add_parent_dir_missing_rejected` | `path_unsafe` |
| 12 | `test_add_blacklisted_path_rejected` | `path_unsafe`(`C:\Windows\...`) |
| 13 | `test_add_missing_path_field` | `invalid_body` |
| 14 | `test_add_missing_branch_detach_false` | `invalid_body` |
| 15 | `test_add_create_and_force_both_true` | `invalid_body` |
| 16 | `test_add_detach_and_create_both_true` | `invalid_body` |
| 17 | `test_add_detach_and_force_both_true` | `invalid_body` |
| 18 | `test_add_base_without_create` | `invalid_body` |
| 19 | `test_add_invalid_branch_name` | `invalid_branch` |
| 20 | `test_add_target_path_exists_nonempty` | `path_exists_nonempty` |
| 21 | `test_add_git_branch_already_checked_out` | `cannot_create_existing`(stderr 映射) |
| 22 | `test_add_git_branch_missing` | `cannot_checkout_missing` |
| 23 | `test_add_post_create_common_dir_mismatch` | `worktree_not_in_repo`(防越权兜底) |
| 24 | `test_add_success_envelope_shape` | `created{}` + `worktrees[]` + 7 字段 |
| 25 | `test_add_no_project_loaded` | preflight |
| 26 | `test_add_feature_disabled` | preflight |

#### `test_git_worktree_remove.py`(~16 cases)

| # | 用例 | 验证 |
|---|------|------|
| 1 | `test_remove_basic` | 标准删除成功 |
| 2 | `test_remove_force_bypasses_dirty` | `force=true` 跳过 dirty 检查 |
| 3 | `test_remove_dirty_without_force` | `worktree_dirty` |
| 4 | `test_remove_main_worktree_rejected` | `cannot_remove_main`(**即使 force=true**) |
| 5 | `test_remove_locked_worktree_rejected` | `worktree_locked` |
| 6 | `test_remove_unknown_path_rejected` | `worktree_not_found` |
| 7 | `test_remove_path_unsafe` | `path_unsafe` |
| 8 | `test_remove_dotdot_rejected` | `path_unsafe` |
| 9 | `test_remove_relative_path_rejected` | `path_unsafe` |
| 10 | `test_remove_missing_path_field` | `invalid_body` |
| 11 | `test_remove_git_returns_not_working_tree` | stderr → `worktree_not_found` |
| 12 | `test_remove_git_returns_locked` | stderr → `worktree_locked` |
| 13 | `test_remove_git_returns_dirty_with_force_false` | stderr → `worktree_dirty` |
| 14 | `test_remove_success_returns_updated_list` | `removed_path` + `worktrees[]` |
| 15 | `test_remove_no_project_loaded` | preflight |
| 16 | `test_remove_feature_disabled` | preflight |

#### `test_git_worktree_lock.py`(~14 cases)

| # | 用例 | 验证 |
|---|------|------|
| 1 | `test_lock_basic` | 无 reason,成功 |
| 2 | `test_lock_with_reason` | `--reason "..."` 写入 list |
| 3 | `test_lock_already_locked_rejected` | `already_locked` |
| 4 | `test_lock_unknown_path_rejected` | `worktree_not_found` |
| 5 | `test_lock_path_unsafe` | `path_unsafe` |
| 6 | `test_lock_main_worktree_allowed` | 不限制 main |
| 7 | `test_lock_git_returns_already_locked` | stderr 映射 |
| 8 | `test_lock_success_envelope_shape` | `locked=true` + `lock_reason` + `worktrees[]` |
| 9 | `test_unlock_basic` | 标准解锁成功 |
| 10 | `test_unlock_not_locked_rejected` | `not_locked` |
| 11 | `test_unlock_unknown_path_rejected` | `worktree_not_found` |
| 12 | `test_unlock_git_returns_not_locked` | stderr 映射 |
| 13 | `test_unlock_success_envelope_shape` | `locked=false` + `worktrees[]` |
| 14 | `test_lock_unlock_no_project_loaded` | preflight |

#### `test_helpers_worktree.py`(~12 cases)

```python
class TestValidateNewWorktreePath:
    test_accepts_absolute_posix_path        # /repo/.worktrees/feature
    test_accepts_absolute_windows_path      # F:\repo\.worktrees\feature
    test_rejects_dotdot_segment             # /repo/../escape
    test_rejects_relative_path              # ./.worktrees/feature
    test_rejects_dot_git_component          # /repo/.git/feature
    test_rejects_empty_path
    test_rejects_too_long_path              # >4096 字符
    test_rejects_missing_parent             # /nonexistent/feature
    test_rejects_blacklisted                # C:\Windows\System32\...

class TestResolveTargetWorktree:
    test_finds_existing_worktree            # 在 list 中精确匹配
    test_returns_none_for_unknown           # 查不到返回 None
    test_rejects_dotdot_path                # format 失败 → (None, path_unsafe)
    test_normcase_path_match                # Windows 大小写不敏感匹配
```

#### `test_git_worktree_porcelain_locked.py`(~6 cases)

```python
test_parse_unlocked_worktree                # 普通 worktree:locked=False
test_parse_locked_worktree                  # 仅有 `locked` 行
test_parse_locked_with_single_reason_line   # locked + reason
test_parse_locked_with_multiline_reason     # git 2.30+ reason 可多行
test_parse_multiple_worktrees_with_locked  # mixed locked/unlocked
test_parse_main_locked_defaults_false       # main 不会被默认标 locked
```

### 6.3 端到端冒烟(`test_worktree_mgmt_e2e.py`,~5 cases)

在真实 git repo(临时目录)上跑完整生命周期:

```python
test_e2e_add_remove_cycle              # ADD → REMOVE, worktree list 增减 1
test_e2e_add_lock_unlock_remove        # ADD → LOCK → UNLOCK → REMOVE 全链路
test_e2e_add_with_create_branch        # ADD -b → 修改文件 → REMOVE
test_e2e_remove_main_worktree_fails    # REMOVE main 必失败
test_e2e_concurrent_add_different_paths # 并发 ADD 2 个独立路径,均成功
```

### 6.4 端到端路由测试(`test_webapi_end_to_end.py` 增量)

```python
# 路由计数
test_routes_table_has_sixteen_endpoints           # routes 集合 16 项
test_register_webapi_routes_calls_context_sixteen_times  # call_count=16
test_register_webapi_routes_continues_on_failure   # call_count=16(防御)

# 新路由注册
test_git_worktree_add_route_registered
test_git_worktree_remove_route_registered
test_git_worktree_lock_route_registered
test_git_worktree_unlock_route_registered

# HANDLERS 表
test_handlers_dict_has_four_worktree_entries
```

### 6.5 既有 GET 端点回归(`test_git_worktrees.py` 增量 ~3 cases)

```python
test_git_worktrees_response_includes_locked_field     # locked: bool 出现
test_git_worktrees_response_includes_locked_reason    # locked_reason: str|None
test_git_worktrees_parser_unlocked_worktree           # 老语义 100% 保留(向下兼容)
```

### 6.6 测试夹具复用(conftest.py)

**已有可复用**(无需新增):

| Fixture | 来源 | 用途 |
|---------|------|------|
| `tmp_git_repo` | conftest.py | 创建临时 git repo |
| `mock_git_binary` | conftest.py | monkeypatch git 路径 |

**可能新增**:

```python
@pytest.fixture
def multi_worktree_repo(tmp_path, monkeypatch):
    """创建 primary + 1 个 linked worktree,加载到 plugin state。"""
```

### 6.7 覆盖度目标

| 维度 | 目标 |
|------|------|
| 行覆盖率(新文件) | ≥ 90% |
| 分支覆盖率(新代码) | ≥ 85% |
| Reason 码映射表 | 100%(§5.2 表每行至少 1 用例) |
| 攻击向量(§4 防御链) | 100%(7 层防御 × 至少 1 用例) |
| 既有端点(GET worktrees) | 100% 向下兼容(老测试全 PASS) |

---

## §7 实施计划 / PR 拆分

### 7.1 PR 拆分(4 个独立 PR,顺序合并)

| PR | 范围 | 风险 | 估时 | 测试 |
|----|------|------|------|------|
| **PR-A: Foundation** | helpers + parser 扩展 + ReasonCode + GET 端点 locked 字段 | 低(additive) | 1 天 | +21 cases |
| **PR-B: ADD endpoint** | `git_worktree_add.py` + 注册 + 测试 | 中(新写端点) | 1.5 天 | +27 cases |
| **PR-C: REMOVE endpoint** | `git_worktree_remove.py` + 注册 + 测试 | 中(硬禁止删 main) | 1.5 天 | +17 cases |
| **PR-D: LOCK + UNLOCK + docs** | 2 个端点 + 文档 + E2E 冒烟 | 低(最简操作) | 1 天 | +21 cases |
| **合计** | 4 endpoint + 1 parser + 2 helper + 文档 | | **~5 天** | **+86 cases** |

**合并顺序建议**:A → B → C → D(A 是地基,B/C 独立可换序,D 含文档和 E2E 收尾)。

### 7.2 各 PR 详细范围

#### PR-A: Foundation(地基,~1 天,~21 tests)

**新增/修改文件**:

| 文件 | 类型 | 内容 |
|------|------|------|
| `tools/_helpers.py` | 改 | +`_validate_new_worktree_path`(~40 行)<br>+`_resolve_target_worktree`(~30 行)<br>+`_is_valid_ref_name`(轻量 ref-format 校验,~10 行) |
| `tools/_helpers.py` | 改 | `_parse_git_worktree_porcelain` 扩展 `locked` / `locked_reason` 字段 |
| `tools/webapi/_helpers.py` | 改 | `ReasonCode` +11 新码字面量 |
| `tools/webapi/git_worktrees.py` | 改 | GET 响应附加 `locked` / `locked_reason` 字段(pure additive) |
| `tests/test_helpers_worktree.py` | 新 | 12 cases(2 个新 helper) |
| `tests/test_git_worktree_porcelain_locked.py` | 新 | 6 cases(parser 扩展) |
| `tests/test_git_worktrees.py` | 改 | +3 cases(GET 返回 locked 字段) |

**验收标准**:
- `ruff check .` 0 error
- `pytest tests/` 全部 PASS(包括现有 50+ 用例的向后兼容)
- `tools/webapi/git_worktrees.handle` 返回数据增加 `locked: bool` / `locked_reason: str | None`

#### PR-B: ADD endpoint(~1.5 天,~27 tests)

**新增文件**:

| 文件 | 行数预估 |
|------|---------|
| `tools/webapi/git_worktree_add.py` | ~180 |
| `tests/test_git_worktree_add.py` | ~400 |
| `tests/test_webapi_end_to_end.py` | +5 行(路由计数 +1) |
| `tools/webapi/__init__.py` | +8 行(import + ROUTES + HANDLERS + `__all__`) |

**关键验证**:
- 7 层防御每层至少 1 用例覆盖
- §5.2 stderr → Reason 映射表 8 行全覆盖
- Post-create git-common-dir 兜底独立 case

#### PR-C: REMOVE endpoint(~1.5 天,~17 tests)

**新增文件**:

| 文件 | 行数预估 |
|------|---------|
| `tools/webapi/git_worktree_remove.py` | ~170 |
| `tests/test_git_worktree_remove.py` | ~280 |
| `tools/webapi/__init__.py` | +8 行 |

**关键验证**:
- 3 道业务闸(main/locked/dirty)各独立 case
- `force=true` 跳过 dirty 但**仍**禁止 main(`test_remove_main_worktree_force_rejected`)
- 成功响应 `removed_path` 字段必含

#### PR-D: LOCK + UNLOCK + docs(~1 天,~21 tests)

**新增文件**:

| 文件 | 行数预估 |
|------|---------|
| `tools/webapi/git_worktree_lock.py` | ~110 |
| `tools/webapi/git_worktree_unlock.py` | ~110 |
| `tests/test_git_worktree_lock.py` | ~220(LOCK + UNLOCK 合并) |
| `tests/test_worktree_mgmt_e2e.py` | ~120(真实 git repo 冒烟) |
| `tests/test_webapi_end_to_end.py` | +10 行(2 路由 + HANDLERS 表) |
| `docs/webapi-git-worktree-mgmt-api.md` | ~250 |
| `tools/webapi/__init__.py` | +12 行 |
| `README.md` | +1 行(endpoint 表格) |
| `AGENTS.md` | +1 行(endpoint 表格)+ section 3.7 增量 |

**关键验证**:
- LOCK `--reason` git 2.30+ 兼容(git < 2.30 静默忽略 reason)
- E2E 冒烟跑通 ADD → LOCK → UNLOCK → REMOVE 完整链路

---

## §8 风险评估

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| 1 | 误删 main worktree | **极低** | **critical** | REMOVE 闸 1 硬禁止 + 4 个独立 case + post-REMOVE list 预查 |
| 2 | ADD path 越权写系统目录 | 低 | 高 | 4 步 format 防御 + parent dir 存在校验 + post-create git-common-dir |
| 3 | 并发 ADD 同一 path | 低 | 低 | 第二次 ADD → `path_exists_nonempty`(git 自身也失败) |
| 4 | dirty 检查到 remove 之间的 race | 极低 | 低 | git remove 自身会 fail,stderr → `worktree_dirty` 兜底 |
| 5 | git < 2.30 不支持 `--reason` | 中 | 极低 | LOCK reason 是 optional,旧 git 自动忽略 |
| 6 | Windows path 大小写不一致 | 中 | 中 | 全部 `_resolve_target_worktree` 用 `normcase` 比较 |
| 7 | post-create git-common-dir 失败半创建 | 极低 | 中 | 失败 → `worktree_not_in_repo` + stderr 详细,运维按 `git worktree remove --force <path>` 清理 |
| 8 | 用户外部 `git worktree unlock` 后 list stale | 低 | 极低 | 下次 GET 自动刷新(无缓存) |
| 9 | `_parse_git_worktree_porcelain` 扩展破坏老 dashboard | 极低 | 低 | 纯 additive 字段,老代码忽略 |
| 10 | 路由表 12→16 超过框架上限 | **极低** | 中 | AstrBot 框架无上限(其他插件已用 30+ 路由) |

---

## §9 回滚策略

| 场景 | 操作 | 恢复时间 |
|------|------|---------|
| PR-A bug | `git revert <PR-A>` | ~2 min |
| PR-B bug | `git revert <PR-B>`(B 独立,不影响 A/C/D) | ~2 min |
| PR-C bug | `git revert <PR-C>` | ~2 min |
| PR-D bug | `git revert <PR-D>` | ~2 min |
| 全部回滚 | 4 个 revert 反向执行 | ~10 min |

**回滚后行为**:
- 回滚 PR-A → GET worktrees 不再返回 `locked` 字段(老客户端本来就忽略)
- 回滚 PR-B → ADD 端点消失,前端 dashboard "新建 worktree" 按钮失效(降级)
- 回滚 PR-C → REMOVE 端点消失,前端 "删除 worktree" 按钮失效(降级)
- 回滚 PR-D → LOCK/UNLOCK 端点消失,前端 "lock" 按钮失效(降级)

**无需数据迁移**(纯 endpoint 改动,不写 DB、不改 `_loaded_projects`)。

---

## §10 兼容性与版本

### 10.1 兼容性矩阵

| 组合 | 行为 | 是否可工作 |
|------|------|-----------|
| **旧 dashboard × 新 backend** | 调 GET `/spcode/git-worktrees` → 返回数据多 `locked` / `locked_reason` 字段(老 dashboard 忽略);调不存在的 4 个新端点 → 404(降级) | ✅ 完全兼容 |
| **新 dashboard × 旧 backend** | 调 4 个新端点 → 404 → 前端按钮显示"功能不可用";调 GET worktrees → 旧字段(无 locked),前端容错渲染 | ✅ 降级但不报错 |
| **新 dashboard × 新 backend** | 完整功能 | ✅ 目标状态 |

### 10.2 版本号

| 项目 | 变更 |
|------|------|
| `astrbot_plugin_spcode_toolkit` | **minor bump**:v2.13.x → **v2.14.0** |
| 跨版本影响 | additive only,patch 兼容 |

按现有 SemVer:4 个新 endpoint = minor bump(沿用 git-show v3.8 / git-log v3.7 同样规则)。

---

## §11 交付清单

| 文档 | 路径 | 内容 |
|------|------|------|
| 设计 spec | `docs/superpowers/specs/2026-06-26-git-worktree-management-design.md` | 本文档 |
| 公开 API | `docs/webapi-git-worktree-mgmt-api.md` | 4 端点契约 + i18n 命名空间 |
| README | `README.md` | endpoint 表格 +4 行 |
| AGENTS.md | `AGENTS.md` | endpoint 表格 +1 行 + section 3.7 增量 |
| 测试文档 | 各 `tests/test_*.py` docstring | 用例意图说明 |

---

## 附录 A: 核心不变量(实现时务必保留)

1. **`_loaded_projects` 永不被新代码修改**(Q1=A 延伸)
2. **worktree 写操作的 target path 必然在 primary 的 list 中**(REMOVE/LOCK/UNLOCK)
3. **post-create git-common-dir 必须匹配 primary**(ADD 防越权最后一关)
4. **main worktree 永不可删除**(REMOVE 闸 1 硬禁止,即使 force=true)
5. **dirty 检查是预查非依赖 git stderr**(更明确的 UX)
6. **`worktrees` 字段在成功响应必含**(前端可立即更新 UI,无需再调 GET)
7. **`_parse_git_worktree_porcelain` 扩展是 pure additive**(老客户端零影响)

## 附录 B: 与现有 spec 的引用关系

- 基础防御链思想沿用: `docs/superpowers/specs/2026-06-18-git-worktree-switcher-design.md` §2.3 (git-common-dir 防越权)
- ReasonCode 集中表沿用: `docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md` §4
- 写端点样板沿用: `docs/superpowers/specs/2026-06-23-git-stage-untage-commit-log-design.md` §3 (envelope / preflight / ReasonCode 三件套)
- ETag 模式(GET 端点):本 spec 不直接复用,因 worktree-mgmt 4 个端点都是写,无需 ETag
