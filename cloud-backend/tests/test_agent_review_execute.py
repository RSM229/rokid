from app.services.agent_review_execute import _business_problem, _flatten_event_fields, _selected_event_fields
from app.services.agent_form_requirements import resolve_event_form
from app.services.record_fields import business_record_fields
from types import SimpleNamespace


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


def test_form_decision_overrides_older_entity_candidate():
    fields = _selected_event_fields({
        "entity_fields": {"inspector": "水印候选陈宇文", "project_name": "系统候选"},
        "fields": {"gd_b_214_xunshi": {
            "inspector": {"value": "用户确认张三", "source": "user_message", "status": "user_confirmed"},
            "project_name": {"value": "用户确认项目A", "source": "user_message", "status": "user_confirmed"},
        }},
    }, "gd_b_214_xunshi")
    assert fields["inspector"] == "用户确认张三"
    assert fields["project_name"] == "用户确认项目A"


def test_user_selected_form_alias_overrides_stale_entity_canonical_value():
    resolved = resolve_event_form({"evidence": []}, {
        "record_type": "巡视",
        "forms": ["gd_b_214_xunshi"],
        "entity_fields": {"project_name": "旧值AAA"},
        "fields": {
            "gd_b_214_xunshi": {
                "project": {"value": "用户选择A", "source": "user_message", "status": "user_confirmed"},
            },
        },
    })
    assert resolved["values"]["project_name"] == "用户选择A"
    assert next(field for field in resolved["fields"] if field["canonical"] == "project_name")["value"] == "用户选择A"


def test_instruction_never_becomes_inspection_problem():
    assert _business_problem({"problems": "记录这个工作"}) == ""
    assert _business_problem({"problems": "临边防护缺失，已要求整改"}) == "临边防护缺失，已要求整改"


def test_confirmed_values_are_authoritative_over_watermark_and_system_values():
    record = SimpleNamespace(
        project_id=1,
        station_project_name=None,
        content_json={
            "confirmed_fields": {
                "project_name": "用户选定项目A",
                "construction_unit": "用户选定施工单位A",
            },
            "form_context": {
                "project_name": "水印项目B",
                "construction_unit": "水印施工单位B",
            },
        },
    )
    project = SimpleNamespace(
        name="系统项目C",
        owner_unit="",
        construction_unit="系统施工单位C",
        supervision_unit="",
        supervision_org="",
    )

    resolved = business_record_fields(record, project)

    assert resolved["business_project_name"] == "用户选定项目A"
    assert resolved["construction_unit"] == "用户选定施工单位A"
    assert resolved["system_project_name"] == "系统项目C"
    assert "水印项目B" in resolved["field_candidates"]["project_name"]


def test_form_contract_fingerprint_covers_definition_and_is_independent_helper():
    from app.services.form_contracts import contract_version

    forms = {"sample": {"name": "Sample", "fields": [{"key": "a", "required": True}]}}
    before = contract_version("sample", forms)
    forms["sample"]["fields"][0]["required"] = False
    assert contract_version("sample", forms) != before


def test_form_contract_rejects_invalid_enum_date_and_number_values():
    base = {
        "record_type": "巡视",
        "forms": ["gd_b_214_xunshi"],
        "date": "2026-10-18",
        "entity_fields": {"project_name": "项目A", "construction_unit": "施工单位A"},
        "fields": {"gd_b_214_xunshi": {
            "section": {"value": "3号楼二层", "source": "user_message", "status": "user_confirmed"},
            "start_time": {"value": "不是时间", "source": "user_message", "status": "user_confirmed"},
        }},
    }
    result = resolve_event_form({"evidence": []}, base)
    assert any(item["canonical"] == "start_time" for item in result["invalid_fields"])
    assert not result["write_allowed"]


