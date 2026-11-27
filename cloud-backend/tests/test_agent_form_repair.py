"""计划字段格式修复循环（2026-11-27）。"""
import asyncio
import json

from app.services import agent_form_repair, model_gateway


def _plan(start_time: str) -> dict:
    return {
        "schema_version": 1,
        "request_text": "记录一次巡视",
        "evidence": [{"id": "e1", "media_id": 1, "image": "https://example.com/a.jpg"}],
        "events": [{
            "id": "item_1",
            "title": "内围排水沟巡视",
            "location": "",
            "content": "沟槽开挖",
            "date": "2026-11-27",
            "evidence_ids": ["e1"],
            "forms": ["gd_b_214_xunshi"],
            "record_type": "巡视",
            "record_type_confirmed": True,
            "record_type_confidence": 1,
            "fields": {"gd_b_214_xunshi": {
                "section": {"value": "内围排水沟", "source": "user_message", "status": "user_stated_candidate"},
                "start_time": {"value": start_time, "source": "user_message", "status": "user_stated_candidate"},
                "end_time": {"value": "2026-11-27 18:10", "source": "user_message", "status": "user_stated_candidate"},
                "problems": {"value": "未发现问题", "source": "user_message", "status": "user_stated_candidate"},
            }},
        }],
        "unassigned_ids": [],
    }


def test_collect_invalid_finds_bad_datetime():
    problems = agent_form_repair.collect_invalid(_plan("傍晚那会儿"))
    assert any(item["canonical"] == "start_time" for item in problems)


def test_model_repairs_value_and_plan_passes(monkeypatch):
    async def fake_chat(messages, **kwargs):
        return json.dumps({
            "repairs": [{"event": 1, "canonical": "start_time", "value": "2026-11-27 17:57"}],
        }, ensure_ascii=False), None

    monkeypatch.setattr(model_gateway, "chat_completion", fake_chat)

    async def scenario():
        plan = _plan("傍晚那会儿")
        repaired, issues = await agent_form_repair.repair_plan(plan)
        assert repaired["events"][0]["fields"]["gd_b_214_xunshi"]["start_time"]["value"] == "2026-11-27 17:57"
        assert repaired["events"][0]["fields"]["gd_b_214_xunshi"]["start_time"]["source"] == "model_repair"
        assert not [issue for issue in issues if "填写无效" in issue]

    asyncio.run(scenario())


def test_no_repair_when_no_invalid_fields(monkeypatch):
    calls = {"n": 0}

    async def fake_chat(messages, **kwargs):
        calls["n"] += 1
        return json.dumps({"repairs": []}), None

    monkeypatch.setattr(model_gateway, "chat_completion", fake_chat)

    async def scenario():
        plan = _plan("2026-11-27 17:57")
        assert agent_form_repair.collect_invalid(plan) == []
        _, issues = await agent_form_repair.repair_plan(plan)
        assert calls["n"] == 0

    asyncio.run(scenario())
