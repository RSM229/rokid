# -*- coding: utf-8 -*-
"""前端旧模型名归一测试（本地 DeepSeek 网关只放行 DEFAULT_LLM_MODEL_ID）。

覆盖三件事：
  1. 已知失效名（小程序 modelCatalog 硬编码）归一到运行时默认模型；
  2. 未知/空模型名原样透传，不影响未来新增的合法模型；
  3. 非流式/流式两条调用链都发出归一后的 model，且华工独立通道不受影响。

导入放在方法内部，沿 test_model_gateway_tools.py 的同款防御。
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings


def _fake_config(model_id="test-model"):
    from app.services.model_config_service import ModelConfig
    config = ModelConfig("llm")
    config.provider = "测试供应商"
    config.api_key = "test-key"
    config.base_url = "https://llm.example.com/v1"
    config.model_id = model_id
    return config


class NormalizeRequestedModelTest(unittest.TestCase):
    def test_known_legacy_names_map_to_default(self):
        from app.services.model_gateway import _normalize_requested_model
        for old in ("deepseek-v4.1-flash-1017", "qwen3.7-plus", "qwen3.8-flash"):
            with self.subTest(old=old):
                self.assertEqual(
                    _normalize_requested_model(old, "deepseek-v4.1-flash"),
                    "deepseek-v4.1-flash",
                )
        # 大小写/空白不应绕过归一
        self.assertEqual(
            _normalize_requested_model("  QWEN3.7-PLUS  ", "deepseek-v4.1-flash"),
            "deepseek-v4.1-flash",
        )

    def test_unknown_and_empty_names_unchanged(self):
        from app.services.model_gateway import _normalize_requested_model
        self.assertEqual(
            _normalize_requested_model("glm-5.3", "deepseek-v4.1-flash"), "glm-5.3")
        self.assertEqual(
            _normalize_requested_model("deepseek-v4.1-flash", "other"),
            "deepseek-v4.1-flash")
        self.assertIsNone(_normalize_requested_model(None, "deepseek-v4.1-flash"))
        self.assertEqual(_normalize_requested_model("", "deepseek-v4.1-flash"), "")


class ChatCompletionAliasTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_name_is_normalized_in_payload(self):
        captured: dict = {}

        def fake_post(url, **kw):
            captured["url"] = url
            captured["payload"] = kw.get("json")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
            }
            return AsyncMock(return_value=response)()

        client = MagicMock()
        client.post = fake_post

        from app.services import model_gateway
        with patch.object(
            model_gateway, "get_runtime_model_config",
            new=AsyncMock(return_value=_fake_config()),
        ):
            _, config = await model_gateway.chat_completion(
                [{"role": "user", "content": "你好"}],
                model="deepseek-v4.1-flash-1017",
                client=client,
            )
        self.assertEqual(captured["payload"]["model"], "test-model")
        self.assertEqual(config.model_id, "test-model")
        self.assertEqual(
            captured["url"], "https://llm.example.com/v1/chat/completions")

    async def test_huagong_override_keeps_its_own_model(self):
        """provider=huagong 时模型名来自华工配置，归一逻辑不得介入。"""
        captured: dict = {}

        def fake_post(url, **kw):
            captured["payload"] = kw.get("json")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
            }
            return AsyncMock(return_value=response)()

        client = MagicMock()
        client.post = fake_post

        from app.services import model_gateway
        with patch.object(
            model_gateway, "get_runtime_model_config",
            new=AsyncMock(return_value=_fake_config()),
        ), patch.multiple(
            settings,
            huagong_llm_api_key="hg-key",
            huagong_llm_base_url="https://hg.example.com/v1",
            huagong_llm_model_id="hg-model",
            huagong_llm_provider="华工",
        ):
            await model_gateway.chat_completion(
                [{"role": "user", "content": "你好"}],
                provider="huagong",
                model="qwen3.7-plus",
                client=client,
            )
        self.assertEqual(captured["payload"]["model"], "hg-model")


class StreamAliasTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_name_is_normalized_in_stream_payload(self):
        lines = [
            'data:{"choices":[{"delta":{"content":"ok"}}]}',
            'data:[DONE]',
        ]

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
                [{"role": "user", "content": "你好"}],
                model="qwen3.8-flash",
                client=client,
            ):
                events.append(event)

        config_event = next(e for e in events if e["type"] == "config")
        self.assertEqual(config_event["config"].model_id, "test-model")
        self.assertEqual(client.stream.call_args.kwargs["json"]["model"], "test-model")


if __name__ == "__main__":
    unittest.main()
