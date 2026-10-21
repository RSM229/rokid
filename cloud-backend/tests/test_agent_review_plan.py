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
            "evidence_ids": ["p1"], "forms": ["acceptance_record"],
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


def test_plan_rejects_unconfirmed_date_or_unknown_form():
    plan = base_plan()
    plan["unassigned_ids"] = []
    plan["events"][0]["date_confirmed"] = False
    review_plan.validate_plan(plan)
    assert any("日期待确认" in issue for issue in review_plan.review_issues(plan))
    plan["events"][0]["forms"] = ["guess_form"]
    try:
        review_plan.validate_plan(plan)
    except ValueError as exc:
        assert "不支持的表式" in str(exc)
    else:
        raise AssertionError("unknown form should fail")


def test_empty_evidence_text_only_event_is_valid():
    plan = base_plan()
    plan["evidence"] = []
    plan["unassigned_ids"] = []
    plan["events"][0]["evidence_ids"] = []
    review_plan.validate_plan(plan)
    assert not review_plan.review_issues(plan)


def test_form_specific_fields_must_be_objects():
    plan = base_plan()
    plan["events"][0]["fields"] = {"gd_b_213_pingxing": "合格"}
    try:
        review_plan.validate_plan(plan)
    except ValueError as exc:
        assert "专用字段" in str(exc)
    else:
        raise AssertionError("non-object form fields should fail")
