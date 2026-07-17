# btw 端点实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `POST /spcode/btw` Web API 端点，发起一次性 LLM 请求（无工具、无历史回写），可选复用当前会话历史以命中 prefix cache。

**Architecture:** 在 `tools/webapi/` 新增 `btw.py` handler，沿用既有 `_wrap()` / `_make_envelope()` 基础设施。端点解析 body 取 prompt+umo，用 `context.get_using_provider()` 拿当前 provider，用 `conversation_manager` + `persona_manager` 拼装历史和 system_prompt，调 `provider.text_chat(func_tool=None, contexts=<history>, system_prompt=<persona prompt>)`，仅返回 `completion_text`。不调 `update_conversation`。

**Tech Stack:** Python 3.10+ / AstrBot webapi / pytest / AstrBot `provider.text_chat` / AstrBot `ConversationManager` + `PersonaManager`

## Global Constraints

- 版本：v2.20（同步更新 `metadata.yaml`）
- 端点路径：`/spcode/btw`，method: `POST`
- 既有 webapi 模式：每个端点一个文件，handler 命名 `async def handle(plugin, *, ...) -> dict`
- envelope 结构（沿用 `_make_envelope`）：`{"status": "ok", "data": {**fields, reason, stderr, elapsed_ms}}`
  - **不嵌套 `data={...}`**：`reply` 和 `has_context` 应作为顶层 kwargs 传给 `_make_envelope`，由它平铺到 `data` 里
  - handler 内手动计算 `elapsed_ms = int((perf_counter() - t0) * 1000)`
- 端点表计数：31 -> 32（路由记录），29 -> 30（唯一路径）
- ReasonCode 在 `tools/webapi/_helpers.py` `ReasonCode` 类中新增 3 个码
- 测试统一用 pytest，与项目测试套件共存

---

### Task 1: 新增 ReasonCode 三个码字面量

**Files:**
- Modify: `tools/webapi/_helpers.py`

- [ ] **Step 1: 在 `ReasonCode` 类内新增三个码字面量**

在 `INVALID_PARAM = "invalid_param"` 之后新增：

```python
# ── btw 端点专用(v2.20, 2026-07-17) ──
NO_PROVIDER = "no_provider"  # 无可用 LLM Provider
EMPTY_RESPONSE = "empty_response"  # LLM 返回空文本
LLM_ERROR = "llm_error"  # LLM 调用异常(provider.text_chat 抛错)
```

- [ ] **Step 2: Commit**

```bash
cd /d F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/_helpers.py
git commit -m "feat(webapi): add 3 ReasonCode literals for btw endpoint"
```

---

### Task 2: 写 btw handler 失败测试

**Files:**
- Create: `tests/test_btw.py`

- [ ] **Step 1: 创建测试文件，含以下用例骨架**

