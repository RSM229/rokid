"""会议纪要路由（P3.2）。

GD-B-217 会议纪要的 CRUD + 议题跟踪 + AI 整理草稿：
- GET    /topics/pending   当前用户所有未完成议题（跨会议聚合，议题跟踪）
- POST   /ai-draft         把录音转写文本结构化为纪要草稿（不落库）
- 其余为标准 CRUD；topics 为 JSON 数组 [{topic, conclusion, owner, deadline, status}]
"""
import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.meeting import MeetingMinute
from app.models.user import User
from app.routers.auth import get_current_user
from app.services.ai_client import AICenterClient

logger = logging.getLogger(__name__)
router = APIRouter()
ai_client = AICenterClient()

TOPIC_STATUS = ("待办", "进行中", "已完成")
MEETING_TYPES = ("例会", "专题会", "协调会", "交底会", "其他")


class TopicItem(BaseModel):
    topic: str = ""
    conclusion: str = ""
    owner: str = ""
    deadline: str = ""
    status: str = "待办"


class MeetingCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    meeting_type: str = "例会"
    meeting_date: str = ""
    location: str = ""
    host: str = ""
    attendees: str = ""
    topics: list[TopicItem] = []
    content: str = ""
    source: str = "manual"
    project_id: Optional[int] = None


class MeetingUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    meeting_type: Optional[str] = None
    meeting_date: Optional[str] = None
    location: Optional[str] = None
    host: Optional[str] = None
    attendees: Optional[str] = None
    topics: Optional[list[TopicItem]] = None
    content: Optional[str] = None


class AiDraftRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=20000)
    hint: str = ""  # 可选提示，如会议类型、项目背景


def _parse_topics(raw: str) -> list[dict]:
    try:
        items = json.loads(raw or "[]")
        return items if isinstance(items, list) else []
    except (ValueError, TypeError):
        return []


def _dump_topics(topics: list[TopicItem]) -> str:
    cleaned = []
    for t in topics:
        status = t.status if t.status in TOPIC_STATUS else "待办"
        cleaned.append({
            "topic": t.topic.strip(),
            "conclusion": t.conclusion.strip(),
            "owner": t.owner.strip(),
            "deadline": t.deadline.strip(),
            "status": status,
        })
    return json.dumps(cleaned, ensure_ascii=False)


def _serialize(m: MeetingMinute, with_detail: bool = False) -> dict:
    topics = _parse_topics(m.topics)
    data = {
        "id": m.id,
        "title": m.title,
        "meeting_type": m.meeting_type,
        "meeting_date": m.meeting_date,
        "location": m.location,
        "host": m.host,
        "source": m.source,
        "form_code": m.form_code,
        "topic_total": len(topics),
        "topic_open": sum(1 for t in topics if t.get("status") != "已完成"),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }
    if with_detail:
        data["attendees"] = m.attendees
        data["topics"] = topics
        data["content"] = m.content
        data["project_id"] = m.project_id
    return data


async def _get_meeting(db: AsyncSession, meeting_id: int, user: User) -> MeetingMinute:
    m = await db.get(MeetingMinute, meeting_id)
    if m is None or m.user_id != user.id:
        raise HTTPException(status_code=404, detail="会议纪要不存在")
    return m


