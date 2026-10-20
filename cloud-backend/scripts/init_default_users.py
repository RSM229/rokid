"""初始化默认用户数据（使用 SQLAlchemy + MySQL）"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import async_session, init_db
from app.models.user import User, Project, ORG_ROLE_SUPER_ADMIN
from app.models.knowledge_graph import KGEntity
from app.services.passwords import pwd_context

DEFAULT_PASSWORD = "admin123"


async def init_data():
    await init_db()
    print("✓ 数据库表结构已创建")

    async with async_session() as session:
        # 1. 创建默认项目
        from sqlalchemy import select
        result = await session.execute(select(Project).where(Project.code == "GDJL-001"))
        project = result.scalar_one_or_none()
        
        if project:
            project_id = project.id
            print(f"  项目已存在: {project.name} (ID={project_id})")
        else:
            project = Project(
                name="广东监理示范工程",
                code="GDJL-001",
                short_name="广东监理",
                location="广东省广州市",
                description="监理通 SaaS 平台默认示范工程",
            )
            session.add(project)
            await session.flush()
            project_id = project.id
            await session.commit()
            print(f"✓ 创建项目: 广东监理示范工程 (ID={project_id})")

        # 2. 创建四个角色的默认用户（Step 1：角色值改英文 key）
        users_data = [
            ("chief", "张总监", "chief_supervisor", "13800000001"),
            ("deputy", "李代表", "supervisor_representative", "13800000002"),
            ("supervisor", "王监理", "professional_supervisor", "13800000003"),
            ("inspector", "赵监理员", "supervisor", "13800000004"),
        ]

        for username, real_name, role, phone in users_data:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
            if user:
                print(f"  用户已存在: {real_name}")
            else:
                pw_hash = pwd_context.hash(DEFAULT_PASSWORD)
                user = User(
                    username=username,
                    password_hash=pw_hash,
                    real_name=real_name,
                    role=role,
                    phone=phone,
                    project_id=project_id,
                    is_active=True,
                )
                session.add(user)
                await session.commit()
                print(f"✓ 创建用户: {real_name} ({role}) — 账号: {username} / 密码: {DEFAULT_PASSWORD}")

        # 3. 创建 admin 超级管理员用户（可使用任意角色登录，拥有所有权限）
        result = await session.execute(select(User).where(User.username == "admin"))
        admin_user = result.scalar_one_or_none()
        if admin_user:
            print(f"  用户已存在: admin (超级管理员)")
        else:
            pw_hash = pwd_context.hash(DEFAULT_PASSWORD)
            admin_user = User(
                username="admin",
                password_hash=pw_hash,
                real_name="系统管理员",
                role="chief_supervisor",
                phone="13800000000",
                project_id=project_id,
                is_active=True,
                org_role=ORG_ROLE_SUPER_ADMIN,
            )
            session.add(admin_user)
            await session.commit()
            print(f"✓ 创建用户: 系统管理员 (超级管理员) — 账号: admin / 密码: {DEFAULT_PASSWORD}")

        # 3.5 用户就绪后补种内置“规范知识库”（空库启动时会跳过）
        from app.services.kb_seed import ensure_builtin_knowledge_base
        await ensure_builtin_knowledge_base()

        # 4. 插入知识图谱种子数据
        seed_entities = [
            ("混凝土结构工程施工质量验收规范", "规范条款", "GB 50204-2015"),
            ("钢筋工程验收", "验收流程", "钢筋绑扎、焊接、保护层厚度验收流程"),
            ("模板安装", "施工工序", "模板安装及支撑体系施工工序"),
            ("脚手架搭设", "施工工序", "落地式钢管脚手架搭设工序"),
            ("高处坠落", "隐患类型", "高处作业未系安全带、临边无防护"),
            ("主体结构", "工程部位", "建筑主体结构部位"),
            ("钢筋绑扎质量检查", "监理要点", "检查钢筋间距、搭接长度、保护层厚度"),
        ]

        count = 0
        for name, entity_type, desc in seed_entities:
            result = await session.execute(
                select(KGEntity).where(KGEntity.name == name, KGEntity.entity_type == entity_type)
            )
            if not result.scalar_one_or_none():
                entity = KGEntity(
                    name=name,
                    entity_type=entity_type,
                    description=desc,
                    source_doc="系统初始化",
                )
                session.add(entity)
                count += 1
        if count:
            await session.commit()
            print(f"✓ 插入 {count} 条知识图谱种子数据")

    print("\n✅ 初始化完成！")
    print(f"\n登录账号（密码均为 {DEFAULT_PASSWORD}）：")
    print(f"  admin      / {DEFAULT_PASSWORD}  — 超级管理员（任意角色均可登录）")
    print(f"  chief      / {DEFAULT_PASSWORD}  — 总监理工程师")
    print(f"  deputy     / {DEFAULT_PASSWORD}  — 总监代表")
    print(f"  supervisor / {DEFAULT_PASSWORD}  — 专业监理工程师")
    print(f"  inspector  / {DEFAULT_PASSWORD}  — 监理员")


if __name__ == "__main__":
    asyncio.run(init_data())