```python
"""POST /spcode/btw - 一次性独立 LLM 请求端点单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestBtwEndpoint:
    """btw handler 单元测试"""

    @pytest.fixture
    def mock_plugin(self):
        """构造最小 plugin mock"""
        plugin = MagicMock()
        plugin.context = MagicMock()
        plugin.context.get_using_provider = MagicMock()
        return plugin

    def _mock_provider(self, completion_text="回答内容"):
        """构造 mock provider，text_chat 返回指定文本"""
        mock_provider = MagicMock()
        mock_provider.text_chat = AsyncMock(
            return_value=MagicMock(completion_text=completion_text, tools_call_args=None)
        )
        return mock_provider

    @pytest.mark.asyncio
    async def test_invalid_body_empty_prompt(self, mock_plugin):
        """prompt 为空 -> invalid_body"""
        from tools.webapi.btw import handle

        resp = await handle(mock_plugin, body={"prompt": ""})
        assert resp["data"]["reason"] == "invalid_body"
        assert "reply" not in resp["data"]

    @pytest.mark.asyncio
    async def test_invalid_body_non_string_prompt(self, mock_plugin):
        """prompt 非字符串 -> invalid_body"""
        from tools.webapi.btw import handle

        resp = await handle(mock_plugin, body={"prompt": 123})
        assert resp["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_invalid_body_missing_prompt(self, mock_plugin):
        """prompt 字段缺失 -> invalid_body"""
        from tools.webapi.btw import handle

        resp = await handle(mock_plugin, body={})
        assert resp["data"]["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_no_provider(self, mock_plugin):
        """无 provider -> no_provider"""
        from tools.webapi.btw import handle

        mock_plugin.context.get_using_provider.return_value = None
        resp = await handle(mock_plugin, body={"prompt": "测试问题"})
        assert resp["data"]["reason"] == "no_provider"

    @pytest.mark.asyncio
    async def test_no_umo_basic_request(self, mock_plugin):
        """无 umo -> 回退无上下文, has_context=False"""
        from tools.webapi.btw import handle

        mock_plugin.context.get_using_provider.return_value = self._mock_provider("回答内容")

        resp = await handle(mock_plugin, body={"prompt": "顺便问问"})
        assert resp["data"]["reason"] is None
        assert resp["data"]["reply"] == "回答内容"
        assert resp["data"]["has_context"] is False
        # 验证 text_chat 被以无 tools / 无 contexts 调用
        call_kwargs = mock_plugin.context.get_using_provider.return_value.text_chat.call_args.kwargs
        assert call_kwargs["func_tool"] is None
        assert call_kwargs["contexts"] is None

    @pytest.mark.asyncio
    async def test_with_umo_uses_history(self, mock_plugin):
        """有 umo 且会话存在 -> 复用历史, has_context=True"""
        from tools.webapi.btw import handle

        umo = "webchat:FriendMessage:test-session"
        history = [
            {"role": "user", "content": "之前的提问"},
            {"role": "assistant", "content": "之前的回答"},
        ]
        # conversation_manager mock
        conv_manager = MagicMock()
        conv_manager.get_curr_conversation_id = AsyncMock(return_value="cid-1")
        mock_conversation = MagicMock()
        mock_conversation.history = json.dumps(history)
        mock_conversation.persona_id = "persona-1"
        conv_manager.get_conversation = AsyncMock(return_value=mock_conversation)
        mock_plugin.context.conversation_manager = conv_manager

        # persona_manager mock (注意是 sync AST export, 但 resolve_selected_persona 是 async)
        persona_manager = MagicMock()
        persona_manager.resolve_selected_persona = AsyncMock(
            return_value=("persona-1", {"prompt": "你是助手"}, None, False)
        )
        mock_plugin.context.persona_manager = persona_manager

        # provider mock
        mock_plugin.context.get_using_provider.return_value = self._mock_provider("基于历史的回答")

        resp = await handle(mock_plugin, body={"prompt": "顺便问问", "umo": umo})

        assert resp["data"]["reason"] is None
        assert resp["data"]["reply"] == "基于历史的回答"
        assert resp["data"]["has_context"] is True
        # 验证 contexts / system_prompt 传入
        call_kwargs = mock_plugin.context.get_using_provider.return_value.text_chat.call_args.kwargs
        assert call_kwargs["contexts"] == history
        assert call_kwargs["system_prompt"] == "你是助手"
        assert call_kwargs["func_tool"] is None
        # 验证 update_conversation 未被调用
        conv_manager.update_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_umo_no_cid_fallback(self, mock_plugin):
        """有 umo 但无 cid -> 回退无上下文"""
        from tools.webapi.btw import handle

        conv_manager = MagicMock()
        conv_manager.get_curr_conversation_id = AsyncMock(return_value=None)
        mock_plugin.context.conversation_manager = conv_manager

        mock_plugin.context.get_using_provider.return_value = self._mock_provider("无上下文回答")

        resp = await handle(mock_plugin, body={"prompt": "顺便问问", "umo": "webchat:FriendMessage:empty"})
        assert resp["data"]["reason"] is None
        assert resp["data"]["has_context"] is False

    @pytest.mark.asyncio
    async def test_empty_response(self, mock_plugin):
        """LLM 返回空白文本 -> empty_response"""
        from tools.webapi.btw import handle

        mock_plugin.context.get_using_provider.return_value = self._mock_provider("   ")

        resp = await handle(mock_plugin, body={"prompt": "测试"})
        assert resp["data"]["reason"] == "empty_response"
        assert "reply" not in resp["data"]

    @pytest.mark.asyncio
    async def test_llm_error(self, mock_plugin):
        """LLM 抛异常 -> llm_error"""
        from tools.webapi.btw import handle

        mock_provider = MagicMock()
        mock_provider.text_chat = AsyncMock(side_effect=Exception("API 500"))
        mock_plugin.context.get_using_provider.return_value = mock_provider

        resp = await handle(mock_plugin, body={"prompt": "测试"})
        assert resp["data"]["reason"] == "llm_error"

    @pytest.mark.asyncio
    async def test_does_not_write_back_history(self, mock_plugin):
        """验证绝不调用 update_conversation"""
        from tools.webapi.btw import handle

        conv_manager = MagicMock()
        conv_manager.get_curr_conversation_id = AsyncMock(return_value="cid-1")
        mock_conversation = MagicMock()
        mock_conversation.history = '[{"role":"user","content":"hi"}]'
        mock_conversation.persona_id = None
        conv_manager.get_conversation = AsyncMock(return_value=mock_conversation)
        mock_plugin.context.conversation_manager = conv_manager

        persona_manager = MagicMock()
        persona_manager.resolve_selected_persona = AsyncMock(
            return_value=(None, None, None, False)
        )
        mock_plugin.context.persona_manager = persona_manager

        mock_plugin.context.get_using_provider.return_value = self._mock_provider("回答")

        await handle(mock_plugin, body={"prompt": "测试", "umo": "webchat:FriendMessage:test"})

        conv_manager.update_conversation.assert_not_called()
        conv_manager.add_message_pair.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_umo_history_parse_failure_fallback(self, mock_plugin):
        """history JSON 解析失败 -> 回退无上下文"""
        from tools.webapi.btw import handle

        conv_manager = MagicMock()
        conv_manager.get_curr_conversation_id = AsyncMock(return_value="cid-1")
        mock_conversation = MagicMock()
        mock_conversation.history = "NOT_VALID_JSON{{{"
        mock_conversation.persona_id = None
        conv_manager.get_conversation = AsyncMock(return_value=mock_conversation)
        mock_plugin.context.conversation_manager = conv_manager

        mock_plugin.context.get_using_provider.return_value = self._mock_provider("无上下文回答")

        resp = await handle(
            mock_plugin, body={"prompt": "顺便问问", "umo": "webchat:FriendMessage:bad"}
        )
        assert resp["data"]["reason"] is None
        assert resp["data"]["has_context"] is False
```

