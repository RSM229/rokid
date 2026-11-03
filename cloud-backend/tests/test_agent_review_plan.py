import importlib.util
from pathlib import Path


MODULE = Path(__file__).parents[1] / "app" / "services" / "agent_review_plan.py"
spec = importlib.util.spec_from_file_location("agent_review_plan", MODULE)
assert spec and spec.loader
review_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_plan)


def base_plan():
    return {
        "schema_version": 1,
        "request_text": "回顾前面的工作，整理记录",
        "evidence": [{"id": "p1"}, {"id": "p2"}],
        "events": [{
            "id": "e1", "title": "A区钢筋验收", "location": "A区",
            "content": "钢筋验收", "date": "2026-10-18", "date_confirmed": True,
            "evidence_ids": ["p1"], "forms": ["acceptance_record"], "record_type": "验收",
            "record_type_confirmed": True,
        }],
        "unassigned_ids": ["p2"],
    }


def test_plan_requires_every_evidence_exactly_once():
    plan = base_plan()
    review_plan.validate_plan(plan)
    plan["events"][0]["evidence_ids"].append("p2")
    try:
        review_plan.validate_plan(plan)
    except ValueError as exc:
        assert "只能归属一个" in str(exc)
    else:
        raise AssertionError("duplicate evidence assignment should fail")


def test_plan_allows_unconfirmed_date_and_rejects_unknown_form():
    plan = base_plan()
    plan["events"][0]["evidence_ids"].append("p2")
    plan["unassigned_ids"] = []
    plan["events"][0]["date_confirmed"] = False
    review_plan.validate_plan(plan)
    assert not any("日期待确认" in issue for issue in review_plan.review_issues(plan))
    plan["events"][0]["forms"] = ["guess_form"]
    try:
        review_plan.validate_plan(plan)
    except ValueError as exc:
        assert "不支持的表式" in str(exc)
    else:
        raise AssertionError("unknown form should fail")


def test_empty_evidence_text_only_event_still_requires_form_fields():
    plan = base_plan()
    plan["evidence"] = []
    plan["unassigned_ids"] = []
    plan["events"][0]["evidence_ids"] = []
    review_plan.validate_plan(plan)
    issues = review_plan.review_issues(plan)
    assert any("验收记录还需补充" in issue for issue in issues)


def test_conflicting_watermark_values_auto_resolve_without_blocking():
    from app.services.agent_form_requirements import resolve_event_form

    plan = base_plan()
    plan["evidence"] = [
        {"id": "p1", "watermark_items": [{"label": "工程项目名称", "value": "项目A"}]},
        {"id": "p2", "watermark_items": [{"label": "工程项目名称", "value": "项目B"}]},
    ]
    plan["unassigned_ids"] = []
    plan["events"][0]["evidence_ids"] = ["p1", "p2"]
    issues = review_plan.review_issues(plan)
    # 2026-11-03 自动裁决：不再产出“多个水印候选值，请确认采用”阻断项。
    assert not any("多个水印候选值" in issue for issue in issues)
    # 智能体据当前项目/水印自动采用其一，候选保留供事后点选。
    resolved = resolve_event_form(plan, plan["events"][0])
    assert resolved["conflicts"] == []
    assert any(
        ar["canonical"] == "project_name" and ar["chosen"] in {"项目A", "项目B"}
        for ar in resolved["auto_resolutions"]
    )


def test_form_specific_fields_must_be_objects():
    plan = base_plan()
    plan["events"][0]["fields"] = {"gd_b_213_pingxing": "合格"}
    try:
        review_plan.validate_plan(plan)
    except ValueError as exc:
        assert "专用字段" in str(exc)
    else:
        raise AssertionError("non-object form fields should fail")


def test_pending_record_type_must_be_resolved_before_confirmation():
    plan = base_plan()
    plan["events"][0]["evidence_ids"].append("p2")
    plan["unassigned_ids"] = []
    plan["events"][0]["record_type"] = "待定"
    review_plan.validate_plan(plan)
    assert any("请选择工作记录类型" in issue for issue in review_plan.review_issues(plan))


