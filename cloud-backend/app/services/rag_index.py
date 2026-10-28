"""RAG 混合检索索引 —— 知识库文档与系统积累记录的统一语义检索

检索策略：**BM25（jieba 分词）+ 向量余弦（bge-small-zh）+ RRF 融合**。

为什么不引入向量数据库：当前语料分块后约 1k 块量级，512 维 float32 全量约 2 MB，
直接载入内存做一次矩阵乘法比维护外部索引更简单也更快。语料涨到几万块再考虑
pgvector / FAISS。

DB 访问用同步引擎（pymysql），与 ``regulation_matcher`` 保持一致的写法，避免在
async 事件循环里混用会话。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.kb_chunk import KbChunk
from app.services import chunker, embedding

logger = logging.getLogger(__name__)

# RRF 融合常数：60 是信息检索里的常规取值，作用是压平头部名次的差距
RRF_K = 60

# 标题块：每个来源额外索引一条「文档名单独成块」的记录，chunk_index 固定为 -1。
# 动机：省统表这类表单文档正文极短（总代授权书仅 64 字、监理档案移交目录 144 字），
# 内容是「工程项目名称：/编号：/移交人：」这类字段名，会把文档名的语义稀释掉。
# 实测把文档名单独编码后，「监理资料最后归档时怎么清点交接」→「监理档案移交目录」
# 的向量相似度 0.674 是全部结果里最高的（其余 ≤0.629），
# 「设计或施工方案改动后怎么记录」→「工程变更台账」也从 rank 3 提升到 rank 1。
TITLE_CHUNK_INDEX = -1

# 标题路权重。**必须是 3 而不是 1**：RRF 是「多路贡献求和」，一个只被标题路命中的
# 文档，天然打不过被 BM25+向量双路命中的文档 —— 实测「监理档案移交目录」向量分
# 最高（0.674）却只排第 8，被一堆双路命中的低分文档挤掉。
# 要让单路标题命中与双路正文命中势均力敌，权重需 > 2；取 3 留出余量。
# 这与检索实践一致：标题命中本身就是强证据（用户常按文档名描述需求），
# Lucene/BM25F 里的 title boost 也是同一思路。
TITLE_CHUNK_WEIGHT = 3.0

# 实体摘要块：把「从该文档抽出的知识图谱实体说明」单独编成一块，chunk_index 固定 -2。
# 动机与标题块同源，但解决的是另一半问题：标题块只表达「文档叫什么」，表达不了
# 「文档是干什么用的」。用户问「现场站着盯着关键工序施工的那种表格」时，文档名
# 「14旁站记录」对不上，正文只有「工程项目名称：」这类字段名，但实体说明写着
# 「对关键部位、关键工序施工质量实施全过程现场跟班监督的记录表格」—— 完全对得上。
#
# 为什么不直接拼进正文：实测拼进正文后该文档只从第 11 名升到第 10 名。原因是 RRF 的
# 「多路命中胜过单路高分」特性 —— 该文档只在向量路上强，BM25 与标题路都弱，
# 融合分天然打不过双路命中的文档。把它做成独立的**标题路**块（权重 3.0）才能抗衡。
EXTRA_CHUNK_INDEX = -2

# 单条结果的理论最高分在语义桥的常量定义之后计算（见下方 _MAX_RRF）。

SOURCE_TYPE_KNOWLEDGE = "knowledge_doc"
SOURCE_TYPE_WORK_RECORD = "work_record"
SOURCE_TYPE_DOCUMENT = "document"
SOURCE_TYPE_NOTE = "note"
SOURCE_TYPE_KG = "kg_entity"

# 「危大工程专项施工方案严重缺陷清单」（建办质〔2024〕63 号）80 条红线。
# 独立 source_type：这是方案审查的**硬性判定标准**（踩中一条即审查不过 / 重大事故隐患），
# 与一般的规范条文、知识库文档性质不同，检索消费方（方案审查、规范问答）需要能单独锁定它。
# 语料来自 app/data/severe_defect_checklist.json（结构化 80 条：原文 + 大白话解读）。
SOURCE_TYPE_SEVERE_DEFECT = "severe_defect"

# 知识图谱实体分两型存放。184 个实体里有 106 个 entity_type = 「监理要点」，
# 它们是 LLM 从表单模板抽出来的**字段名**（"应急救援准备情况"、"当日存在问题及处理"、
# "安装/拆卸特种作业人员到位"），本身不承载条款语义；剩下 78 个
# （规范条款 30 / 验收流程 15 / 工程部位 14 / 施工工序 9 / 隐患类型 6 / 材料标准 4）
# 才是真知识。两者同源但性质不同，用独立 source_type 区分，让消费方（隐患→规范匹配）
# 只降权字段名，不影响真条款。实测隐患 query 的 top5 里有 2~3 条是被字段名占掉的。
SOURCE_TYPE_KG_CHECKPOINT = "kg_checkpoint"

# 判为「表单字段名」的 KG 实体类型
KG_CHECKPOINT_ENTITY_TYPE = "监理要点"

# ---------------------------------------------------------------- 语义桥
# 用户提问常用口语描述表单，而表单文档名是「14旁站记录」这种编号缩写，两者没有任何
# 词面交集 —— 实测「现场站着盯着关键工序施工的那种表格」在纯文档检索里排第 11 位。
# 但知识图谱里恰好有实体能从语义上对上这句口语，且实体记着来源文档。
# 于是让 KG 实体命中后不直接展示，而是把它的融合分转投给它的来源文档。
#
# ⚠️ **默认关闭**（见 search 的 bridge 参数）。实测净收益为负：它救回了 1 道口语题，
# 却把 2 道原本正确的题挤出了 top5，Recall@5 从 93.3% 掉到 90.0%、MRR 从 0.796
# 掉到 0.721。原因是 ``source_doc`` 只说明「实体从哪份文件抽出」，不等于「用户想找的
# 就是那份文件」：用户问「工地存在哪些危险因素要提前梳理」，实体「危险性较大的分部
# 分项工程监理巡视检查」把票投给了巡视检查记录表，而正确答案是危险源控制表。
# 更有效的做法是把实体说明并进文档索引文本（见 _collect_knowledge_files 的
# entity_notes），走正常检索路径而不是额外加权。
BRIDGE_SOURCE_TYPES = (SOURCE_TYPE_KG,)

# 桥的权重。桥只是一条间接证据，与正文路同量级即可 —— 它靠「只有最相关的少数
# 实体才有投票权」来收敛，而不是靠压低权重。
BRIDGE_WEIGHT = 1.0

# 只有向量路名次进入前 N 的 KG 实体才投得出票。
# 实测放开不限制时，hidden 池里大量实体都会拿到好名次，几乎每篇文档都被投到票，
# 结果所有候选的融合分一起顶到归一化上限（score 全变 1.0），区分度被抹平。
BRIDGE_TOP_N = 5

# 单条结果的理论最高分：标题路 + BM25 正文路 + 向量正文路 + 桥路各取第一名。
# 桥路必须计入分母，否则被桥加过分的结果会超过 1.0 被 clamp 成同一个值。
_MAX_RRF = (2.0 + TITLE_CHUNK_WEIGHT + BRIDGE_WEIGHT) / (RRF_K + 1)

ALL_SOURCE_TYPES = (
    SOURCE_TYPE_KNOWLEDGE,
    SOURCE_TYPE_WORK_RECORD,
    SOURCE_TYPE_DOCUMENT,
    SOURCE_TYPE_NOTE,
    SOURCE_TYPE_KG,
    SOURCE_TYPE_SEVERE_DEFECT,
)

# include_kg=False 时要排除的全部 KG 分型
KG_SOURCE_TYPES = (SOURCE_TYPE_KG, SOURCE_TYPE_KG_CHECKPOINT)

SUPPORTED_EXTS = {".docx", ".xlsx", ".pdf", ".doc", ".xls", ".txt", ".md"}

# ---------------------------------------------------------------- 同步 DB 会话

_sync_engine = None
_sync_session_factory = None
_engine_lock = threading.Lock()


def get_sync_session() -> Session:
    """同步 DB 会话（懒加载，单引擎复用）。"""
    global _sync_engine, _sync_session_factory
    if _sync_engine is None:
        with _engine_lock:
            if _sync_engine is None:
                url = (
                    settings.database_url
                    .replace("mysql+aiomysql://", "mysql+pymysql://")
                    .replace("mysql+asyncmy://", "mysql+pymysql://")
                    .replace("sqlite+aiosqlite://", "sqlite://")
                )
                _sync_engine = create_engine(
                    url, echo=False, pool_pre_ping=True, pool_recycle=1800
                )
                _sync_session_factory = sessionmaker(_sync_engine, expire_on_commit=False)
    _ensure_models()
    return _sync_session_factory()


_MODEL_MODULES = (
    "user", "project", "document", "hazard", "work_record", "note",
    "knowledge_base", "kb_chunk", "knowledge_graph", "image", "rectification",
    "inspection_session", "task_assignment", "acceptance_template", "archive",
    "regulation_chunk", "signature", "meeting", "progress", "parallel",
    "resources", "visa", "equipment", "ledger", "miniprogram",
    "wechat_subscription", "regulation_version", "notification", "model_config",
)
_models_ready = False


def _ensure_models() -> None:
    """确保所有 ORM 模型已导入。

    SQLAlchemy 解析 ``relationship("User")`` 这类字符串引用时，目标类必须已注册到
    registry；只导入部分模型会报 ``expression 'User' failed to locate a name``。
    这里在脚本/接口调用路径上也补齐导入，使 ``rag_index`` 不依赖 ``init_db`` 的副作用。
    """
    global _models_ready
    if _models_ready:
        return
    import importlib

    for module in _MODEL_MODULES:
        try:
            importlib.import_module(f"app.models.{module}")
        except Exception:  # noqa: BLE001 — 单个模型缺失不应阻断其余
            continue
    _models_ready = True


# ---------------------------------------------------------------- 内存索引缓存

_cache_lock = threading.Lock()
_cache_version: tuple[int, int] | None = None
_cache_entries: list[dict] = []

# 重建串行锁：后台守护与「上传后立即索引」可能同时触发，两个线程一起重写
# kb_chunks 会互相踩（A 算完 hash、B 删行、A 再插，产物重复或丢失）。
# 用非阻塞获取 —— 拿不到说明已有一次刷新在跑，而那次跑完覆盖的是同一批数据，
# 直接放弃即可，没必要排队。
_build_lock = threading.Lock()


def _table_version(session: Session) -> tuple[int, int]:
    """(行数, 最大 id) —— 任一变化即表示索引内容已更新。"""
    row = session.execute(select(func.count(KbChunk.id), func.max(KbChunk.id))).one()
    return int(row[0] or 0), int(row[1] or 0)


def _cut(text: str) -> list[str]:
    """jieba 分词，丢弃纯空白 token。"""
    if not text:
        return []
    import jieba

    return [token for token in jieba.lcut(text) if token.strip()]


def _load_entries(session: Session) -> list[dict]:
    rows = session.execute(select(KbChunk)).scalars().all()
    entries: list[dict] = []
    for row in rows:
        content = row.content or ""
        search_text = f"{row.source_name} {content}"
        entries.append({
            "id": row.id,
            "kb_id": row.kb_id,
            "project_id": row.project_id,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_key": (row.source_type, row.source_id),
            "source_name": row.source_name,
            "bridge_doc": row.bridge_doc,
            "chunk_index": row.chunk_index,
            "content": content,
            "tokens": _cut(search_text),
            "vec": embedding.from_blob(row.embedding, row.dim or embedding.EMBED_DIM),
        })

    # 标题块命中时，展示用的正文要取同来源的首个内容块（标题块自身只有文档名，
    # 作为引用片段太单薄）。
    first_content: dict[tuple, str] = {}
    for entry in entries:
        if entry["chunk_index"] >= 0:
            first_content.setdefault(entry["source_key"], entry["content"])
    for entry in entries:
        if entry["chunk_index"] < 0:
            entry["display_content"] = first_content.get(entry["source_key"], entry["source_name"])
        else:
            entry["display_content"] = entry["content"]
    return entries


def _get_entries(force: bool = False) -> list[dict]:
    """取缓存索引；表内容变化时自动重载。"""
    global _cache_version, _cache_entries
    with _cache_lock:
        session = get_sync_session()
        try:
            version = _table_version(session)
            if not force and _cache_version == version and _cache_entries:
                return _cache_entries
            entries = _load_entries(session)
        finally:
            session.close()
        _cache_entries = entries
        _cache_version = version
        logger.info("RAG 索引已载入：%s 块（version=%s）", len(entries), version)
        return _cache_entries


def invalidate() -> None:
    """清空内存缓存（重建索引后调用）。"""
    global _cache_version, _cache_entries
    with _cache_lock:
        _cache_version = None
        _cache_entries = []


def has_index() -> bool:
    """索引是否已构建。

    调用方据此决定「检索为空」该解释为「确实没有相关内容」，还是「索引没建、
    应该回退到关键词检索」—— 这两种情况的处置完全不同。
    """
    try:
        return bool(_get_entries())
    except Exception:  # noqa: BLE001
        return False


def stats() -> dict:
    """索引概览，用于自检与前端展示。"""
    session = get_sync_session()
    try:
        rows = session.execute(
            select(KbChunk.source_type, func.count(KbChunk.id)).group_by(KbChunk.source_type)
        ).all()
        total, _ = _table_version(session)
    except Exception as exc:  # noqa: BLE001
        session.close()
        return {"available": False, "error": str(exc)[:200]}
    session.close()
    return {
        "available": True,
        "chunks": total,
        "by_source_type": {row[0]: row[1] for row in rows},
        "embedding_available": embedding.available(),
    }


# ---------------------------------------------------------------- 检索

def _matches(entry: dict, source_types, kb_id, project_id) -> bool:
    if source_types and entry["source_type"] not in source_types:
        return False
    if kb_id is not None and entry["kb_id"] != kb_id:
        return False
    # 知识库文档的 project_id 为空（全局可用），业务记录必须落在指定项目内
    if project_id is not None and entry["project_id"] not in (None, project_id):
        return False
    return True


def _doc_key(value: str | None) -> str:
    """文档名归一化，用于「同一张表的不同副本」判定与「KG 实体 → 源文档」桥接匹配。

    实体记的 source_doc 是 ``省统表word版/14旁站记录.docx``，分块表里知识库文档的
    source_id 也是这个相对路径，但 source_name 可能是裸名，所以两侧都要能对上：
    去掉目录与扩展名、抹平空白差异、统一小写。

    还要去掉开头的序号 —— 省统表文件名普遍带编号（``14旁站记录``），而这些表在
    安全监理用表目录下又存了一份，命名成 ``5.建筑施工起重机械…记录表``，
    带不带前缀完全随机。不去掉就会把同一张表当成两个文档。
    """
    base = os.path.basename((value or "").strip())
    lowered = base.lower()
    for ext in (".docx", ".xlsx", ".pdf", ".doc", ".xls"):
        if lowered.endswith(ext):
            base = base[: len(base) - len(ext)]
            break
    key = base.replace(" ", "").replace("\u3000", "").lower()
    return key.lstrip("0123456789.-_、") or key


def _snippet(content: str, tokens: Sequence[str], width: int = 180) -> str:
    """截取命中词附近的片段。"""
    if not content:
        return ""
    if len(content) <= width * 2:
        return content
    hit = -1
    for token in tokens:
        if len(token) < 2:
            continue
        pos = content.find(token)
        if pos != -1:
            hit = pos
            break
    if hit == -1:
        return content[: width * 2]
    start = max(0, hit - width // 2)
    end = min(len(content), hit + width)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return f"{prefix}{content[start:end]}{suffix}"


def search(
    query: str,
    *,
    top_k: int = 5,
    source_types: Sequence[str] | None = None,
    kb_id: int | None = None,
    project_id: int | None = None,
    bridge: bool = False,
) -> list[dict]:
    """混合检索（三路字段化召回 + 文档级 RRF 融合）。

    三路各自独立排名，再在**来源（文档）级**融合：
        ① BM25 —— 只对正文块排名
        ② 向量 —— 只对正文块排名
        ③ 标题向量 —— 只对标题块排名（权重见 TITLE_CHUNK_WEIGHT）

    为什么标题路必须独立排名：标题块编码的是「文档名」，正文块编码的是「名 + 数百字
    正文」，两者相似度不在同一尺度上（通用表单正文反而更容易与通用问句"看起来像"）。
    混在同一张排名表里，实测会让「工程变更台账」这类正确答案掉到 40 名开外；拆成
    独立一路后，它就能拿到自己应有的名次。这正是检索里 fielded/BM25F 的常规做法。

    同一来源在一路上只保留最好名次，且最终每个来源只占一个结果位。

    另有**语义桥**一路：当调用方明确只要知识库文档时，知识图谱实体不展示，但它一旦
    被召回，就把自己的融合分转投给 ``bridge_doc`` 记录的来源文档。理由见 BRIDGE_WEIGHT
    上方注释 —— 用户的口语描述能对上实体说明，却对不上表单文档名。
    """
    query = (query or "").strip()
    if not query or top_k <= 0:
        return []
    try:
        entries = _get_entries()
    except Exception as exc:  # noqa: BLE001 — 索引不可用不应阻断业务
        logger.warning("RAG 索引不可用：%s", exc)
        return []

    candidates = [e for e in entries if _matches(e, source_types, kb_id, project_id)]
    if not candidates:
        return []

    query_tokens = _cut(query)
    visible_keys = {e["source_key"] for e in candidates}

    # ---- 语义桥：KG 实体不展示，只把它命中的票转投给来源文档 --------------
    # 仅当调用方明确排除了 KG 类型时启用；若调用方本身就要 KG 结果，
    # 实体已经出现在结果里，再让它投票就是重复计分。
    requested = set(source_types) if source_types else None
    bridge_enabled = (
        bridge and bool(requested) and not (requested & set(KG_SOURCE_TYPES))
    )

    pool: list[dict] = list(candidates)
    doc_target: dict[str, tuple] = {}
    if bridge_enabled:
        for entry in entries:
            if entry["source_type"] not in BRIDGE_SOURCE_TYPES or not entry["bridge_doc"]:
                continue
            # 桥实体是全局词典：它不属于任何知识库，故不受 kb_id 约束（票只会投给
            # 通过 kb_id 过滤的文档）；但项目隔离仍要尊重。
            if project_id is not None and entry["project_id"] not in (None, project_id):
                continue
            pool.append(entry)
        for entry in candidates:
            if entry["source_type"] == SOURCE_TYPE_KNOWLEDGE:
                doc_target.setdefault(_doc_key(entry["source_id"]), entry["source_key"])
                doc_target.setdefault(_doc_key(entry["source_name"]), entry["source_key"])

    title_positions = [i for i, e in enumerate(pool) if e["chunk_index"] < 0]
    vector_positions = [i for i, e in enumerate(pool) if e["chunk_index"] >= 0]
    # BM25 只在可见来源上打分：把桥实体塞进语料会改变 IDF，扰动正文排序
    bm25_positions = [i for i in vector_positions if pool[i]["source_key"] in visible_keys]

    # 来源 → {路名: (名次, 分数, 候选下标)}
    arms: dict[tuple, dict[str, tuple[int, float, int]]] = {}

    def _record(key: tuple, arm: str, rank: int, raw: float, idx: int) -> None:
        bucket = arms.setdefault(key, {})
        current = bucket.get(arm)
        if current is None or rank < current[0]:
            bucket[arm] = (rank, raw, idx)

    # ① BM25（仅正文块）
    if query_tokens and bm25_positions:
        try:
            from rank_bm25 import BM25Okapi

            corpus = [pool[i]["tokens"] or [] for i in bm25_positions]
            if any(corpus):
                bm25_scores = np.asarray(
                    BM25Okapi(corpus).get_scores(query_tokens), dtype=np.float64
                )
                for rank, pos in enumerate(np.argsort(-bm25_scores), start=1):
                    value = float(bm25_scores[int(pos)])
                    if value <= 0:
                        break
                    idx = bm25_positions[int(pos)]
                    _record(pool[idx]["source_key"], "bm25", rank, value, idx)
        except Exception as exc:  # noqa: BLE001
            logger.debug("BM25 打分失败，仅用向量检索：%s", exc)

    # ② ③ 向量（正文、标题各自排名，不混在一张表里；正文路含隐藏的桥实体）
    query_vec = embedding.embed_query(query)
    if query_vec is not None:
        for arm, positions in (("vector", vector_positions), ("title", title_positions)):
            usable = [i for i in positions if pool[i]["vec"] is not None]
            if not usable:
                continue
            matrix = np.vstack([pool[i]["vec"] for i in usable])
            sims = matrix @ query_vec
            for rank, pos in enumerate(np.argsort(-sims), start=1):
                idx = usable[int(pos)]
                _record(pool[idx]["source_key"], arm, rank, float(sims[int(pos)]), idx)

    if not arms:
        return []

    arm_weights = {"bm25": 1.0, "vector": 1.0, "title": TITLE_CHUNK_WEIGHT}

    # 每路的最高原始分，用于把原始分折算成 0~1 的「强度」。
    # 纯 RRF 只用名次，会把「某一路断层领先」的优势彻底压平：BM25 上
    # 「11.96 vs 5.93」进到 RRF 只剩 1/61 与 1/63 的差别，于是「三路都在十名
    # 开外」的文档靠路数多，反而赢过「一路第一、另两路平庸」的文档。
    # 实测：问「工地存在哪些危险因素要提前梳理」，真正要的
    # 「安全监理危险源控制表」BM25 排第一（11.96，是第二名的两倍）却因向量第
    # 38、标题第 26 落到第 6 位。
    arm_ceiling: dict[str, float] = {}
    for bucket in arms.values():
        for arm, (_, raw_value, _) in bucket.items():
            if raw_value > arm_ceiling.get(arm, 0.0):
                arm_ceiling[arm] = raw_value

    def _fusion_strength(arm: str, raw_value: float) -> float:
        """该路当前分相对该路最高分的强度（0~1）。

        乘进权重、而不是取代名次：算法仍以「多路命中」为主信号，只是不再把
        路内差距完全抹平。上界依然为 1，``_MAX_RRF`` 无需调整。
        """
        ceiling = arm_ceiling.get(arm) or 0.0
        if ceiling <= 0:
            return 1.0
        return raw_value / ceiling

    def _fuse(bucket: dict) -> float:
        return sum(
            arm_weights[arm] * _fusion_strength(arm, raw_value) / (RRF_K + rank)
            for arm, (rank, raw_value, _) in bucket.items()
        )

    scores: dict[tuple, tuple[float, dict]] = {}
    bridge_bonus: dict[tuple, float] = {}
    for key, bucket in arms.items():
        value = _fuse(bucket)
        if key in visible_keys:
            scores[key] = (value, bucket)
            continue
        if not bridge_enabled:
            continue
        # 隐藏的 KG 实体：自己不占结果位，把票转投给它的来源文档。
        # 只有最相关的 BRIDGE_TOP_N 个实体有投票权，且同一文档只取最高的一票 ——
        # 否则一批弱相关实体各自投票，会把所有候选一起顶到归一化上限。
        best_rank = min(item[0] for item in bucket.values())
        if best_rank > BRIDGE_TOP_N:
            continue
        idx = next(iter(bucket.values()))[2]
        doc = pool[idx].get("bridge_doc")
        target = doc_target.get(_doc_key(doc)) if doc else None
        if target is None:
            continue
        vote = BRIDGE_WEIGHT / (RRF_K + best_rank)
        if vote > bridge_bonus.get(target, 0.0):
            bridge_bonus[target] = vote
    for key, bonus in bridge_bonus.items():
        base, bucket = scores.get(key, (0.0, {}))
        scores[key] = (base + bonus, bucket)
    if not scores:
        return []

    # 每个来源的首个正文块下标（用于「只被桥提名」的来源给引用片段兜底）
    first_content: dict[tuple, int] = {}
    for i, entry in enumerate(pool):
        key = entry["source_key"]
        if entry["chunk_index"] < 0 or key not in visible_keys:
            continue
        current = first_content.get(key)
        if current is None or entry["chunk_index"] < pool[current]["chunk_index"]:
            first_content[key] = i

    ranked = sorted(scores.items(), key=lambda item: item[1][0], reverse=True)

    results: list[dict] = []
    # 同名文档合并：知识库里有若干张表在两个目录各存了一份（危险性较大…记录表、
    # 安全监理工作联系单、安全隐患整改通知、起重机械安装拆卸…记录表），
    # 它们 source_id 不同所以是不同来源，但展示名相同 —— 不去重时同一张表会占掉
    # top5 里的两个席位（实测 q18 的 top1、top2 就是同一张表的两个副本，
    # 把正确答案挤到了第 6 位）。
    seen_names: set[str] = set()
    for key, (score, bucket) in ranked:
        # 展示与引用优先用正文命中块；只有标题路命中时才退回该来源的首个正文块
        content_arms = {arm: value for arm, value in bucket.items() if arm != "title"}
        if content_arms:
            chosen = min(content_arms.values(), key=lambda item: item[0])
            entry = pool[chosen[2]]
            display = entry["content"]
        elif "title" in bucket:
            entry = pool[bucket["title"][2]]
            display = entry["display_content"]
        else:
            # 三路都没直接命中，仅靠 KG 实体投票上位：用首个正文块展示
            position = first_content.get(key)
            if position is None:
                continue
            entry = pool[position]
            display = entry["content"]

        name_key = _doc_key(entry["source_name"])
        if name_key in seen_names:
            continue
        seen_names.add(name_key)

        results.append({
            "chunk_id": entry["id"],
            "source_type": entry["source_type"],
            "source_id": entry["source_id"],
            "source_name": entry["source_name"],
            "kb_id": entry["kb_id"],
            "project_id": entry["project_id"],
            "content": display,
            "snippet": _snippet(display, query_tokens),
            "score": round(min(1.0, score / _MAX_RRF), 4),
            "bm25_score": round(bucket.get("bm25", (0, 0.0, 0))[1], 4),
            "vector_score": round(
                bucket["vector"][1] if "vector" in bucket else bucket.get("title", (0, 0.0, 0))[1],
                4,
            ),
            "matched_by": "+".join(sorted(bucket)) or "bridge",
            "matched_title": "title" in bucket and not content_arms,
            "bridge_score": round(bridge_bonus.get(key, 0.0) / _MAX_RRF, 4),
        })
        if len(results) >= top_k:
            break
    return results


# ---------------------------------------------------------------- 建索引

def index_source(
    session: Session,
    *,
    source_type: str,
    source_id: str,
    source_name: str,
    text: str,
    kb_id: int | None = None,
    project_id: int | None = None,
    bridge_doc: str | None = None,
    extra_text: str = "",
    force: bool = False,
) -> dict:
    """把单个来源切块、编码并写库。内容未变则跳过（按 source_hash 判重）。

    ``extra_text`` 会作为独立的「实体摘要块」（chunk_index = -2）编入标题路，
    叠加在正文分块之上，用于补足短表单文档自身的语义表达。
    """
    text = chunker.normalize(text)
    extra = chunker.normalize(extra_text)
    if not text and not extra:
        return {"status": "empty", "chunks": 0}

    # 指纹必须含 source_name：标题块（chunk_index=-1）的 content 就是文档名，
    # 只算正文会让「文档改名」被判为未变更，标题块永远停在旧名字上
    # （实测：上传文档的落盘名是 uuid，修正显示名后索引里仍是那串十六进制）。
    digest = hashlib.sha256(
        f"{source_name}\x00{text}\x00{extra}".encode("utf-8")
    ).hexdigest()
    existing = session.execute(
        select(KbChunk.source_hash).where(
            KbChunk.source_type == source_type, KbChunk.source_id == source_id
        )
    ).scalars().all()
    if not force and existing and all(item == digest for item in existing):
        return {"status": "skipped", "chunks": len(existing)}

    session.execute(
        delete(KbChunk).where(
            KbChunk.source_type == source_type, KbChunk.source_id == source_id
        )
    )

    chunks = chunker.chunk_text(text) if text else []
    if not chunks and not extra:
        return {"status": "empty", "chunks": 0}

    # 标题块与实体摘要块都落在 chunk_index < 0，检索时统一归入「标题路」（高权重）。
    # 理由见 TITLE_CHUNK_INDEX / EXTRA_CHUNK_INDEX 的注释。
    title_text = (source_name or "").strip()
    head_texts: list[tuple[int, str]] = []
    if title_text:
        head_texts.append((TITLE_CHUNK_INDEX, title_text))
    if extra:
        head_texts.append((EXTRA_CHUNK_INDEX, extra))

    inputs = [value for _, value in head_texts] + [
        chunker.compose_embed_text(source_name, chunk) for chunk in chunks
    ]
    vectors = embedding.embed_texts(inputs)
    offset = len(head_texts)
    bridge_value = (bridge_doc or "").strip()[:256] or None

    def _vector_at(position: int):
        return None if vectors is None else vectors[position]

    rows = []
    for position, (chunk_index, value) in enumerate(head_texts):
        vector = _vector_at(position)
        rows.append(KbChunk(
            kb_id=kb_id,
            project_id=project_id,
            source_type=source_type,
            source_id=source_id,
            source_name=source_name[:256],
            source_hash=digest,
            bridge_doc=bridge_value,
            chunk_index=chunk_index,
            content=value,
            embedding=embedding.to_blob(vector) if vector is not None else None,
            dim=embedding.EMBED_DIM if vector is not None else 0,
        ))

    for position, chunk in enumerate(chunks):
        vector = _vector_at(position + offset)
        rows.append(KbChunk(
            kb_id=kb_id,
            project_id=project_id,
            source_type=source_type,
            source_id=source_id,
            source_name=(source_name or "")[:256],
            source_hash=digest,
            bridge_doc=bridge_value,
            chunk_index=position,
            content=chunk,
            embedding=embedding.to_blob(vector) if vector is not None else None,
            dim=embedding.EMBED_DIM if vector is not None else 0,
        ))
    session.add_all(rows)
    session.flush()
    return {"status": "indexed", "chunks": len(rows)}


def _readable(value) -> str:
    """把 JSON 列 / 列表 / 字典转成可读文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "；".join(part for part in (_readable(item) for item in value) if part)
    if isinstance(value, dict):
        return "；".join(
            f"{key}：{_readable(item)}" for key, item in value.items() if _readable(item)
        )
    return str(value)


