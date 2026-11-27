"""个人/共享知识库模型 — 多知识库 + 成员权限 + 文档。

对标 ima 的「个人知识库 / 共享知识库」：
- 一个用户可以创建多个知识库；
- 共享知识库通过成员角色（owner/admin/editor/viewer）协作；
- 文档归属到具体知识库，问答与检索按库隔离。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update
from sqlalchemy.sql import func

from app.db import Base

# 知识库成员角色（权限由高到低）
KB_ROLES = ("owner", "admin", "editor", "viewer")
KB_ROLE_LEVEL = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # personal=个人知识库；shared=共享知识库
    kb_type: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    # 共享知识库的加入邀请码（可轮换）
    invite_code: Mapped[Optional[str]] = mapped_column(
        String(32), unique=True, nullable=True, index=True
    )
    # 系统内置知识库（如“规范知识库”），所有用户可见，文档由系统维护
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), index=True
    )
    # 是否发布到知识库广场（公开可发现、可订阅加入）
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeBaseMember(Base):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (UniqueConstraint("kb_id", "user_id", name="uq_kb_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # 文档归属的文件夹（分类）；NULL 表示未分类（根目录）。新增可空列，
    # 由 init_db 的 _reconcile_missing_columns 安全补齐，不破坏历史数据。
    folder_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("kb_folders.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(256))
    filename: Mapped[str] = mapped_column(String(256))
    path: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # System-generated project archive copy. One business document keeps one
    # stable knowledge-document row and is overwritten on regeneration.
    source_document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id"), nullable=True, unique=True, index=True
    )
    source_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Stable human-readable trace that survives business-document deletion.
    source_document_ref: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 软删除：删除文档先标记 is_deleted，进回收站可恢复；彻底删除才移除磁盘文件。
    # 两列由 init_db 的 _reconcile_missing_columns 安全补齐，不破坏历史数据。
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), index=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class KbFolder(Base):
    """资料库内的文件夹（分类）树 —— 对标 D:\\rokid\\预设目录 的文档分类标准。

    每个资料库拥有独立的一棵文件夹树；``parent_id`` 自引用形成层级，
    ``is_preset`` 标记来自预设模板的节点（用户可在此基础上增删自定义分类）。
    """

    __tablename__ = "kb_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("kb_folders.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    # 成员文件夹归属：项目资料库里每个在岗成员一个根文件夹（2026-11-27）。
    # 生成的文书只存一份，落在创建人的成员文件夹下；「我的资料」只是定位到
    # 这个文件夹，不复制数据。普通分类文件夹的 owner_id 为 NULL。
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    is_preset: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0")
    )
    # 置顶：同级内置顶文件夹排最前，多个置顶按 pinned_at 倒序（后置顶的更靠前）。
    # 两列由 init_db 的 _reconcile_missing_columns 安全补齐，不破坏历史数据。
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0")
    )
    pinned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KbTag(Base):
    """资料库内的标签 —— 跨文件夹的轻量分类维度（对标 ima 的 #标签）。

    一个文档可打多个标签；同库内标签名唯一（重复新建走幂等返回）。
    """

    __tablename__ = "kb_tags"
    __table_args__ = (UniqueConstraint("kb_id", "name", name="uq_kb_tag_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KbDocumentTag(Base):
    """文档与标签的多对多关联（全量替换式设置）。"""

    __tablename__ = "kb_document_tags"
    __table_args__ = (UniqueConstraint("document_id", "tag_id", name="uq_doc_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id"), index=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("kb_tags.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KbMentionStat(Base):
    """@ 面板的选择埋点 —— 个性化排序的原料。

    只记录「用户真的把这份资料用进了对话」（发送时批量上报），
    不是「他在面板里点过又删了」。所以 hits 反映真实使用频次。

    不按项目分区：同一用户在多个项目间的资料习惯相通；
    项目相关性在检索时用 context_boost 临时加权，不污染长期统计。
    """

    __tablename__ = "kb_mention_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "kb_id", "target_type", "target_id",
                         name="uq_kb_mention_stat"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    # folder=文件夹（整棵子树）、doc=单份文档
    target_type: Mapped[str] = mapped_column(String(16), index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    hits: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# 预设目录分类树（与 D:\\rokid\\预设目录 对应）。
#
# 2026-11-27 重构：原来所有库（个人库 / 项目档案库）都播种同一棵树，根下固定
# 「个人文件夹 + 项目文件夹」两层包壳。小程序补上「项目资料 / 我的资料」切换后，
# 这层包壳就变成了重复结构（切换一次 + 库里再分一次），且个人库里出现「项目文件夹」、
# 项目库里出现「个人文件夹」，语义也错。
#
# 现在按库类型给不同骨架，包壳取消：
#   个人库  → 我的文件 / 工作记录与表单（11 个归档子目录）
#   共享库  → 会议记录 / 合同与资质 / 工作记录与表单 / 往来函件与通报 /
#             技术资料 / 报批报建 / 支付管理 / 汇报材料 / 计划管理 /
#             设计文件 / 造价文件
# 历史库由 _flatten_scope_wrappers() 就地拆壳（有内容的子目录提升到根，空的删）。
_WORK_RECORD_FOLDER = {
    "name": "工作记录与表单",
    "children": [
        {"name": "各类台账"},
        {"name": "巡视记录"},
        {"name": "旁站记录"},
        {"name": "材料进场记录"},
        {"name": "检监测记录"},
        {"name": "监理日志"},
        {"name": "监理日记"},
        {"name": "监理月报"},
        {"name": "监理通知单"},
        {"name": "见证记录"},
        {"name": "验收记录"},
    ],
}

PRESET_FOLDER_TREE_PERSONAL: list[dict] = [
    {"name": "我的文件"},
    _WORK_RECORD_FOLDER,
]

# 项目库里每个在岗成员一个根文件夹（owner_id 指向 users.id）。
# 生成的文书只存一份，落在创建人的成员文件夹下：
#   项目资料 / 张三 / 工作记录与表单 / 巡视记录
# 「我的资料」= 直接定位到「张三」这个文件夹，同一份数据，不复制。
PRESET_FOLDER_TREE_MEMBER: list[dict] = [
    _WORK_RECORD_FOLDER,
    {"name": "我的文件"},
]

# 项目库根下的公共分类（不含工作记录与表单——那份在成员文件夹里）
PRESET_FOLDER_TREE_PROJECT: list[dict] = [
    {"name": "会议记录"},
    {
        "name": "合同与资质",
        "children": [
            {"name": "施工单位"},
            {"name": "监理单位"},
            {"name": "设计单位"},
        ],
    },
    {"name": "往来函件与通报"},
    {
        "name": "技术资料",
        "children": [
            {"name": "施工组织设计与方案"},
            {"name": "管理规划与细则"},
        ],
    },
    {"name": "报批报建"},
    {"name": "支付管理"},
    {"name": "汇报材料"},
    {"name": "计划管理"},
    {
        "name": "设计文件",
        "children": [
            {"name": "初步设计"},
            {"name": "方案图"},
            {"name": "施工图"},
            {"name": "竣工图"},
        ],
    },
    {
        "name": "造价文件",
        "children": [
            {"name": "估算"},
            {"name": "概算"},
            {"name": "结算"},
            {"name": "预算"},
        ],
    },
]


def preset_folder_tree(kb_type: Optional[str]) -> list[dict]:
    """按库类型选骨架：个人库只留「我的文件 + 工作记录与表单」。"""
    return PRESET_FOLDER_TREE_PERSONAL if kb_type == "personal" else PRESET_FOLDER_TREE_PROJECT


# 历史库根下的两层包壳：中文名即旧版 PRESET_FOLDER_TREE 的两个根节点。
_LEGACY_PERSONAL_WRAPPER = "个人文件夹"
_LEGACY_PROJECT_WRAPPER = "项目文件夹"
_PERSONAL_ROOT_BUCKET = "我的文件"
_WORK_RECORD_FORM_NAME = "工作记录与表单"


def _subtree_ids(rows: list["KbFolder"], root_id: int) -> set[int]:
    children: dict[Optional[int], list[int]] = {}
    for row in rows:
        children.setdefault(row.parent_id, []).append(row.id)
    seen: set[int] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, []))
    return seen


async def _subtree_doc_count(db: AsyncSession, kb_id: int, rows: list["KbFolder"], folder_id: int) -> int:
    ids = _subtree_ids(rows, folder_id)
    if not ids:
        return 0
    return int(
        await db.scalar(
            select(func.count(KnowledgeDocument.id)).where(
                KnowledgeDocument.kb_id == kb_id,
                KnowledgeDocument.folder_id.in_(ids),
            )
        )
        or 0
    )


def _subtree_has_custom(rows: list["KbFolder"], folder_id: int) -> bool:
    """子树里是否有用户自建目录（非预设）。有就不能当「空目录」删掉。"""
    ids = _subtree_ids(rows, folder_id)
    return any(row.id in ids and not row.is_preset for row in rows)


async def _merge_folder(
    db: AsyncSession, rows: list["KbFolder"], source: "KbFolder", target: "KbFolder"
) -> None:
    """把 source 并入 target：同名子目录递归合并，文档改挂 target，最后删 source。"""
    for child in [row for row in rows if row.parent_id == source.id]:
        twin = next(
            (row for row in rows if row.parent_id == target.id and row.name == child.name),
            None,
        )
        if twin is not None and twin.id != child.id:
            await _merge_folder(db, rows, child, twin)
        else:
            child.parent_id = target.id
    await db.execute(
        update(KnowledgeDocument)
        .where(
            KnowledgeDocument.kb_id == source.kb_id,
            KnowledgeDocument.folder_id == source.id,
        )
        .values(folder_id=target.id)
    )
    rows[:] = [row for row in rows if row.id != source.id]
    await db.delete(source)


async def _flatten_scope_wrappers(
    db: AsyncSession, kb_id: int, kb_type: Optional[str]
) -> None:
    """拆掉历史库的「个人文件夹 / 项目文件夹」包壳（幂等）。

    旧版每个库都播种同一棵树，两个包壳把所有真实目录压了一层；小程序补上
    「项目资料 / 我的资料」切换后，这层包壳就是重复结构。这里就地迁移：

    * 项目文件夹 → 子目录提升到根（根上同名目录合并），文档回落到「未分类」；
    * 个人文件夹 → 个人库并入「我的文件」；共享库提升子目录后删除。
    * 个人库里只有「工作记录与表单」保留，其余空的预设目录直接删掉，
      避免个人库被 11 个项目分类撑满。
    """
    rows = (
        await db.execute(select(KbFolder).where(KbFolder.kb_id == kb_id))
    ).scalars().all()
    if not rows:
        return
    roots = [row for row in rows if row.parent_id is None]

    async def drop_empty_subtree(folder: "KbFolder", source: Optional[list["KbFolder"]] = None) -> None:
        source = rows if source is None else source
        ids = _subtree_ids(source, folder.id)
        if not ids:
            return
        # kb_folders.parent_id 是自引用外键：ORM 逐条 delete 时无法保证「先子后父」，
        # MySQL 会报 1451（实测 2026-11-27 迁移监理日记时命中）。先批量断开父引用，
        # 再整片删除，两条语句都是集合操作，顺序确定。
        await db.execute(
            update(KbFolder).where(KbFolder.id.in_(ids)).values(parent_id=None)
        )
        await db.execute(delete(KbFolder).where(KbFolder.id.in_(ids)))
        source[:] = [item for item in source if item.id not in ids]

    async def flatten_wrapper(wrapper: "KbFolder", keep_work_record_only: bool) -> None:
        for child in [row for row in rows if row.parent_id == wrapper.id]:
            if keep_work_record_only and child.name != _WORK_RECORD_FORM_NAME:
                if (
                    not _subtree_has_custom(rows, child.id)
                    and await _subtree_doc_count(db, kb_id, rows, child.id) == 0
                ):
                    await drop_empty_subtree(child)
                    continue
            twin = next(
                (row for row in rows if row.parent_id is None and row.name == child.name),
                None,
            )
            if twin is not None and twin.id != child.id:
                await _merge_folder(db, rows, child, twin)
            else:
                child.parent_id = None
        await db.execute(
            update(KnowledgeDocument)
            .where(
                KnowledgeDocument.kb_id == kb_id,
                KnowledgeDocument.folder_id == wrapper.id,
            )
            .values(folder_id=None)
        )
        rows[:] = [row for row in rows if row.id != wrapper.id]
        await db.delete(wrapper)

    for wrapper in roots:
        if wrapper.name == _LEGACY_PROJECT_WRAPPER:
            await flatten_wrapper(wrapper, keep_work_record_only=(kb_type == "personal"))
        elif wrapper.name == _LEGACY_PERSONAL_WRAPPER:
            if kb_type == "personal":
                bucket = next(
                    (row for row in rows if row.parent_id is None and row.name == _PERSONAL_ROOT_BUCKET),
                    None,
                )
                if bucket is not None:
                    await _merge_folder(db, rows, wrapper, bucket)
                else:
                    wrapper.name = _PERSONAL_ROOT_BUCKET
            else:
                await flatten_wrapper(wrapper, keep_work_record_only=False)

    # 共享库根上的「工作记录与表单」：新结构把它移进成员文件夹了。
    # 老库里这层如果是空的（无文档、无自建子目录）就清掉，避免看起来像公共归档区。
    if kb_type != "personal":
        latest = (
            await db.execute(select(KbFolder).where(KbFolder.kb_id == kb_id))
        ).scalars().all()
        for row in [item for item in latest if item.parent_id is None]:
            if row.name != _WORK_RECORD_FORM_NAME:
                continue
            if _subtree_has_custom(latest, row.id):
                continue
            if await _subtree_doc_count(db, kb_id, latest, row.id) == 0:
                await drop_empty_subtree(row, latest)



async def _build_folder_nodes(
    db: AsyncSession, kb_id: int, nodes: list[dict], parent_id: Optional[int]
) -> list[KbFolder]:
    """递归补齐 nodes 这棵子树（按 (parent_id, name) 复用已有节点）。"""
    existing_rows = (
        await db.execute(select(KbFolder).where(KbFolder.kb_id == kb_id))
    ).scalars().all()
    by_parent_name: dict[tuple[Optional[int], str], KbFolder] = {}
    for row in existing_rows:
        by_parent_name.setdefault((row.parent_id, row.name), row)
    order = max((row.sort_order or 0 for row in existing_rows), default=-1) + 1
    created: list[KbFolder] = []

    async def build(items: list[dict], parent: Optional[int]) -> None:
        nonlocal order
        for node in items:
            folder = by_parent_name.get((parent, node["name"]))
            if folder is None:
                folder = KbFolder(
                    kb_id=kb_id,
                    parent_id=parent,
                    name=node["name"],
                    sort_order=order,
                    is_preset=True,
                )
                db.add(folder)
                await db.flush()
                by_parent_name[(parent, node["name"])] = folder
                created.append(folder)
                order += 1
            if node.get("children"):
                await build(node["children"], folder.id)

    await build(nodes, parent_id)
    return created


async def ensure_kb_folder_tree(
    db: AsyncSession, kb_id: int, *, commit: bool = True
) -> None:
    """Ensure every preset folder node exists, preserving user-created siblings.

    This is intentionally a recursive merge rather than a one-shot seed: an older
    or partially initialized KB must still gain the exact archive subdirectories
    required by project-document synchronization.

    骨架按库类型选择（个人库 / 共享库），并顺带拆掉历史库里的
    「个人文件夹 / 项目文件夹」包壳（见 ``_flatten_scope_wrappers``）。
    """
    kb = await db.get(KnowledgeBase, kb_id)
    tree = preset_folder_tree(kb.kb_type if kb else None)
    await _build_folder_nodes(db, kb_id, tree, None)
    await _flatten_scope_wrappers(db, kb_id, kb.kb_type if kb else None)
    if commit:
        await db.commit()


async def ensure_member_folder(
    db: AsyncSession, kb_id: int, user, *, commit: bool = False
) -> KbFolder:
    """项目资料库里取/建某个成员的根文件夹（owner_id = 该用户）。

    生成的文书落在 `<成员>/工作记录与表单/<类型>`；「我的资料」直接打开这个
    文件夹，所以同一份文档不会写两次。
    """
    folder = (
        await db.execute(
            select(KbFolder).where(
                KbFolder.kb_id == kb_id,
                KbFolder.parent_id.is_(None),
                KbFolder.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        base = (getattr(user, "real_name", "") or getattr(user, "username", "") or f"成员{user.id}").strip()
        name = base
        existing_names = set(
            (
                await db.execute(
                    select(KbFolder.name).where(
                        KbFolder.kb_id == kb_id, KbFolder.parent_id.is_(None)
                    )
                )
            ).scalars().all()
        )
        suffix = 2
        while name in existing_names:
            name = f"{base}（{suffix}）"
            suffix += 1
        folder = KbFolder(
            kb_id=kb_id,
            parent_id=None,
            name=name,
            # 成员文件夹排在公共分类前面（sort_order 越小越前）
            sort_order=-1,
            is_preset=False,
            owner_id=user.id,
        )
        db.add(folder)
        await db.flush()
    await _build_folder_nodes(db, kb_id, PRESET_FOLDER_TREE_MEMBER, folder.id)
    if commit:
        await db.commit()
    return folder
