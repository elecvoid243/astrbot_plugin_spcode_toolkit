# /spcode/file-restore — 恢复工作区文件改动端点

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后,下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已通过(用户确认日期 2026-06-22,Q1=A, Q2=A, Q3=A, Q4=A, Q5=A, Q6=A, Q7=同意)
**作者**: elecvoid243 @ 2026-06-22 20:28
**目标插件**: `astrbot_plugin_spcode_toolkit`(主干)
**前置版本**: 当前 HEAD `f0553e1`(v3.x `/spcode/git-diff` `/spcode/file-browser` 均已发布;本 spec 与既有端点正交)
**配套 UI**: 前端 dashboard "GitDiff 侧栏 ↩ 恢复按钮" 功能(由前端 agent 另行实现,本文档为其 API 契约)

## 用户环境约束

本端点在**服务端**(AstrBot 进程所在机器)通过 `subprocess` 调用 `git` 命令行客户端的 `git checkout -- <file>` 写操作,要求:

| 依赖 | 要求 | 缺失时的行为 |
|------|------|--------------|
| `git` 命令行工具 | 安装在系统中(任意平台) | 端点返回 `reason: "git_unavailable"`;不影响插件加载 |
| `git` 在 PATH 中(默认) | `git --version` 可执行 | 同上 |
| 已知 `git` 绝对路径(非标准安装) | 配置 `git_path` 字段 | 端点优先使用该路径;未设置时回退 PATH |

> 与 `/spcode/git-diff` 共用 git 可用性探测,启动期 WARNING 一次即可。

## 目标

为本地 dashboard 提供一个 HTTP 端点,**撤销工作区中某一个文件相对于 index 的改动**(`git checkout -- <file>` 语义)。前端从 `/spcode/git-diff` 响应中拿到 `files_changed[].path`,点 ↩ 按钮后调用本端点恢复该文件。

仅当 `/project load` 功能所需的前置条件(`agentsmd_enabled` + `codegraph_enabled` 配置为 true,且 `_loaded_projects` 中有该项目)满足时,端点才执行写操作;否则按约定返回 `restored: false` + `reason` 字段,HTTP 状态保持 200。

行为对齐现有 `/spcode/git-diff` 端点(可选 `umo` 查询参数、最近载入回退、`?worktree=` 6 步防御链、`_git_binary()` 解析),同时把 read-only 的 `git diff` 扩展为 write 的 `git checkout --`,并额外对**用户提供的 file 路径**做 4 步防御(绝对路径拒绝、`..` 段拒绝、symlink 逃逸拒绝、跨仓库拒绝)。

## 用户决策记录

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: HTTP 方法** | **A** — `POST /spcode/file-restore` | RESTful 规范;首个 POST 端点;body 传 JSON |
| **Q2: 恢复目标(scope)** | **A** — 仅 `unstaged`,保留 `?scope=` 扩展位 | v1 与 git-diff 默认对齐;95% 用户场景;未来加 staged/head 不破坏 API |
| **Q3: 文件路径表示** | **A** — 仓库相对路径(`"main.py"`) | 与 `files_changed[].path` 零摩擦;前端无需拼绝对路径 |
| **Q4: 范围** | **A** — 单文件(`file: str`) | 贴合 dashboard 单点交互;未来扩展批量是 v2 |
| **Q5: 二次确认** | **A** — 不需要,只做 INFO 审计日志 | 前端弹窗已确认一次;二次确认拖慢交互 |
| **Q6: Feature flag** | **A** — 与 git-diff 同前置条件(`agentsmd_enabled` AND `codegraph_enabled`) | 与 dashboard 所有写/读操作开关统一 |
| **Q7: reason code 列表** | 同意推荐方案 | 失败原因 taxonomy 见 §4 |

## 核心设计

### 1. URL 契约

