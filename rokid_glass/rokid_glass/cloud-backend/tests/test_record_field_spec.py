# -*- coding: utf-8 -*-
"""字段表驱动追问：本地纯函数自测（不连库、不调模型）。

跑法：cd cloud-backend && python tests/test_record_field_spec.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import agent_tools as at            # noqa: E402
from app.services import record_field_spec as rfs     # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + detail))
    if not cond:
        FAILED.append(name)


def labels(fields) -> list[str]:
    return [str(f["label"]) for f in fields]


print("== 1. 追问集合与字段表一致 ==")
EXPECT = {
    ("巡视", False): ['巡视的工程部位', '巡视开始时间（年 月 日 时）', '巡视结束时间（年 月 日 时）',
                    '施工作业人数', '巡视的问题及处理情况'],
    ("旁站", False): ['天气', '旁站的关键部位', '旁站的关键工序', '旁站开始时间（年 月 日 时 分）',
                    '旁站结束时间（年 月 日 时 分）', '各专业作业人数情况', '机械设备情况',
                    '材料品牌、规格、数量情况', '施工单位质检员、安全员、施工员等到岗情况',
                    '施工单位是否完成自查自检并达到质量标准', '检查内容及检查结果情况',
                    '旁站发现的问题及处理情况'],
}
for (rtype, weida), want in EXPECT.items():
    got = labels(at.assist_fields(rtype, weida))
    check(f"{rtype} 追问 {len(want)} 项", got == want, f"got={got}")

check("巡视 不含天气（表上 GD-B-214 无天气栏）",
      "天气" not in labels(at.assist_fields("巡视")))
check("签名类不进追问",
      not any("签名" in x or "盖章" in x or "项目章" in x
              for r, w in [("巡视", False), ("旁站", False), ("验收", False),
                           ("监理", False), ("危大", True)]
              for x in labels(at.assist_fields(r, w))))
check("危大巡视 追问含风力（表标是但无气象接口）",
      "风力" in labels(at.assist_fields("巡视", True)))
check("危大巡视 追问含危大类型",
      "危大工程名称/类型/内容" in labels(at.assist_fields("巡视", True)))

print("== 2. 表里标「是」的字段不进追问 ==")
for code in rfs.FORMS:
    auto = labels(rfs.auto_fields(code))
    ask = set(labels(rfs.ask_fields(code)))
    check(f"{code} 系统生成字段与追问集合不重叠", not (set(auto) & ask),
          f"overlap={sorted(set(auto) & ask)}")

print("== 3. extra_fields 槽位读写 ==")
f_qa = next(f for f in rfs.ask_fields("gd_b_212_pangzhan") if f["canonical"] == "qa_onsite")
args: dict = {}
check("缺值判定为空", at._field_value(args, f_qa) == "")
args = at._set_field_value(args, f_qa, "是")
check("写入 extra_fields（槽位=canonical）",
      args.get("extra_fields", {}).get(f_qa["canonical"]) == "是", f"got={args.get('extra_fields')}")
check("读回", at._field_value(args, f_qa) == "是")

f_sec = next(f for f in rfs.ask_fields("gd_b_212_pangzhan") if f["canonical"] == "section")
check("旁站部位 → station_part 参数", f_sec["arg"] == "station_part")
args = at._set_field_value(args, f_sec, "西楼A区十层墙柱")
check("写入独立参数", args.get("station_part") == "西楼A区十层墙柱")

print("== 4. 原话兜底回填（按追问标签反查字段）==")
out = at.refill_missing_from_message({"record_type": "旁站"}, ["施工单位质检员、安全员、施工员等到岗情况"], "都到岗了")
check("旁站到岗情况回填 extra_fields",
      out and out["extra_fields"].get(f_qa["canonical"]) == "都到岗了", f"out={out}")
out2 = at.refill_missing_from_message(
    {"record_type": "巡视"}, ["巡视的工程部位"], "3号楼二层临边防护，无异常")
check("巡视部位回填 section", out2 and out2.get("section") == "3号楼二层临边防护，无异常", f"out={out2}")
check("疑问句不回填",
      at.refill_missing_from_message({"record_type": "巡视"}, ["巡视的工程部位"], "这是哪？") is None)
check("跳过词不回填",
      at.refill_missing_from_message({"record_type": "巡视"}, ["巡视的工程部位"], "跳过") is None)
check("已有值不覆盖",
      at.refill_missing_from_message({"record_type": "巡视", "section": "已填"}, ["巡视的工程部位"], "3号楼") is None)
out3 = at.refill_missing_from_message(
    {"record_type": "巡视"}, ["巡视开始时间（年 月 日 时）"], "9点到11点")
check("时间字段不做整句兜底（交给区间解析）", out3 is None, f"out={out3}")

print("== 5. 草稿合并保留 extra_fields ==")
m = at.merge_args({"extra_fields": {"1_13": "是"}, "section": "A"},
                  {"extra_fields": {"1_14": "否"}, "station_process": "浇筑"})
check("extra_fields 深合并", m.get("extra_fields") == {"1_13": "是", "1_14": "否"}, f"got={m.get('extra_fields')}")
check("普通参数照常合并", m.get("section") == "A" and m.get("station_process") == "浇筑")

print("== 6. 起止时间已给即不再追问 ==")
ws = at.assist_fields("巡视")
check("给了时间段原文 → 起止都不追问",
      all(at._time_filled({"inspection_time": "9:00 到 11:00"}, f)
          for f in ws if f["canonical"] in ("start_time", "end_time")))
check("独立参数给了也算已答",
      at._time_filled({"inspection_start_time": "09:00"},
                      next(f for f in ws if f["canonical"] == "start_time")))

print("== 7. 追问选项 ==")
check("天气给枚举", rfs.options_for(next(f for f in at.assist_fields("旁站") if f["canonical"] == "weather"))
      == ['晴', '多云', '阴', '小雨', '大雨', '大风'])
check("追问话术取 override",
      rfs.question_for(next(f for f in at.assist_fields("巡视") if f["canonical"] == "section")).startswith("这次的工程部位"))
check("无 override 走通用模板",
      "请补充" in rfs.question_for(next(f for f in at.assist_fields("旁站") if f["canonical"] == "equipment")))

print("== 8. 补充回答的命中判定 ==")
d = {"record_type": "旁站"}
lbl = "施工单位质检员、安全员、施工员等到岗情况"
check("extra_fields 命中", at.supplement_hits({"extra_fields": {"1_13": "是"}}, d, [lbl]))
check("标签本身命中", at.supplement_hits({lbl: "是"}, d, [lbl]))
check("参数名命中",
      at.supplement_hits({"station_process": "浇筑"}, d, ["旁站的关键工序"]))
check("不相干的补充不命中",
      not at.supplement_hits({"foo": "bar"}, d, [lbl]))
check("无缺失则不命中", not at.supplement_hits({"extra_fields": {"1_13": "是"}}, d, []))
hint = at.ask_hint(d, ["旁站的关键工序", lbl])
check("hint 区分参数与槽位",
      "参数 station_process" in hint and 'extra_fields["qa_onsite"]' in hint, f"hint={hint}")

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    sys.exit(1)
print("ALL PASS")
