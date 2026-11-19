import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.services import period_report_service


def test_weekly_aggregate_uses_period_values_without_daily_weather_locals(monkeypatch):
    # sync_document_to_project_archive._ensure_document_file 会按
    # 「docx_path 对应文件存在」决定是否兜底重渲染（函数内 import 真
    # render_form，patch 不到）。fake 渲染产物必须真实落盘，否则兜底
    # 会用真渲染覆盖 docx_path，断言漂移。
    fake_out = Path(period_report_service.settings.export_dir) / "weekly-report.docx"
    fake_out.write_bytes(b"PK fake docx")
    aggregate = {
        "work_records": 2, "hazards": 0,
        "construction_progress": "完成两项工作",
        "material_section": "无材料见证取样。",
        "supervision_section": "完成现场巡视。",
        "acceptance_section": "无验收记录。",
        "other_work_section": "无。",
        "problems_section": "无安全隐患及质量问题。",
        # 🔴 2026-10-27：平行检验汇总已收敛到 aggregate_records 单一出口，
        # generate_aggregate_doc 不再自己重复查 ParallelInspection 并二次格式化
        # （两份格式化逻辑必然漂移 + 多打一次库）。所以这里必须在 aggregate
        # 结果里提供 parallel_section，下方 FakeDb.execute 的平行检验替身不再被消费。
        "parallel_section": "A区 钢筋间距：实测符合，结论：合格，依据：图纸，方法：尺量",
    }

    async def aggregate_records(*_args):
        return aggregate

    async def pick_creator(*_args):
        return SimpleNamespace(id=4, real_name="陈宇文", supervisor_level="总监")

    class FakeDb:
        def __init__(self):
            self.rows = []

        async def get(self, model, _id):
            if model.__name__ == "Project":
                return SimpleNamespace(id=_id, name="项目A")
            return None

        async def execute(self, _statement):
            row = SimpleNamespace(
                part="A区", item="钢筋间距", result_detail="实测符合",
                conclusion="合格", basis="图纸", method="尺量",
            )
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [row]),
            )

        def add(self, row):
            self.rows.append(row)

        async def flush(self):
            for index, row in enumerate(self.rows, 1):
                row.id = index

        async def commit(self):
            return None

    monkeypatch.setattr(period_report_service, "aggregate_records", aggregate_records)
    monkeypatch.setattr(period_report_service, "pick_creator", pick_creator)
    monkeypatch.setattr(period_report_service, "project_fields", lambda _project: {})
    monkeypatch.setattr(period_report_service, "render_form",
                        lambda _form, _content: SimpleNamespace(name=fake_out.name))
    db = FakeDb()

    result = asyncio.run(period_report_service.generate_aggregate_doc(
        db, 7, "week", "2026-10-26", use_llm=False,
    ))

    doc = db.rows[0]
    assert result["form_code"] == "gd_b_223_zhoubao"
    assert doc.content["weekday"] == "一"
    assert doc.content["parallel_section"] == "A区 钢筋间距：实测符合，结论：合格，依据：图纸，方法：尺量"
    assert doc.content["weather_source"] == ""
    assert doc.docx_path == "/exports/weekly-report.docx"
