from app.services.agent_review_execute import _business_problem, _flatten_event_fields


def test_confirmed_entity_fields_override_watermark_defaults():
    fields = _flatten_event_fields({
        "project_name": "系统项目",
        "construction_unit": "系统施工单位",
        "inspector": "登录人",
    }, "gd_b_214_xunshi")
    assert fields == {
        "project_name": "系统项目",
        "construction_unit": "系统施工单位",
        "inspector": "登录人",
    }


def test_legacy_form_fields_and_entity_fields_can_be_merged():
    form_fields = _flatten_event_fields({
        "gd_b_214_xunshi": {"section": "3号楼二层", "project": "水印项目"},
    }, "gd_b_214_xunshi")
    entity_fields = _flatten_event_fields({
        "project_name": "用户确认项目", "construction_unit": "施工单位",
    }, "gd_b_214_xunshi")
    assert {**form_fields, **entity_fields} == {
        "section": "3号楼二层",
        "project_name": "用户确认项目",
        "construction_unit": "施工单位",
    }


def test_instruction_never_becomes_inspection_problem():
    assert _business_problem({"problems": "记录这个工作"}) == ""
    assert _business_problem({"problems": "临边防护缺失，已要求整改"}) == "临边防护缺失，已要求整改"
