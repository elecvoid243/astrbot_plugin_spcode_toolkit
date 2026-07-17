# btw 端点实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `POST /spcode/btw` Web API 端点，发起一次性 LLM 请求（无工具、无历史回写），可选复用当前会话历史以命中 prefix cache。

**Architecture:** 在 `tools/webapi/` 新增 `btw.py` handler，沿用既有 `_wrap()` / `_make_envelope()` 基础设施。端点解析 body 取 prompt+umo，用 `context.get_using_provider()` 拿当前 provider，用 `conversation_manager` + `persona_manager` 拼装历史和 system_prompt，调 `provider.text_chat(func_tool=None, contexts=<history>, system_prompt=<persona prompt>)`，仅返回 `completion_text`。不调 `update_conversation`。

**Tech Stack:** Python 3.10+ / AstrBot webapi / pytest / AstrBot `provider.text_chat` / AstrBot `ConversationManager` + `PersonaManager`

## Global Constraints

- 版本：v2.20（同步更新 `metadata.yaml`）
- 端点路径：`/spcode/btw`，method: `POST`
- 既有 webapi 模式：每个端点一个文件，handler 命名 `async def handle(plugin, ...) -> dict`
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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestBtwEndpoint:
    """btw handler 单元测试"""

    @pytest.fixture
    def mock_plugin(self):
        """构造最小 plugin mock：context 含 conversation_manager + persona_manager + get_using_provider"""
        plugin = MagicMock()
        plugin.context = MagicMock()
        plugin.context.get_using_provider = MagicMock()
        return plugin

    @pytest.mark.asyncio
    async def test_invalid_body_empty_prompt(self, mock_plugin):
        """prompt 为空 -> invalid_body"""
        from tools.webapi.btw import handle

        body = {"prompt": ""}
        resp = await handle(mock_plugin, body=body)
        assert resp["success"] is False
        assert resp["reason"] == "invalid_body"
        assert resp["data"] is None

    @pytest.mark.asyncio
    async def test_invalid_body_non_string_prompt(self, mock_plugin):
        """prompt 非字符串 -> invalid_body"""
        from tools.webapi.btw import handle

        body = {"prompt": 123}
        resp = await handle(mock_plugin, body=body)
        assert resp["success"] is False
        assert resp["reason"] == "invalid_body"

    @pytest.mark.asyncio
    async def test_no_provider(self, mock_plugin):
        """无 provider -> no_provider"""
        from tools.webapi.btw import handle

        mock_plugin.context.get_using_provider.return_value = None
        body = {"prompt": "测试问题", "umo": None}
        resp = await handle(mock_plugin, body=body)
        assert resp["success"] is False
        assert resp["reason"] == "no_provider"

    @pytest.mark.asyncio
    async def test_no_umo_basic_request(self, mock_plugin):
        """无 umo -> 回退无上下文, has_context=False"""
        from tools.webapi.btw import handle

        mock_provider = AsyncMock()
        mock_provider.text_chat = AsyncMock(
            return_value=MagicMock(completion_text="回答内容", tools_call_args=None)
        )
        mock_plugin.context.get_using_provider.return_value = mock_provider

        body = {"prompt": "顺便问问", "umo": None}
        resp = await handle(mock_plugin, body=body)
        assert resp["success"] is True
        assert resp["data"]["reply"] == "回答内容"
        assert resp["data"]["has_context"] is False
        # 验证 text_chat 被以无 tools / 无 contexts 调用
        call_kwargs = mock_provider.text_chat.call_args.kwargs
        assert call_kwargs["func_tool"] is None
        assert call_kwargs["contexts"] is None

    @pytest.mark.asyncio
    async def test_with_umo_uses_history(self, mock_plugin):
        """有 umo 且会话存在 -> 复用历史, has_context=True"""
        import json

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

        # persona_manager mock
        persona_manager = MagicMock()
        persona_manager.resolve_selected_persona = AsyncMock(
            return_value=("persona-1", {"prompt": "你是助手"}, None, False)
        )
        mock_plugin.context.persona_manager = persona_manager

        # provider mock
        mock_provider = AsyncMock()
        mock_provider.text_chat = AsyncMock(
            return_value=MagicMock(completion_text="基于历史的回答", tools_call_args=None)
        )
        mock_plugin.context.get_using_provider.return_value = mock_provider

        body = {"prompt": "顺便问问", "umo": umo}
        resp = await handle(mock_plugin, body=body)

        assert resp["success"] is True
        assert resp["data"]["reply"] == "基于历史的回答"
        assert resp["data"]["has_context"] is True
        # 验证 contexts 传入了历史
        call_kwargs = mock_provider.text_chat.call_args.kwargs
        assert call_kwargs["contexts"] == history
        assert call_kwargs["system_prompt"] == "你是助手"
        assert call_kwargs["func_tool"] is None
        # 验证 update_conversation 未被调用
        conv_manager.update_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_umo_no_conversation_fallback(self, mock_plugin):
        """有 umo 但无 cid -> 回退无上下文"""
        from tools.webapi.btw import handle

        conv_manager = MagicMock()
        conv_manager.get_curr_conversation_id = AsyncMock(return_value=None)
        mock_plugin.context.conversation_manager = conv_manager

        mock_provider = AsyncMock()
        mock_provider.text_chat = AsyncMock(
            return_value=MagicMock(completion_text="无上下文回答", tools_call_args=None)
        )
        mock_plugin.context.get_using_provider.return_value = mock_provider

        body = {"prompt": "顺便问问", "umo": "webchat:FriendMessage:empty"}
        resp = await handle(mock_plugin, body=body)
        assert resp["success"] is True
        assert resp["data"]["has_context"] is False

    @pytest.mark.asyncio
    async def test_empty_response(self, mock_plugin):
        """LLM 返回空白文本 -> empty_response"""
        from tools.webapi.btw import handle

        mock_provider = AsyncMock()
        mock_provider.text_chat = AsyncMock(
            return_value=MagicMock(completion_text="   ", tools_call_args=None)
        )
        mock_plugin.context.get_using_provider.return_value = mock_provider

        body = {"prompt": "测试", "umo": None}
        resp = await handle(mock_plugin, body=body)
        assert resp["success"] is False
        assert resp["reason"] == "empty_response"

    @pytest.mark.asyncio
    async def test_llm_error(self, mock_plugin):
        """LLM 抛异常 -> llm_error"""
        from tools.webapi.btw import handle

        mock_provider = AsyncMock()
        mock_provider.text_chat = AsyncMock(side_effect=Exception("API 500"))
        mock_plugin.context.get_using_provider.return_value = mock_provider

        body = {"prompt": "测试", "umo": None}
        resp = await handle(mock_plugin, body=body)
        assert resp["success"] is False
        assert resp["reason"] == "llm_error"

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

        mock_provider = AsyncMock()
        mock_provider.text_chat = AsyncMock(
            return_value=MagicMock(completion_text="回答", tools_call_args=None)
        )
        mock_plugin.context.get_using_provider.return_value = mock_provider

        body = {"prompt": "测试", "umo": "webchat:FriendMessage:test"}
        await handle(mock_plugin, body=body)

        conv_manager.update_conversation.assert_not_called()
        conv_manager.add_message_pair.assert_not_called()