def _collect_knowledge_files(session: Session) -> list[dict]:
    """遍历知识库目录下所有可解析文档（含未登记进 DB 的根目录文档）。"""
    from app.models.knowledge_base import KnowledgeBase, KnowledgeDocument

    root = Path(settings.knowledge_base_path)
    if not root.exists():
        return []

    # 已登记文档 → (kb_id, 显示名) 映射（按绝对路径）。
    # 显示名必须取自 DB：上传时文件以 uuid 落盘，若用 path.stem，
    # 索引里的文档名会变成一串十六进制，标题块（chunk_index=-1）随之失效。
    registered: dict[str, tuple[int, str]] = {}
    for doc in session.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.is_deleted.is_(None)
            | (KnowledgeDocument.is_deleted == False)  # noqa: E712
        )
    ).scalars().all():
        if doc.path:
            registered[os.path.normpath(doc.path)] = (doc.kb_id, doc.name or "")
    default_kb_id = session.execute(select(KnowledgeBase.id).order_by(KnowledgeBase.id)).scalars().first()

    # 知识图谱实体是对文档内容的凝练。把它们作为**独立的实体摘要块**编进该文档
    # （而不是拼进正文），口语化提问就多了一条命中表单文档的路径：用户问「现场站着
    # 盯着关键工序施工的那种表格」，表单正文只有「工程项目名称／编号」这类字段名，
    # 而实体说明写着「对关键部位、关键工序施工质量实施全过程现场跟班监督的记录表格」。
    # 只收录真条款实体 —— 「监理要点」是表单字段名，本身不承载语义。
    from app.models.knowledge_graph import KGEntity

    entity_notes: dict[str, list[str]] = {}
    for entity in session.execute(select(KGEntity)).scalars().all():
        doc = (entity.source_doc or "").strip()
        if not doc or not entity.name:
            continue
        if (entity.entity_type or "").strip() == KG_CHECKPOINT_ENTITY_TYPE:
            continue
        note = entity.name
        description = _readable(entity.description).strip()
        if description:
            note = f"{note}：{description}"
        entity_notes.setdefault(_doc_key(doc), []).append(note)

    sources: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        hit = registered.get(os.path.normpath(str(path)))
        kb_id = hit[0] if hit else default_kb_id
        display_name = (hit[1] if hit else "").strip()
        relative = path.relative_to(root)
        notes = entity_notes.get(_doc_key(str(relative)))
        sources.append({
            "source_type": SOURCE_TYPE_KNOWLEDGE,
            "source_id": str(relative),
            "source_name": display_name or path.stem,
            "path": path,
            "kb_id": kb_id,
            "project_id": None,
            "extra_text": "\n".join(notes) if notes else "",
        })
    return sources


