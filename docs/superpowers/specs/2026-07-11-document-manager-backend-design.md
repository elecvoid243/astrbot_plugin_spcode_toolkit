# /spcode/git-file + /spcode/docs — 文档管理子页面后端端点

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后,下一步调用 writing-plans 技能输出实现计划。

**状态**: 🟡 设计稿(待用户复核,复核通过后改为 ✅)
**作者**: elecvoid243 @ 2026-07-11 20:46
**目标插件**: `astrbot_plugin_spcode_toolkit`(主干)
**前置版本**: 当前 HEAD 包含 20 个 `/spcode/*` 端点;本 spec 新增 1 个新端点 + 1 个新资源(`/spcode/docs` 的 POST/PATCH/DELETE)
**配套 UI**: 前端 dashboard "GitDiff 侧栏 · 文档管理" 子页面(由 `F:\github\Astrbot` 仓库 spec A 定义,本文档为其 API 契约)
**前置 spec**: `2026-07-11-document-manager-design.md`(spec A,前端)在 `F:\github\Astrbot\docs\superpowers\specs\`

## 用户环境约束

本端点群在**服务端**(AstrBot 进程所在机器)执行 git / 文件系统操作,要求与现有端点保持一致:

| 依赖 | 要求 | 缺失时的行为 |
|------|------|--------------|
| `git` 命令行工具 | 安装在系统中(任意平台) | 端点返回 `reason: "git_unavailable"`;不影响插件加载 |
| 已加载 spcode 项目 | dashboard 通过 `/spcode/project-status` 加载 | 端点返回 `reason: "no_project_loaded"` |
| `git_path` 配置 | 可选;未配置时回退 PATH | 同 `/spcode/file-restore` / `git-log` |
| worktree 6 步防御 | `_validate_worktree_param` 已知 | 端点返回 `reason: "worktree_invalid"` |

## 目标

为前端 dashboard 的"文档管理"子页面提供 4 个新端点,行为如下:

1. **`GET /spcode/git-file?ref=&path=`** — 给定 ref 与仓库相对路径,返回该版本下的**完整文件内容**(blob)。**新增端点**。原因:现有 `/spcode/git-show?path=` 返回的是 `patch: string` (unified diff),不能用于"渲染该历史版本的完整 Markdown"。
2. **`POST /spcode/docs`** — 创建 / 覆盖 docs 文件到工作区(**upsert**)。不调 git add;用户后续走 Git Diff tab 提交。
3. **`PATCH /spcode/docs`** — 重命名 docs 文件(**纯文件系统 mv**)。git 后续靠相似度检测识别为 rename。
4. **`DELETE /spcode/docs`** — 从工作区删除 docs 文件(直接 unlink)。不调 git rm;用户后续走 Git Diff tab 提交。

**重要发现**:现有 `/spcode/git-log` **已支持** `?path=` 过滤(代码 `git_log.py:412` 接 `path` 参数,第 605 行 `if path: log_args += ["--", path]`)。所以"per-file 历史"无需后端改动。spec B 的范围**只**含以上 4 项。

## 用户决策记录

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: POST 语义** | **A** — Upsert,POST 永远覆盖 | "保存" 单一语义,前端不区分新建 vs 覆盖;新建走前端预检 + 后端正常 upsert |
| **Q2: PATCH 重命名** | **A** — 纯文件系统 mv | 不调 git mv,git 后续 rename detection 后置;不需追踪判定,实现最简 |
| **Q3: 写入稳健性** | **B** — 最小版 (`Path.write_text` + 2 MB 上限 + 静默 overwrite) | 不原子、不备份、不 mtime 冲突检查;spec 文档协作场景够用 |
| **Q4: GET 缓存** | **A** — 不实现 ETag,返回 `Cache-Control: no-store` | `git show <sha>:<path>` 跑得快(~50ms),文档版本变更频率低,ETag 收益不抵复杂度 |
| **Q5: DELETE 请求体格式** | **A** — body 内 `path` 字段 | 与 `/spcode/file-restore` / `/spcode/file-discard-hunk` 的现有约定一致;spec A 文字同步调整 |
| **Q6: PATCH 不允许覆盖** | **A** — `new_path` 已存在 → `file_exists` 错误 | 避免误删;前端 UI 提前拦截提示 |
| **Q7: 路径后缀约束** | **A** — 必须 `.md` | 与 "文档管理" 子页面定位一致;后端兜底防用户绕过前端写非 markdown |

## 核心设计

### 1. URL 契约

| 端点 | Method | Path | Content-Type | Body | 鉴权 |
|------|--------|------|--------------|------|------|
| `git-file` | `GET` | `/spcode/git-file` | n/a (query) | n/a | AstrBot 框架统一鉴权 |
| `docs` create / upsert | `POST` | `/spcode/docs` | `application/json` | `{umo?, worktree?, path, content}` | 同上 |
| `docs` rename | `PATCH` | `/spcode/docs` | `application/json` | `{umo?, worktree?, path, new_path}` | 同上 |
| `docs` delete | `DELETE` | `/spcode/docs` | `application/json` | `{umo?, worktree?, path}` | 同上 |

**路由注册位置**:
- `tools/webapi/__init__.py:ROUTES` 末尾追加 4 条
- `tools/webapi/__init__.py:HANDLERS` 同步追加
- 经 `register_webapi_routes(plugin)` 自动生效;`main.py` 无需改动

**为什么 body 传 path 而非 URL 路径**:
- 与现有 `file-restore` / `file-discard-hunk` 保持一致
- 避免 Windows 路径(反斜杠 / 盘符)的 URL 编码
- patch 文本 / content 可能很大,body 容量无 query 那么严苛
- spec A 文字同步把"DELETE /spcode/docs/{path}"改成 body 形式

### 2. 统一响应信封(沿用现有约定)

```json
{ "status": "ok", "data": { /* 端点特定 */ } }
```

- `status` 恒为 `"ok"`
- `data.reason`: 成功时 `null`;失败时为 reason code
- `data.elapsed_ms`: 端到端耗时(毫秒)
- `data.stderr`: 写端点 hook 失败时填;读端点失败也填
- HTTP 200 永远返回,业务错误用 `data.reason` 表达

### 3. 端点契约

#### 3.1 `GET /spcode/git-file`

**Query 参数**

| 名称 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `umo` | 否 | string | 项目标识;不传则用最近一个加载的项目 |
| `worktree` | 否 | string | 已注册 worktree 名;不传 = 主工作树 |
| `ref` | 是 | string | 任意 git ref(commit / branch / tag / `HEAD~n`);默认 `HEAD` |
| `path` | 是 | string | 仓库相对路径,被 `_validate_repo_relative_file` 4 步防御 |

**成功响应(200)**

```json
{
  "status": "ok",
  "data": {
    "loaded": true,
    "directory": "/abs/path/to/repo",
    "umo": "...",
    "worktree": "/abs/path/to/repo",
    "ref": "HEAD",
    "resolved_sha": "418bb365...",
    "path": "docs/spec.md",
    "content": "# Spec\n\n...",
    "is_binary": false,
    "size": 1234,
    "truncated": false,
    "max_bytes": 1048576,
    "elapsed_ms": 12,
    "reason": null,
    "stderr": ""
  }
}
```

**失败 reason 码**

| 码 | 触发 |
|---|------|
| `feature_disabled` | agentsmd_enabled + codegraph_enabled 不同时为 true |
| `no_project_loaded` | umo 解析后查不到加载的项目 |
| `worktree_invalid` | worktree 6 步防御失败 |
| `directory_missing` | 项目根目录不存在 |
| `not_a_git_repo` | 根目录非 git 仓库 |
| `git_unavailable` | `git` 不在 PATH |
| `invalid_param` | ref 长度 > 512 / path 缺失 / path 含换行 NUL / path 长度 > 512 |
| `path_unsafe` | `_validate_repo_relative_file` 失败(越权 / 含 `..` / 绝对路径) |
| `ref_not_found` | `git show <ref>:<path>` stderr 含 `bad object` / `unknown revision` / `bad revision` / `not a commit` |
| `empty_repository` | ref 在空仓库中 |
| `file_too_large` | blob 字节数超过 1 MB,`truncated=true`,`content` 截到 1 MB |
| `file_missing_at_ref` | ref 解析成功但 `git show <sha>:<path>` 报 "exists on disk, but not in" / "does not exist in" / "path not in" |
| `git_error` | 兜底 |

**关键实现点**

```python
# 1. 解析 ref + 长度
ref = (_qget("ref") or "HEAD").strip()
if not ref or len(ref) > 512: return invalid_param

