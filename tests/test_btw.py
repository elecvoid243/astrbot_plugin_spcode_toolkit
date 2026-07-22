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
            return_value=MagicMock(
                completion_text=completion_text, tools_call_args=None
            )
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

        mock_plugin.context.get_using_provider.return_value = self._mock_provider(
            "回答内容"
        )

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

        # persona_manager mock
        persona_manager = MagicMock()
        persona_manager.resolve_selected_persona = AsyncMock(
            return_value=("persona-1", {"prompt": "你是助手"}, None, False)
        )
        mock_plugin.context.persona_manager = persona_manager

        # provider mock
        mock_plugin.context.get_using_provider.return_value = self._mock_provider(
            "基于历史的回答"
        )

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

        mock_plugin.context.get_using_provider.return_value = self._mock_provider(
            "无上下文回答"
        )

        resp = await handle(
            mock_plugin,
            body={"prompt": "顺便问问", "umo": "webchat:FriendMessage:empty"},
        )
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

        mock_plugin.context.get_using_provider.return_value = self._mock_provider(
            "回答"
        )

        await handle(
            mock_plugin, body={"prompt": "测试", "umo": "webchat:FriendMessage:test"}
        )

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

        mock_plugin.context.get_using_provider.return_value = self._mock_provider(
            "无上下文回答"
        )

        resp = await handle(
            mock_plugin,
            body={"prompt": "顺便问问", "umo": "webchat:FriendMessage:bad"},
        )
        assert resp["data"]["reason"] is None
        assert resp["data"]["has_context"] is False
