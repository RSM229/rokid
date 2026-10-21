# -*- coding: utf-8 -*-
"""照片水印 → 记录字段预填：本地纯函数自测（不连库、不调模型、不写文件）。

跑法：cd cloud-backend && python tests/test_watermark_prefill.py

本机 venv 缺 fastapi/pydantic，无法直接 import app.routers.agent，
这里按 AST 从源码里取出目标函数单独执行，保证测的就是生产源码。
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "app", "routers", "agent.py")

_CONSTS = {"_PREFILL_START_ARG", "_TIME_RE", "_DATE_RE"}
_FUNCS = {"_hms", "_iso_date", "apply_prefill"}

ns: dict = {"re": re, "Any": object}
tree = ast.parse(open(SRC, encoding="utf-8").read())
for node in tree.body:
    found = False
    if isinstance(node, ast.Assign):
        found = any(getattr(t, "id", None) in _CONSTS for t in node.targets)
    elif isinstance(node, ast.FunctionDef):
        found = node.name in _FUNCS
    if found:
        exec(compile(ast.Module([node], type_ignores=[]), SRC, "exec"), ns)

apply_prefill = ns["apply_prefill"]

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "   " + detail))
    if not cond:
        FAILED.append(name)


print("== 1. 巡视：水印日期/部位/地点/天气/拍摄时间都填进表 ==")
wm = {
    "taken_at": "2026-10-19 15:42:10",
    "location": "广州市黄埔区XX路",
    "section": "3号楼二层东侧",
    "weather": "晴",
}
args, filled = apply_prefill({}, wm, "巡视")
check("record_date", args.get("record_date") == "2026-10-19", str(args))
check("section", args.get("section") == "3号楼二层东侧", str(args))
check("location", args.get("location") == "广州市黄埔区XX路", str(args))
check("weather", args.get("weather") == "晴", str(args))
check("inspection_start_time=15:42", args.get("inspection_start_time") == "15:42", str(args))
check("回执列出已填项", len(filled) == 5, str(filled))

print("\n== 2. 旁站：部位进 station_part、时间进 station_start_time ==")
args2, _ = apply_prefill({}, wm, "旁站")
check("station_part", args2.get("station_part") == "3号楼二层东侧", str(args2))
check("station_start_time", args2.get("station_start_time") == "15:42", str(args2))
check("不写巡视的时间键", "inspection_start_time" not in args2, str(args2))

print("\n== 3. 水印客观字段优先：冲突时采用水印，用户明确时段不覆盖 ==")
args3, filled3 = apply_prefill(
    {"section": "地下室顶板", "inspection_start_time": "09:00"},
    wm, "巡视",
)
check("section 采用水印", args3.get("section") == "3号楼二层东侧", str(args3))
check("时间保持用户原话", args3.get("inspection_start_time") == "09:00", str(args3))
check("覆盖项说明采用水印", "改用照片水印" in "、".join(filled3), str(filled3))

print("\n== 3.1 用户在表单中明确修改的字段受保护 ==")
args31, filled31 = apply_prefill(
    {"section": "地下室顶板", "record_date": "2026-10-18", "weather": "阴"},
    wm,
    "巡视",
    protected_keys={"section", "record_date", "weather"},
)
check("section 保留人工修改", args31.get("section") == "地下室顶板", str(args31))
check("record_date 保留人工修改", args31.get("record_date") == "2026-10-18", str(args31))
check("weather 保留人工修改", args31.get("weather") == "阴", str(args31))
check("受保护字段不回填", not any(label in "、".join(filled31) for label in ("工程部位", "日期", "天气")), str(filled31))

print("\n== 4. 时间格式兼容与非法值 ==")
for raw, want in [("2026/10/19 9:05", "09:05"), ("2026年10月19日 15时42分", "15:42"),
                  ("2026-10-19", ""), ("", ""), ("2026-10-19 99:99", "")]:
    got = ns["_hms"](raw)
    check(f"_hms({raw!r})={want!r}", got == want, got)
check("_iso_date 中文日期", ns["_iso_date"]("2026年10月19日 15:42") == "2026-10-19")

print("\n== 5. 验收/见证：没有时间参数映射时不乱塞键 ==")
args5, _ = apply_prefill({}, wm, "验收")
check("无空键", "" not in args5, str(args5))
check("日期仍填", args5.get("record_date") == "2026-10-19", str(args5))

print("\n== 6. 空水印：不产生任何字段 ==")
args6, filled6 = apply_prefill({"record_type": "巡视"}, {}, "巡视")
check("args 不变", args6 == {"record_type": "巡视"}, str(args6))
check("无回执项", filled6 == [], str(filled6))

print("\n" + ("全部通过" if not FAILED else f"失败 {len(FAILED)} 项：{FAILED}"))
sys.exit(1 if FAILED else 0)
