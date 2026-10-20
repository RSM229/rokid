# -*- coding: utf-8 -*-
"""分步表单组装：本地纯函数自测（不连库、不调模型、不写文件）。

跑法：cd cloud-backend && python tests/test_record_steps.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import provform_schema as pfs    # noqa: E402
from app.services import record_field_spec as rfs  # noqa: E402
from app.services import record_steps as rs        # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + detail))
    if not cond:
        FAILED.append(name)


def fillable(step: dict) -> int:
    return sum(1 for f in step["fields"] if not f["readonly"])


def all_keys(data: dict) -> set[str]:
    return {f["key"] for s in data["steps"] for f in s["fields"]}


print("== 1. 每张表都能组装出合法分步 ==")
for code in sorted(rfs.FORMS):
    data = rs.build_steps(code)
    check(f"{code} 有结果", data is not None)
    if not data:
        continue
    counts = [fillable(s) for s in data["steps"]]
    check(f"{code} 步数为正", data["step_count"] > 0, str(data["step_count"]))
    check(f"{code} 无空步（>=1 可填）", all(c >= 1 for c in counts), str(counts))
    check(f"{code} 每步 <= {rs.MAX_STEP_FIELDS}", all(c <= rs.MAX_STEP_FIELDS for c in counts), str(counts))
    check(f"{code} 字段不重复", len(all_keys(data)) == sum(len(s["fields"]) for s in data["steps"]))


print("\n== 2. 字段表驱动 vs 退回模式 ==")
for code in sorted(rfs.FORMS):
    data = rs.build_steps(code) or {}
    check(f"{code} source=field_spec", data.get("source") == "field_spec", str(data.get("source")))
for code in ["gd_b_217_huiyi", "anquan_jianli_rizhi"]:
    data = rs.build_steps(code) or {}
    check(f"{code} 退回 schema_only", data.get("source") == "schema_only", str(data.get("source")))
check("无 schema 的表返回 None", rs.build_steps("not_a_form") is None)


print("\n== 3. SPEC_DROP 的表栏必须被隐藏（不能退化成可填项）==")
EXPECT_DROP = {
    "gd_b_214_xunshi": {"3_08"},       # 房建巡视无「施工作业人数」
    "gd_b_219_cailiao": {"9_13"},      # 材料台账无「报审表编号」
    "gdaq_30308_yinhuan": {"5_11", "5_12", "5_15"},
}
for code, want in EXPECT_DROP.items():
    data = rs.build_steps(code) or {}
    keys = all_keys(data)
    check(f"{code} 隐藏栏齐全", set(data.get("dropped") or []) == want, str(data.get("dropped")))
    check(f"{code} 隐藏栏不出现在步骤里", not (keys & want), str(keys & want))
    for k in want:
        check(f"{code} {k} 不可填", all(f["readonly"] or f["key"] != k
                                      for s in data["steps"] for f in s["fields"]))

xunshi = rs.build_steps("gd_b_214_xunshi") or {}
labels = [f["label"] for s in xunshi["steps"] for f in s["fields"]]
check("巡视不再出现「施工作业人数」", "施工作业人数" not in labels, str(labels))


print("\n== 4. 只读判定跟随字段表 ==")
for code in sorted(rfs.FORMS):
    data = rs.build_steps(code) or {}
    spec = {f["key"]: f for f in rfs.fields_of(code) if f.get("key")}
    bad = []
    for s in data["steps"]:
        for f in s["fields"]:
            kind = (spec.get(f["key"]) or {}).get("kind")
            if kind and f["kind"] != kind:
                bad.append((f["key"], kind, f["kind"]))
    check(f"{code} kind 与字段表一致", not bad, str(bad[:3]))


print("\n== 5. 预填映射（canonical / arg / schema key 混写）==")
pre = {
    "section": "西楼十层",
    "start_time": "2026-10-19 09:00",
    "end_time": "2026-10-19 11:00",
    "problems": "无异常",
    "project_name": "某项目",
    "doc_number": "GD-B-214-001",
    "3_03": "被同名 canonical 覆盖前先看",   # schema key 直写
    "未知键": "x",
    "worker_count": "12",                  # 对应已隐藏的 3_08
}
data = rs.build_steps("gd_b_214_xunshi", pre, "draft") or {}
vals = {f["key"]: f["value"] for s in data["steps"] for f in s["fields"]}
check("source 标记为 draft", data.get("prefill_source") == "draft")
check("工程部位预填", vals.get("3_03") == "被同名 canonical 覆盖前先看", str(vals.get("3_03")))
check("开始时间预填", vals.get("3_05") == "2026-10-19 09:00", str(vals.get("3_05")))
check("结束时间预填", vals.get("3_06") == "2026-10-19 11:00", str(vals.get("3_06")))
check("问题处理预填", vals.get("3_09") == "无异常", str(vals.get("3_09")))
check("系统带出值也回填", vals.get("3_01") == "某项目", str(vals.get("3_01")))
check("未知键被忽略", "未知键" not in vals)
check("隐藏栏的值不计入预填数", data.get("prefill_count") == 6, str(data.get("prefill_count")))
check("全部必填已填 -> 无缺项", data.get("missing") == [], str(data.get("missing")))

print("\n== 6. 缺项计算 ==")
empty = rs.build_steps("gd_b_214_xunshi", {}, "none") or {}
check("空预填时缺项 = 必填可填项", empty.get("missing_count") == 2, str(empty.get("missing")))
partial = rs.build_steps("gd_b_214_xunshi", {"section": "西楼十层"}, "draft") or {}
check("部分预填后缺项减少", partial.get("missing_count") == 1, str(partial.get("missing")))
check("非标量预填被丢弃（items 不进单值）",
      rs.prefill_to_keys("gd_b_219_cailiao", {"items": [{"name": "水泥"}], "project_name": "P"})
      == {"9_01": "P"})
check("布尔预填转「是/否」",
      rs.prefill_to_keys("gd_b_214_xunshi", {"problems": True}) == {"3_09": "是"})

pz = rs.build_steps("gd_b_212_pangzhan",
                    {"station_part": "西楼A区", "station_process": "墙柱浇筑",
                     "start_time": "08:00", "end_time": "11:00", "content": "顺利",
                     "weather": "晴", "worker_count": "18", "equipment": "泵车",
                     "material_info": "C30", "qa_onsite": "是", "self_check": "是",
                     "problems": "无"}, "draft") or {}
check("旁站 12 项追问全预填后无缺项", pz.get("missing_count") == 0, str(pz.get("missing")))


print("\n== 7. 字段结构完整性 ==")
for code in sorted(rfs.FORMS):
    data = rs.build_steps(code) or {}
    bad = [f for s in data["steps"] for f in s["fields"]
           if not f.get("key") or not f.get("label") or not f.get("type")]
    check(f"{code} 字段必有 key/label/type", not bad, str(bad[:2]))
    check(f"{code} 步骤 index 连续",
          [s["index"] for s in data["steps"]] == list(range(data["step_count"])))


print("\n" + ("ALL PASS" if not FAILED else f"FAILED {len(FAILED)}: {FAILED}"))
sys.exit(0 if not FAILED else 1)
