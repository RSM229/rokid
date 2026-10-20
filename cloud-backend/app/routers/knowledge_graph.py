"""知识图谱路由 — 实体/关系管理、图遍历、AI 抽取"""
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models.user import User
from app.models.knowledge_graph import KGEntity, KGRelation
from app.routers.auth import get_current_user, require_permission
from app.services.ai_client import ai_client

router = APIRouter()


# ===== Pydantic 模型 =====

class EntityCreate(BaseModel):
    name: str
    entity_type: str
    description: Optional[str] = None
    properties: Optional[dict] = None
    source_doc: Optional[str] = None


class RelationCreate(BaseModel):
    source_name: str
    target_name: str
    relation_type: str
    weight: float = 1.0
    description: Optional[str] = None


class EntitySearchRequest(BaseModel):
    keyword: str
    entity_type: Optional[str] = None
    limit: int = 20


class ExtractRequest(BaseModel):
    text: str
    source_doc: Optional[str] = None


# ===== 实体管理 =====

@router.post("/entities")
async def create_entity(
    req: EntityCreate,
    user: User = Depends(require_permission("knowledge_base")),
    db: AsyncSession = Depends(get_db),
):
    """新建知识图谱实体"""
    existing = await db.execute(
        select(KGEntity).where(KGEntity.name == req.name, KGEntity.entity_type == req.entity_type)
    )
    entity = existing.scalar_one_or_none()
    if entity:
        # 合并更新
        entity.description = req.description or entity.description
        if req.properties:
            entity.properties = {**(entity.properties or {}), **req.properties}
        entity.source_doc = req.source_doc or entity.source_doc
    else:
        entity = KGEntity(
            name=req.name,
            entity_type=req.entity_type,
            description=req.description,
            properties=req.properties,
            source_doc=req.source_doc,
        )
        db.add(entity)
    await db.commit()
    await db.refresh(entity)
    return {"id": entity.id, "name": entity.name, "entity_type": entity.entity_type}


