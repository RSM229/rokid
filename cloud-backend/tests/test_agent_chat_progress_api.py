import asyncio

from app.routers import agent


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _ScopeDb:
    """只满足作用域解析里 project_members 查询的最小假 session。"""

    async def execute(self, *_args, **_kwargs):
        return _EmptyResult()


def _fake_user(project_id=3):
    return type("User", (), {
        "id": 7, "project_id": project_id, "org_role": None,
        "is_super_admin": lambda self: False,
    })()


def test_agent_chat_progress_completion_includes_single_card_without_error(monkeypatch):
    calls = {"events": [], "finished": []}

    async def start_request(**kwargs):
        return kwargs["request_id"]

    async def run_chat(*_args, **_kwargs):
        return {
            "kind": "action",
            "tool": "create_work_record",
            "reply": "记录已创建",
            "card": {"kind": "work_record", "id": 41},
            "cards": [],
            "need_more": [],
            "form": None,
        }

    async def append_progress_event(**kwargs):
        calls["events"].append(kwargs)

    async def finish_request(**kwargs):
        calls["finished"].append(kwargs)

    monkeypatch.setattr(agent.agent_runtime, "start_request", start_request)
    monkeypatch.setattr(agent.agent_runtime, "append_progress_event", append_progress_event)
    monkeypatch.setattr(agent.agent_runtime, "finish_request", finish_request)
    monkeypatch.setattr(agent, "_agent_chat_impl", run_chat)

    user = _fake_user(project_id=3)
    result = asyncio.run(agent.agent_chat(
        agent.AgentChatRequest(message="创建巡视记录", request_id="chat-progress-1"),
        user=user,
        db=_ScopeDb(),
    ))

    assert result["request_id"] == "chat-progress-1"
    assert result["card"]["id"] == 41
    completion = next(event for event in calls["events"] if event["stage"] == "agent_result")
    assert completion["detail"]["card_ids"] == [41]
    assert calls["finished"][0]["created_record_refs"] == [
        {"kind": "work_record", "id": 41},
    ]


def test_agent_request_progress_only_returns_cursor_payload(monkeypatch):
    row = type("Trace", (), {"project_id": 3})()
    expected = {
        "request_id": "request-1",
        "status": "running",
        "current_stage": "image_ocr",
        "progress_events": [{"sequence": 5, "event_id": "event-5"}],
        "latest_progress_sequence": 5,
        "message_excerpt": "not needed for polling",
    }

    async def get_request_trace(*_args):
        return row

    def trace_payload(_row, *, after_sequence):
        assert after_sequence == 4
        return expected

    monkeypatch.setattr(agent.agent_runtime, "get_request_trace", get_request_trace)
    monkeypatch.setattr(agent.agent_runtime, "trace_payload", trace_payload)
    user = type("User", (), {"id": 7, "project_id": 3})()

    result = asyncio.run(agent.get_agent_request(
        "request-1", after_sequence=4, progress_only=True, user=user, db=object(),
    ))

    assert result == {
        "request_id": "request-1",
        "status": "running",
        "current_stage": "image_ocr",
        "progress_events": [{"sequence": 5, "event_id": "event-5"}],
        "latest_progress_sequence": 5,
    }


def test_successful_request_planner_recovers_times_omitted_by_model(monkeypatch):
    async def model_response(*_args, **_kwargs):
        return (
            '{"intent":"create_record","actions":[{"id":"record_work"}],'
            '"record_type":"旁站","record_type_confidence":0.95,'
            '"facts":[{"field":"work_content","value":"混凝土浇筑"}]}',
            type("ModelConfig", (), {"model_id": "test-model"})(),
        )

    monkeypatch.setattr(agent.model_gateway, "chat_completion", model_response)
    message = "今天上午10点钟到12点钟，在西楼A区进行混凝土浇筑旁站"
    plan, model = asyncio.run(agent._generate_request_plan(message=message))

    assert model == "test-model"
    facts = {item["field"]: item for item in plan["facts"]}
    assert facts["start_time"]["value"] == "10:00"
    assert facts["end_time"]["value"] == "12:00"
    assert facts["start_time"]["source"] == "user_message_deterministic"
    item_fields = plan["item_plan"]["items"][0]["fields"]
    assert item_fields["start_time"]["value"] == "10:00"
    assert item_fields["end_time"]["value"] == "12:00"
