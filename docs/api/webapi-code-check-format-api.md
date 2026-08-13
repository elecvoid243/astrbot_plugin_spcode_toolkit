# code-check / code-format Web API

> Author: elecvoid243
> Created: 2026-08-12 18:25 CST
> Backend spec: `docs/superpowers/specs/2026-08-12-code-check-format-webapi-design.md`

本文档供 AstrBot Dashboard / WebUI 消费两个代码工具端点：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/spcode/code-check` | POST | 对 repo 内单文件执行 syntax + style 检查 |
| `/spcode/code-format` | POST | 对 repo 内单文件执行格式化，默认写回，`check=true` 预览 |

---

## 1. 通用约定

两个端点均复用现有 `/spcode/*` Web API envelope：

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "reason": null,
    "stderr": "",
    "elapsed_ms": 12
  }
}
```

共同参数：

| 字段 | 类型 | 必传 | 说明 |
|------|------|------|------|
| `path` | string | 是 | repo-relative 文件路径，不允许绝对路径 |
| `umo` | string | 否 | 会话 ID；缺省使用最近加载项目 |
| `worktree` | string | 否 | 可选 worktree 路径 |

安全边界：

- `path` 经过 `_git_endpoint_preflight` 与 `_validate_repo_relative_file`。
- 拒绝绝对路径、`..`、`.git/`、symlink 逃逸。
- `code-check` 是只读端点；`code-format` 是写端点。
- 并发请求由后端串行化，避免同一文件并发格式化。

---

## 2. `POST /spcode/code-check`

### 2.1 Request body

```json
{
  "path": "src/main.py",
  "linter": "auto",
  "umo": "webchat:FriendMessage:..."
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `path` | string | 是 | — | repo-relative 文件路径 |
| `linter` | string | 否 | `"auto"` | `auto` / `ruff` / `cpplint` / `cppcheck` |

### 2.2 成功响应

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "checked": true,
    "path": "src/main.py",
    "linter": "ruff",
    "issues": [
      {
        "line": 1,
        "message": "expected 2 blank lines",
        "code": "E302"
      }
    ],
    "count": 1,
    "linters": null,
    "proposal": "ruff 发现 1 个问题",
    "directory": "F:/repo",
    "umo": "webchat:...",
    "worktree": "F:/repo",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 35
  }
}
```

说明：

- `count=0` 表示检查通过。
- C/C++ auto 模式保持现有行为：cppcheck 优先，按配置短路；不短路时返回 `linters.cppcheck` 与 `linters.cpplint` 分组结果。
- 前 5 个 issue 可能带 `context` 字段。

### 2.3 fetch 示例

```ts
async function checkCode(path: string, linter?: string) {
  const response = await fetch("/spcode/code-check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, linter: linter ?? "auto" }),
  });
  const envelope = await response.json();
  return envelope.data;
}
```

---

## 3. `POST /spcode/code-format`

### 3.1 Request body

```json
{
  "path": "src/main.py",
  "check": false
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `path` | string | 是 | — | repo-relative 文件路径 |
| `check` | boolean | 否 | `false` | `false` 写回；`true` dry-run，不写回 |

### 3.2 成功响应：写回

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "formatted": true,
    "path": "src/main.py",
    "formatter": "ruff",
    "formatter_options": {"style": "allman", "indent": 4},
    "check": false,
    "changed": true,
    "file_size_before": 123,
    "file_size_after": 118,
    "diff_summary": null,
    "proposal": "ruff 重新格式化了 main.py(-5 字节, 123 → 118)",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 42
  }
}
```

### 3.3 成功响应：dry-run

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "formatted": false,
    "path": "src/main.py",
    "formatter": "ruff",
    "check": true,
    "changed": true,
    "diff_summary": "@@ -1 +1 @@\n-x=1\n+x = 1",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 21
  }
}
```

`check=true` 时永不写回文件。

### 3.4 fetch 示例

```ts
async function formatCode(path: string, dryRun = false) {
  const response = await fetch("/spcode/code-format", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, check: dryRun }),
  });
  const envelope = await response.json();
  return envelope.data;
}
```

---

## 4. ReasonCode

| ReasonCode | 场景 |
|------------|------|
| `invalid_body` | body 非 object，或 `check` 非布尔 |
| `invalid_param` | path 字段格式非法 / linter 非枚举 / formatter 参数非法 |
| `feature_disabled` | 项目加载依赖的 feature flag 关闭 |
| `no_project_loaded` | 未加载项目 |
| `worktree_invalid` | worktree 参数未通过防御链 |
| `directory_missing` | 项目目录不存在 |
| `not_a_git_repo` | 项目不是 Git 仓库 |
| `path_unsafe` | path 越权或包含禁止片段 |
| `file_not_found` | 文件不存在或不是普通文件 |
| `unsupported_media_type` | 扩展名不受对应工具支持 |
| `file_too_large` | format 文件超过 10 MB |
| `tool_unavailable` | ruff / cpplint / cppcheck / astyle 未安装或不可用 |
| `check_failed` | code_check 执行失败 |
| `format_failed` | code_format 执行或写回失败 |

业务层诊断信息位于 `data.details`。

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

type CodeCheckData = {
  checked: boolean;
  path?: string;
  linter?: string;
  issues?: unknown[];
  count?: number;
  linters?: Record<string, unknown> | null;
  proposal?: string | null;
  details?: Record<string, unknown>;
};

type CodeFormatData = {
  formatted: boolean;
  path?: string;
  formatter?: string;
  formatter_options?: { style?: string; indent?: number };
  check?: boolean;
  changed?: boolean;
  file_size_before?: number;
  file_size_after?: number;
  diff_summary?: string | null;
  proposal?: string | null;
  details?: Record<string, unknown>;
};
```