- [ ] **Step 2: 运行测试确认全部失败（handle 还不存在）**

Run: `pytest tests/test_btw.py -v`
Expected: 全部失败，ModuleImportError（`tools.webapi.btw` 不存在）

---

### Task 3: 实现 btw handler

**Files:**
- Create: `tools/webapi/btw.py`

- [ ] **Step 1: 写 handler 实现**

```python
"""POST /spcode/btw - 一次性独立 LLM 请求(顺便问问)端点(v2.20)。

设计动机:
    用户在多轮对话中偶尔想问一个不太相关的问题,又不想新建会话,
    也不想污染当前会话上下文。该端点提供一次性 LLM 请求:
    - 复用当前会话历史 + persona system_prompt (命中 prefix cache)
    - 不回写历史 (绝不调 update_conversation)
    - 无工具 (func_tool=None)
    - 仅返回 completion_text 纯文本

    Author: elecvoid243, 2026-07-17
"""

from __future__ import annotations

import json
import logging
import time as _time
from typing import TYPE_CHECKING

from ._helpers import ReasonCode, _make_envelope

if TYPE_CHECKING:
    from main import SPCodeToolkit

logger = logging.getLogger(__name__)


async def handle(
    plugin: "SPCodeToolkit",
    *,
    body: dict,
) -> dict:
    """POST /spcode/btw handler.

    Body schema:
        {
            "prompt": str,  # 必填
            "umo": str | None  # 可选, unified_msg_origin
        }

    Returns:
        Envelope ``{"status": "ok", "data": {...}}``,成功时 data 含
        ``reply`` / ``has_context``;失败时 data 含 ``reason`` (ReasonCode 字符串)。

    Author: elecvoid243, 2026-07-17
    """
    t0 = _time.perf_counter()

    def _elapsed() -> int:
        return int((_time.perf_counter() - t0) * 1000)

    # ── 1. body 校验(防御性: _wrap 保证 body 是 dict, 但保险起见) ──
    if not isinstance(body, dict):
        body = {}
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            elapsed_ms=_elapsed(),
        )

    umo_raw = body.get("umo")
    if isinstance(umo_raw, str) and umo_raw.strip():
        umo: str | None = umo_raw.strip()
    else:
        umo = None

    # ── 2. 获取 provider ──
    provider = plugin.context.get_using_provider(umo=umo)
    if provider is None:
        logger.warning(
            "[btw] no LLM provider available (umo=%r), returning no_provider",
            umo,
        )
        return _make_envelope(
            success=False,
            reason=ReasonCode.NO_PROVIDER,
            elapsed_ms=_elapsed(),
        )

    # ── 3. 解析上下文(umo 传入时尝试复用历史) ──
    contexts: list[dict] | None = None
    system_prompt: str | None = None
    has_context = False

    if umo:
        conv_mgr = plugin.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(umo)
        if cid:
            conversation = await conv_mgr.get_conversation(umo, cid)
            if conversation:
                history_raw = getattr(conversation, "history", None)
                if history_raw:
                    try:
                        parsed = json.loads(history_raw)
                        if isinstance(parsed, list):
                            contexts = parsed
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "[btw] failed to parse conversation.history for %r, "
                            "falling back to no-context",
                            umo,
                        )
                        contexts = None

                if contexts is not None:
                    # resolve persona
                    platform_name = umo.split(":", 1)[0] if ":" in umo else ""
                    provider_settings = plugin.context.get_config(umo=umo)
                    persona = None
                    try:
                        (
                            _persona_id,
                            persona,
                            _,
                            _,
                        ) = await plugin.context.persona_manager.resolve_selected_persona(
                            umo=umo,
                            conversation_persona_id=getattr(conversation, "persona_id", None),
                            platform_name=platform_name,
                            provider_settings=provider_settings,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[btw] resolve_selected_persona failed for %r: %s, "
                            "falling back to system_prompt=None",
                            umo,
                            exc,
                        )
                        persona = None

                    if isinstance(persona, dict):
                        persona_prompt = persona.get("prompt")
                        if isinstance(persona_prompt, str) and persona_prompt.strip():
                            system_prompt = persona_prompt

                    has_context = True

    # ── 4. 调用 LLM ──
    try:
        resp = await provider.text_chat(
            prompt=prompt,
            contexts=contexts,
            func_tool=None,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        logger.exception("[btw] LLM call failed (umo=%r): %s", umo, exc)
        return _make_envelope(
            success=False,
            reason=ReasonCode.LLM_ERROR,
            elapsed_ms=_elapsed(),
        )

    # ── 5. 提取文本 ──
    reply = (getattr(resp, "completion_text", None) or "").strip()
    if not reply:
        return _make_envelope(
            success=False,
            reason=ReasonCode.EMPTY_RESPONSE,
            elapsed_ms=_elapsed(),
        )

    # ── 6. 不回写历史(绝无 update_conversation) ──

    # ── 7. 返回 envelope(注意 reply/has_context 是顶层 kwargs, _make_envelope 会平铺到 data) ──
    return _make_envelope(
        success=True,
        elapsed_ms=_elapsed(),
        reply=reply,
        has_context=has_context,
    )
```