| 维度 | 值 |
|------|---|
| Method | **`POST`** |
| Path | `/spcode/file-restore` |
| Content-Type | `application/json` |
| Body | `{"umo": "<umo>", "worktree": "<abs path>", "file": "<repo-relative path>"}` |
| Response Content-Type | `application/json` |
| 路由注册位置 | `main.py` 中 `initialize()` 内,`register_web_api("/spcode/file-restore", ..., methods=["POST"])` |
| 鉴权 | 沿用 AstrBot 框架统一鉴权(本插件不实现独立鉴权) |

**为什么 POST**:本端点是写操作(`git checkout -- <file>` 会覆盖用户文件);RESTful 规范下写操作必须用非安全方法。GET 带副作用会被浏览器/爬虫/CDN 缓存误触发。

**为什么 body 而不是 query**:POST 端点 body 是 JSON;query 长度有限且需 URL-encode;前端 fetch 友好。

**为什么完全沿用 git-diff 的 umo / worktree 协议**:dashboard 共享同一份项目状态机;前端不需维护两份状态。

### 2. 响应信封(统一)

沿用既有约定:

```json
{
  "status": "ok",
  "data": { /* 端点特定 */ }
}
```

- `status` 恒为 `"ok"`
- `data.restored`: `true` / `false`
- `data.reason`: 成功时 `null`;失败时为 reason code
- HTTP 200 永远返回,业务错误用 `data.reason` 表达

### 3. Body 字段

