"""统一模型调用网关。

所有客户端只访问本后端；模型供应商密钥不会下发到 Web、眼镜或小程序。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import settings
from app.services.model_config_service import (
    ModelConfig,
    get_runtime_model_config,
)

logger = logging.getLogger(__name__)


class ModelGatewayError(RuntimeError):
    """不包含供应商响应正文及凭证的安全错误。"""


def _headers(config: ModelConfig) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _require_available(config: ModelConfig) -> None:
    label = (getattr(config, "model_type", None) or config.config_type).upper()
    if not config.enabled:
        raise ModelGatewayError(f"{label} 模型已停用")
    if not config.base_url or not config.model_id:
        raise ModelGatewayError(f"{label} 模型参数未完整配置")
    if not config.api_key:
        raise ModelGatewayError(f"{label} 模型 API Key 未配置")


# 小程序 modelCatalog 曾硬编码这些旧名，本地 DeepSeek 网关只放行
# DEFAULT_LLM_MODEL_ID（deepseek-v4.1-flash）；旧名直传会 400 model_not_allowed。
# 只归一这批已知失效名，其他模型名（含未来新增的合法名）原样透传。
_LEGACY_LLM_MODEL_ALIASES = frozenset({
    "deepseek-v4.1-flash-1017",
    "qwen3.7-plus",
    "qwen3.8-flash",
})


def _normalize_requested_model(model: str | None, default_model_id: str) -> str | None:
    """把前端旧模型名映射到运行时默认模型，未知名字保持不变。"""
    if not model or not model.strip():
        return model
    if model.strip().lower() in _LEGACY_LLM_MODEL_ALIASES:
        return default_model_id
    return model


def _provider_override(provider: str | None) -> ModelConfig | None:
    """按前端选择的模型供应商切换文本模型配置（2026-10-09）。

    - qwen / 千问：使用默认文本模型（DEFAULT_LLM_*），返回 None 表示不覆盖；
    - huagong / 华工：使用 HUAGONG_LLM_* ；未配置时返回 None → 自动回退默认模型，
      保证前端下拉永远可用，不因供应商缺配置而报错。
    """
    key = (provider or "").strip().lower()
    if key in ("huagong", "hg", "华工"):
        api_key = getattr(settings, "huagong_llm_api_key", "") or ""
        base_url = getattr(settings, "huagong_llm_base_url", "") or ""
        model_id = getattr(settings, "huagong_llm_model_id", "") or ""
        if api_key and base_url and model_id:
            config = ModelConfig("llm")
            config.provider = getattr(settings, "huagong_llm_provider", "") or "华工"
            config.api_key = api_key
            config.base_url = base_url
            config.model_id = model_id
            return config
    return None


_QUOTA_RESET_RE = re.compile(
    r"reset at\s+(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\s+UTC", re.I
)


def _quota_reset_beijing(message: str) -> str | None:
    """Extract the provider's quota reset instant and render it in Beijing time."""
    match = _QUOTA_RESET_RE.search(message or "")
    if not match:
        return None
    month, day, hour, minute, second = (int(part) for part in match.groups())
    now = datetime.now(timezone.utc)
    try:
        reset = datetime(now.year, month, day, hour, minute, second, tzinfo=timezone.utc)
        if reset < now - timedelta(days=180):
            reset = reset.replace(year=now.year + 1)
        return reset.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _provider_http_error(response: httpx.Response, subject: str = "文本模型") -> str:
    """Translate provider HTTP failures into bounded, actionable Chinese text.

    Never surface the provider body or credentials; only classify a small set of
    common errors and extract the quota reset timestamp when present.
    """
    status = int(response.status_code)
    try:
        body = response.json()
    except (ValueError, TypeError):
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}
    fallback = body if isinstance(body, dict) else {}
    code = str(error.get("code") or fallback.get("code") or "").lower()
    message = str(error.get("message") or fallback.get("message") or "")
    haystack = f"{code} {message}".lower()

    if status == 429 and (
        "insufficient_quota" in haystack
        or ("quota" in haystack and ("exhaust" in haystack or "insufficient" in haystack))
    ):
        reset = _quota_reset_beijing(message)
        reset_text = f"，预计 {reset}（北京时间）重置" if reset else ""
        return f"{subject}额度已用尽{reset_text}；请充值或切换模型供应商后重试。"
    if status == 429:
        return f"{subject}供应商限流（HTTP 429），请稍后重试。"
    if status in (401, 403):
        return f"{subject}鉴权失败（HTTP {status}），请检查模型密钥配置。"
    if status == 404:
        return f"{subject}或接口地址不存在（HTTP 404），请检查模型配置。"
    if status >= 500:
        return f"{subject}供应商暂时不可用（HTTP {status}），请稍后重试。"
    return f"{subject}服务调用失败（HTTP {status}）。"


