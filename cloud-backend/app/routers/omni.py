"""语音交互兼容路由。"""
import logging
import os
import random
import traceback
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from typing import Optional

from app.routers.auth import require_permission
from app.config import settings
from app.services import model_gateway
from app.services.media_validation import assert_valid_audio
from app.services.model_config_service import get_runtime_model_config

logger = logging.getLogger(__name__)
router = APIRouter(tags=["语音交互兼容接口"])


# ============ 模拟语音识别 ============
async def mock_transcribe(content: bytes, filename: str = "audio.webm", language: str = "zh") -> dict:
    """模拟语音识别"""
    mock_texts = [
        "巡视发现施工现场安全措施到位，人员规范作业",
        "验收合格，同意进入下一道工序",
        "发现一处安全隐患，已通知施工方整改",
        "今日巡视完成，施工进度正常",
        "质量检查合格，材料符合规范要求",
        "钢筋绑扎符合规范要求，同意隐蔽",
        "混凝土浇筑完成，养护措施已落实",
        "施工现场秩序良好，所有人员已按要求佩戴安全帽",
    ]
    return {
        "text": random.choice(mock_texts),
        "duration": 2.0 + len(content) / 20000,
        "language": language
    }


@router.get("/status")
async def get_omni_status(user=Depends(require_permission("local_ai"))):
    stt = await get_runtime_model_config("stt")
    llm = await get_runtime_model_config("llm")
    ready = all(
        item.enabled and item.base_url and item.model_id and item.api_key
        for item in (stt, llm)
    )
    return {
        "status": "ready" if ready else "not_configured",
        "message": "云端 STT/LLM 已配置" if ready else "请在系统设置中完善 STT/LLM",
        "stt_model": stt.model_id,
        "llm_model": llm.model_id,
    }


@router.post("/load")
async def load_omni_model(user=Depends(require_permission("local_ai"))):
    status = await get_omni_status(user)
    return {"message": "云模型无需本地加载", "status": status}


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = "zh",
    user=Depends(require_permission("local_ai")),
):
    """语音识别"""
    try:
        suffix = os.path.splitext(file.filename or "audio.webm")[1].lower() or ".webm"
        if suffix not in {".mp3", ".m4a", ".aac", ".wav", ".webm"}:
            raise HTTPException(status_code=400, detail="不支持的音频文件类型")
        content = await file.read(settings.max_audio_bytes + 1)
        await file.close()
        if not content:
            raise HTTPException(status_code=400, detail="音频文件为空")
        if len(content) > settings.max_audio_bytes:
            raise HTTPException(status_code=413, detail="音频超过上传大小限制")
        assert_valid_audio(content, suffix)
        
        # ============ 检查是否启用模拟模式 ============
        if os.getenv("STT_MOCK_MODE", "false").lower() == "true":
            return await mock_transcribe(content, file.filename or f"audio{suffix}", language)
        
        # ============ 调用火山引擎 STT ============
        try:
            return await model_gateway.transcribe_audio(
                content,
                filename=file.filename or f"audio{suffix}",
                content_type=file.content_type or "application/octet-stream",
                language=language,
            )
        except Exception as e:
            logger.error("火山引擎 STT 调用失败: %s", str(e))
            logger.error("完整堆栈: %s", traceback.format_exc())
            raise HTTPException(status_code=503, detail=f"语音识别服务调用失败: {str(e)}")
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("语音识别失败: %s", type(exc).__name__)
        logger.error("完整堆栈: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="语音识别服务异常") from exc


@router.post("/stt")
async def stt_compat(
    file: UploadFile = File(...),
    user=Depends(require_permission("local_ai")),
):
    """兼容前端 /stt 调用"""
    return await transcribe(file, user=user)


@router.post("/voice-chat")
async def voice_chat(
    file: UploadFile = File(...),
    context: Optional[str] = None,
    user=Depends(require_permission("local_ai")),
):
    try:
        suffix = os.path.splitext(file.filename or "audio.webm")[1].lower() or ".webm"
        if suffix not in {".mp3", ".m4a", ".aac", ".wav", ".webm"}:
            raise HTTPException(status_code=400, detail="不支持的音频文件类型")
        content = await file.read(settings.max_audio_bytes + 1)
        await file.close()
        if not content:
            raise HTTPException(status_code=400, detail="音频文件为空")
        if len(content) > settings.max_audio_bytes:
            raise HTTPException(status_code=413, detail="音频超过上传大小限制")
        assert_valid_audio(content, suffix)
        
        # 使用模拟语音识别
        if os.getenv("STT_MOCK_MODE", "false").lower() == "true":
            transcription = await mock_transcribe(
                content,
                filename=file.filename or f"audio{suffix}",
                language="zh"
            )
        else:
            transcription = await model_gateway.transcribe_audio(
                content,
                filename=file.filename or f"audio{suffix}",
                content_type=file.content_type or "application/octet-stream",
                language="zh",
            )
        
        system_prompt = (
            "你是监理现场智能助手。请根据用户的语音转写内容给出简洁、准确的回复。"
        )
        if context:
            system_prompt += f"\n当前上下文：{context}"
        answer, config = await model_gateway.chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcription["text"]},
            ],
            temperature=0.3,
            max_tokens=512,
        )
        return {
            "text": answer,
            "transcript": transcription["text"],
            "understood": bool(answer),
            "duration": transcription.get("duration", 0),
            "model": config.model_id,
            "provider": config.provider,
        }
    except HTTPException:
        raise
    except model_gateway.ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("语音交互失败: %s", type(exc).__name__)
        logger.error("完整堆栈: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail="语音交互服务异常") from exc


@router.post("/tts")
async def tts_compat():
    raise HTTPException(status_code=501, detail="语音合成功能开发中")