# 2. path 校验
target_path = _qget("path", "").strip()
if not target_path or len(target_path) > 512: return invalid_param
if "\n" in target_path or "\r" in target_path or "\x00" in target_path:
    return invalid_param
target, path_err = _validate_repo_relative_file(target_path, Path(directory))
if path_err is not None: return path_unsafe

# 3. 解析 ref 到 sha
resolve = await _run_git_async(
    [git_bin, "-C", directory, "-c", "color.ui=never",
     "rev-parse", f"{ref}^{{commit}}"],
    encoding="utf-8",
)
if not resolve["ok"] or not resolve["stdout"]: return ref_not_found

resolved_sha = resolve["stdout"]

# 4. 读 blob
show = await _run_git_async(
    [git_bin, "-C", directory, "-c", "color.ui=never",
     "show", f"{resolved_sha}:{target_path}"],
    encoding="utf-8",
)
if not show["ok"]:
    stderr_lower = show["stderr"].lower()
    if "exists on disk, but not in" in stderr_lower \
            or "does not exist in" in stderr_lower \
            or "path not in" in stderr_lower:
        return file_missing_at_ref
    if "bad revision" in stderr_lower or "bad object" in stderr_lower:
        return ref_not_found
    return git_error

# 5. 字节 / binary 判定 / 截断
raw_bytes = show["stdout"].encode("utf-8")
is_binary = b"\x00" in raw_bytes[:8000]
truncated = False
content = show["stdout"]
if is_binary:
    content = ""
elif len(raw_bytes) > 1 * 1024 * 1024:
    truncated = True
    content = raw_bytes[:1 * 1024 * 1024].decode("utf-8", errors="replace")
size = len(content.encode("utf-8"))

