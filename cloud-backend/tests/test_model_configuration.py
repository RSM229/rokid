from __future__ import annotations

import json
import unittest
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx

from app.routers.model_settings import ModelConfigUpdate
from app.services.model_gateway import (
    ModelGatewayError,
    _validate_audio_download_url,
    _aliyun_workspace_url,
)
from app.services.model_config_service import (
    ModelConfig,
    encrypt_api_key,
    decrypt_api_key,
    serialize_model_config,
)
from app.models.model_config import ModelConfiguration


@dataclass
class FakeRuntimeConfig:
    """测试用轻量替代品（原 RuntimeModelConfig 已废弃）。"""
    model_type: str
    provider: str
    base_url: str
    model_id: str
    api_key: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.model_id)


class ModelConfigurationSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_key = getattr(self.__class__, "_orig_key", None)
        from app.config import settings
        self._orig_key = settings.model_config_encryption_key
        settings.model_config_encryption_key = "unit-test-model-encryption-key-that-is-long-enough"

    def tearDown(self) -> None:
        from app.config import settings
        settings.model_config_encryption_key = self._orig_key

    def test_api_key_encrypted_and_serialize_masked(self) -> None:
        raw_key = "test-provider-secret"
        ciphertext = encrypt_api_key(raw_key)
        self.assertNotIn(raw_key, ciphertext)
        self.assertEqual(decrypt_api_key(ciphertext), raw_key)

        row = ModelConfiguration(
            model_type="llm",
            provider="测试云",
            base_url="https://example.com/v1",
            model_id="test-model",
            api_key_encrypted=ciphertext,
            enabled=True,
            config_json={"token": "must-not-leak", "safe": "value"},
            updated_at=datetime(2026, 7, 28, 12, 0, 0),
        )
        response = serialize_model_config(row)
        self.assertEqual(response["api_key"], "**masked**")
        self.assertTrue(response["enabled"])
        self.assertNotIn("api_key_encrypted", response)
        self.assertNotIn("token", response["options"])
        self.assertEqual(response["options"], {"safe": "value"})
        self.assertNotIn(raw_key, repr(response))

    def test_validate_audio_download_url_security(self) -> None:
        safe_config = FakeRuntimeConfig(
            "tts", "通用", "https://models.example.com/v1", "model", "k"
        )
        aliyun_config = FakeRuntimeConfig(
            "tts",
            "阿里云",
            "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "cosyvoice-v3.5-plus",
            "k",
        )

        _validate_audio_download_url(
            "https://models.example.com/audio.mp3", safe_config
        )

        with self.assertRaises(ModelGatewayError):
            _validate_audio_download_url("http://models.example.com/audio.mp3", safe_config)
        with self.assertRaises(ModelGatewayError):
            _validate_audio_download_url(
                "https://user:pass@models.example.com/audio.mp3", safe_config
            )
        with self.assertRaises(ModelGatewayError):
            _validate_audio_download_url(
                "https://other.com/audio.mp3", safe_config
            )

        _validate_audio_download_url(
            "https://workspace.cn-beijing.maas.aliyuncs.com/out.mp3", aliyun_config
        )
        _validate_audio_download_url(
            "https://my-bucket.oss-cn-beijing.aliyuncs.com/audio.mp3", aliyun_config
        )

        with self.assertRaises(ModelGatewayError):
            _validate_audio_download_url(
                "https://malicious.example.com/audio.mp3", aliyun_config
            )

    def test_rejects_secrets_in_options_validator(self) -> None:
        for key in ("api_key", "apikey", "token", "secret", "password"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    ModelConfigUpdate(
                        provider="test",
                        base_url="https://example.com/v1/",
                        model_id="m",
                        options={key: "value"},
                    )

        valid = ModelConfigUpdate(
            provider="test",
            base_url="https://example.com/v1/",
            model_id="m",
            options={"temperature": 0.7, "safe": "value"},
        )
        self.assertEqual(valid.options, {"temperature": 0.7, "safe": "value"})

    def test_model_config_loads_settings_defaults(self) -> None:
        """锁的是「ModelConfig 逐项镜像 settings」这个契约，不是具体厂商名。

        厂商 / 模型 ID 由部署环境 .env 决定（llm 就从天翼云切到了阿里云，
        model_id 也随之变化）。写死厂商名会让这条测试变成环境配置的镜子，
        配置一改就假红，反而掩盖真正的加载逻辑回归。
        """
        from app.config import settings

        expected = {
            "llm": (settings.default_llm_provider, settings.default_llm_model_id),
            "tts": (settings.default_tts_provider, settings.default_tts_model_id),
            "stt": (settings.default_stt_provider, settings.default_stt_model_id),
            "vlm": (settings.default_vlm_provider, settings.default_vlm_model_id),
        }
        self.assertTrue(
            any(provider for provider, _ in expected.values()),
            "环境里四类默认 provider 全为空，这条契约测试会退化成空对空",
        )
        for model_type, (provider, model_id) in expected.items():
            with self.subTest(model_type=model_type):
                config = ModelConfig(model_type)
                self.assertEqual(config.provider, provider)
                self.assertEqual(config.model_id, model_id)


class ModelGatewayContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_uses_openai_compatible_contract(self) -> None:
        config = FakeRuntimeConfig(
            model_type="llm",
            provider="测试云",
            base_url="https://models.example.com/v1",
            model_id="chat-model",
            api_key="private-key",
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url), "https://models.example.com/v1/chat/completions"
            )
            self.assertEqual(request.headers["authorization"], "Bearer private-key")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "chat-model")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "模型回复"}}]},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch(
                "app.services.model_gateway.get_runtime_model_config",
                new=AsyncMock(return_value=config),
            ):
                from app.services import model_gateway
                content, returned = await model_gateway.chat_completion(
                    [{"role": "user", "content": "你好"}],
                    client=client,
                )
        self.assertEqual(content, "模型回复")
        self.assertEqual(returned.model_id, "chat-model")

    async def test_llm_quota_exhaustion_has_actionable_message(self) -> None:
        config = FakeRuntimeConfig(
            model_type="llm",
            provider="测试云",
            base_url="https://models.example.com/v1",
            model_id="qwen3.7-max",
            api_key="private-key",
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": (
                            "Your token-plan 1-month quota has been exhausted. "
                            "The quota will reset at 11-08 16:00:00 UTC."
                        ),
                        "type": "insufficient_quota",
                        "code": "insufficient_quota",
                    }
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch(
                "app.services.model_gateway.get_runtime_model_config",
                new=AsyncMock(return_value=config),
            ):
                from app.services import model_gateway
                with self.assertRaises(ModelGatewayError) as raised:
                    await model_gateway.chat_completion(
                        [{"role": "user", "content": "你好"}],
                        client=client,
                    )

        message = str(raised.exception)
        self.assertIn("额度已用尽", message)
        self.assertIn("北京时间", message)
        self.assertNotIn("token-plan", message)
        self.assertNotIn("private-key", message)

    async def test_vision_quota_exhaustion_names_vision_model(self) -> None:
        config = FakeRuntimeConfig(
            model_type="llm",
            provider="测试云",
            base_url="https://models.example.com/v1",
            model_id="qwen3.7-max",
            api_key="private-key",
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "error": {
                        "message": (
                            "Your token-plan 1-month quota has been exhausted. "
                            "The quota will reset at 11-08 16:00:00 UTC."
                        ),
                        "code": "insufficient_quota",
                    }
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch(
                "app.services.model_gateway.get_runtime_model_config",
                new=AsyncMock(return_value=config),
            ):
                from app.services import model_gateway
                with self.assertRaises(ModelGatewayError) as raised:
                    await model_gateway.observe_image(
                        b"\xff\xd8fake-image", client=client
                    )

        message = str(raised.exception)
        self.assertIn("图片理解模型额度已用尽", message)
        self.assertNotIn("token-plan", message)
        self.assertNotIn("private-key", message)

    async def test_generic_tts_returns_binary_audio(self) -> None:
        config = FakeRuntimeConfig(
            model_type="tts",
            provider="兼容服务",
            base_url="https://voice.example.com/v1",
            model_id="speech-model",
            api_key="private-key",
            options={"voice": "voice-id"},
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url), "https://voice.example.com/v1/audio/speech"
            )
            return httpx.Response(
                200,
                content=b"fake-mp3",
                headers={"Content-Type": "audio/mpeg"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch(
                "app.services.model_gateway.get_runtime_model_config",
                new=AsyncMock(return_value=config),
            ):
                from app.services import model_gateway
                audio, content_type, _ = await model_gateway.synthesize_speech(
                    "测试播报", client=client
                )
        self.assertEqual(audio, b"fake-mp3")
        self.assertEqual(content_type, "audio/mpeg")

    async def test_generic_stt_uses_openai_compatible_contract(self) -> None:
        config = FakeRuntimeConfig(
            model_type="stt",
            provider="兼容服务",
            base_url="https://speech.example.com/v1",
            model_id="transcribe-model",
            api_key="private-key",
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url),
                "https://speech.example.com/v1/audio/transcriptions",
            )
            self.assertIn(b"transcribe-model", request.content)
            return httpx.Response(200, json={"text": "识别结果", "language": "zh"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch(
                "app.services.model_gateway.get_runtime_model_config",
                new=AsyncMock(return_value=config),
            ):
                from app.services import model_gateway
                result = await model_gateway.transcribe_audio(
                    b"audio",
                    filename="voice.wav",
                    content_type="audio/wav",
                    language="zh",
                    client=client,
                )
        self.assertEqual(result["text"], "识别结果")
        self.assertEqual(result["model"], "transcribe-model")

    async def test_aliyun_tts_uses_workspace_http_contract(self) -> None:
        config = FakeRuntimeConfig(
            model_type="tts",
            provider="阿里云",
            base_url=(
                "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            ),
            model_id="cosyvoice-v3.5-plus",
            api_key="private-key",
            options={"voice": "custom-voice"},
        )
        requested_urls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            if request.method == "POST":
                body = json.loads(request.content)
                self.assertEqual(body["model"], "cosyvoice-v3.5-plus")
                self.assertEqual(body["input"]["voice"], "custom-voice")
                return httpx.Response(
                    200,
                    json={
                        "output": {
                            "audio": {
                                "url": "https://result.oss-cn-beijing.aliyuncs.com/audio.mp3"
                            }
                        }
                    },
                )
            return httpx.Response(
                200,
                content=b"aliyun-audio",
                headers={"Content-Type": "audio/mpeg"},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with patch(
                "app.services.model_gateway.get_runtime_model_config",
                new=AsyncMock(return_value=config),
            ):
                from app.services import model_gateway
                audio, _, _ = await model_gateway.synthesize_speech(
                    "测试播报", client=client
                )
        self.assertEqual(audio, b"aliyun-audio")
        self.assertEqual(
            requested_urls[0],
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
        )

    async def test_aliyun_realtime_stt_protocol(self) -> None:
        config = FakeRuntimeConfig(
            model_type="stt",
            provider="阿里云",
            base_url=(
                "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            ),
            model_id="paraformer-realtime-v2",
            api_key="private-key",
        )

        class FakeWebSocket:
            def __init__(self) -> None:
                self.sent: list[str | bytes] = []
                self.responses = [
                    json.dumps({"header": {"event": "task-started"}}),
                    json.dumps(
                        {
                            "header": {"event": "result-generated"},
                            "payload": {
                                "output": {
                                    "sentence": {
                                        "text": "现场语音",
                                        "sentence_end": True,
                                    }
                                },
                                "usage": {"duration": 1},
                            },
                        }
                    ),
                    json.dumps({"header": {"event": "task-finished"}}),
                ]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def send(self, data):
                self.sent.append(data)

            async def recv(self):
                return self.responses.pop(0)

        fake_websocket = FakeWebSocket()
        endpoints: list[str] = []

        def fake_connect(endpoint, **kwargs):
            endpoints.append(endpoint)
            self.assertIn("Authorization", kwargs["additional_headers"])
            return fake_websocket

        with (
            patch(
                "websockets.asyncio.client.connect",
                side_effect=fake_connect,
            ),
            patch(
                "app.services.model_gateway._convert_audio_to_wav",
                new=AsyncMock(return_value=b"RIFF-wav-data"),
            ),
        ):
            from app.services import model_gateway
            result = await model_gateway._aliyun_realtime_transcribe(
                config, b"browser-webm", language="zh"
            )
        self.assertEqual(result["text"], "现场语音")
        self.assertEqual(result["duration"], 1)
        self.assertEqual(
            endpoints,
            ["wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"],
        )
        run_event = json.loads(fake_websocket.sent[0])
        finish_event = json.loads(fake_websocket.sent[-1])
        self.assertEqual(run_event["header"]["action"], "run-task")
        self.assertEqual(run_event["payload"]["model"], "paraformer-realtime-v2")
        self.assertEqual(finish_event["header"]["action"], "finish-task")
        self.assertTrue(any(isinstance(item, bytes) for item in fake_websocket.sent))

    def test_aliyun_workspace_endpoints_are_derived_without_exposing_key(self) -> None:
        base = (
            "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        )
        self.assertEqual(
            _aliyun_workspace_url(
                base,
                websocket=True,
                path="/api-ws/v1/inference",
            ),
            "wss://workspace.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
        )
        self.assertEqual(
            _aliyun_workspace_url(
                base,
                websocket=False,
                path="/api/v1/services/audio/tts/SpeechSynthesizer",
            ),
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
        )


if __name__ == "__main__":
    unittest.main()