def test_legacy_chat_skip_cannot_bypass_required_form_contract_fields():
    import asyncio
    from types import SimpleNamespace
    from app.services import agent_tools

    class FakeDb:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        async def flush(self):
            return None

        async def commit(self):
            return None

    db = FakeDb()
    context = agent_tools.ToolContext(
        db=db,
        user=SimpleNamespace(id=4, username="tester", has_permission=lambda _p: True),
        project_id=7,
        project=SimpleNamespace(name="项目A", construction_unit="总包A"),
        message="跳过，直接创建",
    )
    result = asyncio.run(agent_tools._create_work_record(context, {
        "record_type": "见证", "work_content": "钢筋取样", "section": "3号楼二层",
    }))

    assert result["need_more"]
    assert not db.rows


def test_legacy_chat_created_record_persists_form_contract_snapshot():
    import asyncio
    from types import SimpleNamespace
    from app.services import agent_tools

    class FakeDb:
        def __init__(self):
            self.rows = []

        def add(self, row):
            row.id = len(self.rows) + 1
            self.rows.append(row)

        async def flush(self):
            return None

        async def commit(self):
            return None

    form = "gd_b_214_xunshi"
    args = {
        "record_type": "巡视", "work_content": "临边防护检查",
        "section": "3号楼二层", "record_date": "2026-10-23",
        "inspection_start_time": "09:00", "inspection_end_time": "10:00",
        "weather": "晴", "problems": "符合要求",
    }
    for field in agent_tools.record_field_spec.required_ask_fields(form):
        canonical = field["canonical"]
        value = {
            "weather": "晴", "start_time": "09:00", "end_time": "10:00",
            "date": "2026-10-23", "record_date": "2026-10-23",
        }.get(canonical)
        if value is None and "数字" in str(field.get("table_type") or ""):
            value = "1"
        if value is None and agent_tools.record_field_spec.options_for(field):
            value = agent_tools.record_field_spec.options_for(field)[0]
        value = value or "已确认"
        existing = agent_tools._field_value(args, field)
        if existing:
            continue
        if field.get("arg"):
            args[field["arg"]] = value
        else:
            args.setdefault("extra_fields", {})[field["canonical"]] = value
    db = FakeDb()
    context = agent_tools.ToolContext(
        db=db,
        user=SimpleNamespace(id=4, username="tester", has_permission=lambda _p: True),
        project_id=7,
        project=SimpleNamespace(name="项目A", construction_unit="总包A", owner_unit="建设单位A",
                                supervision_unit="监理单位A", supervision_org="项目监理部"),
        message="创建巡视记录",
    )

    result = asyncio.run(agent_tools._create_work_record(context, args))

    assert result["ok"]
    assert len(db.rows) == 2  # WorkRecord plus its security audit.
    record = db.rows[0]
    assert record.content_json["form_code"] == form
    assert record.content_json["form_contract_version"]
    assert record.content_json["confirmed_fields"]["section"] == "3号楼二层"
    assert record.content_json["form_requirements"]["write_allowed"]


def test_legacy_chat_complete_record_waits_for_explicit_confirmation():
    import asyncio
    from types import SimpleNamespace
    from app.services import agent_tools
    from app.models.work_record import WorkRecord

    class FakeDb:
        def __init__(self):
            self.rows = []

        def add(self, row):
            row.id = len(self.rows) + 1
            self.rows.append(row)

        async def flush(self):
            return None

        async def commit(self):
            return None

    form = "gd_b_214_xunshi"
    args = {
        "record_type": "巡视", "work_content": "临边防护检查",
        "section": "3号楼二层", "record_date": "2026-10-23",
        "inspection_start_time": "09:00", "inspection_end_time": "10:00",
        "weather": "晴", "problems": "符合要求", "_require_confirmation": True,
    }
    for field in agent_tools.record_field_spec.required_ask_fields(form):
        if agent_tools._field_value(args, field):
            continue
        value = {
            "weather": "晴", "start_time": "09:00", "end_time": "10:00",
            "date": "2026-10-23", "record_date": "2026-10-23",
        }.get(field["canonical"])
        if value is None and agent_tools.record_field_spec.options_for(field):
            value = agent_tools.record_field_spec.options_for(field)[0]
        value = value or "已确认"
        if field.get("arg"):
            args[field["arg"]] = value
        else:
            args.setdefault("extra_fields", {})[field["canonical"]] = value

    db = FakeDb()
    context = agent_tools.ToolContext(
        db=db,
        user=SimpleNamespace(id=4, username="tester", has_permission=lambda _p: True),
        project_id=7,
        project=SimpleNamespace(name="项目A", construction_unit="总包A", owner_unit="建设单位A",
                                supervision_unit="监理单位A", supervision_org="项目监理部"),
        message="整理完整记录",
    )
    preview = asyncio.run(agent_tools._create_work_record(context, args))
    assert preview["need_more"] == ["最终确认"]
    assert not db.rows

    args["_execution_confirmed"] = True
    result = asyncio.run(agent_tools._create_work_record(context, args))
    assert result["ok"]
    assert any(isinstance(row, WorkRecord) for row in db.rows)