# 6. 返回(no Cache-Control)
return _JSONResponseCompat(envelope, headers={"Cache-Control": "no-store"})
```

#### 3.2 `POST /spcode/docs`

**Body 字段(JSON)**

| 名称 | 必填 | 类型 | 约束 |
|------|------|------|------|
| `umo` | 否 | string | 不传则用最近项目 |
| `worktree` | 否 | string | 不传 = 主工作树 |
| `path` | 是 | string | 仓库相对路径;以 `.md` 结尾;4 步防御 |
| `content` | 是 | string | utf-8 文本;`len(content.encode("utf-8"))` ≤ 2 MB |

**成功响应(200)**

```json
{
  "status": "ok",
  "data": {
    "saved": true,
    "created": false,
    "directory": "/abs/path/to/repo",
    "umo": "...",
    "worktree": "/abs/path/to/repo",
    "path": "docs/new.md",
    "size": 1234,
    "elapsed_ms": 8,
    "reason": null,
    "stderr": ""
  }
}
```

**失败 reason 码**

| 码 | 触发 |
|---|------|
| `feature_disabled` | 同 §3.1 |
| `no_project_loaded` | 同 §3.1 |
| `worktree_invalid` | 同 §3.1 |
| `directory_missing` | 同 §3.1 |
| `not_a_git_repo` | 同 §3.1 |
| `git_unavailable` | 同 §3.1 |
| `invalid_body` | body 不是 dict / 必填字段类型错 |
| `invalid_param` | path 不以 `.md` 结尾 / path 长度 > 512 / content 字节 > 2 MB |
| `path_unsafe` | `_validate_repo_relative_file` 失败 |
| `git_error` | 兜底 |

**关键实现点**

```python
# 1. 解析 body
body = await web.request.json(default=None)
if not isinstance(body, dict): return invalid_body
path = body.get("path", "")
content = body.get("content", "")
if not isinstance(content, str): return invalid_body

# 2. path 校验
p = path.strip()
if not p or len(p) > 512: return invalid_param
if "\n" in p or "\r" in p or "\x00" in p: return invalid_param
if not p.endswith(".md"): return invalid_param

# 3. 字节大小
content_bytes = content.encode("utf-8")
if len(content_bytes) > 2 * 1024 * 1024: return invalid_param

# 4. preflight
err, ctx = await _git_endpoint_preflight(plugin, umo, worktree)
if err: return err
directory = ctx["directory"]

# 5. path 4 步防御
target, path_err = _validate_repo_relative_file(p, Path(directory))
if path_err: return path_unsafe

# 6. upsert(决策 B: 直接写,无原子)
target.parent.mkdir(parents=True, exist_ok=True)
created = not target.exists()
target.write_text(content, encoding="utf-8")

