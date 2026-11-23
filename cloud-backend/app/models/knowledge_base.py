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
from sqlalchemy import select
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


# 预设目录分类树（与 D:\\rokid\\预设目录 对应）。每个资料库首次访问时播种为默认骨架。
PRESET_FOLDER_TREE: list[dict] = [
    {"name": "个人文件夹"},
    {
        "name": "项目文件夹",
        "children": [
            {"name": "会议记录"},
            {
                "name": "合同与资质",
                "children": [
                    {"name": "施工单位"},
                    {"name": "监理单位"},
                    {"name": "设计单位"},
                ],
            },
            {
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
        ],
    },
]


async def ensure_kb_folder_tree(
    db: AsyncSession, kb_id: int, *, commit: bool = True
) -> None:
    """Ensure every preset folder node exists, preserving user-created siblings.

    This is intentionally a recursive merge rather than a one-shot seed: an older
    or partially initialized KB must still gain the exact archive subdirectories
    required by project-document synchronization.
    """
    existing_rows = (
        await db.execute(select(KbFolder).where(KbFolder.kb_id == kb_id))
    ).scalars().all()
    by_parent_name: dict[tuple[Optional[int], str], KbFolder] = {}
    for row in existing_rows:
        by_parent_name.setdefault((row.parent_id, row.name), row)
    order = max((row.sort_order or 0 for row in existing_rows), default=-1) + 1

    async def build(nodes: list[dict], parent_id: Optional[int]) -> None:
        nonlocal order
        for node in nodes:
            folder = by_parent_name.get((parent_id, node["name"]))
            if folder is None:
                folder = KbFolder(
                    kb_id=kb_id,
                    parent_id=parent_id,
                    name=node["name"],
                    sort_order=order,
                    is_preset=True,
                )
                db.add(folder)
                await db.flush()
                by_parent_name[(parent_id, node["name"])] = folder
                order += 1
            if node.get("children"):
                await build(node["children"], folder.id)

    await build(PRESET_FOLDER_TREE, None)
    if commit:
        await db.commit()
