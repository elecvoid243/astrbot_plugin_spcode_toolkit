# code_check / code_format Web API 设计

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后，下一步调用 writing-plans 技能输出实现计划。

**状态**: ✅ 设计已分段通过（用户确认日期 2026-08-12，3 段全部确认：§2 端点与请求契约 → §3 执行流程与错误模型 → §4 文件拆分与测试策略）
**作者**: elecvoid243 @ 2026-08-12 16:45 CST
**目标插件 / 仓库**: `astrbot_plugin_spcode_toolkit`
**前置版本**: spcode plugin v2.23.2
**目标版本**: 下一个 minor 版本（本任务不修改 `metadata.yaml`）
**前端消费者**: AstrBot Dashboard / WebUI

---

## 用户决策记录（4 轮澄清）

| Q | 决策 | 影响 |
|---|------|------|
| **Q1: 文件路径语义** | **A** — 仅接受 repo-relative path | 复用现有 `_validate_repo_relative_file` 4 步防御，拒绝任意绝对路径 |
| **Q2: format 写回语义** | **A** — 默认写回，`check=true` dry-run | 与 LLM `code_format` 工具语义一致 |
| **Q3: linter 参数范围** | **B** — `auto` + 可显式指定 linter | Web API 支持 `auto/ruff/cpplint/cppcheck` |
| **Q4: 端点架构** | **A** — 2 个原子 POST 端点，一文件一端点 | 与项目现有 Web API 风格一致 |

---

## §1 背景与动机

### 1.1 当前状态

`code_check` 与 `code_format` 当前均以 AstrBot LLM FunctionTool 暴露：

- `tools/function_tools/code_check.py::CodeCheckTool`
  - 薄封装，调用 `tools/code_check.py::check(filepath, linter)`
  - `.py → ruff`
  - C/C++ → cppcheck 优先，按配置短路，cpplint 兜底
- `tools/function_tools/code_format.py::CodeFormatTool`
  - 薄封装，调用 `tools/code_format.py::format(filepath, "auto", check=..., style=..., indent=...)`
  - `.py → ruff format`
  - C/C++/Java/JS/TS/C# → AStyle
  - `default_style` / `default_indent` 从插件配置注入

Dashboard 已能通过文件浏览器查看源码，但用户无法手动点击按钮触发同一套检查或格式化能力，只能依赖 LLM 调用工具。

### 1.2 目标