```

- [ ] **Step 2: 运行测试确认全部失败（handle 还不存在）**

Run: `pytest tests/test_btw.py -v`
Expected: 全部失败，ModuleImportError 或 AttributeError（`tools.webapi.btw` 不存在）

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
from typing import TYPE_CHECKING, Any

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
        Envelope with data: {"reply": str, "has_context": bool}

    Author: elecvoid243, 2026-07-17
    """
    t0 = _time.perf_counter()

    # ── 1. body 校验 ──
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _make_envelope(
            success=False,
            reason=ReasonCode.INVALID_BODY,
            t0=t0,
        )

    umo_raw = body.get("umo")
    umo = umo_raw.strip() if isinstance(umo_raw, str) and umo_raw.strip() else None

    # ── 2. 获取 provider ──
    provider = plugin.context.get_using_provider(umo=umo)
    if provider is None:
        logger.warning(
            f"[btw] no LLM provider available (umo={umo!r}), returning no_provider"
        )
        return _make_envelope(
            success=False,
            reason=ReasonCode.NO_PROVIDER,
            t0=t0,
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
                history_raw = conversation.history
                if history_raw:
                    try:
                        contexts = json.loads(history_raw)
                        if not isinstance(contexts, list):
                            contexts = None
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            f"[btw] failed to parse conversation.history for {umo}, "
                            "falling back to no-context"
                        )
                        contexts = None

                if contexts is not None:
                    # resolve persona
                    platform_name = umo.split(":", 1)[0] if ":" in umo else ""
                    provider_settings = plugin.context.get_config(umo=umo)
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
                            f"[btw] resolve_selected_persona failed for {umo}: {exc}, "
                            "falling back to default system_prompt=None"
                        )
                        persona = None

                    if persona and isinstance(persona, dict):
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
        logger.exception(f"[btw] LLM call failed (umo={umo!r}): {exc}")
        return _make_envelope(
            success=False,
            reason=ReasonCode.LLM_ERROR,
            t0=t0,
        )

    # ── 5. 提取文本 ──
    reply = (getattr(resp, "completion_text", None) or "").strip()
    if not reply:
        return _make_envelope(
            success=False,
            reason=ReasonCode.EMPTY_RESPONSE,
            t0=t0,
        )

    # ── 6. 不回写历史(绝无 update_conversation) ──

    # ── 7. 返回 envelope ──
    return _make_envelope(
        success=True,
        reason=None,
        t0=t0,
        data={"reply": reply, "has_context": has_context},
    )
```