def _extract_file_text(path: Path) -> str:
    """复用 knowledge_service 里现成的三套抽取器。"""
    from app.services.knowledge_service import (
        _extract_docx_text,
        _extract_pdf_text,
        _extract_xlsx_text,
    )

    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx_text(path)
    if suffix == ".xlsx":
        return _extract_xlsx_text(path)
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    # 纯文本：规范条文正文常以 .txt/.md 形式归档，不接就等于静默丢掉这批语料。
    # 上传接口（ALLOWED_DOC_EXTS）也允许这两类，两侧口径必须一致。
    if suffix in (".txt", ".md"):
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
    # .doc / .xls 无内置解析器（需外部工具），跳过并计入统计
    return ""


def _collect_work_records(session: Session) -> list[dict]:
    from app.models.work_record import WorkRecord

    sources: list[dict] = []
    for record in session.execute(select(WorkRecord)).scalars().all():
        parts = [
            f"工作记录类型：{record.record_type or ''}",
            f"部位：{record.section}" if record.section else "",
            f"位置：{record.location}" if record.location else "",
            f"天气：{record.weather}" if record.weather else "",
            f"工作内容：{_readable(record.work_content)}" if record.work_content else "",
            f"检查事项：{_readable(record.inspection_items)}" if record.inspection_items else "",
            f"检查备注：{_readable(record.inspection_remark)}" if record.inspection_remark else "",
            f"旁站部位：{record.station_location}" if record.station_location else "",
            f"旁站内容：{_readable(record.station_content)}" if record.station_content else "",
            f"旁站结论：{_readable(record.station_conclusion)}" if record.station_conclusion else "",
            f"见证材料：{record.witness_material_name or ''} {record.witness_material_spec or ''}".strip(),
            f"验收依据：{_readable(record.acceptance_basis)}" if record.acceptance_basis else "",
            f"验收结果：{record.acceptance_result}" if record.acceptance_result else "",
            f"存在问题：{_readable(record.problems)}" if record.problems else "",
            f"处理措施：{_readable(record.treatment)}" if record.treatment else "",
            f"备注：{_readable(record.remark)}" if record.remark else "",
        ]
        sources.append({
            "source_type": SOURCE_TYPE_WORK_RECORD,
            "source_id": str(record.id),
            "source_name": f"{record.record_type or '工作'}记录 #{record.id}",
            "text": "\n".join(part for part in parts if part),
            "kb_id": None,
            "project_id": record.project_id,
        })
    return sources


