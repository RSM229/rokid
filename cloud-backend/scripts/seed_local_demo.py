"""Initialize a demo project and user only in a disposable local database."""
from __future__ import annotations

import asyncio

from scripts.run_local import configure_local


async def main() -> None:
    configure_local()
    from app.config import settings
    from sqlalchemy.engine import make_url

    if make_url(settings.database_url).database != "gdjl_local":
        raise RuntimeError("Demo seeding is disabled for the shared business database")

    from sqlalchemy import select
    from app.db import async_session, close_db, init_db
    from app.models.organization import Organization
    from app.models.project_member import ProjectMember
    from app.models.user import Project, User
    from app.services.kb_seed import ensure_builtin_knowledge_base
    from app.services.passwords import pwd_context

    await init_db()
    async with async_session() as db:
        organization = await db.scalar(
            select(Organization).where(Organization.code == "LOCAL-DEMO")
        )
        if organization is None:
            organization = Organization(name="本地测试组织", code="LOCAL-DEMO")
            db.add(organization)
            await db.flush()
        project = await db.scalar(
            select(Project).where(Project.code == "LOCAL-DEMO-PROJECT")
        )
        if project is None:
            project = Project(
                name="本地联调项目", code="LOCAL-DEMO-PROJECT",
                org_id=organization.id, location="本地测试数据",
            )
            db.add(project)
            await db.flush()
        user = await db.scalar(select(User).where(User.username == "local_demo"))
        if user is None:
            user = User(
                username="local_demo",
                password_hash=pwd_context.hash("LocalDemo123!"),
                real_name="本地测试员", role="chief_supervisor",
                project_id=project.id, org_id=organization.id,
                audit_status="approved", is_active=True,
            )
            db.add(user)
            await db.flush()
        membership = await db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        if membership is None:
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role="chief_supervisor"))
        await db.commit()
    await ensure_builtin_knowledge_base()
    await close_db()
    print("Local demo ready: local_demo / LocalDemo123! (chief_supervisor)")


if __name__ == "__main__":
    asyncio.run(main())
