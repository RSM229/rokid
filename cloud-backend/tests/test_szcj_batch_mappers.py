# -*- coding: utf-8 -*-
"""穗建通 SUP-002/003/005/006/007/012/021 批量映射器测试（纯函数，无 DB 无 HTTP）。"""
from __future__ import annotations

import pytest

from app.services.szcj_batch_mappers import (
    map_sup002,
    map_sup003,
    map_sup005,
    map_sup006,
    map_sup007,
    map_sup012,
    map_sup021,
)
from app.services.szcj_sup001_mapper import MissingRequiredError


def _base_record() -> dict:
    return {
        "id": 201,
        "status": "已提交",
        "record_date": "2026-11-19",
        "section": "3栋2层",
        "work_content": "现场施工正常",
        "weather": "晴",
        "problems": "",
        "treatment": "",
        "remark": None,
        "content_json": {},
    }


# ---------------------------------------------------------------------------
# SUP-002 旁站
# ---------------------------------------------------------------------------

class TestSUP002:
    def test_full_mapping(self):
        rec = _base_record() | {
            "station_process": "混凝土浇筑",
            "station_location": "3栋2层墙柱",
            "station_start_time": "2026-11-19 09:30:00",
            "station_end_time": "2026-11-19 12:00:00",
            "station_content": "振捣均匀，无漏振",
            "station_supervision_status": "符合要求",
            "construction_unit": "中建三局",
        }
        payload = map_sup002(
            rec, project_code="PRJ2026A", user_name="张三",
            photo_urls=["http://x/1.jpg"], created_time="2026-11-19 12:01:00",
            updated_time="2026-11-19 12:01:00",
        )
        assert payload["projectCode"] == "PRJ2026A"
        assert payload["userName"] == "张三"
        assert payload["keyPart"] == "3栋2层 混凝土浇筑"   # section + 工序拼合
        assert payload["startTime"] == "2026-11-19 09:30:00"
        assert payload["endTime"] == "2026-11-19 12:00:00"
        assert payload["constructionDesc"] == "振捣均匀，无漏振"
        assert payload["constructionUnit"] == "中建三局"
        assert payload["photoUrls"] == ["http://x/1.jpg"]
        assert payload["status"] == 2
        # 空可选不带：problems/treatment 空串不出现在 payload
        assert "issuesFound" not in payload
        assert "handlingMeasures" not in payload

    def test_missing_keypart_rejected(self):
        rec = _base_record() | {"section": "", "station_process": ""}
        with pytest.raises(MissingRequiredError, match="002"):
            map_sup002(
                rec, project_code="PRJ2026A", user_name="张三",
                photo_urls=["http://x/1.jpg"],
                created_time="2026-11-19 12:01:00", updated_time="2026-11-19 12:01:00",
            )

    def test_missing_photos_rejected(self):
        rec = _base_record() | {"station_process": "混凝土浇筑"}
        with pytest.raises(MissingRequiredError, match="photoUrls"):
            map_sup002(
                rec, project_code="PRJ2026A", user_name="张三", photo_urls=[],
                created_time="2026-11-19 12:01:00", updated_time="2026-11-19 12:01:00",
            )

    def test_public_file_url_hook(self):
        rec = _base_record() | {"station_process": "浇筑", "station_start_time": "2026-11-19 09:30:00"}
        payload = map_sup002(
            rec, project_code="PRJ", user_name="张三",
            photo_urls=["/uploads/a.jpg"],
            created_time="2026-11-19 12:01:00", updated_time="2026-11-19 12:01:00",
            public_file_url=lambda p: f"https://files.example.com{p}",
        )
        assert payload["photoUrls"] == ["https://files.example.com/uploads/a.jpg"]


# ---------------------------------------------------------------------------
# SUP-003 平行检验
# ---------------------------------------------------------------------------

