"""工作会话路由 — 语音文本AI提取 + 拍照语音自动创建"""
import json
import logging
import os
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.auth import get_current_user, require_permission, require_project_id
from app.db import get_db
from app.models.user import Project, User
from app.models.work_record import WorkRecord, WorkRecordType, WorkRecordStatus
from app.services import model_gateway
from app.services.model_gateway import ModelGatewayError
from app.services.watermark_service import apply_watermark_to_image
from app.services.media_validation import assert_valid_image
from app.services import ai_client
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

WORK_TYPES = ["巡视", "验收", "旁站", "见证", "材料进场", "施工记录"]

SYSTEM_PROMPT = (
    "你是工程监理语音助手，负责从现场语音文本中提取工作记录字段。\n"
    "请只返回 JSON，格式如下：\n"
    "{\n"
    '  "work_type": "巡视/验收/旁站/见证/材料进场/施工记录",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "reasons": ["判断依据1", "判断依据2"],\n'
    '  "suggested_title": "建议标题或 null",\n'
    '  "fields": {\n'
    '    "section": "施工区域", "location": "部位", "weather": "天气",\n'
    '    "work_content": "工作内容", "inspection_remark": "备注",\n'
    '    "acceptance_type": "", "acceptance_content": "", "acceptance_result": "",\n'
    '    "witness_material_name": "", "witness_material_spec": "",\n'
    '    "station_project_name": "", "station_location": "",\n'
    '    "station_content": "", "station_conclusion": ""\n'
    "  }\n"
    "}\n"
    "只输出 JSON，不要输出 record_id 或其他内容。"
)


class ExtractRequest(BaseModel):
    voice_text: str
    force_type: Optional[str] = None