def test_agent_record_type_candidate_no_longer_requires_explicit_confirmation():
    plan = base_plan()
    plan["events"][0]["record_type_confirmed"] = False
    issues = review_plan.review_issues(plan)
    # 2026-11-03 去除“记录类型为 Agent 建议，请确认”门禁：类型判定后即可建档。
    assert not any("是 Agent 建议" in issue for issue in issues)


def test_unresolved_entity_conflict_no_longer_blocks_server_confirmation():
    plan = base_plan()
    plan["events"][0]["evidence_ids"].append("p2")
    plan["unassigned_ids"] = []
    plan["events"][0]["unresolved_conflicts"] = ["project_name", "construction_unit"]
    review_plan.validate_plan(plan)
    issues = review_plan.review_issues(plan)
    # 2026-11-03 冲突自动裁决：unresolved_conflicts 不再产生“存在多个候选值，请先选择”阻断。
    assert not any("存在多个候选值" in issue for issue in issues)


def test_pinned_form_contract_is_stable_and_schema_changes_require_review():
    from app.services.agent_form_requirements import resolve_event_form

    plan = base_plan()
    event = plan["events"][0]
    resolved = resolve_event_form(plan, event)
    plan["form_contracts"] = {resolved["form_code"]: resolved["contract_version"]}
    assert not any("规则已更新" in issue for issue in review_plan.review_issues(plan))

    plan["form_contracts"][resolved["form_code"]] = "stale-contract"
    assert any("规则已更新" in issue for issue in review_plan.review_issues(plan))


def test_new_draft_pins_selected_form_contract():
    plan = review_plan.pin_form_contracts(base_plan())
    resolved = __import__("app.services.agent_form_requirements", fromlist=["resolve_event_form"]).resolve_event_form(
        plan, plan["events"][0],
    )
    assert plan["form_contracts"] == {resolved["form_code"]: resolved["contract_version"]}


def test_saving_draft_does_not_silently_repin_a_stale_contract():
    plan = base_plan()
    plan["form_contracts"] = {"acceptance_record": "old-reviewed-contract"}
    saved = review_plan.pin_form_contracts(plan, preserve_existing=True)
    assert saved["form_contracts"]["acceptance_record"] == "old-reviewed-contract"
    assert any("规则已更新" in issue for issue in review_plan.review_issues(saved))


def test_selected_form_must_match_record_type_before_confirmation():
    plan = base_plan()
    event = plan["events"][0]
    event["record_type"] = "巡视"
    event["forms"] = ["gd_b_212_pangzhan"]
    review_plan.validate_plan(plan)
    assert any("记录类型“巡视”与表式“旁站记录”不一致" in issue for issue in review_plan.review_issues(plan))


def test_form_type_mismatch_is_not_confirmable():
    plan = base_plan()
    event = plan["events"][0]
    event["record_type"] = "巡视"
    event["forms"] = ["gd_b_213_pingxing"]
    review_plan.validate_plan(plan)
    assert any("与表式“平行检查记录”不一致" in issue
               for issue in review_plan.review_issues(plan))


def test_agent_record_form_matrix_has_a_contract_and_export_mapping():
    from app.services import provform_schema, record_field_spec
    from app.services import record_steps

    assert review_plan.EXECUTABLE_RECORD_FORMS == {
        "gd_b_212_pangzhan", "gd_b_214_xunshi", "gdaq_30306_weida",
        "acceptance_record", "witness_record", "gd_b_213_pingxing",
        "gdaq_30305_qizhong",
    }
    for form_code in review_plan.EXECUTABLE_RECORD_FORMS:
        contract_fields = record_field_spec.fields_of(form_code)
        if form_code == "gdaq_30305_qizhong":
            schema = provform_schema.get_schema(form_code)
            schema_fields = [field for section in schema["sections"] for field in section["fields"]]
            mapped = provform_schema.CONTENT_MAP[form_code]
            assert len(schema_fields) >= 25
            assert {mapped[field["key"]] for field in schema_fields} >= {
                "machine_name", "machine_number", "machine_model", "location",
                "review_submission_status", "operator_qualification", "certificate_match",
                "emergency_preparation", "warning_measures", "plan_execution",
            }
            continue
        assert contract_fields, form_code
        if form_code == "witness_record":
            witness_schema = provform_schema.get_schema(form_code)
            assert witness_schema is not None
            schema_keys = {
                field["key"] for section in witness_schema["sections"]
                for field in section["fields"]
            }
            assert {field["key"] for field in contract_fields if field.get("key")} <= schema_keys
            assert record_steps.build_steps(form_code, {})["missing_count"] > 0
            assert not provform_schema.validate_values(form_code, {}).get("ok")
            continue  # The editable schema is projected from the witness contract.
        assert provform_schema.get_schema(form_code) is not None, form_code
        mapped_values = set(provform_schema.CONTENT_MAP.get(form_code, {}).values())
        canonical_fields = {
            field["canonical"] for field in contract_fields if field.get("canonical")
        }
        assert canonical_fields <= mapped_values, (form_code, canonical_fields - mapped_values)


