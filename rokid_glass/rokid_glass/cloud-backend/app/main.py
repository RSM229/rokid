"""FastAPI 应用实例与路由注册"""
import logging
import os
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings, get_cors_origins, check_production_security
from app.routers import auth, hazard, document, knowledge, ledger, watermark, simulator, local_ai
from app.routers import work_record, task, knowledge_graph, project
from app.routers import media, miniprogram_auth
from app.routers import signature, inspection, ws
from app.routers import acceptance_template, wechat_notice
from app.routers import knowledge_base, note, ai_extras
from app.routers import omni
from app.routers import model_settings
from app.routers import form_doc
from app.routers import work_session
from app.routers import rtc
from app.routers import provform, meeting, progress, parallel, resources, visa, equipment, archive
from app.routers import stats_company
from app.routers import role_permissions
from app.routers import agent
from app.services.rtc_service import timeout_worker
from app.services.period_report_service import daily_report_worker
from app.db import close_db, init_db

# 项目原先没有任何 logging 配置：root logger 无 handler，于是所有 logger.info
# （RAG 预热结果、索引载入规模、增量刷新…）静默丢失，只剩 WARNING 借
# logging.lastResort 落到 stderr —— 「索引到底刷没刷」只能靠猜。
# 这里补一个最小配置。uvicorn 的 dictConfig 是 disable_existing_loggers=False
# 且不含 root 键，不会覆盖它；uvicorn --reload 重启会重复执行，但 basicConfig
# 幂等，无害。
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 生产环境安全检查
    security_warnings = check_production_security()
    for w in security_warnings:
        logger.warning(f"[安全检查] {w}")
    if security_warnings and not settings.debug and settings.strict_production_config:
        raise RuntimeError(
            "生产配置检查失败：" + "；".join(security_warnings)
        )

    await init_db()
    # RAG 检索预热：把嵌入模型、分词器、索引缓存提前建好，
    # 否则首个检索请求要承担 95MB 模型加载 + 建 BM25 的全部开销。
    # 预热失败不影响服务启动，检索会自动回退关键词模式。
    rag = None
    try:
        from app.services import rag_index as rag

        warm = await asyncio.to_thread(rag.warmup)
        logger.info(f"[RAG] 预热结果：{warm}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[RAG] 预热失败，检索将回退关键词模式：{exc}")

    rtc_timeouts = asyncio.create_task(timeout_worker())
    # 索引增量守护：让「系统使用过程中新积累的记录」自动进检索，无需人工重建。
    rag_refresh = (
        asyncio.create_task(rag.incremental_worker()) if rag is not None else None
    )
    # 周期文书守护：每天 08:00（东八区）自动生成**前一天**的监理日志，目标日
    # 为周日/月末时补该周周报、该月月报。生成端幂等，不覆盖已复核的草稿。
    daily_docs = (
        asyncio.create_task(daily_report_worker())
        if settings.daily_doc_auto
        else None
    )
    try:
        yield
    finally:
        rtc_timeouts.cancel()
        with suppress(asyncio.CancelledError):
            await rtc_timeouts
        if rag_refresh is not None:
            rag_refresh.cancel()
            with suppress(asyncio.CancelledError):
                await rag_refresh
        if daily_docs is not None:
            daily_docs.cancel()
            with suppress(asyncio.CancelledError):
                await daily_docs
        await close_db()


app = FastAPI(
    title="AI眼镜工程管理应用 — 云端后端",
    description="监理行业智能体云端服务",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_origins = get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Step 3：让浏览器端能读到权限版本头（跨域场景下不加 expose 会被挡）
    expose_headers=["X-Perm-Version"],
)
logger.info(f"CORS 允许来源: {_cors_origins}")


@app.middleware("http")
async def attach_perm_version(request, call_next):
    """在每个响应上带出权限配置版本（Step 3 · 权限即时生效）。

    前端拦截器比对本地快照，发现版本变化就重新拉 ``/auth/me`` ——
    于是管理员改完权限，在线用户**下一个请求**就会更新菜单，无需重新登录。

    读的是进程内缓存值（``get_cached_perm_version``），因此这是一次纯内存
    操作，不给每个请求增加任何数据库开销。
    """
    response = await call_next(request)
    try:
        from app.services.permissions import get_cached_perm_version

        version = get_cached_perm_version()
        if version:
            response.headers["X-Perm-Version"] = str(version)
    except Exception:  # noqa: BLE001 —— 版本头失败绝不能影响正常响应
        pass
    return response

