"""知识库路由 — 规范查询、验收指引、文书模板、规范库维护、LLM 智能问答"""
import re
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.auth import get_current_user, require_permission
from app.db import get_db
from app.models.knowledge_graph import KGEntity
from app.services.knowledge_service import (
    search_by_keyword,
    search_by_content,
    get_acceptance_guide,
    get_doc_template,
    REGULATION_CATEGORIES,
    KNOWLEDGE_DOCS,
    SAFETY_DOCS,
    iter_knowledge_documents,
)
from app.services.ai_client import ai_client
from app.services.text_clean import strip_markdown
from app.config import settings

router = APIRouter()


def _normalize_query_value(value: str) -> str:
    """兼容客户端对中文文件名/分类可能出现的重复 URL 编码。

    FastAPI 已自动解码一次；若客户端对已编码字符串再次编码（双编码），
    这里再反复解码，直到不再变化。
    """
    from urllib.parse import unquote

    prev = None
    while prev != value:
        prev = value
        value = unquote(value)
    return value


# ===== 规范版本管理 =====
from datetime import date as date_type
from app.services.regulation_version_service import (
    register_version,
    list_versions,
    get_version_history,
    rollback_version,
    sync_versions_from_files,
)


class VersionCreateRequest(BaseModel):
    filename: str
    doc_name: Optional[str] = None
    category: str = "省统表"
    version: str = "1.0"
    effective_date: Optional[date_type] = None
    expire_date: Optional[date_type] = None
    remark: Optional[str] = None


class VersionRollbackRequest(BaseModel):
    version_id: int