def test_supported_record_form_matrix_is_derived_from_registry():
    from app.services.form_registry import FORM_BY_CODE, FORMS

    assert review_plan.ALLOWED_FORMS == {spec.form_code for spec in FORMS}
    for form_code in review_plan.EXECUTABLE_RECORD_FORMS:
        spec = FORM_BY_CODE[form_code]
        assert (spec.agent_work_record_type or spec.agent_record_type) == review_plan.FORM_RECORD_TYPES[form_code]
    assert "gd_b_213_pingxing" in review_plan.EXECUTABLE_RECORD_FORMS


def test_parallel_inspection_is_a_distinct_agent_business_route():
    from app.services import agent_form_requirements, agent_request_plan, form_registry

    spec = form_registry.FORM_BY_CODE["gd_b_213_pingxing"]
    assert spec.agent_business_route == "parallel_inspection"
    assert agent_request_plan.canonical_record_type("平行检验记录") == "平行检验"
    resolved = agent_form_requirements.resolve_event_form({"evidence": []}, {
        "record_type": "平行检验", "record_type_confirmed": True,
        "forms": ["gd_b_213_pingxing"], "date": "2026-10-18",
        "entity_fields": {"project_name": "项目A"},
        "fields": {"gd_b_213_pingxing": {
            "section": "3号楼二层", "acceptance_content": "钢筋间距",
            "basis": "施工图", "method": "尺量", "instrument": "钢卷尺", "measure_data": "200mm",
            "record": "实测200mm", "conclusion": "合格",
        }},
    })
    assert resolved["write_allowed"]
    assert resolved["form_code"] == "gd_b_213_pingxing"


def test_crane_safety_review_uses_its_own_work_record_and_form_contract():
    from app.services import agent_form_requirements, agent_request_plan, form_registry

    spec = form_registry.FORM_BY_CODE["gdaq_30305_qizhong"]
    assert spec.agent_work_record_type == "起重机械安拆检查"
    assert agent_request_plan.canonical_record_type("塔吊安拆检查") == "起重机械安拆检查"
    resolved = agent_form_requirements.resolve_event_form({"evidence": []}, {
        "record_type": "起重机械安拆检查", "record_type_confirmed": True,
        "forms": ["gdaq_30305_qizhong"], "date": "2026-10-23",
        "entity_fields": {"project_name": "项目A", "construction_unit": "总包A"},
        "fields": {"gdaq_30305_qizhong": {
            "machine_name": "塔吊A", "machine_number": "备案001", "machine_model": "QTZ80",
            "installation_location": "3号楼", "work_type": "安装", "user_unit": "使用单位B",
            "install_unit": "安拆单位C", "inspection_time": "2026-10-23 09:00",
            "review_submission_status": "有", "filing_status": "有",
            "general_contractor_staff": "到岗", "user_unit_staff": "到岗",
            "install_unit_staff": "到岗", "safety_technical_disclosure": "已交底",
            "operator_qualification": "人员符合方案", "certificate_match": "符合",
            "special_plan": "符合要求", "emergency_preparation": "已准备",
            "warning_measures": "警戒到位", "plan_execution": "按方案执行",
        }},
    })
    assert resolved["write_allowed"]
    assert resolved["form_code"] == "gdaq_30305_qizhong"
    assert not any("安拆前是否已办理告知手续" in label for label in resolved["missing_labels"])


