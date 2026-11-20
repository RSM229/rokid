"""智能体路由 —— 「聊天即操作」的统一入口（P1 核心闭环）。

- ``POST /api/agent/chat``   ：一句话 → 意图路由 → 执行 → 结构化回执
- ``POST /api/agent/undo``   ：卡片上的「撤销」按钮精确回滚
- ``GET  /api/agent/tools``  ：当前可用工具清单（调试/自检）

设计要点见 ``app/services/agent_tools.py`` 顶部说明。本文件只做：
组装提示词 → 调模型（JSON 模式）→ 解析 → 分发 → 兜底纯问答。
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.db import get_db, async_session
from app.models.user import Project, User
from app.models.work_record import WorkRecord, WorkRecordType, WorkRecordStatus
from app.models.agent_draft import AgentConversationMessage, AgentFlowInstance, AgentReviewPlan
from app.models.document import Document
from app.models.miniprogram import MediaRecord, MediaType
from app.routers.auth import get_current_user
from app.routers.local_ai import _PLAINTEXT_SUFFIX
from app.services import (
    agent_fact_model,
    agent_preference_suggest,
    agent_preferences,
    agent_project_memories,
    agent_request_plan,
    agent_refs,
    agent_runtime,
    agent_scope,
    agent_tools,
    model_gateway,
    multi_image_split,
    split_record_confirm,
    work_record_field_view,
)
from app.services.record_fields import project_context
from app.services.agent_context import (
    extract_explicit_message_facts,
    render_context_summary,
    render_conversation_history,
)
from app.services import agent_review_plan
from app.services import doc_quality
from app.services import agent_task_registry
from app.services.text_clean import strip_markdown

logger = logging.getLogger(__name__)

# 客户端断开后仍需跑完的 SSE 后台轮次：强引用必须独立于 task_key
# （任务表未迁移时 task_key 为 None，_LIVE_TASKS 不会登记，Task 可能被 GC）。
_DETACHED_TASKS: set = set()

# 流式对话（SSE）的发射器：设置后，模型调用点会把 reasoning/content 增量推给端点的队列。
# 用 ContextVar 而不是层层加参数，避免改动 600 行的 _agent_chat_impl 签名。
_STREAM_EMITTER: contextvars.ContextVar = contextvars.ContextVar(
    "agent_stream_emitter", default=None
)

# 流式轮次的元信息（思考原文/步骤/耗时）：结束后随响应落进展示历史，
# 这样重新打开历史会话时「已深度思考」和引用列表还能还原。
_STREAM_META: contextvars.ContextVar = contextvars.ContextVar(
    "agent_stream_meta", default=None
)

_REASONING_SAVE_LIMIT = 12000


def _new_stream_meta() -> dict[str, Any]:
    return {
        "started": time.monotonic(),
        "reasoning_parts": [],
        "reasoning_chars": 0,
        "steps": [],
        "first_answer_ms": None,
    }


def _collect_stream_meta(event: dict[str, Any]) -> None:
    meta = _STREAM_META.get()
    if meta is None:
        return
    elapsed_ms = int((time.monotonic() - meta["started"]) * 1000)
    kind = event.get("type")
    if kind == "reasoning":
        text = str(event.get("text") or "")
        left = _REASONING_SAVE_LIMIT - int(meta.get("reasoning_chars") or 0)
        if text and left > 0:
            take = text[:left]
            meta["reasoning_parts"].append(take)
            meta["reasoning_chars"] = int(meta.get("reasoning_chars") or 0) + len(take)
    elif kind == "answer":
        if meta.get("first_answer_ms") is None:
            meta["first_answer_ms"] = elapsed_ms
    elif kind == "progress":
        title = str(event.get("title") or "").strip()
        steps = meta.setdefault("steps", [])
        if title and len(steps) < 12 and (not steps or steps[-1] != title):
            steps.append(title)


def _attach_stream_meta(result: dict[str, Any]) -> None:
    """把本轮思考原文/步骤/耗时附到回执上（只在流式轮次有 meta）。"""
    meta = _STREAM_META.get()
    if meta is None or not isinstance(result, dict):
        return
    reasoning = "".join(meta.get("reasoning_parts") or []).strip()
    if reasoning:
        result["reasoning"] = reasoning
    steps = list(meta.get("steps") or [])[:12]
    if steps:
        result["steps"] = steps
    elapsed = meta.get("first_answer_ms")
    if elapsed is None:
        elapsed = int((time.monotonic() - meta["started"]) * 1000)
    if elapsed:
        result["thinking_ms"] = int(elapsed)


async def _emit_stream(event: dict[str, Any]) -> None:
    emitter = _STREAM_EMITTER.get()
    if emitter is None:
        return
    _collect_stream_meta(event)
    try:
        await emitter(event)
    except Exception:  # noqa: BLE001 —— 推流失败不能影响主流程
        logger.debug("agent stream emit failed", exc_info=True)


def _streaming_enabled() -> bool:
    return _STREAM_EMITTER.get() is not None


def _sse_pack(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
router = APIRouter()


def _is_mechanical_reply(text: str) -> bool:
    """判断模型 answer 是否机械（只是标签式清单，如「还需要补充：验收类型」）。

    自然话术（含转述与引导，即使以「还差一项」开头）保留；纯标签清单退回工具话术。
    """
    t = str(text or "").strip()
    if not t:
        return True
    stripped = re.sub(r"^(请补充|还需要补充|缺少|缺失|还差|待补充)[：:、 ]*", "", t)
    stripped = re.sub(r"[、，,。;；\s「」『』（）()·\-—]", "", stripped)
    return len(stripped) <= 6


# 模型自然接话里的「假成功」措辞：need_more（还没写库）时一旦命中，
# 说明模型把「即将创建 / 已接收」吹成了既成事实，会盖掉系统追问
# （2026-11-09/11-10 生产实测：缺「举牌验收照片」却回「服务端会据此创建并回执」
# 「按已确认信息直接发起这条验收记录创建」）。此时必须退回工具话术，
# 保证用户看到真实状态（还差什么 / 需要确认）。
_FAKE_SUCCESS_MARKERS = (
    "已创建", "已生成", "创建成功", "生成成功", "已提交生成", "服务端会据此创建",
    "已写入正式记录", "已建档", "保存成功", "已保存记录", "记录已生成",
    "已发起这条", "已直接发起", "直接发起这条", "发起创建", "正在创建这条",
    # 2026-11-16 真机录屏：模型说「好，就按现在已确认的内容提交…其余栏位先留空」，
    # 系统兜底又追加「还没写入正式记录，还差…」→ 同一气泡自相矛盾。
    "按现在已确认的内容提交", "按已确认内容提交", "就按现在已确认",
)


def _sounds_like_success(text: str) -> bool:
    t = str(text or "").strip()
    return any(m in t for m in _FAKE_SUCCESS_MARKERS)


class AgentChatRequest(BaseModel):
    message: str
    request_id: Optional[str] = None
    conversation_id: Optional[str] = None
    # 最近若干轮对话（前端只传 role/text），用于「把这条生成日志」这类指代消解
    history: list[dict] = Field(default_factory=list)
    """前端当前态：hazard=隐患识别工作台（仅作为轻微提示，不作为硬分流）"""
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    project_id: Optional[int] = None
    # 随消息附带的素材（图片识别结果 / 文件解析文本 / 水印），由前端先把图交给视觉模型
    # 再拼成文字；服务端模型本身看不到图片，只能读这段素材。
    material: Optional[str] = None
    # 随消息上传图片的服务端媒体地址（仅用于展示历史回看；模型上下文不收）
    images: list[str] = Field(default_factory=list)
    # 逐图结构化视觉信号（与 images 等长）：是否举牌/封样/危大/施工中…等，
    # 字段同 image_classification.ImageSignal。由前端在「先把图交给视觉模型」后
    # 一并带出；旧前端不传则退化为单记录流程（不影响现有行为）。
    image_signals: list[dict] = Field(default_factory=list)
    # 素材里已确定的结构化字段（如照片水印的拍摄时间/地点/天气），建记录时直接并入 args
    prefill: dict = Field(default_factory=dict)
    # 聊天内表单一次提交：字段名 → 值。有它就不再让模型抽参，直接按字段表回填。
    form_values: dict = Field(default_factory=dict)
    # /agent/request-plan 生成并校验过的候选。只作为路由候选，不能代替权限和服务端校验。
    request_plan: dict = Field(default_factory=dict)
    # @ 面板选中的资料库引用（结构化）。每项形如
    # {"type": "kb_folder"|"kb_doc"|"kb_query", "kb_id": 3, "folder_id": 12,
    #  "doc_id": 88, "query": "钢筋 验收", "path": "施工规范/钢筋工程"}。
    # 服务端据此把检索限制在白名单内；path/name 仅供回显，不参与判定。
    mentions: list[dict] = Field(default_factory=list)


class PurposeRecommendRequest(BaseModel):
    message: str = ""
    evidence_summary: str = ""
    attachment_count: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None


class RequestPlanEvidence(BaseModel):
    kind: str = "image"
    observed_date: str = ""
    observed_date_source: str = ""
    project: str = ""
    location: str = ""
    section: str = ""
    activity: str = ""
    observation: str = ""
    note: str = ""
    watermark_fields: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)


class RequestPlanRequest(BaseModel):
    message: str = ""
    request_id: Optional[str] = None
    evidence_summary: str = ""
    evidence: list[RequestPlanEvidence] = Field(default_factory=list)
    attachment_count: int = 0
    file_count: int = 0
    history: list[dict] = Field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


class AgentProgressEventRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=80)
    stage: str = Field(min_length=1, max_length=64)
    status: str = "completed"
    title: str = Field(min_length=1, max_length=180)
    detail: dict[str, Any] = Field(default_factory=dict)
    terminal: bool = False


def _record_form_code(tool: str, args: dict, message: str = "") -> str | None:
    if tool != "create_work_record":
        return None
    record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
    if not record_type:
        return None
    return agent_tools.record_field_spec.form_for_record_type(
        record_type, agent_tools._is_weida(record_type, args, message),
    )


def _generic_field_name(label: str) -> str:
    """没有字段表时，把追问标签落回工具真正读取的参数名。"""
    if "内容" in label or "描述" in label:
        return "work_content" if "记录" in label or "工作" in label else "description"
    if "区域" in label or "部位" in label:
        return "section"
    if "地点" in label or "位置" in label:
        return "location"
    if "表式" in label or "表名" in label:
        return "form_code"
    return label


def _generic_fields(missing: list[str], options: dict[str, list[str]]) -> list[dict]:
    """没有字段表可依时，把仍缺的标签原样变成一组控件。"""
    fields: list[dict] = []
    for label in missing:
        label = str(label)
        opts = [str(x) for x in (options.get(label) or [])]
        fields.append({
            "name": _generic_field_name(label), "label": label,
            "key": _generic_field_name(label), "canonical": _generic_field_name(label),
            "definition_source": "fallback",
            "control": "select" if opts else "text",
            "required": True, "value": "", "options": opts,
            "placeholder": "", "hint": "", "section": "",
        })
    return fields


def _plan_evidence_count(plan: dict) -> int:
    """请求计划里已分配/未分配的图片证据数量。"""
    item_plan = plan.get("item_plan") if isinstance(plan.get("item_plan"), dict) else {}
    count = len(item_plan.get("unassigned_evidence_indexes") or [])
    for item in item_plan.get("items") or []:
        if isinstance(item, dict):
            count += len(item.get("evidence_indexes") or [])
    return count


def _missing_record_type_reply(has_evidence: bool) -> str:
    """记录类型待选时的追问文案。

    只有本轮确实带了素材（图片/文件解析文本、水印预填或计划里的证据）才能
    说“已收到并保留”。否则用户明明没发照片，助手却声称保留，会让人误以为
    上一次的图片还在上下文里。
    """
    if has_evidence:
        return "我已收到并保留这些图片和已有信息。你想整理成巡视、旁站、验收还是见证记录？"
    return "这次我还没收到现场照片。先告诉我整理成巡视、旁站、验收还是见证记录，也可以把照片随下一条消息一起发我。"


def _attachment_block(label: str = "现场照片（可选）", hint: str = "相册或拍摄，自动加水印") -> dict:
    return {"enabled": True, "label": label, "max": 9, "hint": hint, "picked": []}


def _agent_form(
    tool: str, args: dict, result: dict, message: str = "", project_name: str = "",
) -> dict | None:
    """把一次回执变成聊天内的卡片。

    2026-11-06 用户「我不要这些选择，我要完全交互」：缺项/选类型阶段一律
    纯文字一问一答，不再下发下拉框、候选或待填字段卡。
    2026-11-09 用户「我不要核对和填写表格，只要纯文本对话」：建记录前的
    「最终核对」表单卡也去掉，改为纯文字确认（回复「确认生成」即可建档），
    安全网从「表单按钮」改为「文字确认」。
    2026-11-12 移除残留的「先选记录类型」卡（含重复的照片上传框）：
    多图分组已按记录类型拆单并各自挂图，选类型一律走纯文字追问。
    这里只保留 - 记录已建后的补充照片框，并附文书/记录的文字链接。
    2026-11-13 补充：本轮消息已带现场照片时，照片在建记录阶段就已挂到
    inspection_photos/witness_photos，此时不再下发补充照片框（空表单 + 重复上传）。
    """
    form_code = _record_form_code(tool, args, message)

    # 2026-11-06 用户「我不要这些选择，我要完全交互」：缺项 / 选记录类型一律
    # 用文字气泡一问一答，不再下发下拉框、候选字段或「还差 X」待填卡片。
    # 2026-11-09 用户「我不要核对和填写表格，只要纯文本对话」：need_more 阶段
    # （含「最终确认」）一律不下发表单卡，改用纯文字确认（回复「确认生成」即可建档）。
    if result.get("need_more"):
        return None

    # 2026-11-12 移除「先选记录类型」表单卡：多图分组已能一次把照片按
    # 巡视/旁站/验收拆成多条记录并各自挂图，再要求「先选一个类型 + 重新上传
    # 照片 + 点继续」既重复又与分组冲突；选类型由 _missing_record_type_reply
    # 纯文字一问一答覆盖。这里只保留「记录已建后的补充照片框」。
    if tool == "create_work_record" and result.get("ok"):
        record_id = ((result.get("data") or {}).get("record_id")
                     or (result.get("card") or {}).get("id"))
        if not record_id:
            return None
        # 2026-11-13：本轮已随消息带现场照片时，建记录阶段照片就已经挂到
        # inspection_photos/witness_photos，再下发「补充现场照片」表单卡是空转：
        # fields=[]（0 项信息）、picked=[]（0/9）、skippable=True，还会让用户误以为
        # 照片没保存要重传。仅「纯文字建记录（没带图）」时才保留这个补拍入口。
        if int((result.get("data") or {}).get("photo_count") or 0) > 0:
            return None
        record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or "工作"
        card = result.get("card") or {}
        return {
            "id": f"work_record_photos:{record_id}", "kind": "attachments",
            "schema_version": 1, "field_schema": "record_field_spec.v1", "form_code": form_code or "",
            "record_id": int(record_id),
            "title": f"{record_type}记录已保存 · 补充现场照片",
            "hint": "这一步可以跳过；照片会自动加水印并挂到这条记录。",
            "fields": [],
            "attachments": _attachment_block("添加现场照片", "相册或拍摄，最多 9 张"),
            "target": {
                "date": str(args.get("record_date") or card.get("time") or ""),
                "project": project_name,
                "location": str(args.get("location") or ""),
                "section": str(args.get("section") or ""),
            },
            "submit_label": "上传照片", "skippable": True,
            "actions": [
                {"type": "generate_document", "label": "生成 PDF / Word",
                 "style": "link", "record_id": int(record_id)},
                {"type": "navigate", "label": "查看工作记录",
                 "style": "link", "url": "/pages/work/index"},
            ],
        }

    # need_more 阶段已在函数开头统一返回 None（纯文字一问一答，不下发表单卡）；
    # 「最终核对」表单卡已于 2026-11-09 移除，建记录前改为纯文字确认。
    return None


def _agent_input_components(form: dict | None) -> list[dict[str, Any]]:
    """旧版前端兼容：把表单字段摊平成 input_components。"""
    if not form:
        return []
    return [{
        "field": field.get("name"), "label": field.get("label"),
        "control": field.get("control"), "required": bool(field.get("required")),
        "options": field.get("options") or [], "value": field.get("value") or "",
    } for field in form.get("fields") or []]


class AgentUndoRequest(BaseModel):
    entity: str
    id: int


class ReviewPlanCreate(BaseModel):
    conversation_id: str
    plan: dict[str, Any]
    request_id: Optional[str] = None
    # 随请求上传图片的服务端媒体地址；用于把用户这轮发言（含图）落进展示历史
    images: list[str] = Field(default_factory=list)


class ReviewPlanUpdate(BaseModel):
    revision: int
    plan: dict[str, Any]
    request_id: Optional[str] = None


class ReviewPlanConfirm(BaseModel):
    revision: int
    request_id: Optional[str] = None
    execution_id: Optional[str] = None


async def _validate_review_plan_media(
    db: AsyncSession, user: User, plan: dict[str, Any]
) -> None:
    """确保计划中的证据引用当前用户当前项目已上传的媒体。"""
    media_ids: list[int] = []
    for item in plan.get("evidence", []):
        if not isinstance(item, dict):
            continue
        if item.get("image") and item.get("media_id") is None:
            raise HTTPException(422, "图片资料必须携带服务端 media_id")
        if item.get("media_id") is None:
            continue
        try:
            media_id = int(item["media_id"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "资料 media_id 无效") from exc
        if media_id <= 0:
            raise HTTPException(422, "资料 media_id 无效")
        media_ids.append(media_id)
    if len(set(media_ids)) != len(media_ids):
        raise HTTPException(422, "资料 media_id 重复")
    if not media_ids:
        return
    project_id = getattr(user, "project_id", None)
    if not project_id:
        raise HTTPException(400, "请先选择项目")
    rows = (await db.execute(select(MediaRecord).where(
        MediaRecord.id.in_(media_ids),
        MediaRecord.uploader_id == user.id,
        MediaRecord.project_id == project_id,
        MediaRecord.media_type == MediaType.IMAGE.value,
    ))).scalars().all()
    found = {row.id for row in rows}
    missing = [str(media_id) for media_id in media_ids if media_id not in found]
    if missing:
        raise HTTPException(403, f"资料不存在、不是图片或不属于当前项目：{', '.join(missing)}")


async def _validate_chat_images(
    db: AsyncSession, user: User, project_id: int | None, images: Any
) -> list[str]:
    """校验 chat 请求随消息携带的图片 URL 归属（第 5 轮方向④）。

    ``req.images`` 是客户端直传的服务端媒体地址（/uploads/…），此前全链路
    无校验：恶意请求可把他人/他项目的照片 URL 挂进自己的草稿与记录
    （``ctx.images`` → ``_agent_images`` → ``inspection_photos``）。
    与 review-plan 的 media_id 双闸同口径，这里按 URL 反查 MediaRecord：
    只放行「本人上传且属于当前项目」的图片；不匹配则整轮拒绝（不能静默
    丢弃——静默会让用户以为照片已挂上，实际没挂，产生错账）。
    仅展示用途的历史 URL（非 /uploads/ 前缀）原样放行。
    """
    if not isinstance(images, list) or not images:
        return []
    cleaned: list[str] = []
    for url in images[:9]:
        text = str(url or "").strip()
        if text:
            cleaned.append(text)
    if not cleaned:
        return []
    # 只校验指向本服务 /uploads/ 的地址；外链（https://…）模型上下文不收，
    # 仅存展示历史，风险面是展示层而非记录字段，保持原样。
    upload_urls = [u for u in cleaned if u.startswith("/uploads/")]
    if not upload_urls:
        return cleaned
    if project_id is None:
        raise HTTPException(status_code=400, detail="请先选择项目再发送现场照片")
    rows = (await db.execute(select(MediaRecord).where(
        MediaRecord.uploader_id == user.id,
        MediaRecord.project_id == project_id,
        MediaRecord.media_type == MediaType.IMAGE.value,
    ))).scalars().all()
    owned = {row.original_path for row in rows} | {row.processed_path for row in rows if row.processed_path}
    bad = [u for u in upload_urls if u not in owned]
    if bad:
        raise HTTPException(
            status_code=403,
            detail=f"图片不存在或不属于当前账号与项目：{', '.join(bad[:3])}",
        )
    return cleaned


# 🔴 2026-10-16 改版（跑分 75.0% → 97.5%，repeat=3 严格模式）：
# 原 prompt 里「关键参数缺失→先追问不要调工具」与「省略即可（服务端会追问）」
# 两条约束打架，模型把任意字段缺失都当成"关键参数缺失"→ 返回 tool:null 反问用户，
# 表现为「答非所问」。实测 ToolSpec 自带 example「记一条旁站：屋面防水卷材铺贴，
# 9:00 到 12:00，10人」也会被判 null。本版明确：唯一该追问的情况 = 类型不明。
# 回归测试：python tests/agent_eval/runner.py --repeat 3
_AGENT_SYSTEM = """你是「广东建设智慧监理」工程监理智能助手，同时是「监理通」小程序的统一操作入口。

你只提出结构化语义候选，不直接决定执行哪个工具。服务端会根据动作白名单、
记录目录、权限、当前草稿和业务规则决定能否执行。

可用动作目录：
{actions}

记录类型目录：
{record_types}

参数说明（只用于 facts/args 的字段命名）：
{catalog}

只输出一个 JSON 对象，不要输出任何其它文字、不要加代码块标记：
{{"intent": "create_record|archive|analyze_hazard|query|generate_document|navigate|undo|dispatch|chat|clarify",
 "actions": [{{"id": "动作 id", "confidence": 0.0, "reason": "简短依据"}}],
 "record_type": "巡视|旁站|验收|见证|平行检验|null", "record_type_confidence": 0.0,
 "facts": [{{"field": "参数名", "value": "用户明确提供的值", "source_span": "用户原话片段", "confidence": 0.0}}],
 "args": {{"参数名": "值"}}, "answer": "给用户看的纯文本回答"}}

