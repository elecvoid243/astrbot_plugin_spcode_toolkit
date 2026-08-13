# Git Commit Amend Web API 设计

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后，下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已分段通过（用户确认日期 2026-08-13，3 段全部确认：§2 端点与请求契约 → §3 执行流程与错误模型 → §4 文件拆分与测试策略）
**作者**: elecvoid243 @ 2026-08-13 11:10 CST
**目标插件 / 仓库**: `astrbot_plugin_spcode_toolkit`
**前置版本**: spcode plugin v2.23.2（已含 git pull/push/remote、code-check/code-format Web API）
**目标版本**: 下一个 minor 版本（本任务不修改 `metadata.yaml`）
**前端消费者**: AstrBot Dashboard / WebUI

---

## 用户决策记录（4 轮澄清）

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: amend 能力范围** | **A** — 仅修改 message | 只暴露 `message`，不暴露 author/committer/verify 等选项 |
| **Q2: staged 改动处理** | **A** — 有 staged 改动则拒绝 | 防止“只想改 message”意外把 staged 改动合入被重写提交 |
| **Q3: merge commit 策略** | **A** — 拒绝 merge commit | parent 数 > 1 时返回 `cannot_amend_merge_commit` |
| **Q4: 端点架构** | **A** — 单一新 POST 端点 | `POST /spcode/git-commit-amend` |

---

## §1 背景与动机

### 1.1 当前状态

现有 `/spcode/git-commit` 端点严格只接受 `message`，完成首次提交；没有 amend 能力。Dashboard 查看提交历史后，用户无法直接修改最近一次提交信息。

`git commit --amend` 默认会：

1. 替换 HEAD 提交对象。
2. 把当前 staged 改动并入被重写提交（这是“改 message”场景的主要坑）。
3. 在 merge commit 上执行时语义更复杂。

因此需要单独的、约束清晰的端点，而不是给 `git-commit` 增加布尔开关。

### 1.2 目标