def _join_api_path(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    suffix = "/" + path.lstrip("/")
    if base.endswith(suffix):
        return base
    return base + suffix


def _hazard_vlm_config() -> ModelConfig:
    """隐患识别专用 VLM 配置：强制走华工 hazard_vlm_detection 通道（2026-11-03）。

    与通用 vlm 配置（DEFAULT_VLM_*，可被环境变量覆盖）解耦。隐患识别是
    安全攸关功能，必须固定走华工——不允许环境变量把它悄悄换到别的供应商；
    配置缺失直接报错，绝不静默回退（避免隐患漏检）。
    """
    config = ModelConfig("vlm")
    config.provider = "华工"
    config.api_key = getattr(settings, "huagong_vlm_token", "") or ""
    config.base_url = getattr(settings, "huagong_vlm_url", "") or ""
    config.model_id = "hazard_vlm_detection"
    return config


def _is_aliyun(config: ModelConfig) -> bool:
    provider = config.provider.lower()
    host = (urlsplit(config.base_url).hostname or "").lower()
    return "阿里" in provider or "aliyun" in provider or "maas.aliyuncs.com" in host


def _aliyun_workspace_url(base_url: str, *, websocket: bool, path: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if websocket else "https"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def _validate_audio_download_url(
    audio_url: str, config: ModelConfig
) -> None:
    parsed = urlsplit(audio_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in ("https", "http") or not hostname or parsed.username or parsed.password:
        raise ModelGatewayError("语音合成服务返回了不安全的音频地址")
    configured_host = (urlsplit(config.base_url).hostname or "").lower()
    if _is_aliyun(config):
        if not (
            hostname == configured_host
            or hostname.endswith(".aliyuncs.com")
            or hostname.endswith(".alibabacloud.com")
        ):
            raise ModelGatewayError("语音合成服务返回了非阿里云音频地址")
    elif parsed.scheme != "https" or hostname != configured_host:
        raise ModelGatewayError("语音合成服务返回了跨域音频地址")


async def chat_completion(
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    response_format: dict | None = None,
    client: httpx.AsyncClient | None = None,
    provider: str | None = None,
    model: str | None = None,
    thinking: bool | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
) -> tuple[str, ModelConfig]:
    """非流式文本模型调用。

    tools/tool_choice（2026-11-23，Agent 化前置 Phase 1）：OpenAI 兼容的
    function calling 透传——仅拼接进 payload，网关不解析、不执行工具；
    工具目录合法性、参数校验与写边界全部留在调用方（服务端 agent 层）。
    供应商不支持时会按其自身错误返回，由 _provider_http_error 翻译，
    网关侧不做静默降级（调用方需要明确知道 tools 是否被接受）。
    """
    config = await get_runtime_model_config("llm")
    override = _provider_override(provider)
    if override is not None:
        config = override
    # 默认端点下，允许前端指定具体模型名（如 qwen3.7-max / glm-5.3）。
    # 仅当未走独立供应商（huagong）时生效，避免把模型名错发给华工端点。
    # 已知失效的旧名（小程序目录硬编码）归一到运行时默认模型。
    if override is None and model:
        config.model_id = _normalize_requested_model(model, config.model_id)
    _require_available(config)
    payload: dict = {
        "model": config.model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if response_format:
        payload["response_format"] = response_format
    if thinking is not None:
        # DashScope/百炼：true=开启深度思考（回答类任务更聪明），false=关闭
        payload["enable_thinking"] = bool(thinking)
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    try:
        response = await http_client.post(
            _join_api_path(config.base_url, "chat/completions"),
            headers={**_headers(config), "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code >= 400:
            raise ModelGatewayError(_provider_http_error(response))
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        content = (
            choices[0].get("message", {}).get("content")
            if isinstance(choices, list) and choices
            else None
        )
        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("文本模型返回格式错误")
        return content, config
    except ModelGatewayError:
        raise
    except httpx.TimeoutException as exc:
        logger.warning("LLM 供应商调用超时: %s", type(exc).__name__)
        raise ModelGatewayError("文本模型调用超时，请稍后重试。") from exc
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        logger.warning("LLM 供应商调用失败: %s", type(exc).__name__)
        raise ModelGatewayError("文本模型网络调用失败，请稍后重试。") from exc
    finally:
        if owns_client:
            await http_client.aclose()


async def stream_chat_completion(
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: dict | None = None,
    client: httpx.AsyncClient | None = None,
    provider: str | None = None,
    model: str | None = None,
    thinking: bool | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
):
    """流式文本模型调用（SSE）。

    逐块产出事件，调用方按 type 分流：

    - ``{"type": "config", "config": ModelConfig}``：仅首个事件，用于拿 model_id
    - ``{"type": "reasoning", "text": "..."}``：思考过程增量（思考模式模型才有）
    - ``{"type": "content", "text": "..."}``：正文增量
    - ``{"type": "tool_calls", "tool_calls": [...]}``：工具调用请求增量（2026-11-23
      新增；供应商返回 finish_reason=tool_calls 时，把增量拼到当前 tool_call 上，
      结束时整段吐出。网关只透传不解析语义。）

    供应商没有 reasoning_content 字段时只会收到 content，调用方无需区分。
    tools/tool_choice 透传语义同 chat_completion：网关不校验工具目录、
    不执行任何工具，写边界留在服务端调用方。
    """
    config = await get_runtime_model_config("llm")
    override = _provider_override(provider)
    if override is not None:
        config = override
    if override is None and model:
        config.model_id = _normalize_requested_model(model, config.model_id)
    _require_available(config)
    payload: dict = {
        "model": config.model_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if response_format:
        payload["response_format"] = response_format
    if thinking is not None:
        # DashScope/百炼：false=关闭思考（路由这类结构化任务更快、也不泄漏规划）
        payload["enable_thinking"] = bool(thinking)
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    try:
        yield {"type": "config", "config": config}
        async with http_client.stream(
            "POST",
            _join_api_path(config.base_url, "chat/completions"),
            headers={**_headers(config), "Content-Type": "application/json"},
            json=payload,
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise ModelGatewayError(_provider_http_error(response))
            # tool_calls 增量累积：OpenAI 兼容流把一次工具调用拆成多个 delta
            # （index/id/function.name 先到，function.arguments 分片到齐）。
            # 网关只负责拼完整块再吐出，不解释 arguments（JSON 解析留给调用方）。
            tool_call_fragments: dict[int, dict] = {}
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield {"type": "content", "text": content}
                deltas = delta.get("tool_calls")
                if isinstance(deltas, list):
                    for piece in deltas:
                        if not isinstance(piece, dict):
                            continue
                        idx = int(piece.get("index") or 0)
                        slot = tool_call_fragments.setdefault(idx, {
                            "id": "", "type": "function", "function": {
                                "name": "", "arguments": "",
                            },
                        })
                        if piece.get("id"):
                            slot["id"] = str(piece["id"])
                        fn = piece.get("function") or {}
                        if isinstance(fn, dict):
                            if fn.get("name"):
                                slot["function"]["name"] += str(fn["name"])
                            args = fn.get("arguments")
                            if isinstance(args, str):
                                slot["function"]["arguments"] += args
                finish_reason = choices[0].get("finish_reason")
                if finish_reason == "tool_calls" and tool_call_fragments:
                    merged = [tool_call_fragments[k] for k in sorted(tool_call_fragments)]
                    yield {"type": "tool_calls", "tool_calls": merged}
                    tool_call_fragments = {}
    except ModelGatewayError:
        raise
    except httpx.TimeoutException as exc:
        logger.warning("LLM 流式调用超时: %s", type(exc).__name__)
        raise ModelGatewayError("文本模型流式调用超时，请稍后重试。") from exc
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        logger.warning("LLM 流式调用失败: %s", type(exc).__name__)
        raise ModelGatewayError("文本模型流式调用失败，请稍后重试。") from exc
    finally:
        if owns_client:
            await http_client.aclose()


async def observe_image(
    image_bytes: bytes,
    *,
    content_type: str = "image/jpeg",
    prompt: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Use the configured general-purpose multimodal LLM for neutral observation."""
    config = await get_runtime_model_config("llm")
    _require_available(config)
    mime = content_type if content_type.startswith("image/") else "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    instruction = prompt or (
        "Describe only visible, relevant construction-site facts in this image. "
        "Identify the scene/activity and visible location clues. Do not infer a "
        "hazard or make a safety judgment unless explicitly asked. Distinguish "
        "what is clearly visible from uncertainty, and do not repeat watermark "
        "text as if it were a visual observation. Reply in concise Chinese."
    )
    payload = {
        "model": config.model_id,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ]}],
        "temperature": 0.2,
        "max_tokens": 512,
        "stream": False,
    }
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0))
    try:
        response = await http_client.post(
            _join_api_path(config.base_url, "chat/completions"),
            headers={**_headers(config), "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code >= 400:
            raise ModelGatewayError(
                _provider_http_error(response, "图片理解模型")
            )
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        content = choices[0].get("message", {}).get("content") if choices else None
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("多模态模型返回格式错误")
        return content.strip()
    except ModelGatewayError:
        raise
    except httpx.TimeoutException as exc:
        logger.warning("图片理解模型调用超时: %s", type(exc).__name__)
        raise ModelGatewayError("图片理解模型调用超时，请稍后重试。") from exc
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        logger.warning("通用图片理解调用失败: %s", type(exc).__name__)
        raise ModelGatewayError("图片理解模型网络调用失败，请稍后重试。") from exc
    finally:
        if owns_client:
            await http_client.aclose()


async def detect_hazard(
    image_bytes: bytes,
    *,
    filename: str,
    prompt: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict:
    config = _hazard_vlm_config()
    _require_available(config)
    options = config.options
    parameters = {"prompt": prompt} if prompt else {}
    data = {
        "org_id": str(options.get("org_id") or ""),
        "environment": str(options.get("environment") or "test"),
        "parameters": json.dumps(parameters, ensure_ascii=False),
        "input_name": str(options.get("input_name") or "image"),
    }
    files = {"file": (filename, image_bytes, "application/octet-stream")}
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0))
    try:
        response = await http_client.post(
            config.base_url,
            headers=_headers(config),
            data=data,
            files=files,
        )
        if response.status_code >= 400:
            raise ModelGatewayError(
                _provider_http_error(response, "视觉检测模型")
            )
        body = response.json()
        if not isinstance(body, dict):
            raise ModelGatewayError("视觉模型返回格式错误")
        return body
    except ModelGatewayError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("VLM 供应商调用失败: %s", type(exc).__name__)
        raise ModelGatewayError("视觉模型服务调用失败") from exc
    finally:
        if owns_client:
            await http_client.aclose()


async def synthesize_speech(
    text: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str, ModelConfig]:
    config = await get_runtime_model_config("tts")
    _require_available(config)
    voice = str(config.options.get("voice") or "").strip()
    if config.model_id.startswith("cosyvoice-v3.5") and not voice:
        raise ModelGatewayError(
            "CosyVoice 3.5 必须先在系统设置中填写已创建的克隆/设计音色 ID"
        )

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    try:
        if _is_aliyun(config):
            url = _aliyun_workspace_url(
                config.base_url,
                websocket=False,
                path="/api/v1/services/audio/tts/SpeechSynthesizer",
            )
            input_data: dict = {
                "text": text,
                "format": "mp3",
                "sample_rate": 24000,
            }
            if voice:
                input_data["voice"] = voice
            instruction = str(config.options.get("instruction") or "").strip()
            if instruction:
                input_data["instruction"] = instruction
            response = await http_client.post(
                url,
                headers={**_headers(config), "Content-Type": "application/json"},
                json={"model": config.model_id, "input": input_data},
            )
        else:
            payload: dict = {
                "model": config.model_id,
                "input": text,
                "response_format": "mp3",
            }
            if voice:
                payload["voice"] = voice
            response = await http_client.post(
                _join_api_path(config.base_url, "audio/speech"),
                headers={**_headers(config), "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise ModelGatewayError(
                _provider_http_error(response, "语音合成模型")
            )

        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            body = response.json()
            audio_url = (
                body.get("output", {}).get("audio", {}).get("url")
                if isinstance(body, dict)
                else None
            )
            audio_url = audio_url or (
                body.get("output", {}).get("url") if isinstance(body, dict) else None
            )
            audio_url = audio_url or (body.get("url") if isinstance(body, dict) else None)
            if not isinstance(audio_url, str) or not audio_url.startswith(
                ("http://", "https://")
            ):
                raise ModelGatewayError("语音合成服务未返回有效音频")
            _validate_audio_download_url(audio_url, config)
            parsed_url = urlsplit(audio_url)
            if parsed_url.scheme == "http" and _is_aliyun(config):
                audio_url = urlunsplit(("https", parsed_url.netloc, parsed_url.path, parsed_url.query, parsed_url.fragment))
            chunks: list[bytes] = []
            total = 0
            async with http_client.stream("GET", audio_url) as download:
                download.raise_for_status()
                content_length = int(download.headers.get("content-length") or 0)
                if content_length > 20 * 1024 * 1024:
                    raise ModelGatewayError("语音合成服务返回的音频超过大小限制")
                audio_type = download.headers.get("content-type", "audio/mpeg")
                async for chunk in download.aiter_bytes():
                    total += len(chunk)
                    if total > 20 * 1024 * 1024:
                        raise ModelGatewayError("语音合成服务返回的音频超过大小限制")
                    chunks.append(chunk)
            audio = b"".join(chunks)
        else:
            audio = response.content
            audio_type = content_type or "audio/mpeg"
        if not audio or len(audio) > 20 * 1024 * 1024:
            raise ModelGatewayError("语音合成服务返回了无效音频")
        return audio, audio_type, config
    except ModelGatewayError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        logger.warning("TTS 供应商调用失败: %s", type(exc).__name__)
        raise ModelGatewayError("语音合成服务调用失败") from exc
    finally:
        if owns_client:
            await http_client.aclose()


async def _convert_audio_to_wav(audio: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ModelGatewayError("服务器未安装 ffmpeg，无法转换语音识别音频")
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate(audio)
    if process.returncode != 0 or not stdout:
        raise ModelGatewayError("语音文件无法转换为识别服务支持的格式")
    return stdout


async def _aliyun_realtime_transcribe(
    config: ModelConfig,
    audio_bytes: bytes,
    *,
    language: str,
) -> dict:
    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise ModelGatewayError("服务器缺少 WebSocket 语音识别依赖") from exc

    wav = await _convert_audio_to_wav(audio_bytes)
    endpoint = _aliyun_workspace_url(
        config.base_url, websocket=True, path="/api-ws/v1/inference"
    )
    task_id = uuid.uuid4().hex
    run_task = {
        "header": {
            "action": "run-task",
            "task_id": task_id,
            "streaming": "duplex",
        },
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": config.model_id,
            "parameters": {
                "format": "wav",
                "sample_rate": 16000,
                "language_hints": [language],
                "punctuation_prediction_enabled": True,
            },
            "input": {},
        },
    }
    finish_task = {
        "header": {
            "action": "finish-task",
            "task_id": task_id,
            "streaming": "duplex",
        },
        "payload": {"input": {}},
    }
    final_sentences: list[str] = []
    duration = 0
    try:
        async with connect(
            endpoint,
            additional_headers={
                "Authorization": f"Bearer {config.api_key}",
                "User-Agent": "gdjl-cloud-backend/1.0",
            },
            open_timeout=15,
            close_timeout=5,
            max_size=4 * 1024 * 1024,
        ) as websocket:
            await websocket.send(json.dumps(run_task, ensure_ascii=False))
            first = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if first.get("header", {}).get("event") != "task-started":
                raise ModelGatewayError("语音识别任务启动失败")
            for offset in range(0, len(wav), 3200):
                await websocket.send(wav[offset : offset + 3200])
                await asyncio.sleep(0)
            await websocket.send(json.dumps(finish_task))
            while True:
                raw = await asyncio.wait_for(websocket.recv(), timeout=30)
                if not isinstance(raw, str):
                    continue
                event = json.loads(raw)
                header = event.get("header", {})
                event_name = header.get("event")
                if event_name == "task-failed":
                    raise ModelGatewayError("语音识别任务执行失败")
                if event_name == "result-generated":
                    payload = event.get("payload", {})
                    sentence = payload.get("output", {}).get("sentence", {})
                    if sentence.get("sentence_end") and sentence.get("text"):
                        final_sentences.append(str(sentence["text"]))
                    usage = payload.get("usage") or {}
                    duration = max(duration, int(usage.get("duration") or 0))
                if event_name == "task-finished":
                    break
    except ModelGatewayError:
        raise
    except Exception as exc:
        logger.warning("阿里云实时 ASR 调用失败: %s", type(exc).__name__)
        raise ModelGatewayError("语音识别服务调用失败") from exc
    return {
        "text": "".join(final_sentences).strip(),
        "language": language,
        "language_probability": None,
        "duration": duration,
        "model": config.model_id,
        "provider": config.provider,
    }


async def _aliyun_multimodal_transcribe(
    config: ModelConfig,
    audio_bytes: bytes,
    *,
    language: str,
) -> dict:
    wav = await _convert_audio_to_wav(audio_bytes)
    data_uri = "data:audio/wav;base64," + base64.b64encode(wav).decode()
    url = _aliyun_workspace_url(
        config.base_url,
        websocket=False,
        path="/api/v1/services/aigc/multimodal-generation/generation",
    )
    payload = {
        "model": config.model_id,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": data_uri}}
                    ],
                }
            ]
        },
        "parameters": {"format": "wav", "sample_rate": "16000"},
    }
    headers = {**_headers(config), "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as http_client:
        try:
            response = await http_client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("阿里云多模态 ASR 请求失败: %s", type(exc).__name__)
            raise ModelGatewayError("语音识别服务调用失败") from exc
        if response.status_code == 400:
            # 百炼对无有效语音的音频（静音/纯音调/过短）返回 400 且响应体为空 {}；
            # 配置类错误可区分（401 InvalidApiKey、404 Model not exist），
            # 因此这里按"未识别到语音内容"处理，返回空文本而非服务不可用。
            logger.info(
                "阿里云多模态 ASR 未识别到语音内容 (request_id=%s)",
                response.headers.get("x-request-id"),
            )
            return {
                "text": "",
                "language": language,
                "language_probability": None,
                "duration": 0,
                "model": config.model_id,
                "provider": config.provider,
            }
        if response.status_code >= 400:
            logger.warning(
                "阿里云多模态 ASR 调用失败: HTTP %s (request_id=%s)",
                response.status_code,
                response.headers.get("x-request-id"),
            )
            raise ModelGatewayError("语音识别服务调用失败")
        try:
            body = response.json()
        except ValueError as exc:
            raise ModelGatewayError("语音识别服务返回格式错误") from exc
    if not isinstance(body, dict):
        raise ModelGatewayError("语音识别服务返回格式错误")
    text = body.get("text") if isinstance(body, dict) else None
    output = body.get("output") if isinstance(body, dict) else None
    if not isinstance(text, str) and isinstance(output, dict):
        text = output.get("text")
    if not isinstance(text, str):
        text = ""
    usage = body.get("usage") if isinstance(body, dict) else None
    duration = usage.get("duration", 0) if isinstance(usage, dict) else 0
    return {
        "text": text.strip(),
        "language": language,
        "language_probability": None,
        "duration": duration,
        "model": config.model_id,
        "provider": config.provider,
    }


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    language: str = "zh",
    client: httpx.AsyncClient | None = None,
) -> dict:
    config = await get_runtime_model_config("stt")
    _require_available(config)
    if _is_aliyun(config) and "realtime" in config.model_id.lower():
        return await _aliyun_realtime_transcribe(
            config, audio_bytes, language=language
        )
    if _is_aliyun(config) and "asr-flash" in config.model_id.lower():
        return await _aliyun_multimodal_transcribe(
            config, audio_bytes, language=language
        )

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))
    try:
        response = await http_client.post(
            _join_api_path(config.base_url, "audio/transcriptions"),
            headers=_headers(config),
            data={"model": config.model_id, "language": language},
            files={"file": (Path(filename).name, audio_bytes, content_type)},
        )
        if response.status_code >= 400:
            raise ModelGatewayError(
                _provider_http_error(response, "语音识别模型")
            )
        body = response.json()
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise ModelGatewayError("语音识别服务返回格式错误")
        return {
            "text": text,
            "language": body.get("language", language),
            "language_probability": body.get("language_probability"),
            "duration": body.get("duration", 0),
            "model": config.model_id,
            "provider": config.provider,
        }
    except ModelGatewayError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("STT 供应商调用失败: %s", type(exc).__name__)
        raise ModelGatewayError("语音识别服务调用失败") from exc
    finally:
        if owns_client:
            await http_client.aclose()
