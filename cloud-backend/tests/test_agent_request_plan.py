from app.services import agent_request_plan as planner


def test_record_aliases_are_canonicalized_in_one_catalog():
    assert planner.canonical_record_type("巡检") == "巡视"
    assert planner.canonical_record_type("现场巡查记录") == "巡视"
    assert planner.canonical_record_type("旁站监理") == "旁站"
    assert planner.canonical_record_type("材料见证") == "见证"
    assert planner.canonical_record_type("未知类型") is None


def test_record_plan_is_a_candidate_and_never_directly_writable():
    plan = planner.normalize_candidate({
        "intent": "create_record",
        "actions": [{"id": "record_work", "confidence": 0.91}],
        "record_type": "巡检",
        "record_type_confidence": 0.88,
    })
    assert plan["intent"] == "create_record"
    assert plan["record_type"] == "巡视"
    assert plan["selected_tool"] == "create_work_record"
    assert plan["requires_confirmation"] is True
    assert plan["write_allowed"] is False
    assert plan["clarifications"] == []


def test_ambiguous_record_request_requires_a_type_and_cannot_execute():
    plan = planner.normalize_candidate({
        "intent": "create_record",
        "actions": [{"id": "record_work", "confidence": 0.7}],
    })
    assert plan["selected_tool"] is None
    assert plan["execution_mode"] == "clarify"
    assert plan["clarifications"][0]["field"] == "record_type"
    assert "巡视" in plan["clarifications"][0]["options"]


def test_attachment_upload_alone_does_not_request_hazard_analysis():
    plan = planner.normalize_candidate(
        {"intent": "clarify", "actions": []},
        attachment_count=9,
        evidence_count=9,
    )
    assert plan["needs_hazard_analysis"] is False
    assert plan["selected_tool"] is None
    assert plan["clarifications"][0]["field"] == "purpose"
    assert plan["write_allowed"] is False


def test_explicit_hazard_request_enables_analysis_but_not_a_write():
    plan = planner.normalize_candidate(
        {
            "intent": "analyze_hazard",
            "actions": [{"id": "analyze_hazard", "confidence": 0.94}],
        },
        attachment_count=2,
        evidence_count=2,
    )
    assert plan["needs_hazard_analysis"] is True
    assert plan["selected_tool"] is None
    assert plan["requires_confirmation"] is False
    assert plan["write_allowed"] is False


def test_hazard_without_evidence_asks_for_an_upload():
    plan = planner.normalize_candidate(
        {"intent": "analyze_hazard", "actions": ["analyze_hazard"]},
        attachment_count=0,
        evidence_count=0,
    )
    assert plan["needs_hazard_analysis"] is False
    assert any(item["control"] == "image_upload" for item in plan["clarifications"])


def test_multiple_business_actions_are_preserved_without_auto_execution():
    plan = planner.normalize_candidate(
        {
            "intent": "create_record",
            "actions": ["record_work", "analyze_hazard"],
            "record_type": "巡视",
            "record_type_confidence": 0.8,
        },
        attachment_count=3,
        evidence_count=3,
    )
    assert {item["id"] for item in plan["actions"]} == {"record_work", "analyze_hazard"}
    assert plan["needs_hazard_analysis"] is True
    assert plan["selected_tool"] is None
    assert plan["requires_confirmation"] is True
    assert plan["write_allowed"] is False


def test_unknown_model_actions_are_dropped_and_degrade_safely():
    plan = planner.normalize_candidate(
        {"intent": "create_record", "actions": ["drop_database", "not_a_tool"]},
        attachment_count=1,
        evidence_count=1,
    )
    assert plan["actions"] == []
    assert plan["intent"] == "clarify"
    assert plan["execution_mode"] == "clarify"
    assert plan["write_allowed"] is False


def test_degraded_plan_never_infers_record_type_or_write():
    plan = planner.degraded_plan(
        attachment_count=1,
        evidence_count=1,
        reason="timeout",
    )
    assert plan["degraded"] is True
    assert plan["record_type"] is None
    assert plan["selected_tool"] is None
    assert plan["write_allowed"] is False
    legacy = planner.plan_to_legacy_purpose(plan)
    assert legacy["intent"] == "clarify"
    assert legacy["recommendation"] is None