@router.get("/regulations/versions")
async def api_list_versions(
    filename: Optional[str] = None,
    category: Optional[str] = None,
    active_only: bool = False,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """查询规范版本列表"""
    records = await list_versions(db, filename, category, active_only)
    return {
        "total": len(records),
        "versions": [
            {
                "id": r.id,
                "doc_name": r.doc_name,
                "filename": r.filename,
                "category": r.category,
                "version": r.version,
                "effective_date": r.effective_date.isoformat() if r.effective_date else None,
                "expire_date": r.expire_date.isoformat() if r.expire_date else None,
                "is_active": r.is_active,
                "checksum": r.checksum,
                "remark": r.remark,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ],
    }


@router.post("/regulations/versions")
async def api_create_version(
    req: VersionCreateRequest,
    user: dict = Depends(require_permission("settings")),
    db=Depends(get_db),
):
    """注册/更新规范文档版本"""
    # 查找文件实际路径
    doc = None
    for d in iter_knowledge_documents():
        if d["filename"] == req.filename:
            doc = d
            break
    if not doc:
        raise HTTPException(status_code=404, detail=f"规范文件不存在: {req.filename}")

    record = await register_version(
        db,
        doc_name=req.doc_name or doc["name"],
        filename=req.filename,
        category=req.category or doc["category"],
        file_path=doc["path"],
        version=req.version,
        effective_date=req.effective_date,
        expire_date=req.expire_date,
        remark=req.remark,
    )
    return {
        "success": True,
        "version_id": record.id,
        "message": f"版本 {record.version} 已注册",
    }


@router.get("/regulations/versions/{filename}")
async def api_version_history(
    filename: str,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """获取指定文档的版本历史"""
    records = await get_version_history(db, filename)
    return {
        "filename": filename,
        "total": len(records),
        "history": [
            {
                "id": r.id,
                "version": r.version,
                "effective_date": r.effective_date.isoformat() if r.effective_date else None,
                "expire_date": r.expire_date.isoformat() if r.expire_date else None,
                "is_active": r.is_active,
                "remark": r.remark,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }


@router.post("/regulations/versions/rollback")
async def api_rollback_version(
    req: VersionRollbackRequest,
    user: dict = Depends(require_permission("settings")),
    db=Depends(get_db),
):
    """版本回退"""
    try:
        record = await rollback_version(db, req.version_id)
        return {
            "success": True,
            "message": f"已回退到版本 {record.version}",
            "version_id": record.id,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/regulations/versions/sync")
async def api_sync_versions(
    user: dict = Depends(require_permission("settings")),
    db=Depends(get_db),
):
    """扫描知识库文件，自动创建缺失的版本记录"""
    result = await sync_versions_from_files(db)
    return {
        "success": True,
        "scanned": result["scanned"],
        "created": result["created"],
        "message": f"扫描完成：新增 {result['created']} 条版本记录",
    }


# 允许导入的文件类型
ALLOWED_IMPORT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
}
ALLOWED_IMPORT_EXTS = set(ALLOWED_IMPORT_TYPES.values())


def _category_dir(category: str) -> Path:
    if category == "省统表":
        return settings.knowledge_base_path / "省统表word版"
    if category == "安全监理用表":
        return (
            settings.knowledge_base_path
            / "第三节安全监理用表"
            / "安全监理用表"
        )
    if category in REGULATION_CATEGORIES:
        # 其余分类作为独立目录，便于扩展
        return settings.knowledge_base_path / category
    raise HTTPException(status_code=400, detail="知识库分类无效")


async def _read_document_limited(file: UploadFile) -> bytes:
    data = await file.read(settings.max_document_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > settings.max_document_bytes:
        raise HTTPException(status_code=413, detail="文件超过上传大小限制")
    return data


def _normalize_filename(name: str) -> str:
    """清理文件名，保留中文、英文、数字、下划线和连字符"""
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("_.")


class SearchRequest(BaseModel):
    keyword: str
    category: Optional[str] = None
    search_content: bool = True


class AcceptanceRequest(BaseModel):
    keyword: str


class LLMQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    use_llm: bool = True
    # all=全部知识文档+知识图谱；knowledge-graph=仅图谱；
    # 其他值按知识文档分类处理（如“省统表”“安全监理用表”）。
    knowledge_scope: str = Field(default="all", max_length=64)


class RegulationMaintainRequest(BaseModel):
    action: str  # add / update / delete / move_category / list
    name: str
    category: Optional[str] = None
    target_category: Optional[str] = None  # move_category 专用：目标分类
    file_path: Optional[str] = None


@router.get("/regulations/search")
async def search_regulations(
    keyword: str,
    category: Optional[str] = None,
    search_content: bool = True,
    user: dict = Depends(get_current_user),
):
    """规范库智能检索（关键词 + 内容匹配）"""
    results = search_by_keyword(keyword, category)
    if search_content:
        content_results = search_by_content(keyword)
        # 合并去重
        existing_names = {r["name"] for r in results}
        for r in content_results:
            if r["name"] not in existing_names:
                results.append(r)
    return {"keyword": keyword, "total": len(results), "results": results}


@router.post("/regulations/search")
async def search_regulations_post(
    req: SearchRequest,
    user: dict = Depends(get_current_user),
):
    """规范库智能检索（POST 方式，适合语音输入）"""
    results = search_by_keyword(req.keyword, req.category)
    if req.search_content:
        content_results = search_by_content(req.keyword)
        existing_names = {r["name"] for r in results}
        for r in content_results:
            if r["name"] not in existing_names:
                results.append(r)
    return {"keyword": req.keyword, "total": len(results), "results": results}


@router.get("/regulations/categories")
async def get_categories(user: dict = Depends(get_current_user)):
    """获取规范分类列表及各分类下的规范数量"""
    from app.services.regulation_category_service import list_categories
    return {"categories": list_categories()}


@router.get("/regulations/list")
async def list_regulations(
    category: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """列出所有规范文档"""
    results = iter_knowledge_documents()

    if category:
        results = [r for r in results if r["category"] == category]

    return {
        "total": len(results),
        "results": [
            {
                "name": item["name"],
                "filename": item["filename"],
                "source_file": item["filename"],
                "category": item["category"],
                "exists": item["exists"],
            }
            for item in results
        ],
    }


@router.get("/regulations/content")
async def get_regulation_content(
    filename: str,
    category: str = "省统表",
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """获取规范文档解析后的文本内容（优先取 regulation_chunks，缺失时现场解析原文件）"""
    from sqlalchemy import select

    from app.models.regulation_chunk import RegulationChunk
    from app.services.knowledge_service import (
        _extract_docx_text,
        _extract_xlsx_text,
        iter_knowledge_documents,
    )

    filename = _normalize_query_value(filename)
    category = _normalize_query_value(category)

    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="文件名无效")

    doc = None
    for d in iter_knowledge_documents():
        if d["filename"] == filename and d["category"] == category:
            doc = d
            break
    if doc is None:
        for d in iter_knowledge_documents():
            if d["filename"] == filename:
                doc = d
                break
    if doc is None:
        raise HTTPException(status_code=404, detail="规范文件不存在")

    stmt = (
        select(RegulationChunk.content)
        .where(
            RegulationChunk.doc_name == doc["name"],
            RegulationChunk.category == doc["category"],
        )
        .order_by(RegulationChunk.chunk_index)
    )
    rows = (await db.execute(stmt)).scalars().all()
    text = "\n\n".join(r for r in rows if r)

    if not text.strip():
        ext = doc["path"].rsplit(".", 1)[-1].lower()
        try:
            if ext == "docx":
                text = _extract_docx_text(doc["path"])
            elif ext == "xlsx":
                text = _extract_xlsx_text(doc["path"])
        except Exception:
            text = ""

    return {
        "name": doc["name"],
        "filename": doc["filename"],
        "category": doc["category"],
        "content": text,
        "exists": doc["exists"],
    }


@router.get("/regulations/download/{filename}")
async def download_regulation(
    filename: str,
    category: str = "省统表",
    user: dict = Depends(get_current_user),
):
    """下载规范文档"""
    filename = _normalize_query_value(filename)
    category = _normalize_query_value(category)
    if Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="文件名无效")
    directory = _category_dir(category).resolve()
    file_path = (directory / filename).resolve()
    if file_path.parent != directory:
        raise HTTPException(status_code=400, detail="文件路径无效")

    if not file_path.is_file() or file_path.suffix.lower() not in ALLOWED_IMPORT_EXTS:
        raise HTTPException(status_code=404, detail="文件不存在")

    media_type = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".pdf": "application/pdf",
    }.get(file_path.suffix.lower(), "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )


@router.post("/acceptance-guide")
async def acceptance_guide(
    req: AcceptanceRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """验收流程AI智能指引（数据库模板优先，内置模板回退，LLM 增强）"""
    from app.services.acceptance_template_service import get_guide_with_fallback
    # 先从数据库获取模板，无则回退内置 ACCEPTANCE_GUIDES
    guide = await get_guide_with_fallback(db, req.keyword)

    # 尝试用 LLM 增强生成
    try:
        # 检索规范上下文
        content_results = search_by_content(req.keyword)
        context = "\n".join(r.get("snippet", "") for r in content_results[:3] if r.get("snippet"))

        llm_guide = await ai_client.generate_acceptance_guide(req.keyword, context)
        if llm_guide.get("steps"):
            # LLM 生成成功，合并结果
            guide["steps"] = llm_guide["steps"]
            guide["key_points"] = llm_guide.get("key_points", guide.get("key_points", []))
            guide["common_violations"] = llm_guide.get("common_violations", guide.get("common_violations", []))
            guide["regulation_basis"] = llm_guide.get("regulation_basis", guide.get("regulation_basis", []))
            guide["source"] = "llm"
    except Exception as e:
        # LLM 失败时回退到内置模板
        guide["source"] = guide.get("source", "template")
        guide["llm_error"] = str(e)

    return guide


@router.get("/acceptance-guide")
async def acceptance_guide_get(
    keyword: str,
    user: dict = Depends(get_current_user),
):
    """验收流程AI智能指引（GET 方式）"""
    guide = get_acceptance_guide(keyword)
    return guide


@router.get("/doc-template/{doc_type}")
async def get_document_template(
    doc_type: str,
    user: dict = Depends(get_current_user),
):
    """获取文书模板信息"""
    template = get_doc_template(doc_type)
    if not template:
        raise HTTPException(status_code=404, detail=f"未找到文书类型: {doc_type} 的模板")
    return template


@router.post("/regulations/maintain")
async def maintain_regulations(
    req: RegulationMaintainRequest,
    user: dict = Depends(require_permission("kg_manage")),
    db=Depends(get_db),
):
    """规范库维护：查询、删除、重命名或跨分类移动；新增文件使用导入接口。"""
    valid_actions = {"add", "update", "delete", "move_category", "list"}
    if req.action not in valid_actions:
        raise HTTPException(status_code=400, detail=f"无效操作: {req.action}")

    if req.action == "list":
        results = iter_knowledge_documents()
        return {"action": "list", "total": len(results), "results": results}

    if req.action == "add":
        raise HTTPException(status_code=400, detail="新增规范请使用文件导入接口")

    # 跨分类移动
    if req.action == "move_category":
        from app.services.regulation_category_service import (
            move_category as _move_category,
            update_category_in_db,
        )
        if not req.target_category:
            raise HTTPException(status_code=400, detail="move_category 需提供 target_category")
        try:
            move_result = _move_category(req.name, req.target_category)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # 同步更新数据库 regulation_chunks 表的 category 字段
        try:
            affected = await update_category_in_db(db, req.name, req.target_category)
        except Exception:
            affected = 0
        move_result["db_updated_rows"] = affected
        move_result["action"] = "move_category"
        return move_result

    directory = _category_dir(req.category or "省统表").resolve()
    source_name = Path(req.file_path or req.name).name
    if source_name != (req.file_path or req.name):
        raise HTTPException(status_code=400, detail="文件名无效")
    source = (directory / source_name).resolve()
    if source.parent != directory or not source.is_file():
        raise HTTPException(status_code=404, detail="规范文件不存在")

    if req.action == "delete":
        source.unlink()
        return {"action": "delete", "filename": source_name, "message": "规范文件已删除"}

    target_base = _normalize_filename(req.name)
    if not target_base:
        raise HTTPException(status_code=400, detail="新文件名无效")
    target = (directory / f"{target_base}{source.suffix.lower()}").resolve()
    if target.parent != directory:
        raise HTTPException(status_code=400, detail="目标文件名无效")
    if target.exists() and target != source:
        raise HTTPException(status_code=409, detail="目标文件名已存在")
    source.rename(target)
    return {
        "action": "update",
        "filename": target.name,
        "message": "规范文件已重命名",
    }


@router.post("/regulations/import")
async def import_regulation(
    file: UploadFile = File(...),
    category: str = Form("省统表"),
    name: Optional[str] = Form(None),
    user: dict = Depends(require_permission("kg_manage")),
):
    """导入规范文档到知识库

    支持 docx/xlsx/pdf 格式，保存到知识库对应分类目录。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")

    content_type = file.content_type or ""
    filename_ext = Path(file.filename).suffix.lower()
    ext = ALLOWED_IMPORT_TYPES.get(content_type)
    if ext is None and filename_ext in ALLOWED_IMPORT_EXTS:
        ext = filename_ext
    if not ext:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {content_type}，仅支持 docx/xlsx/pdf",
        )

    target_dir = _category_dir(category)

    target_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    base_name = _normalize_filename(name or Path(file.filename).stem)
    if not base_name:
        base_name = f"imported_{int(time.time())}"
    target_filename = f"{base_name}{ext}"
    target_path = target_dir / target_filename
    counter = 1
    while target_path.exists():
        target_filename = f"{base_name}_{counter}{ext}"
        target_path = target_dir / target_filename
        counter += 1

    # 保存文件
    try:
        target_path.write_bytes(await _read_document_limited(file))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {str(e)}")
    finally:
        await file.close()

    return {
        "success": True,
        "name": name or base_name,
        "filename": target_filename,
        "category": category,
        "path": str(target_path),
        "message": "导入成功",
    }


@router.post("/regulations/import-batch")
async def import_regulations_batch(
    files: list[UploadFile] = File(...),
    category: str = Form("省统表"),
    user: dict = Depends(require_permission("kg_manage")),
):
    """批量导入规范文档到知识库

    支持同时上传多个 docx/xlsx/pdf/doc/xls 文件。
    """
    if not files:
        raise HTTPException(status_code=400, detail="未选择文件")

    target_dir = _category_dir(category)

    target_dir.mkdir(parents=True, exist_ok=True)

    success_count = 0
    failed_count = 0
    results = []

    for file in files:
        if not file.filename:
            failed_count += 1
            results.append({"filename": "", "success": False, "error": "文件名为空"})
            continue

        # 根据扩展名判断类型（兼容浏览器未设置 content_type 的情况）
        ext_lower = Path(file.filename).suffix.lower()
        if ext_lower not in ALLOWED_IMPORT_EXTS:
            failed_count += 1
            results.append({
                "filename": file.filename,
                "success": False,
                "error": f"不支持的文件类型: {ext_lower}",
            })
            await file.close()
            continue

        base_name = _normalize_filename(Path(file.filename).stem)
        if not base_name:
            base_name = f"imported_{int(time.time())}_{success_count}"
        target_filename = f"{base_name}{ext_lower}"
        target_path = target_dir / target_filename

        # 处理重名
        counter = 1
        while target_path.exists():
            target_filename = f"{base_name}_{counter}{ext_lower}"
            target_path = target_dir / target_filename
            counter += 1

        try:
            target_path.write_bytes(await _read_document_limited(file))
            success_count += 1
            results.append({
                "filename": file.filename,
                "saved_as": target_filename,
                "success": True,
            })
        except Exception as e:
            failed_count += 1
            results.append({
                "filename": file.filename,
                "success": False,
                "error": str(e),
            })
        finally:
            await file.close()

    return {
        "success": True,
        "total": len(files),
        "success_count": success_count,
        "failed_count": failed_count,
        "results": results,
        "message": f"导入完成：成功 {success_count} 个，失败 {failed_count} 个",
    }


@router.post("/llm-query")
async def llm_query(
    req: LLMQueryRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """LLM 智能问答 — 规范条款、施工技术、监理流程等

    先检索知识库获取上下文，再调用 LLM 生成专业解答。
    """
    scope = req.knowledge_scope.strip() or "all"
    include_documents = scope != "knowledge-graph"
    include_graph = scope in {"all", "knowledge-graph"}
    document_category = None if scope in {"all", "documents"} else scope

    content_results: list[dict] = []
    if include_documents:
        content_results = [
            {**item, "source_type": "document"}
            for item in search_by_content(req.question, document_category)
        ]

    graph_results: list[dict] = []
    if include_graph:
        # 提取问题中的主体词，兼顾“脚手架有什么规范要求”一类自然问句。
        normalized = re.sub(
            r"(请问|请帮我|有哪些|有什么|如何|怎么|是否|相关|规范|要求|标准|流程|内容)",
            " ",
            req.question,
        )
        terms = [
            term
            for term in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_-]{2,}", normalized)
            if len(term) <= 32
        ][:6]
        if terms:
            conditions = []
            for term in terms:
                conditions.extend(
                    [
                        KGEntity.name.like(f"%{term}%"),
                        KGEntity.description.like(f"%{term}%"),
                    ]
                )
            graph_query = (
                select(KGEntity)
                .where(or_(*conditions))
                .order_by(KGEntity.created_at.desc())
                .limit(8)
            )
            entities = (await db.execute(graph_query)).scalars().all()
        elif scope == "knowledge-graph":
            entities = (
                await db.execute(
                    select(KGEntity).order_by(KGEntity.created_at.desc()).limit(8)
                )
            ).scalars().all()
        else:
            entities = []
        graph_results = [
            {
                "id": entity.id,
                "title": entity.name,
                "category": entity.entity_type,
                "source_file": entity.source_doc,
                "snippet": "；".join(
                    part
                    for part in [
                        entity.name,
                        entity.entity_type,
                        entity.description or "",
                    ]
                    if part
                ),
                "source_type": "knowledge_graph",
            }
            for entity in entities
        ]

    combined_results = [*content_results[:5], *graph_results[:5]]
    context = "\n".join(
        result.get("snippet", "")
        for result in combined_results
        if result.get("snippet")
    )

    if not req.use_llm:
        # 仅返回检索结果
        return {
            "question": req.question,
            "answer": None,
            "context_results": combined_results,
            "source": "search_only",
            "knowledge_scope": scope,
        }

    # 调用 LLM 生成解答
    try:
        answer = await ai_client.generate_regulation_answer(req.question, context)
        return {
            "question": req.question,
            "answer": strip_markdown(answer),
            "context_results": combined_results,
            "source": "llm",
            "knowledge_scope": scope,
        }
    except Exception as e:
        return {
            "question": req.question,
            "answer": None,
            "error": str(e),
            "context_results": combined_results,
            "source": "search_fallback",
            "knowledge_scope": scope,
        }
