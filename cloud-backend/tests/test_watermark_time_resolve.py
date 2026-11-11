# -*- coding: utf-8 -*-
"""🔴 2026-11-11 回归：照片水印拍摄时间在结构化表单路径的落位。

背景：用户上传带时间水印的照片，系统却反问「巡视开始时间」。
根因：_INPUT_ALIASES 反向索引里 taken_at / record_date 被多重映射覆盖——
      taken_at → sample_date、record_date → sample_date，导致巡视/旁站表单
      的 date、start_time 判缺失，水印时间写进没人读的字段。
修复：taken_at 仅作为水印专用字段，在 resolve_event_form 里显式拆分
      为 record_date（日期）+ start_time / station_start_time（时刻）；
      record_date 反向只映射 date。

跑法：cd cloud-backend && python tests/test_watermark_time_resolve.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.services.agent_form_requirements import _CANONICAL_INPUT_FIELDS, resolve_event_form

WM = {
    "taken_at": "2026-10-19 15:42:10",
    "location": "广州市黄埔区XX路",
    "section": "3号楼二层东侧",
    "weather": "晴",
}

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + detail))
    if not cond:
        FAILED.append(name)


def resolve(record_type: str) -> dict:
    return resolve_event_form(
        {"evidence": [{"id": "1", "watermark_fields": WM}]},
        {"record_type": record_type, "evidence_ids": ["1"], "content": "检查模板支架"},
    )


print("== 0. 反向别名索引无污染 ==")
check("record_date → date", _CANONICAL_INPUT_FIELDS.get("record_date") == "date")
check("taken_at 不再被 sample_date 抢占",
      _CANONICAL_INPUT_FIELDS.get("taken_at") not in ("sample_date", "time"),
      f"taken_at -> {_CANONICAL_INPUT_FIELDS.get('taken_at')}")


print("\n== 1. 巡视：水印时间落位开始时间（含日期），不再反问 ==")
res = resolve("巡视")
start = res["values"].get("start_time", "")
check("start_time=2026-10-19 15:42（规范化完整值）",
      start == "2026-10-19 15:42", str(res["values"]))
# 巡视表单无独立 date 槽位，日期并进开始时间；来源登记为 watermark（sources 在行上）
check("时间来源记 watermark", res["sources"].get("start_time") == "watermark", str(res["sources"]))
check("无缺失", res["missing_fields"] == [], str(res["missing_fields"]))
check("无 invalid", res["invalid_fields"] == [], str(res["invalid_fields"]))
check("can 建档", res["write_allowed"] is True, str(res["write_allowed"]))


print("\n== 2. 旁站：时间落站站开始时间 ==")
res2 = resolve("旁站")
check("station_process 等业务字段缺失可接受",
      "station_start_time" not in res2["values"], str(res2["values"]))
# 旁站表单对 start_time 的读取依赖表单槽位；station_start_time 落值后不再报
check("不把时间当 invalid",
      not any(i["canonical"] in {"start_time", "station_start_time"} for i in res2["invalid_fields"]),
      str(res2["invalid_fields"]))


print("\n== 3. 见证：sample_date 不被水印日期脏填（语义独立） ==")
res3 = resolve("见证")
check("sample_date 不自动带水印日期",
      res3["values"].get("sample_date", "") != "2026-10-19",
      str(res3["values"]))


print("\n== 4. 兼容：无 taken_at 的水印不产生时间 ==")
res4 = resolve_event_form(
    {"evidence": [{"id": "1", "watermark_fields": {"location": "某处"}}]},
    {"record_type": "巡视", "evidence_ids": ["1"], "content": "检查"},
)
check("无 start_time", "start_time" not in res4["values"], str(res4["values"]))
check("巡视仍缺开始时间", "start_time" in res4["missing_fields"], str(res4["missing_fields"]))


print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项：{FAILED}"))
sys.exit(1 if FAILED else 0)