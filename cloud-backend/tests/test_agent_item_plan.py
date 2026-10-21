from app.services.agent_item_plan import build_item_plan
from app.services.agent_fact_model import normalize_model_facts


def test_images_are_not_counted_as_records_and_same_identity_is_grouped():
    plan = build_item_plan(
        facts=[],
        evidence=[
            {"observed_date": "2026-10-18", "project": "白泽项目", "section": "3号楼二层"},
            {"observed_date": "2026-10-18", "project": "白泽项目", "section": "3号楼二层"},
            {"observed_date": "2026-10-18", "project": "白泽项目"},
        ],
        record_type="巡视",
        record_type_confidence=0.88,
        action_ids=["record_work"],
    )
    assert plan["candidate_count"] == 1
    assert plan["items"][0]["evidence_indexes"] == [0, 1, 2]
    assert plan["items"][0]["record_type_confidence"] == 0.88


def test_different_dates_or_sections_create_separate_candidates():
    plan = build_item_plan(
        facts=[],
        evidence=[
            {"observed_date": "2026-10-18", "section": "3号楼二层"},
            {"observed_date": "2026-10-19", "section": "3号楼二层"},
            {"observed_date": "2026-10-18", "section": "3号楼三层"},
        ],
        record_type=None,
        action_ids=["record_work"],
    )
    assert plan["candidate_count"] == 3
    assert any("合并或拆分必须由用户确认" in issue for issue in plan["issues"])


def test_fully_unknown_images_stay_one_pending_candidate_and_are_not_split():
    plan = build_item_plan(
        facts=[], evidence=[{}, {"note": "现场照片"}], record_type="巡视",
        action_ids=["record_work"],
    )
    assert plan["candidate_count"] == 1
    assert plan["items"][0]["status"] == "needs_identity"
    assert plan["items"][0]["evidence_indexes"] == [0, 1]
    assert plan["items"][0]["confidence"] < 0.5


def test_unknown_images_are_not_silently_merged_into_a_known_event():
    plan = build_item_plan(
        facts=[],
        evidence=[{"observed_date": "2026-10-18"}, {}],
        record_type="巡视", action_ids=["record_work"],
    )
    assert plan["candidate_count"] == 1
    assert plan["unassigned_evidence_indexes"] == [1]


def test_text_only_record_uses_user_facts_and_never_writes():
    facts = normalize_model_facts({
        "record_date": "2026-10-18", "section": "3号楼二层",
    }, message_ref="m4")
    plan = build_item_plan(
        facts=facts, evidence=[], record_type="巡视", action_ids=["record_work"],
    )
    assert plan["candidate_count"] == 1
    assert plan["items"][0]["fields"]["record_date"]["source"] == "user_message"
    assert plan["write_allowed"] is False


def test_watermark_identity_wins_over_user_but_generic_vision_does_not():
    facts = normalize_model_facts({
        "record_date": "2026-10-18", "section": "地下室顶板",
    }, message_ref="m7")
    watermark_plan = build_item_plan(
        facts=facts,
        evidence=[{
            "observed_date": "2026-10-19", "observed_date_source": "watermark",
            "section": "3号楼二层", "watermark_fields": {
                "taken_at": "2026-10-19 15:42", "section": "3号楼二层",
            },
        }],
        record_type="巡视",
        action_ids=["record_work"],
    )
    fields = watermark_plan["items"][0]["fields"]
    assert fields["record_date"]["value"] == "2026-10-19"
    assert fields["record_date"]["source"] == "watermark"
    assert fields["section"]["value"] == "3号楼二层"
    assert any("图片水印/EXIF" in issue for issue in watermark_plan["items"][0]["issues"])

    vision_plan = build_item_plan(
        facts=facts,
        evidence=[{"observed_date": "2026-10-19", "section": "3号楼三层"}],
        record_type="巡视",
        action_ids=["record_work"],
    )
    vision_fields = vision_plan["items"][0]["fields"]
    assert vision_fields["record_date"]["value"] == "2026-10-18"
    assert vision_fields["record_date"]["source"] == "user_message"


def test_read_only_request_does_not_create_a_record_candidate():
    plan = build_item_plan(
        facts=[], evidence=[], record_type=None, action_ids=["query_work_records"],
    )
    assert plan["candidate_count"] == 0
