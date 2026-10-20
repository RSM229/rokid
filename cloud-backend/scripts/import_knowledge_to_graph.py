"""批量导入知识库文件到知识图谱（并发版）

从知识库目录读取 docx/xlsx/pdf 文件，提取文本，
用 LLM 自动抽取实体和关系，导入知识图谱数据库。

用法：
    python scripts/import_knowledge_to_graph.py
"""
import asyncio
import sys
import json
import re
import logging
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MAX_CONCURRENT = 1


def extract_text_from_docx(file_path: Path) -> str:
    """从 docx 文件提取文本"""
    try:
        from docx import Document
        doc = Document(str(file_path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        paragraphs.append(cell.text.strip())
        return "\n".join(paragraphs)
    except Exception as e:
        logger.error(f"读取 docx 失败 {file_path.name}: {e}")
        return ""


def extract_text_from_xlsx(file_path: Path) -> str:
    """从 xlsx 文件提取文本"""
    try:
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
    except Exception as e:
        logger.error(f"读取 xlsx 失败 {file_path.name}: {e}")
        return ""


def extract_text_from_pdf(file_path: Path) -> str:
    """从 pdf 文件提取文本"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(file_path))
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                texts.append(text.strip())
        return "\n".join(texts)
    except Exception as e:
        logger.error(f"读取 pdf 失败 {file_path.name}: {e}")
        return ""


def extract_text(file_path: Path) -> str:
    """根据文件类型提取文本"""
    ext = file_path.suffix.lower()
    if ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".xlsx":
        return extract_text_from_xlsx(file_path)
    elif ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".doc":
        logger.warning(f"不支持 .doc 格式，请转换为 .docx: {file_path.name}")
        return ""
    elif ext == ".xls":
        logger.warning(f"不支持 .xls 格式，请转换为 .xlsx: {file_path.name}")
        return ""
    return ""


SYSTEM_PROMPT = (
    "你是建筑工程知识图谱专家。请从给定的文本中抽取关键实体和它们之间的关系。\n"
    "实体类型限：规范条款/验收流程/施工工序/材料标准/隐患类型/工程部位/监理要点\n"
    "关系类型限：属于/前置/后续/关联/应用于/依据\n"
    "输出严格的 JSON 格式：\n"
    '{"entities": [{"name": "...", "type": "...", "description": "..."}], '
    '"relations": [{"source": "...", "target": "...", "type": "...", "description": "..."}]}\n'
    "只输出 JSON，不要其他内容。"
)


async def process_file(file_path: Path, kb_path: Path, semaphore: asyncio.Semaphore) -> tuple[int, int, str | None]:
    """处理单个文件：提取文本、LLM 抽取、入库"""
    from app.db import async_session
    from app.models.knowledge_graph import KGEntity, KGRelation
    from app.services.ai_client import ai_client
    from sqlalchemy import select

    rel_path = str(file_path.relative_to(kb_path))

    async with semaphore:
        async with async_session() as db:
            # 检查是否已导入
            existing = await db.execute(
                select(KGEntity).where(KGEntity.source_doc == rel_path).limit(1)
            )
            if existing.scalar_one_or_none():
                logger.info(f"跳过（已导入）: {rel_path}")
                return 0, 0, None

            # 提取文本
            text = extract_text(file_path)
            if not text or len(text.strip()) < 10:
                logger.warning(f"文本内容为空，跳过: {rel_path}")
                return 0, 0, None

            text_chunk = text[:4000]

            # LLM 抽取
            try:
                result_text = await ai_client.chat_completion(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text_chunk},
                    ],
                    temperature=0.2,
                    max_tokens=2048,
                )
            except Exception as e:
                logger.error(f"LLM 抽取失败 {rel_path}: {e}")
                return 0, 0, f"{rel_path}: LLM 失败"

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
                    logger.warning(f"无法解析 LLM 输出: {rel_path}")
                    return 0, 0, f"{rel_path}: JSON 解析失败"
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.warning(f"无法解析 LLM 输出: {rel_path}")
                    return 0, 0, f"{rel_path}: JSON 解析失败"

            # 入库
            entity_count = 0
            relation_count = 0

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
                    entity_count += 1

            await db.flush()

            for rel in data.get("relations", []):
                if not (rel.get("source") and rel.get("target")):
                    continue
                src_q = await db.execute(select(KGEntity).where(KGEntity.name == rel["source"]))
                src = src_q.scalar_one_or_none()
                tgt_q = await db.execute(select(KGEntity).where(KGEntity.name == rel["target"]))
                tgt = tgt_q.scalar_one_or_none()
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
                        source_id=src.id,
                        target_id=tgt.id,
                        relation_type=rel.get("type", "关联"),
                        description=rel.get("description"),
                    ))
                    relation_count += 1

            await db.commit()
            logger.info(f"完成 {rel_path}: 实体 {entity_count} 个，关系 {relation_count} 条")
            return entity_count, relation_count, None


async def import_to_graph():
    """批量导入知识库到知识图谱"""
    kb_path = Path("/app/知识库")
    if not kb_path.exists():
        logger.error(f"知识库目录不存在: {kb_path}")
        return

    files = [f for f in kb_path.rglob("*") if f.is_file() and f.suffix.lower() in (".docx", ".xlsx", ".pdf")]
    logger.info(f"找到 {len(files)} 个知识库文件，并发数 {MAX_CONCURRENT}")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results = await asyncio.gather(*[process_file(f, kb_path, semaphore) for f in files])

    total_entities = sum(r[0] for r in results)
    total_relations = sum(r[1] for r in results)
    errors = [r[2] for r in results if r[2]]

    logger.info("=" * 50)
    logger.info(f"导入完成！文件数 {len(files)}，实体 {total_entities} 个，关系 {total_relations} 条")
    if errors:
        logger.warning(f"失败 {len(errors)} 项：")
        for err in errors[:10]:
            logger.warning(f"  - {err}")


if __name__ == "__main__":
    asyncio.run(import_to_graph())