def test_legacy_chat_parallel_record_waits_for_confirmation_and_keeps_all_required_values():
    import asyncio
    from types import SimpleNamespace
    from app.services import agent_tools
    from app.models.parallel import ParallelInspection

    class FakeDb:
        def __init__(self):
            self.rows = []

        def add(self, row):
            row.id = len(self.rows) + 1
            self.rows.append(row)

        async def flush(self):
            return None

        async def commit(self):
            return None

    args = {
        "record_type": "平行检验", "section": "3号楼二层", "record_date": "2026-10-23",
        "acceptance_content": "钢筋间距", "basis": "施工图纸", "method": "尺量",
        "instrument": "钢卷尺-01", "measure_data": "实测 200mm", "record": "抽测 5 处均符合",
        "conclusion": "合格", "_require_confirmation": True,
    }
    db = FakeDb()
    context = agent_tools.ToolContext(
        db=db,
        user=SimpleNamespace(id=4, username="tester", has_permission=lambda _p: True),
        project_id=7,
        project=SimpleNamespace(name="项目A", construction_unit="总包A", supervision_org="项目监理部"),
        message="整理平行检验记录",
    )
    preview = asyncio.run(agent_tools._create_work_record(context, args))
    assert preview["need_more"] == ["最终确认"]
    assert not db.rows

    args["_execution_confirmed"] = True
    result = asyncio.run(agent_tools._create_work_record(context, args))
    row = next(item for item in db.rows if isinstance(item, ParallelInspection))
    assert result["ok"]
    assert "钢卷尺-01" in row.result_detail
    assert "实测 200mm" in row.result_detail
    assert "抽测 5 处均符合" in row.result_detail


def test_gdaq30308_cannot_be_created_as_an_unlinked_generic_document():
    import asyncio
    from types import SimpleNamespace
    from app.services import agent_tools

    context = agent_tools.ToolContext(
        db=SimpleNamespace(), user=SimpleNamespace(id=4), project_id=7,
        project=None, message="",
    )
    result = asyncio.run(agent_tools._create_provform_doc(context, {
        "form_code": "gdaq_30308_yinhuan",
        "fields": {"5_06": "安全检查", "part": "A区", "5_08": "临边无防护"},
    }))
    assert result["ok"] is False
    assert "必须关联真实隐患记录" in result["reply"]


def test_hazard_notice_agent_tool_requires_a_hazard_id_before_writing():
    import asyncio
    from types import SimpleNamespace
    from app.services import agent_tools

    context = agent_tools.ToolContext(
        db=SimpleNamespace(), user=SimpleNamespace(id=4), project_id=7,
        project=None, message="生成安全整改通知",
    )
    result = asyncio.run(agent_tools._create_hazard_rectification_doc(context, {}))
    assert result["need_more"] == ["隐患编号"]


