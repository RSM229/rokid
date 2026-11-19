# -*- coding: utf-8 -*-
"""穗建通推送入口路由测试（凭证闸门 / 权限 / 数据组装 / 失败收敛，全 mock 不打真网）。

套路沿用 test_form_doc_flow.py：临时 SQLite → 播种项目/用户/记录 → TestClient +
真 token；推送层用 monkeypatch 把 szcj_push_client.push_payload / push_sup001
换成内存 stub，验证「取数 → 映射 → 发送」链路与回执格式。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_WORKDIR = tempfile.mkdtemp(prefix="gdjl_szcj_")
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
from app.models.work_record import (  # noqa: E402
    WorkRecord, WorkRecordType, WorkRecordStatus,
)

# 推送客户端的内存 stub：记录调用，返回可控结果
_PUSH_CALLS: list[dict] = []
_PUSH_RESULT = {"ok": True, "status": "INSERT", "http_status": 200, "attempts": 1}


async def _stub_push_payload(endpoint, payload, *, client=None, **kwargs):
    # 与真实 push_payload 同口径：裸 payload 统一包裹 {"data": ...}
    if "data" not in payload:
        payload = {"data": payload}
    _PUSH_CALLS.append({"endpoint": endpoint, "payload": payload})
    from app.services.szcj_push_client import PushResult
    return PushResult(**_PUSH_RESULT)


async def _stub_push_sup001(record, *, project_code, user_name, photo_urls,
                            created_time, updated_time, content_type=None, client=None):
    from app.services.szcj_sup001_mapper import map_sup001
    payload = map_sup001(record, project_code=project_code, user_name=user_name,
                         photo_urls=photo_urls, created_time=created_time,
                         updated_time=updated_time, content_type=content_type)
    return await _stub_push_payload("patrol_record", payload, client=client)


async def _seed() -> dict:
    await init_db()
    async with async_session() as db:
        project = Project(name="测试工程S", code="TEST-S")
        db.add(project)
        await db.flush()
        chief = User(
            username="szcj_chief", real_name="总监张三", role="chief_supervisor",
            password_hash="$2b$12$unused", project_id=project.id, is_active=True,
        )
        inspector = User(
            username="szcj_ins", real_name="监理员李四", role="inspector",
            password_hash="$2b$12$unused", project_id=project.id, is_active=True,
        )
        db.add_all([chief, inspector])
        await db.flush()
        # 权限合成读 project_members（主岗真源）：总监 → settings；监理员无。
        from app.models.project_member import ProjectMember

        db.add_all([
            ProjectMember(project_id=project.id, user_id=chief.id, role="chief_supervisor"),
            ProjectMember(project_id=project.id, user_id=inspector.id, role="inspector"),
        ])
        station = WorkRecord(
            record_type=WorkRecordType.STATION.value,
            status=WorkRecordStatus.SUBMITTED.value,
            creator_id=chief.id, project_id=project.id,
            record_date=datetime(2026, 11, 19).date(),
            section="3栋2层", station_process="混凝土浇筑",
            station_start_time=datetime(2026, 11, 19, 9, 30),
            station_end_time=datetime(2026, 11, 19, 12, 0),
            station_content="振捣均匀无漏振",
            inspection_photos=["/uploads/a.jpg"],
            created_at=datetime(2026, 11, 19, 12, 1),
        )
        db.add(station)
        await db.commit()
        return {
            "project_id": project.id, "chief_id": chief.id,
            "inspector_id": inspector.id, "station_id": station.id,
        }


@pytest.fixture(scope="module")
def ctx():
    ids = asyncio.run(_seed())

    def _auth(uid: int, role: str, permissions: list[str]):
        from app.routers.auth import create_access_token
        token = create_access_token(
            {"sub": str(uid), "role": role, "permissions": permissions}
        )
        return {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        yield {
            "client": client, "ids": ids,
            "chief_auth": lambda: _auth(
                ids["chief_id"], "chief_supervisor", ["settings", "work_record"]),
            "inspector_auth": lambda: _auth(ids["inspector_id"], "inspector", ["work_record"]),
        }


@pytest.fixture(autouse=True)
def _stub_push(monkeypatch):
    import app.routers.szcj_push as R

    _PUSH_CALLS.clear()
    _PUSH_RESULT.update({"ok": True, "status": "INSERT", "http_status": 200, "attempts": 1})
    monkeypatch.setattr(R, "push_payload", _stub_push_payload)
    monkeypatch.setattr(R, "push_sup001", _stub_push_sup001)
    yield
    # TestClient(app) 用的是同一 app 对象；monkeypatch 在 fixture teardown 自动还原


def _configure(monkeypatch, *, base="http://10.0.0.9:9000", app_code="123", token="tok",
               codes: dict | None = None):
    from app.config import settings

    monkeypatch.setattr(settings, "szcj_push_base_url", base, raising=False)
    monkeypatch.setattr(settings, "szcj_app_code", app_code, raising=False)
    monkeypatch.setattr(settings, "szcj_bearer_token", token, raising=False)
    monkeypatch.setattr(
        settings, "szcj_project_codes", codes or {}, raising=False)


# ---------------------------------------------------------------------------
# /status：按钮门控
# ---------------------------------------------------------------------------

def test_status_unconfigured(ctx):
    r = ctx["client"].get("/api/szcj/status", headers=ctx["chief_auth"]())
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["hint"].startswith("凭证未配置")
    assert {c["code"] for c in body["sup_codes"]} == {
        "001", "002", "003", "005", "006", "007", "012", "021"}


def test_status_configured_with_project_code(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].get("/api/szcj/status", headers=ctx["chief_auth"]())
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["project_code"] == "PRJ2026S"


# ---------------------------------------------------------------------------
# /one：权限 + 凭证 + 数据链路
# ---------------------------------------------------------------------------

def test_one_requires_settings_permission(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/one",
        json={"sup_code": "002", "record_id": ctx["ids"]["station_id"]},
        headers=ctx["inspector_auth"](),   # 监理员：无 settings
    )
    assert r.status_code == 403


def test_one_unmapped_project_rejected(ctx, monkeypatch):
    _configure(monkeypatch)   # 无项目编码映射
    r = ctx["client"].post(
        "/api/szcj/one",
        json={"sup_code": "002", "record_id": ctx["ids"]["station_id"]},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 400
    assert "未配置穗建通项目编码" in r.json()["detail"]


def test_one_pushes_station_record(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/one",
        json={"sup_code": "002", "record_id": ctx["ids"]["station_id"]},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["succeeded"] == 1 and body["failed"] == 0
    # 请求体关键字段：station_record 端点 + 旁站映射
    assert _PUSH_CALLS[0]["endpoint"] == "station_record"
    data = _PUSH_CALLS[0]["payload"]["data"]
    assert data["userName"] == "总监张三"
    assert data["keyPart"] == "3栋2层 混凝土浇筑"
    assert data["startTime"] == "2026-11-19 09:30:00"
    # 照片本地路径 → 公网 URL（PUBLIC_API_BASE_URL 拼接）
    expected_base = os.getenv("PUBLIC_API_BASE_URL", "http://localhost:8000").rstrip("/")
    assert data["photoUrls"] == [f"{expected_base}/uploads/a.jpg"]


def test_one_record_not_found(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/one", json={"sup_code": "002", "record_id": 999999},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 404


def test_one_invalid_sup_code(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/one", json={"sup_code": "004", "record_id": 1},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 422   # pydantic pattern 校验


def test_one_failure_collects_error(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    # 必填缺失：station_process 为空 + section 为空 → keyPart 缺 → 拒收不发送
    async def _break_keypart():
        async with async_session() as db:
            rec = await db.get(WorkRecord, ctx["ids"]["station_id"])
            rec.section = None
            rec.station_process = None
            await db.commit()
    asyncio.run(_break_keypart())
    r = ctx["client"].post(
        "/api/szcj/one",
        json={"sup_code": "002", "record_id": ctx["ids"]["station_id"]},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 0 and body["failed"] == 1
    assert "keyPart" in body["failures"][0]["error"]
    assert _PUSH_CALLS == []       # 数据不齐绝不发请求


# ---------------------------------------------------------------------------
# /range：批量
# ---------------------------------------------------------------------------

def test_range_pushes_bucket(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/range",
        json={"sup_codes": ["002"], "date_from": "2026-11-01", "date_to": "2026-11-30"},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 上一用例把 keyPart 清了 → 这条进失败清单（数据不齐，不是系统故障）
    assert body["total"] == 1
    assert body["failed"] == 1
    assert "keyPart" in body["failures"][0]["error"]

    # 恢复字段后再推 → 成功
    async def _restore():
        async with async_session() as db:
            rec = await db.get(WorkRecord, ctx["ids"]["station_id"])
            rec.section = "3栋2层"
            rec.station_process = "混凝土浇筑"
            await db.commit()
    asyncio.run(_restore())
    r2 = ctx["client"].post(
        "/api/szcj/range",
        json={"sup_codes": ["002"], "date_from": "2026-11-01", "date_to": "2026-11-30"},
        headers=ctx["chief_auth"](),
    )
    body2 = r2.json()
    assert body2["succeeded"] == 1 and body2["failed"] == 0
    assert body2["items"][0]["status"] == "INSERT"


def test_range_empty_window(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/range",
        json={"sup_codes": ["001"], "date_from": "2020-01-01", "date_to": "2020-01-02"},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0 and body["items"] == []
    assert "没有可推送的记录" in body["detail"]


def test_range_bad_date_rejected(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/range",
        json={"sup_codes": ["001"], "date_from": "2026/11/01"},
        headers=ctx["chief_auth"](),
    )
    assert r.status_code == 400


def test_range_requires_permission(ctx, monkeypatch):
    _configure(monkeypatch, codes={str(ctx["ids"]["project_id"]): "PRJ2026S"})
    r = ctx["client"].post(
        "/api/szcj/range", json={"sup_codes": ["001"]},
        headers=ctx["inspector_auth"](),
    )
    assert r.status_code == 403
