"""企业（组织）模型 —— 多企业入驻的租户锚点。

本模型是「C-lite」方案的落点（见《用户体系结构设计说明》§8.7）：
当前系统是单企业运行，本表只有一行默认企业；``users.org_id`` /
``projects.org_id`` 全量回填为该企业。

**不做全量租户隔离** —— 数据库里带 ``project_id`` 的 22 张业务表一律不动，
将来的隔离通过 ``projects.org_id`` 做 join 实现（表少、口径统一）。
这样现在只需付出"埋锚点"的成本，而上线后再补则需要停机改造唯一约束。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), comment="企业/组织名称")
    code: Mapped[str] = mapped_column(String(64), unique=True, comment="企业编码（稳定标识）")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