def _collect_documents(session: Session) -> list[dict]:
    from app.models.document import Document

    sources: list[dict] = []
    for doc in session.execute(select(Document)).scalars().all():
        parts = [
            f"文书类型：{doc.doc_type or ''}",
            f"文书编号：{doc.doc_number}" if doc.doc_number else "",
            f"部位：{doc.section}" if doc.section else "",
            f"位置：{doc.location}" if doc.location else "",
            _readable(doc.content),
        ]
        sources.append({
            "source_type": SOURCE_TYPE_DOCUMENT,
            "source_id": str(doc.id),
            "source_name": doc.title or f"文书 #{doc.id}",
            "text": "\n".join(part for part in parts if part),
            "kb_id": None,
            "project_id": doc.project_id,
        })
    return sources


def _collect_notes(session: Session) -> list[dict]:
    from app.models.note import Note

    sources: list[dict] = []
    for note in session.execute(select(Note)).scalars().all():
        sources.append({
            "source_type": SOURCE_TYPE_NOTE,
            "source_id": str(note.id),
            "source_name": note.title or f"笔记 #{note.id}",
            "text": f"{note.title or ''}\n{_readable(note.content)}",
            "kb_id": note.source_kb_id,
            "project_id": None,
        })
    return sources


def _collect_kg_entities(session: Session) -> list[dict]:
    from app.models.knowledge_graph import KGEntity

    sources: list[dict] = []
    for entity in session.execute(select(KGEntity)).scalars().all():
        entity_type = (entity.entity_type or "").strip()
        sources.append({
            "source_type": (
                SOURCE_TYPE_KG_CHECKPOINT
                if entity_type == KG_CHECKPOINT_ENTITY_TYPE
                else SOURCE_TYPE_KG
            ),
            "source_id": str(entity.id),
            "source_name": entity.name or f"实体 #{entity.id}",
            "bridge_doc": entity.source_doc,
            "text": "\n".join(part for part in [
                f"实体类型：{entity.entity_type or ''}",
                f"说明：{_readable(entity.description)}" if entity.description else "",
                f"来源：{entity.source_doc}" if entity.source_doc else "",
            ] if part),
            "kb_id": None,
            "project_id": None,
        })
    return sources


