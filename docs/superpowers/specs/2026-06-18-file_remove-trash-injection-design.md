# file_remove 改用系统回收站 + 启用时注入 LLM 指引

> **For agentic workers:** 这是 brainstorming 阶段的产出。设计经用户审核通过后，下一步调用 writing-plans 技能输出实现计划。

**状态**: 🚧 待用户审核（设计已通过 brainstorming 对话）
**作者**: elecvoid243
**目标插件**: `astrbot_plugin_spcode_toolkit`
**目标版本**: v2.x（紧随当前 main 分支）

## 动机

当前 `astrbot_file_remove_tool` 调用 `os.remove` / `os.rmdir` 做**物理删除**，且系统提示词中无任何提示。结果：

1. **不可恢复风险** — 一旦 LLM 通过 `astrbot_file_remove_tool` 删错文件，无法通过 AstrBot 撤销；用户必须依赖备份或磁盘恢复工具。
2. **LLM 行为漂移** — LLM 既然有 `astrbot_execute_shell`（可跑 `rm` / `del` / `Remove-Item` / Python `os.unlink`），当 `astrbot_file_remove_tool` 的 `confirm`/`max_items` 流程让 LLM 不耐烦时，会**绕过**它走 shell/python。绕过后所有路径安全保护（系统黑名单、用户黑名单、UNC 拦截、`..` 拦截、回收站语义）一并失效。

回收站语义 + 一句 system_prompt 注入同时解决这两个问题，且改动半径小。

## 目标

1. `astrbot_file_remove_tool` 的删除行为改为**送入系统回收站**（跨平台统一接口）。
2. **当且仅当** `astrbot_file_remove_tool` 在本次会话已启用（即在 `enabled_tools` 中），向 `req.system_prompt` 末尾追加一句简短指引，让 LLM 优先使用该工具而非 shell/python 绕过。

## 非目标（YAGNI）

- **不做"彻底删除 vs 回收站"配置开关** — 用户在 brainstorming 中明确：**所有情况下都放到回收站**。要彻底删，让用户自己在资源管理器/回收站里执行"清空"或"永久删除"。
- **不暴露 `force_permanent` 参数给 LLM** — 同上理由。
- **不引入 `pywin32` / `AppleScript` / `subprocess gio trash` 等平台分支** — 统一依赖 `send2trash` 跨平台包。
- **不动 `proposal` / `confirm` / `max_items` 协议** — 路径安全/批量确认是独立于"删除方式"的能力，全部保留。
- **不动现有路径安全校验**（系统黑名单 / 用户黑名单 / `..` / UNC / `\\?\`） — 这些校验**先于**送回收站执行，因此回收站失败时上层校验已生效。
- **不记录回收站目标路径** — `send2trash` 在 Windows 上写到 `$Recycle.Bin`、macOS 上写到 `~/.Trash`、Linux 上写到 `gio trash` 指定路径；这些都是系统行为，插件不重复记录。
- **不动其他工具**（`astrbot_execute_shell` 等） — 不给它们加"禁止删除"的限制。LLM 仍可绕过去，但有了指引后绕过的概率显著降低。

## 核心变更

| 维度 | 当前 | 本次 | 备注 |
|------|------|------|------|
| 删除实现 | `os.remove(p)` / `os.walk + os.rmdir` | `send2trash.send2trash(str(p))` | 单文件/目录统一为一次调用 |
| 目录删除返回结构 | `{ok, deleted, freed, deleted_paths[:10], errors[:10]}` | `{ok, deleted, freed}` | `deleted_paths` / `errors` 不再有 partial 语义，删除 |
| `requirements.txt` | `ruff`, `cpplint` | + `send2trash` | 跨平台第三方包 |
| `FileRemoveTool.description` | "Delete an entire file or directory..." | **末尾追加** "Items are sent to the system recycle bin (recoverable), not permanently deleted." | 原句不动 |
| 系统提示词注入 | 无 | 启用该工具时追加一句指引（见 §"设计 3"） | 复用 `_project_inject_codegraph_guidance` 模式 |
| `tools/file_remove.py` | 直接 `os.remove` | 改用 `send2trash` | 路径校验逻辑全保留 |
| `tests/test_file_remove.py` | 断言 `p.exists() is False` | 断言 `send2trash.send2trash` 被以正确参数调用 | 用 monkeypatch 拦截 |
| 新增测试 | — | `tests/test_file_remove_injection.py` | 4 个用例（见 §"设计 5"） |

## 设计

### 1. 删除实现改写（`tools/file_remove.py`）

**导入新增**:
```python
import send2trash
```

**单文件路径**（替换 `os.remove(p)`):
```python
try:
    size = p.stat().st_size
    send2trash.send2trash(str(p))
    return {"ok": True, "deleted": 1, "freed": _human_size(size)}