def test_form_contract_exposes_invalid_fields_for_server_confirmation_gate():
    from app.services.agent_review_plan import review_issues

    plan = {
        "schema_version": 1,
        "request_text": "整理记录",
        "evidence": [{"id": "p1"}],
        "unassigned_ids": [],
        "events": [{
            "id": "e1", "title": "旁站", "location": "A区", "content": "混凝土浇筑",
            "date": "2026-10-18", "evidence_ids": ["p1"],
            "forms": ["gd_b_212_pangzhan"], "record_type": "旁站",
            "record_type_confirmed": True,
            "entity_fields": {"project_name": "项目A", "construction_unit": "施工单位A"},
            "fields": {"gd_b_212_pangzhan": {
                "start_time": {"value": "不是时间", "source": "user_message", "status": "user_confirmed"},
            }},
        }],
    }
    issues = review_issues(plan)
    assert any("旁站开始时间" in issue and "无法识别" in issue for issue in issues)


def test_review_event_returns_versioned_record_draft_preview_until_contract_is_complete():
    from app.services.agent_form_requirements import enrich_event

    event = {
        "id": "e1", "title": "钢筋验收", "record_type": "待定",
        "record_type_confirmed": False, "date": "2026-10-18",
        "section": "A区", "location": "3号楼", "content": "钢筋验收",
        "evidence_ids": [], "forms": [],
    }
    pending_type = enrich_event({"evidence": []}, event)
    assert pending_type["record_draft"]["status"] == "needs_record_type"
    assert pending_type["record_draft"]["generation_allowed"] is False
    assert pending_type["record_draft"]["blockers"][0]["kind"] == "record_type"

    event.update({"record_type": "验收", "record_type_confirmed": True, "forms": ["acceptance_record"]})
    awaiting_fields = enrich_event({"evidence": []}, event)
    draft = awaiting_fields["record_draft"]
    assert draft["status"] == "needs_resolution"
    assert draft["form_code"] == "acceptance_record"
    assert draft["contract_version"]
    assert draft["blockers"]
    assert draft["generation_allowed"] is False


def test_time_only_value_is_valid_when_record_date_is_separate():
    result = resolve_event_form({"evidence": []}, {
        "record_type": "巡视",
        "forms": ["gd_b_214_xunshi"],
        "date": "2026-10-18",
        "entity_fields": {"project_name": "项目A", "construction_unit": "施工单位A"},
        "fields": {"gd_b_214_xunshi": {
            "section": "3号楼二层",
            "start_time": "9:00",
        }},
    })
    assert not any(item["canonical"] == "start_time" for item in result["invalid_fields"])


def test_confirmed_parallel_inspection_materializes_its_own_business_record():
    import asyncio
    import json
    from types import SimpleNamespace
    from app.services.agent_review_execute import execute_confirmed_plan
    from app.models.parallel import ParallelInspection
    from app.services.agent_review_plan import pin_form_contracts

    class FakeDb:
        def __init__(self):
            self.rows = []

        async def get(self, model, _id):
            if model.__name__ == "Project":
                return SimpleNamespace(id=7)
            return None

        def add(self, row):
            row.id = len(self.rows) + 1
            self.rows.append(row)

        async def flush(self):
            return None

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("no media query expected")

    plan = {
        "schema_version": 1,
        "request_text": "整理平行检验记录",
        "evidence": [],
        "unassigned_ids": [],
        "events": [{
            "id": "e1", "title": "钢筋间距检查", "location": "3号楼二层",
            "section": "3号楼二层", "content": "钢筋间距实测",
            "date": "2026-10-18", "date_confirmed": True,
            "evidence_ids": [], "forms": ["gd_b_213_pingxing"],
            "record_type": "平行检验", "record_type_confirmed": True,
            "record_type_confidence": 1,
            "entity_fields": {"project_name": "项目A"},
            "fields": {"gd_b_213_pingxing": {
                "section": "3号楼二层", "acceptance_content": "钢筋间距",
                "basis": "施工图", "method": "尺量", "instrument": "钢卷尺",
                "measure_data": "200mm", "record": "实测200mm", "conclusion": "合格",
            }},
        }],
    }
    plan = pin_form_contracts(plan)
    row = SimpleNamespace(
        status="confirmed", confirmed_revision=3, revision=3, executed_revision=None,
        execution_result_json=None, plan_json=json.dumps(plan, ensure_ascii=False),
        id=11, project_id=7, updated_at=None,
    )
    user = SimpleNamespace(id=4, project_id=7, has_permission=lambda _permission: True)
    db = FakeDb()
    result = asyncio.run(execute_confirmed_plan(db, row, user, 3))

    assert len(db.rows) == 1
    assert isinstance(db.rows[0], ParallelInspection)
    assert db.rows[0].part == "3号楼二层"
    assert db.rows[0].item == "钢筋间距"
    assert db.rows[0].conclusion == "合格"
    assert result["records"][0]["business_type"] == "parallel_inspection"