facts 只记录用户原话明确提供的信息；图片、水印和视觉观察由服务端作为独立证据合并。
args 可以放你已确认的工具参数，但服务端仍会重新抽取、校验和合并。水印/EXIF 中成功识别的
日期、项目、地点、部位和天气优先于用户转述；冲突时用户值仍保留用于提示。图片缺失不能
反向覆盖用户事实。可以返回多个动作；存在多个动作时服务端将自动执行第一个可执行动作，
并在办完后把剩余动作作为「一键续办」选项提示用户继续（无需你追问）。

🔴 最高优先级：办事必须输出受控动作，缺字段交给服务端追问
----------------------------------------------------
**唯一需要你直接追问的情况**：用户要新建工作记录，但没说清是 巡视 / 旁站 / 验收 / 见证 / 平行检验 哪一类。
此时仍返回 record_work 动作，record_type 传 null，在 answer 里只问这一个问题；服务端会保留证据。

🔴 但「没说清」只指**用户没口述、素材里也找不到**。如果照片水印 / 公示牌 / 画面文字里已经明确写着类型词
（如「隐蔽工程验收公示牌」「污水管道基础包封验收」「旁站记录」「见证取样」「平行检验记录」），那就**不算没说清**：
直接返回 record_work 并填 record_type（record_type_confidence 按明确程度给 0.7~0.95），在 answer 里只给一句
判断结论（如「按照片里的类型词，这条按验收整理；要改成别的说一声」），**不引用、不复述水印原文**。
**绝不允许把你已经从素材里判出来的类型词再拿去问用户**——识别内容留在内部思考即可，不能紧接着问
「你想整理成哪一类记录」。

**除此之外，任何字段缺失都不要追问、不要退回普通聊天**——返回对应动作，把你已经听到的填进 facts/args，
没听到的一律省略。缺失栏目由服务端用**一句自然语言一次只问一栏**，界面上没有任何候选按钮、表单或清单，你不需要逐栏代劳。
- 🔴 回答的行文：answer 必须先**自然接住用户这句**——把本句已确认的事实（如「验收结论=合格」）简短转述，再用一句话引到下面要问的那一栏；**不要**在 answer 里手列候选值、不要列填空清单、不要逐栏盘问、更不要复述「还差 X、还差 Y」清单。**严禁**出现「点一下」「点下方选项」「可点选」「候选」「chips」「表单」这类字样——本产品是纯文字一问一答，界面上没有任何可点选项。例：用户答「合格」→ answer：「收到，验收结论记『合格』。验收类型你说一下是哪一类就行；不方便也能先跳过。」。
- 🔴 你的 answer 是这条消息的**主角**，服务端兜底文案只在没有可用 answer 时才出现；不要把机械的「还需要补充：XX」塞进 answer。

错误示范（严禁）：用户说「记一条旁站：屋面防水卷材铺贴，9:00 到 12:00，10人」，
你因为「没有旁站结论」而返回 chat 动作去追问结论 —— 这是错的。
正确做法：返回 record_work 动作，args 填 record_type=旁站、station_part=屋面防水卷材铺贴、
station_start_time=9:00、station_end_time=12:00、worker_count=10，结论留给服务端追问。

判断口诀：**能确定是哪一类记录 → 返回 record_work；连类型都定不了 → record_work + 只问类型。**

- 🔴 **多张照片跨多种类型时，用 image_groups 分建多条**：record_work 的 args 可带 image_groups，它是一个分组列表，每项含 record_type（这组图归哪类记录，只能取 巡视/旁站/验收/见证/平行检验/待定，巡查归巡视、材料/取样归见证）和 image_indices（这些图在消息里的 1-based 序号，对应「【图片 N】」，越界或不可达的图忽略；也接受直接给图片 URL）。系统会回执「分了 巡视X / 旁站Y / 待定Z，确认后建」并等你确认后**分别建多条记录**（确认前不建）。仅当照片确实跨多种类型才用；单一类型多图不要传 image_groups（直接建一条、多图挂同一条即可）。用户说「确认建 / 建吧」时再次返回 record_work 即可（不必重复传 image_groups，系统已记住分组）。

其他硬约束
----------
- args 只能包含该工具声明的参数；拿不准的参数不要编造，省略即可（服务端会追问缺失项）。
- 🔴 缺失字段由**服务端一次只问一栏**（当前最该补的那一栏），你**不要**在 answer 里逐栏追问、也不要手列候选值、更不要复述「还差 X、还差 Y」清单。接话时只自然转述已确认事实 + 一句「你直接说一声即可」，**严禁**提到任何按钮/选项/表单（本产品是纯文字对话）。🔴 带照片水印/素材且已经能判断是哪类记录时，**必须返回 record_work 动作**，不许因为"信息和水印不一致"就退回纯聊天去汇报差异——冲突由服务端用一句提示承载。验收类型只能从受控枚举里选（隐蔽工程／检验批／进场验收／分项工程验收／分部工程验收／单位工程验收／竣工验收／预验收；「分项分部」为旧值别名），拿不准就用文字问「是哪一类验收」，绝不要自己造类型词（如把「分项分部」说成「分部分部」）。
- section（工程部位/施工区域）只填**用户明确说出来的**具体施工部位（如「3号楼二层东侧临边」）。🔴 上面括号里的字只是示意格式，**不是候选值**：用户没说部位就留空，服务端会追问；严禁按规范/经验列举（例如把「临边洞口防护、高处作业、卸料平台、起重吊装、移动操作平台、模板支撑体系」这类检查项清单当成部位填进来）。用户句中的项目名称（如「巡视XX项目」）也不要填进 section。
- 🔴 用户只回了记录类型（如只答「巡视」「旁站」「验收」）时，**除 record_type 外一个字段都不要填**：不准按经验补部位、工作内容、时间、天气、人数。留空由服务端补齐/追问，编造的字段会直接写进正式文书。
- content（工作内容）只写用户说过的内容；不要把隐患识别结果、检查项清单、规范条目搬进来。
- 记录类栏目口径以**字段表**为准：标「由系统生成=是」的栏（工程项目名称/编号/日期/星期/施工单位/项目章等）由系统带出，**不要追问、也不用传**；标「否」的栏用户没说才要问。表上有、参数清单里没单列的栏目，放进 **extra_fields**（键用服务端给出的字段 key，如 1_13/10_14），值填用户原话。
- 用户补充回答追问时，除了已有参数，也要把该栏目写进 extra_fields 一并传入。
- 用户回复「跳过 / 直接创建 / 不用补」等表示不再补充时，**必须仍返回 record_work**，把历史里已收集到的信息一并填入 facts/args（不要只回文字）。
- 🔴 用户补充回答追问时（如答了类型、补了时间、补了结论），必须把**历史对话里已收集到的全部信息与本次补充合并成完整 args** 一次性传入，然后**立即调用工具**。合并后信息已足够建单，严禁继续追问下一个字段（只传本次提到的字段、或答完又追问，都会导致用户被反复盘问）。
- 你**没有**写库能力，严禁在 answer 里声称「已保存」「已记录」「已生成」「已删除」——落库由服务端执行后回执。
- 用户要换记录类型（如「改成旁站」「这条算验收」）→ 仍返回 record_work，record_type 换成新类型，
  其余已经确认过的部位/时间/内容照抄，不要反问用户重新说一遍。
- 用户说「撤销 / 删掉刚才那条 / 说错了」→ 返回 undo 动作。
- 用户问「今天天气」→ query_weather 动作；问「规范/标准怎么要求」→ query_regulation 动作。
- 🔴 用户只说一个页面名或功能名（如「监理文书」「台账」「查看台账」「待办任务」「巡视记录」）→ 用 navigate 打开该页面，不要追问用户想干什么，也不要当成知识问答。
- 用户说「有没有XX」「查一下XX」「今天记了几条」→ 是**查询**，返回对应 query_* 动作；不是「生成/新建」。
- 输出纯文本，禁止 Markdown 标记（#、*、表格、代码块）。

🔴 图片与素材（最容易编造的地方）
--------------------------------
- 你**看不到图片/照片本身**。只有消息里出现「【图片 N】」素材段（系统已把图交给视觉模型）时，才能据此做内部判断；识别结果不写进 answer。
- 🔴 素材里一旦出现「【水印】」「【图片内容观察】」等识别段：**识别到的内容只保留在你的内部思考里，不需要写出来**——
严禁在 answer 里逐项复述、列「字段名：值」、写「拍摄时间：…／项目：…／施工部位：…／施工内容：…／天气：…」，
也不要把多张图的识别结果按张罗列或拼成一段。识别值由服务端回填到记录栏位、作为证据保存，你只需用一句话说明
「已读到这批照片的水印信息，拍摄时间／地点／部位等已自动带入」；需要核对具体值时，让用户到记录草稿或工作记录页查看。
若业务类型仍不明确，只问一个决定性问题，不能把水印识别失败说成用户没有提供信息。
🔴 用户让你「看 / 识别 / 读」照片（没说要建记录）：同样不写出识别内容，一句话说明已读到位即可、返回 chat。
**若这批照片明显跨多种记录类型**（如同时有验收、旁站、巡视、材料单据等不同场景），应返回 record_work 并带上 image_groups，
让系统回执「分了 巡视X / 旁站Y / 待定Z，确认后建」、经你确认后**分别建多条记录**——不要逼用户选「单一类别」去建一条。
识别≠强制建档，但也不排斥顺带建档。
- **严禁凭空说「经图片识别」「看图发现」「照片显示」**，也不要在没有素材时描述现场状况——那是编造，监理会据此签字。
- 素材里没有识别到隐患，就照实说「素材中未提到隐患」，不要补一句「现场安全状况良好」。
- 🔴 建「巡视」记录时 `problems`（问题及处理情况）**必须填、不能留空**：用户没口述问题时，从各张照片素材的【图片内容观察】里归结——任一张看出问题就按「图N：问题…，处理…」汇总；全部照片都看不出问题才写「经巡视，各部位未发现质量、安全问题（符合要求）」。绝不许因为用户没说就空着，也绝不许没依据硬写「符合要求」（那会变成假的合规记录，多张照片时尤其危险）。
- 用户说「识别水印 / 先识别水印 / 看下水印」：
  · 本轮带素材且素材里有【水印】段 → 一句话说明已读到水印、已自动带入哪些栏目（只说栏目名，不写识别到的具体值），不逐项复述；
  · 本轮**带了素材但没读到【水印】** → 说「这张没读到水印，水印文字要清晰、别被裁掉，麻烦重拍一张或从相册选原图」；
  · 本轮**完全没有素材** → 说：「把带水印的照片发给我（点输入框的「+」选相册或拍照），我先读水印里的拍摄时间和地点。你要发的是哪张？」——**不许说「这张没读到水印」**，你根本没看到图；
  · **不要说「我接收不到图片」「无法识别图片」**。
- 🔴 **用户只发照片、没明确记录类型时**：必须先**分析**——把水印客观信息（拍摄时间/地点/部位/天气/公示牌文字）只放在内部思考里、不写出来，据此判断最可能的记录类型并**给一句依据**（如「按照片里的类型词，建议整理成验收记录」）——最后**请用户确认类别**（一次只问这一个决定性问题，如「确认按验收记录整理吗？还是旁站/巡视？」）。**严禁**直接列出待填字段清单、开多栏表单或建一堆事项——本轮模型返回 tool=null 即可，把分析和类别建议说清、问清类别，用户确认后再逐栏引导。🔴 **「识别 / 看 / 读这些照片」「请识别这 N 张图片」通常是「查看类」请求**：同样不写出识别内容，一句话说明已读到位即可。但**若这批照片明显跨多种记录类型**（同时出现验收、旁站、巡视、材料单据等），**不要**逼用户选「单一类别」去建一条——应返回 record_work 并带 image_groups（按你从水印读出的类型把图分组：image_groups 的每项写 record_type 加 image_indices，例如把第 5、8 张归验收、第 1、3 张归巡视，就传两组，验收配序号 5 和 8、巡视配序号 1 和 3），由系统回执分组并等你确认后分别建多条记录（确认前不会私自建）。确实只想看不想建档时，说明已读到位即可、不调 record_work。用户明确说「记一条 / 建记录 / 整理成记录 / 帮我存」同样返回 record_work（可带 image_groups）。

「记录今天工作情况」这类模糊请求
--------------------------------
- 「记录今天工作情况 / 今天干了什么 / 记一下今天」= 要留痕。
- 🔴 **先判文书、再判素材（顺序不能反）**：如果用户这条消息里是一段**完整的监理日报 / 监理日记 / 监理日志叙述正文**（描述当天施工内容与监理工作，且能抽出日期），**无论是否同时附了照片、水印或其它素材**，都按下面「粘贴叙述性监理日报」的规则返回 `generate_document`——照片只作附件保存，**不得因为带了照片就改走 record_work，更不得抽字段让用户逐项确认项目 / 单位 / 人员**。照片与项目资料对不上（水印是别的项目）时，只在回答里提一句「照片水印项目名与当前项目不一致」，不要把它变成待确认项。
- **图片/水印素材不等于巡视，也不等于任何固定表式**。如果用户只发了照片、没说要建哪类记录，**且素材里也找不到类型词**：把水印客观信息只放在内部思考里、不写出来，据此判断最可能的记录类型并给一句依据，最后在回答里**只问一个决定性问题确认类别**，例如：「按照片里的类型词，建议整理成验收记录，你确认吗？还是旁站/巡视/见证/平行检验？」——**先给建议再确认，不要直接开字段清单**（本轮模型返回 tool=null 即可，问清类别后下一轮再建草稿）🔴 注意：用户只说「识别 / 看 / 读 / 请识别这些照片」通常是查看类请求，同样不写出识别内容；但若照片明显跨多种类型，同样返回 record_work 并带 image_groups 让系统分组建多条（见上文），不要逼用户选一种。素材里已有明确单一类型词时按上文直接定类型、不必再问。
- 用户明确说了记录类型时才填写 record_type 并进入记录候选。用户已经在前文说过日期、地点、时间、工作内容的，必须沿用，不得因图片没有识别到而说成缺失。
- 错误示范（严禁）：直接回一段「今日工作情况结论与建议」，甚至还写「经图片识别」。这是答非所问。
- 用户明确说「生成今天的监理日志 / 月报」时，才返回 generate_document 动作（args.period=day / month）。
- 🔴 **「监理日志」与「监理日记」是两种文书**：「监理日志」＝项目级（聚合全项目当天记录），
  「监理日记」＝**个人级**（只聚合当前用户当天的工作记录/隐患/发文，落款本人）。
  用户说「生成我的监理日记 / 生成今天的监理日记 / 个人日记」时，generate_document 的
  args 带 period="day" **且** form_code="jianli_riji"（或直接写「监理日记」）；说「监理日志」
  时不带 form_code，走项目级聚合。两者不要混。
- 「安全监理日志 / 安全日志」是**独立表式**，不是普通监理日志：返回 generate_document 时 period=day 且必须同时带 args.form_code="anquan_jianli_rizhi"，否则会被静默生成成普通监理日志。
- 🔴 用户**直接粘贴 / 口述一段叙述性「监理日报 / 监理日记 / 监理日志」文字**（描述当天施工与监理工作，而非点名某张省统表）时，**即使同一条消息还附了照片 / 水印素材也一样**，也返回 generate_document 动作：args 填 period="day"，并从文字里抽出日期写进 args.date（如「10月27日」→ "2026-10-27"）；若是安全监理日志再带 form_code="anquan_jianli_rizhi"。🔴 **严禁在此场景返回 create_provform_doc**——那是新建省统表模板，会被必填项校验卡成「该表还不能生成」；监理日志由日报生成服务聚合 / 起草，你不需要抽任何字段，用户原文会作为起草素材自动带入。

🔴 上述「叙述性文字严禁进 create_provform_doc」的精神适用于**所有省统表**：只要用户发的是一段描述现场 / 工作或记录 / 日志的**叙述性自由文字**、没有明确点名要建哪张省统表表式，就**严禁返回 create_provform_doc**（必填项校验会卡成「该表还不能生成」）。按意图分流：要留痕 → record_work；要出监理文书 → generate_document；只是陈述 / 咨询 → chat。只有用户**明确说出表式名或「建 / 新建 / 生成 XX 表」**（如「建一份监理工程师通知单」「生成旁站记录表」）时才返回 create_provform_doc。

🔴 **例外——续接未完成草稿优先于上面的守门**：如果系统上下文里已给出「未完成草稿 / 仍未收集的字段」（即上一轮你已为某张省统表追问过缺哪几栏），而用户这句正是在补这些栏（描述现场情况、工作内容等），必须**续接上一轮同一个工具**（create_provform_doc）把这段文字填进对应 fields / extra_fields，**不要改投 record_work，也不要重复问已经给过的内容**。守门只拦「凭空新建一张表」，不拦「补全自己刚开的那张表的草稿」。