except OSError as e:
    return {"ok": False, "error": f"移入回收站失败: {e}"}
```

**目录路径**（替换 `os.walk + os.remove/os.rmdir` 整段循环）:
- `rglob("*")` 统计 `file_count` / `total_size`（只读，保留）
- `max_items` 超限 / 未 `confirm` → 行为完全不变（继续返回 `proposal_reply`）
- 删除改为单次 `send2trash.send2trash(str(p))`
- 返回结构简化为 `{ok, deleted, freed}`，移除 `deleted_paths` / `errors`

**错误处理矩阵**(实现时**必须**按 MRO 顺序:子类先于父类):

| 异常 | 触发条件 | 返回 |
|------|---------|------|
| `FileNotFoundError` | 文件已不存在 | `{"ok": False, "error": "路径不存在: {path}"}`（与现状一致） |
| `PermissionError` | 无权限送回收站 | `{"ok": False, "error": "无权限移入回收站: {path}"}` |
| `OSError` / `send2trash.TrashPermissionError` | Linux 缺 trash-cli / 服务不可用 | `{"ok": False, "error": "回收站不可用: {原因}。请确认系统已安装 trash-cli (Linux) 或回收站服务可用。"}` |
| 其他 `Exception` | 兜底 | `{"ok": False, "error": str(e)}` |

实现伪代码(示例):
```python
try:
    send2trash.send2trash(str(p))
except FileNotFoundError:   # 子类必须先于 OSError
    return {"ok": False, "error": f"路径不存在: {path}"}
except PermissionError:     # 子类先于 OSError
    return {"ok": False, "error": f"无权限移入回收站: {path}"}
except OSError as e:        # 兜底父类,涵盖 TrashPermissionError
    return {"ok": False, "error": f"回收站不可用: {e}。请确认系统已安装 trash-cli (Linux) 或回收站服务可用。"}
except Exception as e:
    return {"ok": False, "error": str(e)}
```

**路径校验顺序不变**: `..` → UNC/`\\?\` → 系统黑名单 → 用户黑名单 → `exists()`。回收站失败在最后一步捕获。

### 2. 工具 description 追加（`main.py::FileRemoveTool`）

原 description 第一句 `"Delete an entire file or directory. Before deleting, it is necessary to ask the user..."` **完整保留**，只在末尾追加：

```python
description: str = (
    "Delete an entire file or directory. Before deleting, it is necessary to ask the user. "
    "If delete fragments instead of the entire file, use `astrbot_file_edit_tool`. "
    "Deleting a DIRECTORY requires parameter 'confirm=true'. "
    "If a directory contains more than max_items files, the call returns a "
    "proposal asking for batch confirmation INSTEAD of deleting — read the "
    "proposal/options, then retry with confirm=true. "
    "Single files are deleted without confirm. "
    "Items are sent to the system recycle bin (recoverable), not permanently deleted."
)
```

### 3. 系统提示词注入（`main.py`）

**常量定义**（紧邻 `_PROJECT_CODEGRAPH_GUIDANCE`）:
```python
_FILE_REMOVE_GUIDANCE_MARKER = "[ASTRBOT_FILE_REMOVE_TOOL_GUIDANCE]"

_FILE_REMOVE_GUIDANCE = f"""
{_FILE_REMOVE_GUIDANCE_MARKER}
优先使用 `astrbot_file_remove_tool` 进行文件或目录删除，不要用 shell 命令（如 `rm`/`del`）或 Python 调用绕过它。
"""
```

**触发钩子**（对照 `_project_inject_codegraph_guidance` 实现）:
```python
@filter.on_llm_request()
async def _file_remove_inject_guidance(self, event, req: ProviderRequest):
    """astrbot_file_remove_tool 启用时,把"优先使用"指引注入到 system_prompt 末尾。

    触发条件(全部满足):
    - `astrbot_file_remove_tool` 在 self._tool_names 中(说明用户已启用)
    - 同 req.system_prompt 中尚未包含 marker(防重复注入)
    """
    if "astrbot_file_remove_tool" not in self._tool_names:
        return
    if _FILE_REMOVE_GUIDANCE_MARKER in (req.system_prompt or ""):
        return
    if req.system_prompt is None or req.system_prompt == "":
        req.system_prompt = _FILE_REMOVE_GUIDANCE.lstrip("\n")
    else:
        req.system_prompt = req.system_prompt + _FILE_REMOVE_GUIDANCE
    logger.debug("[file_remove] 已向 system_prompt 注入优先使用指引")