return envelope(saved=True, created=created, ...)
```

#### 3.3 `PATCH /spcode/docs`

**Body 字段**

| 名称 | 必填 | 类型 | 约束 |
|------|------|------|------|
| `umo` | 否 | string | |
| `worktree` | 否 | string | |
| `path` | 是 | string | 旧路径(必须存在);以 `.md` 结尾 |
| `new_path` | 是 | string | 新路径;以 `.md` 结尾;不允许已存在 |

**成功响应(200)**

```json
{
  "status": "ok",
  "data": {
    "renamed": true,
    "directory": "/abs/path/to/repo",
    "umo": "...",
    "worktree": "/abs/path/to/repo",
    "path": "docs/old.md",
    "new_path": "docs/new.md",
    "elapsed_ms": 4,
    "reason": null,
    "stderr": ""
  }
}
```

**失败 reason 码**

| 码 | 触发 |
|---|------|
| (复用 §3.2 全表) | |
| `invalid_param` | `new_path` 不以 `.md` 结尾 / 长度 > 512 / `path == new_path` |
| `path_unsafe` | 旧或新路径 4 步防御失败 |
| `file_not_found` | 旧路径不存在 |
| `file_exists` | 新路径已存在(spec 决策 Q6:不允许覆盖式 rename) |
| `git_error` | 兜底 |

**关键实现点**

```python
# 1. 校验 path + new_path(两个都跑 _validate_doc_path)
# 2. if old_path == new_path: return invalid_param
# 3. preflight
# 4. 两个 path 都 4 步防御
old_target, err1 = _validate_repo_relative_file(old_path, Path(directory))
new_target, err2 = _validate_repo_relative_file(new_path, Path(directory))
if err1 or err2: return path_unsafe
# 5. 检查旧存在 + 新不存在
if not old_target.exists(): return file_not_found
if new_target.exists(): return file_exists
# 6. 决策 A: 纯文件系统 mv
import os
new_target.parent.mkdir(parents=True, exist_ok=True)
os.replace(old_target, new_target)  # 跨平台原子;因 step 5 保证 new 不存在,语义安全
```

#### 3.4 `DELETE /spcode/docs`

**Body 字段**

| 名称 | 必填 | 类型 | 约束 |
|------|------|------|------|
| `umo` | 否 | string | |
| `worktree` | 否 | string | |
| `path` | 是 | string | 仓库相对路径(必须存在);以 `.md` 结尾 |

**成功响应(200)**

```json
{
  "status": "ok",
  "data": {
    "deleted": true,
    "directory": "/abs/path/to/repo",
    "umo": "...",
    "worktree": "/abs/path/to/repo",
    "path": "docs/old.md",
    "elapsed_ms": 3,
    "reason": null,
    "stderr": ""
  }
}
```

**失败 reason 码**

| 码 | 触发 |
|---|------|
| (复用 §3.2 全表) | |
| `path_unsafe` | 4 步防御失败 |
| `file_not_found` | 路径不存在 |
| `git_error` | 兜底(`target.is_dir() == True` 时 unlink 抛 `IsADirectoryError`,归 `git_error`) |

**关键实现点**

```python
# 1. 校验(同 POST)
# 2. preflight
# 3. path 4 步防御
# 4. 检查存在
if not target.exists(): return file_not_found
# 5. 决策: 直接 unlink
target.unlink()
```

### 4. 跨端点通用规则

#### 4.1 is_binary 判定(git-file)

```python
# 简化(spec B 用):只看 NUL 判定
# - 头 8000 字节里有 NUL (\x00) → is_binary=True,content=""
# - 否则 → 文本(content 已 utf-8 decode)
HEAD_BYTES = 8000
is_binary = b"\x00" in raw_bytes[:HEAD_BYTES]
```

#### 4.2 size 字段

- 始终是 `content.encode("utf-8")` 后的字节数
- 对 `git-file`:若 `truncated=true`,size 字段反映**返回的** content 字节数(而非原始 blob 字节数);原始字节数在 stderr 描述里附"original size = N"

#### 4.3 路径校验顺序(POST/PATCH/DELETE 一致)

1. 类型:必须 str
2. 长度:≤ 512
3. 字符:不含换行 / NUL
4. (POST/PATCH/DELETE 额外) 必须以 `.md` 结尾
5. `_validate_repo_relative_file(path, worktree_dir)`:4 步防御

> PATCH 中要校验**两个**路径(旧 / 新),任一失败都 `path_unsafe`。

#### 4.4 stdin 喂 patch 文本(沿用现有约定)

不适用本 spec(无 `git apply` 调用);但若未来扩展要喂 patch,沿用 `file-discard-hunk` 的 `_run_git_async(..., input_text=...)` 模式。

### 5. reason 码汇总

#### 5.1 复用既有码(`tools/webapi/_helpers.py:ReasonCode` 已有)

| 码 | spec B 用途 |
|---|---|
| `feature_disabled` | 全部 4 端点 |
| `no_project_loaded` | 全部 4 端点 |
| `worktree_invalid` | 全部 4 端点 |
| `directory_missing` | 全部 4 端点 |
| `not_a_git_repo` | 全部 4 端点 |
| `git_unavailable` | 全部 4 端点 |
| `git_error` | 全部 4 端点(兜底) |
| `empty_repository` | git-file ref 在空仓库 |
| `invalid_body` | POST/PATCH/DELETE body 不是 dict |
| `invalid_param` | 长度 / 字符 / 类型 / 大小超出 |
| `path_unsafe` | `_validate_repo_relative_file` 失败 |
| `ref_not_found` | git-file ref 解析失败 |
| `file_not_found` | DELETE / PATCH 旧路径不存在 |

#### 5.2 新增码(写入 `_helpers.py:ReasonCode`)

```python
# ── /spcode/docs 专用(spec B 2026-07-11) ──
FILE_TOO_LARGE = "file_too_large"           # git-file: blob 超过 1 MB
FILE_MISSING_AT_REF = "file_missing_at_ref" # git-file: ref 解析成功但 path 不在 ref 下
FILE_EXISTS = "file_exists"                 # PATCH: new_path 已存在
```

### 6. i18n / 前端契约(consume 端)

本 spec 不直接做 i18n;所有 reason 码是稳定字符串,前端用 i18n key 翻译。spec A 已在 `documentManager.*` 命名空间下提供全部翻译键。

| reason 码 | spec A i18n key(已存在,部分复用) | zh-CN 翻译 |
|---|---|---|
| `feature_disabled` | `documentManager.editor.saveError` | "保存失败" |
| `no_project_loaded` | `documentManager.editor.saveError` | "保存失败:未加载项目" |
| `worktree_invalid` | `documentManager.editor.saveError` | "保存失败:工作树无效" |
| `directory_missing` | `documentManager.editor.saveError` | "保存失败:项目根目录不存在" |
| `not_a_git_repo` | `documentManager.editor.saveError` | "保存失败:项目不是 git 仓库" |
| `git_unavailable` | `documentManager.editor.saveError` | "保存失败:git 命令不可用" |
| `git_error` | `documentManager.editor.saveError` + stderr | "保存失败:<stderr>" |
| `empty_repository` | `documentManager.history.noHistory` | "此文档还未被提交过任何历史" |
| `invalid_param` | `documentManager.editor.saveError` | "保存失败:参数无效" |
| `invalid_body` | `documentManager.editor.saveError` | "保存失败:请求体无效" |
| `path_unsafe` | `documentManager.editor.saveError` | "保存失败:路径越权" |
| `ref_not_found` | `documentManager.history.loadFail` | "无法读取历史版本:找不到该版本" |
| `file_too_large` | `documentManager.history.loadFail` | "无法读取历史版本:文件过大" |
| `file_missing_at_ref` | `documentManager.history.loadFail` | "无法读取历史版本:该版本下不存在此文件" |
| `file_exists` | `documentManager.editor.renameUnavailable` 复用 | "重命名失败:目标文件已存在" |
| `file_not_found` | `documentManager.editor.deleteError` | "删除失败:文件不存在" |

> spec A 实施阶段需检查 i18n 文案是否覆盖以上全部 reason 码;缺则补 key。

### 7. spec A ↔ spec B 契约 cross-check

| spec A 期望 | spec B 提供 | 一致性 |
|---|---|---|
| `GET /spcode/git-file?ref=&path=` 返回 `{content, is_binary, sha, path, reason, success}` | `{loaded, directory, umo, worktree, ref, resolved_sha, path, content, is_binary, size, truncated, max_bytes, elapsed_ms, reason, stderr}` | ✅ 多 envelope 字段(`loaded/directory/umo/worktree/size/truncated`);前端用 `data.content` / `data.is_binary` / `data.reason`;`data.path` / `data.ref` / `data.resolved_sha` 也可用 |
| `POST /spcode/docs` body `{path, content}`, 成功回 `{success, reason, path, sha?}` | `{saved, created, directory, umo, worktree, path, size, elapsed_ms, reason, stderr}` | ✅ `data.saved` / `data.created` / `data.reason` / `data.path` 全部可读 |
| `DELETE /spcode/docs/{path}` (spec A 文字) | body 内 `path` 字段(spec B 决策) | ⚠️ **spec A 文字需调整**:从 URL 路径改为 body 形式。前端实现无需改(axios 拦截器可统一处理) |
| `PATCH /spcode/docs` (spec A 标记 stub) | spec B 真实实现,body `{path, new_path}`,返回 `{renamed, path, new_path}` | ✅ spec A 旧 stub 现在变真实,UI 自动激活 |

### 8. 文件清单

#### 8.1 新增文件

```
F:\github\astrbot_plugin_spcode_toolkit\
├── tools\webapi\
│   ├── git_file.py          ← 新建:GET /spcode/git-file handler
│   └── docs_crud.py         ← 新建:POST/PATCH/DELETE /spcode/docs(单一模块 3 个 handler)
├── tests\
│   ├── test_git_file.py     ← 新建
│   └── test_docs_crud.py    ← 新建
```

#### 8.2 修改文件

```
F:\github\astrbot_plugin_spcode_toolkit\
└── tools\webapi\
    ├── __init__.py          ← 头部 import + ROUTES 追加 4 条 + HANDLERS 追加
    └── _helpers.py          ← ReasonCode 新增 3 个码
