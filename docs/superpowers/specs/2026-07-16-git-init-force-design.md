# git-init `force` 标志:支持在非空目录初始化 git 仓库

- **Spec ID**: 2026-07-16-git-init-force
- **Author**: elecvoid243
- **Created**: 2026-07-16
- **Status**: approved (pending implementation)
- **Version target**: v2.17.1(内部标记,不 bump metadata.yaml)
- **Predecessor**: `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md`(v2.17.0 git-init 原始设计)
- **Supersedes**: v2.17.0 spec §3.1 中关于 `directory_not_empty` 的"最严格"语义(放宽为 opt-in)

---

## 1. 背景与动机

### 1.1 v2.17.0 现状

`POST /spcode/git-init` 端点(v2.17.0 PR-B)在 `_git_init_preflight` 中执行 4 步检查:

1. path 是否为绝对路径
2. path 是否在黑名单 / 含 `..` 段
3. path 是否存在且为目录(`path_not_directory`)
4. `.git/` 是否已存在(`already_a_git_repo`)
5. 目录是否为空(`directory_not_empty`) - **任何文件都拒绝**

v2.17.0 spec 原文(`docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md` L264):

> `directory_not_empty` 判定:目录里**没有**任何文件(允许 `.` 和 `..`);若有任何文件(包括
> `README.md`、`.gitignore` 等)则拒绝 -- 这是 v2.17.0 的**最严格**语义,避免误覆盖用户已有
> 内容;若用户想"在已有 README 的目录 init",需先手动清空或放到子目录

### 1.2 用户痛点

实际开发场景中,常见需求是**在已有代码的目录上 init git 仓库**(例如:从 zip 解压的项目、从其他
VCS 迁移、本地长期开发但未纳入版本控制)。v2.17.0 的"最严格"语义强制用户先手动清空目录或把代码
移到子目录,体验割裂。

### 1.3 目标

在不破坏 v2.17.0 安全默认的前提下,允许用户**显式 opt-in** 在非空目录上 init git 仓库。

---

## 2. 设计决策

### 2.1 方案对比(brainstorming 已确认)

| 方案 | 行为 | 评估 |
|------|------|------|
| A. 默认允许非空 | 去掉非空检查 | ❌ 失去"防止误操作"护栏;无法区分意图 |
| **B. 加 `force: true` 标志** ⭐ | `force=false`(默认)仍拒绝;`force=true` 跳过非空检查 | ✅ 保留安全默认 + 显式 opt-in + 与现有 `force` 语义一致 |
| C. 智能白名单 | 允许 `.gitignore`/`README.md` 等少量文件 | ❌ 白名单边界模糊,spec 难穷举 |

**采纳方案 B**。

### 2.2 Q1 决策:`.git/` 已存在时 `force=true` 是否豁免?

**采纳 (a) 不豁免** - `already_a_git_repo` 是 hard-ban,与 `git-branch-delete` 的
`branch_is_current` 同性质,`force` 永不绕过。

理由:
- `.git/` 已存在意味着这本来就是 git repo,应使用 `/spcode/project-status` 加载,而非 `git-init`
- `git-init` 语义应限定在"非 git 目录 -> git 目录"
- `git init` 在已有 .git 上是幂等的(reinit 不破坏数据),但允许 force 会导致语义混乱:用户
  无法区分"我成功 init 了一个新 repo"和"我误对一个已有 repo 做了无操作"

### 2.3 `bare=true` 与 `force=true` 的关系

**完全独立,可组合**。`bare` 影响 git 子进程 flag(`--bare`),`force` 影响 preflight 非空检查。
组合 `force=true, bare=true` -> 在非空目录上 init bare repo(语义合法,虽然 bare repo 不该有
working tree,git 自身会处理)。

---

## 3. API 行为规范

### 3.1 请求 schema

```typescript
interface GitInitRequest {
  path: string;                // 必填,绝对路径,必须存在且为目录
  initial_branch?: string;     // 可选,默认 "main"
  bare?: boolean;              // 可选,默认 false
  force?: boolean;             // 【新增】可选,默认 false
}
```

### 3.2 行为矩阵

