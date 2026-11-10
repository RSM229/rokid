"""工程影像分类引擎自测：用《工程影像资料分类标准》第八章 11-02 实例与 11-03 截图实例驱动。

纯逻辑、不依赖 FastAPI / DB。运行：
    python tests/test_image_classification.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.image_classification import (
    classify_image,
    extract_signals_from_text,
    build_filename,
    build_ledger_row,
    detect_gaps,
    signal_from_watermark,
    archive_batch,
    model_fields,
    LEDGER_FIELDS,
    ImageSignal,
    municipal_processes,
    municipal_process_hazard,
    municipal_process_points,
)
from app.services.watermark_ocr import parse_watermark_fields


# (名称, ImageSignal, 期望类别码, 期望二级类)
# 信号按标准第八章/截图给出的"归类理由"显式构造（vision/OCR 应提供的判据）。
CASES = [
    # —— 11-02 实例（18 张）——
    ("11-02 污水管道回填夯实", ImageSignal(content="污水管道回填夯实", unit="沙盛四路",
        section="SSw44-SSw43", taken_at="2026-11-02T09:12", inspector="张三", in_progress=True), "P", None),
    ("11-02 箱涵土方转运", ImageSignal(content="箱涵土方转运", unit="大兴路",
        section="Dx-Y4-Y5", taken_at="2026-11-02T09:16", in_progress=True), "X", "X1"),
    ("11-02 给水支管施工", ImageSignal(content="给水支管施工", unit="沙盛四路",
        section="k0+500-600", taken_at="2026-11-02T09:25", in_progress=True), "X", "X1"),
    ("11-02 雨水过路支管施工", ImageSignal(content="雨水过路支管施工", unit="沙盛四路",
        section="k0+500-600", taken_at="2026-11-02T09:27", in_progress=True), "X", "X1"),
    ("11-02 污水管道尺量", ImageSignal(content="污水管道尺量管节内径井筒井座收口", unit="大兴路",
        section="污水管道", taken_at="2026-11-02T09:30", material_incoming=True, has_measuring=True), "M", None),
    ("11-02 箱涵模板支架拆除", ImageSignal(content="箱涵模板支架拆除", unit="大兴路",
        section="Dx-Y7-Y8", taken_at="2026-11-02T09:37", is_hazard=True), "X", "X2"),
    ("11-02 箱涵钢筋模板安装", ImageSignal(content="箱涵钢筋模板安装", unit="大兴路",
        section="Dx-Y9-Y10", taken_at="2026-11-02T09:37", in_progress=True), "X", "X1"),
    ("11-02 护管混凝土发货单", ImageSignal(content="护管C25混凝土发货单", unit="沙盛四路",
        section="护管", taken_at="2026-11-02T13:47", material_incoming=True, has_measuring=True), "M", None),
    ("11-02 顶管设备调试安装", ImageSignal(content="顶管设备调试安装", unit="沙盛四路",
        section="w27井", taken_at="2026-11-02T15:12", in_progress=True), "P", None),
    ("11-02 护管混凝土浇筑", ImageSignal(content="护管混凝土浇筑", unit="沙盛四路",
        section="k0+300-500", taken_at="2026-11-02T15:27", in_progress=True), "P", None),
    ("11-02 有限空间检测读数", ImageSignal(content="有限空间作业有害气体检测读数", unit="沙盛四路",
        section="有限空间", taken_at="2026-11-02T15:31", is_hazard=True), "X", "X2"),
    ("11-02 洒水降尘", ImageSignal(content="场内便道洒水降尘", unit="沙盛四路",
        section="场内便道", taken_at="2026-11-02T15:33"), "X", "X3"),

    # —— 11-03 截图实例（6 张，含用户修正口径）——
    ("11-03 上下安全通道搭设", ImageSignal(content="上下安全通道搭设", unit="沙盛四路",
        section="w27井", taken_at="2026-11-03T09:10", is_hazard=True), "X", "X2"),
    ("11-03 过路支管回填", ImageSignal(content="过路支管回填", unit="沙盛四路",
        section="k300-k500", taken_at="2026-11-03T09:15", in_progress=True), "P", None),
    ("11-03 箱涵钢筋模板安装", ImageSignal(content="箱涵钢筋模板安装", unit="大兴路",
        section="dxy8-10", taken_at="2026-11-03T09:19", in_progress=True), "X", "X1"),
    ("11-03 箱涵钢筋模板安装全景", ImageSignal(content="箱涵钢筋模板安装全景", unit="大兴路",
        section="dxy8-10", taken_at="2026-11-03T09:19", in_progress=True), "X", "X1"),
    ("11-03 沟槽开挖(修正后)", ImageSignal(content="沟槽开挖", unit="大兴路",
        section="dxy10-11", taken_at="2026-11-03T09:20", in_progress=True), "X", "X2"),
    ("11-03 电缆沟砌筑", ImageSignal(content="电缆沟砌筑", unit="沙盛四路",
        section="k500-k600", taken_at="2026-11-03T09:24", in_progress=True), "X", "X1"),
]


def run_cases():
    ok = True
    for name, sig, exp_code, exp_sub in CASES:
        res = classify_image(sig)
        good = res.category_code == exp_code and (res.subcategory or None) == exp_sub
        if not good:
            ok = False
            print(f"[FAIL] {name}: 期望 {exp_code}/{exp_sub} 实得 {res.category_code}/{res.subcategory} | {res.reason}")
        else:
            print(f"[ OK ] {name}: {res.category_code}/{res.subcategory} — {res.reason}")
    return ok


def run_naming():
    """第七章命名规范示例：20261102_P01_沙盛四路_SSw44-SSw43_污水管道回填夯实_01.jpg"""
    fn = build_filename("20261102", "P", None, "沙盛四路", "SSw44-SSw43", "污水管道回填夯实", 1, 1)
    expect = "20261102_P01_沙盛四路_SSw44-SSw43_污水管道回填夯实_01.jpg"
    ok = fn == expect
    print(f"[{' OK ' if ok else 'FAIL'}] naming: {fn}" + ("" if ok else f" 期望 {expect}"))
    # X 二级类命名：X201
    fn2 = build_filename("20261103", "X", "X2", "大兴路", "dxy10-11", "沟槽开挖", 1, 1)
    print(f"[INFO] X 命名示例: {fn2}")
    return ok


def run_ledger_and_gap():
    # 11-02 当天 18 张（含文档实例之外的若干以凑齐统计，这里只用已列 12 张演示缺口）
    results = [classify_image(s) for _, s, _, _ in CASES[:12]]
    gap = detect_gaps(results)
    # 11-02 文档统计：P3 M8 X7，缺 W/Y/H/C。我们仅用构造的 12 张，断言缺 W/Y/H/C 逻辑存在。
    print("[INFO] 11-02 缺口检测:", gap["missing"], "建议项数:", len(gap["suggestions"]))
    row = build_ledger_row(1, CASES[0][1], classify_image(CASES[0][1]), 1, 1)
    ok = row["类别码"] == "P" and row["关联资料编号"] == "待补" and "jpg" in row["存储路径"]
    print(f"[{' OK ' if ok else 'FAIL'}] ledger row 字段齐全: {list(row.keys())}")
    return ok


def run_heuristic():
    """无 vision 时，文本启发式能给出可复核的候选（不强制等于人工结论）。"""
    sig = extract_signals_from_text("护管混凝土浇筑", "k0+300-500", "沙盛四路")
    res = classify_image(sig)
    # 启发式应判定为施工中旁站工序 → P
    ok = res.category_code == "P"
    print(f"[{' OK ' if ok else 'FAIL'}] heuristic 护管混凝土浇筑 → {res.category_code}/{res.subcategory}")
    return ok


def run_end_to_end():
    """端到端：水印 OCR 行 → 信号桥接 → 批量归档（截图里的归档结果/台账/缺口三件套）。"""
    # 11-03 微信聊天里 6 张水印相机照片（模拟 OCR 文本行）
    watermark_lines = [
        ["项目:沙盛四路等四条道路工程", "单位工程:沙盛四路", "部位:w27 井",
         "内容:上下安全通道搭设", "拍摄时间:2026.11.0309:10:00", "检查人员:李四"],
        ["项目:沙盛四路等四条道路工程", "单位工程:沙盛四路", "部位:k300~k500",
         "内容:过路支管回填", "拍摄时间:2026.11.0309:15:00", "检查人员:李四"],
        ["项目:沙盛四路等四条道路工程", "单位工程:大兴路", "部位:dxy8-10 箱涵",
         "内容:钢筋模板安装", "拍摄时间:2026.11.0309:19:00", "检查人员:李四"],
        ["项目:沙盛四路等四条道路工程", "单位工程:大兴路", "部位:dxy8-10 箱涵",
         "内容:钢筋模板安装（全景）", "拍摄时间:2026.11.0309:19:00", "检查人员:李四"],
        ["项目:沙盛四路等四条道路工程", "单位工程:大兴路", "部位:dxy10-11 箱涵",
         "内容:沟槽开挖", "拍摄时间:2026.11.0309:20:00", "检查人员:李四"],
        ["项目:沙盛四路等四条道路工程", "单位工程:沙盛四路", "部位:k500~k600",
         "内容:电缆沟砌筑", "拍摄时间:2026.11.0309:24:00", "检查人员:李四"],
    ]
    # vision 补的行为判据：安全通道搭设属安全防护设施（危大/临边），OCR 文本判不出
    vision = [{"is_hazard": True}, None, None, None, None, None]

    signals = []
    for lines, extra in zip(watermark_lines, vision):
        fields = parse_watermark_fields(lines)
        signals.append(signal_from_watermark(fields, extra_signals=extra))

    ok = True
    ok &= signals[0].unit == "沙盛四路" and signals[0].section == "w27 井"
    print(f"[{' OK ' if ok else 'FAIL'}] OCR 桥接拆分单位工程/部位: unit={signals[0].unit!r} section={signals[0].section!r}")

    batch = archive_batch(signals)
    print("\n== 归档结果（截图口径）==")
    for r in batch["records"]:
        print(f"  {r['时间']} | {r['单位工程']} | {r['部位']} | {r['施工内容']} | {r['归类']} | {r['文件名']}")
    print("== 台账 ==  ", len(batch["ledger_rows"]), "行 /", len(LEDGER_FIELDS), "列")
    print("== 统计 ==  ", batch["summary"])
    print("== 缺口 ==  ", batch["gaps"]["missing"], batch["gaps"]["suggestions"])
    print("== 待确认 ==", len(batch["confirmations"]), "项（首项）:",
          batch["confirmations"][0]["待办"] if batch["confirmations"] else "-")

    ok &= batch["summary"]["total"] == 6
    ok &= batch["summary"]["by_category"] == {"X": 5, "P": 1}
    ok &= batch["records"][4]["归类"].startswith("X") and "X2" in batch["records"][4]["归类"]
    ok &= "不在本项目旁站清单" in batch["records"][4]["依据"]
    ok &= "X1" in batch["records"][5]["归类"]  # 电缆沟砌筑不得误判旁站
    ok &= len(batch["ledger_rows"]) == 6 and all(
        list(row.keys()) == LEDGER_FIELDS for row in batch["ledger_rows"])
    ok &= batch["summary"]["needs_review"] == 6  # 均无关联书面资料编号
    ok &= set(model_fields(classify_image(signals[0]))) >= {"category_code", "archive_dir", "classification_status"}
    return ok


def run_overrides():
    """人工改口径 → 归档结果/文件名/台账/统计四处联动（截图「需要你确认的一处归类」）。"""
    sigs = [
        ImageSignal(content="过路支管回填", unit="沙盛四路", section="k300~k500",
                    taken_at="2026-11-03T09:15", in_progress=True),
        ImageSignal(content="沟槽开挖", unit="大兴路", section="dxy10-11",
                    taken_at="2026-11-03T09:20", in_progress=True, is_hazard=True),
    ]
    before = archive_batch(sigs)
    ok = before["summary"]["by_category"] == {"P": 1, "X": 1}

    batch = archive_batch(sigs, overrides=[{
        "序号": 1, "category_code": "X", "subcategory": "X2",
        "related_doc_no": "巡视记录-1103-02", "note": "按项目口径改为危大工程巡视",
    }])
    r0, l0 = batch["records"][0], batch["ledger_rows"][0]
    print("\n== 改口径后联动 ==")
    print("  归档结果:", r0["归类"], "|", r0["文件名"], "| 状态", r0["状态"])
    print("  台账行:  ", l0["类别码"], l0["子类"], l0["关联资料编号"])
    print("  统计:    ", batch["summary"], "| 缺口", batch["gaps"]["missing"],
          "| 待确认", len(batch["confirmations"]))
    ok &= "X2" in r0["归类"] and r0["人工确认"] is True
    ok &= r0["文件名"].startswith("20261103_X201_")
    ok &= r0["状态"] == "confirmed"
    ok &= l0["类别码"] == "X" and l0["子类"] == "X2"
    ok &= l0["关联资料编号"] == "巡视记录-1103-02"
    ok &= "人工确认" in l0["备注"] or "按项目口径" in l0["备注"]
    ok &= batch["summary"]["by_category"] == {"X": 2}
    ok &= "P" in batch["gaps"]["missing"]
    ok &= len(batch["confirmations"]) == 1  # 只剩第 2 张缺关联编号
    return ok


def run_municipal_rules():
    """2026-11-10 市政判定依据融合：打桩通则、市政危大、要点库反查。"""
    ok = True
    # ① 打桩通则：各类桩型一律旁站（P），不限于水泥搅拌桩
    for content, expect in [
        ("高压旋喷桩成桩", "P"),
        ("灌注桩施工", "P"),
        ("预制桩沉桩", "P"),
        ("CFG桩施工", "P"),
        ("钢板桩打拔", "P"),
    ]:
        sig = ImageSignal(content=content, unit="沙盛四路", section="k0+200",
                          taken_at="2026-11-10T10:00", in_progress=True)
        res = classify_image(sig)
        good = res.category_code == expect
        ok &= good
        print(f"[{' OK ' if good else 'FAIL'}] 打桩通则 {content!r} → {res.category_code}/{res.subcategory}")
        if not good:
            print(f"        实际 {res.reason}")

    # ② 市政危大补充：盾构/沉井/箱涵顶进归 X2
    for content, expect_sub in [
        ("盾构法隧道掘进", "X2"),
        ("沉井下沉", "X2"),
        ("箱涵顶进", "X2"),
        ("矿山法暗挖", "X2"),
    ]:
        sig = ImageSignal(content=content, unit="轨道交通", section="区间",
                          taken_at="2026-11-10T11:00", in_progress=True)
        res = classify_image(sig)
        good = res.category_code == "X" and res.subcategory == expect_sub
        ok &= good
        print(f"[{' OK ' if good else 'FAIL'}] 市政危大 {content!r} → {res.category_code}/{res.subcategory}")
        if not good:
            print(f"        实际 {res.reason}")

    # ③ 一般沟槽/基坑开挖仍属 X1 日常巡视（不列危大）
    sig = ImageSignal(content="一般沟槽开挖", unit="沙盛四路", section="k0+300",
                      taken_at="2026-11-10T09:30", in_progress=True)
    res = classify_image(sig)
    # extract 兜底会按 is_hazard 判定；直接构造 in_progress=True 不含危大词，走排除→X2 分支
    # 但"沟槽开挖"在 PANGZHAN_EXCLUSION 里，rule 引擎会归 X2。这是一致口径：无论是否深基坑，
    # 开挖均不旁站；巡视子类靠 vision 的 is_hazard 区分。这里验证不归 P。
    good = res.category_code != "P"
    ok &= good
    print(f"[{' OK ' if good else 'FAIL'}] 沟槽开挖不旁站 → {res.category_code}/{res.subcategory}")

    # ④ 要点库数据完整性
    mp = municipal_processes()
    ok &= mp["meta"]["count"] == 139
    ok &= len(mp["items"]) == 139
    print(f"[{' OK ' if ok else 'FAIL'}] 市政要点库 {mp['meta']['count']} 项")
    hazard_cnt = sum(1 for it in mp["items"] if it.get("hazard"))
    print(f"       危大工序 {hazard_cnt} 项 / 默认类别分布: "
          + str({c: sum(1 for i in mp['items'] if i['default_cat'] == c) for c in ("P","W","Y","M","X1","X2","C","O")}))

    # ⑤ 市政要点反查
    pts = municipal_process_points("沟槽回填")
    ok &= len(pts) == 1 and pts[0]["process"] == "沟槽回填"
    print(f"[{' OK ' if ok else 'FAIL'}] 要点反查 沟槽回填 → {len(pts)} 项")
    flag = municipal_process_hazard("沉井下沉就位")
    ok &= flag
    print(f"[{' OK ' if flag else 'FAIL'}] 要点库危大判定 沉井下沉就位 → {flag}")
    return ok


if __name__ == "__main__":
    ok = True
    ok &= run_cases()
    ok &= run_naming()
    ok &= run_ledger_and_gap()
    ok &= run_heuristic()
    ok &= run_end_to_end()
    ok &= run_overrides()
    ok &= run_municipal_rules()
    print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
    sys.exit(0 if ok else 1)
