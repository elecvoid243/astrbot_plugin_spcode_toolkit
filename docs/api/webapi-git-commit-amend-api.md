# git-commit-amend Web API

> Author: elecvoid243
> Created: 2026-08-13 11:20 CST
> Backend spec: `docs/superpowers/specs/2026-08-13-git-commit-amend-design.md`

`POST /spcode/git-commit-amend` 修改当前 HEAD 提交信息，不做其它内容变更。

---

## 1. Request body

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
| `worktree` | string | 否 | primary worktree | 走现有防御链 |

安全前检查：

- 空仓库 → `empty_repository`
- 冲突/操作进行中 → `operation_in_progress`
- HEAD 是 merge commit → `cannot_amend_merge_commit`
- 有 staged 改动 → `staged_changes_present`

## 2. Success response

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

## 3. ReasonCode

| ReasonCode | 场景 |
|------------|------|
| `invalid_body` | body 非 object |
| `invalid_message` | message 非 str / 空 / 超过 8192 |
| `feature_disabled` | 项目加载依赖的 feature flag 关闭 |
| `no_project_loaded` | 未加载项目 |
| `worktree_invalid` | worktree 参数未通过防御链 |
| `directory_missing` | 项目目录不存在 |
| `not_a_git_repo` | 项目不是 Git 仓库 |
| `empty_repository` | 无 HEAD 提交 |
| `operation_in_progress` | merge/cherry-pick/revert/rebase 进行中 |
| `cannot_amend_merge_commit` | HEAD 是 merge commit |
| `staged_changes_present` | 存在已暂存改动 |
| `hook_rejected` | pre-commit / commit-msg hook 拒绝 |
| `identity_not_set` | user.name / user.email 未设置 |
| `nothing_to_commit` | 无改动可提交（兜底） |
| `amend_failed` | amend 其他执行失败 |
| `git_error` | 其他 Git 错误 |

## 4. TypeScript

```ts
type GitCommitAmendData = {
  amended: boolean;
  before_sha?: string;
  after_sha?: string;
  subject?: string;
  message?: string;
  files_changed?: string[];
  staged_files?: string[];
  staged_count?: number;
  operation?: string | null;
};
```

## 5. fetch 示例

```ts
async function amendCommitMessage(message: string) {
  const response = await fetch("/spcode/git-commit-amend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  const envelope = await response.json();
  return envelope.data;
}
```
