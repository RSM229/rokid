"""RAG 分块索引模型 — 知识库文档与系统积累记录的统一语义索引

一个 source（知识库文档 / 工作记录 / 已生成文书 / 笔记 / 知识图谱实体）会被切成
若干 chunk，每块一行。embedding 以 float32 原始字节存放（dim × 4 字节）。

增量重建的判据是 ``source_hash``：同一 (source_type, source_id) 下 hash 不变则跳过，
变了则先删旧行再插新行，避免残留陈旧向量。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class KbChunk(Base):
    """统一检索分块。"""

    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 知识库维度（业务记录可为空）
    kb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 项目维度（知识库文档可为空；用于按项目隔离业务记录）
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 来源类型：knowledge_doc / work_record / document / note / kg_entity
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    # 来源标识：知识库文档用相对路径，业务记录用主键
    source_id: Mapped[str] = mapped_column(String(160))
    # 展示名（文档名 / 文书标题）
    source_name: Mapped[str] = mapped_column(String(256))
    # sha256(原文)，用于幂等增量
    source_hash: Mapped[str] = mapped_column(String(64), index=True)
    # 语义桥：知识图谱实体记录的来源文档（相对路径）。用户用口语描述表单
    # （"站着盯着关键工序施工的那种表格"），实体说明是书面语（"对关键部位、关键
    # 工序施工质量实施全过程现场跟班监督的记录表格"），两者语义相通，而源文档名
    # （"14旁站记录"）与口语没有任何词面交集 —— 于是让实体命中后把票回投给源文档。
    # 仅 KG 来源有值。
    bridge_doc: Mapped[str | None] = mapped_column(String(256), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    dim: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
