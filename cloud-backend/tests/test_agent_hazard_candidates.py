import importlib.util
from pathlib import Path


MODULE = Path(__file__).parents[1] / "app" / "services" / "agent_hazard_candidates.py"
spec = importlib.util.spec_from_file_location("agent_hazard_candidates", MODULE)
assert spec and spec.loader
candidates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidates)


def hit(**overrides):
    value = {"has_hazard": True, "risk_status": "存在隐患", "top1_risk": "临边防护缺失"}
    value.update(overrides)
    return value


def test_normalizes_selected_hazard_candidate_without_writing():
    result = candidates.normalize_candidates([{
        "media_id": 18,
        "detection": hit(evidence=["栏杆缺失"], confidence=0.91),
        "section": "  三层东侧  ",
        "taken_at": "2026-10-18T09:30:00",
    }])
    assert result[0]["media_id"] == 18
    assert result[0]["section"] == "三层东侧"
    assert result[0]["detection"]["top1_risk"] == "临边防护缺失"
    assert result[0]["taken_at"].startswith("2026-10-18T09:30:00")


def test_rejects_non_hazard_and_duplicate_media_in_one_batch():
    try:
        candidates.normalize_candidates([{
            "media_id": 1, "detection": {"has_hazard": False, "risk_status": "未发现隐患"},
        }])
    except candidates.HazardCandidateError as exc:
        assert "未命中隐患" in str(exc)
    else:
        raise AssertionError("non-hazard candidate should fail")

    try:
        candidates.normalize_candidates([
            {"media_id": 2, "detection": hit()},
            {"media_id": 2, "detection": hit()},
        ])
    except candidates.HazardCandidateError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("duplicate media should fail")


def test_rejects_invalid_timestamp_before_database_write():
    try:
        candidates.normalize_candidates([{
            "media_id": 3, "detection": hit(), "taken_at": "10月18日",
        }])
    except candidates.HazardCandidateError as exc:
        assert "拍摄时间" in str(exc)
    else:
        raise AssertionError("invalid timestamp should fail")