| 字段 | 必填 | 类型 | 含义 |
|------|------|------|------|
| `umo` | 否 | string | 统一消息源标识;与 git-diff 一致;缺省走最近载入 |
| `worktree` | 否 | string | 目标 worktree 绝对路径;与 git-diff 一致;缺省走主 worktree |
| `file` | **是** | string | 仓库相对路径(单文件);**绝对路径**、以 `/` 或 `\` 开头、含 `..` 段、解析后不在 worktree 内、解析后是 symlink 逃逸 → 拒绝 |

**Body 缺失 / JSON 解析失败 / Content-Type 不是 JSON** → `reason: "invalid_body"`。

### 4. `reason` 字段取值(互斥)

| 值 | 触发 | HTTP | data.restored |
|----|------|------|---------------|
| `null` | 成功 | 200 | `true` |
| `"invalid_body"` | body 不是合法 JSON / 不是 dict / 缺 `file` 字段 | 200 | `false` |
| `"feature_disabled"` | `agentsmd_enabled` / `codegraph_enabled` 任一为 false | 200 | `false` |
| `"no_project_loaded"` | umo 不命中且无任何项目 | 200 | `false` |
| `"directory_missing"` | loaded 目录不存在 | 200 | `false` |
| `"not_a_git_repo"` | `git rev-parse --is-inside-work-tree` 失败 | 200 | `false` |
| `"worktree_invalid"` | `?worktree=` 6 步防御失败 | 200 | `false` |
| `"git_unavailable"` | git 二进制缺失 | 200 | `false` |
| `"missing_file"` | `file` 字段为空字符串 / 仅空白 | 200 | `false` |
| `"path_unsafe"` | `file` 是绝对路径 / 以 `/` `\` 开头 / 含 `..` 段 / resolve 后逃出 worktree / 指向 `.git/` 内部 / symlink 逃逸 | 200 | `false` |
| `"file_not_found"` | resolve 后路径不存在 | 200 | `false` |
| `"not_modified"` | `git status --porcelain` 显示该路径无 unstaged 改动 | 200 | `false` |
| `"untracked_file"` | 该路径是 untracked,git checkout 拒绝 | 200 | `false` |
| `"git_error"` | git 命令非零退出(兜底) | 200 | `false` |

**成功响应**:
```json
{
  "status": "ok",
  "data": {
    "restored": true,
    "directory": "F:\\github\\astrbot_plugin_spcode_toolkit",
    "umo": "qq_official:xxx",
    "worktree": "F:\\github\\astrbot_plugin_spcode_toolkit",
    "file": "main.py",
    "scope": "unstaged",
    "elapsed_ms": 23,
    "stderr": "",
    "reason": null
  }
}
```

**失败响应(untracked_file 示例)**:
```json
{
  "status": "ok",
  "data": {
    "restored": false,
    "directory": "F:\\github\\astrbot_plugin_spcode_toolkit",
    "umo": "qq_official:xxx",
    "worktree": "F:\\github\\astrbot_plugin_spcode_toolkit",
    "file": "new_file.py",
    "scope": "unstaged",
    "elapsed_ms": 18,
    "stderr": "error: pathspec 'new_file.py' did not match any file(s) known to git",
    "reason": "untracked_file"
  }
}
```

> 注:成功响应也含 `reason` 字段(值为 `null`),与失败响应**形状对称**,便于前端统一解析。

### 5. file 路径安全校验(4 步防御)

1. **基本格式**:
   - 非空字符串
   - 不以 `/` 或 `\` 开头(Windows 盘符 + 绝对路径)
   - 不含 `..` 路径段(切 `/` 后逐段查)
   - 不含反斜杠(强制 POSIX 风格,跨平台一致)
2. **相对路径 resolve**:
   - `target = (Path(worktree) / file).resolve()`
   - `target.relative_to(Path(worktree).resolve())` 必须成功(即 target 仍在 worktree 内)
3. **特殊目录拒绝**:
   - target 路径中任一段等于 `.git` → 拒绝
4. **symlink 防御**:
   - `os.path.realpath(target) == str(target)` 否则拒绝(防止 symlink 指向 worktree 外)

任一步失败 → `path_unsafe`。

### 6. 行为流程(伪代码)

```python
async def handle_post_file_restore(self) -> dict:
    t0 = _time.time()
    from astrbot.api import web

    # 1. 读取 body(POST 协议)
    try:
        body = web.request.get_json(silent=True) or {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return _make_file_restore_empty_envelope(reason="invalid_body", elapsed_ms=_elapsed())

    file_path = body.get("file", "")
    if not isinstance(file_path, str) or not file_path.strip():
        return _make_file_restore_empty_envelope(reason="missing_file", elapsed_ms=_elapsed())

    umo = body.get("umo") or None
    worktree_param = body.get("worktree")  # 与 git-diff 保持:不 or-None,保留空串触发 6 步校验

    git_bin = self._git_binary()

    # 2. Feature flag 校验
    if not (self._config.get("agentsmd_enabled", True)
            and self._config.get("codegraph_enabled", True)):
        return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                  reason="feature_disabled",
                                                  elapsed_ms=_elapsed())

    # 3. umo 解析与回退
    if umo:
        info = self._loaded_projects.get(umo)
    else:
        if not self._loaded_projects:
            info = None
        else:
            _, info = max(self._loaded_projects.items(),
                          key=lambda kv: kv[1].get("loaded_at", 0))
    if info is None:
        return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                  reason="no_project_loaded",
                                                  elapsed_ms=_elapsed())
    directory = info.get("directory", "")

    # 4. worktree 校验(6 步防御,与 git-diff 完全相同)
    if worktree_param is not None and worktree_param.strip():
        validated_wt, wt_err = _validate_worktree_param(git_bin, directory, worktree_param)
        if wt_err is not None:
            logger.warning(f"[file-restore] rejected ?worktree={worktree_param!r} "
                           f"(loaded={directory!r})")
            return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                      reason=wt_err,
                                                      directory=directory,
                                                      elapsed_ms=_elapsed())
        directory = validated_wt

    # 5. 目录存在性
    if not Path(directory).is_dir():
        return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                  reason="directory_missing",
                                                  directory=directory,
                                                  elapsed_ms=_elapsed())

    # 6. git repo probe
    probe = await _run_git_async(
        [git_bin, "-C", directory, "rev-parse", "--is-inside-work-tree"],
        encoding="utf-8",
    )
    if not probe["ok"]:
        combined = (probe.get("stderr", "") + probe.get("error", "")).lower()
        if "not a git repository" in combined:
            return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                      reason="not_a_git_repo",
                                                      directory=directory,
                                                      elapsed_ms=_elapsed())
        if "未安装" in probe.get("error", ""):
            return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                      reason="git_unavailable",
                                                      directory=directory,
                                                      elapsed_ms=_elapsed())
        return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                  reason="git_error",
                                                  directory=directory,
                                                  stderr=probe.get("stderr", "")
                                                          or probe.get("error", ""),
                                                  elapsed_ms=_elapsed())

    # 7. file 路径安全校验(4 步防御,本端点独有)
    target, err = _validate_restore_file(file_path, Path(directory))
    if err is not None:
        logger.warning(f"[file-restore] rejected file={file_path!r} "
                       f"(worktree={directory!r}): {err}")
        return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                  reason="path_unsafe",
                                                  directory=directory,
                                                  elapsed_ms=_elapsed())
    target_str = str(target)

    # 8. file 存在性
    if not target.exists():
        return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                  reason="file_not_found",
                                                  directory=directory,
                                                  elapsed_ms=_elapsed())

    # 9. git status --porcelain 预检:判断是 untracked 还是 not_modified
    status = await _run_git_async(
        [git_bin, "-C", directory, "status", "--porcelain", "--", file_path],
        encoding="utf-8",
    )
    if status["ok"]:
        porcelain = status["stdout"]
        if not porcelain.strip():
            # working tree 与 index 一致 → 无可恢复
            return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                      reason="not_modified",
                                                      directory=directory,
                                                      elapsed_ms=_elapsed())
        # 解析第一行第二列:?? = untracked, M/A/D/R = modified 等
        first = porcelain.splitlines()[0] if porcelain else ""
        if first.startswith("??") or first.startswith("!!"):
            return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                      reason="untracked_file",
                                                      directory=directory,
                                                      stderr=porcelain,
                                                      elapsed_ms=_elapsed())

    # 10. 执行 git checkout -- <file>
    result = await _run_git_async(
        [git_bin, "-C", directory, "-c", "color.ui=never", "checkout", "--", file_path],
        encoding="utf-8",
    )

    if not result["ok"]:
        stderr = result.get("stderr", "")
        lower = stderr.lower()
        if "did not match any file" in lower or "unknown revision" in lower:
            reason = "untracked_file"
        elif "needs merge" in lower or "conflict" in lower:
            reason = "git_error"  # 冲突;git 拒绝
        else:
            reason = "git_error"
        return _make_file_restore_empty_envelope(umo=umo, file=file_path,
                                                  reason=reason,
                                                  directory=directory,
                                                  stderr=stderr,
                                                  elapsed_ms=_elapsed())

    # 11. 成功:审计日志
    logger.info(f"[file-restore] restored: file={file_path!r} "
                f"worktree={directory!r} umo={umo!r} elapsed_ms={_elapsed()}")
    return _make_file_restore_success_envelope(
        umo=umo, file=file_path, directory=directory,
        elapsed_ms=_elapsed(),
    )
