# -*- coding: utf-8 -*-
"""model_gateway tools 透传契约测试（Agent 化前置 Phase 1，2026-11-23）。

不真调供应商：mock httpx.AsyncClient，断言三件事——
  1. tools/tool_choice 正确拼进请求 payload（网关只透传、不改写）
  2. 不传 tools 时 payload 与旧版完全一致（向后兼容，现有调用零影响）
  3. 流式 tool_calls 增量正确累积成整块（OpenAI 兼容分片协议）

导入放在方法内部，避免 test_form_doc_flow.py 在模块级 del sys.modules
重新加载 app.* 时导致本文件中的旧引用失效（与 test_extract_endpoint.py 同款防御）。
"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings


def _fake_config():
    """构造一个可用配置（绕开环境变量依赖）。"""
    from app.services.model_config_service import ModelConfig
    config = ModelConfig("llm")
    config.provider = "测试供应商"
    config.api_key = "test-key"
    config.base_url = "https://llm.example.com/v1"
    config.model_id = "test-model"
    return config


def _sse_lines(*chunks) -> list[str]:
    return ["data:" + json.dumps(chunk, ensure_ascii=False) for chunk in chunks] + [
        "data:[DONE]",
    ]


class ChatCompletionToolsContract(unittest.IsolatedAsyncioTestCase):
    """非流式 chat_completion 的 tools 透传契约。"""

    async def test_tools_are_passed_through_untouched(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "query_regulation",
                "description": "检索规范条文",
                "parameters": {"type": "object", "properties": {
                    "keyword": {"type": "string"},
                }, "required": ["keyword"]},
            },
        }]
        captured: dict = {}

        def fake_post(url, *, headers=None, json_body=None, **kwargs):
            captured["url"] = url
            captured["payload"] = json_body
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
            }
            return AsyncMock(return_value=response)()

        # httpx.AsyncClient.post(url=..., headers=..., json=...) —— json 参数名是 json
        client = MagicMock()
        client.post = lambda url, **kw: fake_post(url, json_body=kw.get("json"), **kw)

        from app.services import model_gateway

        with patch.object(
            model_gateway, "get_runtime_model_config",
            new=AsyncMock(return_value=_fake_config()),
        ):
            content, config = await model_gateway.chat_completion(
                [{"role": "user", "content": "查规范"}],
                tools=tools,
                tool_choice="auto",
                client=client,
            )
        self.assertEqual(content, "ok")
        self.assertEqual(config.model_id, "test-model")
        # 🔴 契约核心：tools 原样透传，网关不增不删不改
        self.assertEqual(captured["payload"]["tools"], tools)
        self.assertEqual(captured["payload"]["tool_choice"], "auto")
        # 供应商端点拼接正确
        self.assertEqual(captured["url"], "https://llm.example.com/v1/chat/completions")

    async def test_payload_unchanged_without_tools(self):
        """不传 tools 时 payload 与旧版完全一致（回归保障）。"""
        captured: dict = {}

        def fake_post(url, **kw):
            captured["payload"] = kw.get("json")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "hi"}}],
            }
            return AsyncMock(return_value=response)()

        client = MagicMock()
        client.post = fake_post

        from app.services import model_gateway

        with patch.object(
            model_gateway, "get_runtime_model_config",
            new=AsyncMock(return_value=_fake_config()),
        ):
            await model_gateway.chat_completion(
                [{"role": "user", "content": "你好"}], client=client,
            )
        payload = captured["payload"]
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["stream"], False)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "你好"}])


class StreamToolCallsContract(unittest.IsolatedAsyncioTestCase):
    """流式 stream_chat_completion 的 tool_calls 增量累积契约。"""

    async def test_tool_call_fragments_are_merged(self):
        """OpenAI 兼容流把一次工具调用拆多个 delta：name 先到、arguments 分片。"""
        # 模拟 httpx stream 响应：aiter_lines 逐行吐 SSE
        response_ctx = MagicMock()
        response_ctx.__aenter__ = AsyncMock(return_value=None)
        response_ctx.__aexit__ = AsyncMock(return_value=False)
        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.aiter_lines = AsyncMock(return_value=AsyncMock(
            __aiter__=lambda self: self,
            __anext__=MagicMock(side_effect=StopAsyncIteration),
        ))
        # 上面这种 aiter_lines mock 太绕，直接换成 list iterator
        lines = _sse_lines(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "type": "function",
                 "function": {"name": "query_regulation", "arguments": ""}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"key'}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": 'word": "临边"}'}},
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        )

        async def aiter_lines():
            for line in lines:
                yield line

        stream_response.aiter_lines = aiter_lines

        class _StreamCtx:
            async def __aenter__(self):
                return stream_response

            async def __aexit__(self, *exc):
                return False

        client = MagicMock()
        client.stream = MagicMock(return_value=_StreamCtx())

        from app.services import model_gateway

        with patch.object(
            model_gateway, "get_runtime_model_config",
            new=AsyncMock(return_value=_fake_config()),
        ):
            events = []
            async for event in model_gateway.stream_chat_completion(
                [{"role": "user", "content": "查临边规范"}],
                tools=[{"type": "function", "function": {
                    "name": "query_regulation", "parameters": {},
                }}],
                client=client,
            ):
                events.append(event)

        types = [event["type"] for event in events]
        self.assertIn("config", types)
        self.assertIn("tool_calls", types)
        tool_event = next(e for e in events if e["type"] == "tool_calls")
        self.assertEqual(len(tool_event["tool_calls"]), 1)
        call = tool_event["tool_calls"][0]
        # 分片拼接完整：id/name/arguments 逐项还原
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["function"]["name"], "query_regulation")
        self.assertEqual(call["function"]["arguments"], '{"keyword": "临边"}')

    async def test_plain_content_stream_unaffected(self):
        """没有 tool_calls 的普通流不受影响（回归保障：reasoning/content 原样）。"""
        lines = _sse_lines(
            {"choices": [{"delta": {"reasoning_content": "思考中"}}]},
            {"choices": [{"delta": {"content": "回复正文"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        )

        async def aiter_lines():
            for line in lines:
                yield line

        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.aiter_lines = aiter_lines

        class _StreamCtx:
            async def __aenter__(self):
                return stream_response

            async def __aexit__(self, *exc):
                return False

        client = MagicMock()
        client.stream = MagicMock(return_value=_StreamCtx())

        from app.services import model_gateway

        with patch.object(
            model_gateway, "get_runtime_model_config",
            new=AsyncMock(return_value=_fake_config()),
        ):
            events = []
            async for event in model_gateway.stream_chat_completion(
                [{"role": "user", "content": "你好"}], client=client,
            ):
                events.append(event)

        types = [event["type"] for event in events]
        self.assertIn("reasoning", types)
        self.assertIn("content", types)
        self.assertNotIn("tool_calls", types)
        content = "".join(
            e["text"] for e in events if e["type"] == "content")
        self.assertEqual(content, "回复正文")


if __name__ == "__main__":
    unittest.main()