- [ ] **Step 2: 运行测试确认全部通过**

Run: `pytest tests/test_btw.py -v`
Expected: 12 用例全部 PASS

- [ ] **Step 3: Commit**

```bash
cd /d F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/btw.py tests/test_btw.py
git commit -m "feat(webapi): POST /spcode/btw - standalone LLM request endpoint"
```

---

### Task 4: 注册路由到 ROUTES 和 HANDLERS

**Files:**
- Modify: `tools/webapi/__init__.py`

- [ ] **Step 1: 在 import 区按字母序添加 `btw` 模块**

完整按字母序的 import 块（`btw` 放在 `codegraph_status` 之前）：

```python
from . import (
    btw,  # v2.20 (2026-07-17) - 一次性独立 LLM 请求(顺便问问)
    codegraph_status,
    docs_crud,
    file_browser,
    file_discard_hunk,
    file_name_search,
    file_restore,
    file_search,
    git_branch_create,
    git_branch_delete,
    git_branches,
    git_branch_switch,
    git_commit,
    git_diff,
    git_file,
    git_init,
    git_log,
    git_repo_check,
    git_revert,
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
```

- [ ] **Step 2: 在 ROUTES 列表添加一条**

将代码插到 `codegraph_status` ROUTE 条目之后、`git_file` 之前：

