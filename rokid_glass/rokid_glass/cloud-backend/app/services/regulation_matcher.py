"""规范库匹配服务 - 根据隐患类别与描述匹配规范条款

作者: cloud-backend
创建日期: 2026-07-18

实现逻辑：
1. 先用 hazard_category 映射到 REGULATION_CATEGORIES 的对应分类
2. 在对应分类下用关键词匹配 RegulationChunk
3. 若匹配数不足，回退到全文搜索（所有分类）
4. 返回结构化匹配结果，含 relevance_score

DB 访问：使用同步 SQLAlchemy 引擎（pymysql），避免与 async 事件循环冲突。
"""
import logging
import re
from typing import List

from sqlalchemy import create_engine, select, or_
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.regulation_chunk import RegulationChunk

logger = logging.getLogger(__name__)


# 隐患类别 -> 规范库分类映射
# 隐患 risk_category 可能值：安全帽、临边防护、脚手架、临时用电、机械设备、消防安全等
CATEGORY_MAPPING: dict[str, list[str]] = {
    "安全帽": ["安全管理规范", "安全监理用表"],
    "安全": ["安全管理规范", "安全监理用表"],
    "临边": ["安全管理规范", "施工技术规范"],
    "洞口": ["安全管理规范", "施工技术规范"],
    "高处作业": ["安全管理规范"],
    "脚手架": ["施工技术规范", "安全管理规范"],
    "临时用电": ["安全管理规范", "施工技术规范"],
    "用电": ["安全管理规范", "施工技术规范"],
    "机械": ["施工技术规范", "安全管理规范"],
    "塔吊": ["施工技术规范", "安全管理规范"],
    "起重": ["施工技术规范", "安全管理规范"],
    "消防": ["消防/给排水/人防规范"],
    "给排水": ["消防/给排水/人防规范"],
    "人防": ["消防/给排水/人防规范"],
    "防水": ["施工技术规范"],
    "钢筋": ["质量验收规范", "施工技术规范"],
    "混凝土": ["质量验收规范", "施工技术规范"],
    "模板": ["施工技术规范", "质量验收规范"],
    "材料": ["材料规范"],
    "基坑": ["施工技术规范", "安全管理规范"],
    "深基坑": ["施工技术规范", "安全管理规范"],
    "电气": ["施工技术规范"],
    "质量": ["质量验收规范"],
}


# 同步引擎懒加载（将 mysql+aiomysql 转为 mysql+pymysql）
_sync_engine = None
_sync_session_factory = None


def _get_sync_session():
    """获取同步 DB 会话（懒加载）"""
    global _sync_engine, _sync_session_factory
    if _sync_engine is None:
        sync_url = settings.database_url.replace(
            "mysql+aiomysql://", "mysql+pymysql://"
        ).replace("mysql+asyncmy://", "mysql+pymysql://")
        _sync_engine = create_engine(sync_url, echo=False, pool_pre_ping=True)
        _sync_session_factory = sessionmaker(_sync_engine, expire_on_commit=False)
    return _sync_session_factory()


def _map_to_regulation_categories(hazard_category: str) -> list[str]:
    """根据隐患类别映射到规范库分类"""
    if not hazard_category:
        return []
    normalized = hazard_category.strip()
    # 精确匹配
    if normalized in CATEGORY_MAPPING:
        return CATEGORY_MAPPING[normalized]
    # 模糊包含匹配
    for key, cats in CATEGORY_MAPPING.items():
        if key in normalized or normalized in key:
            return cats
    # 默认返回安全类
    return ["安全管理规范", "安全监理用表"]


def _extract_keywords(text: str) -> list[str]:
    """从隐患描述中提取关键词"""
    if not text:
        return []
    # 简单分词：按常见分隔符切分，过滤过短词
    parts = re.split(r"[，,。.；;、\s]+", text)
    keywords = [p.strip() for p in parts if len(p.strip()) >= 2]
    return keywords


def _compute_relevance(content: str, keywords: list[str], category_match: bool) -> float:
    """计算相关度得分

    Args:
        content: 规范条款内容
        keywords: 隐患关键词列表
        category_match: 是否分类匹配

    Returns:
        0.0 ~ 1.0
    """
    if not content or not keywords:
        return 0.1 if category_match else 0.0
    hits = 0
    for kw in keywords:
        if kw in content:
            hits += 1
    keyword_score = hits / len(keywords) if keywords else 0
    category_bonus = 0.2 if category_match else 0.0
    return min(1.0, keyword_score + category_bonus)


def _snippet(content: str, keywords: list[str], context: int = 80) -> str:
    """提取关键词上下文片段"""
    if not content:
        return ""
    if not keywords:
        return content[:context * 2] + ("..." if len(content) > context * 2 else "")
    idx = -1
    match_len = len(keywords[0])
    for kw in keywords:
        pos = content.find(kw)
        if pos != -1:
            idx = pos
            match_len = len(kw)
            break
    if idx == -1:
        return content[:context * 2] + ("..." if len(content) > context * 2 else "")
    start = max(0, idx - context)
    end = min(len(content), idx + match_len + context)
    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet


def _match_from_regulation_chunks(
    hazard_category: str,
    hazard_description: str,
    top_k: int,
) -> List[dict]:
    """在 ``regulation_chunks`` 规范库里做关键词匹配（原实现，保留）。

    该表当前为空（规范条文尚未入库），所以实际检索会落到 RAG 索引兜底；
    待规范条文导入后本路径会自动重新生效。
    """
    keywords = _extract_keywords(hazard_description)
    # 隐患类别也作为关键词
    if hazard_category:
        keywords.insert(0, hazard_category)

    categories = _map_to_regulation_categories(hazard_category)

    results: list[dict] = []
    seen_ids: set[int] = set()

    try:
        session = _get_sync_session()
    except Exception as exc:
        logger.warning(f"规范库 DB 会话创建失败：{exc}")
        return []

    try:
        # 1. 分类匹配
        if categories:
            query = select(RegulationChunk).where(
                RegulationChunk.category.in_(categories)
            )
            chunks = session.execute(query).scalars().all()
            for chunk in chunks:
                content = chunk.content or ""
                doc_name = chunk.doc_name or ""
                if any(kw in content for kw in keywords) or any(kw in doc_name for kw in keywords):
                    if chunk.id in seen_ids:
                        continue
                    seen_ids.add(chunk.id)
                    score = _compute_relevance(content, keywords, True)
                    results.append({
                        "title": doc_name,
                        "content_snippet": _snippet(content, keywords),
                        "category": chunk.category,
                        "relevance_score": round(score, 3),
                        "chunk_id": chunk.id,
                    })

        # 2. 不足时回退全文搜索
        if len(results) < top_k and keywords:
            conditions = [RegulationChunk.content.like(f"%{kw}%") for kw in keywords]
            query = select(RegulationChunk).where(or_(*conditions)).limit(top_k * 2)
            chunks = session.execute(query).scalars().all()
            for chunk in chunks:
                if chunk.id in seen_ids:
                    continue
                seen_ids.add(chunk.id)
                score = _compute_relevance(chunk.content or "", keywords, False)
                results.append({
                    "title": chunk.doc_name,
                    "content_snippet": _snippet(chunk.content or "", keywords),
                    "category": chunk.category,
                    "relevance_score": round(score, 3),
                    "chunk_id": chunk.id,
                })
    except Exception as exc:
        logger.warning(f"规范库匹配查询失败：{exc}")
        return []
    finally:
        session.close()

    # 按相关度排序，取 top_k
    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    return results[:top_k]


# 检索来源 → 展示用分类名
_SOURCE_TYPE_LABELS = {
    "knowledge_doc": "知识库文档",
    "work_record": "工作记录",
    "document": "已生成文书",
    "note": "笔记",
    "kg_entity": "知识图谱",
    "kg_checkpoint": "监理要点（表单字段）",
}

# 各来源在「隐患→条款」场景下的可信度系数。
# kg_checkpoint 是 LLM 从表单模板抽出的字段名（"应急救援准备情况"、"当日存在问题及处理"），
# 不含条款语义，却因为文本短、词面重叠高而容易冲进前列 —— 实测 5 条测试隐患里有 2~3 条
# 的 top5 席位被它占掉，把真正的规范条款和知识库文档挤到后面。降权后保留其可用性，
# 但不再霸榜。其余来源保持 1.0。
_SOURCE_WEIGHTS = {
    "kg_checkpoint": 0.55,
}


def _match_from_rag_index(
    hazard_category: str,
    hazard_description: str,
    top_k: int,
) -> List[dict]:
    """用 RAG 混合检索匹配相关条款/资料。

    覆盖知识库文档、系统积累的工作记录与文书、知识图谱实体。返回结构与
    ``regulation_chunks`` 路径保持一致（title / content_snippet / category /
    relevance_score / chunk_id），消费方（ai_client、inspection_service）无需改动。
    """
    try:
        from app.services import rag_index
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG 检索模块不可用：%s", exc)
        return []

    query = " ".join(
        part for part in (hazard_category, hazard_description) if part
    ).strip()
    if not query:
        return []

    try:
        hits = rag_index.search(query, top_k=max(top_k * 3, 15))
    except Exception as exc:  # noqa: BLE001
        logger.warning("RAG 规范匹配失败：%s", exc)
        return []

    results: list[dict] = []
    for hit in hits:
        haystack = f"{hit['source_name']}{hit['content']}"
        # 类别词出现在结果里，说明专业方向对得上，给一点加权
        bonus = 0.15 if hazard_category and hazard_category in haystack else 0.0
        weight = _SOURCE_WEIGHTS.get(hit["source_type"], 1.0)
        results.append({
            "title": hit["source_name"],
            "content_snippet": hit["snippet"],
            "category": _SOURCE_TYPE_LABELS.get(hit["source_type"], hit["source_type"]),
            "relevance_score": round(min(1.0, hit["score"] * weight + bonus), 3),
            "chunk_id": hit["chunk_id"],
            "source_type": hit["source_type"],
            "matched_by": hit["matched_by"],
        })

    results.sort(key=lambda item: item["relevance_score"], reverse=True)
    return results[:top_k]


def match_regulations_for_hazard(
    hazard_category: str,
    hazard_description: str,
    top_k: int = 5,
) -> List[dict]:
    """根据隐患类别和描述，检索相关条款/资料。

    两条路径：
    1. ``regulation_chunks`` 规范库关键词匹配（规范条文入库后生效）；
    2. 命中不足时用 RAG 混合检索兜底 —— 覆盖知识库文档、工作记录、已生成文书、
       知识图谱实体，解决"规范条文尚未入库时该能力完全空转"的问题。

    Args:
        hazard_category: 隐患类别（如：安全帽、临边防护、脚手架）
        hazard_description: 隐患描述
        top_k: 返回最多 top_k 条

    Returns:
        [{title, content_snippet, category, relevance_score, chunk_id}]
    """
    if top_k <= 0:
        return []

    results = _match_from_regulation_chunks(hazard_category, hazard_description, top_k)
    if results:
        return results

    return _match_from_rag_index(hazard_category, hazard_description, top_k)