新增 2 个 Web API：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/spcode/code-check` | POST | 手动触发单文件 syntax + style 检查 |
| `/spcode/code-format` | POST | 手动触发单文件格式化，默认写回，可选 dry-run |

### 1.3 范围与非目标

**范围内**：

- 2 个原子 POST endpoint
- repo-relative path + 现有 preflight + 4 步路径防御
- 复用 `tools/code_check.check` 与 `tools/code_format.format`
- 插件配置一致性：`cppcheck_enable`、`cppcheck_shortcircuit`、`default_style`、`default_indent`
- 统一 envelope、ReasonCode、路由注册、测试与 Dashboard API 文档

**范围外**：

- 批量文件检查或格式化
- 任意绝对路径
- 通用 LLM tool-call 端点
- 新 linter / formatter 接入
- 前端 Dashboard UI 实现
- 修改 `README.md` / `AGENTS.md` / `metadata.yaml`

---

## §2 端点与请求契约

### 2.1 `POST /spcode/code-check`

请求体：

```json
{
  "path": "src/main.py",
  "linter": "auto",
  "umo": "webchat:...",
  "worktree": "F:/optional-worktree"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `path` | string | 是 | — | repo-relative 文件路径 |
| `linter` | string | 否 | `"auto"` | `auto` / `ruff` / `cpplint` / `cppcheck` |
| `umo` | string | 否 | 最近加载项目 | 标准参数 |
| `worktree` | string | 否 | primary worktree | 走现有 6 步防御链 |

行为：

- `auto` 模式保持现有业务层语义：
  - `.py → ruff`
  - `.c/.cpp/.cc/.cxx/.h/.hpp/.hxx/.hh → cppcheck 优先 + cpplint 兜底`
- 显式 `linter` 直接选择对应 runner。
- 返回业务层结构化 issues；前 5 个 issue 保留上下文。

### 2.2 `POST /spcode/code-format`

请求体：

```json
{
  "path": "src/main.py",
  "check": false,
  "umo": "webchat:...",
  "worktree": "F:/optional-worktree"
}
```

| 字段 | 类型 | 必传 | 默认 | 说明 |
|------|------|------|------|------|
| `path` | string | 是 | — | repo-relative 文件路径 |
| `check` | boolean | 否 | `false` | `false` 写回；`true` dry-run |
| `umo` | string | 否 | 最近加载项目 | 标准参数 |
| `worktree` | string | 否 | primary worktree | 走现有 6 步防御链 |

行为：

- `.py → ruff format`
- `.c/.cpp/.h/.hpp/.cc/.cxx/.hxx/.hh/.java/.js/.jsx/.mjs/.cjs/.cs → AStyle`
- `default_style` / `default_indent` 从 `plugin._config` 读取。
- 普通请求不暴露 `formatter` / `style` / `indent`。

### 2.3 配置一致性

`code_format.format()` 已接受 `style` / `indent` 参数，Web API 直接传入插件配置。

`code_check.check()` 当前从模块级覆盖、环境变量和默认配置中读取 cppcheck 配置。为避免 Web API 修改全局环境并保证并发安全，计划为其增加 keyword-only 可选参数：

```python
def check(
    filepath: str,
    linter: str = "auto",
    *,
    cppcheck_enable: list[str] | None = None,
    cppcheck_shortcircuit: str | None = None,
) -> dict:
    ...
```

默认值 `None` 表示沿用现有三源读取链，因此 LLM 旧调用行为完全不变；Web API 显式传入 `plugin._config`。

---

## §3 执行流程、响应与错误模型

### 3.1 共同执行流程

```text
POST /spcode/code-check or /spcode/code-format
  ├─ body 类型 / 字段类型校验
  ├─ path 基本校验（非空、≤512、禁止换行/NUL）
  ├─ _git_endpoint_preflight（5 步）
  ├─ _validate_repo_relative_file（4 步）
  ├─ asyncio.Lock 串行化
  ├─ run_sync 在线程池调用同步业务层
  └─ 结果转换为统一 envelope
```

设计要点：

1. **路径安全**：只接受 repo-relative path；拒绝绝对路径、`..`、`.git/`、symlink 逃逸。
2. **事件循环安全**：业务层是同步 subprocess 调用，必须通过 `run_sync()` 在线程池执行。
3. **并发安全**：模块级 `asyncio.Lock` 串行化 check/format，避免并发格式化同一文件或并发 linter 子进程互相干扰。
4. **plan-mode**：Dashboard 手动按钮是用户显式动作，不套用 LLM 工具过滤规则。

### 3.2 `code-check` 成功响应

```json
{
  "status": "ok",
  "data": {
    "success": true,
    "checked": true,
    "path": "src/main.py",
    "linter": "ruff",
    "issues": [],
    "count": 0,
    "directory": "F:/repo",
    "umo": "webchat:...",
    "worktree": "F:/repo",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 35
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `checked` | boolean | 是否完成检查 |
| `linter` | string | 实际使用的 linter |
| `issues` | array | 业务层结构化 issue 列表 |
| `count` | integer | issue 数量 |

### 3.3 `code-format` 成功响应

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
    "diff_summary": "...",
    "proposal": "ruff 重新格式化了 main.py(-5 字节, 123 → 118)",
    "directory": "F:/repo",
    "umo": "webchat:...",
    "worktree": "F:/repo",
    "reason": null,
    "stderr": "",
    "elapsed_ms": 42
  }
}
```

语义：

- `check=false`：`formatted=true`；`changed=true` 表示实际写回。
- `check=true`：`formatted=false`；永不写回；`changed=true` 时附带 `diff_summary`。

### 3.4 错误映射

| 场景 | ReasonCode |
|------|------------|
| body 非 object / 字段类型错误 | `invalid_body` |
| `linter` 非枚举 / path 非法 / 参数非法 | `invalid_param` |
| path 越权 | `path_unsafe` |
| 文件不存在或不是普通文件 | `file_not_found` |
| 扩展名不支持 | `unsupported_media_type` |
| 文件超过 formatter 10 MB 上限 | `file_too_large` |
| ruff/cpplint/cppcheck/astyle 不可用 | `tool_unavailable` |
| code_check 执行失败 | `check_failed` |
| code_format 执行或写回失败 | `format_failed` |

新增 ReasonCode：

| 常量 | 字面量 | 用途 |
|------|--------|------|
| `TOOL_UNAVAILABLE` | `tool_unavailable` | linter / formatter 未安装或不可用 |
| `CHECK_FAILED` | `check_failed` | code_check 执行失败 |
| `FORMAT_FAILED` | `format_failed` | code_format 执行或写回失败 |

业务层返回的 `error`、`supported_extensions`、`supported_styles`、`proposal` 等诊断信息放入 `data.details`，避免丢失排错上下文。

---

## §4 文件拆分、测试策略与交付范围

### 4.1 后端文件

新增：

| 文件 | 职责 |
|------|------|
| `tools/webapi/_code_tools.py` | body/path 校验、target 解析、共享 lock、错误映射 |
| `tools/webapi/code_check.py` | `POST /spcode/code-check` handler |
| `tools/webapi/code_format.py` | `POST /spcode/code-format` handler |

修改：

| 文件 | 职责 |
|------|------|
| `tools/code_check.py` | 增加 keyword-only cppcheck 配置 override，默认行为不变 |
| `tools/webapi/_helpers.py` | 新增 3 个 ReasonCode |
| `tools/webapi/__init__.py` | 注册 2 条 POST 路由，路由数 53 → 55，POST 数 34 → 36 |

### 4.2 测试文件

新增：

| 文件 | 覆盖 |
|------|------|
| `tests/test_code_check_api.py` | body/path/linter、preflight、成功响应、错误映射、配置传递 |
| `tests/test_code_format_api.py` | body/path、写回/dry-run 响应、style/indent 配置传递、错误映射 |
| `tests/test_code_check_config_overrides.py` | `tools/code_check.check` 的 cppcheck 配置 override 行为 |

修改：

| 文件 | 覆盖 |
|------|------|
| `tests/test_reason_code.py` | 3 个新增 ReasonCode 字面量 |
| `tests/test_webapi_end_to_end.py` | 路由总数 53 → 55、POST 数 34 → 36、handler 注册 smoke |

### 4.3 测试策略

- Web API 单元测试 mock 业务层函数，避免依赖本机 ruff/cpplint/cppcheck/astyle 安装状态。
- 使用临时 Git 仓库验证 repo-relative path 与 preflight。
- 使用 mock 验证 `plugin._config` 中的 `default_style` / `default_indent` 正确传给 `code_format.format()`。
- 使用 fake cppcheck runner 验证 `cppcheck_enable` / `cppcheck_shortcircuit` override 改变命令行或短路行为。
- 路由测试更新既有计数和 smoke。

### 4.4 文档交付

新增：

- `docs/superpowers/specs/2026-08-12-code-check-format-webapi-design.md`
- `docs/superpowers/plans/2026-08-12-code-check-format-webapi.md`
- `docs/api/webapi-code-check-format-api.md`

不修改：

- `README.md`
- `AGENTS.md`
- `metadata.yaml`

### 4.5 验证门槛

- 所有新逻辑先写失败测试，再实现。
- 新增与受影响 focused tests 全部通过。
- 新增/修改 Python 文件 ruff 0 issues。
- 路由 smoke 通过。
- `git diff --check` 无错误。
- 工作区干净后本地提交；不推送、不创建 PR。

---

## §5 验收标准

- [ ] `POST /spcode/code-check` 可对 repo 内 `.py` 文件返回 ruff issues。
- [ ] `POST /spcode/code-check` 可对 repo 内 C/C++ 文件保持现有 cppcheck + cpplint 行为。
- [ ] `linter` 显式传 `ruff/cpplint/cppcheck` 时按选择执行。
- [ ] `POST /spcode/code-format` 默认写回文件并返回 `changed` 与文件大小。
- [ ] `check=true` 时不写回并返回 `diff_summary`。
- [ ] `default_style` / `default_indent` 从插件配置传入 formatter。
- [ ] `cppcheck_enable` / `cppcheck_shortcircuit` 从插件配置传入 checker。
- [ ] 所有 path 均无法逃逸当前仓库或指定 worktree。
- [ ] 工具不可用、扩展名不支持、文件不存在等场景均有明确 ReasonCode。
- [ ] 路由表、ReasonCode、Dashboard API 文档同步更新。
