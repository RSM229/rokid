from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    level: Mapped[str] = mapped_column(String(20), comment="personal/team/enterprise")
    role: Mapped[str] = mapped_column(String(32), comment="角色名称（项目级岗位 key）")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    used_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # ---- Step 4（设计说明 §8.8.1 / A-1）：企业归属 ----
    # 码只决定「哪家公司」，**不带 project_id、不带岗位** —— 项目与岗位由审批指定。
    # nullable → init_db() 的 create_all / _reconcile_missing_columns() 可安全补齐。
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True, index=True,
        comment="企业归属（Step 4 A-1：码管企业，审批管项目）",
    )
    # ---- 邀请码语义升级：项目负责人加项目成员 ----
    # project_id 非空 = 该码是「某项目的入项目码」，持有人扫码即加入此项目并获 role 岗位；
    # 为空 = 仅企业归属码（老语义，由超管发放）。
    # nullable → _reconcile_missing_columns() 可在存量实例安全补列，无需停机 ALTER。
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True,
        comment="目标项目（非空=项目邀请码，用于项目负责人加成员）",
    )
    # ---- 邀请码语义升级结束 ----
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