【长期偏好沉淀——只有这一种情况才输出标记】
当用户这句话是在**提一条「以后都要照这样办」的工作规矩**（例如「整改回复必须附前后对比照片」「结论要写具体数字」「旁站记录一律写时间点」），**并且此前对话里用户已经表达过同类要求**时，在回复正文的最后**另起一行**输出：
<<PREF 规矩原文|分类>>
分类只能是 general / rectification / record / writing 之一。
这条标记只供系统解析，**不要在正文里解释它、不要说「我已记住」、不要提及偏好**。
除上述情况外——普通提问、本次任务的临时要求、首次提出、闲聊——**一律不要输出该标记**。"""


# ---------------------------------------------------------------------------
# 水印/素材预填：把照片水印里客观确定的字段直接写进记录，缺的才问用户
# （2026-10-19 用户反馈：发带水印的照片 + 「记录今天工作」，系统却什么都没填就反问类型）
# ---------------------------------------------------------------------------
# 水印拍摄时间 → 该类型的「开始时间」参数（表字段 arg 名）
_PREFILL_START_ARG = {"巡视": "inspection_start_time", "旁站": "station_start_time"}
_TIME_RE = re.compile(r"(\d{1,2})\s*[:：时]\s*(\d{2})")
_DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")


def _hms(value: Any) -> str:
    """水印拍摄时间文本 → HH:MM；取不到返回空串。"""
    m = _TIME_RE.search(str(value or ""))
    if not m:
        return ""
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _iso_date(value: Any) -> str:
    """水印拍摄时间文本 → YYYY-MM-DD；取不到返回空串。"""
    m = _DATE_RE.search(str(value or ""))
    if not m:
        return ""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def apply_prefill(
    args: dict,
    prefill: dict,
    record_type: str,
    *,
    protected_keys: set[str] | None = None,
) -> tuple[dict, list[str]]:
    """把水印等客观字段并入建记录参数，返回（新 args，已自动填入的说明）。

    水印/EXIF 对日期、地点、部位、天气等客观栏拥有更高优先级；用户转述
    有冲突时采用照片证据。拍摄时间对“开始时间”只是推断，存在用户明确
    时段时仍保留用户值。用户在表单中主动修改的键通过 protected_keys 保护。
    """
    out = dict(args or {})
    filled: list[str] = []
    protected = protected_keys or set()

    def put(key: str, value: Any, label: str, *, objective: bool = False) -> None:
        if not key:
            return
        text = str(value if value is not None else "").strip()
        if not text or key in protected:
            return
        existing = str(out.get(key) or "").strip()
        if existing:
            if not objective or existing == text:
                return
            out[key] = text
            filled.append(f"{label}（改用照片水印）：{text}")
            return
        out[key] = text
        filled.append(f"{label}：{text}")

    put(
        "record_date",
        prefill.get("record_date") or _iso_date(prefill.get("taken_at")),
        "日期",
        objective=True,
    )
    put("location", prefill.get("location"), "地点", objective=True)

    section = str(prefill.get("section") or "").strip()
    if section:
        if record_type == "旁站":
            put("station_part", section, "关键部位", objective=True)
            if "section" not in protected:
                out["section"] = section
        else:
            put("section", section, "工程部位", objective=True)

    put("weather", prefill.get("weather"), "天气", objective=True)

    hhmm = _hms(prefill.get("taken_at"))
    if hhmm:
        put(
            _PREFILL_START_ARG.get(record_type, ""),
            hhmm,
            "开始时间（水印拍摄时间）",
        )

    return out, filled


def _auto_filled_summary(filled: list[str]) -> str:
    """自动填入回执只列栏目名，不写水印识别到的具体值（2026-11-16 用户口径）。

    ``apply_prefill`` 返回的说明形如
    ``["日期（改用照片水印）：2026-10-27", "地点：沙盛四路等4条道路工"]``，
    过去整串拼进 reply 会把日期/地点/天气等识别值全念一遍。识别值仍留在
    ``args`` 里用于落库和证据，只是不再出现在对话文本中。

    🔴 必须保留「自动填入」四个字：小程序 ``index.ts`` 靠它判断服务端已经
    回执过、不再把本地水印清单拼一遍（旧版客户端兼容）。
    """
    labels: list[str] = []
    for item in filled or []:
        label = str(item).split("：", 1)[0].split(":", 1)[0].strip()
        label = label.replace("（改用照片水印）", "").strip()
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return "已从照片水印自动填入部分栏位"
    return ("已从照片水印自动填入：" + "、".join(labels)
            + "（具体值已带入记录栏位，可在草稿中核对）")


def _history_messages(history: list[dict], limit: int = 6) -> list[dict]:
    """把前端历史压成模型消息（只保留最近 limit 轮，忽略卡片等非文本内容）。"""
    out: list[dict] = []
    for item in (history or [])[-limit:]:
        role = str(item.get("role") or "").strip()
        content = str(item.get("text") or item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        out.append({"role": role, "content": content[:500]})
    return out


_CITE_KIND_MAP = {
    "knowledge_doc": "knowledge",
    "work_record": "work_record",
    "document": "document",
    "note": "note",
    "kg_entity": "knowledge",
    "kg_checkpoint": "knowledge",
    "severe_defect": "regulation",
}
_CITE_KIND_LABEL = {
    "knowledge": "知识库",
    "work_record": "工作记录",
    "document": "监理文书",
    "note": "笔记",
    "regulation": "规范条文",
}
_CITE_TARGET = {
    "knowledge": "/knowledge-base",
    "work_record": "/work-record/inspection",
    "document": "/documents",
    "note": "/knowledge-base",
    "regulation": "/knowledge-base",
}

# 指代类追问（这个/这份/这样/它/刚才…）：没有上一轮结果卡时不要硬检索
_FOLLOWUP_REFERENCE_RE = re.compile(
    r"(这个|这份|这条|那份|那个|它|上面|上述|刚才|这样|那样|这里|那里|该文|该表|该份)"
)


def _plain_persona(mode: str | None) -> str:
    persona = "你是广东建设智慧监理工程监理智能助手，请给出准确、可执行、简洁的回答。"
    if mode == "hazard":
        persona = "你是AI安全隐患识别专家。若用户描述现场情况，请判断是否存在隐患并给出风险类别、主要风险、证据与复查建议。"
    persona += ("你不具备直接保存或创建记录的能力，不要声称已保存；"
                "需要留痕时引导用户到「工作记录」页点「+新建」，或直接说「记一条XXX」由系统落库。"
                "你看不到图片本身，只有用户消息里给出图片识别结果时才能引用；"
                "禁止凭空声称「经图片识别」「看图发现」「照片显示」。")
    return persona


def _plain_messages(message: str, history: list[dict], mode: str | None,
                    cite_block: str = "", context_hint: str = "",
                    require_context_hint: bool = False) -> list[dict]:
    persona = _plain_persona(mode)
    if context_hint:
        persona += (
            "\n\n【上一轮助手结果】上一轮助手返回过这些结果（用户说「这个 / 这份 / 这条 / 它」时，"
            "优先指代其中最近的一个；问「为什么这样写」时先点明指的是哪份，再解释）：\n"
            + context_hint
        )
    if require_context_hint:
        persona += (
            "\n\n用户这句话用了指代（这个/这份/这条/它/刚才/上面），但系统没有可对应的上一轮结果卡。"
            "如果对话历史里也找不到明确对象：只请用户指明他指的是什么，不要猜测、不要展开其他话题、"
            "不要引用任何材料。"
        )
    if cite_block:
        persona += (
            "\n\n下面是系统从项目资料/规范库检索到的参考材料，材料编号就是引用号。"
            "回答时只要用到了某条材料，就在那句话末尾标注 [[编号]]（如 [[1]]）；"
            "不要编造材料里没有的依据，材料不足就直说没查到。\n\n" + cite_block
        )
    messages = [{"role": "system", "content": persona + _PLAINTEXT_SUFFIX}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": message})
    return messages


async def _generate_plain(messages: list[dict], provider: str | None, model: str | None) -> str:
    """回答路径统一出口：开深度思考（thinking=True），让回答更聪明。

    🔴 只影响「回答模型」：路由模型（JSON 规划）在 _agent_chat_impl 里显式
    thinking=False 保持快速，不泄漏思考过程；这里只负责给用户的正文回答。
    """
    if _streaming_enabled():
        parts: list[str] = []
        async for event in model_gateway.stream_chat_completion(
            messages, temperature=0.7, provider=provider, model=model,
            thinking=True,
        ):
            if event["type"] == "reasoning":
                await _emit_stream({"type": "reasoning", "text": event["text"]})
            elif event["type"] == "content":
                parts.append(event["text"])
                await _emit_stream({"type": "answer", "text": event["text"]})
        return strip_markdown("".join(parts))
    answer, _cfg = await model_gateway.chat_completion(
        messages, temperature=0.7, provider=provider, model=model,
        thinking=True,
    )
    return strip_markdown(answer or "")


async def _plain_answer(message: str, history: list[dict], provider: str | None, model: str | None,
                        mode: str | None) -> str:
    """兜底：走纯问答（与原 /local/llm/chat 行为一致）。"""
    return await _generate_plain(_plain_messages(message, history, mode), provider, model)


def _mention_scope(mentions: list[dict], resolved_paths: set[str]) -> set[str] | None:
    """把 mentions 归纳成 rag 检索的路径白名单语义。

    三种情况必须区分开，否则用户会在「选了东西反而查不到」上栽跟头：

    * 只选了模糊词（kb_query）→ ``None``：不限定范围，模糊词只作为检索查询词生效；
    * 选了文件夹/文档且有命中 → 命中路径集合：只在这些资料里检索；
    * 选了文件夹/文档但一篇文档都没解析出来（空文件夹）→ **空集合**：
      刻意不外扩。此时宁可让检索空转、由模型追问，也不要偷偷搜全库并冒充"依据"。
    """
    has_scope_item = any(
        str(item.get("type") or "") in {"kb_folder", "kb_doc"}
        for item in mentions or []
        if isinstance(item, dict)
    )
    if not has_scope_item:
        return None
    return set(resolved_paths)


def _retrieve_citations(message: str, project_id: int | None,
                        top_k: int = 5, min_score: float = 0.0,
                        paths: set[str] | None = None) -> tuple[list[dict], str]:
    """针对用户问题做一次混合检索，产出引用条目与资料块（失败静默降级为空）。

    ``paths`` 是 @ 面板选中的资料库白名单（相对 ``knowledge_base_path`` 的路径）；
    非空时只在白名单内检索，等价于「在这几个文件夹/文件里找依据」。
    """
    try:
        from app.services import rag_index

        hits = rag_index.search(
            message, top_k=top_k, project_id=project_id, paths=paths,
        ) or []
    except Exception:  # noqa: BLE001 —— 检索不可用不应阻断聊天
        logger.warning("引用检索失败", exc_info=True)
        return [], ""
    citations: list[dict] = []
    blocks: list[str] = []
    seen_titles: set[str] = set()
    for hit in hits:
        try:
            score = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score < min_score:
            continue
        title = str(hit.get("source_name") or "").strip()
        snippet = str(hit.get("snippet") or hit.get("content") or "").strip().replace("\n", " ")
        if not title or not snippet:
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        index = len(citations) + 1
        kind = _CITE_KIND_MAP.get(str(hit.get("source_type") or ""), "knowledge")
        citations.append({
            "index": index,
            "kind": kind,
            "title": title[:120],
            "sub": _CITE_KIND_LABEL.get(kind, "资料"),
            "snippet": snippet[:220],
            "target": _CITE_TARGET.get(kind),
            "ref_id": hit.get("source_id"),
        })
        blocks.append(f"【材料{index}】{title}\n{snippet[:300]}")
    return citations, "\n\n".join(blocks)


def _doc_excerpt(doc: Document | None) -> str:
    """文书 content（字段字典）压成一行摘要，给模型解释「为什么这样写」用。"""
    if doc is None or not isinstance(doc.content, dict):
        return ""
    parts: list[str] = []
    for key, value in doc.content.items():
        if len(parts) >= 10:
            break
        if value is None or isinstance(value, (list, dict)):
            continue
        text = str(value).strip()
        if not text or text in ("无", "待补", "/"):
            continue
        parts.append(f"{key}={text[:40]}")
    return "；".join(parts)[:500]


async def _last_result_context(db: AsyncSession, user_id: Any, conversation_id: str | None,
                               project_id: int | None) -> tuple[str, str]:
    """取上一轮助手回执里的结果卡，作为指代消解上下文。

    返回 (给模型看的结果摘要, 检索时补充的关键词)。没有上一轮或没有卡片时返回空。
    """
    if not conversation_id:
        return "", ""
    stmt = (
        select(AgentConversationMessage)
        .where(
            AgentConversationMessage.user_id == int(user_id),
            AgentConversationMessage.conversation_id == conversation_id,
            AgentConversationMessage.role == "assistant",
        )
        .order_by(AgentConversationMessage.id.desc())
        .limit(1)
    )
    if project_id is None:
        stmt = stmt.where(AgentConversationMessage.project_id.is_(None))
    else:
        stmt = stmt.where(AgentConversationMessage.project_id == int(project_id))
    row = (await db.execute(stmt)).scalars().first()
    if row is None:
        return "", ""
    try:
        content = json.loads(row.content_json or "{}")
    except (TypeError, ValueError):
        return "", ""
    response = content.get("response") if isinstance(content, dict) else None
    if not isinstance(response, dict):
        return "", ""
    cards = response.get("cards") if isinstance(response.get("cards"), list) else []
    if not cards and isinstance(response.get("card"), dict):
        cards = [response["card"]]
    lines: list[str] = []
    titles: list[str] = []
    doc_excerpts: list[str] = []
    for card in cards[:5]:
        if not isinstance(card, dict):
            continue
        kind = str(card.get("kind") or "")
        title = str(card.get("title") or "").strip()
        sub = str(card.get("sub") or "").strip()
        if not title:
            continue
        titles.append(f"{title} {sub}".strip())
        lines.append(f"- {kind} #{card.get('id')}：{title}（{sub or kind}，{card.get('status') or ''}）")
        if kind == "document":
            try:
                doc = await db.get(Document, int(card.get("id") or 0))
            except (TypeError, ValueError):
                doc = None
            if doc is not None and (project_id is None or doc.project_id == project_id):
                excerpt = _doc_excerpt(doc)
                if excerpt:
                    doc_excerpts.append(f"《{doc.title}》(#{doc.id}, {doc.doc_type}, {doc.status}) 内容：{excerpt}")
    if not lines:
        return "", ""
    hint = "\n".join(lines[:5])
    if doc_excerpts:
        hint += "\n文书内容摘要：" + " | ".join(doc_excerpts[:2])
    return hint[:1200], " ".join(titles[:3])


async def _plain_answer_cited(message: str, history: list[dict], provider: str | None,
                              model: str | None, mode: str | None,
                              project_id: int | None,
                              *, context_hint: str = "",
                              query_extra: str = "",
                              require_context: bool = False,
                              scope_paths: set[str] | None = None,
                              scope_query: str = "") -> tuple[str, list[dict]]:
    if require_context:
        # 有指代但系统没有可对应的结果卡：不检索、不引用，让模型只追问
        citations: list[dict] = []
        block = ""
    else:
        extra = " ".join(part for part in (query_extra, scope_query) if part).strip()
        query = f"{message} {extra}".strip() if extra else message
        citations, block = _retrieve_citations(
            query, project_id, min_score=0.3, paths=scope_paths,
        )
    messages = _plain_messages(
        message, history, mode, cite_block=block, context_hint=context_hint,
        require_context_hint=require_context,
    )
    answer = await _generate_plain(messages, provider, model)
    # 只保留模型真正引用过的材料；它没引用就不展示「引用」列表（避免检索噪声冒充依据）
    used = {int(num) for num in re.findall(r"\[\[(\d+)\]\]", answer or "")}
    if used:
        citations = [item for item in citations if int(item.get("index") or 0) in used]
    else:
        citations = []
    return answer, citations


_SUPPLEMENT_SYSTEM = (
    "用户在补全一条待办理事项的字段。只输出一个 JSON 对象，键为参数名、值为从用户这句话能确定的参数。"
    "优先填补「仍缺字段」：这句话只要能当作某个仍缺字段的值（如部位、内容、问题、描述类字段），"
    "就把整句或提炼后的短语填入该字段；确实与仍缺字段无关的才省略。"
    "不要编造，不要输出其它文字。"
)

# 命中即不把这句话当「补充回答」（提问 / 找页面 / 闲聊）——避免中途问规范被当成补字段
_SUPPLEMENT_SKIP_HINTS = ("？", "?", "吗", "怎么", "如何", "为什么", "是什么", "多少",
                          "打开", "你好", "谢谢")


async def _extract_supplement(message: str, tool: str, draft: dict,
                              provider: str | None, model: str | None) -> dict:
    """把「补充回答」单独抽成参数：路由器没选工具、但存在未完成草稿时用。失败返回 {}。"""
    spec = agent_tools.TOOL_BY_NAME.get(tool)
    if not spec:
        return {}
    params = "、".join(f"{k}={v}" for k, v in spec.params.items())
    prompt = (
        f"工具：{tool}\n参数说明：{params}\n"
        f"已收集：{json.dumps(draft.get('args') or {}, ensure_ascii=False)}\n"
        f"仍缺字段（含该填到哪个参数/槽位）："
        f"{agent_tools.ask_hint(draft.get('args') or {}, draft.get('missing'))}\n"
        f"用户这句话：{message}"
    )
    try:
        raw, _cfg = await model_gateway.chat_completion(
            [{"role": "system", "content": _SUPPLEMENT_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.1, provider=provider, model=model,
            response_format={"type": "json_object"},
        )
    except model_gateway.ModelGatewayError:
        return {}
    data = agent_tools._parse_json_object(raw) or {}
    if isinstance(data.get("args"), dict):  # 兼容 {"args": {...}} 包一层
        data = data["args"]
    return {k: v for k, v in data.items()
            if str(v if v is not None else "").strip()}


async def _generate_request_plan(
    *,
    message: str,
    evidence_summary: str = "",
    evidence: list[dict[str, Any]] | None = None,
    attachment_count: int = 0,
    file_count: int = 0,
    history: list[dict] | None = None,
    provider: str | None = None,
    model: str | None = None,
    system_facts: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str]:
    """Ask for semantic candidates, then pass them through the server gate."""
    evidence_items = [item for item in (evidence or []) if isinstance(item, dict)]
    evidence_count = len(evidence_items) + max(0, file_count)
    if not evidence_summary and evidence_items:
        evidence_summary = "\n".join(
            str(item.get("note") or item.get("observation") or "")
            for item in evidence_items
            if str(item.get("note") or item.get("observation") or "").strip()
        )
    prompt = agent_request_plan.build_prompt(
        message, evidence_summary, evidence_items, history,
    )
    try:
        raw, config = await model_gateway.chat_completion(
            [
                {
                    "role": "system",
                    "content": (
                        "你是监理智能体的语义计划器。只输出候选 JSON；"
                        "不得执行、写入或签发任何业务数据。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            provider=provider,
            model=model,
            response_format={"type": "json_object"},
        )
        candidate = agent_tools._parse_json_object(raw) or {}
        plan = agent_request_plan.normalize_candidate(
            candidate,
            message=message,
            attachment_count=max(0, attachment_count),
            evidence_count=evidence_count,
            evidence=evidence_items,
            system_facts=system_facts,
        )
        plan = agent_request_plan.enrich_plan_facts(
            plan,
            extract_explicit_message_facts(message, message_ref=message),
            evidence=evidence_items,
            system_facts=system_facts,
        )
        return plan, config.model_id
    except (model_gateway.ModelGatewayError, TypeError, ValueError) as exc:
        logger.warning("请求计划器不可用: %s", type(exc).__name__)
        plan = agent_request_plan.degraded_plan(
            attachment_count=max(0, attachment_count),
            evidence_count=evidence_count,
            reason=type(exc).__name__,
        )
        plan = agent_request_plan.enrich_plan_facts(
            plan,
            extract_explicit_message_facts(message, message_ref=message),
            evidence=evidence_items,
            system_facts=system_facts,
        )
        return plan, ""


@router.post("/request-plan")
async def request_plan(
    req: RequestPlanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build a validated, non-executing plan for text and uploaded evidence."""
    message = (req.message or "").strip()
    evidence = [item.model_dump() for item in req.evidence[:20]]
    attachment_count = max(
        0,
        req.attachment_count or sum(1 for item in evidence if item.get("kind") == "image"),
    )
    request_id = await agent_runtime.start_request(
        request_id=req.request_id,
        user_id=user.id,
        project_id=getattr(user, "project_id", None),
        conversation_id=None,
        endpoint="/api/agent/request-plan",
        stage="request_plan",
        message=message or req.evidence_summary,
        provider=req.provider or "",
        model=req.model or "",
    )
    try:
        project = await db.get(Project, user.project_id) if getattr(user, "project_id", None) else None
        project_facts = project_context(project)
        system_facts = agent_fact_model.build_system_facts(
            project_name=project_facts["project_name"],
            construction_unit=project_facts["construction_unit"],
            supervision_unit=project_facts["supervision_unit"],
            supervision_org=project_facts["supervision_org"],
            inspector=user.real_name or "",
        )
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=int(user.id),
            project_id=getattr(user, "project_id", None),
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            status="running",
            title="正在调用规划模型，根据用户原话和已提取的图片/文件证据生成候选计划",
            detail={
                "image_count": attachment_count,
                "file_count": max(0, req.file_count),
                "evidence_count": len(evidence),
                "model": req.model or "系统选择的模型",
            },
        )
        plan, model_id = await _generate_request_plan(
            message=message,
            evidence_summary=req.evidence_summary,
            evidence=evidence,
            attachment_count=attachment_count,
            file_count=max(0, req.file_count),
            history=req.history,
            provider=req.provider,
            model=req.model,
            system_facts=system_facts,
        )
        plan["model"] = model_id
        plan["request_id"] = request_id
        # 🔴 A 层零必填（jl 零必填）：带图 + 「你先记着」+ 类型不明时，前端
        # sendAttachments 的 wantsRecordPlan 会命中 → 走 /agent/review-plans
        # 「候选事项/待确认草稿」，一路追问记录类型，永远到不了 /agent/chat。
        # 这里把 plan 改成「无候选动作 + 非 clarify」，前端自然落到
        # runAgentWithMaterial → /agent/chat；stash_as_pending 让 chat 无条件落待定。
        _plan_has_images = attachment_count > 0 or any(
            str(item.get("kind") or "").strip() == "image" for item in evidence
        )
        if agent_request_plan.apply_zero_required_stash(plan, message, _plan_has_images):
            logger.info("A层零必填：request-plan 改写为直达 chat 落待定")
        planned_facts = [
            {"field": item.get("field"), "value": item.get("value"), "source": item.get("source")}
            for item in plan.get("facts", []) if isinstance(item, dict)
        ]
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=int(user.id),
            project_id=getattr(user, "project_id", None),
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            status="completed",
            title=(
                "结构化判断完成："
                + (str(plan.get("record_type") or "记录类型待确认"))
                + ("候选" if plan.get("actions") else "，需确认处理用途")
            ),
            detail={
                "intent": plan.get("intent"),
                "actions": plan.get("actions"),
                "facts": planned_facts[:32],
                "conflicts": plan.get("field_conflicts", [])[:16],
                "clarifications": plan.get("clarifications", [])[:12],
                "degraded": bool(plan.get("degraded")),
                "degraded_reason": (plan.get("trace") or {}).get("degraded_reason"),
                "model": model_id,
            },
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user.id,
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            request_plan=plan,
            result={"intent": plan.get("intent"), "actions": plan.get("actions")},
            model=model_id,
        )
        return plan
    except Exception as exc:
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user.id,
            endpoint="/api/agent/request-plan",
            stage="request_plan",
            status="failed",
            error=exc,
        )
        raise


@router.post("/recommend-purpose")
async def recommend_purpose(
    req: PurposeRecommendRequest,
    user: User = Depends(get_current_user),
):
    """Suggest a workflow from user intent and evidence; never creates a record.

    🔴 必须登录：本接口内部会真实调用大模型（付费），历史上漏了鉴权，
    等于把「匿名刷额度」的入口开在公网上。小程序调用方本来就带 Bearer，
    加鉴权不影响正常使用。
    """
    evidence_count = 1 if req.evidence_summary.strip() else 0
    plan, model_id = await _generate_request_plan(
        message=req.message,
        evidence_summary=req.evidence_summary,
        attachment_count=max(0, req.attachment_count or evidence_count),
        evidence_count=evidence_count,
        provider=req.provider,
        model=req.model,
    )
    legacy = agent_request_plan.plan_to_legacy_purpose(plan)
    legacy["model"] = model_id
    if plan.get("degraded"):
        legacy["degraded"] = True
    return legacy


async def _apply_pending_type_change(
    db: AsyncSession, user: User, project_id: int | None, new_type: str,
) -> dict | None:
    """把当前项目里最近一条「待定」记录改成具体类型。

    与 ``POST /api/work-record/{id}/set-type`` 同语义（类型校验、创建者/权限、
    content_json.type_history 都一致），但不走 HTTP、不自动生成省统表——
    产品口径是「工作记录与正式表单解耦，表式由用户在详情里按需选择」。

    Returns: 改成功 → 回执 dict；没有可改的待定记录或无权限 → None（交回模型路由）。
    """
    if not project_id or new_type not in {t.value for t in WorkRecordType}:
        return None
    res = await db.execute(
        select(WorkRecord)
        .where(
            WorkRecord.project_id == project_id,
            WorkRecord.record_type == "待定",
            WorkRecord.status != WorkRecordStatus.ARCHIVED.value,
        )
        .order_by(WorkRecord.id.desc())
        .limit(1)
    )
    record = res.scalar_one_or_none()
    if record is None:
        return None
    if record.creator_id != user.id and not user.has_permission("task_manage"):
        return None
    old_type = record.record_type
    record.record_type = new_type
    cj = dict(record.content_json or {})
    history = list(cj.get("type_history") or [])
    history.append({
        "from": old_type, "to": new_type,
        "by": user.real_name or user.username,
        "at": datetime.now().isoformat(timespec="seconds"),
        "source": "agent_chat",
    })
    cj["type_history"] = history
    record.content_json = cj
    await db.commit()
    logger.info("待定记录 #%s 类型 %s → %s（对话快捷指令）", record.id, old_type, new_type)
    return {
        "ok": True, "record_id": record.id, "record_type": new_type,
        "reply": (
            f"已把刚才那条待定记录改成「{new_type}」（记录 #{record.id}）。"
            "\n类型定了以后，到「工作记录」页点开这条记录，用「按需生成表单」选对应表式即可。"
            "\n要换别的类型，直接回「改成旁站」这类话就行。"
        ),
    }


