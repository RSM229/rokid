"""智能体材料收集：先收集 → 理解 → 分类 → 看够不够 → 再收集 → 最后回答。

2026-11-27 用户口径：工作台要先读最近几轮对话，把能收集的材料（图片/视频/音频/
文档、项目资料库、个人资料库、待办、项目记忆、个人记忆、近期记录与文书）先收起来，
分类后判断够不够，不够再补一轮，整个过程按「思维链 → 工具调用 → 调用结果」展示。

设计约束：
* 只读，不写库、不改业务状态；权限沿用各模块自己的可见性（资料库按成员表、
  工作记录按本人、文书按项目）。
* 最多两轮：第一轮按消息内容挑探针；只有「像在查资料/问情况」且第一轮命中不足
  时才补第二轮，避免每条「记一条巡视」都多打一次检索。
* 单条材料都有长度上限，最终拼成一段可注入 system 的文本，不改变下游路由逻辑。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 触发「去资料库找」的词：问规范/问文件/回顾上次
_LOOKUP_HINTS = (
    "规范", "标准", "条文", "图集", "方案", "图纸", "合同", "资质", "资料",
    "文件", "文档", "表格", "表式", "上次", "之前", "那分", "那份", "那个文件",
    "怎么写", "怎么填", "要求", "依据", "查一下", "找一下", "有没有",
)
# 触发「看待办」的词
_TASK_HINTS = ("待办", "任务", "催", "谁处理", "进度", "协作", "指派", "待处理", "没做")
# 触发「看近期记录/文书」的词
_RECORD_HINTS = (
    "记录", "文书", "日志", "日报", "周报", "月报", "最近", "今天", "昨天",
    "这周", "本月", "上次", "之前",
)

_CHAIN_LABELS = {"think": "思维链", "tool": "工具调用", "result": "调用结果"}
_MAX_BLOCK_CHARS = 6000
# 检索时先去掉的口语/疑问词：整句丢进检索基本命不中文件名（实测「上次那份验收
# 规范怎么要求的」一条都搜不到，去掉虚词后「验收规范」才有意义）。
_STOPWORDS = (
    "上次", "之前", "那个", "那份", "这份", "这个", "怎么", "怎样", "如何",
    "要求", "规定", "是什么", "有没有", "一下", "帮我", "看看", "请问",
    "的", "了", "吗", "呢", "啊", "吧",
)


@dataclass
class Probe:
    key: str
    label: str
    query: str = ""


@dataclass
class Materials:
    block: str = ""
    buckets: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {str(b.get("key")): int(b.get("count") or 0) for b in self.buckets}


def _hit(message: str, hints: Sequence[str]) -> bool:
    text = message or ""
    return any(word in text for word in hints)


def _looks_like_lookup(message: str) -> bool:
    text = (message or "").strip()
    if _hit(text, _LOOKUP_HINTS):
        return True
    return text.endswith(("？", "?")) and len(text) >= 8


def _broad_query(message: str) -> str:
    """检索用的关键词：去掉标点与口语疑问词，保留有信息量的短语。"""
    cleaned = "".join(
        ch for ch in (message or "")
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )
    for word in _STOPWORDS:
        cleaned = cleaned.replace(word, "")
    return cleaned[:12] or (message or "")[-8:]


def plan_probes(
    message: str,
    *,
    has_attachments: bool,
    has_pending: bool,
) -> list[Probe]:
    """按消息内容挑本轮要看的材料源（顺序即展示顺序）。"""
    probes: list[Probe] = [Probe("conversation", "最近几轮对话")]
    if has_attachments:
        probes.append(Probe("attachments", "本轮附件（图片/视频/音频/文档）"))
    if _looks_like_lookup(message):
        probes.append(Probe("kb", "资料库（项目 + 个人）", _broad_query(message) or message))
    if _hit(message, _TASK_HINTS):
        probes.append(Probe("todos", "待办事项"))
    probes.append(Probe("memories", "项目记忆 / 个人记忆"))
    if _hit(message, _RECORD_HINTS) or has_pending:
        probes.append(Probe("records", "近期工作记录与文书"))
    return probes


def needs_second_round(message: str, materials: Materials) -> bool:
    """第一轮资料库命中太少且这轮确实在查东西 → 换宽泛词再搜一次。"""
    if not _looks_like_lookup(message):
        return False
    if "kb" not in materials.counts:
        return False
    return materials.counts.get("kb", 0) < 2


async def _emit(
    report: Optional[Callable[..., Awaitable[None]]],
    kind: str,
    step: str,
    title: str,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    if report is None:
        return
    payload = {
        "chain_kind": kind,
        "chain_step": step,
        "chain_label": f"{_CHAIN_LABELS.get(kind, kind)} {step}",
    }
    payload.update(detail or {})
    await report("materials", "completed", title, payload)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


async def _collect_conversation(
    earlier: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[str] = []
    for item in list(earlier)[-4:]:
        role = "用户" if str(item.get("role")) == "user" else "助手"
        content = _clip(item.get("content"), 160)
        if content:
            rows.append(f"{role}：{content}")
    return {"key": "conversation", "label": "最近对话", "count": len(rows), "items": rows}


async def _collect_attachments(
    images: Sequence[str], material: str
) -> dict[str, Any]:
    rows: list[str] = []
    if images:
        rows.append(f"图片 {len(images)} 张（已由视觉模型识别）")
    if material.strip():
        rows.append(f"素材文本：{_clip(material, 400)}")
    return {"key": "attachments", "label": "本轮附件", "count": len(rows), "items": rows}


async def _collect_kb(
    db: AsyncSession,
    user_id: int,
    project_id: Optional[int],
    query: str,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    if not query.strip():
        return {"key": "kb", "label": "资料库", "count": 0, "items": []}
    from app.services import kb_search

    try:
        result = await kb_search.search(db, int(user_id), query, limit=limit, project_id=project_id)
    except Exception:  # noqa: BLE001 —— 检索失败不能挡住聊天
        return {"key": "kb", "label": "资料库", "count": 0, "items": []}
    docs = result.get("documents") or []
    rows: list[str] = []
    for folder in (result.get("folders") or [])[:3]:
        path = str(folder.get("path") or folder.get("name") or "").strip()
        if not path:
            continue
        count = folder.get("count")
        rows.append(f"文件夹：{path}" + (f"（{count} 份）" if count else ""))
    for doc in docs[:limit]:
        name = str(doc.get("name") or "").strip()
        if not name:
            continue
        kb_name = str(doc.get("kb_name") or "").strip()
        folder = str(doc.get("folder_path") or "").strip()
        where = " / ".join(part for part in (kb_name, folder) if part)
        rows.append(f"《{name}》" + (f" · {where}" if where else ""))
    return {
        "key": "kb",
        "label": "资料库",
        "count": len(rows),
        "items": rows,
        "doc_ids": [int(doc.get("id") or 0) for doc in docs[:limit]],
    }


async def _collect_todos(
    db: AsyncSession, user_id: int, project_id: Optional[int]
) -> dict[str, Any]:
    from app.models.collaboration import CollaborationItem

    stmt = select(CollaborationItem).where(
        CollaborationItem.recipient_id == int(user_id),
        CollaborationItem.status.in_(["pending", "processing"]),
    )
    if project_id:
        stmt = stmt.where(CollaborationItem.project_id == project_id)
    rows_db = (
        await db.execute(stmt.order_by(CollaborationItem.created_at.desc()).limit(5))
    ).scalars().all()
    rows = [f"[{item.status}] {_clip(item.title, 60)}" for item in rows_db]
    return {"key": "todos", "label": "待办", "count": len(rows), "items": rows}


def _collect_memories(project_memories, personal_memories) -> dict[str, Any]:
    rows: list[str] = []
    for item in list(project_memories or [])[:4]:
        text = getattr(item, "content", "") or ""
        if text:
            rows.append(f"项目：{_clip(text, 80)}")
    for item in list(personal_memories or [])[:4]:
        text = getattr(item, "rule", "") or ""
        if text:
            rows.append(f"个人：{_clip(text, 80)}")
    return {"key": "memories", "label": "记忆", "count": len(rows), "items": rows}


async def _collect_records(
    db: AsyncSession, user_id: int, project_id: Optional[int]
) -> dict[str, Any]:
    from app.models.document import Document
    from app.models.work_record import WorkRecord

    rows: list[str] = []
    if project_id:
        records = (
            await db.execute(
                select(WorkRecord)
                .where(
                    WorkRecord.project_id == project_id,
                    WorkRecord.creator_id == int(user_id),
                )
                .order_by(WorkRecord.created_at.desc())
                .limit(5)
            )
        ).scalars().all()
        for item in records:
            day = item.created_at.strftime("%m-%d") if item.created_at else ""
            where = " ".join(x for x in (item.section, item.location) if x)
            rows.append(f"工作记录：{item.record_type} {day} {_clip(where, 30)}".strip())

        documents = (
            await db.execute(
                select(Document)
                .where(Document.project_id == project_id)
                .order_by(Document.created_at.desc())
                .limit(5)
            )
        ).scalars().all()
        for item in documents:
            day = item.created_at.strftime("%m-%d") if item.created_at else ""
            rows.append(f"文书：{_clip(item.title, 50)} {day}".strip())
    return {"key": "records", "label": "近期记录与文书", "count": len(rows), "items": rows}


def _render_block(message: str, materials: Materials) -> str:
    if not materials.buckets:
        return ""
    lines = [
        "【本轮已收集的材料 · 系统自动整理，按来源分类】",
        f"用户这轮说的是：{_clip(message, 200)}",
    ]
    index = 0
    for bucket in materials.buckets:
        items = bucket.get("items") or []
        if not items:
            continue
        index += 1
        lines.append(f"{index}. {bucket.get('label')}（{len(items)} 项）")
        for item in items[:6]:
            lines.append(f"   - {item}")
    if index == 0:
        lines.append("（本轮没有检索到可用材料：对话为空、资料库无命中、待办与记忆也为空）")
    lines.append(
        "【收集结论】以上是服务端在回答前按权限收集到的材料。"
        "材料够就直接据此回答；不够就在回答里明确说缺哪一份（不要编造文件内容）。"
    )
    block = "\n".join(lines)
    return block[:_MAX_BLOCK_CHARS]


async def collect(
    db: AsyncSession,
    user,
    project_id: Optional[int],
    conversation_id: Optional[str],
    message: str,
    *,
    images: Sequence[str] = (),
    material: str = "",
    earlier_messages: Sequence[dict[str, Any]] = (),
    project_memories: Sequence[Any] = (),
    personal_memories: Sequence[Any] = (),
    has_pending: bool = False,
    report: Optional[Callable[..., Awaitable[None]]] = None,
) -> Materials:
    """两轮收集：计划 → 并行取材料 → 判断是否补搜 → 返回可注入的材料块。"""
    probes = plan_probes(
        message,
        has_attachments=bool(images) or bool((material or "").strip()),
        has_pending=has_pending,
    )
    materials = Materials()
    await _emit(
        report, "think", "1.1",
        "先盘点这轮能拿到哪些材料：对话、附件、资料库、待办、记忆、近期记录",
        {"probes": [p.label for p in probes]},
    )
    await _emit(report, "tool", "1.1", "并行查看：" + "、".join(p.label for p in probes))

    buckets: list[dict[str, Any]] = []
    for probe in probes:
        if probe.key == "conversation":
            buckets.append(await _collect_conversation(earlier_messages))
        elif probe.key == "attachments":
            buckets.append(await _collect_attachments(images, material))
        elif probe.key == "kb":
            buckets.append(await _collect_kb(db, user.id, project_id, probe.query))
        elif probe.key == "todos":
            buckets.append(await _collect_todos(db, user.id, project_id))
        elif probe.key == "memories":
            buckets.append(_collect_memories(project_memories, personal_memories))
        elif probe.key == "records":
            buckets.append(await _collect_records(db, user.id, project_id))
    materials.rounds = 1
    await _emit(
        report, "result", "1.1", "第一轮材料已分类",
        {b["key"]: b["count"] for b in buckets},
    )

    if needs_second_round(message, Materials(buckets=buckets)):
        broad = _broad_query(message)
        # 第二轮要比第一轮更宽：取前 4 个字（「验收规范」→「验收」），
        # 否则两轮查的是同一个词，等于白跑。
        narrow = broad[:4] if len(broad) > 4 else broad
        await _emit(
            report, "think", "1.2",
            "资料库命中偏少，换个更宽的说法再搜一次",
            {"query": narrow},
        )
        await _emit(report, "tool", "1.2", f"检索资料库：{narrow}")
        extra = await _collect_kb(db, user.id, project_id, narrow, limit=8)
        first = next((b for b in buckets if b["key"] == "kb"), None)
        if first is None:
            buckets.append(extra)
        else:
            seen = set(first.get("items") or [])
            merged = list(first.get("items") or [])
            for item in extra.get("items") or []:
                if item not in seen and len(merged) < 8:
                    merged.append(item)
            first["items"] = merged
            first["count"] = len(merged)
        materials.rounds = 2
        await _emit(
            report, "result", "1.2", "补充检索完成",
            {"kb": next((b["count"] for b in buckets if b["key"] == "kb"), 0)},
        )

    materials.buckets = buckets
    materials.block = _render_block(message, materials)
    total = sum(int(b.get("count") or 0) for b in buckets)
    await _emit(
        report, "think", "1.3",
        (f"材料收集完成，共 {total} 项，开始理解并分类" if total
         else "没有额外材料可用，按现有对话继续；缺什么会在回答里说明"),
        {"rounds": materials.rounds, "total": total},
    )
    return materials