def _parse_json(text: str) -> dict:
    """从模型输出中提取 JSON，兼容 markdown 代码块与前后缀文本。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


@router.post("/extract")
async def extract_work_session(
    req: ExtractRequest,
    user: dict = Depends(get_current_user),
):
    """从语音文本提取工作记录字段（仅提取预览，不创建记录）。"""
    voice_text = (req.voice_text or "").strip()
    if not voice_text:
        raise HTTPException(status_code=400, detail="语音文本不能为空")

    system_prompt = SYSTEM_PROMPT
    if req.force_type:
        system_prompt = (
            f"已确定为：{req.force_type}。请跳过类型判断，"
            f"直接按「{req.force_type}」记录抽取 fields 字段。\n\n" + SYSTEM_PROMPT
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"语音文本：\n{voice_text}"},
    ]

    try:
        text, _ = await model_gateway.chat_completion(
            messages, temperature=0.1, max_tokens=1024
        )
    except ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=f"模型服务不可用：{exc}") from exc

    try:
        parsed = _parse_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("语音提取结果解析失败：%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="模型返回内容无法解析") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="模型返回内容无法解析")

    fields = parsed.get("fields")
    if not isinstance(fields, dict):
        fields = {}

    return {
        "work_type": req.force_type or parsed.get("work_type") or "施工记录",
        "confidence": parsed.get("confidence", 0.5),
        "reasons": parsed.get("reasons", []),
        "suggested_title": parsed.get("suggested_title"),
        "fields": fields,
    }


# ---- 语音拍照自动创建 ----

VOICE_CREATE_PROMPT = (
    "你是工程监理语音助手，负责从现场语音文本{和照片分析结果}中提取工作记录字段并判断是否完整。\n\n"
    "各类型必填字段：\n"
    "- 巡视：section(巡视部位), work_content(施工情况)\n"
    "- 验收：acceptance_type(验收类型), section(施工区域), acceptance_content(验收内容), acceptance_result(验收结果)\n"
    "- 旁站：station_project_name(旁站工程), station_location(旁站部位), start_time(开始时间HH:MM), end_time(结束时间HH:MM), station_content(施工情况), station_supervision_status(监理情况)\n"
    "- 见证：witness_material_name(材料名称), witness_quantity(取样数量)\n\n"
    "可选字段：location(具体位置), weather(天气), problems(发现问题), treatment(处理意见), remark(备注)等\n\n"
    "只返回 JSON：\n"
    '{\n'
    '  "work_type": "巡视/验收/旁站/见证",\n'
    '  "fields": { ... },\n'
    '  "missing": [{"field": "section", "label": "巡视部位", "prompt": "请问巡视部位在哪里？"}],\n'
    '  "complete": true/false\n'
    "}\n"
    "只输出 JSON。如果语音文本是补充信息，请结合之前的字段一起判断。"
)

# 各类型必填字段定义
REQUIRED_FIELDS = {
    "巡视": [["section", "巡视部位"], ["work_content", "施工情况"]],
    "验收": [["acceptance_type", "验收类型"], ["section", "施工区域"], ["acceptance_content", "验收内容"], ["acceptance_result", "验收结果"]],
    "旁站": [["station_project_name", "旁站工程"], ["station_location", "旁站部位"], ["start_time", "开始时间"], ["end_time", "结束时间"], ["station_content", "施工情况"], ["station_supervision_status", "监理情况"]],
    "见证": [["witness_material_name", "材料名称"], ["witness_quantity", "取样数量"]],
}

# 类型 → WorkRecordType
TYPE_MAP = {
    "巡视": WorkRecordType.INSPECTION.value,
    "验收": WorkRecordType.ACCEPTANCE.value,
    "旁站": WorkRecordType.STATION.value,
    "见证": WorkRecordType.WITNESS.value,
}


def _check_missing(work_type: str, fields: dict) -> list[dict]:
    """检查必填字段是否齐全，返回缺失字段列表。"""
    missing = []
    for field, label in REQUIRED_FIELDS.get(work_type, []):
        val = str(fields.get(field, "")).strip()
        if not val:
            missing.append({"field": field, "label": label, "prompt": f"请补充{label}"})
    return missing


def _build_record_kwargs(work_type: str, fields: dict, user: User, project_id: int) -> dict:
    """根据类型和字段构建 WorkRecord 创建参数。"""
    record_type = TYPE_MAP.get(work_type, "待定")
    kwargs = {
        "record_type": record_type,
        "status": WorkRecordStatus.DRAFT.value,
        "creator_id": user.id,
        "project_id": project_id,
        "section": fields.get("section", "") or None,
        "location": fields.get("location", "") or None,
        "weather": fields.get("weather", "") or None,
        "problems": fields.get("problems", "") or "",
        "treatment": fields.get("treatment", "") or "",
        "content_json": {"voice_text": fields.get("_voice_text", ""), "source": "voice-create"},
    }
    if record_type == WorkRecordType.INSPECTION.value:
        kwargs["work_content"] = fields.get("work_content", "") or None
        kwargs["inspection_remark"] = fields.get("inspection_remark", "") or None
    elif record_type == WorkRecordType.ACCEPTANCE.value:
        kwargs["acceptance_result"] = fields.get("acceptance_result", "") or None
        kwargs["acceptance_basis"] = fields.get("acceptance_basis", "") or ""
        kwargs["content_json"] = {
            "acceptance_type": fields.get("acceptance_type", ""),
            "acceptance_content": fields.get("acceptance_content", ""),
            "voice_text": fields.get("_voice_text", ""),
            "source": "voice-create",
        }
    elif record_type == WorkRecordType.STATION.value:
        kwargs["station_project_name"] = fields.get("station_project_name", "") or None
        kwargs["station_location"] = fields.get("station_location", "") or None
        kwargs["station_content"] = fields.get("station_content", "") or None
        kwargs["station_supervision_status"] = fields.get("station_supervision_status", "") or ""
        kwargs["station_conclusion"] = fields.get("station_conclusion", "") or None
        today = datetime.now().strftime("%Y-%m-%d")
        if fields.get("start_time"):
            kwargs["station_start_time"] = datetime.fromisoformat(f"{today}T{fields['start_time']}:00")
        if fields.get("end_time"):
            kwargs["station_end_time"] = datetime.fromisoformat(f"{today}T{fields['end_time']}:00")
        if fields.get("end_time"):
            kwargs["status"] = WorkRecordStatus.SUBMITTED.value
        else:
            kwargs["status"] = WorkRecordStatus.IN_PROGRESS.value
    elif record_type == WorkRecordType.WITNESS.value:
        kwargs["witness_material_name"] = fields.get("witness_material_name", "") or None
        kwargs["witness_material_spec"] = fields.get("witness_material_spec", "") or None
        kwargs["witness_quantity"] = fields.get("witness_quantity", "") or ""
        kwargs["witness_unit"] = fields.get("witness_unit", "") or ""
        kwargs["witness_cert_no"] = fields.get("witness_cert_no", "") or ""
        kwargs["witness_accept_result"] = fields.get("witness_accept_result", "") or ""
        kwargs["remark"] = fields.get("remark", "") or None
    return kwargs


async def _read_photo(photo: UploadFile) -> bytes:
    """读取并校验照片。"""
    ext = os.path.splitext(photo.filename or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(400, "仅支持 jpg、png、webp 图片")
    data = await photo.read(settings.max_image_bytes + 1)
    await photo.close()
    if not data:
        raise HTTPException(400, "图片为空")
    if len(data) > settings.max_image_bytes:
        raise HTTPException(413, "图片超过大小限制")
    assert_valid_image(data)
    return data


async def _voice_create_impl(
    voice_text: str,
    previous_text: str,
    photo_bytes: Optional[bytes],
    filename: str,
    user: User,
    db: AsyncSession,
    auto_time: str = "",
) -> dict:
    """拍照 + 语音自动创建工作记录的共用实现。

    接收累计语音文本 + 可选照片 → VLM 分析照片 → LLM 提取字段 + 判断缺失 →
    字段齐全则创建记录，否则返回追问提示。
    """
    full_text = (previous_text + "\n" + voice_text).strip() if previous_text else voice_text.strip()
    if not full_text:
        raise HTTPException(status_code=400, detail="语音文本不能为空")

    project_id = require_project_id(user)

    # 1. 如有照片 → VLM 分析
    vlm_context = ""
    if photo_bytes:
        try:
            vlm_result = await ai_client.detect_hazard(photo_bytes, filename=filename or "capture.jpg")
            scene = vlm_result.get("scene_related", "")
            risk = vlm_result.get("risk_status", "")
            category = vlm_result.get("risk_category", "")
            evidence = vlm_result.get("evidence", [])
            parts = []
            if scene:
                parts.append(f"场景：{scene}")
            if risk:
                parts.append(f"风险状态：{risk}")
            if category:
                parts.append(f"风险类别：{category}")
            if evidence:
                parts.append(f"证据：{'、'.join(evidence)}")
            vlm_context = "；".join(parts)
        except Exception as exc:
            logger.warning("VLM 分析失败（继续用语音文本提取）：%s", exc)

    # 2. LLM 提取字段 + 判断缺失
    system_prompt = VOICE_CREATE_PROMPT
    if vlm_context:
        system_prompt = system_prompt.replace("{和照片分析结果}", "和照片分析结果") + f"\n\n照片分析结果：{vlm_context}"
    else:
        system_prompt = system_prompt.replace("{和照片分析结果}", "")

    user_content = f"语音文本：\n{full_text}"
    if previous_text:
        user_content = f"之前累计语音：\n{previous_text}\n\n本次补充：\n{voice_text}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        llm_text, _ = await model_gateway.chat_completion(
            messages, temperature=0.1, max_tokens=1024
        )
    except ModelGatewayError as exc:
        raise HTTPException(status_code=503, detail=f"模型服务不可用：{exc}") from exc

    try:
        parsed = _parse_json(llm_text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("voice-create 提取结果解析失败：%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="模型返回内容无法解析") from exc

    work_type = parsed.get("work_type", "巡视")
    fields = parsed.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}
    fields["_voice_text"] = full_text

    # 2.5 时间自动生成（精确到分钟，2026-10-09）
    #     旁站的开始/结束时间必填，但语音里往往不会说时间 → 直接用当前时间兜底，
    #     避免为「几点几分」反复追问；用户之后可在工作记录页里改。
    now_hm = (auto_time or "").strip() or datetime.now().strftime("%H:%M")
    if work_type == "旁站":
        if not str(fields.get("start_time", "")).strip():
            fields["start_time"] = now_hm
        if not str(fields.get("end_time", "")).strip():
            fields["end_time"] = now_hm

    # 3. 检查必填字段
    missing = parsed.get("missing", [])
    if not isinstance(missing, list) or not missing:
        # LLM 没返回 missing 或为空 → 自己再校验一遍
        missing = _check_missing(work_type, fields)

    complete = parsed.get("complete", False)
    if not complete and not missing:
        complete = True

    # 4. 字段不齐全 → 返回追问
    if not complete and missing:
        prompt_text = "、".join(m["label"] for m in missing)
        return {
            "created": False,
            "work_type": work_type,
            "missing_fields": missing,
            "prompt_text": f"请补充{prompt_text}",
            "fields_so_far": {k: v for k, v in fields.items() if k != "_voice_text"},
        }

    # 5. 字段齐全 → 创建记录
    saved_voice_photo = None
    try:
        kwargs = _build_record_kwargs(work_type, fields, user, project_id)
        record = WorkRecord(**kwargs)
        db.add(record)
        await db.flush()

        # 保存照片（如有）
        if photo_bytes:
            project = await db.get(Project, project_id)
            watermarked = await apply_watermark_to_image(
                image_data=photo_bytes,
                project_name=project.name if project else "",
                supervisor=user.real_name,
                section=fields.get("section", "") or "",
                location=fields.get("location", "") or "",
                work_content=fields.get("work_content", "") or work_type,
                weather=fields.get("weather", "") or "",
            )
            filename = f"voice_create_{record.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
            save_path = settings.upload_dir / "inspection" / str(record.id)
            save_path.mkdir(parents=True, exist_ok=True)
            file_path = save_path / filename
            file_path.write_bytes(watermarked)
            saved_voice_photo = file_path  # 记录已落盘路径，失败时清理
            public_path = f"/uploads/inspection/{record.id}/{filename}"
            record.inspection_photos = [public_path]

        # 自动生成省统表文书
        try:
            from app.routers.work_record import auto_create_record_document
            await auto_create_record_document(db, user, record, auto_generate=False)
        except Exception as exc:
            logger.warning("自动生成文书失败（记录已保存）：%s", exc)

        await db.commit()
        await db.refresh(record)

        return {
            "created": True,
            "record_id": record.id,
            "record_type": work_type,
            "record_time": record.station_start_time.strftime("%Y-%m-%d %H:%M")
            if getattr(record, "station_start_time", None)
            else datetime.now().strftime("%Y-%m-%d %H:%M"),
            "has_photo": bool(photo_bytes),
            "summary": f"{work_type}记录已创建",
        }
    except Exception as exc:
        logger.error("voice-create 创建记录失败：%s", exc)
        await db.rollback()
        # rollback 后清理已写盘的照片，防孤儿文件
        try:
            if saved_voice_photo is not None:
                saved_voice_photo.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"创建记录失败：{exc}") from exc


# ---- 对外端点：带照片走 multipart，纯语音走 JSON（2026-10-09 补齐纯语音链路）----


class VoiceCreateRequest(BaseModel):
    voice_text: str
    previous_text: str = ""
    """客户端本地时间 HH:MM，用于把「记录时间」自动生成到分钟；为空时用服务器时间。"""
    auto_time: str = ""


@router.post("/voice-create")
async def voice_create_work_record(
    voice_text: str = Form(...),
    previous_text: str = Form(""),
    auto_time: str = Form(""),
    photo: Optional[UploadFile] = File(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """拍照 + 语音自动创建工作记录（multipart，可带一张现场照片）。"""
    photo_bytes = await _read_photo(photo) if photo else None
    return await _voice_create_impl(
        voice_text=voice_text,
        previous_text=previous_text,
        photo_bytes=photo_bytes,
        filename=(photo.filename if photo else "capture.jpg") or "capture.jpg",
        user=user,
        db=db,
        auto_time=auto_time,
    )


@router.post("/voice-create-text")
async def voice_create_work_record_text(
    req: VoiceCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """纯语音（无照片）自动创建工作记录 —— 支持「先说话，再补拍照片」的顺序。"""
    return await _voice_create_impl(
        voice_text=req.voice_text,
        previous_text=req.previous_text,
        photo_bytes=None,
        filename="capture.jpg",
        user=user,
        db=db,
        auto_time=req.auto_time,
    )