- [ ] **Step 2: 运行测试确认全部通过**

Run: `pytest tests/test_btw.py -v`
Expected: 9 用例全部 PASS

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

- [ ] **Step 1: 在 import 区添加 `btw` 模块**

在 `from .git_status import ...` 之后（或任意位置，按字母序）添加：

```python
from . import (
    ...
    btw,  # v2.20 (2026-07-17) - 一次性独立 LLM 请求
    ...
)
```

完整 import 块（保持字母序）：

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

在 `("/spcode/codegraph-status", ...)` 之后添加：

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

按字母序在 `__all__` 内顶部附近添加：

```python
"btw",  # v2.20 (2026-07-17)
```

完整 `__all__` 顶部：

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

### Task 5: 更新路由计数测试

**Files:**
- Modify: `tests/test_webapi_end_to_end.py`

- [ ] **Step 1: 找到现有的 routes / handlers 计数断言**

测试文件中查找形如：

```python
assert len(ROUTES) == 31
assert len(HANDLERS) == 30  # 或实际值
```

将 `31` 改为 `32`，将 handlers 计数改为 `31`。

- [ ] **Step 2: 运行 end_to_end 测试确认通过**

Run: `pytest tests/test_webapi_end_to_end.py -v`
Expected: 所有用例 PASS（路由计数已对齐）

- [ ] **Step 3: Commit**

```bash
cd /d F:\github\astrbot_plugin_spcode_toolkit
git add tests/test_webapi_end_to_end.py
git commit -m "test(webapi): bump route count assertion 31->32 for btw"
```

---

### Task 6: 更新文档

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: 在 README.md 端点表添加 `/spcode/btw`**

在 `/spcode/docs` DELETE 行后添加：

```markdown
| `/spcode/btw` | POST | 一次性独立 LLM 请求（顺便问问）：复用当前会话历史命中 prefix cache，不回写历史，无工具，纯文本输出 | body: `{prompt, umo?}` |
```

同时在 Web API 段开头描述中更新计数：

```markdown
Web 路由由 ... 当前共 **32 条路由记录**（30 个唯一路径，...）：
```

- [ ] **Step 2: 在 AGENTS.md 执行同样的更新**

- 在架构分层描述中更新计数
- 在端点表添加 `/spcode/btw` 行
- 在 ReasonCode 集中表添加三行：
  ```markdown
  | 业务结果 | `no_provider` / `empty_response` / `llm_error` | btw 端点专用：无可用 Provider / LLM 返回空文本 / LLM 调用异常（v2.20） |
  ```

- [ ] **Step 3: 同步更新 metadata.yaml 版本号**

将 `metadata.yaml` 中的 `version: v2.20` 确认（如果尚未是 v2.20，改为 v2.20）。

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

## Self-Review Checklist

✅ 端点契约明确（请求/响应 schema、reason 码）
✅ ReasonCode 3 个码在 `_helpers.py` 中定义
✅ handler 完全独立（无 main.py / 项目加载 / git 依赖）
✅ 测试覆盖：body 校验、provider 检查、3 种上下文场景、空响应、LLM 异常、回写验证
✅ 路由注册 + 计数同步
✅ 文档同步 + 版本号统一
✅ 不写历史（test_does_not_write_back_history 显式断言）
✅ 不传工具（test_no_umo_basic_request 显式断言 func_tool=None）