```

### 7. `_validate_restore_file` 实现

```python
def _validate_restore_file(
    file_path: str, worktree: Path,
) -> tuple[Path | None, str | None]:
    """4-step defense for the ``file`` field of /spcode/file-restore.

    Returns ``(resolved_path, error_reason)``:
      - On success: ``(absolute_path_inside_worktree, None)``
      - On rejection: ``(None, "path_unsafe")``
    """
    if not file_path:
        return None, "path_unsafe"

    # Step 1: 拒绝绝对路径与路径穿越
    if file_path.startswith("/") or file_path.startswith("\\"):
        return None, "path_unsafe"
    if "\\" in file_path:  # 强制 POSIX 风格
        return None, "path_unsafe"
    if ".." in file_path.replace("\\", "/").split("/"):
        return None, "path_unsafe"

    # Step 2: resolve 到 worktree 内部
    worktree_resolved = worktree.resolve()
    target = (worktree_resolved / file_path).resolve()
    try:
        target.relative_to(worktree_resolved)
    except ValueError:
        return None, "path_unsafe"

    # Step 3: 拒绝 .git 内部
    if any(part == ".git" for part in target.parts):
        return None, "path_unsafe"

    # Step 4: symlink 防御
    real = os.path.realpath(target)
    if os.path.normcase(real) != os.path.normcase(str(target)):
        return None, "path_unsafe"

    return target, None