# 「前面创建的 / 刚建的 / 这条记录」等明确指向已建记录的说法
_EXISTING_RECORD_HINTS = (
    "前面创建", "刚创建", "刚才创建", "刚建", "已创建", "已建", "这条记录",
    "该记录", "上面那条", "前面那条", "上一条记录", "前面建的",
)


def _record_id_from_text(*parts: Any) -> int | None:
    """从本轮消息/历史里取最近一次出现的「#126」式记录编号。"""
    found: list[int] = []
    for part in parts:
        for m in re.finditer(r"#\s*(\d{1,9})", str(part or "")):
            found.append(int(m.group(1)))
    return found[-1] if found else None


def _refers_to_existing_record(message: str, *contexts: Any) -> bool:
    text = str(message or "")
    if any(h in text for h in _EXISTING_RECORD_HINTS) or _record_id_from_text(text):
        return True
    # 「写入正式记录 / 请写入验收记录」本身不带指代，但同一会话（历史原话或
    # 前端 history）里出现过 #126 → 视为延续同一条记录，避免再新建一条重复的。
    return _record_id_from_text(*contexts) is not None


# 2026-11-17 用户截图「还有其他的没填呢，怎么不跟用户交互」：
# 「写入正式记录」只授权把已确认内容落库，不再等于「其余必填栏一律留空」。
# 需要用户口述的栏目仍要一问一答；只有明确说留空/跳过才真的留空。
_WRITE_THROUGH_STATE_KEY = "_write_through_fields"
_LEAVE_BLANK_HINTS = (
    "先留空", "都留空", "其余留空", "栏位留空", "先不填", "都不填",
    "不用填", "暂不填", "跳过",
)

# 🔴 2026-11-17：写入后逐栏追问必须「有界 + 单调推进」。
# 同一栏最多「没对上」2 次，第 2 次自动跳过并记为留空；跳过的栏不再回到追问列表。
# 任何分支都必须让 labels 变短或 attempts+1，杜绝「同一栏 + 同一句回复」死循环。
_WRITE_THROUGH_MAX_ATTEMPTS = 2

# 通用填写口径：只给格式与占位示例，绝不带出本轮水印 / OCR / 工序识别值
# （用户 11-16「AI 对话不复述识别/匹配值」）。也不下发候选卡、表单卡。
_FIELD_FILL_HINTS: tuple[tuple[str, str], ...] = (
    ("日期", "按「年-月-日」给日期"),
    ("时间", "按「年-月-日 时:分」给到时刻，也可以只说上午/下午"),
    ("天气", "说天气加气温"),
    ("人数", "直接给数字"),
    ("部位", "按「楼栋 / 楼层 / 构件」说，如「X#楼 X 层 X 构件」"),
    ("位置", "按「楼栋 / 楼层 / 构件」或桩号区间说"),
    ("结论", "直接给一句结论"),
    ("单位", "给单位全称"),
    ("类型", "直接说类型名称，一个词就行"),
    ("内容", "直接说现场情况，不用带栏名"),
)

_FIELD_QUESTION_HINTS = (
    "怎么填", "如何填", "填什么", "写什么", "怎么写", "是什么", "什么意思",
    "啥意思", "举例", "例如", "例子", "哪些", "为什么", "怎么样",
    "吗？", "吗?", "？", "?",
)


def _is_field_question(message: str) -> bool:
    """用户在问「这一栏怎么填」而不是在给值；这类句子绝不写进字段。"""
    text = str(message or "").strip()
    return bool(text) and any(h in text for h in _FIELD_QUESTION_HINTS)


def _field_fill_hint(label: str) -> str:
    """按栏目给通用填写口径（占位示例，不含本轮任何识别值）。"""
    for key, hint in _FIELD_FILL_HINTS:
        if key in str(label or ""):
            return hint
    return "直接说这一栏的内容，不用带栏名"


def _write_through_state_full(args: dict | None) -> dict:
    """完整续办状态；兼容旧草稿里只有 {record_id, labels} 的结构。"""
    raw = (args or {}).get(_WRITE_THROUGH_STATE_KEY)
    state: dict[str, Any] = {
        "record_id": None, "labels": [], "attempts": {}, "skipped": [],
    }
    if isinstance(raw, dict):
        try:
            state["record_id"] = int(raw.get("record_id"))
        except (TypeError, ValueError):
            state["record_id"] = None
        state["labels"] = [str(x) for x in (raw.get("labels") or []) if str(x).strip()]
        attempts = raw.get("attempts")
        if isinstance(attempts, dict):
            state["attempts"] = {
                str(k): int(v) for k, v in attempts.items()
                if str(k).strip() and str(v).isdigit()
            }
        state["skipped"] = [str(x) for x in (raw.get("skipped") or []) if str(x).strip()]
    elif isinstance(raw, (list, tuple)):
        state["labels"] = [str(x) for x in raw if str(x).strip()]
    return state


def _short_label_fields(form_code: str | None) -> dict[str, dict]:
    """详情页短标签 → 字段表字段。

    详情页展示用 _shorten_label 截短/替换过的短标签，字段表却按原始 label 反查，
    长栏目必然对不上 → 用户答了也写不进库 → 反复问同一栏。这里建双向名称映射。
    """
    out: dict[str, dict] = {}
    if not form_code:
        return out
    for field in agent_tools.record_field_spec.fields_of(form_code):
        raw = str(field.get("raw_label") or field.get("label") or "").strip()
        short, _ = work_record_field_view._shorten_label(raw)
        for name in (short, raw, str(field.get("label") or "").strip()):
            if name:
                out.setdefault(name, field)
    return out


def _wants_leave_blank(message: str) -> bool:
    return any(hint in str(message or "") for hint in _LEAVE_BLANK_HINTS)


def _looks_like_field_answer(label: str, message: str) -> bool:
    """续办轮别把闲聊当栏位值（「今天天气不错」不能塞进验收类型）。"""
    text = str(message or "").strip()
    if not text or len(text) > 120:
        return False
    if any(h in text for h in ("天气", "你好", "谢谢", "几点", "为什么", "是什么", "怎么样")):
        return False
    if "验收类型" in str(label or ""):
        return any(k in text for k in (
            "隐蔽", "检验批", "进场", "分项", "分部", "单位工程",
            "竣工", "预验收", "验收",
        ))
    return True


def _write_through_state(args: dict | None) -> tuple[int | None, list[str]]:
    """从草稿里取「正在补哪一栏」的状态，供下一轮续办。"""
    state = _write_through_state_full(args)
    return state["record_id"], state["labels"]


def _write_through_missing_labels(record: WorkRecord, cj: dict) -> list[str]:
    """详情页判定为「未填写」的必填栏，排除照片/签章这类聊天填不了的栏。

    与小程序详情页共用 work_record_field_view.build_field_sections，
    避免对话里问的栏和详情页标红的栏对不上。
    """
    detail = {k: v for k, v in (getattr(record, "__dict__", {}) or {}).items()
              if not str(k).startswith("_")}
    detail["content_json"] = cj
    detail["acceptance_points_text"] = split_record_confirm.acceptance_points_text(
        cj.get("acceptance_points"))
    sections = work_record_field_view.build_field_sections(
        str(record.record_type or ""), detail, cj)
    out: list[str] = []
    for section in sections:
        for field in section.get("fields") or []:
            if not field.get("missing"):
                continue
            label = str(field.get("label") or "").strip()
            if not label or any(k in label for k in ("照片", "签章", "签字")):
                continue
            if label not in out:
                out.append(label)
    return out


async def _apply_existing_record_write_through(
    db: AsyncSession, user: User, project_id: int | None,
    conversation_id: str | None, message: str,
    draft_args: dict | None, *contexts: Any,
    record_id_hint: int | None = None,
) -> dict | None:
    """「把验收要点补充到前面创建的验收记录里 / 写入正式记录 #126」→ 更新已建记录。

    2026-11-16 真机录屏：6 条记录（含验收 #126）已由带图链路建好，用户随后
    要求补要点并连说「写入正式记录」，但 Agent 工具表只有 create_work_record，
    每次都被路由成一份**新的**待确认草稿，必填缺项又拦住确认 → 无限追问。
    这里做确定性短路：定位到已建记录，把草稿里已确认的字段合并进去并清草稿。
    找得到才处理；找不到返回 None，交回原流程（不阻断其它场景）。
    """
    if not project_id:
        return None
    # 续办轮用户只回「隐蔽工程」，消息里没有 #编号 → 用草稿里的写入态定位
    record_id = record_id_hint or _record_id_from_text(message, *contexts)
    if record_id:
        record = await db.get(WorkRecord, record_id)
        if record is None or record.project_id != project_id:
            return None
    else:
        args = draft_args if isinstance(draft_args, dict) else {}
        rtype = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
        # 没有编号且草稿类型不明时不猜目标，交回原流程（避免误改无关记录）。
        if not rtype or rtype == "待定":
            return None
        stmt = select(WorkRecord).where(
            WorkRecord.project_id == project_id,
            WorkRecord.creator_id == user.id,
            WorkRecord.record_type == rtype,
            WorkRecord.status != WorkRecordStatus.ARCHIVED.value,
        ).order_by(WorkRecord.id.desc()).limit(1)
        record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    if record.creator_id != user.id and not user.has_permission("task_manage"):
        return None

    args = draft_args if isinstance(draft_args, dict) else {}
    extra = args.get("extra_fields") if isinstance(args.get("extra_fields"), dict) else {}
    changed: list[str] = []
    # 只写「草稿里确实已确认」的值，不臆测、不用空值覆盖已有内容。
    direct = {
        "acceptance_result": "acceptance_result", "work_content": "work_content",
        "problems": "problems", "treatment": "treatment",
        "station_conclusion": "station_conclusion", "section": "section",
        "location": "location",
    }
    for key, col in direct.items():
        val = str(args.get(key) or "").strip()
        if val and val != str(getattr(record, col, "") or "").strip():
            limit = 2000 if col in ("work_content", "problems", "treatment") else 256
            setattr(record, col, val[:limit])
            changed.append(col)
    cj = dict(record.content_json or {})
    # extra_fields 一律按 canonical 搬进 content_json（「只搬家不丢弃」）：
    # 后续逐栏追问补的字段不只验收四栏，巡视时间等也会从这里被详情页读到。
    _internal_keys = {"voice_text", "acceptance_points", "agent_write_through",
                      "prov_fields", "acceptance_points_text"}
    for key, value in extra.items():
        slot = str(key or "").strip()
        if not slot or slot in _internal_keys or slot.startswith("_"):
            continue
        val = value if isinstance(value, (list, dict)) else str(value or "").strip()
        if val in (None, "", [], {}):
            continue
        if val != cj.get(slot):
            cj[slot] = val
            changed.append(slot)
    for key in ("acceptance_type", "acceptance_content", "supervision_conclusion",
                "self_check_conclusion"):
        if key in extra:
            continue
        val = str(args.get(key) or "").strip()
        if val and val != str(cj.get(key) or "").strip():
            cj[key] = val
            changed.append(key)
        if not val:
            continue
        # 2026-11-18 同步镜像到 prov_fields：表式必填校验（resolve_record_form）
        # 与文书渲染都以 prov_fields 为准，只写顶层会导致「详情有值、生成说没填」。
        prov = dict(cj.get("prov_fields") or {})
        if str(prov.get(key) or "").strip() != val:
            prov[key] = val
            cj["prov_fields"] = prov
            if "prov_fields" not in changed:
                changed.append("prov_fields")
    # 时间类字段在表式里是独立参数，续办轮只按文本收，别塞进 DateTime 列
    for arg_key, cj_key in (("inspection_start_time", "start_time"),
                            ("inspection_end_time", "end_time")):
        val = str(args.get(arg_key) or "").strip()
        if val and val != str(cj.get(cj_key) or "").strip():
            cj[cj_key] = val
            changed.append(cj_key)
    # 分组建档时验收要点只落成「待确认草案」；用户给出写入授权且没有否定词时，
    # 才把要点按逐条结论转成正式验收内容（正式文书不能只放要点清单）。
    # 2026-11-16 用户截图：要点只在对话里出现过，记录里看不到。
    if record.record_type == "验收" and not str(cj.get("acceptance_content") or "").strip():
        _negative = any(w in message for w in (
            "不符合", "不合格", "不通过", "待整改", "需整改", "有问题"))
        built = split_record_confirm.build_acceptance_content_from_points(
            cj.get("acceptance_points"), positive=not _negative)
        if built:
            cj["acceptance_content"] = built
            changed.append("acceptance_content")
            # 2026-11-18 用户「还有这么多空吗」：要点确认通过后同步落两条结论，
            # 详情页不再一直空着。否定答复（不符合/待整改）不代为下结论，留待整改复验。
            if not _negative:
                if not str(cj.get("self_check_conclusion") or "").strip():
                    cj["self_check_conclusion"] = "施工单位自检合格"
                    changed.append("self_check_conclusion")
                if not str(cj.get("supervision_conclusion") or "").strip():
                    cj["supervision_conclusion"] = "验收合格，同意进入下道工序。"
                    changed.append("supervision_conclusion")
    # 用户原话留痕（「只搬家不丢弃」）
    if message:
        prev = str(cj.get("voice_text") or "").strip()
        cj["voice_text"] = (prev + "\n" + message).strip()[:4000]
    # 🔴 2026-11-17 用户「还有其他的没填呢，怎么不跟用户交互」：
    # 写入授权只落已确认内容；详情页仍标红的必填栏要逐栏问，不再静默留空。
    # 自动跳过过的栏不再回到追问列表（跳过又回来就是死循环）。
    _state = _write_through_state_full(args)
    _skipped = set(_state["skipped"])
    missing_labels = [x for x in _write_through_missing_labels(record, cj)
                      if x not in _skipped]
    stamp = datetime.now().isoformat(timespec="seconds")
    if missing_labels and not _wants_leave_blank(message):
        cj["agent_write_through"] = {
            "at": stamp, "by": user.real_name or user.username,
            "fields": changed, "source": "agent_chat",
            "status": "awaiting_fields",
        }
        record.content_json = cj
        await db.commit()
        current = missing_labels[0]
        logger.info("对话写入授权：记录 #%s 已落已确认内容，仍待补 %s", record.id, missing_labels)
        return {
            "ok": True, "record_id": record.id, "fields": changed,
            "record_type": str(record.record_type or ""),
            "need_more": [current], "pending_labels": missing_labels,
            "_write_through_state": {
                "record_id": record.id, "labels": missing_labels,
                "attempts": _state["attempts"], "skipped": _state["skipped"],
            },
            "reply": (
                f"已写入正式记录 #{record.id}（{record.record_type}）。"
                + (f"\n本次补充：{'、'.join(changed)}。" if changed else "")
                + f"\n还差「{current}」，{_field_fill_hint(current)}；你说一下我就补进去。"
                + (f"后面还要补：{'、'.join(missing_labels[1:])}。" if len(missing_labels) > 1 else "")
                + "\n这些栏位都不补就说「先留空」。"
            ),
        }
    cj["agent_write_through"] = {
        "at": stamp, "by": user.real_name or user.username,
        "fields": changed, "source": "agent_chat",
        "status": "completed" if not missing_labels else "left_blank",
    }
    record.content_json = cj
    await db.commit()
    # 草稿清理由调用方统一做：还需补栏时要把待补状态留在草稿里跨轮续办。
    logger.info("对话写入授权：已更新记录 #%s，字段=%s", record.id, changed)
    return {
        "ok": True, "record_id": record.id, "fields": changed,
        "record_type": str(record.record_type or ""),
        "reply": (
            f"已写入正式记录 #{record.id}（{record.record_type}）。"
            + (f"\n本次补充：{'、'.join(changed)}。" if changed else "")
            + ("\n没提供的栏位按你的授权留空，不做臆测填写；到「工作记录」页可以继续补。"
               if missing_labels else "\n字段已齐。")
        ),
    }


async def _continue_existing_record_write_through(
    db: AsyncSession, user: User, project_id: int | None,
    conversation_id: str | None, message: str, draft_args: dict | None,
) -> dict | None:
    """写入后逐栏追问的续办。

    🔴 2026-11-17 防死循环：每一轮状态都必须朝终止推进——
    答上 → labels 去掉本栏；没答上 → attempts+1，达到上限自动跳过本栏；
    「在问这一栏怎么填」只回通用口径，不写值，但同样计入次数。
    """
    state = _write_through_state_full(draft_args)
    record_id, labels = state["record_id"], state["labels"]
    if record_id is None or not labels:
        return None
    args = dict(draft_args or {})
    current = labels[0]
    attempts = dict(state["attempts"])
    skipped = list(dict.fromkeys(state["skipped"]))
    rest = list(labels[1:])
    ask_again = False
    auto_skipped = ""

    if _wants_leave_blank(message):
        # 整批留空：一次性收口，不留下任何待问栏。
        skipped = list(dict.fromkeys(skipped + labels))
        rest = []
        attempts = {}
    elif _is_field_question(message) or not _looks_like_field_answer(current, message):
        ask_again = True
    else:
        refilled = agent_tools.refill_missing_from_message(args, [current], message)
        if refilled is None:
            # 短标签 → 表式字段精确映射：长栏目被 _shorten_label 截短时也能命中。
            form = agent_tools.record_field_spec.form_for_record_type(
                str(args.get("record_type") or ""), False)
            field = _short_label_fields(form).get(current) or next(
                (f for f in agent_tools.record_field_spec.fields_of(form)
                 if current in str(f.get("label") or "")
                 or current in str(f.get("raw_label") or "")),
                None,
            )
            if field is not None:
                args = agent_tools._set_field_value(args, field, message[:120])
                refilled = args
        if refilled is None:
            ask_again = True
        else:
            args = refilled
            attempts.pop(current, None)   # 本栏已答上，计数清零

    if ask_again:
        used = int(attempts.get(current, 0)) + 1
        if used >= _WRITE_THROUGH_MAX_ATTEMPTS:
            # 第 2 次仍没对上 → 自动跳过，labels 单调变短，不再重复问这一栏。
            skipped = list(dict.fromkeys(skipped + [current]))
            attempts.pop(current, None)
            auto_skipped = current
        else:
            attempts[current] = used
            rest = list(labels)          # 保留本栏，但只再问一次且回复不同

    full_state = {
        "record_id": record_id, "labels": rest,
        "attempts": attempts, "skipped": skipped,
    }
    args[_WRITE_THROUGH_STATE_KEY] = dict(full_state)

    if ask_again and not auto_skipped:
        # 用户在问「怎么填」：给通用口径，不写库、不重复那句「我没对上」。
        return {
            "ok": False, "record_id": record_id,
            "need_more": [current], "pending_labels": rest,
            "_write_through_state": full_state,
            "reply": (
                f"「{current}」：{_field_fill_hint(current)}。"
                "\n你说一下我就补进去；不想补就说「先留空」。"
            ),
        }

    result = await _apply_existing_record_write_through(
        db, user, project_id, conversation_id, message, args,
        record_id_hint=record_id,
    )
    if result is not None:
        # 以写入后重新算出的缺栏为准（已填/已跳过的栏都会被过滤掉），
        # 避免 state 里只留 rest 而把详情页其它缺栏丢掉。
        final_labels = [str(x) for x in (result.get("pending_labels") or rest)]
        full_state = {
            "record_id": record_id, "labels": final_labels,
            "attempts": attempts, "skipped": skipped,
        }
        result["pending_labels"] = final_labels
        result["_write_through_state"] = full_state
        if auto_skipped:
            result["reply"] = (
                str(result.get("reply") or "").rstrip()
                + f"\n「{auto_skipped}」先按留空处理，到「工作记录」页还能补。"
            )
    return result


async def _persist_write_through_state(
    db: AsyncSession, user: User, project_id: int | None,
    conversation_id: str | None, message: str,
    result: dict, draft_args: dict | None,
) -> None:
    """还有栏要问 → 留在草稿跨轮续办；全部落定 → 清草稿。"""
    need = [str(x) for x in (result.get("need_more") or []) if str(x).strip()]
    if need:
        state_args = dict(draft_args or {})
        if not str(state_args.get("record_type") or "").strip():
            state_args["record_type"] = str(result.get("record_type") or "")
        full_state = result.get("_write_through_state")
        state_args[_WRITE_THROUGH_STATE_KEY] = (
            dict(full_state) if isinstance(full_state, dict) else {
                "record_id": result.get("record_id"),
                "labels": [str(x) for x in (result.get("pending_labels") or need)],
                "attempts": {}, "skipped": [],
            }
        )
        await agent_tools.stash_pending(
            db, user.id, "create_work_record", state_args, need,
            message=message, conversation_id=conversation_id, project_id=project_id,
        )
    else:
        await agent_tools.clear_pending(
            db, user.id, "create_work_record", conversation_id,
            project_id=project_id,
        )