@router.get("/entities")
async def list_entities(
    entity_type: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询实体列表"""
    query = select(KGEntity)
    if entity_type:
        query = query.where(KGEntity.entity_type == entity_type)
    if keyword:
        query = query.where(KGEntity.name.like(f"%{keyword}%"))
    query = query.order_by(KGEntity.created_at.desc()).limit(limit)
    result = await db.execute(query)
    entities = result.scalars().all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "entity_type": e.entity_type,
            "description": e.description,
            "source_doc": e.source_doc,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entities
    ]


@router.post("/entities/search")
async def search_entities(
    req: EntitySearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按关键字搜索实体（用于图谱探索）"""
    query = select(KGEntity).where(
        or_(
            KGEntity.name.like(f"%{req.keyword}%"),
            KGEntity.description.like(f"%{req.keyword}%"),
        )
    )
    if req.entity_type:
        query = query.where(KGEntity.entity_type == req.entity_type)
    query = query.limit(req.limit)
    result = await db.execute(query)
    entities = result.scalars().all()
    return [
        {"id": e.id, "name": e.name, "entity_type": e.entity_type, "description": e.description}
        for e in entities
    ]


@router.delete("/entities/{entity_id}")
async def delete_entity(
    entity_id: int,
    user: User = Depends(require_permission("knowledge_base")),
    db: AsyncSession = Depends(get_db),
):
    """删除实体（级联删除相关关系）"""
    result = await db.execute(select(KGEntity).where(KGEntity.id == entity_id))
    entity = result.scalar_one_or_none()
    if not entity:
        raise HTTPException(404, "实体不存在")
    # 删除相关关系
    rels = await db.execute(
        select(KGRelation).where(or_(KGRelation.source_id == entity_id, KGRelation.target_id == entity_id))
    )
    for r in rels.scalars().all():
        await db.delete(r)
    await db.delete(entity)
    await db.commit()
    return {"detail": "已删除"}


# ===== 关系管理 =====

@router.post("/relations")
async def create_relation(
    req: RelationCreate,
    user: User = Depends(require_permission("knowledge_base")),
    db: AsyncSession = Depends(get_db),
):
    """新建实体关系（自动创建不存在的端点实体）"""
    # 获取或创建 source
    src_q = await db.execute(select(KGEntity).where(KGEntity.name == req.source_name))
    source = src_q.scalar_one_or_none()
    if not source:
        source = KGEntity(name=req.source_name, entity_type="未分类")
        db.add(source)
        await db.flush()
    # 获取或创建 target
    tgt_q = await db.execute(select(KGEntity).where(KGEntity.name == req.target_name))
    target = tgt_q.scalar_one_or_none()
    if not target:
        target = KGEntity(name=req.target_name, entity_type="未分类")
        db.add(target)
        await db.flush()

    # 检查是否已存在
    rel_q = await db.execute(
        select(KGRelation).where(
            KGRelation.source_id == source.id,
            KGRelation.target_id == target.id,
            KGRelation.relation_type == req.relation_type,
        )
    )
    rel = rel_q.scalar_one_or_none()
    if rel:
        rel.weight = req.weight
        rel.description = req.description or rel.description
    else:
        rel = KGRelation(
            source_id=source.id, target_id=target.id,
            relation_type=req.relation_type, weight=req.weight,
            description=req.description,
        )
        db.add(rel)
    await db.commit()
    return {"detail": "关系已建立", "source": source.name, "target": target.name, "relation": req.relation_type}


# ===== 图遍历 =====

@router.get("/graph/subgraph")
async def get_subgraph(
    entity_id: int,
    depth: int = 2,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取以某实体为中心指定深度的子图（用于图谱可视化）"""
    visited = set()
    nodes = []
    edges = []

    async def traverse(eid: int, d: int):
        if eid in visited or d < 0:
            return
        visited.add(eid)
        ent_q = await db.execute(select(KGEntity).where(KGEntity.id == eid))
        ent = ent_q.scalar_one_or_none()
        if not ent:
            return
        nodes.append({"id": ent.id, "name": ent.name, "type": ent.entity_type, "description": ent.description})

        # 出边
        out_q = await db.execute(
            select(KGRelation).options(selectinload(KGRelation.target)).where(KGRelation.source_id == eid)
        )
        for r in out_q.scalars().all():
            edges.append({
                "source": r.source_id, "target": r.target_id,
                "relation": r.relation_type, "weight": r.weight,
            })
            await traverse(r.target_id, d - 1)
        # 入边
        in_q = await db.execute(
            select(KGRelation).options(selectinload(KGRelation.source)).where(KGRelation.target_id == eid)
        )
        for r in in_q.scalars().all():
            edges.append({
                "source": r.source_id, "target": r.target_id,
                "relation": r.relation_type, "weight": r.weight,
            })
            await traverse(r.source_id, d - 1)

    await traverse(entity_id, depth)
    return {"nodes": nodes, "edges": edges}


# ===== AI 抽取 =====

@router.post("/extract")
async def extract_entities_relations(
    req: ExtractRequest,
    user: User = Depends(require_permission("knowledge_base")),
    db: AsyncSession = Depends(get_db),
):
    """通过 LLM 从文本中抽取实体与关系，存入知识图谱"""
    system_prompt = (
        "你是建筑工程知识图谱专家。请从给定的文本中抽取关键实体和它们之间的关系。\n"
        "实体类型限：规范条款/验收流程/施工工序/材料标准/隐患类型/工程部位/监理要点\n"
        "关系类型限：属于/前置/后续/关联/应用于/依据\n"
        "输出严格的 JSON 格式：\n"
        '{"entities": [{"name": "...", "type": "...", "description": "..."}], '
        '"relations": [{"source": "...", "target": "...", "type": "...", "description": "..."}]}\n'
        "只输出 JSON，不要其他内容。"
    )
    try:
        result_text = await ai_client.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": req.text},
            ],
            temperature=0.2, max_tokens=2048,
        )
    except Exception as e:
        raise HTTPException(502, f"AI 抽取失败: {e}")

    # 解析 JSON
    cleaned = result_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"raw": result_text, "extracted_entities": 0, "extracted_relations": 0}

    # 入库
    entity_count = 0
    relation_count = 0
    for ent in data.get("entities", []):
        if not ent.get("name"):
            continue
        existing = await db.execute(
            select(KGEntity).where(
                KGEntity.name == ent["name"], KGEntity.entity_type == ent.get("type", "未分类")
            ).limit(1)
        )
        if not existing.scalars().first():
            db.add(KGEntity(
                name=ent["name"], entity_type=ent.get("type", "未分类"),
                description=ent.get("description"),
                source_doc=req.source_doc,
            ))
            entity_count += 1

    await db.flush()

    for rel in data.get("relations", []):
        if not (rel.get("source") and rel.get("target")):
            continue
        src_q = await db.execute(select(KGEntity).where(KGEntity.name == rel["source"]).limit(1))
        src = src_q.scalars().first()
        tgt_q = await db.execute(select(KGEntity).where(KGEntity.name == rel["target"]).limit(1))
        tgt = tgt_q.scalars().first()
        if not src or not tgt:
            continue
        rel_exist = await db.execute(
            select(KGRelation).where(
                KGRelation.source_id == src.id, KGRelation.target_id == tgt.id,
                KGRelation.relation_type == rel.get("type", "关联"),
            ).limit(1)
        )
        if not rel_exist.scalars().first():
            db.add(KGRelation(
                source_id=src.id, target_id=tgt.id,
                relation_type=rel.get("type", "关联"),
                description=rel.get("description"),
            ))
            relation_count += 1

    await db.commit()
    return {
        "extracted_entities": entity_count,
        "extracted_relations": relation_count,
        "raw": result_text,
    }


@router.get("/stats")
async def kg_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """知识图谱统计信息"""
    ent_result = await db.execute(select(KGEntity))
    entities = ent_result.scalars().all()
    rel_result = await db.execute(select(KGRelation))
    relations = rel_result.scalars().all()

    type_count: dict[str, int] = {}
    for e in entities:
        type_count[e.entity_type] = type_count.get(e.entity_type, 0) + 1

    return {
        "total_entities": len(entities),
        "total_relations": len(relations),
        "entity_types": type_count,
    }


@router.post("/import-from-knowledge-base")
async def import_from_knowledge_base(
    user: User = Depends(require_permission("knowledge_base")),
    db: AsyncSession = Depends(get_db),
):
    """从知识库文件批量导入实体和关系到知识图谱

    扫描知识库目录中的 docx/xlsx/pdf 文件，
    提取文本后用 LLM 自动抽取实体和关系。
    """
    from pathlib import Path as PathLib
    from app.config import settings
    import json
    import re

    kb_path = settings.knowledge_base_path
    if not kb_path.exists():
        raise HTTPException(404, f"知识库目录不存在: {kb_path}")

    # 收集所有文件
    files = []
    for f in kb_path.rglob("*"):
        if f.is_file() and f.suffix.lower() in (".docx", ".xlsx", ".pdf"):
            files.append(f)

    total_entities = 0
    total_relations = 0
    skipped = 0
    errors = []
    import asyncio

    # 先一次性收集尚未导入的文件，避免对已入库文件重复调用 LLM
    pending = []
    for file_path in files:
        rel_path = str(file_path.relative_to(kb_path))

        existing = await db.execute(
            select(KGEntity).where(KGEntity.source_doc == rel_path).limit(1)
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        pending.append((rel_path, file_path))

    # 并发上限：LLM 调用并发提效，但不过度冲击上游
    sem = asyncio.Semaphore(5)
    system_prompt = (
        "你是建筑工程知识图谱专家。请从给定的文本中抽取关键实体和它们之间的关系。\n"
        "实体类型限：规范条款/验收流程/施工工序/材料标准/隐患类型/工程部位/监理要点\n"
        "关系类型限：属于/前置/后续/关联/应用于/依据\n"
        "输出严格的 JSON 格式：\n"
        '{"entities": [{"name": "...", "type": "...", "description": "..."}], '
        '"relations": [{"source": "...", "target": "...", "type": "...", "description": "..."}]}\n'
        "只输出 JSON，不要其他内容。"
    )

    async def _extract_and_parse(rel_path: str, file_path) -> dict | str | None:
        """并发抽取单个文件的文本并用 LLM 提取实体/关系；入库阶段保持串行。"""
        async with sem:
            try:
                text = _extract_text(file_path)
            except Exception as e:
                errors.append(f"{rel_path}: {str(e)}")
                return None

            if not text or len(text.strip()) < 10:
                return "SKIP"

            text_chunk = text[:4000]

            try:
                result_text = await ai_client.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text_chunk},
                    ],
                    temperature=0.2, max_tokens=2048,
                )
            except Exception as e:
                errors.append(f"{rel_path}: LLM 抽取失败 - {str(e)}")
                return None

            # 解析 JSON
            cleaned = result_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                match = re.search(r'\{.*\}', cleaned, re.DOTALL)
                if not match:
                    errors.append(f"{rel_path}: JSON 解析失败")
                    return None
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    errors.append(f"{rel_path}: JSON 解析失败")
                    return None
            return data

    if pending:
        results = await asyncio.gather(
            *(_extract_and_parse(rel_path, file_path) for rel_path, file_path in pending)
        )

        for (rel_path, _), data in zip(pending, results):
            if data is None:
                continue
            if data == "SKIP":
                skipped += 1
                continue

            # 入库
            for ent in data.get("entities", []):
                if not ent.get("name"):
                    continue
                existing_q = await db.execute(
                    select(KGEntity).where(
                        KGEntity.name == ent["name"],
                        KGEntity.entity_type == ent.get("type", "未分类"),
                    )
                )
                if not existing_q.scalar_one_or_none():
                    db.add(KGEntity(
                        name=ent["name"],
                        entity_type=ent.get("type", "未分类"),
                        description=ent.get("description"),
                        source_doc=rel_path,
                    ))
                    total_entities += 1

            await db.flush()

            for rel in data.get("relations", []):
                if not (rel.get("source") and rel.get("target")):
                    continue
                src_q = await db.execute(select(KGEntity).where(KGEntity.name == rel["source"]))
                src = src_q.scalars().first()
                tgt_q = await db.execute(select(KGEntity).where(KGEntity.name == rel["target"]))
                tgt = tgt_q.scalars().first()
                if not src or not tgt:
                    continue
                rel_exist = await db.execute(
                    select(KGRelation).where(
                        KGRelation.source_id == src.id,
                        KGRelation.target_id == tgt.id,
                        KGRelation.relation_type == rel.get("type", "关联"),
                    )
                )
                if not rel_exist.scalar_one_or_none():
                    db.add(KGRelation(
                        source_id=src.id, target_id=tgt.id,
                        relation_type=rel.get("type", "关联"),
                        description=rel.get("description"),
                    ))
                    total_relations += 1

            await db.commit()

    return {
        "total_files": len(files),
        "extracted_entities": total_entities,
        "extracted_relations": total_relations,
        "skipped": skipped,
        "errors": errors[:10],
    }


def _extract_text(file_path) -> str:
    """从文件中提取文本"""
    from pathlib import Path as PathLib
    ext = PathLib(file_path).suffix.lower()

    if ext == ".docx":
        from docx import Document
        doc = Document(str(file_path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        paragraphs.append(cell.text.strip())
        return "\n".join(paragraphs)

    elif ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(str(file_path), read_only=True, data_only=True)
        lines = []
        for sheet in wb:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    lines.append(" | ".join(cells))
        wb.close()
        return "\n".join(lines)

    elif ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(file_path))
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                texts.append(text.strip())
        return "\n".join(texts)

    return ""
