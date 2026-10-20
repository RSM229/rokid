"""业务签认位置、岗位与提交校验。签认必须使用本人签章和密码。"""
from fastapi import HTTPException
from sqlalchemy import select
from app.models.document_signoff import DocumentSignoff
from app.models.project_member import ProjectMember

SLOTS = {
    "inspector": ("检查/记录人", "bottom-left"),
    "reviewer": ("复核人", "bottom-right"),
    "supervisor": ("专业监理工程师", "bottom-left"),
    "chief": ("总监理工程师或代表", "bottom-right"),
    "acceptor": ("验收人", "bottom-left"),
    "project_seal": ("项目监理机构用章", "top-right"),
}


def required_slots_for(doc):
    # 用户明确选择保留既有表式的必签要求（2026-10-12）。
    if doc.doc_type == "验收记录":
        return {"acceptor"}
    if doc.doc_type in ("旁站记录", "巡视记录"):
        return {"inspector"}
    if doc.doc_type == "平行检查记录":
        return {"reviewer"}  # 既有专业监理工程师签认，落到复核位。
    if doc.doc_type == "监理日志":
        return {"supervisor"}  # 原表式记录人必签，总监仍可选。
    if "通知单" in doc.doc_type:
        return {"supervisor", "chief"}
    return set()


def slots_for(doc):
    kind = doc.doc_type
    if kind == "验收记录":
        return ["acceptor"]
    if kind == "平行检查记录":
        return ["inspector", "reviewer"]
    if kind in ("巡视记录", "旁站记录"):
        return ["inspector"]
    if kind == "监理日志":
        return ["supervisor", "chief"]
    if "通知单" in kind:
        return ["supervisor", "chief", "project_seal"]
    return ["supervisor", "chief"]


async def current_signoffs(db, doc):
    return list((await db.execute(select(DocumentSignoff).where(
        DocumentSignoff.document_id == doc.id,
        DocumentSignoff.revision == (doc.content_revision or 1),
    ).order_by(DocumentSignoff.signed_at))).scalars().all())


async def assert_required_signoffs(db, doc):
    signed = {s.slot for s in await current_signoffs(db, doc)}
    missing = required_slots_for(doc) - signed
    if missing:
        raise HTTPException(400, "请先完成必签项：" + "、".join(SLOTS[s][0] for s in sorted(missing)))


async def assert_slot_authorized(db, doc, user, slot):
    if slot not in slots_for(doc):
        raise HTTPException(400, "该文书没有此签认位置")
    member = (await db.execute(select(ProjectMember).where(
        ProjectMember.project_id == doc.project_id,
        ProjectMember.user_id == user.id,
        ProjectMember.left_at.is_(None),
    ))).scalar_one_or_none()
    roles = ({member.role, *(member.extra_roles or [])} if member else set())
    senior = {"chief_supervisor", "deputy_chief"}
    professional = senior | {"special_supervisor"}
    if slot == "chief" and not roles.intersection(senior):
        raise HTTPException(403, "总监签认须由本项目总监或总监代表操作")
    if slot == "supervisor" and not roles.intersection(professional):
        raise HTTPException(403, "专业签认须由本项目专业监理工程师或总监操作")
    if slot == "reviewer":
        if not roles.intersection(professional):
            raise HTTPException(403, "复核须由本项目专业监理工程师或总监操作")
        signed = await current_signoffs(db, doc)
        if any(s.slot == "inspector" and s.signer_id == user.id for s in signed):
            raise HTTPException(403, "检查人与复核人不能是同一人")
    if slot in ("inspector", "acceptor") and not member:
        raise HTTPException(403, "签认人须是本项目在岗成员")
    permission = "acceptance" if slot == "acceptor" else "station" if doc.doc_type == "旁站记录" else "inspection"
    if slot in ("inspector", "acceptor") and not user.has_permission(permission):
        raise HTTPException(403, "当前项目岗位无权签认该类记录")
    if slot == "project_seal" and not user.has_permission("document_approve"):
        raise HTTPException(403, "项目用章需要文书签发权限")