async def _agent_chat_impl(
    req: AgentChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    progress_hook: Any = None,
):
    """统一入口：意图路由 + 执行 + 回执。识别不到工具时退化为纯问答。"""
    async def report_progress(stage: str, status: str, title: str, detail: dict[str, Any] | None = None) -> None:
        if progress_hook is not None:
            await progress_hook(stage, status, title, detail or {})

    "用户的问题 message 如果有包括在req 否则为空 "
    "用户的问题 message 如果有包括在req 否则为空 "
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    # @ 资料库引用 → 检索白名单。放在最前面是因为它直接影响后面所有检索路径
    # （路由上下文、纯问答引用、流式回答）。解析失败不能挡住聊天，退化为不限范围。
    scope_paths: set[str] | None = None
    scope_query = ""
    if req.mentions:
        try:
            from app.services import kb_search

            resolved_paths, extra_queries = await kb_search.resolve_mentions(
                db, user.id, req.mentions,
            )
            scope_query = " ".join(extra_queries).strip()
            # 语义见 _mention_scope：模糊词不限定范围，空文件夹则保持空集
            scope_paths = _mention_scope(req.mentions, resolved_paths)
        except Exception:  # noqa: BLE001 —— 引用解析失败不应阻断对话
            logger.warning("@资料库引用解析失败", exc_info=True)
            scope_paths = None
            scope_query = ""

    await report_progress(
        "load_context", "running", "先看看当前项目的资料、刚才的对话，以及还没填完的草稿",
        {"conversation_id": req.conversation_id or "", "message_length": len(message)},
    )
    # 作用域由服务端推导：项目取 users.project_id 并校验成员身份，会话禁止跨项目。
    conversation_id = (req.conversation_id or "").strip()[:64] or None
    try:
        scope = await agent_scope.resolve_agent_scope(db, user, conversation_id)
    except agent_scope.ConversationProjectMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    project_id = scope.project_id
    project = await db.get(Project, project_id) if project_id else None
    # @[引用] 解析：小程序 @ 面板插入的是纯文本标注（记录#/隐患#/规范/资料库），
    # 此前没有任何解析，模型只看到字面量、拿不到正文，于是回答「无法读取文件」。
    # 这里在进入模型前读出被引用资料的正文，作为 system 上下文注入。
    # 注意与上面的 req.mentions 是两条互补通路：mentions 是结构化检索范围（新），
    # 这段是按文本 token 回捞正文（旧，兼容历史消息与手工粘贴的标注）。
    ref_context = ""
    if "@[" in message:
        try:
            _, ref_context = await agent_refs.resolve_message_refs(
                db, user, project_id, message,
            )
        except Exception:  # noqa: BLE001
            logger.warning("@ 引用解析失败，本轮按无引用继续", exc_info=True)
            ref_context = ""
    project_facts = project_context(project)
    system_facts = agent_fact_model.build_system_facts(
        project_name=project_facts["project_name"],
        construction_unit=project_facts["construction_unit"],
        supervision_unit=project_facts["supervision_unit"],
        supervision_org=project_facts["supervision_org"],
        inspector=user.real_name or "",
    )
    prefill = req.prefill if isinstance(req.prefill, dict) else {}
    # 🔴 图片归属校验（2026-11-18，第 5 轮方向④）：req.images 是客户端直传的
    # 服务端媒体地址，此前无任何校验——恶意请求可把他人/他项目的 /uploads/
    # 照片 URL 挂进自己的草稿与记录（ctx.images → _agent_images →
    # inspection_photos，全链路无闸）。review-plan 链路早有
    # _validate_review_plan_media（uploader+project 双闸），这里补齐同口径：
    # 每张 URL 反查 MediaRecord，非本人/本项目上传的一律拒绝。
    req.images = await _validate_chat_images(db, user, project_id, req.images)
    earlier_messages = await agent_tools.record_conversation_message(
        db, user.id, conversation_id, message, images=req.images,
        project_id=project_id,
    )

    # Load durable draft context before the routing model runs.  Recent chat
    # history is not a substitute for this state: users often give date,
    # place and activity in separate turns.
    pend_for_context = await agent_tools.get_pending(
        db, user.id, conversation_id=conversation_id, project_id=project_id,
    )
    draft_for_context = pend_for_context[1] if pend_for_context else {}
    # 项目规约（总监维护、全项目共享）+ 个人记忆（仅本人），各自独立 system message。
    project_memories = (
        await agent_project_memories.list_active_memories(db, project_id)
        if project_id else []
    )
    personal_memories = await agent_preferences.list_active_preferences(
        db, user.id, project_id=project_id,
    )
    project_memory_block = agent_project_memories.render_project_memories_block(
        project_memories,
    )
    personal_memory_block = agent_preferences.render_preferences_block(
        personal_memories,
    )
    # 使用统计：本轮实际注入的项目规约记一次引用（失败不影响聊天）。
    if project_memories:
        try:
            await agent_project_memories.record_usage(
                db, project_id,
                [row.id for row in project_memories],
                actor_id=user.id,
                conversation_id=conversation_id,
            )
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            logger.warning("项目规约使用统计写入失败，本轮继续", exc_info=True)
    await report_progress(
        "load_context", "completed", "已了解项目背景和刚才的对话上下文",
        {
            "history_messages": len(earlier_messages or []),
            "has_pending_draft": bool(pend_for_context),
            "pending_tool": pend_for_context[0] if pend_for_context else "",
            "pending_fields": list(draft_for_context.get("missing") or [])[:24],
            "project_memory_count": len(project_memories),
            "personal_memory_count": len(personal_memories),
        },
    )

    system = _AGENT_SYSTEM.format(
        catalog=agent_tools.catalog_for_prompt(),
        actions=json.dumps(agent_request_plan.action_catalog(), ensure_ascii=False),
        record_types=json.dumps(agent_request_plan.RECORD_TYPES, ensure_ascii=False),
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    # 🔴 项目规约在前、个人记忆在后：项目统一口径优先于个人习惯；两块都独立成
    # system message，不合并进 _AGENT_SYSTEM 模板体（模板保持字节稳定供跑分基线）。
    # 具体落库前服务端仍做参数/权限/审计，记忆只是非破坏性基线，不能越权。
    if project_memory_block:
        messages.append({"role": "system", "content": project_memory_block})
    if personal_memory_block:
        messages.append({"role": "system", "content": personal_memory_block})
    if ref_context:
        messages.append({"role": "system", "content": ref_context})
    if pend_for_context:
        messages.append({"role": "system", "content": render_context_summary(
            draft_for_context.get("args"), draft_for_context.get("facts"),
            draft_for_context.get("missing"), draft_for_context.get("messages"))})
    if earlier_messages:
        history_summary = render_conversation_history(earlier_messages)
        if history_summary:
            messages.append({"role": "user", "content": history_summary})
    messages.extend(_history_messages(req.history))
    material = (req.material or "").strip()
    if material:
        # 图/文件已由前端交给视觉模型/文档解析，这里只把文字素材挂到用户消息后面。
        # 不挂的话模型会以为「用户只发了一句话」，既抓不到现场情况，也容易编造。
        messages.append({"role": "user", "content": (
            f"{message}\n\n以下是随消息发送的素材（系统已识别，不是你要识别的图片本体）：\n{material}")})
    else:
        messages.append({"role": "user", "content": message})

    external_plan = agent_request_plan.normalize_candidate(
        req.request_plan,
        message=message,
        attachment_count=1 if material else 0,
        evidence_count=1 if material else 0,
    )
    has_external_plan = bool(external_plan.get("actions"))
    incoming_plan = external_plan

    form_values = req.form_values if isinstance(req.form_values, dict) else {}
    pending_args_for_submit = (pend_for_context[1].get("args") or {}) if pend_for_context else {}
    structured_submit = bool(form_values) and bool(pend_for_context)
    # 表单在字段全部齐全时可能没有新增值；点击“保存并生成记录”仍是一次
    # 明确确认，因此把该无字段提交视为确认动作，而不是重新追问。
    if (
        pend_for_context
        and pending_args_for_submit.get("_awaiting_confirmation")
        and message.strip() in {"按表单补充信息", "确认生成", "确认保存", "保存并生成记录"}
    ):
        structured_submit = True
    model_id = req.model or ""
    parsed: dict | None = None
    raw = ""

    # 🔴 待定记录改类型：确定性快捷指令，先于模型路由处理（2026-11-13）。
    # 后端 /api/work-record/{id}/set-type 早已存在，但小程序与 Web 后台都没接入口，
    # 用户按回执提示去「工作记录页」其实改不了类型。这里是纯后端兜底：
    # 纯文字说「改成巡视」即改最近一条待定记录，现网所有小程序版本立刻可用，
    # 不改提示词、不依赖模型路由，也就不用重跑跑分基线。
    if not structured_submit and not (req.images if isinstance(req.images, list) else None):
        _change_to = agent_request_plan.parse_pending_type_change(message)
        if _change_to:
            _changed = await _apply_pending_type_change(db, user, project_id, _change_to)
            if _changed:
                await report_progress(
                    "tool_validation", "completed", "已把待定记录改成具体类型",
                    {"tool": "set_work_record_type", "record_id": _changed["record_id"],
                     "record_type": _change_to},
                )
                return {
                    "kind": "action", "tool": "set_work_record_type",
                    "model": model_id, "request_plan": incoming_plan,
                    "reply": _changed["reply"],
                    "card": None, "cards": [], "actions": [], "need_more": [],
                    "options": {}, "form": None, "input_components": [],
                    "recommendations": [], "undo": None,
                }

    # 🔴 写入后仍有必填栏待补：用户这句就是对该栏的回答，先续办同一条记录。
    # 必须在模型路由前短路，否则「隐蔽工程」会被当成新建记录的字段，重复建档。
    _wt_args = draft_for_context.get("args") or {}
    if (
        not structured_submit
        and _write_through_state(_wt_args)[0] is not None
        and not agent_tools.wants_write_through(message)
    ):
        _wt = await _continue_existing_record_write_through(
            db, user, project_id, conversation_id, message, _wt_args,
        )
        if _wt is not None:
            await _persist_write_through_state(
                db, user, project_id, conversation_id, message, _wt, _wt_args,
            )
            await report_progress(
                "tool_validation", "completed", "已按用户补充更新正式记录",
                {"tool": "update_work_record", "record_id": _wt.get("record_id"),
                 "fields": _wt.get("fields") or []},
            )
            return {
                "kind": "action", "tool": "update_work_record",
                "model": model_id, "request_plan": incoming_plan,
                "reply": _wt["reply"],
                "card": None, "cards": [], "actions": [],
                "need_more": [str(x) for x in (_wt.get("need_more") or [])],
                "options": {}, "form": None, "input_components": [],
                "recommendations": [], "undo": None,
            }

    # 🔴 显式写入已建记录（2026-11-16 真机录屏）：先于模型路由确定性处理，
    # 避免「写入正式记录 / 请写入验收记录」又被路由成一份新的待确认草稿。
    if (
        not structured_submit
        and agent_tools.wants_write_through(message)
        and (
            _refers_to_existing_record(message, earlier_messages, req.history)
            or bool(pend_for_context)   # 有待确认草稿 = 延续同一件事
        )
    ):
        _wt = await _apply_existing_record_write_through(
            db, user, project_id, conversation_id, message,
            _wt_args, earlier_messages, req.history,
        )
        if _wt:
            await _persist_write_through_state(
                db, user, project_id, conversation_id, message, _wt, _wt_args,
            )
            await report_progress(
                "tool_validation", "completed", "已按写入授权更新正式记录",
                {"tool": "update_work_record", "record_id": _wt["record_id"],
                 "fields": _wt["fields"]},
            )
            return {
                "kind": "action", "tool": "update_work_record",
                "model": model_id, "request_plan": incoming_plan,
                "reply": _wt["reply"],
                "card": None, "cards": [], "actions": [],
                "need_more": [str(x) for x in (_wt.get("need_more") or [])],
                "options": {}, "form": None, "input_components": [],
                "recommendations": [], "undo": None,
            }

    if not structured_submit:
        try:
            await report_progress(
                "intent_model", "running", "正在理解你想做什么，并从你的话里抽取关键字段",
                {"provider": req.provider or "系统路由", "model": req.model or "系统选择", "response_mode": "structured_json"},
            )
            if _streaming_enabled():
                raw_parts: list[str] = []
                config = None
                async for event in model_gateway.stream_chat_completion(
                    messages,
                    temperature=0.2,
                    provider=req.provider,
                    model=req.model,
                    response_format={"type": "json_object"},
                    thinking=False,  # 路由只要 JSON，不要思考：更快且不展示内部规划
                ):
                    event_type = event.get("type")
                    if event_type == "config":
                        config = event.get("config")
                    elif event_type == "content":
                        # 路由模型的 content 是 JSON 规划，不展示；思考过程只从
                        # 「回答模型」那条流里取（否则会把工具目录/提示词逻辑暴露给用户）。
                        raw_parts.append(event["text"])
                raw = "".join(raw_parts)
            else:
                raw, config = await model_gateway.chat_completion(
                    messages,
                    temperature=0.2,
                    provider=req.provider,
                    model=req.model,
                    response_format={"type": "json_object"},
                )
            model_id = getattr(config, "model_id", "") or req.model or ""
            parsed = agent_tools._parse_json_object(raw)
            await report_progress(
                "intent_model", "completed", "已读懂你的意图，正在核对可用的动作和字段",
                {
                    "model": model_id,
                    "has_json_result": bool(parsed),
                    "proposed_action": str((parsed or {}).get("intent") or ""),
                    "proposed_record_type": str((parsed or {}).get("record_type") or ""),
                    "proposed_fact_fields": list((parsed or {}).get("facts", {}).keys())[:24]
                    if isinstance((parsed or {}).get("facts"), dict)
                    else [str(item.get("field") or item.get("name") or "") for item in ((parsed or {}).get("facts") or [])[:24] if isinstance(item, dict)],
                },
            )
        except model_gateway.ModelGatewayError as exc:
            # 不支持 JSON 模式的端点：退化为纯问答，聊天不中断
            logger.warning("agent JSON 路由失败，退化纯问答: %s", type(exc).__name__)
            try:
                await report_progress(
                    "intent_model", "running", "结构化理解暂时不可用，我改用普通问答来回答",
                    {"reason": type(exc).__name__},
                )
                answer = await _plain_answer(message, req.history, req.provider, req.model, req.mode)
            except model_gateway.ModelGatewayError as exc2:
                raise HTTPException(status_code=503, detail=str(exc2)) from exc2
            await report_progress(
                "intent_model", "completed", "已用普通问答给出回复，本轮没有执行业务写入",
                {"degraded": True},
            )
            return {"kind": "chat", "tool": None, "reply": answer, "card": None,
                    "cards": [], "actions": [], "need_more": [], "undo": None,
                    "form": None, "model": None, "degraded": True,
                    "request_plan": incoming_plan}

    semantic_candidate = dict(parsed or {})
    semantic_args = (
        semantic_candidate.get("args")
        if isinstance(semantic_candidate.get("args"), dict)
        else {}
    )
    # Some models still put the record type only inside args. Preserve that
    # value as a candidate while the action itself remains catalog-controlled.
    if not semantic_candidate.get("record_type") and semantic_args.get("record_type"):
        semantic_candidate["record_type"] = semantic_args.get("record_type")
    # 🔴 素材明示类型：照片水印/公示牌上写着「隐蔽工程验收公示牌」这类字样时，
    # 不再回头问「整理成哪一类记录」。逻辑在 agent_request_plan，跑分器同源调用。
    semantic_candidate = agent_request_plan.apply_material_record_type(
        semantic_candidate, material or "")
    semantic_plan = agent_request_plan.normalize_candidate(
        semantic_candidate,
        message=message,
        attachment_count=1 if material else 0,
        evidence_count=1 if material else 0,
        evidence=[],
        system_facts=system_facts,
    )
    semantic_plan = agent_request_plan.enrich_plan_facts(
        semantic_plan,
        extract_explicit_message_facts(message, message_ref=message),
        evidence=[],
        system_facts=system_facts,
    )
    if has_external_plan:
        # The precomputed attachment plan is authoritative for actions and
        # clarifications; the chat turn contributes the extracted user facts.
        merged_facts = agent_fact_model.merge_fact_candidates(
            external_plan.get("facts"), semantic_plan.get("facts"),
        )
        incoming_plan = dict(external_plan)
        incoming_plan["facts"] = merged_facts
        incoming_plan["system_facts"] = system_facts
        incoming_plan["field_status"] = {
            **agent_fact_model.fact_context_fields(system_facts),
            **agent_fact_model.fact_context_fields(merged_facts),
        }
        prefill_evidence = [{
            "record_date": prefill.get("record_date") or _iso_date(prefill.get("taken_at")),
            "observed_date_source": "watermark",
            "location": prefill.get("location"),
            "section": prefill.get("section"),
            "weather": prefill.get("weather"),
            "field_sources": {
                field: "watermark"
                for field in ("record_date", "location", "section", "weather")
                if str(prefill.get(field) or "").strip()
                or (field == "record_date" and _iso_date(prefill.get("taken_at")))
            },
        }] if prefill else []
        prefill_conflicts = agent_fact_model.detect_source_conflicts(
            merged_facts, prefill_evidence, system_facts,
        )
        existing_conflicts = [
            item for item in (external_plan.get("field_conflicts") or [])
            if isinstance(item, dict)
        ]
        conflict_fields = {str(item.get("field") or "") for item in existing_conflicts}
        incoming_plan["field_conflicts"] = existing_conflicts + [
            item for item in prefill_conflicts
            if str(item.get("field") or "") not in conflict_fields
        ]
        trace = dict(incoming_plan.get("trace") or {})
        trace.update({
            "fact_count": len(merged_facts),
            "conflict_count": len(incoming_plan["field_conflicts"]),
            "semantic_action_ids": [
                str(item.get("id") or "") for item in semantic_plan.get("actions", [])
                if isinstance(item, dict)
            ],
        })
        incoming_plan["trace"] = trace
    else:
        incoming_plan = semantic_plan

    tool = str(incoming_plan.get("selected_tool") or "").strip()
    if tool not in agent_tools.TOOL_BY_NAME:
        tool = ""

    # 🔴 多步编排（2026-11-12）：模型给出多个动作（execution_mode="plan_only"）时，
    # 不再整轮退化纯问答——自动执行第一个可执行动作，剩余动作在办完后以
    # 「一键续办」chips 呈现（前端 chips 机制已有，点击即以该文本再发一轮）。
    # 仅当计划无澄清、无需问记录类型时启用，避免与 needs_record_type /
    # clarifications 的确定性追问打架。
    multi_action_ids: list[str] = []
    if not tool and incoming_plan.get("execution_mode") == "plan_only":
        _plan_clarifications = [
            item for item in (incoming_plan.get("clarifications") or [])
            if isinstance(item, dict)
        ]
        if not _plan_clarifications:
            # 🔴 只收「真业务动作」进多步编排：navigate（打开页面）是导航不是干活，
            # undo（撤销）不该被编排进后续步骤，chat 是问答不是动作。
            # 否则「打开工作台再记一条巡视」会先跳页、把建档丢给续办，顺序完全颠倒。
            _ORCHESTRATION_SKIP = {"navigate", "undo", "chat"}
            _executables: list[tuple[str, str]] = []
            for _item in (incoming_plan.get("actions") or []):
                if not isinstance(_item, dict):
                    continue
                _action_id = str(_item.get("id") or "").strip()
                if _action_id in _ORCHESTRATION_SKIP:
                    continue
                _spec = agent_request_plan.ACTIONS.get(_action_id)
                _map_tool = str((_spec or {}).get("tool") or "").strip() if _spec else ""
                if _map_tool and _map_tool in agent_tools.TOOL_BY_NAME:
                    _executables.append((_action_id, _map_tool))
            if _executables:
                _first_id, _first_tool = _executables[0]
                tool = _first_tool
                # 剩余动作 id 留到首个动作执行成功后再下发，避免打断补字段流程
                multi_action_ids = [aid for aid, _ in _executables[1:]]
                logger.info(
                    "多步编排：首个动作 %s 已接管，剩余动作 %s 待续办",
                    _first_id, multi_action_ids,
                )
    fact_args = agent_fact_model.facts_to_args(incoming_plan.get("facts"))
    args = {**semantic_args, **fact_args}
    if incoming_plan.get("record_type") and not str(args.get("record_type") or "").strip():
        args["record_type"] = incoming_plan["record_type"]
    inferred_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
    selected_form = agent_tools.record_field_spec.form_for_record_type(
        inferred_type, agent_tools._is_weida(inferred_type, args, message),
    ) if inferred_type else None
    args = agent_tools.apply_explicit_user_facts(
        args, incoming_plan.get("facts"), selected_form,
    )
    turn_fact_args = dict(args)
    proposed_record_type = agent_tools._norm_record_type(str(args.get("record_type") or ""))
    _thought_record_type = str(incoming_plan.get("record_type") or proposed_record_type or "")
    await report_progress(
        "semantic_validation", "completed",
        ("我判断本轮要处理的是「" + _thought_record_type + "」")
        if _thought_record_type else "已梳理出本轮要执行的动作与字段",
        {
            "intent": incoming_plan.get("intent"),
            "action": incoming_plan.get("selected_tool"),
            "record_type": incoming_plan.get("record_type") or proposed_record_type,
            "model": model_id,
            "facts": [
                {
                    "field": item.get("field"), "value": item.get("value"),
                    "source": item.get("source"), "confidence": item.get("confidence"),
                }
                for item in (incoming_plan.get("facts") or [])
                if isinstance(item, dict)
            ][:32],
            "conflicts": (incoming_plan.get("field_conflicts") or [])[:16],
            "clarifications": (incoming_plan.get("clarifications") or [])[:12],
            "explicit_user_fields": [
                {"field": item.get("field"), "value": item.get("value")}
                for item in (incoming_plan.get("facts") or [])
                if isinstance(item, dict)
                and item.get("source") == "user_message_deterministic"
            ][:24],
        },
    )

    # 上一轮追问留下的草稿（同用户，已落库）：本轮参数合并到草稿上，避免丢字段
    pend = pend_for_context
    pending_tool = pend[0] if pend else ""
    """
    user - message
    assisent
    system
    tools
    有限的目标
    1 2 3 4 5
    1 - 1234
    2 - 3214
    3 - 123123
    4 - 123123
    5 - 123133132

    整理表
    整理成模板内容
    意图 - 对应表
    填入对应表
    确认信息
    登记
    """
    draft = pend[1] if pend else {}
    if structured_submit:
        # 表单一次提交：不再让模型猜字段，直接按字段表写回草稿。
        tool = pending_tool
        draft_args = draft.get("args") or {}
        draft_type = agent_tools._norm_record_type(
            str(draft_args.get("record_type") or form_values.get("record_type") or "")
        ) or ""
        draft_form = agent_tools.record_field_spec.form_for_record_type(
            draft_type, agent_tools._is_weida(draft_type, draft_args, message),
        ) if draft_type else None
        # Keep facts already accumulated in earlier turns; a form submission is
        # an update to the durable draft, not a replacement for it.
        args = agent_tools.apply_form_values(dict(draft_args), draft_form, form_values)
        turn_fact_args = dict(args)
    draft_prefill = (draft.get("args") or {}).get("_prefill")
    if not prefill and isinstance(draft_prefill, dict):
        prefill = draft_prefill
    # 模型没选工具、但草稿未完成：把这句当「补充回答」单独抽一次参数
    # （明显是提问/找页面的话不抽，避免「用户中途问规范」被当成补字段）
    if not tool and pending_tool and not any(h in message for h in _SUPPLEMENT_SKIP_HINTS):
        await report_progress(
            "extract_supplement", "running", "正在把你说的内容，对照草稿里还缺的字段提取出来",
            {"pending_tool": pending_tool, "missing_fields": list(draft.get("missing") or [])[:24]},
        )
        extra = await _extract_supplement(message, pending_tool, draft, req.provider, req.model)
        # 命中「仍缺字段」才当补充回答：标签、extra_fields 槽位、或仍缺字段对应的参数名
        if agent_tools.supplement_hits(extra, draft.get("args") or {}, draft.get("missing")):
            tool = pending_tool
            args = {**args, **extra}
            turn_fact_args.update(extra)
            await report_progress(
                "extract_supplement", "completed", "已把你补充的内容填进草稿对应字段",
                {"fields": list(extra.keys())[:24]},
            )
        else:
            await report_progress(
                "extract_supplement", "completed", "本轮内容没有匹配到当前草稿缺项，原草稿保持不变",
                {"candidate_fields": list(extra.keys())[:24]},
            )
    # 🔴 多图分类分流的「确认」轮 —— 必须**先于**单类型词提取判断：
    # 上一轮已给出分组回执（草稿含 _agent_pending_split），本轮用户可能同时确认
    # 多种记录类型（如「巡视符合要求，验收符合」）。若先跑 canonical_record_type，
    # 会从这句话里只提取到第一个类型词（如「巡视」），导致 tool 被先设成
    # create_work_record 且 args 里 record_type="巡视"，下方的多图分组确认检查
    # 就因为 `if not tool` 守卫被跳过，分组方案永不消费 → 验收记录丢失。
    # 修复：草稿里有 _agent_pending_split 时，**跳过单类型词提取**（这是分组
    # 确认轮，不是新的建单请求），并把 _agent_pending_split 从 draft.args 合并
    # 到 args 里，让工具层 _maybe_handle_multi_image_split 能消费它。
    if pending_tool == "create_work_record":
        _draft_args = draft.get("args") or {}
        _pending_split = _draft_args.get("_agent_pending_split")
        logger.info("[多图分组调试] message=%r tool=%r pending_tool=%r has_pending_split=%s split_groups=%d is_confirm=%s",
                     message, tool, pending_tool,
                     bool(_pending_split and _pending_split.get("groups")),
                     len((_pending_split or {}).get("groups") or []),
                     multi_image_split.is_confirm(message))
        if isinstance(_pending_split, dict) and _pending_split.get("groups"):
            # 分组方案从草稿带过来 → 合并进本轮 args，工具层需要
            args["_agent_pending_split"] = _pending_split
            orig_args_ref = args  # 别丢引用：后续要镜像回
            if not tool and multi_image_split.is_confirm(message):
                tool = "create_work_record"
                # 分组确认轮不要从用户消息里抽 record_type（如"巡视符合，验收符合"
                # 会只抽到"巡视"）——分组方案里已经有正确的类型分配了
            # 有分组方案但本轮没说确认词 → 保持 need_more 态，等用户确认

    # A type choice is intentionally kept as a deterministic transition for
    # the evidence-only draft.  The generic supplement extractor cannot infer
    # a form before record_type exists.
    # 🔴 仅在**没有多图分组方案**时才跑（有分组时上面已跳过，避免误匹配单类型）
    if not tool and pending_tool == "create_work_record" and not args.get("_agent_pending_split"):
        candidate = agent_request_plan.canonical_record_type(message)
        if candidate:
            tool = pending_tool
            args = {**args, "record_type": candidate}
            turn_fact_args = dict(args)

    # 素材里已确定的结构化字段（照片水印的拍摄时间/地点/天气）并入建记录参数。
    # 日期、地点、部位和天气以水印/EXIF 客观证据为准；用户冲突描述保留在计划中。
    # A 层（jl 零必填）：现场有证据（照片/内容/水印/部位/位置）且类型不明时，
    # 直接落「待定」记录，不阻塞用户；完全无证据才保留追问（避免空记录被静默建出）。
    # 覆盖两条路径：① 上一轮遗留「待确认草稿」（pending_tool=create_work_record，
    # 草稿无 record_type）② 全新请求（无 pending）。不再依赖模型 clarifications 标记。
    auto_filled: list[str] = []
    needs_record_type = any(
        str(item.get("field") or "") == "record_type"
        for item in incoming_plan.get("clarifications", [])
        if isinstance(item, dict)
    )
    _rt_known = bool(
        agent_tools._norm_record_type(
            str((draft.get("args") or {}).get("record_type") or args.get("record_type") or "")
        )
    )
    _has_evidence = bool(
        (req.images if isinstance(req.images, list) else None)
        or material or prefill
        or _plan_evidence_count(incoming_plan)
        or str(args.get("work_content") or "").strip()
        or str(args.get("section") or "").strip()
        or str(args.get("location") or "").strip()
    )
    # 🔴 真因修复（2026-11-13 第三次复测）：带图发消息时小程序前端
    # sendAttachments → /agent/request-plan → wantsRecordPlan → /agent/review-plans
    # 「候选事项/待确认草稿」，在前端直接 return，根本不调用 /agent/chat。
    # request-plan 对「你先记着」类请求会打 stash_as_pending 标记并清空 actions，
    # 前端才落到本端点；这里直读原始请求标记（normalize_candidate 会丢自定义字段），
    # 无条件落「待定」，不再依赖模型路由结果。
    _stash_pending_plan = bool(
        isinstance(req.request_plan, dict)
        and req.request_plan.get("stash_as_pending")
    )
    # 🔴 带图/有内容的请求里，request-plan 往往已直接把 selected_tool 选成
    # create_work_record（而非留空等路由补），导致原来的 `if not tool` 守卫整轮失效、
    # 永远走「追问类型→落草稿」。守卫放宽到「plan 已选 create_work_record
    # 但类型仍不明」也命中，强制落「待定」并免确认。
    if _stash_pending_plan and _has_evidence:
        args["record_type"] = "待定"
        args["_auto_tbd"] = True
        # 🔴 只有「你先记着」这类用户明确要求先存着的请求才跳过逐图分组：
        # 用户要的是「别问、先存一条」。普通「有照片但没说类型」不能跳过分组，
        # 否则一批跨类型的现场照片会被硬塞进同一条待定记录（2026-11-13 真机反馈）。
        args["_no_multi_split"] = True
        tool = "create_work_record"
        logger.info("A层零必填：request-plan 标记直达 → 无条件落待定记录（跳过逐图分组）")
    elif (not tool or tool == "create_work_record") and not _rt_known and _has_evidence:
        # 不再追问类型：落「待定」记录，用户在「工作记录」页改标签即可。
        # 但多图仍按逐图信号分组（跨类型时先回执「分了 X/Y」再确认建多条），
        # 分组是「照片怎么归档」而不是「记录类型填什么」，不属于零必填要跳过的追问。
        args["record_type"] = "待定"
        args["_auto_tbd"] = True
        tool = "create_work_record"
        logger.info("A层零必填：有证据且类型不明 → 落待定记录（保留多图分组）")

    # 🔴 服务端接管：模型在「已带素材证据 + 已识别记录类型或存在字段冲突」时仍返回
    # 纯聊天（典型：只汇报「你描述的信息与图片证据不一致…请核对」），整轮会退化成一堵
    # 文字，need_more 全空，整轮退化成纯汇报。
    # 2026-11-05 生产会话实测：kind=chat / need_more=[]。此时不等模型自觉，
    # 直接走建记录流程，由 _create_work_record 给出一句自然语言追问。
    if (not tool and not needs_record_type
            and (str(material or "").strip() or prefill)
            and (incoming_plan.get("field_conflicts")
                 or agent_tools._norm_record_type(str(args.get("record_type") or "")))):
        tool = "create_work_record"

    if tool == "create_work_record":
        # 🔴 短结论吸收（2026-11-16 真机录屏）：用户只回「合格」，模型接住了
        # 却没落进 acceptance_result → 下一轮又追问验收结论。确定性兜底。
        if not str(args.get("acceptance_result") or "").strip():
            _short = agent_tools.short_conclusion(message)
            if _short:
                args["acceptance_result"] = _short
        if prefill:
            rtype = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
            if not rtype:
                # Do not apply type-specific prefill when the target form is
                # unknown.  The values stay in req.prefill for the next step.
                rtype = ""
            if not rtype:
                auto_filled = []
            else:
                args, auto_filled = apply_prefill(
                    args, prefill, rtype,
                    protected_keys=set(form_values) if structured_submit else None,
                )

    if tool:
        spec = agent_tools.TOOL_BY_NAME[tool]
        if spec.risk != "read" and not project_id:
            return {"kind": "action", "tool": tool, "reply": "你还没有所属项目，先加入项目后再记录。",
                    "card": None, "cards": [], "actions": [{"type": "navigate", "label": "去加入项目",
                    "url": "/pages/join-project/index"}], "need_more": [], "undo": None,
                    "form": None, "model": model_id, "request_plan": incoming_plan}
        if tool == pending_tool:
            args = agent_tools.merge_args(draft.get("args"), args)
            # 🔧 extra_fields 键归一（key↔canonical），避免模型按 key 写、服务端读 canonical 误判缺失
            record_type = agent_tools._norm_record_type(str(args.get("record_type") or "")) or ""
            _form = None
            if record_type:
                _form = agent_tools.record_field_spec.form_for_record_type(
                    record_type,
                    agent_tools._is_weida(record_type, args, message),
                ) or ""
                if not structured_submit:
                    args = agent_tools.apply_explicit_user_facts(
                        args, draft.get("facts"), _form,
                    )
                    args = agent_tools.apply_explicit_user_facts(
                        args, incoming_plan.get("facts"), _form,
                    )
            args = agent_tools.normalize_extra_fields(args, _form)
        elif pending_tool and spec.risk != "read":
            # 用户换成了另一个写操作 → 旧草稿作废（纯查询/问答不清草稿，草稿有 24 小时 TTL）
            await agent_tools.clear_pending(
                db, user.id, pending_tool, conversation_id, project_id=project_id,
            )
        # _prefill is durable context only; it is never a business field.
        args.pop("_prefill", None)
        # _followup_actions（多步编排续办）是路由层内部键，不是工具参数，绝不传给 handler。
        args.pop("_followup_actions", None)
        # 普通聊天创建记录与整理计划统一采用“字段齐全 → 用户确认 → 正式写入”。
        # 首次执行只做合同校验并保存待确认草稿；用户确认后才放行工具写入。
        if tool == "create_work_record":
            if args.get("_auto_tbd"):
                # A 层零必填：照片/内容自动落待定，免最终确认（待定内容空、风险低，
                # 用户可在工作记录页改标签或删除）。
                args["_require_confirmation"] = False
                args["_execution_confirmed"] = True
            else:
                awaiting = bool((draft.get("args") or {}).get("_awaiting_confirmation"))
                # 草稿已整理好、只差确认时，用户明确说「按草稿生成」「直接生成」
                # 同样是一次授权；旧白名单只认「确认」字样，「按当前草稿生成」
                # 会被当成又一轮草稿整理（模型还接话「收到，已生成」，实际没写库）。
                # 🔴 2026-11-16 真机录屏：用户连说「写入正式记录 / 请写入验收记录」
                # 不在白名单；且草稿仍有必填缺项时 awaiting=False，白名单根本不生效
                # → 无限追问。显式写入授权必须独立于 awaiting 生效。
                write_through = agent_tools.wants_write_through(message)
                _confirm_tokens = (
                    "确认生成", "确认保存", "保存并生成", "确认建档",
                    "按当前草稿生成", "按草稿生成", "直接生成", "生成吧",
                    "写入正式记录", "写入验收记录", "写入工作记录",
                    "请写入", "直接写入", "正式写入", "直接建档",
                )
                confirmed = (awaiting or write_through) and (
                    structured_submit
                    or any(token in message for token in _confirm_tokens)
                    or write_through
                )
                # 工具层据此放行必填缺项（留空、不臆测）与「一起询问」拦截
                args["_write_through"] = bool(write_through)
                args["_require_confirmation"] = not confirmed
                if confirmed:
                    args["_execution_confirmed"] = True
        ctx = agent_tools.ToolContext(db=db, user=user, project_id=project_id, project=project, message=message, material=material, images=req.images, image_signals=req.image_signals)
        await report_progress(
            "tool_validation", "running", "确认要执行的动作，并核对字段是否齐全",
            {"tool": tool, "record_type": str(args.get("record_type") or ""), "form": _record_form_code(tool, args, message) or ""},
        )
        result = await agent_tools.dispatch(ctx, tool, args)
        if result.get("need_more") and tool == pending_tool:
            # 🔴 兜底：用户这句是对上一轮追问的实质性回复，但模型没填进该字段 → 原话直接填入重跑，
            # 避免「回复了还反复追问同一字段」死循环（2026-10-15 真机踩坑：巡视部位）
            prev_missing = [str(m) for m in (draft.get("missing") or [])]
            refill_labels = [m for m in result["need_more"] if str(m) in prev_missing]
            refilled = agent_tools.refill_missing_from_message(args, refill_labels, message)
            if refilled is not None:
                args = refilled
                result = await agent_tools.dispatch(ctx, tool, args)
        await report_progress(
            "tool_validation", "completed", "服务端字段合同校验完成",
            {
                "tool": tool,
                "missing_fields": list(result.get("need_more") or [])[:32],
                "provided_fields": [
                    {"field": str(key), "value": value}
                    for key, value in args.items()
                    if not str(key).startswith("_")
                    and key != "extra_fields"
                    and str(value if value is not None else "").strip()
                ][:32],
                "extra_fields": [
                    {"field": str(key), "value": value}
                    for key, value in (args.get("extra_fields") or {}).items()
                    if str(value if value is not None else "").strip()
                ][:32] if isinstance(args.get("extra_fields"), dict) else [],
                "created_ids": [item.get("id") for item in (result.get("cards") or []) if isinstance(item, dict) and item.get("id") is not None][:12],
                "created_id": (result.get("card") or {}).get("id") if isinstance(result.get("card"), dict) else None,
            },
        )
        if result.get("need_more"):
            if tool == "create_work_record" and "最终确认" in [str(x) for x in (result.get("need_more") or [])]:
                args["_awaiting_confirmation"] = True
            # 🔴 追问气泡以「模型自然接话」为主：need_more 时优先用 LLM 的 answer
            # 当主气泡文案（自然接话、不重复标签；bold 规则串只出现在表单里），
            # 模板 reply 只在 answer 缺失/过于机械时才兜底。两者绝不叠加，避免机械感。
            _model_answer = str((parsed or {}).get("answer") or "").strip()
            _tool_reply = str(result.get("reply") or "").strip()
            _awaiting_final = (
                tool == "create_work_record"
                and "最终确认" in [str(x) for x in (result.get("need_more") or [])]
            )
            # 🎯 最终确认快捷键：只要进入最终确认态，本条气泡必下发「确认生成」chip，
            # 前端点击即以该词发送（命中后方 _confirm_tokens 白名单直接建档），免打字。
            # 与字段候选 chips 不同（字段候选已按 2026-11-06 纯文字需求禁用）——这是
            # 用户主动要求的单一动作快捷键（2026-11-10）。放在分支外统一兜底，模型
            # 无论接不接话，快捷键都出现。
            if _awaiting_final and not result.get("ask_block"):
                result["chips"] = ["确认生成"]
            _result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            if result.get("ask_block") or _result_data.get("split"):
                # 「一起询问」与「多图分组建档」回执必须完整呈现：逐类待确认内容
                # 是确定性生成的，不能被模型自然接话盖掉（2026-11-16 真机需求）。
                result["reply"] = _tool_reply
            elif _model_answer and not _is_mechanical_reply(_model_answer) and not _sounds_like_success(_model_answer):
                # 模型自然接话常说「收到，已生成」「按已确认信息直接发起这条…创建」，
                # 会把系统的真实追问（还差哪一项 / 还需确认）盖掉，用户以为建档成功
                # 却查不到记录（2026-11-09/11-10 生产实测）。保留自然接话，
                # 但建记录时缺字段必须补上真实状态与下一步引导。
                result["reply"] = _model_answer
                if _awaiting_final:
                    result["reply"] += (
                        "\n\n信息已整理完毕，回复「确认生成」即可建档。"
                    )
                elif tool == "create_work_record":
                    _missing_labels = [str(x) for x in (result.get("need_more") or [])]
                    _missing_labels = [m for m in _missing_labels if m != "最终确认"]
                    if _missing_labels:
                        result["reply"] += (
                            "\n\n还没写入正式记录，还差「"
                            + "、".join(_missing_labels[:3])
                            + "」；回复补充一下，或发相应照片即可。"
                        )
            elif _tool_reply:
                result["reply"] = _tool_reply
            # 续办动作随草稿持久化：首动作若是写操作，本轮只进确认流程就 return，
            # 剩余动作必须跨轮存活，等首动作确认成功后一并下发（2026-11-12）。
            _stash_args = dict(args)
            if prefill and tool == "create_work_record":
                _stash_args["_prefill"] = prefill
            if multi_action_ids:
                _stash_args["_followup_actions"] = list(multi_action_ids)
            await agent_tools.stash_pending(
                db, user.id, tool,
                _stash_args,
                result.get("need_more"), message=message,
                fact_args=turn_fact_args, conversation_id=conversation_id,
                project_id=project_id,
            )
            if result.get("ask_block"):
                # 「一起询问」：验收要点以对话块（ask_block）一次性呈现，用户一并
                # 文字回复即可，**不叠加表单卡片**（2026-11-05 用户真机反馈
                # 「怎么还是填表格」——路由层无条件挂 _agent_form，前端把表格
                # 卡片渲染在询问气泡下方，交互观感又拉回逐栏填写）。
                result["form"] = None
                result["input_components"] = []
            else:
                form = _agent_form(tool, args, result, message, str(getattr(project, "name", "") or ""))
                result["form"] = form
                result["input_components"] = _agent_input_components(form)
            result["input_channels"] = ["text", "voice", "image", "file"]
        elif result.get("ok"):
            # 首动作确认成功：若草稿里留有续办动作（上一轮多步编排存下的
            # _followup_actions），转交给下发逻辑，再清草稿（2026-11-12）。
            if not multi_action_ids:
                _draft_followup = (draft.get("args") or {}).get("_followup_actions")
                if isinstance(_draft_followup, list):
                    multi_action_ids = [str(x) for x in _draft_followup if str(x).strip()]
            await agent_tools.clear_pending(
                db, user.id, tool, conversation_id, project_id=project_id,
            )
            result["form"] = _agent_form(tool, args, result, message, str(getattr(project, "name", "") or ""))
        if auto_filled:
            # 让用户一眼看到「水印确实读到了、也确实填进表单了」，而不是被空问一句「要记什么」。
            # 但冲突提示（field_conflicts brief）里已经说过「已按水印/EXIF 填入」时不再重复
            # 清单+口号——2026-11-05 真机反馈：冲突提示 + 自动填入清单 + 模型接话三段
            # 堆成一面墙，交互感全无。
            if result.get("field_conflicts"):
                pass  # 冲突 brief 已覆盖「按水印填入」这层信息，正文交给模型接话
            else:
                head = _auto_filled_summary(auto_filled)
                if result.get("need_more"):
                    rtype = agent_tools._norm_record_type(str(args.get("record_type") or "")) or "巡视"
                    head = f"已按「{rtype}」开始记录，{head}。"
                result["reply"] = head + "\n" + str(result.get("reply") or "").strip()
        # 🔴 多步编排续办：首个动作执行成功（无 need_more）后，把剩余动作变成
        # 「一键续办」chips——前端点击即以该文本再发一轮（无需手打、不改前端）。
        # 文案指向动作目录里的中文 label，避免暴露动作 id 给用户。
        if multi_action_ids and not result.get("need_more") and result.get("ok"):
            _pending_labels: list[str] = []
            for _aid in multi_action_ids:
                _spec = agent_request_plan.ACTIONS.get(_aid)
                _label = str((_spec or {}).get("label") or "").strip() if _spec else ""
                if _label:
                    _pending_labels.append(_label)
            if _pending_labels:
                _suffix = "\n已记下还要" + "、".join(_pending_labels[:2]) + (
                    "等" if len(_pending_labels) > 2 else ""
                ) + "，点下面继续："
                result["reply"] = str(result.get("reply") or "").strip() + _suffix
                existing = list(result.get("chips") or [])
                for _label in _pending_labels:
                    if _label not in existing:
                        existing.append(_label)
                result["chips"] = existing
        # 2026-11-13：建档成功后统一补「缺项标黄 + 独立复核意见」。
        # 只加工回执，不改业务写库；复核失败/超时由 doc_quality 内部降级，绝不阻断出稿。
        if result.get("ok") and str(tool or "") == "create_work_record":
            try:
                await doc_quality.enrich_action_result(result, args, message)
            except Exception:  # noqa: BLE001
                logger.warning("doc quality enrichment failed", exc_info=True)
        return {"kind": "action", "tool": tool, "model": model_id,
                "request_plan": incoming_plan, **result}

    # Keep evidence context while asking the one missing business decision.
    # This is deliberately a draft, not a record: no database side effect is
    # performed until the user chooses a target type and completes the form.
    if needs_record_type and not tool:  # 仅在无证据（未落待定）时才追问类型
        has_evidence = bool(
            material or prefill
            or (incoming_plan.get("evidence") if isinstance(incoming_plan.get("evidence"), list) else None)
            or _plan_evidence_count(incoming_plan)
        )
        # 2026-11-06「我不要这些选择，我要完全交互」：只回一句自然语言提问，
        # 不下发记录类型下拉框 / 候选 chips / 表单卡 / 推荐项。
        return {
            "kind": "action", "tool": "create_work_record",
            "reply": _missing_record_type_reply(has_evidence),
            "card": None, "cards": [], "actions": [],
            "need_more": ["记录类型"],
            "options": {},
            "form": None,
            "input_components": [],
            "recommendations": [],
            "undo": None, "model": model_id, "request_plan": incoming_plan,
        }

    # 无工具 → 纯问答（把模型已生成的 answer 直接用；空则再调一次）
    # 流式模式下强制再走一次可流式的回答模型：路由模型那条 JSON 里的 answer
    # 没法逐字推给前端，而且它的思考过程不适合展示。
    answer = str((parsed or {}).get("answer") or "").strip()
    citations: list[dict] = []
    # 上一轮的结果卡（文书 id/标题/摘要）作为指代上下文，否则「解释一下为什么这样写」会不知道指谁
    context_hint, query_extra = await _last_result_context(
        db, user.id, conversation_id, project_id,
    )
    require_context = bool(_FOLLOWUP_REFERENCE_RE.search(message)) and not context_hint
    if not answer or _streaming_enabled():
        try:
            answer, citations = await _plain_answer_cited(
                message, req.history, req.provider, req.model, req.mode, project_id,
                context_hint=context_hint, query_extra=query_extra,
                require_context=require_context,
                scope_paths=scope_paths, scope_query=scope_query,
            )
        except model_gateway.ModelGatewayError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    else:
        # 非流式：先复用路由模型的 answer（快）。若检索到高相关材料，则带材料
        # 用深度思考回答模型重生成——让回答真正基于资料（检索增强问答质量）。
        # 只保留模型真正用到的材料作为引用；无高相关材料时就维持路由 answer。
        citations, block = _retrieve_citations(
            message, project_id, min_score=0.6, paths=scope_paths,
        )
        if citations:
            try:
                answer, citations = await _plain_answer_cited(
                    message, req.history, req.provider, req.model, req.mode, project_id,
                    context_hint=context_hint, query_extra=query_extra,
                    require_context=require_context,
                    scope_paths=scope_paths, scope_query=scope_query,
                )
            except model_gateway.ModelGatewayError as exc:
                # 重生成失败不阻断：保留路由模型的 answer 和引用列表
                logger.warning("检索增强回答失败，复用路由 answer: %s", type(exc).__name__)
    return {"kind": "chat", "tool": None, "reply": strip_markdown(answer), "card": None,
            "cards": [], "actions": [], "need_more": [], "undo": None,
            "form": None, "model": model_id, "request_plan": incoming_plan,
            "citations": citations}


@router.post("/chat")
async def agent_chat(
    req: AgentChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run one agent turn, then persist the exact assistant result for restore."""
    user_id = int(user.id)
    conversation_id = (req.conversation_id or "").strip()[:64] or None
    # 作用域先解析一次：聊天落库/trace/进度都用同一个 project_id；会话跨项目直接拒绝。
    try:
        scope = await agent_scope.resolve_agent_scope(db, user, conversation_id)
    except agent_scope.ConversationProjectMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    project_id = scope.project_id
    request_id = await agent_runtime.start_request(
        request_id=req.request_id,
        user_id=user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        endpoint="/api/agent/chat",
        stage="agent_chat",
        message=req.message,
        provider=req.provider or "",
        model=req.model or "",
    )

    async def progress_hook(stage: str, status: str, title: str, detail: dict[str, Any]) -> None:
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            endpoint="/api/agent/chat",
            stage=stage,
            status=status,
            title=title,
            detail=detail,
        )
        # 流式模式下同时把步骤推给前端（/chat 没有 emitter，这里是空操作）
        await _emit_stream({"type": "progress", "stage": stage, "status": status, "title": title})

    try:
        result = await _agent_chat_impl(req, user=user, db=db, progress_hook=progress_hook)
    except Exception as exc:
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            endpoint="/api/agent/chat",
            stage="agent_chat",
            status="failed",
            title="Agent 本轮处理失败，未返回成功结果",
            detail={"error": str(exc)[:400]},
            terminal=True,
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/chat",
            stage="agent_chat",
            status="failed",
            error=exc,
        )
        raise
    plan = result.get("request_plan") if isinstance(result, dict) else None
    conflicts = plan.get("field_conflicts") if isinstance(plan, dict) else None
    if isinstance(conflicts, list) and conflicts and isinstance(result, dict):
        labels = {
            "record_date": "日期", "project": "项目",
            "location": "地点", "section": "工程部位", "weather": "天气",
            "owner_unit": "建设单位", "construction_unit": "施工单位",
            "supervision_unit": "监理单位", "inspector": "检查人员",
        }
        # 冲突详情不再逐条铺成 5 行文字（2026-11-05 真机反馈「怎么还是这样子」）。
        # 压成一句 + 字段名；2026-11-06 用户「完全交互」后不再提「点下方选项」，
        # 改口径全靠用户直接说一句话。
        conflict_labels = [
            labels.get(str(item.get("field") or ""), str(item.get("field") or "字段"))
            for item in conflicts[:4] if isinstance(item, dict)
        ]
        brief = (f"有 {len(conflicts)} 项与照片水印不一致（{'、'.join(conflict_labels)}），"
                 "已先按水印/EXIF 填入；要改直接说一声就行。")
        result["field_conflicts"] = conflicts
        result["reply"] = brief + "\n" + str(result.get("reply") or "").strip()
    if isinstance(result, dict):
        result["request_id"] = request_id
        # 流式轮次：把思考原文/步骤/耗时附到回执上，随展示历史一起落库
        _attach_stream_meta(result)
    if isinstance(result, dict):
        # 长期偏好自动沉淀：先剥掉模型打的 <<PREF ...>> 标记（用户不该看到），
        # 再兜底做一次「同一句规矩说了两遍」的重复检测。只产生建议，绝不自动落库。
        try:
            existing_rules = [
                row.rule for row in await agent_preferences.list_active_preferences(
                    db, user_id, project_id=project_id,
                )
            ]
            if project_id:
                existing_rules.extend(
                    row.content for row in
                    await agent_project_memories.list_active_memories(db, project_id)
                )
            clean_reply, suggestion = await agent_preference_suggest.build_suggestion(
                db,
                user_id,
                req.message,
                str(result.get("reply") or ""),
                existing_rules=existing_rules,
                project_id=project_id,
            )
            if suggestion:
                result["reply"] = clean_reply
                result["preference_suggestion"] = suggestion
        except Exception:  # noqa: BLE001
            # 不 rollback：业务动作可能已提交，这里只是「锦上添花」的建议；
            # 若会话确实已坏，后面落历史的 try/except 会兜住并回滚。
            logger.exception("偏好沉淀建议生成失败，user_id=%s", user_id)
    if conversation_id and isinstance(result, dict):
        result["conversation_id"] = conversation_id
        try:
            await agent_tools.append_conversation_response(
                db, user.id, conversation_id, result, project_id=project_id,
            )
        except Exception:  # noqa: BLE001
            # The business action may already be committed. Never turn a
            # history-write failure into a misleading failed chat response.
            logger.exception("保存智能体会话回复失败，conversation_id=%s", conversation_id)
            await db.rollback()
            result["history_saved"] = False
    if isinstance(result, dict):
        cards = result.get("cards") if isinstance(result.get("cards"), list) else []
        if not cards and isinstance(result.get("card"), dict):
            cards = [result["card"]]
        needs_user = bool(result.get("need_more")) or bool(result.get("form"))
        await agent_runtime.append_progress_event(
            request_id=request_id,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            endpoint="/api/agent/chat",
            stage="agent_result",
            status="waiting" if needs_user else "completed",
            title=(
                "当前草稿已整理，等待你补充或确认"
                if needs_user else "本轮思考与处理已完成，结果已返回工作台"
            ),
            detail={
                "kind": result.get("kind"),
                "tool": result.get("tool"),
                "missing_fields": list(result.get("need_more") or [])[:32],
                "card_ids": [item.get("id") for item in cards if isinstance(item, dict) and item.get("id") is not None][:12],
                "model": result.get("model") or req.model or "",
            },
            terminal=True,
        )
        plan_result = result.get("request_plan") if isinstance(result.get("request_plan"), dict) else None
        record_refs = [
            {"kind": item.get("kind"), "id": item.get("id")}
            for item in cards
            if isinstance(item, dict) and item.get("id") is not None
        ]
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/chat",
            stage="agent_chat",
            request_plan=plan_result,
            result=result,
            created_record_refs=record_refs,
            model=str(result.get("model") or req.model or ""),
        )
    return result


@router.post("/chat/stream")
async def agent_chat_stream(
    req: AgentChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """同 ``/chat``，但用 SSE 推送思考过程与正文增量，最后用 ``done`` 带回完整回执。

    事件：``meta`` / ``progress`` / ``reasoning`` / ``answer`` / ``done`` / ``error``。
    前端在拿不到 SSE 时应回退到 ``/chat``（本端点不复用其响应，两者可并存）。
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    # 2026-11-13「说一句就走」：把这一轮登记成可查询的后台任务。表未迁移 /
    # DB 抖动时静默降级成原来的纯内存 SSE，绝不让任务登记影响聊天主流程。
    user_id = int(user.id)
    task_key: str | None = None
    try:
        task_key = await agent_task_registry.create_task(
            user_id=user_id,
            project_id=getattr(user, "project_id", None),
            conversation_id=(req.conversation_id or "").strip()[:64] or None,
            kind="agent_chat",
            request=json.loads(req.model_dump_json()),
        )
    except Exception:  # noqa: BLE001
        logger.warning("agent task registration failed; stream runs without persistence")

    async def emitter(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def runner() -> None:
        token = _STREAM_EMITTER.set(emitter)
        meta_token = _STREAM_META.set(_new_stream_meta())
        # 🔴 客户端断开后 runner 仍要跑完：不能用请求的 db/user（响应结束即失效），
        # 另开会话并重新加载用户；agent_chat 内部会把回执写进会话历史。
        try:
            await agent_task_registry.mark_running(task_key)
            async with async_session() as run_db:
                fresh_user = await run_db.get(User, user_id)
                if fresh_user is None:
                    raise RuntimeError("用户不存在，后台轮次无法继续")
                result = await agent_chat(req, user=fresh_user, db=run_db)
            await agent_task_registry.finish_task(
                task_key, result if isinstance(result, dict) else {}
            )
            await queue.put({"type": "done", "result": result})
        except asyncio.CancelledError:
            await agent_task_registry.fail_task(task_key, "任务被取消")
            raise
        except HTTPException as exc:
            await agent_task_registry.fail_task(task_key, str(exc.detail))
            await queue.put({"type": "error", "message": str(exc.detail)})
        except model_gateway.ModelGatewayError as exc:
            await agent_task_registry.fail_task(task_key, str(exc))
            await queue.put({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001 —— 错误要变成事件，不能让 SSE 静默断流
            logger.exception("agent stream failed")
            await agent_task_registry.fail_task(task_key, "服务异常")
            await queue.put({"type": "error", "message": "服务异常，请稍后重试。"})
        finally:
            _STREAM_META.reset(meta_token)
            _STREAM_EMITTER.reset(token)

    async def event_stream():
        task = asyncio.create_task(runner())
        _DETACHED_TASKS.add(task)
        agent_task_registry.register_live(task_key, task)

        def _on_done(finished) -> None:
            _DETACHED_TASKS.discard(finished)
            agent_task_registry.drop_live(task_key)

        task.add_done_callback(_on_done)
        yield _sse_pack("meta", {
            "conversation_id": req.conversation_id,
            "request_id": req.request_id,
            "task_key": task_key,
        })
        try:
            while True:
                event = await queue.get()
                kind = event.get("type") or "message"
                if kind == "done":
                    yield _sse_pack("done", event.get("result") or {})
                    break
                if kind == "error":
                    yield _sse_pack("error", {"message": event.get("message") or "服务异常"})
                    break
                yield _sse_pack(kind, event)
        finally:
            # 🔴 不再 cancel：客户端断开也让后台跑完，回执经 agent_chat 落会话历史，
            # 用户回来刷新即可看到；状态可从 /api/agent/tasks/{task_key} 查。
            if not task.done():
                logger.info("agent stream detached; task continues: %s", task_key)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/conversations")
async def agent_conversations(
    limit: int = 30,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List conversations owned by the current account in the current project."""
    scope = await agent_scope.resolve_agent_scope(db, user)
    conversations = await agent_tools.list_conversation_summaries(
        db, user.id, limit, project_id=scope.project_id,
    )
    return {"conversations": conversations, "project_id": scope.project_id}


@router.get("/conversations/{conversation_id}")
async def agent_conversation_detail(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await agent_scope.resolve_agent_scope(db, user)
    detail = await agent_tools.get_conversation_detail(
        db, user.id, conversation_id.strip()[:64], project_id=scope.project_id,
    )
    if not detail:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail


@router.delete("/conversations/{conversation_id}")
async def agent_conversation_delete(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await agent_scope.resolve_agent_scope(db, user)
    deleted = await agent_tools.delete_conversation(
        db, user.id, conversation_id.strip()[:64], project_id=scope.project_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True}


@router.post("/undo")
async def agent_undo(
    req: AgentUndoRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """聊天卡片上的「撤销」按钮：精确回滚一个本次聊天创建的对象。"""
    project_id = getattr(user, "project_id", None)
    project = await db.get(Project, project_id) if project_id else None
    ctx = agent_tools.ToolContext(db=db, user=user, project_id=project_id, project=project)
    return await agent_tools.undo_entity(ctx, req.entity, int(req.id))


@router.get("/tools")
async def agent_tools_list(user: User = Depends(get_current_user)):
    """当前可用工具清单（按权限标注可用性，便于自检与调试）。"""
    items: list[dict[str, Any]] = []
    for spec in agent_tools.TOOLS:
        items.append({
            "name": spec.name,
            "desc": spec.desc,
            "params": spec.params,
            "risk": spec.risk,
            "permission": spec.permission,
            "allowed": bool(not spec.permission or user.has_permission(spec.permission)),
        })
    return {"count": len(items), "tools": items}


@router.get("/requests")
async def list_agent_requests(
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List request-level traces owned by the current account and project."""
    rows = await agent_runtime.list_request_traces(
        db, user.id, getattr(user, "project_id", None), limit,
    )
    return {"requests": [agent_runtime.trace_payload(row) for row in rows]}


@router.get("/requests/{request_id}")
async def get_agent_request(
    request_id: str,
    after_sequence: int = 0,
    progress_only: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return one trace by id; ids are scoped to the authenticated account."""
    normalized = agent_runtime.normalize_request_id(request_id)
    row = await agent_runtime.get_request_trace(db, normalized, user.id)
    if not row:
        raise HTTPException(404, "请求追踪不存在")
    if row.project_id is not None and row.project_id != getattr(user, "project_id", None):
        raise HTTPException(403, "请求追踪不属于当前项目")
    payload = agent_runtime.trace_payload(row, after_sequence=max(0, after_sequence))
    if progress_only:
        return {
            "request_id": payload["request_id"],
            "status": payload["status"],
            "current_stage": payload["current_stage"],
            "progress_events": payload["progress_events"],
            "latest_progress_sequence": payload["latest_progress_sequence"],
        }
    return payload


@router.post("/requests/{request_id}/progress")
async def append_agent_progress(
    request_id: str,
    req: AgentProgressEventRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Append an authenticated, bounded progress event for live workbench display."""
    if req.status not in {"running", "completed", "failed", "waiting", "skipped"}:
        raise HTTPException(422, "进度状态无效")
    try:
        detail_size = len(json.dumps(req.detail, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        raise HTTPException(422, "进度详情格式无效")
    if detail_size > 16_000:
        raise HTTPException(413, "进度详情过长")
    project_id = getattr(user, "project_id", None)
    existing = await agent_runtime.get_request_trace(
        db, agent_runtime.normalize_request_id(request_id), int(user.id),
    )
    if existing and existing.project_id is not None and existing.project_id != project_id:
        raise HTTPException(403, "请求追踪不属于当前项目")
    event = await agent_runtime.append_progress_event(
        request_id=request_id,
        user_id=int(user.id),
        project_id=project_id,
        endpoint="/api/agent/progress",
        event_id=req.event_id,
        stage=req.stage,
        status=req.status,
        title=req.title,
        detail=req.detail,
        terminal=req.terminal,
    )
    return {"event": event}


@router.get("/executions/{execution_id}")
async def get_agent_execution(
    execution_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read a durable execution result after timeout, disconnect or restart."""
    normalized = agent_runtime.normalize_execution_id(execution_id)
    row = (await db.execute(select(AgentFlowInstance).where(
        AgentFlowInstance.execution_id == normalized,
        AgentFlowInstance.user_id == user.id,
        AgentFlowInstance.project_id == getattr(user, "project_id", None),
    ))).scalars().first()
    if not row:
        raise HTTPException(404, "执行实例不存在")
    return agent_runtime.flow_payload(row)


class AgentClearDraftsRequest(BaseModel):
    """放弃草稿：指定 tool 只清该工具草稿；省略则清空当前用户全部待补全草稿。"""
    tool: Optional[str] = None
    conversation_id: Optional[str] = None


@router.get("/drafts")
async def agent_drafts(
    conversation_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户当前项目下所有未过期草稿（聊天即操作的半成品，落库持久化、跨会话可见）。"""
    scope = await agent_scope.resolve_agent_scope(db, user)
    drafts = await agent_tools.list_pending(
        db, user.id, (conversation_id or "").strip()[:64] or None,
        project_id=scope.project_id,
    )
    return {"count": len(drafts), "drafts": drafts, "project_id": scope.project_id}


@router.post("/drafts/clear")
async def agent_clear_drafts(
    req: AgentClearDraftsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """放弃草稿：清掉当前用户当前项目下指定工具或全部待补全草稿。"""
    scope = await agent_scope.resolve_agent_scope(db, user)
    await agent_tools.clear_pending(
        db, user.id, req.tool, (req.conversation_id or "").strip()[:64] or None,
        project_id=scope.project_id,
    )
    return {"ok": True}


def _review_plan_payload(row: AgentReviewPlan) -> dict[str, Any]:
    try:
        plan = json.loads(row.plan_json or "{}")
    except (TypeError, ValueError):
        plan = {}
    try:
        execution_result = json.loads(row.execution_result_json or "null")
    except (TypeError, ValueError):
        execution_result = None
    request_plan = plan.get("request_plan") if isinstance(plan.get("request_plan"), dict) else {}
    request_id = agent_runtime.normalize_request_id(
        plan.get("request_id") or request_plan.get("request_id")
        or f"legacy-review-plan-{row.id}",
    )
    from app.services.agent_form_requirements import enrich_plan

    view_plan = enrich_plan(plan)
    return {
        "id": row.id, "conversation_id": row.conversation_id,
        "request_id": request_id,
        "execution_id": agent_runtime.review_execution_id(
            row.id, row.confirmed_revision or row.revision,
        ),
        "revision": row.revision, "status": row.status,
        "confirmed_revision": row.confirmed_revision,
        "executed_revision": row.executed_revision,
        "execution_result": execution_result,
        "plan": view_plan, "issues": agent_review_plan.review_issues(plan),
        "updated_at": row.updated_at.isoformat(),
    }


@router.post("/review-plans")
async def create_review_plan(
    req: ReviewPlanCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a persistent, unconfirmed multi-event review plan."""
    user_id = int(user.id)
    conversation_id = req.conversation_id.strip()[:64]
    if not conversation_id:
        raise HTTPException(400, "conversation_id 不能为空")
    # 与 /agent/chat 同一作用域口径：项目/成员/会话绑定校验，禁止跨项目续写计划。
    try:
        scope = await agent_scope.resolve_agent_scope(db, user, conversation_id)
    except agent_scope.ConversationProjectMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    project_id = scope.project_id
    request_plan = req.plan.get("request_plan") if isinstance(req.plan.get("request_plan"), dict) else {}
    request_id = agent_runtime.normalize_request_id(
        req.request_id or req.plan.get("request_id") or request_plan.get("request_id"),
    )
    stored_plan = agent_review_plan.pin_form_contracts({**req.plan, "request_id": request_id})
    project = await db.get(Project, project_id) if project_id else None
    if project:
        stored_plan["system_project"] = project_context(project)
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=project_id,
        conversation_id=conversation_id,
        endpoint="/api/agent/review-plans",
        stage="review_plan_save",
        message=stored_plan.get("request_text") or "",
    )
    # 带 attachments 建记录的这轮不走 /agent/chat，用户消息（含图）必须在这里落进
    # 展示历史，否则「查看历史记录」看不到这条消息和照片。落库失败不阻断建计划。
    # 图片 URL 与 /agent/chat 同口径校验（防挂他人照片进展示历史/证据链）。
    _display_images = await _validate_chat_images(db, user, project_id, req.images)
    if str(stored_plan.get("request_text") or "").strip() or _display_images:
        try:
            await agent_tools.record_conversation_message(
                db, user_id, conversation_id,
                str(stored_plan.get("request_text") or ""),
                images=_display_images,
                project_id=project_id,
            )
        except Exception:  # noqa: BLE001 - 历史落库失败不能挡记录整理
            logger.warning("persist review-plan user turn failed", exc_info=True)
    try:
        agent_review_plan.validate_plan(stored_plan)
        await _validate_review_plan_media(db, user, stored_plan)
        existing = (await db.execute(select(AgentReviewPlan).where(
            AgentReviewPlan.user_id == user_id,
            AgentReviewPlan.project_id == project_id,
            AgentReviewPlan.conversation_id == conversation_id,
        ).with_for_update())).scalars().first()
        if existing:
            if existing.status != "executed":
                raise HTTPException(409, detail={
                    "code": "ACTIVE_PLAN_EXISTS",
                    "message": "该会话已有整理计划，请读取并修订现有计划",
                    "plan_id": existing.id,
                    "revision": existing.revision,
                })
            existing.revision += 1
            existing.status = "draft"
            existing.plan_json = json.dumps(stored_plan, ensure_ascii=False)
            existing.confirmed_revision = None
            existing.executed_revision = None
            existing.execution_result_json = None
            existing.updated_at = datetime.now()
            row = existing
        else:
            row = AgentReviewPlan(
                user_id=user_id, conversation_id=conversation_id,
                project_id=project_id,
                revision=1, status="draft", plan_json=json.dumps(stored_plan, ensure_ascii=False),
            )
            db.add(row)
        await db.commit()
        await db.refresh(row)
        payload = _review_plan_payload(row)
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans",
            stage="review_plan_save",
            request_plan=request_plan,
            result={
                "plan_id": row.id,
                "revision": row.revision,
                "status": row.status,
                "event_count": len(stored_plan.get("events") or []),
                "evidence_count": len(stored_plan.get("evidence") or []),
            },
            review_plan_id=row.id,
        )
        return payload
    except IntegrityError as exc:
        await db.rollback()
        # The row lock cannot lock a row that does not exist. The unique
        # session index is the final arbiter when two first saves race.
        raced = (await db.execute(select(AgentReviewPlan).where(
            AgentReviewPlan.user_id == user_id,
            AgentReviewPlan.project_id == project_id,
            AgentReviewPlan.conversation_id == conversation_id,
        ))).scalars().first()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans",
            stage="review_plan_save",
            status="failed",
            error=exc,
        )
        if raced and raced.status != "executed":
            raise HTTPException(409, detail={
                "code": "ACTIVE_PLAN_EXISTS",
                "message": "该会话已有整理计划，请读取并修订现有计划",
                "plan_id": raced.id,
                "revision": raced.revision,
            }) from exc
        raise
    except Exception as exc:
        await db.rollback()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans",
            stage="review_plan_save",
            status="failed",
            error=exc,
        )
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise


@router.get("/review-plans")
async def list_review_plans(
    conversation_id: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AgentReviewPlan).where(
        AgentReviewPlan.user_id == user.id,
        AgentReviewPlan.project_id == getattr(user, "project_id", None),
    )
    if conversation_id and conversation_id.strip():
        stmt = stmt.where(AgentReviewPlan.conversation_id == conversation_id.strip()[:64])
    rows = (await db.execute(stmt.order_by(AgentReviewPlan.updated_at.desc()))).scalars().all()
    return {"plans": [_review_plan_payload(row) for row in rows]}


@router.get("/review-plans/{plan_id}")
async def get_review_plan(
    plan_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user.id,
    ))).scalars().first()
    if not row:
        raise HTTPException(404, "整理计划不存在")
    if row.project_id != getattr(user, "project_id", None):
        raise HTTPException(403, "整理计划不属于当前项目")
    return _review_plan_payload(row)


@router.put("/review-plans/{plan_id}")
async def update_review_plan(
    plan_id: int,
    req: ReviewPlanUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user.id)
    project_id = getattr(user, "project_id", None)
    row = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user_id,
    ).with_for_update())).scalars().first()
    if not row:
        raise HTTPException(404, "整理计划不存在")
    if row.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
    if row.status != "draft":
        raise HTTPException(409, "已确认的整理计划不能修改")
    if row.revision != req.revision:
        raise HTTPException(409, "整理计划已在其他请求中更新，请重新加载")
    try:
        current_plan = json.loads(row.plan_json or "{}")
        request_id = agent_runtime.normalize_request_id(
            req.request_id or req.plan.get("request_id") or current_plan.get("request_id"),
        )
        stored_plan = agent_review_plan.pin_form_contracts(
            {**req.plan, "request_id": request_id}, preserve_existing=True,
        )
        project = await db.get(Project, row.project_id) if row.project_id else None
        if project:
            stored_plan["system_project"] = project_context(project)
        agent_review_plan.validate_plan(stored_plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        endpoint="/api/agent/review-plans/{plan_id}",
        stage="review_plan_save",
        message=stored_plan.get("request_text") or "",
    )
    try:
        await _validate_review_plan_media(db, user, stored_plan)
        row.revision += 1
        row.plan_json = json.dumps(stored_plan, ensure_ascii=False)
        row.confirmed_revision = None
        row.updated_at = datetime.now()
        await db.commit()
        await db.refresh(row)
        payload = _review_plan_payload(row)
        stored_request_plan = (
            stored_plan.get("request_plan")
            if isinstance(stored_plan.get("request_plan"), dict) else {}
        )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}",
            stage="review_plan_save",
            request_plan=stored_request_plan,
            result={
                "plan_id": row.id,
                "revision": row.revision,
                "status": row.status,
                "event_count": len(stored_plan.get("events") or []),
                "evidence_count": len(stored_plan.get("evidence") or []),
            },
            review_plan_id=row.id,
        )
        return payload
    except Exception as exc:
        await db.rollback()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}",
            stage="review_plan_save",
            status="failed",
            error=exc,
        )
        raise


@router.post("/review-plans/{plan_id}/confirm")
async def confirm_review_plan(
    plan_id: int,
    req: ReviewPlanConfirm,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm only the exact reviewed revision; formal writes are a later command."""
    user_id = int(user.id)
    project_id = getattr(user, "project_id", None)
    row = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user_id,
    ).with_for_update())).scalars().first()
    if not row:
        raise HTTPException(404, "整理计划不存在")
    if row.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
    if row.status == "confirmed" and row.confirmed_revision == req.revision:
        return _review_plan_payload(row)
    if row.status != "draft" or row.revision != req.revision:
        raise HTTPException(409, "整理计划版本已变化，请重新核对")
    plan = json.loads(row.plan_json or "{}")
    try:
        agent_review_plan.validate_plan(plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _validate_review_plan_media(db, user, plan)
    issues = agent_review_plan.review_issues(plan)
    if issues:
        raise HTTPException(422, {"message": "整理计划仍有待确认项", "issues": issues})
    stored_request_plan = plan.get("request_plan") if isinstance(plan.get("request_plan"), dict) else {}
    request_id = agent_runtime.normalize_request_id(
        req.request_id or plan.get("request_id") or stored_request_plan.get("request_id")
        or f"legacy-review-plan-{plan_id}",
    )
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        endpoint="/api/agent/review-plans/{plan_id}/confirm",
        stage="review_confirm",
        message=plan.get("request_text") or "",
    )
    try:
        row.status = "confirmed"
        row.confirmed_revision = row.revision
        row.updated_at = datetime.now()
        await db.commit()
        await db.refresh(row)
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/confirm",
            stage="review_confirm",
            result={"plan_id": plan_id, "revision": row.confirmed_revision, "status": row.status},
            review_plan_id=plan_id,
        )
    except Exception as exc:
        await db.rollback()
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/confirm",
            stage="review_confirm",
            status="failed",
            error=exc,
            review_plan_id=plan_id,
        )
        raise
    return _review_plan_payload(row)


@router.post("/review-plans/{plan_id}/execute")
async def execute_review_plan(
    plan_id: int,
    req: ReviewPlanConfirm,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute one confirmed revision with durable, idempotent recovery."""
    user_id = int(user.id)
    preview = (await db.execute(select(AgentReviewPlan).where(
        AgentReviewPlan.id == plan_id, AgentReviewPlan.user_id == user_id,
    ))).scalars().first()
    if not preview:
        raise HTTPException(404, "整理计划不存在")
    project_id = getattr(user, "project_id", None)
    if preview.project_id != project_id:
        raise HTTPException(403, "整理计划不属于当前项目")
    preview_plan = json.loads(preview.plan_json or "{}")
    stored_request_plan = (
        preview_plan.get("request_plan")
        if isinstance(preview_plan.get("request_plan"), dict) else {}
    )
    request_id = agent_runtime.normalize_request_id(
        req.request_id or preview_plan.get("request_id") or stored_request_plan.get("request_id")
        or f"legacy-review-plan-{plan_id}",
    )
    execution_id = agent_runtime.review_execution_id(plan_id, req.revision)
    await agent_runtime.start_request(
        request_id=request_id,
        user_id=user_id,
        project_id=project_id,
        conversation_id=preview.conversation_id,
        endpoint="/api/agent/review-plans/{plan_id}/execute",
        stage="review_execute",
        message=preview_plan.get("request_text") or "",
    )
    execution_prepared = False
    try:
        flow = await agent_runtime.prepare_execution(
            db,
            execution_id=execution_id,
            request_id=request_id,
            flow_type="review_plan_execute",
            scope_id=f"review-plan:{plan_id}",
            user_id=user_id,
            project_id=project_id,
            conversation_id=preview.conversation_id,
            expected_revision=req.revision,
        )
        execution_prepared = True
        locked_flow = await agent_runtime.lock_execution(db, execution_id)
        if locked_flow is None:
            raise HTTPException(409, "执行实例已丢失，请重新发起")
        if locked_flow.status == "COMMITTED":
            result = agent_runtime.execution_result(locked_flow)
            await db.commit()
            await db.refresh(preview)
            await agent_runtime.finish_request(
                request_id=request_id,
                user_id=user_id,
                endpoint="/api/agent/review-plans/{plan_id}/execute",
                stage="review_execute",
                result=result,
                review_plan_id=plan_id,
                created_record_refs=result.get("records") or [],
            )
            return _review_plan_payload(preview)

        agent_runtime.mark_execution_running(locked_flow)
        row = (await db.execute(select(AgentReviewPlan).where(
            AgentReviewPlan.id == plan_id,
            AgentReviewPlan.user_id == user_id,
        ).with_for_update())).scalars().first()
        if not row:
            raise HTTPException(404, "整理计划不存在")
        if row.project_id != project_id:
            raise HTTPException(403, "整理计划不属于当前项目")
        from app.services.agent_review_execute import execute_confirmed_plan

        result = await execute_confirmed_plan(db, row, user, req.revision)
        agent_runtime.mark_execution_committed(locked_flow, result)
        # Formal records, review-plan terminal state and COMMITTED are atomic.
        await db.commit()
        await db.refresh(row)
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/execute",
            stage="review_execute",
            result=result,
            review_plan_id=plan_id,
            created_record_refs=result.get("records") or [],
        )
        return _review_plan_payload(row)
    except Exception as exc:
        await db.rollback()
        if execution_prepared:
            await agent_runtime.mark_execution_failed(
                execution_id=execution_id, error=exc,
                user_id=user_id, project_id=project_id,
            )
        await agent_runtime.finish_request(
            request_id=request_id,
            user_id=user_id,
            endpoint="/api/agent/review-plans/{plan_id}/execute",
            stage="review_execute",
            status="failed",
            error=exc,
            review_plan_id=plan_id,
        )
        raise


# ---------------------------------------------------------------------------
# 用户长期偏好管理端点（跨会话注入 agent 上下文）
# ---------------------------------------------------------------------------
class PreferenceCreateRequest(BaseModel):
    rule: str = Field(min_length=1, max_length=500)
    category: str = "general"
    source: str = "user_declared"
    # None=全局个人记忆（跟人走）；非空=仅当前项目生效（必须等于服务端解析出的项目）
    project_id: Optional[int] = None


class PreferenceToggleRequest(BaseModel):
    active: bool


class PreferenceUpdateRequest(BaseModel):
    rule: str = Field(min_length=1, max_length=500)
    category: str = "general"


class PreferenceScopeRequest(BaseModel):
    """个人记忆作用域：global=全局跟人走；project=仅当前项目生效。"""
    scope: str = "global"


class ProjectMemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    category: str = "general"
    kind: str = "rule"


class ProjectMemoryUpdateRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = None
    kind: Optional[str] = None
    active: Optional[bool] = None
    pinned: Optional[bool] = None


class ProjectMemoryApproveRequest(BaseModel):
    approve: bool = True


def _preference_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "rule": row.rule,
        "category": row.category,
        "source": row.source,
        "active": row.active,
        "project_id": getattr(row, "project_id", None),
        "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
    }


async def _project_memory_payloads(db: AsyncSession, rows: list[Any]) -> list[dict[str, Any]]:
    """项目规约 payload；批量补 author_name，避免 N+1 查询。"""
    author_ids = {
        int(row.author_id) for row in rows
        if getattr(row, "author_id", None) is not None
    }
    names: dict[int, str] = {}
    if author_ids:
        users = (await db.execute(
            select(User).where(User.id.in_(author_ids))
        )).scalars().all()
        names = {int(item.id): (item.real_name or item.username or "") for item in users}
    return [
        {
            "id": row.id,
            "content": row.content,
            "kind": row.kind,
            "category": row.category,
            "source": row.source,
            "status": row.status,
            "active": row.active,
            "pinned": row.pinned,
            "author_id": row.author_id,
            "author_name": names.get(int(row.author_id or 0), ""),
            "approved_by": row.approved_by,
            "use_count": int(getattr(row, "use_count", 0) or 0),
            "last_used_at": (
                row.last_used_at.isoformat() if getattr(row, "last_used_at", None) else None
            ),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


async def _project_memory_scope(db: AsyncSession, user: User):
    """项目规约接口的统一作用域：必须有当前项目且是成员/管理员。"""
    try:
        return await agent_scope.resolve_project_memory_scope(db, user)
    except agent_scope.ProjectContextError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _source_for_project_memory(scope) -> str:
    if scope.is_super_admin:
        return "system"
    if "chief_supervisor" in scope.role_codes:
        return "chief_declared"
    if "deputy_chief" in scope.role_codes:
        return "deputy_declared"
    return "member_suggested"


async def _project_memory_payload_one(db: AsyncSession, row: Any) -> dict[str, Any]:
    payloads = await _project_memory_payloads(db, [row])
    return payloads[0] if payloads else {}


@router.get("/preferences")
async def list_preferences(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    category: str | None = None,
):
    """List this user's personal memories visible in the current project."""
    scope = await agent_scope.resolve_agent_scope(db, user)
    rows = await agent_preferences.list_all_preferences(
        db, user.id, project_id=scope.project_id,
    )
    payload = [_preference_payload(r) for r in rows]
    if category:
        payload = [p for p in payload if p["category"] == category]
    project = await db.get(Project, scope.project_id) if scope.project_id else None
    return {
        "preferences": payload,
        "project_id": scope.project_id,
        "project_name": str(getattr(project, "name", "") or ""),
        "can_manage_project_memory": scope.can_manage_project_memory,
    }


@router.post("/preferences")
async def create_preference(
    req: PreferenceCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add (or reactivate) a persistent personal memory for the current user."""
    scope = await agent_scope.resolve_agent_scope(db, user)
    binding_project_id: int | None = None
    if req.project_id is not None:
        if scope.project_id is None or int(req.project_id) != int(scope.project_id):
            raise HTTPException(status_code=400, detail="只能把个人记忆绑定到当前项目")
        binding_project_id = scope.project_id
    try:
        row = await agent_preferences.upsert_preference(
            db,
            user.id,
            req.rule,
            category=req.category,
            source=req.source,
            project_id=binding_project_id,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _preference_payload(row)


@router.post("/preferences/{preference_id}/scope")
async def set_personal_memory_scope(
    preference_id: int,
    req: PreferenceScopeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把个人记忆改为「全局」或「仅本项目生效」。"""
    if req.scope not in {"global", "project"}:
        raise HTTPException(status_code=422, detail="scope 只支持 global / project")
    scope = await agent_scope.resolve_agent_scope(db, user)
    target_project_id: int | None = None
    if req.scope == "project":
        if scope.project_id is None:
            raise HTTPException(status_code=400, detail="当前没有项目，无法绑定到项目")
        target_project_id = scope.project_id
    try:
        row = await agent_preferences.set_preference_scope(
            db, user.id, preference_id, target_project_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="记忆不存在或不属于当前账号")
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _preference_payload(row)


@router.put("/preferences/{preference_id}")
async def edit_preference(
    preference_id: int,
    req: PreferenceUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit one saved memory belonging to the current account."""
    try:
        row = await agent_preferences.update_preference(
            db, user.id, preference_id, req.rule, category=req.category
        )
        if row is None:
            raise HTTPException(status_code=404, detail="记忆不存在或不属于当前账号")
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _preference_payload(row)


@router.delete("/preferences/{preference_id}")
async def delete_preference(
    preference_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete one memory belonging to the current account."""
    deleted = await agent_preferences.delete_preference(db, user.id, preference_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在或不属于当前账号")
    await db.commit()
    return {"ok": True, "id": preference_id}


@router.patch("/preferences/{preference_id}")
@router.post("/preferences/{preference_id}")
async def toggle_preference(
    preference_id: int,
    req: PreferenceToggleRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enable/disable one preference row (soft delete)."""
    updated = await agent_preferences.set_preference_active(
        db, user.id, preference_id, req.active
    )
    if not updated:
        raise HTTPException(status_code=404, detail="偏好不存在或不属于当前用户")
    await db.commit()
    return {"ok": True, "id": preference_id, "active": req.active}


# ---------------------------------------------------------------------------
# 项目规约（项目级共享规则）—— 总监/总监代表维护，全项目成员读取
# ---------------------------------------------------------------------------


@router.get("/project-memories")
async def list_project_memories(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List project memories. Members read active only; managers also see
    disabled rows and pending member suggestions."""
    scope = await _project_memory_scope(db, user)
    can_manage = scope.can_manage_project_memory
    if can_manage:
        rows = await agent_project_memories.list_management_memories(db, scope.project_id)
        pending = await agent_project_memories.list_pending_memories(db, scope.project_id)
        audit = await agent_project_memories.list_audit(db, scope.project_id)
        usage = await agent_project_memories.usage_summary(db, scope.project_id)
    else:
        rows = await agent_project_memories.list_active_memories(db, scope.project_id)
        pending = []
        audit = []
        usage = []
    project = await db.get(Project, scope.project_id)
    return {
        "project_id": scope.project_id,
        "project_name": str(getattr(project, "name", "") or ""),
        "can_manage": can_manage,
        "maintainers": await agent_project_memories.project_maintainers(db, scope.project_id),
        "memories": await _project_memory_payloads(db, rows),
        "pending": await _project_memory_payloads(db, pending),
        "usage": usage,
        "audit": audit,
    }


@router.post("/project-memories")
async def create_project_memory(
    req: ProjectMemoryCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a project memory. Members create a pending suggestion instead."""
    scope = await _project_memory_scope(db, user)
    can_manage = scope.can_manage_project_memory
    try:
        row = await agent_project_memories.create_memory(
            db,
            scope.project_id,
            req.content,
            actor_id=user.id,
            actor_roles=scope.role_codes,
            category=req.category,
            kind=req.kind,
            status=(
                agent_project_memories.STATUS_ACTIVE if can_manage
                else agent_project_memories.STATUS_PENDING
            ),
            source=_source_for_project_memory(scope),
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "ok": True,
        "pending": row.status == agent_project_memories.STATUS_PENDING,
        "message": (
            "已提交给项目总监确认"
            if row.status == agent_project_memories.STATUS_PENDING
            else "已加入项目规约，全项目下一轮对话生效"
        ),
        "memory": await _project_memory_payload_one(db, row),
    }


@router.put("/project-memories/{memory_id}")
@router.patch("/project-memories/{memory_id}")
@router.post("/project-memories/{memory_id}")
async def update_project_memory(
    memory_id: int,
    req: ProjectMemoryUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit / enable / disable / pin one project memory (manager only)."""
    scope = await _project_memory_scope(db, user)
    if not scope.can_manage_project_memory:
        raise HTTPException(status_code=403, detail="只有项目总监/总监代表可以维护项目规约")
    try:
        row = await agent_project_memories.update_memory(
            db, scope.project_id, memory_id,
            content=req.content, category=req.category, kind=req.kind,
            pinned=req.pinned, actor_id=user.id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="项目规约不存在")
        if req.active is not None:
            row = await agent_project_memories.set_memory_active(
                db, scope.project_id, memory_id, req.active, actor_id=user.id,
            )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "memory": await _project_memory_payload_one(db, row)}


@router.delete("/project-memories/{memory_id}")
async def delete_project_memory(
    memory_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    scope = await _project_memory_scope(db, user)
    if not scope.can_manage_project_memory:
        raise HTTPException(status_code=403, detail="只有项目总监/总监代表可以维护项目规约")
    deleted = await agent_project_memories.delete_memory(
        db, scope.project_id, memory_id, actor_id=user.id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="项目规约不存在")
    await db.commit()
    return {"ok": True, "id": memory_id}


@router.post("/project-memories/{memory_id}/approve")
async def approve_project_memory(
    memory_id: int,
    req: ProjectMemoryApproveRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve/reject a member's pending project-memory suggestion."""
    scope = await _project_memory_scope(db, user)
    if not scope.can_manage_project_memory:
        raise HTTPException(status_code=403, detail="只有项目总监/总监代表可以审批项目规约")
    try:
        row = await agent_project_memories.approve_memory(
            db, scope.project_id, memory_id,
            approve=req.approve, approver_id=user.id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="项目规约不存在")
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "memory": await _project_memory_payload_one(db, row)}


@router.post("/preferences/{preference_id}/publish-to-project")
async def publish_preference_to_project(
    preference_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """总监把自己的个人记忆发布为项目规约（复制，保留来源 id）。"""
    scope = await _project_memory_scope(db, user)
    if not scope.can_manage_project_memory:
        raise HTTPException(status_code=403, detail="只有项目总监/总监代表可以发布项目规约")
    preference = await agent_preferences.get_preference(db, user.id, preference_id)
    if preference is None:
        raise HTTPException(status_code=404, detail="个人记忆不存在或不属于当前账号")
    try:
        row = await agent_project_memories.publish_preference(
            db, scope.project_id, preference,
            actor_id=user.id, actor_roles=scope.role_codes,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "ok": True,
        "project_id": scope.project_id,
        "memory": await _project_memory_payload_one(db, row),
    }
