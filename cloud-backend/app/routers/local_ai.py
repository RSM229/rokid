"""兼容旧路径的统一 AI 服务。

路由路径保持 /api/local 不变，内部已切换为系统设置中的云模型配置。
"""
import os
import json
import logging
import re
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.config import settings
from app.models.user import User
from app.routers.auth import get_current_user
from app.services.media_validation import assert_valid_audio
from app.services import model_gateway
from app.services.model_config_service import get_runtime_model_config
from app.services.text_clean import strip_markdown

# 纯文本输出约束：聊天气泡不渲染 Markdown，禁止 #/* 等标记与多余空行
_PLAINTEXT_SUFFIX = (
    "\n\n输出格式要求：使用纯文本回答，禁止使用任何 Markdown 标记"
    "（如 #、*、-、表格、代码块等）；标题直接写文字；"
    "条目用「1.」「2.」或「•」编号；段落之间只用单个换行，不要空行。"
)

router = APIRouter()
logger = logging.getLogger(__name__)

# ===== STT 语音识别 =====

@router.post("/stt/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = "zh",
    user: User = Depends(get_current_user),
):
    """使用系统设置中的 STT 模型识别上传音频。"""
    try:
        # 读取上传的音频
        suffix = os.path.splitext(file.filename or "audio.webm")[1].lower() or ".webm"
        if suffix not in {".mp3", ".m4a", ".aac", ".wav", ".webm"}:
            raise HTTPException(status_code=400, detail="不支持的音频文件类型")
        audio_bytes = await file.read(settings.max_audio_bytes + 1)
        await file.close()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="音频文件为空")
        if len(audio_bytes) > settings.max_audio_bytes:
            raise HTTPException(status_code=413, detail="音频超过上传大小限制")
        assert_valid_audio(audio_bytes, suffix)

        logger.info("开始识别音频: %s, %s bytes", file.filename, len(audio_bytes))
        return await model_gateway.transcribe_audio(
            audio_bytes,
            filename=file.filename or f"audio{suffix}",
            content_type=file.content_type or "application/octet-stream",
            language=language,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("语音识别失败: %s", type(e).__name__)
        detail = str(e) if isinstance(e, model_gateway.ModelGatewayError) else "语音识别服务异常"
        raise HTTPException(status_code=503, detail=detail) from e


# ===== LLM 问答 =====


class LLMChatRequest(BaseModel):
    message: str
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    """模型供应商：留空走默认文本模型端点；huagong 走华南理工独立供应商。
    未配置对应供应商时自动回退到默认文本模型，调用不会失败。"""
    provider: Optional[str] = None
    """具体模型名（覆盖默认端点的 model 字段），如 qwen3.7-max / glm-5.3 / deepseek-v4-pro 等。
    留空则沿用系统默认模型。仅对默认端点生效，provider=huagong 时忽略。"""
    model: Optional[str] = None


class TTSRequest(BaseModel):
    text: str


@router.post("/llm/chat")
async def local_llm_chat(
    req: LLMChatRequest,
    user: User = Depends(get_current_user),
):
    """使用系统设置中的 OpenAI 兼容文本模型。"""
    try:
        messages = []
        # 兜底约束：无论前端传入什么 system_prompt，都附加纯文本输出要求
        messages.append({
            "role": "system",
            "content": (req.system_prompt or "你是建筑工程监理智能助手。") + _PLAINTEXT_SUFFIX,
        })
        messages.append({"role": "user", "content": req.message})
        answer, config = await model_gateway.chat_completion(
            messages, temperature=req.temperature, provider=req.provider, model=req.model
        )
        return {
            "answer": strip_markdown(answer),
            "model": config.model_id,
            "source": config.provider,
        }
    except model_gateway.ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/llm/status")
async def llm_status(user: User = Depends(get_current_user)):
    """返回文本模型配置状态，不进行计费调用，也不返回密钥。"""
    try:
        config = await get_runtime_model_config("llm")
        return {
            "configured": bool(
                config.enabled and config.base_url and config.model_id and config.api_key
            ),
            "enabled": config.enabled,
            "provider": config.provider,
            "current_model": config.model_id,
            "model_available": bool(config.enabled and config.api_key),
        }
    except Exception:
        return {"configured": False, "enabled": False, "model_available": False}


# ===== TTS 语音合成（可选外部服务）=====

@router.post("/tts/synthesize")
async def synthesize_speech(
    req: TTSRequest,
    user: User = Depends(get_current_user),
):
    """调用服务端配置的 TTS，将短期音频 URL 返回给小程序。

    TTS Token 只保留在服务端。未配置时返回 503，不影响文字问答。
    """
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="播报文本不能为空")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="单次播报文本不能超过 1000 字")
    try:
        audio, content_type, config = await model_gateway.synthesize_speech(text)
    except model_gateway.ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    directory = settings.upload_dir / "media" / "tts"
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".wav" if "wav" in content_type else ".mp3"
    filename = f"{user.id}_{uuid.uuid4().hex}{suffix}"
    (directory / filename).write_bytes(audio)
    return {
        "audio_url": f"/uploads/media/tts/{filename}",
        "model": config.model_id,
        "provider": config.provider,
    }


