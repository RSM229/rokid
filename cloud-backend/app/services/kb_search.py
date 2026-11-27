"""资料库混排搜索 —— 给智能体 @ 面板用。

设计前提：**数据量可能很大**（个人库 + 项目库，上万份文档是常态）。

所以这里刻意把「召回」和「精排」分开：

* 召回交给数据库：只按 ``kb_id`` 圈定范围，再用若干 token 的 ``LIKE`` 预过滤，
  每类各取一个有上限的候选池（``_FOLDER_POOL`` / ``_DOC_POOL``）。
  这是给未来换 FTS/向量召回留的接口位——只要替换 ``_prefilter`` 即可。
* 精排交给 Python：候选池是几十到几百行，逐条算分不打紧，换来的是
  「精确 > 前缀 > 包含 > 分词全命中 > 路径命中 > 子序列」这种可解释的排序。

搜索只返回「候选」，不展开正文；选中后的范围语义由 :func:`resolve_mentions`
翻译成检索白名单（``rag_index.search(paths=...)``）。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.knowledge_base import (
    KnowledgeBaseMember,
    KnowledgeDocument,
    KbFolder,
)

# 候选池上限：越大越稳，越小越快。召回阶段用 LIKE，行宽很窄，这个量级无压力。
_FOLDER_POOL = 400
_DOC_POOL = 600

# 分数档位（对外可解释，也方便测试断言顺序）
S_EXACT = 100
S_PREFIX = 95
S_CONTAINS = 90
S_TOKENS = 85
S_PATH_CONTAINS = 70
S_PATH_TOKENS = 65
S_SUBSEQ = 55

# 未删除过滤：软删除进回收站，is_deleted 为 NULL 的历史行视作未删除
_DOC_ALIVE = KnowledgeDocument.is_deleted.is_(None) | (
    KnowledgeDocument.is_deleted == False  # noqa: E712
)


def norm_path(value: object) -> str:
    """统一成 posix 风格、去掉前导 ./ 的规范化路径。

    索引里的 ``source_id`` 是相对 ``knowledge_base_path`` 的路径（Windows 上是反斜杠），
    文档表的 ``path`` 是绝对路径，两边要靠这个函数对齐。
    """
    text = str(value or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def rel_path(absolute: str) -> str:
    """把文档的绝对路径换算成索引里的相对路径口径。"""
    try:
        rel = os.path.relpath(absolute, str(settings.knowledge_base_path))
    except ValueError:
        rel = absolute
    return norm_path(rel)


def tokenize(query: str) -> list[str]:
    """查询词切分成小写 token：整串 + jieba 分词结果。

    整串保留是关键——用户打「钢筋验收」时往往就是文件名里连续的一段，
    拆成两个词反而会把精确命中降到 S_TOKENS 档。
    """
    raw = (query or "").strip()
    if not raw:
        return []
    tokens: list[str] = [raw.lower()]
    try:
        import jieba

        for piece in jieba.cut(raw, cut_all=False):
            piece = piece.strip().lower()
            if len(piece) >= 2 and piece not in tokens:
                tokens.append(piece)
    except Exception:  # noqa: BLE001 —— 分词不可用不应阻断搜索
        pass
    return tokens


def score_text(name: str, path: str, tokens: Sequence[str]) -> int:
    """给单个候选打分；0 表示不匹配（调用方直接丢弃）。"""
    if not tokens:
        return 0
    whole = tokens[0]
    lowered_name = (name or "").lower()
    lowered_path = (path or "").lower()
    if not lowered_name:
        return 0

    if lowered_name == whole:
        return S_EXACT
    if lowered_name.startswith(whole):
        return S_PREFIX
    if whole in lowered_name:
        return S_CONTAINS
    if len(tokens) > 1 and all(t in lowered_name for t in tokens[1:]):
        return S_TOKENS
    if whole in lowered_path:
        return S_PATH_CONTAINS
    if len(tokens) > 1 and all(t in lowered_path for t in tokens[1:]):
        return S_PATH_TOKENS
    if _is_subsequence(whole, lowered_name):
        return S_SUBSEQ
    return 0


def _is_subsequence(needle: str, haystack: str) -> bool:
    """needle 的字符是否按序出现在 haystack 里（允许中间夹杂）。

    「钢筋验收」→ 命中「钢_筋_绑扎_验收_记录」这类长名，成本 O(n)。
    """
    if not needle or len(needle) < 2:
        return False
    pos = 0
    for ch in haystack:
        if ch == needle[pos]:
            pos += 1
            if pos == len(needle):
                return True
    return False


def _prefilter(column, tokens: Sequence[str]) -> object:
    """召回条件：任一 token 命中即进候选池。

    LIKE 用 ``%token%`` 而不是前缀匹配——中文文件名很少以关键词开头，
    前缀匹配会漏掉绝大多数真实查询。代价是走不了索引，因此靠
    ``kb_id`` 圈定范围 + 候选池上限来兜住耗时。
    """
    clauses = [column.like(f"%{t}%") for t in tokens if t]
    return or_(*clauses) if clauses else None


async def accessible_kb_ids(db: AsyncSession, user_id: int) -> list[int]:
    """当前用户可访问的资料库（个人库 + 项目库，靠成员表判定）。"""
    rows = await db.execute(
        select(KnowledgeBaseMember.kb_id).where(
            KnowledgeBaseMember.user_id == int(user_id)
        )
    )
    return sorted({int(x) for x in rows.scalars().all()})


async def _folder_path_map(db: AsyncSession, kb_ids: Sequence[int]) -> dict[int, dict]:
    """一次取出所有文件夹，算出 id → {name, parent_id, kb_id, path}。

    路径用「自下而上爬 parent 链」得到，带 memo，深度通常不超过 5 层。
    """
    if not kb_ids:
        return {}
    rows = await db.execute(
        select(KbFolder.id, KbFolder.kb_id, KbFolder.parent_id, KbFolder.name).where(
            KbFolder.kb_id.in_(list(kb_ids))
        )
    )
    nodes: dict[int, dict] = {}
    for folder_id, kb_id, parent_id, name in rows.all():
        nodes[int(folder_id)] = {
            "id": int(folder_id),
            "kb_id": int(kb_id),
            "parent_id": int(parent_id) if parent_id is not None else None,
            "name": name or "",
            "path": "",
        }

    def build(node: dict, guard: int = 0) -> str:
        if node["path"] or guard > 32:
            return node["path"]
        parent = nodes.get(node["parent_id"])
        if parent is None:
            node["path"] = node["name"]
        else:
            node["path"] = f"{build(parent, guard + 1)}/{node['name']}"
        return node["path"]

    for node in nodes.values():
        build(node)
    return nodes


# ---------------------------------------------------------------- 个性化

# 加分上限的设计意图：个性化只做「微调排序」，不能压过文本相关性。
# 精确命中 100 分，最多 +15 也就到 115，不会让一个「用户常点但名字不相关」的
# 文件夹盖过真正精确命中的结果——这是避免「越用越窄」的关键约束。
BOOST_HITS_CAP = 15
BOOST_HITS_PER_USE = 2
BOOST_RECENT = 8          # 7 天内用过
BOOST_PROJECT = 5         # 与当前项目同库
RECENT_WINDOW_DAYS = 7


async def _stat_map(
    db: AsyncSession, user_id: int, kb_ids: Sequence[int]
) -> dict[tuple[str, int], dict]:
    """取当前用户在给定资料库里的选择埋点：(target_type, target_id) → {hits, last}。"""
    if not kb_ids:
        return {}
    from app.models.knowledge_base import KbMentionStat

    rows = await db.execute(
        select(
            KbMentionStat.target_type,
            KbMentionStat.target_id,
            KbMentionStat.hits,
            KbMentionStat.last_used_at,
        ).where(
            KbMentionStat.user_id == int(user_id),
            KbMentionStat.kb_id.in_(list(kb_ids)),
        )
    )
    return {
        (str(target_type), int(target_id)): {
            "hits": int(hits or 0),
            "last": last_used_at,
        }
        for target_type, target_id, hits, last_used_at in rows.all()
    }


def _personal_boost(
    stats: dict[tuple[str, int], dict],
    target_type: str,
    target_id: int,
    *,
    now: datetime | None = None,
) -> int:
    """个性化加分：用得越多、越近用过的越靠前（有上限，不改变相关性主导地位）。"""
    stat = stats.get((target_type, int(target_id)))
    if not stat:
        return 0
    boost = min(BOOST_HITS_CAP, stat["hits"] * BOOST_HITS_PER_USE)
    last = stat.get("last")
    if last is not None:
        try:
            age = (now or datetime.now()) - last
            if age.days < RECENT_WINDOW_DAYS:
                boost += BOOST_RECENT
        except TypeError:
            # 时区感知与朴素 datetime 混用时不因加分逻辑炸掉搜索
            pass
    return boost


async def record_mentions(
    db: AsyncSession, user_id: int, items: Iterable[dict]
) -> int:
    """批量记录「用户把哪些引用真的用进了对话」。

    幂等：同一 (user, kb, type, id) 只累加 hits 并刷新 last_used_at。
    逐条 upsert（先查后改）而不是依赖方言级 ON DUPLICATE KEY，
    是为了让这段逻辑在 MySQL/SQLite 下都能跑（测试用假 session 也能覆盖）。
    """
    from app.models.knowledge_base import KbMentionStat

    allowed = set(await accessible_kb_ids(db, user_id))
    now = datetime.now()
    written = 0
    for raw in items or []:
        # 路由层传的是 Pydantic 模型，直接调用方可能传 dict——两种都收，
        # 否则 isinstance(dict) 会把所有上报静默丢掉（线上踩过）。
        if not isinstance(raw, dict):
            raw = getattr(raw, "model_dump", lambda: None)() or {}
        if not isinstance(raw, dict):
            continue
        try:
            kb_id = int(raw.get("kb_id"))
            target_id = int(raw.get("target_id"))
        except (TypeError, ValueError):
            continue
        target_type = str(raw.get("target_type") or "")
        if target_type not in {"folder", "doc"} or kb_id not in allowed:
            continue
        existing = (
            await db.execute(
                select(KbMentionStat).where(
                    KbMentionStat.user_id == int(user_id),
                    KbMentionStat.kb_id == kb_id,
                    KbMentionStat.target_type == target_type,
                    KbMentionStat.target_id == target_id,
                )
            )
        ).scalars().first()
        if existing is None:
            db.add(KbMentionStat(
                user_id=int(user_id), kb_id=kb_id, target_type=target_type,
                target_id=target_id, hits=1, last_used_at=now,
            ))
        else:
            existing.hits = int(existing.hits or 0) + 1
            existing.last_used_at = now
        written += 1
    if written:
        await db.commit()
    return written


async def _kb_project_map(db: AsyncSession, kb_ids: Sequence[int]) -> dict[int, Optional[int]]:
    """资料库 id → 绑定的项目 id（个人库/公共库为 None）。"""
    if not kb_ids:
        return {}
    from app.models.knowledge_base import KnowledgeBase

    rows = await db.execute(
        select(KnowledgeBase.id, KnowledgeBase.project_id).where(
            KnowledgeBase.id.in_(list(kb_ids))
        )
    )
    return {
        int(kb_id): (int(project_id) if project_id is not None else None)
        for kb_id, project_id in rows.all()
    }


def _project_boost(
    project_map: dict[int, Optional[int]],
    kb_id: int,
    project_id: Optional[int],
) -> int:
    """同项目加分：当前项目的资料库略微靠前。

    为什么用「同库优先」而不是给项目库单独排序：用户在同一项目里连续作业时，
    项目库里的资料才是他此刻真正要引的；但个人库里也可能有更好的命中，
    所以只加 5 分——足以在同分段内拉开次序，不足以翻越相关性档位。
    """
    if project_id is None:
        return 0
    if project_map.get(int(kb_id)) != int(project_id):
        return 0
    return BOOST_PROJECT


async def _kb_name_map(db: AsyncSession, kb_ids: Sequence[int]) -> dict[int, str]:
    """资料库 id → 名称。

    为什么要它：个人库和项目库各自有一份同名的预设文件夹树（「工作记录与表单/验收记录」
    在几个库里都叫这个名字），只按路径展示会出现多条一模一样的行，用户会以为重复。
    带上库名后前端可以在多库场景下区分「这是哪个库里的」。
    """
    if not kb_ids:
        return {}
    from app.models.knowledge_base import KnowledgeBase

    rows = await db.execute(
        select(KnowledgeBase.id, KnowledgeBase.name).where(
            KnowledgeBase.id.in_(list(kb_ids))
        )
    )
    return {int(kb_id): (name or "") for kb_id, name in rows.all()}


async def _folder_doc_counts(db: AsyncSession, kb_ids: Sequence[int]) -> dict[int, int]:
    """每个文件夹直属的文档数（子树计数在内存里往上累加，避免递归 SQL）。"""
    if not kb_ids:
        return {}
    rows = await db.execute(
        select(KnowledgeDocument.folder_id, func.count(KnowledgeDocument.id))
        .where(KnowledgeDocument.kb_id.in_(list(kb_ids)), _DOC_ALIVE)
        .group_by(KnowledgeDocument.folder_id)
    )
    return {
        int(folder_id): int(count)
        for folder_id, count in rows.all()
        if folder_id is not None
    }


def _subtree_counts(nodes: dict[int, dict], direct: dict[int, int]) -> dict[int, int]:
    """把「直属数」沿 parent 链上卷，得到每个文件夹的整棵子树文档数。"""
    totals = dict(direct)
    for folder_id, node in nodes.items():
        count = direct.get(folder_id, 0)
        if not count:
            continue
        parent_id = node["parent_id"]
        guard = 0
        while parent_id is not None and guard < 32:
            totals[parent_id] = totals.get(parent_id, 0) + count
            parent_id = nodes.get(parent_id, {}).get("parent_id")
            guard += 1
    return totals


async def search(
    db: AsyncSession,
    user_id: int,
    query: str,
    *,
    kb_ids: Optional[Sequence[int]] = None,
    limit: int = 20,
    offset: int = 0,
    include_documents: bool = True,
    project_id: Optional[int] = None,
) -> dict:
    """混排搜索：文件夹与文档在同一个列表里按分数排序，越靠前越可信。

    返回结构固定为 ``{query, tokens, kb_ids, folders, documents, has_more, ...}``，
    前端据此渲染「📁 路径」和「📄 文件名 · 路径」两种行。
    """
    raw_query = (query or "").strip()
    allowed = await accessible_kb_ids(db, user_id)
    if kb_ids:
        wanted = {int(x) for x in kb_ids}
        allowed = [kb_id for kb_id in allowed if kb_id in wanted]

    empty = {
        "query": raw_query,
        "tokens": [],
        "kb_ids": allowed,
        "folders": [],
        "documents": [],
        "has_more": False,
        "total_folders": 0,
        "total_documents": 0,
    }
    if not raw_query or not allowed:
        return empty

    tokens = tokenize(raw_query)
    if not tokens:
        return empty

    nodes = await _folder_path_map(db, allowed)
    direct_counts = await _folder_doc_counts(db, allowed)
    subtree_counts = _subtree_counts(nodes, direct_counts)
    kb_names = await _kb_name_map(db, allowed)
    stats = await _stat_map(db, user_id, allowed)
    project_map = await _kb_project_map(db, allowed) if project_id is not None else {}

    # ---- 文件夹召回与打分 -------------------------------------------------
    folder_where = _prefilter(KbFolder.name, tokens)
    folder_rows = []
    if folder_where is not None:
        folder_rows = (
            await db.execute(
                select(KbFolder.id, KbFolder.kb_id, KbFolder.name)
                .where(KbFolder.kb_id.in_(allowed), folder_where)
                .limit(_FOLDER_POOL)
            )
        ).all()

    folders: list[dict] = []
    for folder_id, kb_id, name in folder_rows:
        node = nodes.get(int(folder_id))
        if node is None:
            continue
        folder_score = score_text(name or "", node["path"], tokens)
        if folder_score <= 0:
            continue
        folders.append(
            {
                "id": int(folder_id),
                "kb_id": int(kb_id),
                "kb_name": kb_names.get(int(kb_id), ""),
                "name": name or "",
                "path": node["path"],
                "count": int(subtree_counts.get(int(folder_id), 0)),
                # 个性化加分 + 同项目加分，都只叠加在文本分之上（各有上限），相关性仍是主导
                "score": folder_score + _personal_boost(stats, "folder", int(folder_id))
                         + _project_boost(project_map, int(kb_id), project_id),
            }
        )
    folders.sort(key=lambda item: (-item["score"], item["path"]))

    # ---- 文档召回与打分 ---------------------------------------------------
    documents: list[dict] = []
    if include_documents:
        doc_where = _prefilter(KnowledgeDocument.name, tokens)
        if doc_where is not None:
            doc_rows = (
                await db.execute(
                    select(
                        KnowledgeDocument.id,
                        KnowledgeDocument.kb_id,
                        KnowledgeDocument.name,
                        KnowledgeDocument.folder_id,
                        KnowledgeDocument.size_bytes,
                        KnowledgeDocument.created_at,
                    )
                    .where(
                        KnowledgeDocument.kb_id.in_(allowed),
                        _DOC_ALIVE,
                        doc_where,
                    )
                    .limit(_DOC_POOL)
                )
            ).all()
            for doc_id, kb_id, name, folder_id, size_bytes, created_at in doc_rows:
                folder_node = nodes.get(int(folder_id)) if folder_id is not None else None
                folder_path = folder_node["path"] if folder_node else ""
                doc_score = score_text(
                    name or "", f"{folder_path}/{name or ''}", tokens
                )
                if doc_score <= 0:
                    continue
                documents.append(
                    {
                        "id": int(doc_id),
                        "kb_id": int(kb_id),
                        "kb_name": kb_names.get(int(kb_id), ""),
                        "name": name or "",
                        "folder_id": int(folder_id) if folder_id is not None else None,
                        "folder_path": folder_path,
                        "size_bytes": int(size_bytes or 0),
                        "created_at": created_at.isoformat() if created_at else None,
                        "score": doc_score + _personal_boost(stats, "doc", int(doc_id))
                                 + _project_boost(project_map, int(kb_id), project_id),
                    }
                )
            documents.sort(key=lambda item: (-item["score"], item["name"]))

    total_folders = len(folders)
    total_documents = len(documents)
    # 分页口径：文件夹在前、文档在后拼成一条流，offset/limit 作用于拼接后的结果，
    # 这样前端「加载更多」不会出现两类结果各自翻页导致的重复。
    merged_total = total_folders + total_documents
    start = max(0, offset)
    end = start + max(1, limit)
    folders_page = folders[start:end]
    docs_page = documents[max(0, start - total_folders):max(0, end - total_folders)]
    return {
        "query": raw_query,
        "tokens": tokens,
        "kb_ids": allowed,
        "folders": folders_page,
        "documents": docs_page,
        "has_more": end < merged_total,
        "total_folders": total_folders,
        "total_documents": total_documents,
    }


async def browse(
    db: AsyncSession,
    user_id: int,
    *,
    kb_ids: Optional[Sequence[int]] = None,
    limit: int = 12,
) -> dict:
    """空查询时的「有东西可看」：最近用过 + 候选文件夹。

    为什么需要它：用户打出 @ 时脑子里还没有关键词——给一个空列表等于让人
    猜该输入什么。专业做法（命令面板类控件）都是先给出有意义的默认项，
    再让用户缩小范围。

    两段的取法：
    * ``recent``  最近用过的（last_used_at desc），最多 5 条——「我刚才用的」，
      前端渲染成一行横向快捷条，不占纵向空间
    * ``folders`` 兜底给有文档的文件夹（按整棵子树文档数 desc）+ 预设根目录，
      保证新用户（埋点为空）也不会看到空面板；**已出现在 recent 的会被剔除**

    为什么去掉「最常用」一段：数据量小时它与 recent 高度重叠，同一个文件夹
    在两段里各出现一次并各带一个勾，会让用户误以为选了很多条。
    """
    from app.models.knowledge_base import KbMentionStat

    allowed = await accessible_kb_ids(db, user_id)
    if kb_ids:
        wanted = {int(x) for x in kb_ids}
        allowed = [kb_id for kb_id in allowed if kb_id in wanted]

    result = {"mode": "browse", "kb_ids": allowed, "recent": [], "folders": []}
    if not allowed:
        return result

    nodes = await _folder_path_map(db, allowed)
    direct_counts = await _folder_doc_counts(db, allowed)
    subtree_counts = _subtree_counts(nodes, direct_counts)
    kb_names = await _kb_name_map(db, allowed)

    # 文档行（只在埋点里出现时才查，避免全库扫描）
    doc_stats = (
        await db.execute(
            select(
                KbMentionStat.target_id,
                KbMentionStat.kb_id,
                KbMentionStat.hits,
                KbMentionStat.last_used_at,
            ).where(
                KbMentionStat.user_id == int(user_id),
                KbMentionStat.kb_id.in_(allowed),
                KbMentionStat.target_type == "doc",
            )
        )
    ).all()
    doc_ids = {int(row[0]) for row in doc_stats}
    doc_rows: dict[int, dict] = {}
    if doc_ids:
        rows = await db.execute(
            select(
                KnowledgeDocument.id,
                KnowledgeDocument.kb_id,
                KnowledgeDocument.name,
                KnowledgeDocument.folder_id,
            ).where(KnowledgeDocument.id.in_(doc_ids), _DOC_ALIVE)
        )
        for doc_id, kb_id, name, folder_id in rows.all():
            folder_node = nodes.get(int(folder_id)) if folder_id is not None else None
            doc_rows[int(doc_id)] = {
                "kind": "doc",
                "id": int(doc_id),
                "kb_id": int(kb_id),
                "kb_name": kb_names.get(int(kb_id), ""),
                "name": name or "",
                "path": folder_node["path"] if folder_node else "",
                "score": 0,
            }

    folder_stats = (
        await db.execute(
            select(
                KbMentionStat.target_id,
                KbMentionStat.kb_id,
                KbMentionStat.hits,
                KbMentionStat.last_used_at,
            ).where(
                KbMentionStat.user_id == int(user_id),
                KbMentionStat.kb_id.in_(allowed),
                KbMentionStat.target_type == "folder",
            )
        )
    ).all()

    def folder_item(folder_id: int, kb_id: int) -> Optional[dict]:
        node = nodes.get(int(folder_id))
        if node is None:
            return None
        return {
            "kind": "folder",
            "id": int(folder_id),
            "kb_id": int(kb_id),
            "kb_name": kb_names.get(int(kb_id), ""),
            "name": node["name"],
            "path": node["path"],
            "count": int(subtree_counts.get(int(folder_id), 0)),
            "score": 0,
        }

    # recent：文件夹与文档混排，按最近使用时间倒序
    recent_rows: list[tuple[datetime | None, dict]] = []
    for folder_id, kb_id, _hits, last in folder_stats:
        item = folder_item(int(folder_id), int(kb_id))
        if item:
            recent_rows.append((last, item))
    for target_id, kb_id, _hits, last in doc_stats:
        item = doc_rows.get(int(target_id))
        if item:
            recent_rows.append((last, item))
    recent_rows.sort(key=lambda pair: pair[0] or datetime.min, reverse=True)
    recent_items = [item for _last, item in recent_rows[:5]]
    result["recent"] = recent_items

    # folders：兜底——有文档的文件夹按数量倒序，再补预设根目录。
    # 关键：已经出现在 recent 里的条目在这里**必须剔除**，否则同一个文件夹会在
    # 「最近」和「候选」里各出现一次，用户看到多个勾会以为选了很多东西。
    seen_keys = {_item_key(item) for item in recent_items}
    ordered = sorted(
        nodes.values(),
        key=lambda n: (-int(subtree_counts.get(n["id"], 0)), n["path"]),
    )
    picked: list[dict] = []
    for node in ordered:
        if len(picked) >= limit:
            break
        if node["parent_id"] is None:      # 根目录最后补，先给"有内容"的
            continue
        if not subtree_counts.get(node["id"]):
            continue
        item = folder_item(node["id"], node["kb_id"])
        if item is None:
            continue
        key = _item_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        picked.append(item)
    for node in ordered:
        if len(picked) >= limit:
            break
        if node["parent_id"] is not None:
            continue
        item = folder_item(node["id"], node["kb_id"])
        if item is None:
            continue
        key = _item_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        picked.append(item)
    result["folders"] = picked
    return result


def _item_key(item: dict) -> tuple[str, int]:
    """browse 结果项的唯一键：类型 + id。

    不用「路径」做键：个人库与项目库各有一份同名预设树（路径完全一样），
    按路径去重会把两个不同库里的文件夹误合并成一个。
    """
    return (str(item.get("kind") or ""), int(item.get("id") or 0))


# ---------------------------------------------------------------- 引用解析

async def _folder_subtree_ids(
    db: AsyncSession, kb_id: int, folder_id: int
) -> list[int]:
    """文件夹整棵子树的 id 列表（含自身）。"""
    rows = await db.execute(
        select(KbFolder.id, KbFolder.parent_id).where(KbFolder.kb_id == int(kb_id))
    )
    children: dict[Optional[int], list[int]] = {}
    for node_id, parent_id in rows.all():
        children.setdefault(int(parent_id) if parent_id is not None else None, []).append(
            int(node_id)
        )
    found: list[int] = []
    stack = [int(folder_id)]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        found.append(current)
        stack.extend(children.get(current, []))
    return found


async def resolve_mentions(
    db: AsyncSession, user_id: int, mentions: Iterable[dict]
) -> tuple[set[str], list[str]]:
    """把前端传来的 mentions 翻译成检索白名单与补充查询词。

    返回 ``(允许的文档相对路径集合, 额外查询词列表)``：

    * ``kb_folder`` → 该文件夹整棵子树下所有文档
    * ``kb_doc``    → 单个文档
    * ``kb_query``  → 不进白名单，只把词追加到检索查询里

    白名单为空 ⇒ 不限制范围（沿用原来的全局检索），避免「选了东西反而搜不到」。
    """
    allowed = set(await accessible_kb_ids(db, user_id))
    paths: set[str] = set()
    extra_queries: list[str] = []
    folder_requests: list[tuple[int, int]] = []
    doc_ids: list[int] = []

    for raw in mentions or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "").strip()
        try:
            kb_id = int(raw.get("kb_id")) if raw.get("kb_id") is not None else None
        except (TypeError, ValueError):
            kb_id = None
        if kind == "kb_query":
            text = str(raw.get("query") or "").strip()
            if text:
                extra_queries.append(text)
            continue
        if kb_id is None or kb_id not in allowed:
            continue
        if kind == "kb_folder":
            try:
                folder_id = int(raw.get("folder_id"))
            except (TypeError, ValueError):
                continue
            folder_requests.append((kb_id, folder_id))
        elif kind == "kb_doc":
            try:
                doc_ids.append(int(raw.get("doc_id")))
            except (TypeError, ValueError):
                continue

    # 文件夹 → 子树 → 文档路径
    for kb_id, folder_id in folder_requests:
        subtree = await _folder_subtree_ids(db, kb_id, folder_id)
        if not subtree:
            continue
        rows = await db.execute(
            select(KnowledgeDocument.path).where(
                KnowledgeDocument.kb_id == kb_id,
                _DOC_ALIVE,
                KnowledgeDocument.folder_id.in_(subtree),
            )
        )
        for (doc_path,) in rows.all():
            if doc_path:
                paths.add(rel_path(str(doc_path)))

    # 单文件 → 路径
    if doc_ids:
        rows = await db.execute(
            select(KnowledgeDocument.path, KnowledgeDocument.kb_id).where(
                KnowledgeDocument.id.in_(doc_ids), _DOC_ALIVE
            )
        )
        for doc_path, kb_id in rows.all():
            if doc_path and int(kb_id) in allowed:
                paths.add(rel_path(str(doc_path)))

    return paths, extra_queries