app.mount("/uploads", StaticFiles(directory=str(settings.upload_dir)), name="uploads")
app.mount("/exports", StaticFiles(directory=str(settings.export_dir)), name="exports")
app.mount(
    "/simulator",
    StaticFiles(directory=str(APP_DIR / "static"), html=True),
    name="simulator",
)

# 路由注册
app.include_router(rtc.router, prefix="/api/rtc", tags=["实时音视频"])
app.include_router(auth.router, prefix="/api/auth", tags=["认证鉴权"])
app.include_router(hazard.router, prefix="/api/hazard", tags=["隐患识别"])
app.include_router(document.router, prefix="/api/document", tags=["监理文书"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["知识库"])
app.include_router(knowledge_base.router, prefix="/api/kb", tags=["个人/共享知识库"])
app.include_router(note.router, prefix="/api/notes", tags=["笔记"])
app.include_router(ai_extras.router, prefix="/api/ai", tags=["AI扩展"])
app.include_router(ledger.router, prefix="/api/ledger", tags=["台账管理"])
app.include_router(watermark.router, prefix="/api/watermark", tags=["水印服务"])
app.include_router(simulator.router, prefix="/api", tags=["眼镜端模拟器"])
app.include_router(local_ai.router, prefix="/api/local", tags=["本地AI服务"])
app.include_router(work_record.router, prefix="/api/work-record", tags=["工作记录"])
app.include_router(task.router, prefix="/api/task", tags=["派单管理"])
app.include_router(knowledge_graph.router, prefix="/api/kg", tags=["知识图谱"])
app.include_router(project.router, prefix="/api/project", tags=["项目管理"])
app.include_router(
    miniprogram_auth.router,
    prefix="/api/miniprogram/auth",
    tags=["微信小程序认证"],
)
app.include_router(media.router, prefix="/api/media", tags=["统一媒体"])
app.include_router(signature.router, prefix="/api", tags=["电子签章"])
app.include_router(inspection.router, prefix="/api/inspection", tags=["云端巡检"])
app.include_router(ws.router, prefix="/api/ws", tags=["WebSocket实时推送"])
app.include_router(
    acceptance_template.router,
    prefix="/api/acceptance-templates",
    tags=["验收流程模板"],
)
app.include_router(
    wechat_notice.router,
    prefix="/api/wechat",
    tags=["微信服务通知"],
)
app.include_router(omni.router, prefix="/api/omni", tags=["语音交互兼容接口"])
app.include_router(
    model_settings.router,
    prefix="/api/model-settings",
    tags=["模型调用设置"],
)
app.include_router(
    form_doc.router,
    prefix="/api/form-docs",
    tags=["省统表文书"],
)
app.include_router(
    work_session.router,
    prefix="/api/work-session",
    tags=["眼镜端工作会话"],
)
app.include_router(
    provform.router,
    prefix="/api/provform",
    tags=["省统表schema"],
)
app.include_router(
    meeting.router,
    prefix="/api/meeting",
    tags=["会议纪要"],
)
app.include_router(
    progress.router,
    prefix="/api/progress",
    tags=["进度记录"],
)
app.include_router(
    parallel.router,
    prefix="/api/parallel",
    tags=["平行检验"],
)
app.include_router(
    resources.router,
    prefix="/api/resources",
    tags=["资源投入"],
)
app.include_router(
    visa.router,
    prefix="/api/visa",
    tags=["工程签证"],
)
app.include_router(
    equipment.router,
    prefix="/api/equipment",
    tags=["测量设备"],
)
app.include_router(
    archive.router,
    prefix="/api/archive",
    tags=["资料记录"],
)
app.include_router(
    stats_company.router,
    prefix="/api/stats",
    tags=["公司级看板"],
)
app.include_router(
    role_permissions.router,
    prefix="/api/permissions",
    tags=["角色权限配置"],
)
app.include_router(
    agent.router,
    prefix="/api/agent",
    tags=["智能体（聊天即操作）"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gdjl-cloud-backend"}
