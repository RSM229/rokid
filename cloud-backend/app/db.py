"""数据库初始化、轻量结构迁移与会话管理。

项目早期只使用 ``metadata.create_all`` 建表；该方法不会为已经存在的表
增加后来新增的字段。为了保证开发环境和已部署实例升级后都能直接运行，
启动时会对 ORM 元数据做一次非破坏性的列补齐。这里只执行 ``ADD COLUMN``，
不会删除或修改已有字段与数据。
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)

engine_options = {
    "echo": settings.database_echo,
    # SSH tunnel or MySQL may silently drop idle connections. Check a pooled
    # connection before handing it to a request instead of failing the request.
    "pool_pre_ping": True,
}
if settings.database_url.startswith("mysql+aiomysql:"):
    engine_options["pool_recycle"] = 300
    engine_options["pool_timeout"] = 30
engine = create_async_engine(settings.database_url, **engine_options)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _reconcile_missing_columns(conn: Connection, table_names=None) -> None:
    """为已有表补齐 ORM 中新增的列。

    SQLAlchemy 的 ``create_all`` 只创建缺失的表，不会演进现有表。当前项目
    需要兼容 MySQL 与 SQLite，因此使用当前数据库方言编译列类型，并仅为
    可安全添加的列执行 ``ALTER TABLE ... ADD COLUMN``。
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    preparer = conn.dialect.identifier_preparer

    for table in Base.metadata.sorted_tables:
        if table_names is not None and table.name not in table_names:
            continue
        if table.name not in existing_tables:
            continue
        existing_columns = {item["name"] for item in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            if not column.nullable and column.server_default is None:
                # 含历史数据的表无法安全添加无默认值的非空列。此类变更应由
                # 显式数据迁移完成，避免启动时破坏现有数据。
                logger.warning(
                    "跳过无法安全自动迁移的非空字段 %s.%s",
                    table.name,
                    column.name,
                )
                continue

            table_name = preparer.quote(table.name)
            column_name = preparer.quote(column.name)
            column_type = column.type.compile(dialect=conn.dialect)
            nullable_sql = "" if column.nullable else " NOT NULL"
            default_sql = ""
            if column.server_default is not None:
                default_value = str(column.server_default.arg)
                default_sql = f" DEFAULT {default_value}"

            statement = (
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} "
                f"{column_type}{nullable_sql}{default_sql}"
            )
            conn.execute(text(statement))
            logger.info("数据库迁移：已添加字段 %s.%s", table.name, column.name)

    # ``create_all`` cannot add constraints to existing tables. Keep the
    # source-document mapping one-to-one on upgraded databases as well.
    if table_names is None or "knowledge_documents" in table_names:
        if "knowledge_documents" in existing_tables:
            columns = {
                item["name"]
                for item in inspect(conn).get_columns("knowledge_documents")
            }
            if "source_document_id" in columns:
                indexes = inspect(conn).get_indexes("knowledge_documents")
                constraints = inspect(conn).get_unique_constraints(
                    "knowledge_documents"
                )
                has_unique_source_index = any(
                    item.get("unique") and item.get("column_names") == ["source_document_id"]
                    for item in indexes
                ) or any(
                    item.get("column_names") == ["source_document_id"]
                    for item in constraints
                )
                if not has_unique_source_index:
                    duplicates = conn.execute(text(
                        "SELECT source_document_id FROM knowledge_documents "
                        "WHERE source_document_id IS NOT NULL "
                        "GROUP BY source_document_id HAVING COUNT(*) > 1 LIMIT 1"
                    )).first()
                    if duplicates:
                        logger.error(
                            "无法创建项目归档来源唯一索引：knowledge_documents.source_document_id "
                            "存在重复值 %s", duplicates[0],
                        )
                    else:
                        index_name = preparer.quote(
                            "uq_knowledge_documents_source_document_id"
                        )
                        table_name = preparer.quote("knowledge_documents")
                        column_name = preparer.quote("source_document_id")
                        conn.execute(text(
                            f"CREATE UNIQUE INDEX {index_name} ON {table_name} "
                            f"({column_name})"
                        ))


