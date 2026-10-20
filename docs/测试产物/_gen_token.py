"""只读：为指定用户签发一个 access_token，用于接口验收测试。

用法（容器内）::

    python /workspace/_gen_token.py <username>

不修改任何数据（只 SELECT + 内存签发 JWT）。
"""
from __future__ import annotations

import asyncio
import importlib
import pkgutil
import sys

# 全量导入模型，避免 relationship 解析时找不到表（项目铁律）
import app.models as _M  # noqa: E402

for _m in pkgutil.iter_modules(_M.__path__):
    importlib.import_module(f"app.models.{_m.name}")

from sqlalchemy import select  # noqa: E402

from app.db import async_session  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers.auth import create_access_token  # noqa: E402
from app.services.permissions import effective_permissions  # noqa: E402


async def main(name: str) -> None:
    async with async_session() as db:
        user = (await db.execute(
            select(User).where(User.username == name)
        )).scalar_one_or_none()
        if user is None:
            print(f"ERR: 用户 {name} 不存在", file=sys.stderr)
            sys.exit(2)
        perms = await effective_permissions(user, db)
        token = create_access_token({
            "sub": str(user.id),
            "role": user.role,
            "permissions": perms,
        })
        print(f"USER={user.username} id={user.id} role={user.role} "
              f"org_role={user.org_role} audit={user.audit_status} "
              f"active={user.is_active}", file=sys.stderr)
        print(f"PERMS={sorted(perms)}", file=sys.stderr)
        print(token)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
