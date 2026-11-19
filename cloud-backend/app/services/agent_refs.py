"""解析用户消息里的 ``@[引用]`` 标记，把被引用资料的正文读出来喂给模型。

小程序 @ 面板点选后只往输入框插入一段纯文本，例如：

    @[记录#12 巡视3号楼]   @[隐患#5 楼面积水]
    @[规范 混凝土结构工程施工质量验收规范]   @[资料库 项目文件夹/合同与资质]

后端此前完全没有解析这些标记：模型只看到这段字面量，拿不到任何正文，
于是只能回答「无法读取文件」。这里在请求进入模型前统一解析：

- ``记录#<id>``  → 该条工作记录的结构化字段全文
- ``隐患#<id>``  → 该条隐患的风险、证据、状态
- ``文件#<id>``  → 资料库里指定文档的正文（个人库或有权访问的共享库）
- ``规范 <名称>`` → 规范库按名称检索出的文档正文片段
- ``资料库 <路径>`` → 个人资料库该文件夹（含子文件夹）下的文档正文片段

读到的内容作为 system 上下文注入；读不到的条目也会写进上下文，明确告诉
模型"这条引用没读到"，避免它继续编造。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hazard import Hazard
from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument, KbFolder
from app.models.work_record import WorkRecord

logger = logging.getLogger(__name__)

REF_RE = re.compile(r"@\[([^\]\n]{1,200})\]")
_RECORD_RE = re.compile(r"^记录#\s*(\d+)\s*(.*)$")
_HAZARD_RE = re.compile(r"^隐患#\s*(\d+)\s*(.*)$")
_FILE_RE = re.compile(r"^文件#\s*(\d+)\s*(.*)$")
_REGULATION_RE = re.compile(r"^规范\s+(.+)$")
_LIBRARY_RE = re.compile(r"^资料库\s+(.+)$")

MAX_DOCS = 6
MAX_CHARS_PER_DOC = 1800
MAX_TOTAL_CHARS = 9000


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…（后略）"


def _fmt_record(row: WorkRecord) -> str:
    lines = [f"工作记录 #{row.id}（{row.record_type}，{row.status}）"]
    if row.record_date:
        lines.append(f"日期：{row.record_date}")
    if row.section:
        lines.append(f"施工区域：{row.section}")
    if row.location:
        lines.append(f"具体位置：{row.location}")
    if row.weather:
        lines.append(f"天气：{row.weather}")
    for label, value in (
        ("施工内容", row.work_content),
        ("发现问题", row.problems),
        ("处理意见", row.treatment),
        ("巡视备注", row.inspection_remark),
        ("旁站情况", row.station_content),
        ("旁站结论", row.station_conclusion),
        ("验收结果", row.acceptance_result),
        ("检验依据", row.acceptance_basis),
        ("材料名称", row.witness_material_name),
        ("规格型号", row.witness_material_spec),
    ):
        if value:
            lines.append(f"{label}：{value}")
    return "\n".join(lines)


def _fmt_hazard(row: Hazard) -> str:
    lines = [f"隐患 #{row.id}（状态：{row.status}）"]
    if row.risk_category:
        lines.append(f"风险类别：{row.risk_category}")
    if row.top1_risk:
        lines.append(f"主要风险：{row.top1_risk}")
    if row.section:
        lines.append(f"施工区域：{row.section}")
    if row.location:
        lines.append(f"具体位置：{row.location}")
    if row.evidence:
        try:
            lines.append("证据：" + "；".join(str(x) for x in row.evidence)[:600])
        except TypeError:
            pass
    if row.recheck_suggestion:
        try:
            lines.append("复查建议：" + "；".join(str(x) for x in row.recheck_suggestion)[:400])
        except TypeError:
            pass
    return "\n".join(lines)


async def _personal_kb(db: AsyncSession, user: Any) -> KnowledgeBase | None:
    rows = (await db.execute(
        select(KnowledgeBase)
        .where(
            KnowledgeBase.owner_id == user.id,
            KnowledgeBase.kb_type == "personal",
        )
        .order_by(KnowledgeBase.id)
    )).scalars().all()
    return rows[0] if rows else None


def _folder_path_map(folders: list[KbFolder]) -> dict[int, str]:
    by_id = {f.id: f for f in folders}
    cache: dict[int, str] = {}

    def path_of(fid: int) -> str:
        if fid in cache:
            return cache[fid]
        node = by_id.get(fid)
        if node is None:
            return ""
        parent = path_of(node.parent_id) if node.parent_id else ""
        value = f"{parent}/{node.name}" if parent else node.name
        cache[fid] = value
        return value

    for f in folders:
        path_of(f.id)
    return cache


def _descendant_ids(folders: list[KbFolder], root_id: int) -> set[int]:
    children: dict[int | None, list[KbFolder]] = {}
    for f in folders:
        children.setdefault(f.parent_id, []).append(f)
    out: set[int] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        stack.extend(c.id for c in children.get(current, []))
    return out


def _extract_text(path: Path) -> str:
    # 延迟导入：避免 routers ↔ services 的循环导入
    from app.routers.knowledge_base import _extract_text as extract

    try:
        return extract(path)
    except Exception:  # noqa: BLE001
        logger.warning("@引用文档正文提取失败：%s", path, exc_info=True)
        return ""


async def _resolve_library(
    db: AsyncSession, user: Any, path_text: str, budget: list[int],
) -> str:
    kb = await _personal_kb(db, user)
    if kb is None:
        return f"【资料库 {path_text}】当前账号还没有资料库，无法读取。"

    folders = (await db.execute(
        select(KbFolder).where(KbFolder.kb_id == kb.id)
    )).scalars().all()
    path_map = _folder_path_map(list(folders))
    needle = path_text.strip().strip("/")

    matched: KbFolder | None = None
    for fid, path in path_map.items():
        if path == needle or path.endswith("/" + needle):
            matched = next((f for f in folders if f.id == fid), None)
            break
    if matched is None:
        for f in folders:
            if f.name == needle:
                matched = f
                break

    docs: list[KnowledgeDocument] = []
    title = path_text
    if matched is not None:
        title = path_map.get(matched.id, matched.name)
        folder_ids = _descendant_ids(list(folders), matched.id)
        docs = (await db.execute(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.kb_id == kb.id,
                KnowledgeDocument.folder_id.in_(folder_ids),
                KnowledgeDocument.is_deleted == False,  # noqa: E712
            )
            .order_by(KnowledgeDocument.id.desc())
            .limit(MAX_DOCS)
        )).scalars().all()
    else:
        # 文件夹没命中：按文档名兜底匹配（用户可能引用的是文件名而非文件夹）
        docs = (await db.execute(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.kb_id == kb.id,
                KnowledgeDocument.name.like(f"%{needle}%"),
                KnowledgeDocument.is_deleted == False,  # noqa: E712
            )
            .order_by(KnowledgeDocument.id.desc())
            .limit(MAX_DOCS)
        )).scalars().all()

    if not docs:
        return f"【资料库 {title}】没有找到可读取的文档（文件夹为空或名称不匹配）。"

    parts = [f"【资料库 {title}】共读取 {len(docs)} 份文档："]
    for doc in docs:
        if budget[0] <= 0:
            parts.append("（已达到本次上下文上限，其余文档未读取）")
            break
        text = _extract_text(Path(doc.path))
        if not text.strip():
            suffix = (doc.filename.rsplit(".", 1)[-1].lower() if "." in (doc.filename or "") else "")
            if suffix not in {"pdf", "docx", "xlsx", "txt", "md"}:
                reason = f"该格式（{suffix or '未知'}）暂不支持文本提取"
            else:
                reason = "未提取到文字，可能是扫描件/图片型文件"
            parts.append(f"--- {doc.name} ---\n（{reason}：{doc.filename}）")
            continue
        piece = _clip(text, min(MAX_CHARS_PER_DOC, budget[0]))
        budget[0] -= len(piece)
        parts.append(f"--- {doc.name}（{doc.filename}）---\n{piece}")
    return "\n".join(parts)


async def _resolve_file(
    db: AsyncSession, user: Any, doc_id: int, budget: list[int],
) -> str:
    """按文档 id 读取资料库里的单个文件（个人库或当前用户可访问的共享库）。"""
    doc = await db.get(KnowledgeDocument, doc_id)
    if doc is None or doc.is_deleted:
        return f"【文件#{doc_id}】不存在或已删除，未能读取。"

    kb = await db.get(KnowledgeBase, doc.kb_id)
    if kb is None:
        return f"【文件#{doc_id}】所属资料库不存在，未能读取。"
    owner_id = getattr(kb, "owner_id", None)
    if owner_id != user.id:
        # 共享库：复用知识库路由的权限判定，非成员会抛 403
        try:
            from app.routers.knowledge_base import _require_role

            await _require_role(db, kb, user, "viewer")
        except Exception:  # noqa: BLE001
            return f"【文件#{doc_id} {doc.name}】无权访问该资料库，未能读取。"

    text = _extract_text(Path(doc.path))
    if not text.strip():
        suffix = (doc.filename.rsplit(".", 1)[-1].lower() if "." in (doc.filename or "") else "")
        reason = (f"该格式（{suffix or '未知'}）暂不支持文本提取"
                  if suffix not in {"pdf", "docx", "xlsx", "txt", "md"}
                  else "未提取到文字，可能是扫描件/图片型文件")
        return f"【文件 {doc.name}（{doc.filename}）】{reason}，未能读取正文。"

    piece = _clip(text, min(MAX_CHARS_PER_DOC * 2, budget[0]))
    budget[0] -= len(piece)
    return f"【文件 {doc.name}（{doc.filename}）】\n{piece}"


async def _resolve_regulation(name: str, budget: list[int]) -> str:
    from app.services import knowledge_service

    name = name.strip()
    hits: list[dict] = []
    # 按名称子串检索：先全名，再逐步缩短（用户 @ 的常是完整长名，库里可能是简称）
    candidates: list[str] = []
    for cand in (name, re.sub(r"(规范|标准|规程|条例)$", "", name), name[:10], name[:8], name[:6]):
        cand = (cand or "").strip()
        if len(cand) >= 4 and cand not in candidates:
            candidates.append(cand)
    for cand in candidates:
        hits = knowledge_service.search_by_keyword(cand)[:2]
        if hits:
            break

    weak = False
    if not hits:
        # 名称没命中才退到语义检索，并用标注说明"不是同名文档"
        hits = knowledge_service.search_by_content(name)[:2]
        weak = bool(hits)

    if not hits:
        return f"【规范 {name}】规范库中没有检索到同名文档，未能读取。"

    parts = [f"【规范 {name}】" + ("未找到同名文档，以下为语义检索到的相近资料，仅供参考：" if weak else f"检索到 {len(hits)} 份：")]
    for hit in hits:
        if budget[0] <= 0:
            break
        path = Path(hit.get("path") or "")
        text = _extract_text(path) if path.is_file() else ""
        if not text:
            snippet = hit.get("snippet") or ""
            text = snippet
        if not text.strip():
            parts.append(f"--- {hit.get('name')} ---\n（无法提取正文）")
            continue
        piece = _clip(text, min(MAX_CHARS_PER_DOC, budget[0]))
        budget[0] -= len(piece)
        parts.append(f"--- {hit.get('name')} ---\n{piece}")
    return "\n".join(parts)


async def resolve_message_refs(
    db: AsyncSession, user: Any, project_id: int | None, message: str,
) -> tuple[str, str]:
    """解析消息里的 ``@[引用]``。

    返回 ``(改写后的消息, 引用正文上下文)``。没有引用标记时原样返回。
    """
    message = message or ""
    refs = REF_RE.findall(message)
    if not refs:
        return message, ""

    budget = [MAX_TOTAL_CHARS]
    blocks: list[str] = []

    for raw in refs:
        label = raw.strip()
        try:
            m = _RECORD_RE.match(label)
            if m:
                rid = int(m.group(1))
                row = await db.get(WorkRecord, rid)
                if row is None or (project_id and row.project_id != project_id):
                    blocks.append(f"【引用记录#{rid}】不存在或不属于当前项目，未能读取。")
                else:
                    blocks.append(_fmt_record(row))
                continue

            m = _HAZARD_RE.match(label)
            if m:
                hid = int(m.group(1))
                row = await db.get(Hazard, hid)
                if row is None or (project_id and row.project_id != project_id):
                    blocks.append(f"【引用隐患#{hid}】不存在或不属于当前项目，未能读取。")
                else:
                    blocks.append(_fmt_hazard(row))
                continue

            m = _FILE_RE.match(label)
            if m:
                blocks.append(await _resolve_file(db, user, int(m.group(1)), budget))
                continue

            m = _REGULATION_RE.match(label)
            if m:
                blocks.append(await _resolve_regulation(m.group(1), budget))
                continue

            m = _LIBRARY_RE.match(label)
            if m:
                blocks.append(await _resolve_library(db, user, m.group(1), budget))
                continue

            blocks.append(f"【引用 {label}】无法识别引用类型，未能读取。")
        except Exception:  # noqa: BLE001
            logger.warning("@引用解析失败：%s", label, exc_info=True)
            blocks.append(f"【引用 {label}】读取失败。")

    # 原文标注保留：用户回看时能对上号，模型也能把引用与正文对应起来。
    cleaned = message
    context = (
        "以下是用户通过 @ 引用的资料，系统已读取其正文。"
        "回答时直接依据这些内容，不要声称无法读取文件；"
        "若某个引用标注「未能读取」，再如实告知用户。\n\n"
        + "\n\n".join(blocks)
    )
    return cleaned, context