```

**注册位置**: 与现有 `filter.on_llm_request()` 装饰的方法并列（在 `_project_inject_codegraph_guidance` 附近）。

**为什么用 `self._tool_names`**: 该属性在 `main.py` 的"工具注册"段(line ~1104 附近)已根据 `enabled_tools` 配置初始化为 set — 启用的工具在集合内,未启用不在。是注入是否生效的唯一权威源。**无需新增 feature flag**——`enabled_tools` 即 gate,避免双重配置源。

**与现有注入钩子的关系**:
- `_agentsmd_inject_to_llm_request` — 依赖 session state（"已加载 AGENTS.md"），不同语义，本钩子不依赖
- `_project_inject_codegraph_guidance` — 依赖 session state（"已加载项目"），不同语义，本钩子不依赖
- `_file_remove_inject_guidance`（新增）— 仅依赖"工具全局是否启用"，无 session 状态

三者独立判断、独立 marker，可叠加。

### 4. 数据流

```
AstrBot LLM 请求
   ↓
filter.on_llm_request() 三钩子按注册顺序触发:
  - _agentsmd_inject_to_llm_request     (若本会话已加载 AGENTS.md)
  - _project_inject_codegraph_guidance  (若本会话已加载项目)
  - _file_remove_inject_guidance        (若 astrbot_file_remove_tool 全局启用) ← 新增
   ↓
LLM 决策: 看到工具 schema description(末尾"回收站"提示)
        + system_prompt 末尾指引("优先使用...不要绕过...")
   ↓
调用 astrbot_file_remove_tool(path, confirm, max_items)
   ↓
FileRemoveTool.call → file_remove.remove(...)
   ↓
路径校验(顺序不变): .. → UNC → 系统黑名单 → 用户黑名单 → exists
   ↓
单文件: send2trash.send2trash(str(p))         # 一次调用
目录:   send2trash.send2trash(str(p))         # 一次调用
   ↓
返回: {"ok": True, "deleted": N, "freed": "X.XMB"}
   或 {"ok": False, "error": "..."}
