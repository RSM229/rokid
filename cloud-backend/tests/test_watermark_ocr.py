"""水印识别端到端自测：合成带水印图片 → OCR → 字段抽取。不依赖 FastAPI。"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont

from app.services.watermark_ocr import recognize_watermark, parse_watermark_fields


def make_image(lines: list[str]) -> bytes:
    img = Image.new("RGB", (800, 600), (120, 140, 110))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)
    bg_h = len(lines) * 36 + 20
    draw.rectangle([10, 600 - bg_h - 10, 620, 590], fill=(0, 0, 0))
    y = 600 - bg_h
    for line in lines:
        draw.text((20, y), line, fill=(255, 255, 255), font=font)
        y += 36
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


CASES = [
    (
        "自有水印样式",
        ["项目:海珠湾隧道工程", "监理:张三", "时间:2026-10-19 09:32:05", "位置:广州市南沙区大岗镇", "部位:东湾2栋10层", "天气:晴 28℃"],
        {"project_name": "海珠湾隧道工程", "inspector": "张三", "location": "广州市南沙区大岗镇", "section": "东湾2栋10层"},
    ),
    (
        "水印相机样式(无标签)",
        ["2026年10月18日 14:05", "广东省广州市海珠区新港东路100号", "多云 25℃", "李四"],
        {},
    ),
]

parser_cases = [
    # (输入行, 期望字段子集)
    (["时间:2026-10-19 09:32:05"], {"taken_at": "2026-10-19T09:32:05"}),
    (["2026年10月18日 14:05"], {"taken_at": "2026-10-18T14:05:00"}),
    (["经度:113.2956,纬度:23.0974"], {}),
    (["113.2956,23.0974"], {"lat": 23.0974, "lon": 113.2956}),
    (["天气:雷阵雨"], {"weather": "雷阵雨"}),
    (["记录人:王五"], {"inspector": "王五"}),
    (["施工区域：西楼外脚手架搭设"], {"section": "西楼外脚手架搭设"}),
    (["危大名称脚手架工程"], {"hazard_name": "脚手架工程"}),
    (["检查人员：刘幸、陈宇文"], {"inspector": "刘幸、陈宇文"}),
    (["监理：刘幸"], {"inspector": "刘幸"}),
    (["监 理单位：广州珠江监理咨询集团有限公司"], {"supervision_unit": "广州珠江监理咨询集团有限公司"}),
    (["拍摄时间：2026.10.0915:48"], {"taken_at": "2026-10-09T15:48:00"}),
    (["检查人员", "陈宇文"], {"inspector": "陈宇文"}),
    (["地点:", "广州市南沙区·广东省中医院南沙", "医院(装修中)"], {"location": "广州市南沙区·广东省中医院南沙"}),
    (["施工区域:西楼十一层A区混凝土浇筑旁站"], {"section": "西楼十一层A区混凝土浇筑旁站"}),
    (["施工区域:西楼十一层A区混凝土浇筑旁站"], {"location": None}),
    ([
        "广东省中西医结合应急救治", "中心项目", "珠江监理", "施工区域：安全巡视",
        "检查人员：陈宇文", "拍摄时间：2026.10.0915:48",
        "广州市南沙区·广东省中医院南沙", "建设单位：广州市南沙区建设中心",
        "监理单位：广州珠江监理咨询集团有限公司", "施工单位：广州一建建设集团有限公司",
    ], {"project_name": "广东省中西医结合应急救治中心项目", "inspector": "陈宇文"}),
    (["项目名称：海珠湾工程", "施工区域：西楼A区", "危大工程名称：脚手架", "检查人员：张三", "拍摄时间：2026-10-18 09:30", "地点：西楼", "建设单位：甲公司", "监理单位：乙公司", "施工单位：丙公司", "备注：复查东侧", "备注二：补充材料"], {
        "project_name": "海珠湾工程", "section": "西楼A区", "hazard_name": "脚手架", "inspector": "张三",
        "taken_at": "2026-10-18T09:30:00", "location": "西楼",
        "owner_unit": "甲公司", "supervision_unit": "乙公司", "construction_unit": "丙公司",
    }),
    (["现场人员佩戴安全帽", "施工单位名称", "丙公司", "自定义标签：自定义值", "备注：", "备注：第二条备注"], {"construction_unit": "丙公司"}),
    (["没有水印的现场杂字", "墙面编号 A:B"], {}),
]

ok = True
for name, expect in [(f"case{i}", c) for i, c in enumerate(parser_cases)]:
    lines, expect_subset = expect
    fields = parse_watermark_fields(lines)
    for k, v in expect_subset.items():
        if fields.get(k) != v:
            ok = False
            print(f"[FAIL] parser {name}: {k} 期望 {v!r} 实得 {fields.get(k)!r}")
    if lines[-2:] == ["备注：", "备注：第二条备注"]:
        items = fields.get("labeled_items", [])
        if len(items) != 4 or items[-2:] != [{"label": "备注", "value": ""}, {"label": "备注", "value": "第二条备注"}]:
            ok = False
            print(f"[FAIL] duplicate/empty labels should be preserved: {items!r}")
print("parser cases:", "PASS" if ok else "FAIL")

for name, lines, expect_subset in CASES:
    data = make_image(lines)
    import asyncio

    result = asyncio.run(recognize_watermark(data))
    fields = result.get("fields", {})
    print(f"\n== {name} == available={result['available']}")
    print("lines:", result["lines"])
    print("fields:", fields)
    if not result["available"]:
        ok = False
        print("[FAIL] OCR 不可用")
    for k, v in expect_subset.items():
        got = fields.get(k, "")
        if isinstance(v, str) and isinstance(got, str):
            match = bool(got) and (v in got or got in v)
        else:
            match = got == v
        if not match:
            ok = False
            print(f"[FAIL] {k}: 期望含 {v!r}，实得 {got!r}")

# 🔴 2026-11-04 用户明令：图片「单位工程」「施工区域」属施工部位（section），
# 不再当项目名/独立 work_area —— 验收表单的「验收部位」要靠它预填。
# 多个部位标签都归 section，按出现顺序取第一个命中值（不覆盖）。
extract_cases = [
    (
        ["单位工程:污水管道基础包封", "施工区域:ss-w44至ss-w-43"],
        {"section": "污水管道基础包封"},
    ),
    (
        ["施工区域:西楼A区"],
        {"section": "西楼A区"},
    ),
]
for idx, (lines, expect_subset) in enumerate(extract_cases):
    fields = parse_watermark_fields(lines)
    ok &= bool(fields.get("section"))
    ok &= "work_area" not in fields
    ok &= bool(fields.get("project_name")) is False
    for k, v in expect_subset.items():
        got = fields.get(k, "")
        ok &= (v in got) if isinstance(got, str) else False
    print(f"\n== extract_case{idx} == section={fields.get('section')} project_name={fields.get('project_name')} work_area={fields.get('work_area')}")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
