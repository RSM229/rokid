"""给本地 SQLite 开发库补/改测试账号（隔离库，不碰生产）。

用法：
  cloud-backend/.venv/Scripts/python.exe tmp/seed_local_users.py [用户名] [密码]

默认建 chief / chief123，角色总监理工程师（chief_supervisor），
供小程序「绑定监理通账号」联调。已存在则重置密码并校正角色。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(r"D:\rokid\rokid")
sys.path.insert(0, str(REPO / "tmp"))

import dev_local  # noqa: E402  # 复用其环境装配（SQLite 隔离库 + env）

dev_local.prepare_env(8001)

from sqlalchemy import select  # noqa: E402

from app.db import async_session, init_db  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.passwords import pwd_context  # noqa: E402

# init_db 会把全部模型 import 进 metadata（users.org_id 的 FK 目标 organizations
# 也在内）并建表；与服务启动同路径，幂等。跳过它 INSERT 会直接
# NoReferencedTableError。
asyncio.run(init_db())


async def main() -> None:
    username = sys.argv[1] if len(sys.argv) > 1 else "chief"
    password = sys.argv[2] if len(sys.argv) > 2 else "chief123"
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=username,
                password_hash=pwd_context.hash(password),
                real_name="总监测试",
                role="chief_supervisor",
                is_active=True,
            )
            db.add(user)
            action = "created"
        else:
            user.password_hash = pwd_context.hash(password)
            user.role = "chief_supervisor"
            user.is_active = True
            action = "updated"
        await db.commit()
        await db.refresh(user)
        print(
            f"{action}: id={user.id} username={user.username} "
            f"role={user.role} active={user.is_active} "
            f"verify={pwd_context.verify(password, user.password_hash)}"
        )


asyncio.run(main())
