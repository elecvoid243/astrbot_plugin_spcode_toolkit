# btw 端点设计 - "顺便问问"独立 LLM 请求

> Author: elecvoid243 · Created: 2026-07-17 · Spec for v2.20

## 1. 背景与动机

用户在进行多轮对话时，偶尔想问一个不太相关的问题，但又不想：
- 新建会话（切换成本高）
- 在当前会话内直接问（污染上下文，影响后续对话质量）

典型场景：用户在 Dashboard 的非 chat 页面操作（如 git commit 页面点击"自动生成 commit message"按钮），需要一个一次性的 LLM 请求，复用当前会话的上下文（命中 prefix cache），但**结果不计入历史**。

## 2. 需求摘要

- 新增 `POST /spcode/btw` Web API 端点
- 发起一次独立的 LLM 请求，**不附带任何工具**（`func_tool=None`）
- 复用当前会话的历史作为前缀（命中 prefix cache），但**不回写历史**
- 只接受 LLM 的文本输出
- 新指令以用户消息形式传入（不修改 system_prompt，避免破坏缓存命中）
- `umo` 可选：传入则复用该会话历史 + persona system_prompt；不传则退化为无上下文请求

## 3. 端点契约

### 3.1 请求

```
POST /spcode/btw
Content-Type: application/json
```

```json
{
  "prompt": "<必填，用户的顺便问问内容>",
  "umo": "<可选，会话来源，用于复用历史>"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 用户问题/指令。前端可在其中拼接 `<system-reminder>` 标识。空 / 非 str -> `invalid_body` |
| `umo` | string | 否 | unified_msg_origin。传入且会话存在 -> 复用历史 + persona；不传 / 会话不存在 -> 无上下文 |

### 3.2 响应

envelope 结构（沿用 `_make_envelope`）：
```json
{
  "status": "ok",
  "data": {
    "reply": "<LLM 纯文本输出>",
    "has_context": true,
    "reason": null,
    "stderr": "",
    "elapsed_ms": 1234
  }
}
```

失败时 `"reason"` 是 ReasonCode 字符串，`reply`/`has_context` 字段缺失。

| data 字段 | 类型 | 说明 |
|-----------|------|------|
| `reply` | string | LLM 的纯文本输出（`completion_text`）。失败时该字段缺失 |
| `has_context` | bool | 是否复用了会话历史。`umo` 传入且会话存在为 true，否则 false。前端据此判断是否命中缓存 |
| `reason` | string \| null | 失败原因（ReasonCode 字符串）；成功时为 null |
| `elapsed_ms` | int | handler 端到端耗时（毫秒） |

### 3.3 新增 ReasonCode

| 码 | 含义 |
|------|------|
| `no_provider` | 无可用 LLM Provider（`get_using_provider` 返回 None） |
| `empty_response` | LLM 返回空文本（`completion_text` 为空或空白） |
| `llm_error` | LLM 调用异常（`provider.text_chat` 抛错） |

复用既有 ReasonCode：`invalid_body`（prompt 空/非 str）。

## 4. 核心流程

```
POST /spcode/btw
  │
  ├─ 1. 解析 body
  │    prompt = body.get("prompt")  -> 空/非 str => invalid_body
  │    umo = body.get("umo")
  │
  ├─ 2. 获取 provider
  │    provider = context.get_using_provider(umo=umo or None)
  │    provider is None => no_provider
  │
  ├─ 3. 解析上下文（umo 传入时）
  │    if umo:
  │      cid = conversation_manager.get_curr_conversation_id(umo)
  │      if cid:
  │        conversation = conversation_manager.get_conversation(umo, cid)
  │        if conversation:
  │          history = json.loads(conversation.history)  # list[dict]
  │          persona = persona_manager.resolve_selected_persona(
  │              umo=umo,
  │              conversation_persona_id=conversation.persona_id,
  │              platform_name=<从 umo 解析>,
  │              provider_settings=<context.get_config(umo)>,
  │          )
  │          system_prompt = persona["prompt"] if persona else None
  │          contexts = history
  │          has_context = True
  │      # cid 不存在 / conversation 不存在 => contexts=None, has_context=False
  │    else:
  │      contexts = None
  │      system_prompt = None
  │      has_context = False
  │
  ├─ 4. 调用 LLM
  │    try:
  │      resp = await provider.text_chat(
  │          prompt=prompt,
  │          contexts=contexts,
  │          func_tool=None,       # 无工具
  │          system_prompt=system_prompt,
  │      )
  │    except Exception => llm_error
  │
  ├─ 5. 提取文本
  │    reply = (resp.completion_text or "").strip()
  │    reply 为空 => empty_response
  │
  ├─ 6. 不回写历史（绝不调用 update_conversation）
  │
  └─ 7. 返回 envelope
       { success: true, data: { reply, has_context } }