```

### 8. `_make_file_restore_empty_envelope` 实现

```python
def _make_file_restore_empty_envelope(
    *,
    umo: str | None = None,
    file: str = "",
    directory: str | None = None,
    worktree: str | None = None,
    scope: str = "unstaged",
    reason: str,
    stderr: str = "",
    elapsed_ms: int = 0,
) -> dict:
    """构造失败路径的响应骨架(对称于 _make_git_diff_empty_envelope)。"""
    return {
        "status": "ok",
        "data": {
            "restored": False,
            "directory": directory,
            "umo": umo,
            "worktree": worktree or directory,
            "file": file,
            "scope": scope,
            "elapsed_ms": elapsed_ms,
            "stderr": stderr,
            "reason": reason,
        },
    }


def _make_file_restore_success_envelope(
    *,
    umo: str | None,
    file: str,
    directory: str,
    elapsed_ms: int,
) -> dict:
    return {
        "status": "ok",
        "data": {
            "restored": True,
            "directory": directory,
            "umo": umo,
            "worktree": directory,
            "file": file,
            "scope": "unstaged",
            "elapsed_ms": elapsed_ms,
            "stderr": "",
            "reason": None,
        },
    }
```

> 为保持与 `_make_git_diff_empty_envelope` 风格一致,放在 `tools/_config.py`(或 `main.py` 模块级)。**推荐放在 `tools/_config.py`**,与既有 `make_git_diff_empty_envelope` / `make_git_worktrees_empty_envelope` 同居一处。

### 9. 路由注册(`initialize()` 内)

```python
# v3.5: 注册 /spcode/file-restore — 供 dashboard "↩ 恢复" 按钮调用。
# 详见 docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md
try:
    self.context.register_web_api(
        route="/spcode/file-restore",
        view_handler=self.handle_post_file_restore,
        methods=["POST"],
        desc="恢复工作区中某一文件相对于 index 的改动(供 dashboard 调用)",
    )
except Exception as exc:  # pragma: no cover - defensive
    logger.warning(f"注册 spcode file-restore web API 失败: {exc!s}")
