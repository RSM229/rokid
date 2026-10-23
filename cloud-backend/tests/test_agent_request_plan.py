from app.services import agent_request_plan as planner
from app.services import agent_tools


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


def test_model_facts_are_source_controlled_and_exposed_with_status():
    plan = planner.normalize_candidate({
        "intent": "create_record",
        "actions": [{"id": "record_work", "confidence": 0.9}],
        "record_type": "巡视",
        "facts": [{
            "field": "section",
            "value": "3号楼二层",
            "source": "user_confirmed",
            "status": "user_confirmed",
            "source_span": "3号楼二层",
        }],
    }, message="3号楼二层做巡视")
    fact = plan["facts"][0]
    assert fact["field"] == "section"
    assert fact["source"] == "user_message_model"
    assert fact["status"] == "user_stated_candidate"
    assert plan["field_status"]["section"]["value"] == "3号楼二层"


def test_watermark_field_wins_and_user_value_is_reported_when_evidence_disagrees():
    plan = planner.normalize_candidate({
        "intent": "create_record",
        "actions": ["record_work"],
        "record_type": "巡视",
        "facts": [{"field": "record_date", "value": "2026-10-18"}],
    }, message="10月18日巡视", evidence=[{
        "kind": "image", "observed_date": "2026-10-19", "observed_date_source": "watermark",
        "location": "A区", "section": "3号楼二层",
        "watermark_fields": {
            "taken_at": "2026-10-19 09:30", "location": "A区", "section": "3号楼二层",
        },
    }])
    assert plan["field_conflicts"][0]["field"] == "record_date"
    assert plan["field_conflicts"][0]["user_value"] == "2026-10-18"
    assert plan["field_conflicts"][0]["evidence_values"] == ["2026-10-19"]
    assert plan["field_conflicts"][0]["preferred_value"] == "2026-10-19"
    assert plan["field_conflicts"][0]["preferred_source"] == "watermark"
    assert plan["field_conflicts"][0]["resolution"] == "evidence_value_preferred_pending_confirmation"
    assert plan["item_plan"]["candidate_count"] == 1


def test_legacy_tool_output_is_mapped_through_the_action_catalog():
    plan = planner.normalize_candidate({
        "tool": "query_work_records",
        "args": {"keyword": "钢筋"},
    })
    assert plan["actions"][0]["id"] == "query_work_records"
    assert plan["selected_tool"] == "query_work_records"


def test_every_executable_tool_has_a_controlled_action():
    assert set(agent_tools.TOOL_BY_NAME) <= set(planner.ACTION_BY_TOOL)


def test_hazard_rectification_action_is_bound_to_dedicated_tool():
    plan = planner.normalize_candidate({
        "actions": ["create_hazard_rectification_doc"],
    })
    assert plan["selected_tool"] == "create_hazard_rectification_doc"
    assert plan["requires_confirmation"] is True


def test_evidence_dates_become_multiple_item_candidates_without_writing():
    plan = planner.normalize_candidate({
        "intent": "create_record",
        "actions": ["record_work"],
        "record_type": "巡视",
    }, message="记录这些巡视照片", evidence=[
        {"observed_date": "2026-10-18", "section": "3号楼二层"},
        {"observed_date": "2026-10-18", "section": "3号楼二层"},
        {"observed_date": "2026-10-19", "section": "3号楼二层"},
    ])
    assert plan["item_plan"]["candidate_count"] == 2
    assert plan["item_plan"]["write_allowed"] is False
    assert plan["write_allowed"] is False


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