# ===== 语音指令智能匹配 =====

class VoiceCommandRequest(BaseModel):
    text: str
    module: str = "auto"
    context: Optional[str] = None  # 当前工程上下文信息


VOICE_MODULE_LABELS = {
    "inspection": "巡视记录",
    "acceptance": "验收记录",
    "station": "旁站记录",
    "witness": "见证记录",
    "quality": "质量问题",
    "other": "其他工作",
    "hazard": "安全巡检",
    "rectify": "整改跟踪",
}


def infer_voice_module(text: str) -> str | None:
    """从自然语言中确定交付业务，顺序优先处理语义更具体的场景。"""
    normalized = text.strip().lower()
    keyword_groups = (
        ("rectify", ("整改", "复查", "复验", "闭环", "销项")),
        ("station", ("旁站", "关键工序")),
        ("witness", ("见证", "取样", "送检")),
        ("acceptance", ("验收", "检验批", "隐蔽工程")),
        ("quality", ("质量问题", "质量缺陷", "不合格")),
        ("hazard", ("安全巡检", "安全检查", "安全隐患", "危险源")),
        ("inspection", ("巡视", "巡查", "现场检查")),
        ("other", ("其他工作", "会议", "发文", "联系单", "备忘")),
    )
    for module, keywords in keyword_groups:
        if any(keyword in normalized for keyword in keywords):
            return module
    return None