| 目录状态 | `force=false`(默认) | `force=true` |
|----------|----------------------|--------------|
| 路径不存在 / 不是目录 | `path_not_directory` | `path_not_directory`(**force 不绕过路径防御**) |
| 路径安全黑名单 / `..` 段 / 非绝对路径 | `path_unsafe` | `path_unsafe`(**force 不绕过路径防御**) |
| 完全空目录 | (继续 init) | (继续 init) |
| **非空目录,无 `.git/`** | `directory_not_empty` | ✅ **跳过非空检查,直接 init** |
| **已有 `.git/`(任何 force)** | `already_a_git_repo` | `already_a_git_repo`(**始终拒绝,Q1=a**) |
| git 子进程失败 | `init_failed` | `init_failed` |

### 3.3 关键不变量

1. **`force` 只豁免 `directory_not_empty` 这一步**,不豁免:
   - `path_not_directory`(路径不存在)
   - `path_unsafe`(黑名单 / `..` 段 / 非绝对路径)
   - `already_a_git_repo`(.git 已存在)
2. **`bare=true` 与 `force=true` 独立**,可组合使用
3. **`already_a_git_repo` 是 hard-ban**,`force` 永不绕过
4. **检查顺序不变**:`already_a_git_repo` 检查**在** `directory_not_empty` 检查之前,所以即使
   `force=true`,只要 `.git/` 存在就先返回 `already_a_git_repo`,不会"误以为是非空目录放行"

### 3.4 与 git CLI 行为对齐

`git init` 本身对非空目录的处理:
- 非 bare 模式:直接 init,文件保留(untracked)
- bare 模式:直接 init,文件保留(但 bare repo 不该有 working tree,git 会警告)
- 已有 `.git/`:reinit(幂等,不破坏)

本 spec 的 `force=true` 模式与 git CLI 在"非空无 .git"场景下行为一致;`already_a_git_repo` 的
硬禁是有意收紧(防止用户误把已存在的 repo 当新 repo 处理)。

---

## 4. ReasonCode 与响应字段

### 4.1 ReasonCode 表(无新增)

复用 v2.17.0 已有的 4 个 reason,**不新增 reason code**:

| reason | 触发条件 | `force` 影响 |
|--------|----------|-------------|
| `path_not_directory` | path 不存在或不是目录 | 不豁免 |
| `path_unsafe` | path 含 `..` / 黑名单命中 / 非绝对路径 | 不豁免 |
| `directory_not_empty` | 目录非空且无 `.git/` | **`force=true` 豁免** |
| `already_a_git_repo` | `.git/` 已存在 | **不豁免**(hard-ban) |
| `init_failed` | git 子进程失败 | 不豁免 |

**为什么不需要新增 reason**:v2.17.0 的 4 个 reason 已经精确覆盖所有失败场景,`force` 只是改变
`directory_not_empty` 的触发条件,不引入新失败模式。

### 4.2 响应字段(扩展)

成功响应新增 `force` 回显(便于前端日志/审计):

```typescript
interface GitInitResponse extends ResponseEnvelope<{
  initialized:    true;
  directory:      string;
  initial_branch: string;
  bare:           boolean;
  force:          boolean;       // 【新增】实际使用的 force
  hint:           string;
}> {}
```

失败响应不变(仍包含 `initialized: false` + `path`)。

### 4.3 类型校验

`force` 必须为 `bool` 类型。非 bool 值(如 `"true"` 字符串、`1` int)返回 `invalid_param`:

```python
if not isinstance(force, bool):
    return _make_envelope(
        success=False, reason=ReasonCode.INVALID_PARAM,
        elapsed_ms=_elapsed(), initialized=False, path=path,
    )
```

---

## 5. 向后兼容性

### 5.1 调用方影响矩阵

| 调用方 | `force` 字段 | 行为变化 |
|--------|-------------|----------|
| 旧客户端(不传 `force`) | 缺省 = `false` | **完全无变化** - 与 v2.17.0 行为一致 |
| 新客户端(`force=false`) | 显式 false | 与 v2.17.0 一致 |
| 新客户端(`force=true`) | 显式 true | 新能力:可 init 非空目录 |

### 5.2 兼容性保证

本次变更是**纯加性**(additive)的:
- 不删除任何字段
- 不改变默认行为
- 不改变任何已有 reason code 的语义
- 不破坏 v2.17.0 的任何测试(测试用例仍传 `force=false` 或不传)

### 5.3 版本号策略

- **不 bump 主版本号**:这是 v2.17.0 的纯加性补丁,向后兼容
- 内部标记为 **v2.17.1**(changelog 备注),metadata.yaml 暂不 bump(保持 v2.17.0,等下次大版本一起)