def _collect_severe_defects(session: Session) -> list[dict]:
    """把「危大工程专项施工方案严重缺陷清单」80 条红线接入语义检索。

    语料是结构化 JSON（app/data/severe_defect_checklist.json），每条缺陷按
    「分类名 + 条款原文 + 大白话解读」合成一段文本，单独作为一条来源；
    source_id 用缺陷 id（如 general-01 / lifting-03），稳定可定位。
    """
    data_dir = Path(__file__).resolve().parent.parent / "data"
    checklist_path = data_dir / "severe_defect_checklist.json"
    if not checklist_path.exists():
        # 数据文件缺失时报错但不中断其它来源 —— 由 build_all 记入 errors。
        raise FileNotFoundError(f"严重缺陷清单数据文件不存在：{checklist_path}")

    try:
        checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"严重缺陷清单数据读取失败：{exc}") from exc

    items = checklist.get("items") or []
    cat_map: dict[str, str] = {
        c["key"]: c["name"] for c in (checklist.get("meta", {}).get("categories") or [])
    }

    sources: list[dict] = []
    for item in items:
        cat = cat_map.get(item.get("category_key"), item.get("category") or "")
        clause = (item.get("clause") or "").strip()
        interp = (item.get("interpretation") or "").strip()
        text = "\n".join(part for part in [
            f"严重缺陷分类：{cat}",
            f"条款原文：{clause}",
            f"大白话解读：{interp}",
        ] if part)
        if not text:
            continue
        sources.append({
            "source_type": SOURCE_TYPE_SEVERE_DEFECT,
            "source_id": item.get("id") or f"{cat}-{item.get('seq')}",
            "source_name": f"{cat}·严重缺陷{item.get('seq')}",
            "text": text,
            "kb_id": None,
            "project_id": None,
        })
    return sources