def test_crane_form_materializes_only_as_its_own_record_type():
    from app.services import form_contracts, provform_schema
    from app.routers.form_doc import RECORD_TYPE_EXTRA_FORMS

    plan = base_plan()
    event = plan["events"][0]
    event["record_type"] = "起重机械安拆检查"
    event["forms"] = ["gdaq_30305_qizhong"]
    review_plan.validate_plan(plan)
    issues = review_plan.review_issues(plan)
    assert "gdaq_30305_qizhong" in review_plan.EXECUTABLE_RECORD_FORMS
    assert not any("尚未接入 Agent 业务记录物化流程" in issue for issue in issues)
    assert not any("不能将其转换成其他类型记录" in issue for issue in issues)
    contract = form_contracts.contract_for("gdaq_30305_qizhong")
    mapped = provform_schema.CONTENT_MAP["gdaq_30305_qizhong"]
    assert contract["version"] and len(contract["fields"]) >= 25
    assert "gdaq_30305_qizhong" not in RECORD_TYPE_EXTRA_FORMS["旁站"]
    assert "gdaq_30305_qizhong" in RECORD_TYPE_EXTRA_FORMS["起重机械安拆检查"]
    assert all(field["key"] in mapped for field in contract["fields"])
    assert {"operator_qualification", "certificate_match", "emergency_preparation",
            "warning_measures", "plan_execution", "other_checks"} <= set(mapped)


def test_all_registered_forms_have_a_versioned_field_contract_but_only_declared_routes_write():
    from app.services import form_contracts, form_registry, provform_schema, record_field_spec

    for spec in form_registry.FORMS:
        contract = form_contracts.contract_for(spec.form_code)
        assert contract["fields"], spec.form_code
        assert contract["version"], spec.form_code
        if record_field_spec.fields_of(spec.form_code):
            assert contract["source"] == "record_field_spec", spec.form_code
        else:
            assert provform_schema.get_schema(spec.form_code), spec.form_code
            assert contract["source"] == "provform_schema", spec.form_code
        if not spec.agent_work_record_type and not spec.agent_business_route:
            event = {
                "record_type": "旁站", "record_type_confirmed": True,
                "forms": [spec.form_code], "evidence_ids": [],
            }
            from app.services.agent_form_requirements import resolve_event_form
            resolved = resolve_event_form({"evidence": []}, event)
            assert resolved["fields"], spec.form_code
            assert resolved["contract_source"] == contract["source"]
            assert not resolved["write_allowed"], spec.form_code
            assert resolved["materialization_error"], spec.form_code


def test_schema_backed_contract_fields_participate_in_watermark_matching():
    from app.services.watermark_ocr import match_form_field

    assert match_form_field("隐患内容描述", "gdaq_30308_yinhuan") == "problems"


def test_record_draft_preview_marks_separate_business_form_not_writable():
    from app.services.agent_form_requirements import enrich_event, resolve_event_form

    event = {
        "id": "e1", "title": "整改通知", "location": "A区", "content": "发现隐患",
        "record_type": "巡视", "record_type_confirmed": True,
        "forms": ["gdaq_30308_yinhuan"], "evidence_ids": [],
        "fields": {"gdaq_30308_yinhuan": {
            "subject": "安全检查", "part": "A区", "problems": "防护缺失",
            "requirement": "立即整改", "deadline": "2026-10-30",
        }},
    }
    resolved = resolve_event_form({"evidence": []}, event)
    assert not resolved["write_allowed"]
    assert resolved["contract_source"] == "record_field_spec"
    assert resolved["fields"]
    assert "独立业务或汇总流程" in resolved["materialization_error"]
    draft = enrich_event({"evidence": []}, event)["record_draft"]
    assert draft["status"] == "needs_resolution"
    assert any(item["kind"] == "form_not_executable" for item in draft["blockers"])


def test_multiple_forms_cannot_be_silently_reduced_to_one_record():
    plan = base_plan()
    event = plan["events"][0]
    event["record_type"] = "验收"
    event["forms"] = ["acceptance_record", "witness_record"]
    review_plan.validate_plan(plan)
    assert any("只能绑定一种正式工作记录表式" in issue for issue in review_plan.review_issues(plan))
