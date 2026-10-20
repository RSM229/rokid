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
        {"project": "海珠湾隧道工程", "person": "张三", "location": "广州市南沙区大岗镇", "section": "东湾2栋10层"},
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
    (["记录人:王五"], {"person": "王五"}),
]

ok = True
for name, expect in [(f"case{i}", c) for i, c in enumerate(parser_cases)]:
    lines, expect_subset = expect
    fields = parse_watermark_fields(lines)
    for k, v in expect_subset.items():
        if fields.get(k) != v:
            ok = False
            print(f"[FAIL] parser {name}: {k} 期望 {v!r} 实得 {fields.get(k)!r}")
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
            match = v in got or got in v
        else:
            match = got == v
        if not match:
            ok = False
            print(f"[FAIL] {k}: 期望含 {v!r}，实得 {got!r}")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