@router.get("")
async def list_meetings(
    keyword: Optional[str] = None,
    meeting_type: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(MeetingMinute).where(MeetingMinute.user_id == user.id)
    if keyword:
        query = query.where(MeetingMinute.title.like(f"%{keyword.strip()}%"))
    if meeting_type and meeting_type in MEETING_TYPES:
        query = query.where(MeetingMinute.meeting_type == meeting_type)
    rows = (await db.execute(
        query.order_by(MeetingMinute.meeting_date.desc(), MeetingMinute.id.desc()).limit(200)
    )).scalars().all()
    return {"total": len(rows), "items": [_serialize(m) for m in rows]}


@router.get("/topics/pending")
async def pending_topics(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """议题跟踪：跨会议聚合所有未完成议题，按截止日期排序（空日期排最后）。"""
    rows = (await db.execute(
        select(MeetingMinute)
        .where(MeetingMinute.user_id == user.id)
        .order_by(MeetingMinute.meeting_date.desc())
        .limit(200)
    )).scalars().all()
    items: list[dict] = []
    for m in rows:
        for idx, t in enumerate(_parse_topics(m.topics)):
            if t.get("status") == "已完成":
                continue
            items.append({
                "meeting_id": m.id,
                "meeting_title": m.title,
                "meeting_date": m.meeting_date,
                "topic_index": idx,
                "topic": t.get("topic", ""),
                "conclusion": t.get("conclusion", ""),
                "owner": t.get("owner", ""),
                "deadline": t.get("deadline", ""),
                "status": t.get("status", "待办"),
            })
    items.sort(key=lambda x: (x["deadline"] == "", x["deadline"]))
    return {"total": len(items), "items": items}


@router.post("")
async def create_meeting(
    req: MeetingCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    m = MeetingMinute(
        user_id=user.id,
        project_id=req.project_id if req.project_id is not None else user.project_id,
        title=(req.title or "").strip() or "工地例会纪要",
        meeting_type=req.meeting_type if req.meeting_type in MEETING_TYPES else "例会",
        meeting_date=req.meeting_date.strip(),
        location=req.location.strip(),
        host=req.host.strip(),
        attendees=req.attendees.strip(),
        topics=_dump_topics(req.topics),
        content=req.content,
        source=req.source if req.source in ("manual", "ai") else "manual",
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _serialize(m, with_detail=True)


@router.get("/{meeting_id}")
async def get_meeting(
    meeting_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    m = await _get_meeting(db, meeting_id, user)
    return _serialize(m, with_detail=True)


@router.put("/{meeting_id}")
async def update_meeting(
    meeting_id: int,
    req: MeetingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    m = await _get_meeting(db, meeting_id, user)
    if req.title is not None:
        m.title = req.title.strip() or "工地例会纪要"
    if req.meeting_type is not None and req.meeting_type in MEETING_TYPES:
        m.meeting_type = req.meeting_type
    if req.meeting_date is not None:
        m.meeting_date = req.meeting_date.strip()
    if req.location is not None:
        m.location = req.location.strip()
    if req.host is not None:
        m.host = req.host.strip()
    if req.attendees is not None:
        m.attendees = req.attendees.strip()
    if req.topics is not None:
        m.topics = _dump_topics(req.topics)
    if req.content is not None:
        m.content = req.content
    await db.commit()
    await db.refresh(m)
    return _serialize(m, with_detail=True)


@router.delete("/{meeting_id}")
async def delete_meeting(
    meeting_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    m = await _get_meeting(db, meeting_id, user)
    await db.delete(m)
    await db.commit()
    return {"detail": "会议纪要已删除", "id": meeting_id}


_DRAFT_SYSTEM = (
    "你是资深监理工程师，负责把工地会议的录音转写文本整理成结构化会议纪要。"
    "只输出 JSON（不要 Markdown 代码围栏、不要解释），字段如下：\n"
    '{"title": "会议名称", "meeting_type": "例会|专题会|协调会|交底会|其他", '
    '"meeting_date": "YYYY-MM-DD 或空串", "location": "", "host": "", '
    '"attendees": "参会单位及人员，顿号分隔", '
    '"topics": [{"topic": "议题", "conclusion": "议定结论", "owner": "责任单位/人", '
    '"deadline": "YYYY-MM-DD 或空串"}], '
    '"content": "Markdown 正文：议题讨论要点、议定事项、下一步安排"}\n'
    "要求：不编造转写中不存在的人名、日期与数据；无法确定的字段留空串；"
    "议题按会议讨论顺序排列，待办事项必须有 owner。"
)


def _extract_json(text: str) -> dict:
    """从 LLM 输出中防御性提取 JSON 对象。"""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 返回内容不是 JSON")
    return json.loads(cleaned[start:end + 1])


@router.post("/ai-draft")
async def ai_draft(
    req: AiDraftRequest,
    user: User = Depends(get_current_user),
):
    """把录音转写文本结构化为纪要草稿（不落库，由前端确认后走 POST 创建）。"""
    user_content = req.transcript.strip()
    if req.hint:
        user_content = f"背景提示：{req.hint.strip()}\n\n转写文本：\n{user_content}"
    try:
        raw = await ai_client.chat_completion(
            [
                {"role": "system", "content": _DRAFT_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=3000,
        )
        draft = _extract_json(raw)
    except ValueError as exc:
        logger.warning("会议纪要 AI 整理 JSON 解析失败: %s", exc)
        raise HTTPException(status_code=502, detail="AI 整理结果格式异常，请重试") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"AI 整理不可用：{exc}") from exc

    topics = draft.get("topics")
    if not isinstance(topics, list):
        topics = []
    draft["topics"] = [
        {
            "topic": str(t.get("topic", "")).strip(),
            "conclusion": str(t.get("conclusion", "")).strip(),
            "owner": str(t.get("owner", "")).strip(),
            "deadline": str(t.get("deadline", "")).strip(),
            "status": "待办",
        }
        for t in topics if isinstance(t, dict) and str(t.get("topic", "")).strip()
    ]
    if draft.get("meeting_type") not in MEETING_TYPES:
        draft["meeting_type"] = "例会"
    return draft