_COLLECTORS = {
    SOURCE_TYPE_KNOWLEDGE: _collect_knowledge_files,
    SOURCE_TYPE_WORK_RECORD: _collect_work_records,
    SOURCE_TYPE_DOCUMENT: _collect_documents,
    SOURCE_TYPE_NOTE: _collect_notes,
    SOURCE_TYPE_KG: _collect_kg_entities,
    SOURCE_TYPE_SEVERE_DEFECT: _collect_severe_defects,
}


def build_all(
    *,
    force: bool = False,
    only: Iterable[str] | None = None,
    include_kg: bool = True,
) -> dict:
    """重建全部索引（幂等：内容未变的来源会被跳过）。

    Returns:
        {indexed, skipped, empty, failed, chunks, by_type, errors}
    """
    wanted = [t for t in (only or ALL_SOURCE_TYPES) if t in _COLLECTORS]
    if not include_kg:
        wanted = [t for t in wanted if t not in KG_SOURCE_TYPES]

    report = {
        "indexed": 0, "skipped": 0, "empty": 0, "failed": 0,
        "chunks": 0, "pruned": 0, "by_type": {}, "errors": [],
        "embedding_available": embedding.available(),
    }
    session = get_sync_session()
    try:
        # 本次运行「应当存在」的 (source_type, source_id) 全集，用于回收孤儿行
        expected: set[tuple[str, str]] = set()
        touched_types: set[str] = set()

        for collector_type in wanted:
            try:
                sources = _COLLECTORS[collector_type](session)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"{collector_type} 采集失败：{exc}")
                continue

            counter = {"indexed": 0, "skipped": 0, "empty": 0, "failed": 0, "chunks": 0}
            for source in sources:
                # 一个采集器可以产出多种 source_type（KG 采集器会分 kg_entity /
                # kg_checkpoint 两型），单据以 source 自带的为准。
                actual_type = source.get("source_type") or collector_type
                touched_types.add(actual_type)
                expected.add((actual_type, source["source_id"]))
                try:
                    if "text" in source:
                        text = source["text"]
                    else:
                        text = _extract_file_text(source["path"])
                    result = index_source(
                        session,
                        source_type=actual_type,
                        source_id=source["source_id"],
                        source_name=source["source_name"],
                        text=text,
                        kb_id=source.get("kb_id"),
                        project_id=source.get("project_id"),
                        bridge_doc=source.get("bridge_doc"),
                        extra_text=source.get("extra_text") or "",
                        force=force,
                    )
                    status = result["status"]
                    counter[status] = counter.get(status, 0) + 1
                    counter["chunks"] += result.get("chunks", 0) if status == "indexed" else 0
                except Exception as exc:  # noqa: BLE001
                    counter["failed"] += 1
                    report["errors"].append(f"{actual_type}:{source['source_id']} → {exc}")

            session.commit()
            bucket = report["by_type"].setdefault(collector_type, {
                "indexed": 0, "skipped": 0, "empty": 0, "failed": 0, "chunks": 0})
            for key in ("indexed", "skipped", "empty", "failed", "chunks"):
                bucket[key] += counter.get(key, 0)
                report[key] += counter.get(key, 0)

        # 回收孤儿行：本次采集范围内、已不在来源清单里的分块。
        # 典型场景是 KG 实体改了分型、或知识库删了文件 —— 不清掉这些行，检索会一直
        # 召回已经不存在的内容，且因 source_key 不同，index_source 的幂等删除拦不住。
        if touched_types:
            existing = session.execute(
                select(KbChunk.source_type, KbChunk.source_id).where(
                    KbChunk.source_type.in_(tuple(touched_types))
                )
            ).all()
            stale = {(t, s) for t, s in existing} - expected
            for stype, sid in stale:
                session.execute(
                    delete(KbChunk).where(
                        KbChunk.source_type == stype, KbChunk.source_id == sid
                    )
                )
            if stale:
                session.commit()
            report["pruned"] = len(stale)
    finally:
        session.close()

    # 只在确有变更时清缓存。后台守护会周期性地跑一次幂等刷新（稳态下
    # indexed=pruned=0），若无条件 invalidate，每次刷新都会让紧随其后的
    # 第一个检索请求白付一次「重载 600 块 + 重新分词」的开销。
    if report["indexed"] or report.get("pruned") or report["failed"]:
        invalidate()
    return report


