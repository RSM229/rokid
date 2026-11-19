# -*- coding: utf-8 -*-
"""穗建通推送客户端测试：凭证闸门 / 重试 / 幂等 / 业务拒绝（全部 mock HTTP，不打真网）。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.config import settings
from app.services.szcj_push_client import (
    PushNotConfiguredError,
    PushResult,
    project_code_for,
    push_payload,
    push_sup001,
)


def _configure(monkeypatch: pytest.MonkeyPatch, *, base="http://10.0.0.1:9000", app_code="123", token="tok"):
    monkeypatch.setattr(settings, "szcj_push_base_url", base, raising=False)
    monkeypatch.setattr(settings, "szcj_app_code", app_code, raising=False)
    monkeypatch.setattr(settings, "szcj_bearer_token", token, raising=False)


class _StubTransport(httpx.AsyncBaseTransport):
    """按预设脚本回放响应：list of (status_code, json_body) 或 'network' 抛网络错。"""

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else (200, {"code": 0, "data": {"status": "INSERT"}})
        if step == "network":
            raise httpx.ConnectError("connection refused", request=request)
        status, body = step
        return httpx.Response(status, json=body)


def _run(coro):
    return asyncio.run(coro)


# ---------- 凭证闸门 ----------

def test_missing_credentials_raises(monkeypatch):
    monkeypatch.setattr(settings, "szcj_push_base_url", "", raising=False)
    monkeypatch.setattr(settings, "szcj_app_code", "", raising=False)
    monkeypatch.setattr(settings, "szcj_bearer_token", "", raising=False)
    with pytest.raises(PushNotConfiguredError) as ei:
        _run(push_payload("patrol_record", {"data": {}}))
    assert "SZCJ_PUSH_BASE_URL" in str(ei.value)


def test_partial_credentials_raises(monkeypatch):
    _configure(monkeypatch, base="", app_code="123", token="tok")
    with pytest.raises(PushNotConfiguredError):
        _run(push_payload("patrol_record", {"data": {}}))


# ---------- 成功 / 幂等 ----------

def test_success_insert(monkeypatch):
    _configure(monkeypatch)
    transport = _StubTransport([(200, {"code": 0, "data": {"status": "INSERT", "srcId": "157"}})])
    client = httpx.AsyncClient(transport=transport)
    result = _run(push_payload("patrol_record", {"data": {"id": 157}}, client=client))
    _run(client.aclose())
    assert result.ok and result.status == "INSERT" and result.attempts == 1
    # 请求头契约：Bearer / appCode / X-Data-Source
    req = transport.requests[0]
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.headers["appCode"] == "123"
    assert req.headers["X-Data-Source"] == "jlt"
    assert str(req.url).endswith("/tenantgz/e-api/ods/push/patrol_record")


def test_duplicate_counts_as_success(monkeypatch):
    _configure(monkeypatch)
    transport = _StubTransport([(200, {"code": 0, "data": {"status": "DUPLICATE_INSERT"}})])
    client = httpx.AsyncClient(transport=transport)
    result = _run(push_payload("patrol_record", {"data": {"id": 1}}, client=client))
    _run(client.aclose())
    assert result.ok and result.status == "DUPLICATE_INSERT"


# ---------- 重试 ----------

def test_retry_on_5xx_then_success(monkeypatch):
    _configure(monkeypatch)
    transport = _StubTransport([
        (503, {"msg": "busy"}),
        (200, {"code": 0, "data": {"status": "INSERT"}}),
    ])
    client = httpx.AsyncClient(transport=transport)
    result = _run(push_payload("patrol_record", {"data": {"id": 1}}, client=client))
    _run(client.aclose())
    assert result.ok and result.attempts == 2


def test_retry_exhausted(monkeypatch):
    _configure(monkeypatch)
    transport = _StubTransport(["network", "network", "network", "network"])
    client = httpx.AsyncClient(transport=transport)
    result = _run(push_payload("patrol_record", {"data": {"id": 1}}, client=client))
    _run(client.aclose())
    assert not result.ok and result.attempts == 4
    assert "网络错误" in result.error


def test_4xx_no_retry(monkeypatch):
    _configure(monkeypatch)
    transport = _StubTransport([(401, {"msg": "bad token"})])
    client = httpx.AsyncClient(transport=transport)
    result = _run(push_payload("patrol_record", {"data": {"id": 1}}, client=client))
    _run(client.aclose())
    assert not result.ok and result.attempts == 1 and result.http_status == 401


def test_business_reject_no_retry(monkeypatch):
    _configure(monkeypatch)
    transport = _StubTransport([(200, {"code": 1001, "msg": "字段非法"})])
    client = httpx.AsyncClient(transport=transport)
    result = _run(push_payload("patrol_record", {"data": {"id": 1}}, client=client))
    _run(client.aclose())
    assert not result.ok and result.attempts == 1
    assert "1001" in result.error


# ---------- 一键推送（映射+发送） ----------

def _sample_record() -> dict:
    return {
        "id": 157, "record_date": "2026-11-19", "inspection_start_time": "09:30",
        "section": "大兴路", "work_content": "安全文明施工情况正常",
        "status": "已提交", "problems": "", "treatment": "",
    }


def test_push_sup001_end_to_end(monkeypatch):
    _configure(monkeypatch)
    transport = _StubTransport([(200, {"code": 0, "data": {"status": "INSERT", "srcId": "157"}})])
    client = httpx.AsyncClient(transport=transport)
    result = _run(push_sup001(
        _sample_record(),
        project_code="PRJ20261119001", user_name="csm", photo_urls=["https://x/y.jpg"],
        created_time="2026-11-19 09:31:00", updated_time="2026-11-19 09:31:00",
        client=client,
    ))
    _run(client.aclose())
    assert result.ok
    body = json.loads(transport.requests[0].content)
    assert body["data"]["projectCode"] == "PRJ20261119001"
    assert body["data"]["userName"] == "csm"
    assert body["data"]["contentType"] == "安全"


def test_push_sup001_missing_required_propagates(monkeypatch):
    _configure(monkeypatch)
    rec = _sample_record()
    rec["work_content"] = ""   # sceneDesc 将缺失 → 必填校验应抛
    from app.services.szcj_sup001_mapper import MissingRequiredError
    with pytest.raises(MissingRequiredError):
        _run(push_sup001(
            rec, project_code="PRJ1", user_name="csm", photo_urls=[],
            created_time="2026-11-19 09:31:00", updated_time="2026-11-19 09:31:00",
        ))


# ---------- 项目编码映射 ----------

def test_project_code_mapping(monkeypatch):
    monkeypatch.setattr(settings, "szcj_project_codes", {"1": "PRJ2026A", "15": "PRJ2026B"}, raising=False)
    assert project_code_for(15) == "PRJ2026B"
    assert project_code_for(1) == "PRJ2026A"
    assert project_code_for(999) == ""      # 未映射返回空
    assert project_code_for(None) == ""
