"""llm_inject — LLM 请求前的 system_prompt 注入 helpers。

PR-4 (2026-06-23) 提取:
- inject_guidance(): 把静态 guidance 文本追加到 req.system_prompt 末尾,
  带 marker 防重复注入,处理 None / 空字符串边界情况。

背景: 多个 @filter.on_llm_request() 钩子(项目 codegraph 指引 / file_remove
指引)有相同的 5 行模式:marker 检查 → 追加到 system_prompt。本模块把这套
样板集中到一处,2 个 hook 各自变成"条件检查 + 1 行调用"。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.provider import ProviderRequest


def inject_guidance(
    req: ProviderRequest,
    text: str,
    marker: str,
    *,
    strip_leading_newline: bool = True,
) -> bool:
    """把 ``text`` 追加到 ``req.system_prompt``,带 marker 防重复。

    行为:
    - 若 ``marker`` 已在 system_prompt 中 → no-op(防止同一请求多次注入)
    - 若 ``req.system_prompt is None or == ''`` → 设为 ``text``(默认
      ``strip_leading_newline=True`` 时去掉 text 开头的空行,避免 LLM
      看到多 1 行前缀空行)
    - 若已有 system_prompt → 拼接 ``existing + text``

    Args:
        req: 目标 ProviderRequest(直接修改 system_prompt 字段)
        text: 要注入的 guidance 文本
        marker: 防重复的标记(若 text 末尾已含 marker 可省略;通常由调用方
            显式传入以支持"全文 marker 与文本中 marker 分离")
        strip_leading_newline: system_prompt 为空时是否去掉 text 开头的
            ``\\n``(默认 True 保持与原行为一致)

    Returns:
        ``True`` 表示已注入,``False`` 表示跳过(空 text / marker 已存在 /
        文本为空)。
    """
    if not text:
        return False
    current = req.system_prompt or ""
    if marker and marker in current:
        return False
    payload = text
    if strip_leading_newline and not current:
        payload = payload.lstrip("\n")
    req.system_prompt = (current + payload) if current else payload
    return True


def last_marked_text(req: ProviderRequest, marker: str) -> str | None:
    """Return the most recent injected block carrying ``marker``.

    Injected blocks are persisted with the conversation, so history can hold
    several blocks from earlier states. Only the newest one describes the state
    in effect now; comparing against older ones would suppress a legitimate
    re-injection after the state changed and then changed back.

    Args:
        req: Request about to be handed to the agent runner.
        marker: Stable text that identifies the injection.

    Returns:
        The newest marked text, or None when nothing was injected yet.
    """
    parts = getattr(req, "extra_user_content_parts", None)
    if isinstance(parts, (list, tuple)):
        for part in reversed(parts):
            part_text = (
                part.get("text")
                if isinstance(part, dict)
                else getattr(part, "text", None)
            )
            if isinstance(part_text, str) and marker in part_text:
                return part_text

    contexts = getattr(req, "contexts", None)
    if isinstance(contexts, (list, tuple)):
        for message in reversed(contexts):
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                if marker in content:
                    return content
            elif isinstance(content, list):
                for block in reversed(content):
                    if isinstance(block, dict):
                        block_text = block.get("text")
                        if isinstance(block_text, str) and marker in block_text:
                            return block_text
    return None


def already_injected(req: ProviderRequest, text: str, marker: str) -> bool:
    """Return whether the newest marked block already equals ``text``.

    Args:
        req: Request about to be handed to the agent runner.
        text: Text about to be injected.
        marker: Stable text that identifies the injection.

    Returns:
        True when the most recent marked block is this text.
    """
    last = last_marked_text(req, marker)
    if last is None:
        return False
    return last == text or text in last