# 综合现场工作流程指引
WORKFLOW_GUIDE = {
    "inspection": (
        "巡视工作流程：\n"
        "1. 准备阶段：检查《安全监理危险源控制表》，明确当日巡视重点和危险源。\n"
        "2. 现场巡视：按《安全监理日志》《施工安全监理周报》要求，对施工现场进行巡视。\n"
        "   - 重点关注：危险性较大的分部分项工程（见《监理巡视检查记录表》）。\n"
        "   - 重点检查：施工起重机械安装拆卸、临时用电、高处作业、脚手架、基坑工程等。\n"
        "3. 隐患处置：发现安全隐患时，下发《安全隐患整改通知》给施工单位项目部。\n"
        "   - 施工单位整改后，应回复《安全隐患整改通知回复》。\n"
        "4. 重大情况：发现重大安全隐患，应立即报告（见《安全监理重大情况报告》）。\n"
        "5. 暂停施工：必要时下发《暂时停止施工通知》，整改合格后受理《复工申请》。\n"
        "6. 记录归档：填写《安全监理日志》《施工安全监理周报》，留存影像资料。"
    ),
    "acceptance": (
        "验收工作流程：\n"
        "1. 准备阶段：审查施工单位提交的施工技术资料、质量自评记录、检验试验报告。\n"
        "2. 资料核查：核对《材料台账》中的进场材料合格证、检验报告、复试报告。\n"
        "   - 钢筋、水泥、混凝土等关键材料必须有见证取样复试记录。\n"
        "3. 现场验收：依据《省统表》和设计文件，对工程部位进行实测实量、外观检查。\n"
        "   - 隐蔽工程验收：检查钢筋绑扎、模板支护、防水层等隐蔽部位。\n"
        "   - 检验批验收：按《监理审查表》逐项核对质量验收指标。\n"
        "4. 平行检查：按《平行检查记录》对关键工序进行独立检测复核。\n"
        "5. 旁站见证：关键工序实施旁站（见《旁站记录》），材料复试实施见证取样。\n"
        "6. 验收记录：验收合格后签署验收记录，不合格的下发《监理通知单》要求整改。\n"
        "7. 资料归档：将验收记录、检验报告、影像资料归入监理档案。"
    ),
    "witness": (
        "见证工作流程：\n"
        "1. 准备阶段：根据《监理细则》明确见证取样范围和频次（不少于 30%且均匀分布）。\n"
        "2. 见证取样：在施工单位现场取样时实施见证，确保样品真实性。\n"
        "   - 见证范围：钢筋、水泥、砂石、混凝土、砂浆、防水材料、回填土等关键材料。\n"
        "   - 见证标识：对样品加贴见证标识，记录取样部位、时间、数量。\n"
        "3. 送检登记：填写《材料台账》记录送检信息，送至有资质的检测机构。\n"
        "4. 结果跟踪：跟踪检测报告回收情况，核对报告中的见证人员信息。\n"
        "5. 异常处理：检测不合格时立即下发《监理通知单》，按规定处理（双倍复试、清退等）。\n"
        "6. 资料归档：见证取样记录、检测报告归入监理档案，作为竣工验收依据。"
    ),
    "station": (
        "旁站工作流程：\n"
        "1. 语音说明旁站工程、部位和关键工序。\n"
        "2. 广东建设智慧监理记录开始时间并持续采集现场照片与语音备注。\n"
        "3. 发现异常时立即语音描述问题并拍照取证。\n"
        "4. 说“结束旁站”后记录结束时间，形成旁站记录并进入归档流程。"
    ),
    "quality": (
        "质量问题处理流程：\n"
        "1. 语音描述问题部位、现象和影响范围。\n"
        "2. 拍照取证，广东建设智慧监理自动归类为质量问题。\n"
        "3. 形成整改建议或监理通知单，明确责任人与期限。\n"
        "4. 完成复查后转入整改闭环。"
    ),
    "other": (
        "其他工作记录流程：\n"
        "1. 语音说明会议、发文、协调或其他工作事项。\n"
        "2. 广东建设智慧监理自动提取时间、地点、参与方和工作内容。\n"
        "3. 可补充照片，确认后保存并归入监理日志。"
    ),
    "hazard": (
        "隐患识别流程：\n"
        "1. 拍照取证：对疑似隐患部位进行拍照，系统将自动识别风险类型。\n"
        "2. AI 识别：上传照片后系统调用视觉模型识别安全隐患。\n"
        "3. 评估分级：根据识别结果评估隐患等级（一般/重大/紧急）。\n"
        "4. 处置建议：\n"
        "   - 一般隐患：下发《安全隐患整改通知》要求限期整改。\n"
        "   - 重大隐患：立即下发《暂时停止施工通知》，并报告主管部门。\n"
        "5. 整改验证：施工单位整改后提交《安全隐患整改通知回复》，监理复核确认。\n"
        "6. 记录归档：将隐患照片、识别结果、整改通知、回复单归入监理档案。"
    ),
    "rectify": (
        "整改跟踪流程：\n"
        "1. 语音说明需复查的隐患或质量问题。\n"
        "2. 拍摄整改后照片并与整改前证据关联。\n"
        "3. 广东建设智慧监理记录复查结论；未通过时继续整改，通过后完成闭环。\n"
        "4. 整改过程、复查意见和影像资料统一归档。"
    ),
}