```

### 10. 不修改既有契约

- `_conf_schema.json`:不需要新增字段(`git_path` 已在 v3.1 git-diff 中加入)
- `_loaded_projects`:只读,不修改
- `metadata.yaml` / `README.md`:v1 不变更(若需文档后续追加)

## 错误处理矩阵

| 触发条件 | data.restored | data.reason | stderr | HTTP |
|----------|---------------|-------------|--------|------|
| body 非 JSON / 非 dict | false | invalid_body | "" | 200 |
| body 缺 `file` 字段 | false | missing_file | "" | 200 |
| feature flag 关闭 | false | feature_disabled | "" | 200 |
| 未传 umo 且无任何项目 | false | no_project_loaded | "" | 200 |
| umo 不在 _loaded_projects | false | no_project_loaded | "" | 200 |
| loaded 目录已不存在 | false | directory_missing | "" | 200 |
| 目录不是 git 仓库 | false | not_a_git_repo | "" | 200 |
| worktree 6 步防御失败 | false | worktree_invalid | "" | 200 |
| git 未安装 | false | git_unavailable | "" | 200 |
| file 是绝对路径 / `..` / `\..` | false | path_unsafe | "" | 200 |
| file 解析后逃出 worktree | false | path_unsafe | "" | 200 |
| file 指向 .git 内部 | false | path_unsafe | "" | 200 |
| file 是 symlink 逃逸 | false | path_unsafe | "" | 200 |
| file 解析后路径不存在 | false | file_not_found | "" | 200 |
| working tree 与 index 一致(无可恢复) | false | not_modified | "" | 200 |
| file 是 untracked | false | untracked_file | git status 输出 | 200 |
| git checkout 冲突 / 其他错误 | false | git_error | git stderr | 200 |
| 成功 | true | null | "" | 200 |

> 永远返回 HTTP 200;客户端按 `data.restored` + `data.reason` 决策。这与 git-diff 行为完全一致,避免前端引入 HTTP 状态码分支。

## 测试用例(15+)

新增 `tests/test_file_restore.py`(沿用 `tests/test_git_diff.py` 的 fixture 模式,真 git + tmp_path,不 mock subprocess)。`plugin` fixture 来自 `tests/conftest.py::_make_plugin`,并新增 `_make_web_post_request_mock` helper 模拟 POST body。

| # | 用例 | 场景 | 关键断言 |
|---|------|------|---------|
| 1 | `test_restore_modifies_file_back_to_index` | 修改 README.md 后 restore | 文件内容回到 HEAD;`restored=True`;`reason=None` |
| 2 | `test_restore_intent_to_add_file` | `git add -N new.py` 后写内容,restore | 新增意图被取消;`restored=True` |
| 3 | `test_restore_no_modification_returns_not_modified` | 文件无改动,restore | `restored=False`;`reason="not_modified"` |
| 4 | `test_restore_untracked_file_returns_untracked` | 未 `git add -N` 的新文件 | `restored=False`;`reason="untracked_file"`;`stderr` 非空 |
| 5 | `test_restore_rejects_absolute_path` | `file: "/etc/passwd"` | `restored=False`;`reason="path_unsafe"` |
| 6 | `test_restore_rejects_windows_absolute_path` | `file: "C:\\Windows\\system.ini"` | `restored=False`;`reason="path_unsafe"` |
| 7 | `test_restore_rejects_parent_traversal` | `file: "../foo.py"` | `restored=False`;`reason="path_unsafe"` |
| 8 | `test_restore_rejects_dot_git_path` | `file: ".git/config"` | `restored=False`;`reason="path_unsafe"` |
| 9 | `test_restore_rejects_symlink_escape` | 创建指向 worktree 外的 symlink | `restored=False`;`reason="path_unsafe"` |
| 10 | `test_restore_rejects_backslash_path` | `file: "src\\foo.py"` | `restored=False`;`reason="path_unsafe"` |
| 11 | `test_restore_rejects_file_outside_worktree` | resolve 后逃出 worktree 的相对路径 | `restored=False`;`reason="path_unsafe"` |
| 12 | `test_restore_invalid_body_returns_invalid_body` | body 不是 JSON | `restored=False`;`reason="invalid_body"` |
| 13 | `test_restore_missing_file_field_returns_missing_file` | body `{"umo": "x"}` | `restored=False`;`reason="missing_file"` |
| 14 | `test_restore_empty_file_returns_missing_file` | body `{"file": "  "}` | `restored=False`;`reason="missing_file"` |
| 15 | `test_restore_no_project_loaded_returns_no_project_loaded` | `_loaded_projects` 空 | `restored=False`;`reason="no_project_loaded"` |
| 16 | `test_restore_feature_disabled_agentsmd` | `agentsmd_enabled=False` | `restored=False`;`reason="feature_disabled"` |
| 17 | `test_restore_feature_disabled_codegraph` | `codegraph_enabled=False` | `restored=False`;`reason="feature_disabled"` |
| 18 | `test_restore_directory_missing` | loaded 目录已被删除 | `restored=False`;`reason="directory_missing"` |
| 19 | `test_restore_not_a_git_repo` | 加载的是非 git 目录 | `restored=False`;`reason="not_a_git_repo"` |
| 20 | `test_restore_worktree_param_6step_defense` | 沿用 `test_git_diff_worktree.py` 攻击向量 | 6 个用例全过(`?worktree=` 6 步防御) |
| 21 | `test_restore_with_worktree_param_succeeds` | 合法 worktree + file,restore 在 worktree 内 | `restored=True`;`worktree` 字段回显 |
| 22 | `test_restore_uses_configured_git_path` | `git_path="C:/some/other/git.exe"` | 验证 `_git_binary()` 返回配置值;handler 调 git 时首参数为该路径 |
| 23 | `test_restore_logs_audit_trail` | 成功路径 | `caplog` 验证 INFO 级别审计日志含 file + worktree + umo |
| 24 | `test_restore_response_envelope_shape` | 成功路径 | 所有字段存在;类型与 spec 一致;`data.restored` 是 bool |
| 25 | `test_restore_max_bytes_field_absent` | 成功路径 | `data` 中**不包含** `max_bytes` 字段(本端点无截断语义) |
| 26 | `test_restore_idempotent_on_clean_file` | 文件无改动,连续 restore 两次 | 两次都返回 `not_modified`;无副作用 |

### Test fixture helper(本地)

```python
def _make_post_body_mock(body: dict) -> MagicMock:
    """Mock astrbot.api.web.request with get_json() returning body."""
    from astrbot.api import web
    mock = MagicMock()
    mock.get_json = MagicMock(return_value=body)
    monkeypatch.setattr(web, "request", mock)
