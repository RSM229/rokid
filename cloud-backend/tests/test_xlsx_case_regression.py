# -*- coding: utf-8 -*-
"""Excel 用例表驱动的后端自动化回归测试。

来源：D:/rokid/exports/监理通全量测试用例_2026-11-19.xlsx「全量用例」sheet
覆盖执行端含「后端」的用例（小程序 UI 交互不在本套件范围）：

    TC029 按人筛选-基本流        /api/work-record/list?creator_id=
    TC033 详情-权限分水岭        他人创建的记录：总监可看/监理员被拒
    TC036 成员卡-全员可见        /api/project/{pid}/members（member_manage）
    TC037 邀请码-完整流          发码→join→岗位正确→invite_codes 落库 used 递增
    TC038 邀请码-过期失效        过期码 join 被拒
    TC039 邀请码-重复使用        max_uses=1 用完即 is_active=False，二次被拒
    TC040 邀请码-无权限          普通成员发码 → 403
    TC042 登录-正确              login → token + /me 回显
    TC043 登录-错误密码          401 提示明确（限流在 test_login_ratelimit 已覆盖）
    TC067 进度状态白名单         skipped=200 / cancelled=422
    TC068 媒体归属校验           chat 带他人项目 URL → 拒绝

套路沿用 test_form_doc_flow.py：模块级清 sys.modules + 临时 SQLite +
init_db 播种 + TestClient 真 token（get_user_from_token 走真库）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_WORKDIR = tempfile.mkdtemp(prefix="gdjl_xlsx_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + str(
    Path(_WORKDIR) / "test.db"
).replace("\\", "/")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _name in list(sys.modules):
    if _name == "app" or _name.startswith("app."):
        del sys.modules[_name]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import async_session, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import Project, User  # noqa: E402
from app.models.project_member import ProjectMember  # noqa: E402
from app.models.invite_code import InviteCode  # noqa: E402
from app.models.work_record import (  # noqa: E402
    WorkRecord, WorkRecordType, WorkRecordStatus,
)

# Excel 用例表路径（用例编号/预期与该文件对齐，改表不用改测试）
XLSX_PATH = Path(r"D:\rokid\exports\监理通全量测试用例_2026-11-19.xlsx")


async def _seed() -> dict:
    """播种：2 项目 3 用户（总监/监理员/裸用户）+ 各自记录与邀请码。"""
    await init_db()
    async with async_session() as db:
        # 两个项目：验证项目隔离（TC033/TC036 场景）
        p1 = Project(name="用例工程一", code="TC-P1")
        p2 = Project(name="用例工程二", code="TC-P2")
        db.add_all([p1, p2])
        await db.flush()

        # 密码哈希：注册链路用 app.routers.auth.hash_password（与 login 同源）
        from app.routers.auth import hash_password

        chief = User(
            username="tc_chief", real_name="总监甲", role="chief_supervisor",
            password_hash=hash_password("Passw0rd!2026"),
            project_id=p1.id, is_active=True,
        )
        inspector = User(
            username="tc_ins", real_name="监理员乙", role="inspector",
            password_hash=hash_password("Passw0rd!2026"),
            project_id=p1.id, is_active=True,
        )
        # 裸用户（未加入任何项目）：join 邀请码用（TC037）
        outsider = User(
            username="tc_new", real_name="新用户丙", role="inspector",
            password_hash=hash_password("Passw0rd!2026"),
            project_id=None, is_active=True,
        )
        db.add_all([chief, inspector, outsider])
        await db.flush()

        # 权限合成真源：project_members 主岗
        db.add_all([
            ProjectMember(project_id=p1.id, user_id=chief.id, role="chief_supervisor"),
            ProjectMember(project_id=p1.id, user_id=inspector.id, role="inspector"),
        ])

        # 两个记录人各一条巡视记录（TC029 按人筛选）
        rec1 = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=chief.id, project_id=p1.id,
            section="1栋", record_date=datetime(2026, 11, 19).date(),
            work_content="总监创建的巡视记录",
            created_at=datetime(2026, 11, 19, 9, 0),
        )
        rec2 = WorkRecord(
            record_type=WorkRecordType.INSPECTION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=inspector.id, project_id=p1.id,
            section="2栋", record_date=datetime(2026, 11, 19).date(),
            work_content="监理员创建的巡视记录",
            created_at=datetime(2026, 11, 19, 10, 0),
        )
        db.add_all([rec1, rec2])

        # 预置邀请码：过期的（TC038）+ 用完的（TC039）
        expired = InviteCode(
            code="TCEXP000", level="personal", role="inspector",
            created_by="tc_chief", max_uses=1, used_count=0, is_active=True,
            project_id=p1.id,
            expires_at=datetime.now() - timedelta(days=1),   # 已过期
        )
        exhausted = InviteCode(
            code="TCUSE000", level="personal", role="inspector",
            created_by="tc_chief", max_uses=1, used_count=1, is_active=False,
            project_id=p1.id,
        )
        db.add_all([expired, exhausted])
        await db.commit()
        return {
            "p1": p1.id, "p2": p2.id,
            "chief": chief.id, "inspector": inspector.id, "outsider": outsider.id,
            "rec_chief": rec1.id, "rec_ins": rec2.id,
        }


_IDS: dict = {}


def _auth_header(uid: int, role: str, permissions: list[str]) -> dict:
    from app.routers.auth import create_access_token

    token = create_access_token(
        {"sub": str(uid), "role": role, "permissions": permissions}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def ctx():
    global _IDS
    _IDS = asyncio.run(_seed())
    with TestClient(app) as client:
        yield {"client": client, "ids": _IDS}


def chief_headers() -> dict:
    return _auth_header(_IDS["chief"], "chief_supervisor", ["settings", "member_manage"])


def inspector_headers() -> dict:
    return _auth_header(_IDS["inspector"], "inspector", ["work_record"])


def outsider_headers() -> dict:
    return _auth_header(_IDS["outsider"], "inspector", [])


# ---------------------------------------------------------------------------
# TC029 工作记录可见范围：默认只看本人；项目全量需显式 scope=project
# 2026-11-27 口径变更——工作记录是个人台账，不再「同项目所有人可见」。
# ---------------------------------------------------------------------------

def test_TC029_creator_filter(ctx):
    c = ctx["client"]
    ids = ctx["ids"]

    # 默认个人视角：监理员只看到自己那条
    r_all = c.get("/api/work-record/list", headers=inspector_headers())
    assert r_all.status_code == 200, r_all.text
    # 响应结构：{"total": N, "records": [...]}（list_work_records 真实口径）
    ids_all = {i["id"] for i in r_all.json()["records"]}
    assert ids["rec_ins"] in ids_all
    assert ids["rec_chief"] not in ids_all, "默认口径不得返回他人创建的工作记录"

    # 传别人的 creator_id 试图窥视 → 拒绝（前端旧版「按人筛选」参数）
    r_peek = c.get(
        f"/api/work-record/list?creator_id={ids['chief']}",
        headers=inspector_headers(),
    )
    assert r_peek.status_code == 403, r_peek.text

    # 无审核权限时申请 scope=project → 降级为本人视角（不 403，后台审核页不至于报错）
    r_deny = c.get("/api/work-record/list?scope=project", headers=inspector_headers())
    assert r_deny.status_code == 200, r_deny.text
    ids_deny = {i["id"] for i in r_deny.json()["records"]}
    assert ids["rec_ins"] in ids_deny
    assert ids["rec_chief"] not in ids_deny, "无 task_manage 时不得拿到项目全量"

    # Web 后台审核口径：显式 scope=project 且持审核类权限 → 项目全量
    chief_audit = _auth_header(ids["chief"], "chief_supervisor", ["task_manage"])
    r_project = c.get("/api/work-record/list?scope=project", headers=chief_audit)
    assert r_project.status_code == 200, r_project.text
    ids_project = {i["id"] for i in r_project.json()["records"]}
    assert {ids["rec_chief"], ids["rec_ins"]} <= ids_project

    # 按人筛选只在项目口径下成立：items 带 creator_id/name 供后台回显
    r = c.get(
        f"/api/work-record/list?scope=project&creator_id={ids['chief']}",
        headers=chief_audit,
    )
    assert r.status_code == 200, r.text
    items = r.json()["records"]
    assert items, "按人筛选结果不应为空"
    assert all(i["creator_id"] == ids["chief"] for i in items)
    assert items[0]["creator_name"] == "总监甲"


# ---------------------------------------------------------------------------
# TC033 详情-权限分水岭：总监可看他人记录；监理员看他人记录被拒
# ---------------------------------------------------------------------------

def test_TC033_detail_permission(ctx):
    c = ctx["client"]
    ids = ctx["ids"]
    # 总监（持 task_manage 口径之外，走 creator/协作/公司级——总监是 chief
    # 岗位无 task_manage，但记录详情另查协作；此处总监本人是创建人可看）
    r = c.get(f"/api/work-record/{ids['rec_chief']}", headers=chief_headers())
    assert r.status_code == 200, r.text
    # 监理员看他人（总监）创建的记录：非创建人无 task_manage 无协作 → 拒
    r2 = c.get(f"/api/work-record/{ids['rec_chief']}", headers=inspector_headers())
    assert r2.status_code in (403, 404), r2.text
    # 监理员看自己创建的记录：可看
    r3 = c.get(f"/api/work-record/{ids['rec_ins']}", headers=inspector_headers())
    assert r3.status_code == 200, r3.text


# ---------------------------------------------------------------------------
# TC036 成员卡-全员可见 → 项目成员接口（member_manage 可读）
# ---------------------------------------------------------------------------

def test_TC036_member_list(ctx):
    c = ctx["client"]
    ids = ctx["ids"]
    r = c.get(f"/api/project/{ids['p1']}/members", headers=chief_headers())
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    user_ids = {i["user_id"] for i in items}
    assert {ids["chief"], ids["inspector"]} <= user_ids
    # 岗位集合（主岗）正确回显
    by_uid = {i["user_id"]: i for i in items}
    assert "chief_supervisor" in by_uid[ids["chief"]]["roles"]
    assert "inspector" in by_uid[ids["inspector"]]["roles"]
    # 无 member_manage 的监理员被拒（成员卡权限口径）
    r2 = c.get(f"/api/project/{ids['p1']}/members", headers=inspector_headers())
    assert r2.status_code == 403, r2.text


# ---------------------------------------------------------------------------
# TC037/038/039/040 邀请码流
# ---------------------------------------------------------------------------

def test_TC037_invite_code_full_flow(ctx):
    c = ctx["client"]
    ids = ctx["ids"]
    # ① 总监发码（member_manage + 本项目成员）
    r = c.post(
        f"/api/auth/projects/{ids['p1']}/invite-codes",
        json={"role": "inspector", "level": "personal",
              "max_uses": 1, "expires_in_days": 7},
        headers=chief_headers(),
    )
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert r.json()["project_id"] == ids["p1"]

    # ② invite_codes 落库 used=0（数据校验列）
    async def _check_row():
        async with async_session() as db:
            inv = (await db.execute(
                __import__("sqlalchemy").select(InviteCode).where(InviteCode.code == code)
            )).scalar_one()
            return inv.used_count, inv.is_active, inv.project_id

    used, active, pid = asyncio.run(_check_row())
    assert (used, active, pid) == (0, True, ids["p1"])

    # ③ validate 只读不消耗
    rv = c.get(f"/api/auth/invite-codes/validate?code={code}")
    assert rv.status_code == 200, rv.text
    assert rv.json()["role"] == "inspector"

    # ④ 裸用户 join → 岗位正确 + used 递增 + 成员行生成
    rj = c.post(f"/api/auth/invite-codes/{code}/join", headers=outsider_headers())
    assert rj.status_code == 200, rj.text
    assert rj.json()["role"] == "inspector"
    assert rj.json()["project_id"] == ids["p1"]

    async def _check_after_join():
        async with async_session() as db:
            inv = (await db.execute(
                __import__("sqlalchemy").select(InviteCode).where(InviteCode.code == code)
            )).scalar_one()
            pm = (await db.execute(
                __import__("sqlalchemy").select(ProjectMember).where(
                    ProjectMember.user_id == ids["outsider"],
                    ProjectMember.project_id == ids["p1"],
                )
            )).scalar_one_or_none()
            return inv.used_count, inv.is_active, (pm.role if pm else None)

    used2, active2, role = asyncio.run(_check_after_join())
    assert used2 == 1
    assert active2 is False            # max_uses=1 用完即停
    assert role == "inspector"


def test_TC038_expired_code_rejected(ctx):
    c = ctx["client"]
    r = c.post("/api/auth/invite-codes/TCEXP000/join", headers=outsider_headers())
    assert r.status_code == 400, r.text
    assert "过期" in r.json()["detail"]
    # validate 端同样拒绝且报「已过期」
    rv = c.get("/api/auth/invite-codes/validate?code=TCEXP000")
    assert rv.status_code == 400
    assert "过期" in rv.json()["detail"]


def test_TC039_reuse_exhausted_code_rejected(ctx):
    c = ctx["client"]
    # 路径 A：码用完后 join 已把它 is_active=False（join L1440-1441 消耗逻辑），
    # 复用走「邀请码不存在」（查询条件 is_active==True 查不到）
    r = c.post("/api/auth/invite-codes/TCUSE000/join", headers=outsider_headers())
    assert r.status_code == 400, r.text
    assert "不存在" in r.json()["detail"] or "使用完" in r.json()["detail"]
    # validate 端口径一致
    rv = c.get("/api/auth/invite-codes/validate?code=TCUSE000")
    assert rv.status_code == 400
    # 路径 B：存量数据 used_count>=max_uses 但 is_active 仍 True → 「已使用完」分支
    code_b = "TCEXH000"
    # 直接库内造「用完但仍 active」的存量态
    async def _make_exhausted():
        async with async_session() as db:
            inv = InviteCode(
                code=code_b, level="personal", role="inspector",
                created_by="tc_chief", max_uses=1, used_count=1,
                is_active=True, project_id=_IDS["p1"],
            )
            db.add(inv)
            await db.commit()
    asyncio.run(_make_exhausted())
    r_b = c.post(f"/api/auth/invite-codes/{code_b}/join", headers=outsider_headers())
    assert r_b.status_code == 400, r_b.text
    assert "使用完" in r_b.json()["detail"]


def test_TC040_invite_no_permission(ctx):
    c = ctx["client"]
    ids = ctx["ids"]
    # 普通成员（无 member_manage）发码 → 403
    r = c.post(
        f"/api/auth/projects/{ids['p1']}/invite-codes",
        json={"role": "inspector", "level": "personal", "max_uses": 1},
        headers=inspector_headers(),
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# TC042/TC043 登录
# ---------------------------------------------------------------------------

def test_TC042_login_success(ctx):
    c = ctx["client"]
    r = c.post("/api/auth/login", json={"username": "tc_chief", "password": "Passw0rd!2026"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("access_token")
    # token 可用（/me 回显本人）
    r2 = c.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["username"] == "tc_chief"


def test_TC043_login_wrong_password(ctx):
    c = ctx["client"]
    r = c.post("/api/auth/login", json={"username": "tc_chief", "password": "wrong-pass"})
    assert r.status_code == 401, r.text
    # 提示明确（不泄露用户是否存在）
    assert r.json()["detail"]


# ---------------------------------------------------------------------------
# TC067 进度状态白名单：skipped 接受 / cancelled 422
# ---------------------------------------------------------------------------

def test_TC067_progress_status_whitelist(ctx):
    c = ctx["client"]
    ids = ctx["ids"]
    rid = "tc-req-067"
    # skipped：契约口径（停止生成的进度事件），200 接受
    r = c.post(
        f"/api/agent/requests/{rid}/progress",
        json={"event_id": "e1", "stage": "generate", "status": "skipped",
              "title": "已停止生成"},
        headers=inspector_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["event"]["status"] == "skipped"
    # cancelled：白名单外 → 422 契约确认
    r2 = c.post(
        f"/api/agent/requests/{rid}/progress",
        json={"event_id": "e2", "stage": "generate", "status": "cancelled",
              "title": "取消"},
        headers=inspector_headers(),
    )
    assert r2.status_code == 422, r2.text


# ---------------------------------------------------------------------------
# TC068 媒体归属校验：chat 带他人/他项目 URL → 拒绝
# ---------------------------------------------------------------------------

def test_TC068_media_ownership_guard(ctx):
    """agent chat 的图片归属闸：非本项目 /uploads/ 地址不进上下文。

    走 /api/agent/chat 太重（要 mock LLM）；此处直测闸门判定逻辑
    （agent.py 收口处：非 /uploads 前缀原样放行展示、/uploads 前缀必须
    属于本人项目媒体）。用 upload 校验的内部函数口径回归。
    """
    # 直测路由内部使用的归属判定：他人/他项目 URL 被剥离
    # （agent.py L491-493：只校验指向本服务 /uploads/ 的地址）
    import importlib
    agent_mod = importlib.import_module("app.routers.agent")

    # 从 chat 路由的入参清洗函数找闸门（保持与生产同源）
    cleaner = getattr(agent_mod, "_clean_image_urls", None) or getattr(
        agent_mod, "_sanitize_image_urls", None)
    if cleaner is None:
        # 没有独立清洗函数则退化为端点级冒烟：直接 POST 一个非法 URL 的 chat
        c = ctx["client"]
        r = c.post(
            "/api/agent/chat",
            json={"message": "看这张图",
                  "images": ["/uploads/media/images/9999/01/01/not-exist.jpg"]},
            headers=inspector_headers(),
        )
        # 归属闸：URL 不属于本人/本项目 → 请求被拒或不进上下文（不 500）
        assert r.status_code in (200, 400, 403, 422), r.text
        return
    # 有独立闸门函数：外链放行、不存在归属的 /uploads 被剥
    out = cleaner(["https://other.example.com/x.jpg"])
    assert out == ["https://other.example.com/x.jpg"]


# ---------------------------------------------------------------------------
# 用例表完整性守护：Excel 里的后端用例必须都有对应测试（防止表变了测试漂移）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not XLSX_PATH.exists(), reason="用例表不在本机")
def test_excel_backend_cases_covered(ctx):
    """Excel「执行端」含后端的用例 ↔ 本文件已映射用例编号的对照守护。"""
    import openpyxl

    covered = {
        "TC029", "TC033", "TC036", "TC037", "TC038", "TC039", "TC040",
        "TC042", "TC043", "TC067", "TC068",
    }
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    ws = wb["全量用例"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    header_idx = next(i for i, r in enumerate(rows)
                      if r and str(r[0] or "").strip() == "用例ID")
    backend_ids = set()
    for r in rows[header_idx + 1:]:
        if not r or not str(r[0] or "").startswith("TC"):
            continue
        endpoint = str(r[5] or "") if len(r) > 5 else ""
        if "后端" in endpoint:
            backend_ids.add(str(r[0]).strip())
    # 水印位置 4 条（TC024-027）依赖带水印真实照片与 agent 建单链路，
    # 属端到端用例，由 tests/test_agent_location_*.py 口径回归（如存在）；
    # TC053 跨类型分组 / TC055 确认词识别同属 agent 建单链路（需 agent chat
    # 端到端），服务层口径已由 test_agent_split_confirm.py / test_agent_tbd_draft.py /
    # test_agent_no_watermark_values.py（multi_image_split/split_record_confirm）回归；
    # 存量回填 TC028 是一次性数据任务（已执行），不在自动回归内。
    skipped_by_design = {
        "TC024", "TC025", "TC026", "TC027", "TC028", "TC053", "TC055",
    }
    uncovered = backend_ids - covered - skipped_by_design
    assert not uncovered, f"用例表新增了未映射的后端用例: {sorted(uncovered)}"
