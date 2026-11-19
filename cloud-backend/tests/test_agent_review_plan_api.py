import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.routers import agent


def _plan():
    return {
        "schema_version": 1,
        "request_text": "整理验收记录",
        "evidence": [],
        "events": [{
            "id": "e1", "title": "钢筋验收", "location": "A区",
            "content": "钢筋验收", "date": "2026-10-26", "date_confirmed": True,
            "evidence_ids": [], "forms": ["acceptance_record"],
            "record_type": "验收", "record_type_confirmed": True,
        }],
        "unassigned_ids": [],
    }


class _ExpiringUser:
    def __init__(self):
        self.expired = False
        self.project_id = 7
        self.org_role = None

    @property
    def id(self):
        if self.expired:
            raise RuntimeError("ORM user id was expired after rollback")
        return 4

    def is_super_admin(self):
        return False


class _Result:
    def __init__(self, row):
        self.row = row

    def scalars(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return []


class _PlanDb:
    def __init__(self, user, rows=(), *, fail_insert=False, race_row=None):
        self.user = user
        self.rows = list(rows)
        self.pending = []
        self.fail_insert = fail_insert
        self.race_row = race_row
        self.query_count = 0

    async def get(self, _model, _id):
        return None

    async def execute(self, _statement):
        self.query_count += 1
        row = self.rows[0] if self.rows else None
        return _Result(row)

    def add(self, row):
        self.pending.append(row)

    async def commit(self):
        if self.fail_insert:
            raise IntegrityError("insert", {}, RuntimeError("unique session index"))

    async def refresh(self, _row):
        return None

    async def rollback(self):
        self.user.expired = True
        self.pending.clear()
        if self.race_row is not None:
            self.rows.append(self.race_row)


@pytest.fixture
def trace_recorder(monkeypatch):
    calls = []

    async def start_request(**kwargs):
        return kwargs["request_id"]

    async def finish_request(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(agent.agent_runtime, "start_request", start_request)
    monkeypatch.setattr(agent.agent_runtime, "finish_request", finish_request)
    async def no_media_check(*_args, **_kwargs):
        return None
    monkeypatch.setattr(agent, "_validate_review_plan_media", no_media_check)
    return calls


def test_active_plan_conflict_stays_409_after_rollback_expires_user(trace_recorder):
    user = _ExpiringUser()
    existing = SimpleNamespace(id=22, revision=7, status="draft")
    db = _PlanDb(user, [existing])
    request = agent.ReviewPlanCreate(
        conversation_id="conversation-1", plan=_plan(), request_id="request-save-1",
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(agent.create_review_plan(request, user, db))

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "ACTIVE_PLAN_EXISTS"
    assert raised.value.detail["plan_id"] == 22
    assert trace_recorder[-1]["user_id"] == 4


def test_unique_index_race_is_translated_to_recoverable_active_plan_conflict(trace_recorder):
    user = _ExpiringUser()
    db = _PlanDb(
        user, fail_insert=True,
        race_row=SimpleNamespace(id=23, revision=2, status="draft"),
    )
    request = agent.ReviewPlanCreate(
        conversation_id="conversation-1", plan=_plan(), request_id="request-save-2",
    )
    with pytest.raises(HTTPException) as raised:
        asyncio.run(agent.create_review_plan(request, user, db))

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "ACTIVE_PLAN_EXISTS"
    assert raised.value.detail["plan_id"] == 23
    # 2 → 4：5eb8089 在 create_review_plan 前置 _validate_chat_images
    # 与 record_conversation_message（用户轮次含 request_text 时落展示历史，
    # FakeDb 下失败被吞）各多一次 execute。原有 scope + existing 两次不变。
    assert db.query_count == 4
    assert trace_recorder[-1]["user_id"] == 4


def test_request_planner_degradation_preserves_time_facts_from_user_text(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise agent.model_gateway.ModelGatewayError("model unavailable")

    monkeypatch.setattr(agent.model_gateway, "chat_completion", unavailable)
    plan, model = asyncio.run(agent._generate_request_plan(
        message="今天上午10点钟到12点钟，在西楼A区做混凝土浇筑旁站",
        evidence=[{"kind": "image", "note": "水印未识别到时间"}],
        attachment_count=1,
    ))

    values = {item["field"]: item["value"] for item in plan["facts"]}
    assert model == ""
    assert plan["degraded"] is True
    assert values["start_time"] == "10:00"
    assert values["end_time"] == "12:00"
    assert values["record_date"]
    assert plan["write_allowed"] is False