class TestSUP003:
    def test_full_mapping(self):
        rec = _base_record() | {
            "acceptance_result": "合格",
            "acceptance_basis": "GB50204-2015",
            "content_json": {"method": "尺量", "instrument": "钢卷尺5m", "measure_data": "偏差3mm"},
        }
        payload = map_sup003(
            rec, project_code="PRJ2026B", user_name="李四",
            photo_urls=["http://x/2.jpg"],
            created_time="2026-11-19 10:00:00", updated_time="2026-11-19 10:00:00",
        )
        assert payload["inspectDate"] == "2026-11-19"
        assert payload["inspectPart"] == "3栋2层"
        assert payload["inspectBasis"] == "GB50204-2015"
        assert payload["inspectMethod"] == "尺量"
        assert payload["inspectEquipment"] == "钢卷尺5m"
        assert payload["inspectResult"] == 1          # 合格→1
        assert payload["status"] == 2

    def test_unqualified_result_is_zero(self):
        rec = _base_record() | {"acceptance_result": "不合格"}
        payload = map_sup003(
            rec, project_code="PRJ", user_name="李四", photo_urls=["http://x/2.jpg"],
            created_time="2026-11-19 10:00:00", updated_time="2026-11-19 10:00:00",
        )
        assert payload["inspectResult"] == 0

    def test_result_unknown_not_sent(self):
        rec = _base_record() | {"acceptance_result": "待整改"}
        payload = map_sup003(
            rec, project_code="PRJ", user_name="李四", photo_urls=["http://x/2.jpg"],
            created_time="2026-11-19 10:00:00", updated_time="2026-11-19 10:00:00",
        )
        assert "inspectResult" not in payload        # 不猜，宁缺勿错

    def test_missing_inspect_date_rejected(self):
        rec = _base_record() | {"record_date": None}
        with pytest.raises(MissingRequiredError, match="inspectDate"):
            map_sup003(
                rec, project_code="PRJ", user_name="李四", photo_urls=["http://x/2.jpg"],
                created_time="2026-11-19 10:00:00", updated_time="2026-11-19 10:00:00",
            )


# ---------------------------------------------------------------------------
# SUP-005 验收
# ---------------------------------------------------------------------------

class TestSUP005:
    def test_full_mapping(self):
        rec = _base_record() | {
            "acceptance_result": "整改后合格",
            "acceptance_participants": "监理：张三；施工：王五",
        }
        payload = map_sup005(
            rec, project_code="PRJ2026C", photo_urls=["http://x/3.jpg"],
            created_time="2026-11-19 11:00:00", updated_time="2026-11-19 11:00:00",
            acceptance_type="隐蔽工程",
        )
        assert payload["acceptanceType"] == "隐蔽工程"
        assert payload["acceptanceDate"] == "2026-11-19"
        assert payload["participants"] == "监理：张三；施工：王五"
        assert payload["conclusion"] == 1            # 含「合格」→1（整改后合格）
        assert payload["status"] == 2

    def test_bad_conclusion_not_guessed(self):
        rec = _base_record() | {"acceptance_result": "待整改"}
        payload = map_sup005(
            rec, project_code="PRJ", photo_urls=[], created_time="2026-11-19 11:00:00",
            updated_time="2026-11-19 11:00:00", acceptance_type="检验批",
        )
        assert "conclusion" not in payload

    def test_missing_acceptance_type_rejected(self):
        rec = _base_record()
        with pytest.raises(MissingRequiredError, match="acceptanceType"):
            map_sup005(
                rec, project_code="PRJ", photo_urls=[],
                created_time="2026-11-19 11:00:00", updated_time="2026-11-19 11:00:00",
                acceptance_type="",
            )


# ---------------------------------------------------------------------------
# SUP-006 监理日志
# ---------------------------------------------------------------------------