```

#### 8.3 不修改文件(明确边界)

- `main.py` — 由 `register_webapi_routes(plugin)` 自动接管,无需改
- `tools/webapi/git_log.py` — **已支持** `?path=`,无需改
- `tools/webapi/git_show.py` — 不动
- `tools/webapi/file_browser.py` — 不动
- 任何 `tools/project/*` / `tools/agentsmd/*` 模块 — 不动

### 9. 实施细节

#### 9.1 git_file.py 模块骨架

```python
# tools/webapi/git_file.py
"""GET /spcode/git-file — 给定 ref 下某文件的完整内容(blob)。

Spec: docs/superpowers/specs/2026-07-11-document-manager-backend-design.md §3.1
"""

from __future__ import annotations
import logging
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    _JSONResponseCompat,
    _git_endpoint_preflight,
    _make_envelope,
    _run_git_async,
    _validate_repo_relative_file,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_FILE_BLOB_BYTES = 1 * 1024 * 1024  # 1 MB
MAX_PARAM_LENGTH = 512
HEAD_BYTES = 8000  # binary 探测窗口
_NO_STORE = {"Cache-Control": "no-store"}


def _qget(query, key, default=None):
    try:
        v = query.get(key)
        return v if v else default
    except Exception:
        return default


async def handle(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
) -> dict:
    t0 = _time.time()

    def _elapsed() -> int:
        return int((_time.time() - t0) * 1000)

    from astrbot.api import web
    query = web.request.query

    ref = (_qget(query, "ref") or "HEAD").strip()
    if not ref or len(ref) > MAX_PARAM_LENGTH:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False, umo=umo, worktree=worktree,
        )

    target_path = _qget(query, "path")
    if not target_path or not isinstance(target_path, str):
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False, umo=umo, worktree=worktree,
        )
    target_path = target_path.strip()
    if len(target_path) > MAX_PARAM_LENGTH:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False, umo=umo, worktree=worktree,
        )
    if "\n" in target_path or "\r" in target_path or "\x00" in target_path:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(), loaded=False, umo=umo, worktree=worktree,
        )

    # preflight
    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed()
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    # path 4 步防御
    target, path_err = _validate_repo_relative_file(target_path, Path(directory))
    if path_err is not None:
        return _make_envelope(
            success=False, reason=ReasonCode.PATH_UNSAFE,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
        )

    git_bin = plugin._git_binary()
    git_prefix = [git_bin, "-C", directory, "-c", "color.ui=never"]

    # 解析 ref 到 sha
    resolve = await _run_git_async(
        git_prefix + ["rev-parse", f"{ref}^{{commit}}"],
        encoding="utf-8",
    )
    if not resolve["ok"] or not resolve["stdout"]:
        stderr = (resolve.get("stderr", "") or resolve.get("error", "")).lower()
        if "bad revision" in stderr or "unknown revision" in stderr \
                or "bad object" in stderr or "not a commit" in stderr:
            reason = ReasonCode.REF_NOT_FOUND
        elif "does not have any commits" in stderr or "ambiguous" in stderr:
            reason = ReasonCode.EMPTY_REPOSITORY
        else:
            reason = ReasonCode.GIT_ERROR
        return _make_envelope(
            success=False, reason=reason,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=resolve.get("stderr", "") or resolve.get("error", ""),
        )

    resolved_sha = resolve["stdout"]

    # 读 blob
    show = await _run_git_async(
        git_prefix + ["show", f"{resolved_sha}:{target_path}"],
        encoding="utf-8",
    )
    if not show["ok"]:
        stderr_lower = (show.get("stderr", "") or show.get("error", "")).lower()
        if "exists on disk, but not in" in stderr_lower \
                or "does not exist in" in stderr_lower \
                or "path not in" in stderr_lower:
            return _make_envelope(
                success=False, reason=ReasonCode.FILE_MISSING_AT_REF,
                elapsed_ms=_elapsed(), loaded=False,
                directory=directory, umo=effective_umo, worktree=directory,
                ref=ref, resolved_sha=resolved_sha, path=target_path,
                stderr=show.get("stderr", "") or show.get("error", ""),
            )
        if "bad revision" in stderr_lower or "bad object" in stderr_lower:
            return _make_envelope(
                success=False, reason=ReasonCode.REF_NOT_FOUND,
                elapsed_ms=_elapsed(), loaded=False,
                directory=directory, umo=effective_umo, worktree=directory,
                stderr=show.get("stderr", ""),
            )
        return _make_envelope(
            success=False, reason=ReasonCode.GIT_ERROR,
            elapsed_ms=_elapsed(), loaded=False,
            directory=directory, umo=effective_umo, worktree=directory,
            stderr=show.get("stderr", "") or show.get("error", ""),
        )

    raw = show["stdout"]
    raw_bytes = raw.encode("utf-8")
    is_binary = b"\x00" in raw_bytes[:HEAD_BYTES]
    truncated = False
    content = raw
    if is_binary:
        content = ""
    elif len(raw_bytes) > MAX_FILE_BLOB_BYTES:
        truncated = True
        content = raw_bytes[:MAX_FILE_BLOB_BYTES].decode("utf-8", errors="replace")
    size = len(content.encode("utf-8"))

    return _JSONResponseCompat(
        _make_envelope(
            success=True, elapsed_ms=_elapsed(),
            loaded=True,
            directory=directory, umo=effective_umo, worktree=directory,
            ref=ref, resolved_sha=resolved_sha, path=target_path,
            content=content, is_binary=is_binary, size=size,
            truncated=truncated, max_bytes=MAX_FILE_BLOB_BYTES,
        ),
        status_code=200, headers=_NO_STORE,
    )
```

#### 9.2 docs_crud.py 公共骨架

```python
# tools/webapi/docs_crud.py
"""POST / PATCH / DELETE /spcode/docs — 文档 CRUD(工作区 only,无 git 操作)。

Spec: docs/superpowers/specs/2026-07-11-document-manager-backend-design.md §3.2-§3.4
"""

from __future__ import annotations
import logging
import os
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from ._helpers import (
    _git_endpoint_preflight,
    _make_envelope,
    _validate_repo_relative_file,
    ReasonCode,
)

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)

MAX_PATH_LENGTH = 512
MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MB


def _elapsed(t0: float) -> int:
    return int((_time.time() - t0) * 1000)


async def _read_body() -> dict | None:
    from astrbot.api import web
    try:
        body = await web.request.json(default=None)
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _validate_doc_path(path: object) -> str | None:
    """返回 None 表示 OK;否则返回 reason 码。"""
    if not isinstance(path, str):
        return ReasonCode.INVALID_BODY
    p = path.strip()
    if not p or len(p) > MAX_PATH_LENGTH:
        return ReasonCode.INVALID_PARAM
    if "\n" in p or "\r" in p or "\x00" in p:
        return ReasonCode.INVALID_PARAM
    if not p.endswith(".md"):
        return ReasonCode.INVALID_PARAM
    return None


# ── POST ─────────────────────────────────────────────
async def handle_post_docs(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    t0 = _time.time()
    if body is None:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY,
                              elapsed_ms=_elapsed(t0))

    path = body.get("path", "")
    content = body.get("content", "")
    if not isinstance(content, str):
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY,
                              elapsed_ms=_elapsed(t0))

    err_reason = _validate_doc_path(path)
    if err_reason is not None:
        return _make_envelope(success=False, reason=err_reason,
                              elapsed_ms=_elapsed(t0))

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_CONTENT_BYTES:
        return _make_envelope(
            success=False, reason=ReasonCode.INVALID_PARAM,
            elapsed_ms=_elapsed(t0),
            stderr=f"content bytes {len(content_bytes)} > limit {MAX_CONTENT_BYTES}",
        )

    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    target, path_err = _validate_repo_relative_file(path, Path(directory))
    if path_err is not None:
        return _make_envelope(success=False, reason=ReasonCode.PATH_UNSAFE,
                              elapsed_ms=_elapsed(t0), loaded=False,
                              directory=directory, umo=effective_umo, worktree=directory)

    target.parent.mkdir(parents=True, exist_ok=True)
    created = not target.exists()
    target.write_text(content, encoding="utf-8")

    return _make_envelope(
        success=True, elapsed_ms=_elapsed(t0),
        saved=True, created=created,
        directory=directory, umo=effective_umo, worktree=directory,
        path=path, size=len(content_bytes),
    )


# ── PATCH ────────────────────────────────────────────
async def handle_patch_docs(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    t0 = _time.time()
    if body is None:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY,
                              elapsed_ms=_elapsed(t0))

    old_path = body.get("path", "")
    new_path = body.get("new_path", "")

    for val in (old_path, new_path):
        err_reason = _validate_doc_path(val)
        if err_reason is not None:
            return _make_envelope(success=False, reason=err_reason,
                                  elapsed_ms=_elapsed(t0))

    if old_path == new_path:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_PARAM,
                              elapsed_ms=_elapsed(t0),
                              stderr="path and new_path are equal")

    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    old_target, err1 = _validate_repo_relative_file(old_path, Path(directory))
    new_target, err2 = _validate_repo_relative_file(new_path, Path(directory))
    if err1 is not None or err2 is not None:
        return _make_envelope(success=False, reason=ReasonCode.PATH_UNSAFE,
                              elapsed_ms=_elapsed(t0), loaded=False,
                              directory=directory, umo=effective_umo, worktree=directory)

    if not old_target.exists():
        return _make_envelope(success=False, reason=ReasonCode.FILE_NOT_FOUND,
                              elapsed_ms=_elapsed(t0), loaded=False,
                              directory=directory, umo=effective_umo, worktree=directory,
                              path=old_path)
    if new_target.exists():
        return _make_envelope(success=False, reason=ReasonCode.FILE_EXISTS,
                              elapsed_ms=_elapsed(t0), loaded=False,
                              directory=directory, umo=effective_umo, worktree=directory,
                              path=new_path)

    new_target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(old_target, new_target)

    return _make_envelope(
        success=True, elapsed_ms=_elapsed(t0),
        renamed=True,
        directory=directory, umo=effective_umo, worktree=directory,
        path=old_path, new_path=new_path,
    )


# ── DELETE ───────────────────────────────────────────
async def handle_delete_docs(
    plugin: "SPCodeToolkit",
    *,
    umo: str | None = None,
    worktree: str | None = None,
    body: dict | None = None,
) -> dict:
    t0 = _time.time()
    if body is None:
        return _make_envelope(success=False, reason=ReasonCode.INVALID_BODY,
                              elapsed_ms=_elapsed(t0))

    path = body.get("path", "")
    err_reason = _validate_doc_path(path)
    if err_reason is not None:
        return _make_envelope(success=False, reason=err_reason,
                              elapsed_ms=_elapsed(t0))

    err, ctx = await _git_endpoint_preflight(
        plugin, umo=umo, worktree_param=worktree,
    )
    if err is not None:
        err["data"]["elapsed_ms"] = _elapsed(t0)
        err["data"].setdefault("loaded", False)
        return err
    directory = ctx["directory"]
    effective_umo = ctx["umo"]

    target, path_err = _validate_repo_relative_file(path, Path(directory))
    if path_err is not None:
        return _make_envelope(success=False, reason=ReasonCode.PATH_UNSAFE,
                              elapsed_ms=_elapsed(t0), loaded=False,
                              directory=directory, umo=effective_umo, worktree=directory)

    if not target.exists():
        return _make_envelope(success=False, reason=ReasonCode.FILE_NOT_FOUND,
                              elapsed_ms=_elapsed(t0), loaded=False,
                              directory=directory, umo=effective_umo, worktree=directory,
                              path=path)

    if target.is_dir():
        return _make_envelope(success=False, reason=ReasonCode.GIT_ERROR,
                              elapsed_ms=_elapsed(t0), loaded=False,
                              directory=directory, umo=effective_umo, worktree=directory,
                              stderr=f"path {path} is a directory, not a file")

    target.unlink()
    return _make_envelope(
        success=True, elapsed_ms=_elapsed(t0),
        deleted=True,
        directory=directory, umo=effective_umo, worktree=directory,
        path=path,
    )
```

#### 9.3 `webapi/__init__.py` 增量

```python
# 头部 import
from . import (
    codegraph_status,
    docs_crud,                 # 新增
    file_browser,
    file_discard_hunk,
    file_name_search,
    file_restore,
    file_search,
    git_commit,
    git_diff,
    git_file,                  # 新增
    git_log,
    git_show,
    git_stage,
    git_status,
    git_unstage,
    git_worktree_add,
    git_worktree_lock,
    git_worktree_remove,
    git_worktree_unlock,
    git_worktrees,
    plan_mode,
    project_status,
)

# ROUTES 末尾追加:
ROUTES.append(("/spcode/git-file", ["GET"],  git_file.handle,
              "获取 ref 下某文件的完整内容(blob)"))
ROUTES.append(("/spcode/docs",     ["POST"],  docs_crud.handle_post_docs,
              "创建/覆盖 docs 文件(写到工作区)"))
ROUTES.append(("/spcode/docs",     ["PATCH"], docs_crud.handle_patch_docs,
              "重命名 docs 文件(纯 mv)"))
ROUTES.append(("/spcode/docs",     ["DELETE"], docs_crud.handle_delete_docs,
              "删除 docs 文件"))

# HANDLERS 末尾追加:
HANDLERS.update({
    "handle_get_git_file":          git_file.handle,
    "handle_post_docs":             docs_crud.handle_post_docs,
    "handle_patch_docs":            docs_crud.handle_patch_docs,
    "handle_delete_docs":           docs_crud.handle_delete_docs,
})
```

#### 9.4 `_helpers.py:ReasonCode` 增量

```python
# tools/webapi/_helpers.py:ReasonCode class 内追加
# ── /spcode/docs 专用(spec B 2026-07-11) ──
FILE_TOO_LARGE = "file_too_large"           # git-file: blob 超过 1 MB
FILE_MISSING_AT_REF = "file_missing_at_ref" # git-file: ref 解析成功但 path 不在 ref 下
FILE_EXISTS = "file_exists"                 # PATCH: new_path 已存在
```

### 10. 测试矩阵

#### 10.1 `tests/test_git_file.py`

环境:复用 `conftest.py` 的 `git_repo` fixture(spawns tmp git repo + commits);若 `git` 不可用 → `pytest.skip`。

```
[happy path]
✓ HEAD 默认 ref → 拿到文件全文
✓ ?ref=<short-sha> → 拿到该提交时的内容
✓ ?ref=<branch> → 拿到分支 tip 的内容
✓ ?ref=<tag> → 同上
✓ ?ref=HEAD~1 → 拿到父提交的内容
✓ ?ref=<commit-sha> → 拿到准确内容
✓ ?worktree=<wt-name> → 在指定 worktree 下读
✓ path 在 ref 下不存在 → file_missing_at_ref
✓ content 含 0x00 → is_binary=true, content=""
✓ content > 1 MB → truncated=true, content[:1MB]
✓ 空文件 → content="", is_binary=false, size=0
✓ 二级子目录 path → OK
✓ 文件名含中文 / emoji → utf-8 正确
✓ 末尾带 \n 的文件 → content 末尾 \n 已被 _run_git_async rstrip 掉(契约里写明)

[error path]
✓ path 缺失 → invalid_param
✓ path 含 NUL / 换行 → invalid_param
✓ path 含 .. → invalid_param(经 _validate_repo_relative_file 抛 path_unsafe)
✓ path 长度 > 512 → invalid_param
✓ ref 长度 > 512 → invalid_param
✓ ref 不存在 → ref_not_found
✓ umo 错 → no_project_loaded
✓ worktree 6 步防御失败 → worktree_invalid
✓ 关闭 feature flag → feature_disabled
✓ 根目录被删 → directory_missing
✓ 子目录不是 git repo → not_a_git_repo
✓ 模拟 git 在 PATH 中不可用(monkeypatch _git_binary 抛) → git_unavailable
✓ 跨 worktree 越权 → path_unsafe
```

#### 10.2 `tests/test_docs_crud.py`

```
[POST]
✓ 创建不存在的 .md → created=true, 文件出现在 worktree
✓ 覆盖存在的 .md → created=false, content 替换
✓ 父目录不存在 → 自动 mkdir(parents=True)
✓ path 不以 .md 结尾 → invalid_param
✓ content 字节 > 2 MB → invalid_param
✓ body 不是 dict → invalid_body
✓ path 含 NUL/换行 → invalid_param
✓ path 含 .. → path_unsafe
✓ 跨 worktree 越权 → path_unsafe
✓ umo 错 → no_project_loaded
✓ worktree 6 步防御失败 → worktree_invalid
✓ 关闭 feature flag → feature_disabled

[PATCH]
✓ 旧存在 + 新不存在 → renamed=true, 文件 mv
✓ 旧不存在 → file_not_found
✓ 新已存在 → file_exists
✓ path == new_path → invalid_param
✓ path 校验失败 → path_unsafe
✓ 父目录不存在 → 自动 mkdir
✓ 跨 worktree → path_unsafe
✓ umo 错 → no_project_loaded

[DELETE]
✓ 路径存在 → deleted=true, 文件消失
✓ 路径不存在 → file_not_found
✓ path 不以 .md 结尾 → invalid_param
✓ 路径是目录(非文件) → git_error(stderr 描述)
✓ 跨 worktree → path_unsafe
✓ umo 错 → no_project_loaded
```

#### 10.3 回归测试

- `pytest tests/ -k 'git_file or docs_crud'` 全 pass
- `pytest tests/` 整体 pass(无回归到现有 20 个端点)
- `ruff check tools/webapi/git_file.py tools/webapi/docs_crud.py` 干净
- `ruff check tools/webapi/_helpers.py tools/webapi/__init__.py` 干净

### 11. acceptance criteria

本 spec 完成当:

- [ ] `tools/webapi/git_file.py` 存在,提供 `handle(plugin, *, umo, worktree)`
- [ ] `tools/webapi/docs_crud.py` 存在,提供 `handle_post_docs / handle_patch_docs / handle_delete_docs`
- [ ] `tools/webapi/_helpers.py:ReasonCode` 新增 3 个码:`FILE_TOO_LARGE / FILE_MISSING_AT_REF / FILE_EXISTS`
- [ ] `tools/webapi/__init__.py:ROUTES` 新增 4 条;`HANDLERS` 同步
- [ ] `main.py` 无需改动(由 `register_webapi_routes` 接管)
- [ ] `tests/test_git_file.py` 全部 PASS,覆盖 §10.1 的 happy + error path
- [ ] `tests/test_docs_crud.py` 全部 PASS,覆盖 §10.2 的 POST/PATCH/DELETE
- [ ] `pytest tests/ -k 'git_file or docs_crud'` 通过
- [ ] `pytest tests/` 整体通过(无回归)
- [ ] `ruff check tools/webapi/git_file.py tools/webapi/docs_crud.py tools/webapi/_helpers.py tools/webapi/__init__.py` 干净
- [ ] 手工 smoke:在某个加载项目里,`curl GET /spcode/git-file?path=README.md` 返回 `loaded=true, content=<file>`;`curl POST /spcode/docs -d '{"path":"docs/test.md","content":"# hi"}'` 返回 `saved=true`

### 12. 范围之外(明确不做的)

- 不修改 `/spcode/git-log`(已支持 `?path=`)
- 不修改 `/spcode/git-show`(已支持 `?path=`)
- 不修改 `/spcode/file-browser`
- 不调 git add / git rm / git mv(working-tree only)
- 不实现并发编辑检测 / mtime 冲突(决策 B)
- 不实现原子写 / 备份(决策 B)
- 不实现 ETag 缓存(决策 Q4)
- 不引入新依赖(`_run_git_async` / `os.replace` / `Path.write_text` 全部 stdlib)
- 不修改 `main.py` 任何代码

### 13. 配套 spec A 文字调整

实施 spec A 时,**spec A 文档需同步更新以下 1 处**(影响 §3.6 端点描述):

```diff
- │ `DELETE` | `/spcode/docs` | Delete a docs file at the given relative path. Query/path encodes the path. |
+ │ `DELETE` | `/spcode/docs` | Delete a docs file. Body: `{ path: string }`. |
```

其它 3 个端点(PATCH、POST、GET)描述不变。spec A 的 i18n 键已覆盖全部新增 reason 码(spec A 验收阶段验证)。

### 14. 实施顺序建议(与 spec A 联动)

1. spec A 主流程(只读部分:tree / render / history / diff vs current)— 此时 spec B 不可用,前端用 `file-browser` 临时拉数据
2. spec B `git-file` 端点 → spec A 切换到 spec B 数据源
3. spec A 编辑器主流程 + spec B `docs` POST/PATCH/DELETE 端点(联调)
4. 错误处理 / i18n 收尾 / 回归

spec A 与 spec B 各自独立可合,顺序 1+2 优先(只读体验),3+4 后续(写体验)。
