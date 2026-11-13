"""服务侧质量守护的单元测试：13 项冒烟、退化检测、字段映射、只读汇总。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import doc_quality, quality_guard


class TestSmokeChecks(unittest.IsolatedAsyncioTestCase):
    async def test_all_13_checks_pass(self):
        report = await quality_guard.run_smoke_checks()
        self.assertEqual(report["total"], 13, report["checks"])
        self.assertEqual(report["passed"], 13,
                         [c for c in report["checks"] if not c["ok"]])
        self.assertTrue(report["ok"])

    async def test_smoke_detects_broken_placeholder_rule(self):
        """守护的价值：占位词规则被改坏时必须报红，而不是静默通过。"""
        broken = frozenset({"未知"})
        with patch.object(doc_quality, "PLACEHOLDERS", broken):
            report = await quality_guard.run_smoke_checks()
        self.assertFalse(report["ok"])
        failed = [c["name"] for c in report["checks"] if not c["ok"]]
        self.assertIn("占位词集合完整", failed)
        self.assertIn("占位词算缺项", failed)

    async def test_smoke_detects_broken_no_issue_boundary(self):
        """「无问题」被误判成缺项也要报红（11-13 回填边界）。"""
        with patch.object(doc_quality, "_is_blank", lambda value: (True, "placeholder")):
            report = await quality_guard.run_smoke_checks()
        self.assertIn("无问题不算缺项",
                      [c["name"] for c in report["checks"] if not c["ok"]])

    async def test_uncovered_form_is_warning_not_failure(self):
        report = await quality_guard.run_smoke_checks()
        self.assertTrue(report["ok"])
        self.assertTrue(any("起重机械安拆检查" in w for w in report["warnings"]),
                        report["warnings"])


class TestRecordValues(unittest.TestCase):
    def test_maps_common_aliases(self):
        row = SimpleNamespace(
            record_type="巡视", section="3#5F 顶板", station_location=None,
            location=None, work_content="检查钢筋", station_content=None,
            problems="无问题", treatment=None, weather="晴", worker_count=8,
            station_process=None, station_start_time=None, station_end_time=None,
            station_conclusion=None, acceptance_result=None,
            inspection_start_time=None, content_json={"acceptance_basis": "GB50204"},
        )
        values = quality_guard._record_values(row)
        self.assertEqual(values["section"], "3#5F 顶板")
        self.assertEqual(values["work_content"], "检查钢筋")
        self.assertEqual(values["problems"], "无问题")
        self.assertEqual(values["acceptance_basis"], "GB50204")
        self.assertEqual(doc_quality._is_blank(values["problems"])[0], False)

    def test_placeholder_in_record_is_detected(self):
        row = SimpleNamespace(
            record_type="巡视", section="待补", station_location=None,
            location=None, work_content=None, station_content=None,
            problems="待定", treatment=None, weather=None, worker_count=None,
            station_process=None, station_start_time=None, station_end_time=None,
            station_conclusion=None, acceptance_result=None,
            inspection_start_time=None, content_json=None,
        )
        missing = doc_quality.missing_fields("巡视", quality_guard._record_values(row))
        self.assertTrue(any(x["reason"] == "placeholder" for x in missing), missing)


class TestScanDegrades(unittest.IsolatedAsyncioTestCase):
    async def test_db_failure_returns_ok_false_not_exception(self):
        class Broken:
            def __call__(self):
                raise RuntimeError("DB down")

        with patch.object(quality_guard, "async_session", Broken()):
            report = await quality_guard.scan_missing_summary()
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