class TestSUP006:
    def test_full_mapping(self):
        rec = _base_record() | {
            "content_json": {
                "construction_progress": "3栋2层完成浇筑",
                "supervision_section": "旁站2次巡视1次",
                "problems_section": "无",
                "temperature_high": "31", "temperature_low": "22",
            },
        }
        payload = map_sup006(
            rec, project_code="PRJ2026D", project_name="四会大沙项目",
            created_time="2026-11-19 18:00:00", updated_time="2026-11-19 18:00:00",
        )
        assert payload["projectName"] == "四会大沙项目"
        assert payload["logDate"] == "2026-11-19"
        assert payload["weatherMorning"] == "晴"
        assert payload["temperatureHigh"] == 31 and payload["temperatureLow"] == 22
        assert payload["constructionProgress"] == "3栋2层完成浇筑"
        assert payload["patrolStatus"] == "旁站2次巡视1次"
        assert payload["foundProblems"] == "无"
        assert "logImages" not in payload            # 没图不带空数组

    def test_missing_log_date_rejected(self):
        rec = _base_record() | {"record_date": None}
        with pytest.raises(MissingRequiredError, match="logDate"):
            map_sup006(
                rec, project_code="PRJ", project_name="项目A",
                created_time="2026-11-19 18:00:00", updated_time="2026-11-19 18:00:00",
            )

    def test_photos_with_hook(self):
        payload = map_sup006(
            _base_record(), project_code="PRJ", project_name="项目A",
            created_time="2026-11-19 18:00:00", updated_time="2026-11-19 18:00:00",
            photo_urls=["/uploads/x.jpg"],
            public_file_url=lambda p: f"https://f.example.com{p}",
        )
        assert payload["logImages"] == ["https://f.example.com/uploads/x.jpg"]


# ---------------------------------------------------------------------------
# SUP-007 监理月报
# ---------------------------------------------------------------------------

class TestSUP007:
    def test_full_mapping(self):
        rec = _base_record() | {
            "content_json": {
                "project_overview": "本月完成3栋主体",
                "progress_plan": "计划完成80%实际78%",
                "safety_stats": "隐患2条已闭环",
                "work_stats": "通知单2份",
                "next_focus": "4栋主体结构验收",
                "submit_date": "2026-11-05",
            },
        }
        payload = map_sup007(
            rec, project_code="PRJ2026E", project_name="四会大沙项目",
            report_month="2026-11",
            created_time="2026-11-05 09:00:00", updated_time="2026-11-05 09:00:00",
            compiler="张三", reviewer="赵总",
        )
        assert payload["reportMonth"] == "2026-11"
        assert payload["projectOverview"] == "本月完成3栋主体"
        assert payload["progressDesc"] == "计划完成80%实际78%"
        assert payload["safetyDesc"] == "隐患2条已闭环"
        assert payload["compiler"] == "张三"
        assert payload["compileDate"] == "2026-11-05"
        assert payload["status"] == 2

    def test_report_month_falls_back_to_record_date(self):
        rec = _base_record() | {"record_date": "2026-10-20"}
        payload = map_sup007(
            rec, project_code="PRJ", project_name="项目A", report_month="",
            created_time="2026-11-05 09:00:00", updated_time="2026-11-05 09:00:00",
        )
        assert payload["reportMonth"] == "2026-10"

    def test_report_month_chinese_label_normalized(self):
        # period_report_service 的 period_label 是「2026年11月」中文
        rec = _base_record() | {"record_date": "2026-10-20"}
        payload = map_sup007(
            rec, project_code="PRJ", project_name="项目A", report_month="2026年11月",
            created_time="2026-11-05 09:00:00", updated_time="2026-11-05 09:00:00",
        )
        assert payload["reportMonth"] == "2026-11"

    def test_missing_month_and_date_rejected(self):
        rec = _base_record() | {"record_date": None}
        with pytest.raises(MissingRequiredError, match="reportMonth"):
            map_sup007(
                rec, project_code="PRJ", project_name="项目A", report_month="",
                created_time="2026-11-05 09:00:00", updated_time="2026-11-05 09:00:00",
            )


# ---------------------------------------------------------------------------
# SUP-012 材料进场
# ---------------------------------------------------------------------------