async def init_db():
    """初始化数据库表，并非破坏性地补齐新增字段。"""
    from app.models import user, project, document, hazard, ledger  # noqa: F401
    from app.models import image, rectification, regulation_chunk  # noqa: F401
    from app.models import organization  # noqa: F401  # Step 1：企业锚点（user/project 的 FK 目标）
    from app.models import work_record, task_assignment, knowledge_graph  # noqa: F401
    from app.models import miniprogram  # noqa: F401
    from app.models import document_signoff  # noqa: F401
    from app.models import signature, inspection_session, video  # noqa: F401
    from app.models import acceptance_template, archive, wechat_subscription  # noqa: F401
    from app.models import regulation_version  # noqa: F401
    from app.models import notification  # noqa: F401
    from app.models import model_config  # noqa: F401
    from app.models import knowledge_base  # noqa: F401
    from app.models import kb_chunk  # noqa: F401  # RAG 检索分块索引
    from app.models import note  # noqa: F401
    from app.models import meeting  # noqa: F401
    from app.models import progress  # noqa: F401
    from app.models import parallel  # noqa: F401
    from app.models import resources  # noqa: F401
    from app.models import visa  # noqa: F401
    from app.models import equipment  # noqa: F401
    from app.models import archive  # noqa: F401,F811  # P3.7：追加 ArchiveRecord/IssueTicket（archive 上方已导入）
    from app.models import project_member, security_audit  # noqa: F401  # 项目成员边界 + 安全审计
    from app.models import verification_code  # noqa: F401  # 短信验证码（手机号注册/登录）
    from app.models import invite_code  # noqa: F401  # 邀请码（含 project_id 升级）
    from app.models import role_permission  # noqa: F401  # Step 3：角色与权限矩阵
    from app.models import security_question  # noqa: F401  # 密保问题（忘记密码自助重置）
    from app.models import agent_draft  # noqa: F401  # 聊天追问草稿（多轮补字段持久状态）
    from app.models import collaboration, project_archive  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_reconcile_missing_columns)
    from app.services.model_config_service import seed_default_model_configs
    await seed_default_model_configs()
    from app.services.kb_seed import ensure_builtin_knowledge_base
    await ensure_builtin_knowledge_base()
    # Step 3：角色权限矩阵播种（幂等，仅在表为空时写入）+ 权限缓存预热。
    # 预热失败不影响启动 —— 读路径会自动回退到代码常量（见 services.permissions）。
    from app.services.role_seed import seed_roles_and_permissions
    await seed_roles_and_permissions()
    from app.services.permissions import load_matrix
    async with async_session() as session:
        await load_matrix(session)


async def ensure_agent_history_schema() -> None:
    """Create isolated agent runtime tables even when shared DB init is skipped."""
    from app.models.agent_draft import (
        AgentConversationMessage,
        AgentFlowInstance,
        AgentRequestTrace,
    )

    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[
                    AgentConversationMessage.__table__,
                    AgentRequestTrace.__table__,
                    AgentFlowInstance.__table__,
                ],
            )
    except SQLAlchemyError:
        # Shared-DB app accounts may be intentionally read/write-only. A
        # missing table must not take the whole API down; deploy the checked-in
        # migration once with a schema-owner account instead.
        logger.exception(
            "Agent 运行态表不可用；请使用 migrations 中的 SQL 以 schema-owner 账号初始化"
        )


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def close_db() -> None:
    """释放数据库连接池，避免服务重载或测试退出时遗留连接。"""
    try:
        await engine.dispose()
    except Exception as exc:  # noqa: BLE001
        # During uvicorn reload the SSH tunnel may disappear before the old
        # worker closes its sockets. Shutdown must never crash the reloader.
        logger.warning("数据库连接池关闭时连接已断开，忽略退出异常：%s", exc)