```

## 改动文件清单

| 文件 | 类型 | 行数预估 | 职责 |
|------|------|---------|------|
| `main.py` | 修改 | +180 行 | 新增 `handle_post_file_restore` 方法、`_validate_restore_file` 内部 helper;`initialize()` 内新增 `register_web_api` 块 |
| `tools/_config.py` | 修改 | +60 行 | 新增 `make_file_restore_empty_envelope()` 与 `make_file_restore_success_envelope()` 失败/成功响应骨架 |
| `tests/test_file_restore.py` | 新增 | +500 行 | 26 个测试用例 + 4 个 helper(`_init_git_repo`、`_load_project`、`_make_post_body_mock`、`_patch_web_request_post_body`) |
| `docs/superpowers/specs/2026-06-22-file-restore-endpoint-design.md` | 新增 | — | 本文档 |
| `docs/superpowers/plans/2026-06-22-file-restore-endpoint.md` | 新增 | — | implementation plan(writing-plans 产出) |

> 不修改 `_conf_schema.json`(无新配置项);不修改 `metadata.yaml` / `README.md`(v1 端点不暴露用户配置)

## 不在本次范围

- **staged / HEAD / branch 恢复**:v1 仅 `unstaged`。Q2=A 决定保留 `scope` 字段为扩展位
- **批量恢复**(`files: list[str]`):Q4=A 决定 v1 单文件
- **二次确认协议**:Q5=A 决定不需要
- **独立 `restore_enabled` 开关**:Q6=A 决定与 git-diff 共享前置条件
- **前端 Dashboard UI 实现**:本文档仅定义 API 契约
- **L1 鉴权**:沿用 AstrBot 框架统一鉴权
- **持久化审计日志**:INFO 日志可由用户从 logback 收集

## 迁移 / 兼容性

- **无破坏性变更**:新增端点与 `git-diff` / `file-browser` 平行;既有端点行为不变
- **现有用户**:若不调用新端点,行为完全不变
- **代码风格**:复用现有 `handle_get_git_diff` / `_validate_worktree_param` / `_run_git_async` 模式
- **测试模式**:复用 conftest 中 `_make_plugin` / `make_web_request_mock` fixtures
- **错误响应**:与既有端点 envelope 完全一致(`{"status": "ok", "data": {...}}`)

## 实施约束

- Python ≥ 3.10(使用 `str | None`、`list[str]`、`dict[str, str]`)
- 仅使用标准库 + 现有依赖
- 不引入新依赖
- 沿用 ruff 配置(line-length 100)
- `ruff check .` 必须 0 error
- `pytest tests/` 必须全 PASS(允许既有 codegraph skip)
- 不修改 `data/workspaces/`、`data/temp/` 下的实际数据
- 不修改既有 `_conf_schema.json` 字段名
- 路径安全:任何用户输入路径都先经 `_validate_restore_file`(本端点)或 `_validate_worktree_param`(`?worktree=`) 校验
