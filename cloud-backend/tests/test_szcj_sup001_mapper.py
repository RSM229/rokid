"""SUP-001 巡视记录映射器回归测试。

跑法（venv 无 pytest）：
  cloud-backend/.venv/Scripts/python.exe -m unittest tests.test_szcj_sup001_mapper -v
"""
from __future__ import annotations

import unittest
from datetime import date, datetime

from app.services.szcj_sup001_mapper import (
    MissingRequiredError,
    assert_duplicate_ok,
    classify_content_type,
    map_sup001,
)


def _record(**overrides):
    base = {
        "id": 156,
        "status": "已提交",
        "record_date": date(2026, 11, 17),
        "inspection_start_time": datetime(2026, 11, 17, 9, 30),
        "inspection_end_time": datetime(2026, 11, 17, 10, 15),
        "section": "西楼A区十层",
        "work_content": "安全文明施工情况：临边防护到位；特种作业人员持证上岗。",
        "weather": "晴",
        "problems": "个别工人未戴安全帽",
        "treatment": "已当场要求整改",
        "content_json": {
            "doc_number": "GD-B-214-20261117-001",
            "construction_unit": "广州某建工集团",
            "quality_staff_on_job": "到位",
            "signature_url": "https://example.com/sign/156.png",
        },
    }
    base.update(overrides)
    return base


def _call(record=None, **kw):
    opts = {
        "project_code": "PRJ202600000854",
        "user_name": "张工",
        "photo_urls": ["https://example.com/photo/1.jpg"],
        "created_time": datetime(2026, 11, 17, 10, 20, 0),
        "updated_time": datetime(2026, 11, 17, 10, 25, 0),
    }
    opts.update(kw)
    return map_sup001(record or _record(), **opts)


class Sup001MapperTest(unittest.TestCase):

    def test_happy_path_required_fields_filled(self):
        body = _call()
        self.assertIn("data", body)
        d = body["data"]
        self.assertEqual(d["id"], 156)
        self.assertEqual(d["projectCode"], "PRJ202600000854")
        self.assertEqual(d["userName"], "张工")
        self.assertEqual(d["patrolDate"], "2026-11-17")
        self.assertEqual(d["patrolStartTime"], "09:30:00")
        self.assertEqual(d["patrolArea"], "西楼A区十层")
        self.assertEqual(d["photoUrls"], ["https://example.com/photo/1.jpg"])
        self.assertEqual(d["status"], 2)  # 已提交
        self.assertEqual(d["createdTime"], "2026-11-17 10:20:00")

    def test_optional_fields_only_when_present(self):
        d = _call()["data"]
        self.assertEqual(d["patrolNo"], "GD-B-214-20261117-001")
        self.assertEqual(d["constructionUnit"], "广州某建工集团")
        self.assertEqual(d["qualityStaffOnJob"], 1)
        self.assertEqual(d["foundProblems"], "个别工人未戴安全帽")
        self.assertEqual(d["signatureUrl"], "https://example.com/sign/156.png")
        self.assertNotIn("longitude", d)  # 源没有就不发

    def test_status_mapping(self):
        cases = {"草稿": 1, "进行中": 1, "已提交": 2, "已审批": 2, "已归档": 3}
        for src, expected in cases.items():
            with self.subTest(src=src):
                self.assertEqual(_call(_record(status=src))["data"]["status"], expected)
        self.assertEqual(_call(_record(status="未知态"))["data"]["status"], 1)

    def test_missing_required_raises_with_field_names(self):
        with self.assertRaises(MissingRequiredError) as ctx:
            _call(_record(section=""))
        self.assertIn("patrolArea", ctx.exception.fields)

    def test_missing_all_photos_raises(self):
        with self.assertRaises(MissingRequiredError) as ctx:
            _call(photo_urls=[])
        self.assertIn("photoUrls", ctx.exception.fields)

    def test_content_type_infer_and_explicit_override(self):
        self.assertEqual(classify_content_type("安全文明施工情况"), "安全")
        self.assertEqual(classify_content_type("使用的工程材料是否合格"), "质量")
        self.assertIsNone(classify_content_type("无关文本"))
        # 显式传值优先
        self.assertEqual(
            _call(content_type="质量")["data"]["contentType"], "质量"
        )
        # 推断不出时报错，不猜
        with self.assertRaises(MissingRequiredError) as ctx:
            _call(_record(work_content="无关键字"))
        self.assertIn("contentType", ctx.exception.fields)

    def test_mixed_safety_quality_refuses_guess(self):
        """真实巡视常同时写安全+质量两类，此时必须显式传 contentType，不许猜。"""
        mixed = _record(
            work_content="1.施工单位按设计文件施工 2.使用的工程材料合格 3.安全文明施工情况良好"
        )
        self.assertIsNone(classify_content_type(mixed["work_content"]))
        with self.assertRaises(MissingRequiredError) as ctx:
            _call(mixed)
        self.assertIn("contentType", ctx.exception.fields)
        # 显式给值后可通过
        self.assertEqual(
            _call(mixed, content_type="安全")["data"]["contentType"], "安全"
        )

    def test_public_file_url_hook(self):
        hook = lambda u: "https://cdn.example.com" + u.removeprefix("/uploads")
        d = _call(photo_urls=["/uploads/photo/x.jpg"], public_file_url=hook)["data"]
        self.assertEqual(d["photoUrls"], ["https://cdn.example.com/photo/x.jpg"])

    def test_explicit_id_for_idempotency(self):
        d = _call(created_id=9001)["data"]
        self.assertEqual(d["id"], 9001)

    def test_duplicate_response_treated_as_ok(self):
        self.assertTrue(assert_duplicate_ok({"code": 0, "data": {"status": "DUPLICATE_INSERT"}}))
        self.assertTrue(assert_duplicate_ok({"code": 0, "data": {"status": "DUPLICATE_UPDATE"}}))
        self.assertFalse(assert_duplicate_ok({"code": 1, "msg": "err"}))
        self.assertFalse(assert_duplicate_ok({"code": 0, "data": {"status": "INSERT"}}))


if __name__ == "__main__":
    unittest.main()