def refresh(*, only: Iterable[str] | None = None, wait: bool = False) -> dict:
    """增量刷新索引 —— 后台守护与「写后触发」的统一入口。

    ``wait=False``（守护用）：拿不到重建锁就放弃，返回 ``status="busy"``。
    稳态下一次调用约 1.3 秒且不写库，可以放心周期性调用。

    ``wait=True``（写后触发用）：排队等锁，保证这次刷新一定执行到 —— 用户刚
    上传完文档就会去搜，不能因为恰好和守护撞上就让那份文档暂时检索不到。
    """
    if wait:
        _build_lock.acquire()
    elif not _build_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        report = build_all(force=False, only=only)
    finally:
        _build_lock.release()
    changed = bool(report.get("indexed") or report.get("pruned") or report.get("failed"))
    report["status"] = "changed" if changed else "clean"
    return report


# 后台增量刷新的首次延迟与周期（秒）。可用环境变量覆盖。
# 稳态一次刷新约 1.3 秒，300 秒周期下平均 CPU 占用可忽略。
REFRESH_INITIAL_DELAY_SECONDS = float(os.getenv("RAG_REFRESH_DELAY", "90"))
REFRESH_INTERVAL_SECONDS = float(os.getenv("RAG_REFRESH_INTERVAL", "300"))


