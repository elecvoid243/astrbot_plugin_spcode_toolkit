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
                            conversation_persona_id=getattr(
                                conversation, "persona_id", None
                            ),
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