---

## 6. 实现改动点

### 6.1 改动 1:`tools/webapi/git_init.py` handler

新增 `force` 参数读取 + 类型校验,传入 preflight:

```python
# 现状(第 85 行附近):
initial_branch = body.get("initial_branch", "main")
bare = body.get("bare", False)

# 改为:
initial_branch = body.get("initial_branch", "main")
bare = body.get("bare", False)
force = body.get("force", False)        # 【新增】

# 类型校验(紧接现有 bare 校验之后):
if not isinstance(force, bool):
    return _make_envelope(
        success=False, reason=ReasonCode.INVALID_PARAM,
        elapsed_ms=_elapsed(), initialized=False, path=path,
    )

# preflight 调用(第 125 行附近):
err, ctx = await _git_init_preflight(plugin, path=path, force=force)  # 【新增 force=】
```

成功响应 payload 加 `force` 回显:

```python
return _make_envelope(
    success=True, reason=None, elapsed_ms=_elapsed(),
    initialized=True,
    directory=abs_path,
    initial_branch=actual_branch,
    bare=bare,
    force=force,                         # 【新增】
    hint=result["stdout"].strip() or "Initialized empty Git repository",
)
```

### 6.2 改动 2:`tools/webapi/_helpers.py` 的 `_git_init_preflight`

函数签名加 `force: bool = False` 参数,非空检查改为条件触发:

```python
async def _git_init_preflight(
    plugin, *, path: str, force: bool = False
) -> tuple[dict | None, dict | None]:
    # ... 现有 step 1-3(path_unsafe / path_not_directory / already_a_git_repo)不变 ...

    # Step 4: non-empty check - force=true 时豁免
    if not force and any(target.iterdir()):
        return _make_envelope(
            success=False, reason=ReasonCode.DIRECTORY_NOT_EMPTY,
            elapsed_ms=0, path=path,
        ), None

    return None, {"path": str(target)}
```

**关键不变量**:`already_a_git_repo` 检查**在** `directory_not_empty` 检查之前(顺序不变),所以
即使 `force=true`,只要 `.git/` 存在就先返回 `already_a_git_repo`,不会"误以为是非空目录放行"。

### 6.3 改动范围

仅 2 个文件,纯加性改动:
- `tools/webapi/git_init.py`(handler:参数读取 + 类型校验 + preflight 调用 + 响应回显)
- `tools/webapi/_helpers.py`(`_git_init_preflight`:签名 + 非空检查条件化)

**不触及**:其他 5 个 v2.17.0 端点(branch-* / revert)、其他 webapi 端点、conftest、ReasonCode 定义。

---

## 7. 测试策略

### 7.1 保留 v2.17.0 测试(向后兼容验证)

`tests/test_git_init.py` 和 `tests/test_git_init_preflight.py` 中所有现有用例**不修改**,继续
验证 `force=false` / 缺省行为。

### 7.2 新增 `force=true` 测试用例

| 用例 | 输入 | 期望 |
|------|------|------|
| `force=true` + 非空目录(含文件) | `{path, force: true}` + 目录有 `README.md` | ✅ 成功 init,`force=true` 回显 |
| `force=true` + 非空目录(含子目录) | `{path, force: true}` + 目录有 `src/` 子目录 | ✅ 成功 init |
| `force=true` + 已有 `.git/` | `{path, force: true}` + 目录有 `.git/` | ❌ `already_a_git_repo`(验证 hard-ban) |
| `force=true` + 不存在路径 | `{path: "/nonexistent", force: true}` | ❌ `path_not_directory`(验证 force 不绕过路径防御) |
| `force=true` + 黑名单路径 | `{path: 黑名单路径, force: true}` | ❌ `path_unsafe`(验证 force 不绕过路径安全) |
| `force=true` + `bare=true` + 非空目录 | `{path, force: true, bare: true}` + 非空 | ✅ 成功 init bare repo |
| `force=true` + 空目录 | `{path, force: true}` + 空目录 | ✅ 成功 init(与 `force=false` 一致) |
| `force` 类型校验 | `{path, force: "true"}`(字符串) | ❌ `invalid_param` |
| `force` 类型校验 | `{path, force: 1}`(int) | ❌ `invalid_param` |
| `force` 类型校验 | `{path, force: null}` | ✅ 等同 `force=false`(None -> default) |

### 7.3 测试文件组织