async def incremental_worker() -> None:
    """后台守护：周期性地把新入库的文档/记录/文书/笔记增量编进索引。

    「系统在使用过程中积累的记录」是持续增长的。只靠人工跑一次
    ``scripts/build_rag_index.py`` 只能覆盖当时那一份快照 —— 此后新录入的
    工作记录、新生成的事故文书、新上传的知识库文档都检索不到。这里用低频
    轮询补齐：刷新完全幂等（``source_hash`` 判重），稳态下不写库、只花一次
    遍历的开销，因此可以常驻。
    """
    await asyncio.sleep(REFRESH_INITIAL_DELAY_SECONDS)
    first = True
    while True:
        try:
            report = await asyncio.to_thread(refresh)
            status = report.get("status")
            # 首次无论结果都记一条：这是运维确认「守护真的在跑」的唯一线索。
            # 稳态下没有变更、不打日志，看起来和「没启动」一模一样。
            if status == "changed" or first:
                logger.info(
                    "[RAG] 增量刷新（%s）：更新 %s 个来源 / 回收 %s 个孤儿 / 新增 %s 块",
                    status, report.get("indexed"), report.get("pruned"),
                    report.get("chunks"),
                )
            for err in (report.get("errors") or [])[:5]:
                logger.warning("[RAG] 增量刷新告警：%s", err)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[RAG] 增量刷新失败（下个周期重试）：%s", exc)
        first = False
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


def warmup() -> dict:
    """启动预热：加载嵌入模型 + 分词器 + 索引缓存，避免首个请求承担全部开销。"""
    result = {"embedding": embedding.warmup()}
    try:
        _cut("预热分词器")
        result["tokenizer"] = True
    except Exception as exc:  # noqa: BLE001
        result["tokenizer"] = f"failed: {exc}"
    try:
        result["chunks"] = len(_get_entries())
    except Exception as exc:  # noqa: BLE001
        result["chunks"] = f"failed: {exc}"
    return result
