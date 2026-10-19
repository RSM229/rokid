"""各文书版本的签认履历。旧内容修订后仍保留，不能冒充新版签认。"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class DocumentSignoff(Base):
    __tablename__ = "document_signoffs"
    __table_args__ = (UniqueConstraint("document_id", "revision", "slot", name="uq_doc_signoff_slot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    slot: Mapped[str] = mapped_column(String(32))
    signer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    signer_name: Mapped[str] = mapped_column(String(128))
    signature_id: Mapped[int] = mapped_column(ForeignKey("signatures.id"))
    signature_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), comment="该次签认内容的SHA256快照")
    signed_pdf_path: Mapped[str] = mapped_column(String(256))
    signed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