@router.post("/voice-command")
async def voice_command_match(
    req: VoiceCommandRequest,
    user: User = Depends(get_current_user),
):
    """用 LLM 动态匹配语音指令并返回操作类型 + 工作流程指引

    输入：用户语音识别文本 + 最近一次自动识别的业务模块
    输出：{
        "action": "capture_photo | start | end | query | guidance | unknown",
        "params": {...},
        "reply": "对用户的反馈语",
        "workflow": "对应工作流程指引文本"
    }
    """
    import json as json_module

    system_prompt = (
        '你是"广东建设智慧监理"工程监理现场智能助手。用户不需要预选模块，你必须根据语音内容自动判断业务类型。\n'
        f"最近一次业务：{VOICE_MODULE_LABELS.get(req.module, '尚未识别')}\n"
        "业务类型及 target_module：inspection=巡视记录，acceptance=验收记录，"
        "station=旁站记录，witness=见证记录，quality=质量问题，other=其他工作，"
        "hazard=安全巡检，rectify=整改跟踪。\n\n"
        "可执行的指令类型（action）：\n"
        "- capture_photo：用户想要拍照、抓拍、留影、记录画面\n"
        "- start：用户想要开始某个工作流程\n"
        "- end：用户想要结束当前工作流程\n"
        "- query：用户询问某个问题（如规范要求、验收标准、检查内容等）\n"
        "- guidance：用户请求工作流程指引（怎么做、流程是什么、步骤是什么）\n"
        "- switch_module：用户明确开始交付另一类现场工作\n"
        "- unknown：无法理解的指令\n\n"
        "params.target_module 必须返回最匹配的业务类型；只有语音完全没有业务线索时才沿用最近一次业务。\n"
        "返回严格 JSON 格式：\n"
        '{"action": "...", "params": {"target_module": "inspection"}, "reply": "对用户的简短确认语"}\n'
        "只返回 JSON，不要其他内容。"
    )

    try:
        text_to_parse, _ = await model_gateway.chat_completion(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.text},
            ],
            temperature=0.2,
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        text_to_parse = text_to_parse.strip()

        # 尝试从文本中提取 JSON
        parsed = {"action": "unknown", "reply": ""}
        if text_to_parse:
            try:
                parsed = json_module.loads(text_to_parse)
            except json_module.JSONDecodeError:
                match = re.search(r'\{.*\}', text_to_parse, re.DOTALL)
                if match:
                    try:
                        parsed = json_module.loads(match.group())
                    except json_module.JSONDecodeError:
                        parsed = {"action": "unknown", "reply": text_to_parse[:200]}

        allowed_actions = {
            "capture_photo", "start", "end", "query",
            "guidance", "switch_module", "unknown",
        }
        action = parsed.get("action", "unknown")
        if action not in allowed_actions:
            action = "unknown"
        params = parsed.get("params")
        if not isinstance(params, dict):
            params = {}
        target_module = params.get("target_module")
        if target_module == "quality_issue":
            target_module = "quality"
        if target_module not in VOICE_MODULE_LABELS:
            target_module = None

        inferred_module = infer_voice_module(req.text)
        recent_module = req.module if req.module in VOICE_MODULE_LABELS else None
        resolved_module = target_module or inferred_module or recent_module or "other"
        params["target_module"] = resolved_module

        reply = str(parsed.get("reply") or "").strip()
        if not reply:
            reply = f"已识别为{VOICE_MODULE_LABELS[resolved_module]}。"

        # 附加自动识别业务对应的工作流程指引
        workflow = ""
        if action in {"guidance", "start"}:
            workflow = WORKFLOW_GUIDE.get(resolved_module, "")

        return {
            "action": action,
            "params": params,
            "reply": reply,
            "workflow": workflow,
            "module": resolved_module,
            "module_label": VOICE_MODULE_LABELS[resolved_module],
            "raw_text": req.text,
        }

    except model_gateway.ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("语音指令匹配失败: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="指令匹配失败") from exc


@router.get("/workflow-guide")
async def get_workflow_guide(
    module: str = "inspection",
    user: User = Depends(get_current_user),
):
    """获取指定模块的工作流程指引"""
    guide = WORKFLOW_GUIDE.get(module)
    if not guide:
        raise HTTPException(404, f"未知模块: {module}")
    return {
        "module": module,
        "module_label": VOICE_MODULE_LABELS.get(module, module),
        "workflow": guide,
    }