```

### 5. 测试策略

#### 5.1 更新 `tests/test_file_remove.py` 现有用例

**改动原则**: **所有产生成功返回 (`r["ok"] is True`) 的用例必须 mock `send2trash.send2trash`**,否则会把 `tmp_path` 下的真实测试文件送入系统回收站(CI 污染 + 部分容器无回收站直接报错)。

**受影响用例清单**(共 5 个,line 编号基于 `tests/test_file_remove.py`):
- `test_remove_single_file` (line 23)
- `test_remove_dir_with_confirm` (line 47)
- `test_remove_user_blacklist_empty_allows` (line 185)
- `test_remove_user_blacklist_none_allows` (line 194)
- `test_remove_user_blacklist_unrelated_allows` (line 203)

mock 方式统一为 `monkeypatch.setattr(file_remove, "send2trash", mock_send2trash)`,断言 `mock_send2trash.send2trash.assert_called_once_with(str(p))`,**不再**断言 `p.exists() is False`(回收站不在原路径)。

**未受影响的用例**(路径在校验阶段拦截,send2trash 不应被调用,新增 `mock.assert_not_called()` 断言):系统黑名单、`..`/UNC 拦截、用户黑名单、目录 confirm/proposal 路径等全部保留原行为,仅加 `mock.assert_not_called()`。

**推荐实现**:在 `tests/test_file_remove.py` 顶部加 `pytest.fixture(autouse=True)`,**对所有用例默认 monkeypatch `send2trash` 为 MagicMock**,然后成功路径用例用 `mock_send2trash.send2trash.assert_called_once_with(...)` 做断言。这样无需逐个手工 mock,但显式断言保留。

新增 `test_remove_returns_error_when_trash_fails`:让 mock `send2trash.send2trash` 抛 `OSError("trash-cli not found")`,断言 `r["ok"] is False` 且 error 提示"回收站不可用"。

**`_tool_names` 注入要求**: 现有 `tests/test_project_cmd.py::_make_plugin` 不初始化 `plugin._tool_names`(因为该测试不需要)。新测试文件不得直接复用该 helper(否则 `AttributeError`)。**方案 A**(推荐):在 `tests/test_file_remove_injection.py` 内自己定义一个最小 `_make_plugin` helper,显式 `plugin._tool_names = {"astrbot_file_remove_tool"}` 或 `set()`,与 project_cmd 测试解耦。

#### 5.2 新增 `tests/test_file_remove_injection.py`

覆盖 4 个用例（紧邻 `tests/test_project_cmd.py::test_inject_guidance_*` 风格）:
1. `test_inject_guidance_when_tool_enabled` — `_tool_names` 含 `astrbot_file_remove_tool` → 注入 marker
2. `test_inject_no_inject_when_tool_disabled` — `_tool_names` 不含 → 不注入
3. `test_inject_idempotent` — 同一 req 多次走钩子不重复
4. `test_inject_handles_none_system_prompt` — `system_prompt = None` 时正确初始化

`_make_plugin` helper 在本文件内自包含(详见 §5.1 "_tool_names 注入要求" 方案 A)。

### 6. 验收标准

- `ruff check .` 无 error
- `pytest tests/` 全部 PASS（`codegraph_*` 相关脚本可在没有 codegraph MCP 的环境下 skip — 见 §"兼容性"）
- `requirements.txt` 含 `send2trash`
- 删除的文件可在系统回收站恢复
- 工具启用时 AstrBot 日志可见 `[file_remove] 已向 system_prompt 注入优先使用指引`
- 工具未启用时日志不出现该行、`req.system_prompt` 不含 marker
- `_tool_names` 判定覆盖了 `enabled_tools` 过滤后的真实状态（与 `context.add_llm_tools(*tools_to_register)` 一致）

### 7. 兼容性 / 风险

| 风险 | 缓解 |
|------|------|
| Linux 主机未装 trash-cli → send2trash 抛 `OSError` | 返回明确 error 给 LLM（设计 1. 错误矩阵），不 fallback 到 `os.remove`（用户明确禁止） |
| `file_remove` 返回字段删减 (`deleted_paths[:10]` / `errors[:10]`) | 旧依赖此字段的 LLM prompt / 外部脚本需同步更新;但 `deleted` / `freed` 保留,核心信息无损 |
| 验收脚本 `pytest tests/` 全跑时遇到 codegraph MCP 不可用 | `codegraph_*` 相关脚本可单独 skip / xfail；不阻塞本特性验收 |
| 同一文件被 send2trash 两次（罕见但可能：LLM 重试） | 第二次会抛 `FileNotFoundError` → `{"ok": False, "error": "路径不存在: ..."}`，LLM 看到后停止重试 |
| `_tool_names` 是 set 而非 list，对 in 检查 OK | 不需要额外类型处理 |
| `send2trash` 是首次引入运行时依赖 | AGENTS.md §"依赖管理" 说明需更新:从"仅含 lint 工具"改为"含 lint 工具与运行时依赖 send2trash" |

### 8. 不在本 spec 范围

- 改写 `astrbot_execute_shell` 拦截删除命令（如拦截 `rm -rf`） — 改动大、误伤多，留待后续
- 给回收站提供"白名单 / 跳过回收站"开关 — YAGNI
- 把回收站路径写进插件日志或返回字段 — YAGNI（系统行为，无需插件层介入）

## 变更文件清单

- `requirements.txt` — 新增 `send2trash`
- `tools/file_remove.py` — `os.remove` / `os.rmdir` → `send2trash.send2trash`,目录返回结构精简,错误处理矩阵扩展;模块 docstring "执行" 改为 "送入回收站"
- `main.py` — `FileRemoveTool.description` 末尾追加回收站说明；新增 `_FILE_REMOVE_GUIDANCE_MARKER` / `_FILE_REMOVE_GUIDANCE` 常量与 `_file_remove_inject_guidance` 钩子
- `tests/test_file_remove.py` — 现有用例改 mock 风格；新增 `test_remove_returns_error_when_trash_fails`
- `tests/test_file_remove_injection.py` — 新增，4 个用例
- `AGENTS.md` §"依赖管理" 一句话更新（lint + 运行时依赖）