def test_confirmed_crane_plan_materializes_work_record_with_authoritative_contract_snapshot():
    import asyncio
    import json
    from types import SimpleNamespace
    from app.models.work_record import WorkRecord
    from app.services.agent_review_execute import execute_confirmed_plan
    from app.services.agent_review_plan import pin_form_contracts

    class FakeDb:
        def __init__(self):
            self.rows = []

        async def get(self, model, _id):
            if model.__name__ == "Project":
                return SimpleNamespace(id=7)
            return None

        def add(self, row):
            row.id = len(self.rows) + 1
            self.rows.append(row)

        async def flush(self):
            return None

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("no media query expected")

    fields = {
        "machine_name": "塔吊A", "machine_number": "备案001", "machine_model": "QTZ80",
        "installation_location": "3号楼", "work_type": "安装", "user_unit": "使用单位B",
        "install_unit": "安拆单位C", "inspection_time": "2026-10-23 09:00",
        "review_submission_status": "有", "filing_status": "有",
        "general_contractor_staff": "到岗", "user_unit_staff": "到岗",
        "install_unit_staff": "到岗", "safety_technical_disclosure": "已交底",
        "operator_qualification": "人员符合方案", "certificate_match": "符合",
        "special_plan": "符合要求", "emergency_preparation": "已准备",
        "warning_measures": "警戒到位", "plan_execution": "按方案执行",
    }
    plan = pin_form_contracts({
        "schema_version": 1,
        "request_text": "整理起重机械安拆检查记录",
        "evidence": [], "unassigned_ids": [],
        "events": [{
            "id": "e1", "title": "塔吊A安装检查", "location": "3号楼",
            "section": "3号楼", "content": "塔吊安装过程检查",
            "date": "2026-10-23", "date_confirmed": True, "evidence_ids": [],
            "forms": ["gdaq_30305_qizhong"], "record_type": "起重机械安拆检查",
            "record_type_confirmed": True, "record_type_confidence": 1,
            "entity_fields": {"project_name": "项目A", "construction_unit": "总包A"},
            "fields": {"gdaq_30305_qizhong": fields},
        }],
    })
    row = SimpleNamespace(
        status="confirmed", confirmed_revision=4, revision=4, executed_revision=None,
        execution_result_json=None, plan_json=json.dumps(plan, ensure_ascii=False),
        id=12, project_id=7, updated_at=None,
    )
    user = SimpleNamespace(id=4, project_id=7, has_permission=lambda _permission: True)
    db = FakeDb()
    result = asyncio.run(execute_confirmed_plan(db, row, user, 4))

    assert len(db.rows) == 1
    record = db.rows[0]
    assert isinstance(record, WorkRecord)
    assert record.record_type == "起重机械安拆检查"
    assert record.section == "3号楼"
    assert record.content_json["form_code"] == "gdaq_30305_qizhong"
    assert record.content_json["form_contract_version"] == plan["form_contracts"]["gdaq_30305_qizhong"]
    assert record.content_json["confirmed_fields"]["machine_name"] == "塔吊A"
    assert record.content_json["prov_fields"]["machine_number"] == "备案001"
    assert result["records"][0]["record_type"] == "起重机械安拆检查"