新增测试用例追加到 `tests/test_git_init.py`(保持与现有测试同文件,便于维护)。预计新增 ~10 个
测试函数,目标总测试数从 35 增至 ~45。

---

## 8. 前端 UX 建议

### 8.1 二次确认流程

```typescript
async function gitInit(path: string, opts: GitInitOptions) {
  const r = await client.call('/spcode/git-init', 'POST', {
    path, ...opts, force: false,
  });
  if (!r.success && r.reason === 'directory_not_empty') {
    const ok = await confirmDialog({
      title: '目录非空',
      body: `目录 ${path} 已有内容。继续初始化 git 仓库不会删除现有文件,但它们将处于 untracked 状态。是否继续?`,
      confirmText: '仍要初始化',
      cancelText: '取消',
    });
    if (!ok) return;
    return client.call('/spcode/git-init', 'POST', {
      path, ...opts, force: true,    // 二次确认后 force=true 重试
    });
  }
  return r;
}
```

### 8.2 UX 要点

- `directory_not_empty` 是**可恢复**错误(用户确认后重试)
- `already_a_git_repo` 是**不可恢复**错误(引导用户去 `/spcode/project-status` 加载现有 repo)
- `path_not_directory` / `path_unsafe` 是**输入错误**(高亮 path 输入框)
- `init_failed` 是**系统错误**(显示 stderr)

---

## 9. 文档更新清单

| 文件 | 更新内容 |
|------|---------|
| `docs/api/v2.17.0-endpoints-frontend.md` | git-init 端点加 `force` 字段 + 二次确认 UX 示例 |
| `docs/api/webapi-git-init-branch-revert-api.md` | 同上,详细 API reference |
| `docs/superpowers/specs/2026-07-15-git-init-branch-revert-design.md` | 添加 v2.17.1 changelog 备注(指向本 spec) |
| `AGENTS.md` | git-init 行为矩阵更新(加 `force` 列) |
| `README.md` | API 表 git-init 行更新 |

---

## 10. Scope 边界(YAGNI)

**本次不做**:
- ❌ 不加 `--template` / `--separate-git-dir` 等 git init 高级选项
- ❌ 不改 `already_a_git_repo` 的硬禁语义(Q1=a 已确认)
- ❌ 不加白名单文件智能判定(方案 C 已否决)
- ❌ 不改其他 5 个 v2.17.0 端点(branch-* / revert)
- ❌ 不引入新 ReasonCode
- ❌ 不 bump metadata.yaml 版本号

---

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 用户误用 `force=true` 在重要目录 init | (1) 默认 `force=false` 保留护栏;(2) 前端二次确认;(3) `already_a_git_repo` hard-ban 防止覆盖已有 repo |
| `bare=true` + 非空目录 + `force=true` 产生奇怪状态 | git 自身会处理(bare repo 不创建 working tree);用户责任,spec 不额外限制 |
| 旧测试假设非空目录必失败 | 不修改旧测试,新测试单独追加;旧测试用 `force=false` / 缺省,行为不变 |

---

## 12. 验收标准

1. `ruff check .` 无 error
2. `pytest tests/test_git_init.py tests/test_git_init_preflight.py` 全部 PASS(含新增 ~10 个用例)
3. `pytest tests/test_webapi_end_to_end.py` PASS(路由表 smoke 不受影响)
4. v2.17.0 现有测试用例**零修改**,全部继续 PASS(向后兼容)
5. 手动验证:`force=true` + 含 `README.md` 的目录 -> 成功 init,`README.md` 保留为 untracked

---

## 附录 A:与现有 `force` 语义的一致性

v2.14.0+ 已建立的 `force` 语义:

| 端点 | `force` 作用 | 是否绕过 hard-ban |
|------|-------------|-------------------|
| `git-worktree-add` | 跳过 dirty 检查(`-B` 覆盖已有分支) | N/A(无 hard-ban) |
| `git-branch-delete` | `-d` -> `-D`(允许删 unmerged) | **不绕过** `branch_is_current`(硬禁删当前分支) |
| `git-branch-switch` | 切换时 `-f` 跳过 dirty | N/A |
| **`git-init`(本 spec)** | **跳过 `directory_not_empty`** | **不绕过** `already_a_git_repo`(硬禁) |

本 spec 的 `force` 语义与 `git-branch-delete` 完全一致:`force` 豁免**软**检查,不豁免**硬**禁。