```python
(
    "/spcode/btw",  # v2.20 (2026-07-17)
    ["POST"],
    btw.handle,
    "一次性独立 LLM 请求(顺便问问): 复用当前会话历史(命中 prefix cache),不回写历史,无工具,纯文本输出",
),
```

- [ ] **Step 3: 在 HANDLERS 别名表添加**

在 `"handle_get_codegraph_status": ...` 之后添加：

```python
"handle_post_btw": btw.handle,  # v2.20 (2026-07-17)
```

- [ ] **Step 4: 在 `__all__` 添加 `btw`**

将 `"btw"` 插到字母序首位（`"ROUTES"` 之后）：

```python
__all__ = [
    "ROUTES",
    "HANDLERS",
    "_wrap",
    "register_webapi_routes",
    "btw",  # v2.20 (2026-07-17)
    "codegraph_status",
    ...
]
```

- [ ] **Step 5: Commit**

```bash
cd /d F:\github\astrbot_plugin_spcode_toolkit
git add tools/webapi/__init__.py
git commit -m "feat(webapi): register POST /spcode/btw route (v2.20, route count 31->32)"
```

---

### Task 5: 更新 end_to_end 测试的路由断言

**Files:**
- Modify: `tests/test_webapi_end_to_end.py`

- [ ] **Step 1: 把 `/spcode/btw` 加入路由 set 断言**

在 `test_routes_table_has_thirty_endpoints` 的 `routes == {` 集合里添加 `/spcode/btw`（放在 `# ── v2.17.0` 块之前）：

```python
        "/spcode/codegraph-status",  # v2.14.x (2026-06-28)
        # ── v2.20 (2026-07-17) — POST /spcode/btw ──
        "/spcode/btw",  # v2.20 btw endpoint
```

- [ ] **Step 2: 把 methods POST 计数从 17 改到 18**

找到 `assert methods.count("POST") == 17` 行，改为：

```python
    # v2.20 (2026-07-17): +1 POST for btw endpoint
    assert methods.count("POST") == 18
```

并在 docstring 注释中说明总数演变：32 entries，13 GET + 18 POST + 1 PATCH + 1 DELETE

- [ ] **Step 3: 把 `test_register_webapi_routes_calls_context_thirty_times` 改名并更新计数**

找到该函数，改为：

```python
def test_register_webapi_routes_calls_context_thirty_two_times() -> None:
    """``register_webapi_routes`` must call ``register_web_api`` once per route.

    v2.17.0 (2026-07-15): route count 24 -> 30(+git-init/branches/create/delete/switch/revert)。
    v2.18.0 (2026-07-16): route count 30 -> 31(+git-repo-check)。
    v2.20 (2026-07-17): route count 31 -> 32(+btw)。
    """
    plugin = MagicMock()
    register_webapi_routes(plugin)
    # 32 entries total: 31 + 1 v2.20
    assert plugin.context.register_web_api.call_count == 32
```

- [ ] **Step 4: 把 `handle_post_btw` 加入 `_SKIP_FILE_BROWSER` 跳过 smoke 测试**

btw handler 需要 `body` 参数；现有 `test_handler_callable_returns_dict` 用 `await handler(plugin)`（无 kwargs）会失败。把 `handle_post_btw` 加入 skip 集合：

```python
_SKIP_FILE_BROWSER = frozenset(
    {
        "handle_get_file_browser",
        "handle_get_git_log",
        "handle_get_git_show",
        "handle_get_git_file",
        "handle_post_btw",  # v2.20 — 需要 body 参数(由 _wrap 注入)
    }
)
```