class TestSUP012:
    def test_full_mapping(self):
        rec = _base_record() | {
            "witness_material_name": "C30商品混凝土",
            "witness_material_spec": "C30 P6",
            "witness_quantity": "120",
            "witness_unit": "m³",
            "witness_cert_no": "HZ2026-118",
            "witness_accept_result": "合格",
            "content_json": {"manufacturer": "华润建材", "batch_no": "BT-09"},
        }
        payload = map_sup012(
            rec, project_code="PRJ2026F", checker_name="王五",
            photo_urls=["http://x/4.jpg"],
            created_time="2026-11-19 08:00:00", updated_time="2026-11-19 08:00:00",
        )
        assert payload["materialName"] == "C30商品混凝土"
        assert payload["materialSpec"] == "C30 P6"
        assert payload["quantity"] == 120 and payload["unit"] == "m³"
        assert payload["certificateNo"] == "HZ2026-118"
        assert payload["checkResult"] == 1
        assert payload["manufacturer"] == "华润建材"
        assert payload["materialBatch"] == "BT-09"
        assert payload["checkDate"] == "2026-11-19"   # record_date 优先

    def test_missing_material_name_rejected(self):
        rec = _base_record() | {"witness_material_name": None}
        with pytest.raises(MissingRequiredError, match="materialName"):
            map_sup012(
                rec, project_code="PRJ", checker_name="王五", photo_urls=[],
                created_time="2026-11-19 08:00:00", updated_time="2026-11-19 08:00:00",
            )

    def test_non_numeric_quantity_not_sent(self):
        rec = _base_record() | {"witness_material_name": "钢筋", "witness_quantity": "两车"}
        payload = map_sup012(
            rec, project_code="PRJ", checker_name="王五", photo_urls=[],
            created_time="2026-11-19 08:00:00", updated_time="2026-11-19 08:00:00",
        )
        assert "quantity" not in payload             # 转不了数值不带


# ---------------------------------------------------------------------------
# SUP-021 见证取样
# ---------------------------------------------------------------------------

class TestSUP021:
    def test_full_mapping(self):
        rec = _base_record() | {
            "witness_material_name": "钢筋焊接件",
            "witness_material_spec": "HRB400 Φ25",
            "witness_quantity": "3",
            "witness_unit": "组",
            "witness_cert_no": "JC-2026-088",
            "content_json": {"batch_no": "GT-2026-77", "test_result": "合格"},
        }
        payload = map_sup021(
            rec, project_code="PRJ2026G", witness_person="张三",
            photo_urls=["http://x/5.jpg"],
            created_time="2026-11-19 14:00:00", updated_time="2026-11-19 14:00:00",
        )
        assert payload["sampleNo"] == "GT-2026-77"    # 批号承载样品编号
        assert payload["sampleName"] == "钢筋焊接件"
        assert payload["sampleSpec"] == "HRB400 Φ25"
        assert payload["sampleQuantity"] == 3 and payload["unit"] == "组"
        assert payload["witnessPerson"] == "张三"
        assert payload["testReportNo"] == "JC-2026-088"
        assert payload["testResult"] == 1
        assert payload["samplingDate"] == "2026-11-19"

    def test_missing_sample_no_rejected(self):
        # 没有编号/批号/序号 → 拒收，不编造编号
        rec = _base_record() | {"witness_material_name": "钢筋"}
        with pytest.raises(MissingRequiredError, match="sampleNo"):
            map_sup021(
                rec, project_code="PRJ", witness_person="张三", photo_urls=[],
                created_time="2026-11-19 14:00:00", updated_time="2026-11-19 14:00:00",
            )

    def test_missing_witness_person_rejected(self):
        rec = _base_record() | {
            "witness_material_name": "钢筋",
            "content_json": {"batch_no": "GT-01"},
        }
        with pytest.raises(MissingRequiredError, match="witnessPerson"):
            map_sup021(
                rec, project_code="PRJ", witness_person="", photo_urls=[],
                created_time="2026-11-19 14:00:00", updated_time="2026-11-19 14:00:00",
            )