```

## 5. 关键设计决策

### 5.1 复用历史以命中 prefix cache

`provider.text_chat` 的 `contexts` 参数传入当前会话历史（`list[dict]`），`system_prompt` 传入当前 persona 的 prompt。这样 LLM 请求的前缀与主对话链路完全一致，命中 provider 侧的 prefix cache，降低延迟和 token 成本。

### 5.2 不回写历史

绝不调用 `conversation_manager.update_conversation`。请求是一次性的，结果不进入会话历史，不污染后续对话。

### 5.3 prompt 以 user message 传入

前端负责在 `prompt` 中拼接 `<system-reminder>` 标识（如"顺便问问"指令）。端点不额外包装，直接传给 `text_chat(prompt=...)`。这与你的要求一致：新指令以用户消息形式传入，不修改 system_prompt。

### 5.4 无工具

`func_tool=None`，LLM 无法调用任何工具。只接受文本输出，忽略 `tools_call_args` / `result_chain`。

### 5.5 umo 可选回退

`umo` 不传或会话不存在时，退化为无上下文请求（`contexts=None, system_prompt=None`）。`has_context` 字段告知前端实际状态。

### 5.6 persona 解析

复用 `persona_manager.resolve_selected_persona(umo, conversation_persona_id, platform_name, provider_settings)`，与主对话链路（`_ensure_persona_and_skills`）一致，保证 system_prompt 前缀完全匹配。

- `platform_name`：从 umo 字符串解析（格式 `platform_name:message_type:session_id`，取第一段）
- `provider_settings`：从 `context.get_config(umo)` 获取

### 5.7 无 preflight

本端点不需要项目加载 / git 仓库，与 `git-repo-check` 类似走独立路径，不经过 `_git_endpoint_preflight`。

## 6. 文件变更

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `tools/webapi/btw.py` | 新增 | handler 实现 |
| `tools/webapi/__init__.py` | 修改 | ROUTES +1 / HANDLERS +1 / import / `__all__` |
| `tools/webapi/_helpers.py` | 修改 | ReasonCode +3 码 |
| `tests/test_btw.py` | 新增 | 单元测试 |
| `tests/test_webapi_end_to_end.py` | 修改 | 路由计数 31->32 |
| `README.md` | 修改 | 端点表 +1 行 |
| `AGENTS.md` | 修改 | 端点表 +1 行 / ReasonCode 表 +3 行 / 路由计数更新 |

## 7. 不做的事（YAGNI）

- ❌ 不支持流式输出（一次性返回文本）
- ❌ 不支持图片/音频输入（纯文本）
- ❌ 不支持 system_prompt 参数（避免破坏缓存命中）
- ❌ 不鉴权（复用现有 webapi 的无鉴权模式，与其他端点一致）
- ❌ 不做 git preflight（不需要项目加载/git 仓库）

## 8. 测试计划

`tests/test_btw.py` 覆盖：

| 用例 | 说明 |
|------|------|
| 无 umo 基本请求 | contexts=None, has_context=False, 返回 reply |
| 有 umo 且会话存在 | 复用历史, has_context=True |
| 有 umo 但无会话 | 回退无上下文, has_context=False |
| prompt 为空 | invalid_body |
| prompt 非 str | invalid_body |
| 无 provider | no_provider |
| LLM 返回空文本 | empty_response |
| LLM 抛异常 | llm_error |
| 验证不回写历史 | 确认 update_conversation 未被调用 |
| 验证 func_tool=None | 确认 text_chat 未传工具 |

`tests/test_webapi_end_to_end.py`：路由计数 31->32，handler smoke。

## 9. 参考实现路径

- provider 获取：`plugin.context.get_using_provider(umo=...)` -> `Provider | None`
- 历史获取：`plugin.context.conversation_manager.get_curr_conversation_id(umo)` -> `cid`
- 会话对象：`plugin.context.conversation_manager.get_conversation(umo, cid)` -> `Conversation`（`.history` 是 JSON 字符串）
- persona 解析：`plugin.context.persona_manager.resolve_selected_persona(umo, conversation_persona_id, platform_name, provider_settings)` -> `(persona_id, persona, _, _)`
- LLM 调用：`provider.text_chat(prompt=..., contexts=[...], func_tool=None, system_prompt=...)` -> `LLMResponse`（`.completion_text`）