并在文件末尾加一个 pinning test（仿照已有的 `test_git_show_handler_excluded_from_smoke` 模式）：

```python
def test_btw_handler_excluded_from_smoke() -> None:
    """v2.20: handle_post_btw 需要 body 参数,smoke parametrize 自动调
    handler(plugin) 会触发 TypeError(body 是 keyword-only)。"""
    assert "handle_post_btw" not in (set(HANDLERS.keys()) - _SKIP_FILE_BROWSER)
```

- [ ] **Step 5: 跑 end_to_end 测试确认通过**

Run: `pytest tests/test_webapi_end_to_end.py -v`
Expected: 所有用例 PASS

- [ ] **Step 6: Commit**

```bash
cd /d F:\github\astrbot_plugin_spcode_toolkit
git add tests/test_webapi_end_to_end.py
git commit -m "test(webapi): route count 31->32 + btw skip for body-required handler"
```

---

### Task 6: 更新文档（README.md / AGENTS.md / metadata.yaml）

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `metadata.yaml`

- [ ] **Step 1: 在 README.md 端点表中添加 `/spcode/btw`**

在 `/spcode/docs` DELETE 行后添加：

```markdown
| `/spcode/btw` | POST | 一次性独立 LLM 请求（顺便问问）：复用当前会话历史命中 prefix cache，不回写历史，无工具，纯文本输出 | body: `{prompt, umo?}` |
```

同时把描述 "当前共 **31 条路由记录**（29 个唯一路径...）" 改为 **32 条路由记录（30 个唯一路径）**。

- [ ] **Step 2: 在 AGENTS.md 做同样更新**

- 架构分层段：`31 条路由记录 / 29 个唯一路径` → `32 条路由记录 / 30 个唯一路径`
- 端点表添加 `/spcode/btw` 行（同上）
- ReasonCode 集中表添加三行：
  ```markdown
  | 业务结果 | `no_provider` | btw 端点专用：无可用 LLM Provider（v2.20） |
  | 业务结果 | `empty_response` | btw 端点专用：LLM 返回空文本（v2.20） |
  | 业务结果 | `llm_error` | btw 端点专用：LLM 调用异常（v2.20） |
  ```

- [ ] **Step 3: 同步更新 `metadata.yaml` 版本号**

将 `metadata.yaml` 中的 `version: v2.17.0` 改为 `v2.20`，并在 description 末尾追加：

```yaml
v2.20 新增 POST /spcode/btw 端点(2026-07-17) — 一次性独立 LLM 请求
  (顺便问问):复用当前会话历史命中 prefix cache 但不回写历史,
  无工具,仅返回纯文本输出;umo 可选回退无上下文。
```

- [ ] **Step 4: 跑完整测试套件确认无回归**

Run: `pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 5: 跑 lint**

Run: `ruff check tools/webapi/btw.py tools/webapi/__init__.py tools/webapi/_helpers.py tests/test_btw.py tests/test_webapi_end_to_end.py`
Expected: 无 error（warning 可接受）

- [ ] **Step 6: Commit**

```bash
cd /d F:\github\astrbot_plugin_spcode_toolkit
git add README.md AGENTS.md metadata.yaml
git commit -m "docs: sync README/AGENTS/metadata for v2.20 btw endpoint"
```

---

## 自检要点（实施前确认）

✅ `_make_envelope(success, reason, stderr, elapsed_ms, **data_fields)` 实际签名 — `reply`/`has_context` 作为 kwargs 平铺到 `data`
✅ envelope 真实结构：`{"status": "ok", "data": {...}}`（**不是** `{success, reason, ...}`，README 描述已修正）
✅ 路由计数测试：精确列举 set + methods count（POST 17→18，函数名改 `thirty_two`）
✅ `handle_post_btw` 需 `body` 形参，加入 `_SKIP_FILE_BROWSER` 跳过 parametrize smoke
✅ handler 不依赖 `tools._helpers`（避免循环），仅 import `._helpers`
✅ persona manager 是属性 `plugin.context.persona_manager`（已在 main.py 验证）
✅ LLM 调用失败用 `logger.exception` 保留堆栈
