from app.services import agent_fact_model


def test_model_cannot_assign_itself_a_trusted_source():
    facts = agent_fact_model.normalize_model_facts([{
        "field": "record_date",
        "value": "2026-10-18",
        "source": "user_confirmed",
        "status": "user_confirmed",
        "source_span": "10月18日",
    }], message_ref="m1")
    assert facts[0]["source"] == "user_message_model"
    assert facts[0]["status"] == "user_stated_candidate"
    assert facts[0]["source_ref"] == "m1"
    assert facts[0]["source_span"] == "10月18日"


def test_extra_fields_are_flattened_and_rebuilt_for_tool_args():
    facts = agent_fact_model.normalize_model_facts({
        "extra_fields": {"1_13": "张三", "1_14": "李四"},
        "ignored": "",
    }, message_ref="m2")
    args = agent_fact_model.facts_to_args(facts)
    assert args["extra_fields"] == {"1_13": "张三", "1_14": "李四"}


def test_common_fact_aliases_are_canonicalized_and_unknown_fields_are_dropped():
    facts = agent_fact_model.normalize_model_facts([
        {"field": "date", "value": "2026-10-18"},
        {"field": "content", "value": "钢筋绑扎"},
        {"field": "made_up_field", "value": "不应进入事实层"},
    ], message_ref="m5")
    assert [item["field"] for item in facts] == ["record_date", "work_content"]
    assert agent_fact_model.facts_to_args(facts) == {
        "record_date": "2026-10-18", "work_content": "钢筋绑扎",
    }


def test_conflict_detection_ignores_missing_evidence_but_reports_disagreement():
    facts = agent_fact_model.normalize_model_facts({
        "record_date": "2026-10-18",
        "section": "3号楼二层",
    }, message_ref="m3")
    no_conflict = agent_fact_model.detect_source_conflicts(facts, [{"location": "A区"}])
    assert no_conflict == []
    conflicts = agent_fact_model.detect_source_conflicts(
        facts, [{
            "observed_date": "2026-10-19", "observed_date_source": "watermark",
            "section": "3号楼三层", "watermark_fields": {
                "taken_at": "2026-10-19 10:00", "section": "3号楼三层",
            },
        }],
    )
    assert {item["field"] for item in conflicts} == {"record_date", "section"}
    assert all(item["resolution"] == "evidence_value_preferred_pending_confirmation" for item in conflicts)
    assert all(item["preferred_source"] == "watermark_or_exif" for item in conflicts)


def test_generic_vision_observation_does_not_masquerade_as_watermark():
    facts = agent_fact_model.normalize_model_facts({
        "section": "3号楼二层",
    }, message_ref="m6")
    conflicts = agent_fact_model.detect_source_conflicts(
        facts, [{
            "observed_date": "2026-10-19", "observed_date_source": "watermark",
            "watermark_fields": {"taken_at": "2026-10-19 10:00"},
            "section": "3号楼三层", "observation": "视觉模型观察",
        }],
    )
    assert len(conflicts) == 1
    assert conflicts[0]["evidence_sources"] == ["evidence"]
    assert conflicts[0]["preferred_value"] == "3号楼二层"
    assert conflicts[0]["resolution"] == "user_value_preferred_pending_evidence_confirmation"