新增 1 个端点：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/spcode/git-commit-amend` | POST | 修改当前 HEAD 提交信息，不做其它内容变更 |

### 1.3 范围与非目标

**范围内**：

- 单一 POST endpoint
- message 校验、preflight、空仓库/冲突/merge/staged 前置检查
- 复用 `git_commit` 的 env 透传与 stderr 分类
- 统一 envelope、ReasonCode、路由注册、测试与 API 文档

**范围外**：

- `--reset-author` / author/committer 覆盖
- `--no-verify` / 签名 / 多 parent 高级选项
- amend 非 HEAD 提交（需要交互式 rebase）
- 修改提交内容（tree）或合并 staged 改动
- 前端 Dashboard UI 实现
- 修改 `README.md` / `AGENTS.md` / `metadata.yaml`

---

## §2 端点与请求契约

### 2.1 `POST /spcode/git-commit-amend`

请求体：

```json
{
  "message": "fix: updated message",
  "umo": "webchat:...",
  "worktree": "F:/optional-worktree"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `message` | string | 是 | — | 新提交信息；1..8192 字符，支持多行 |
| `umo` | string | 否 | 最近加载项目 | 标准参数 |
| `worktree` | string | 否 | primary worktree | 走现有 6 步防御链 |

行为约束：

- 只调用 `git commit --amend -F -`，仅替换当前 HEAD 提交信息。
- 不暴露 `--no-verify` / `--reset-author` / `--no-edit` / parent 等高级选项。
- 复用现有 `git-commit` 的 `_build_git_env()` 透传 `GIT_AUTHOR_*/GIT_COMMITTER_*`。
- 复用 `_classify_commit_error()` 分类 hook/identity/nothing-to-commit/git-error。
- 复用 `_git_endpoint_preflight()` 与 `?worktree=` 防御链。

### 2.2 安全前检查

执行 amend 前依次检查：

1. 空仓库（HEAD 不是 commit）→ `empty_repository`。
2. 冲突/操作进行中（merge/cherry-pick/revert/rebase）→ `operation_in_progress`。
3. 当前 HEAD 是 merge 提交（parent 数 > 1）→ `cannot_amend_merge_commit`。
4. 存在已暂存改动 → `staged_changes_present`。

---

## §3 执行流程、响应与错误模型

### 3.1 执行流程

```text
POST /spcode/git-commit-amend
  ├─ body / message 校验（str，1..8192）
  ├─ _git_endpoint_preflight
  ├─ 空仓库检查（HEAD 不是 commit → empty_repository）
  ├─ 冲突状态检查（_detect_conflict_operation → operation_in_progress）
  ├─ merge commit 检查（parent 数 > 1 → cannot_amend_merge_commit）
  ├─ staged 检查（_get_staged_files 非空 → staged_changes_present）
  ├─ 记录 before_sha
  ├─ git commit --amend -F -（stdin 读 message，透传 GIT_AUTHOR_*）
  └─ 回读 after_sha / subject / full message
```

merge 提交判定：

```bash
git rev-list --parents -n 1 HEAD
```

输出以空格分隔，首字段是 commit 自身，剩余字段是 parent。parent 数量 `> 1` 即 merge commit。

### 3.2 成功响应

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "amended": true,
    "before_sha": "1111111111111111111111111111111111111111",
    "after_sha": "2222222222222222222222222222222222222222",
    "subject": "fix: updated message",
    "message": "fix: updated message\n\nbody line",
    "files_changed": [],
    "directory": "F:/repo",
    "umo": "webchat:...",
    "worktree": "F:/repo",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 42
  }
}
```

- 即使 message 文本相同，`git commit --amend` 也会生成新的提交对象，因此 `amended=true`、`after_sha` 可能变化。
- `files_changed` 固定为空数组：纯 message amend 不改变 tree，已 staged 改动被前置拒绝。

### 3.3 错误映射

| 场景 | ReasonCode |
|------|------------|
| body 非 object / message 非 str | `invalid_body` |
| message 为空或超过 8192 | `invalid_message` |
| 无 HEAD 提交 | `empty_repository` |
| 冲突操作进行中 | `operation_in_progress` |
| HEAD 是 merge 提交 | `cannot_amend_merge_commit` |
| 有 staged 改动 | `staged_changes_present` |
| hook / identity / nothing-to-commit / 其他 | 复用 `_classify_commit_error` |
| amend 其他失败 | `amend_failed` |

`_classify_commit_error` 在纯 message amend 场景下通常不会返回 `nothing_to_commit`，但保留该映射作为兜底。

---

## §4 文件拆分、测试策略与交付范围

### 4.1 后端文件

新增：

| 文件 | 职责 |
|------|------|
| `tools/webapi/git_commit_amend.py` | `POST /spcode/git-commit-amend` handler |

修改：

| 文件 | 职责 |
|------|------|
| `tools/webapi/_helpers.py` | 新增 ReasonCode：`cannot_amend_merge_commit`、`staged_changes_present`、`amend_failed` |
| `tools/webapi/__init__.py` | 注册 1 条 POST 路由；路由数 55 → 56，POST 数 36 → 37 |

### 4.2 测试文件

新增：

- `tests/test_git_commit_amend.py`

修改：

- `tests/test_reason_code.py`
- `tests/test_webapi_end_to_end.py`

测试重点：

- 成功 amend：message 替换、`before_sha != after_sha`、subject 正确
- 多行 message 原样保留
- 空仓库 → `empty_repository`
- merge commit → `cannot_amend_merge_commit`
- staged 改动 → `staged_changes_present`
- message 空 / 超长 / 非 str → `invalid_message` / `invalid_body`
- 冲突进行中 → `operation_in_progress`（mock）
- hook 拒绝 / identity 未设（mock 或真实 hook）
- 路由 smoke 与 ReasonCode 断言

测试使用真实本地临时仓库，不依赖远程网络。

### 4.3 文档交付

新增：

- `docs/superpowers/specs/2026-08-13-git-commit-amend-design.md`
- `docs/superpowers/plans/2026-08-13-git-commit-amend.md`
- `docs/api/webapi-git-commit-amend-api.md`

不修改：

- `README.md`
- `AGENTS.md`
- `metadata.yaml`

### 4.4 验证门槛

- 所有新逻辑先写失败测试，再实现。
- 新增与受影响 focused tests 全部通过。
- 新增/修改 Python 文件 ruff 0 issues。
- 路由 smoke 通过。
- `git diff --check` 无错误。
- 工作区干净后本地提交；不推送、不创建 PR。

---

## §5 验收标准

- [ ] `POST /spcode/git-commit-amend` 能替换 HEAD 提交信息并返回 before/after SHA。
- [ ] 多行 message 原样保存。
- [ ] 空仓库返回 `empty_repository`。
- [ ] merge commit 返回 `cannot_amend_merge_commit`。
- [ ] staged 改动返回 `staged_changes_present`。
- [ ] 冲突操作进行中返回 `operation_in_progress`。
- [ ] message 校验与 `git-commit` 保持一致。
- [ ] 路由表、ReasonCode、Dashboard API 文档同步更新。
