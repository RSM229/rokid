"""工程影像归档 IO 自测：标准目录复制、不覆盖、路径防穿越、台账 CSV 合并去重。

纯 IO、不依赖 FastAPI / DB，用临时目录跑。运行：
    python tests/test_image_archive_service.py
"""
import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import image_archive_service as io
from app.services.image_classification import LEDGER_FIELDS

ok = True


def check(condition, label, detail=""):
    global ok
    if not condition:
        ok = False
        print(f"[FAIL] {label} {detail}")
    else:
        print(f"[ OK ] {label}")


def ledger_row(path, code="P", sub="", date="2026-11-03", time="09:15"):
    return {
        "序号": 1, "拍摄日期": date, "时间": time, "类别码": code, "子类": sub,
        "单位工程": "沙盛四路", "部位/桩号": "k300~k500", "工序/对象": "过路支管回填",
        "拍摄人": "李四", "关联资料编号": "旁站记录-1103-01", "存储路径": path,
        "标签": "", "备注": "旁站清单第 1 项",
    }


with tempfile.TemporaryDirectory() as tmp:
    upload = Path(tmp)
    src_dir = upload / "media" / "image" / "2026" / "11" / "03"
    src_dir.mkdir(parents=True)
    src = src_dir / "camera_001.jpg"
    src.write_bytes(b"fake-jpeg-bytes-001")
    public_src = f"/uploads/{src.relative_to(upload).as_posix()}"

    row = ledger_row("20261103_P01_沙盛四路_k300~k500_过路支管回填_01.jpg")
    r1 = io.archive_one(upload, public_src, row, "01_旁站")
    check(r1["status"] == "copied", "首次归档复制", r1)
    check("/uploads/现场影像/01_旁站/2026-11/" in r1["public_path"], "标准目录结构", r1["public_path"])
    check(src.exists(), "原图仍保留（复制而非移动）")
    check(Path(r1["dest"]).read_bytes() == b"fake-jpeg-bytes-001", "副本内容一致")

    r2 = io.archive_one(upload, public_src, row, "01_旁站")
    check(r2["status"] == "reused", "同名同内容复用不重复写", r2["status"])

    src.write_bytes(b"fake-jpeg-bytes-002-CHANGED")
    r3 = io.archive_one(upload, public_src, row, "01_旁站")
    check(r3["status"] == "copied" and "_v2" in r3["dest"], "同名不同内容加 _v2 不覆盖", r3["dest"])
    check(Path(r1["dest"]).read_bytes() == b"fake-jpeg-bytes-001", "旧副本未被覆盖")

    xrow = ledger_row("20261103_X201_大兴路_dxy10-11_沟槽开挖_01.jpg", "X", "X2")
    rx = io.archive_one(upload, public_src, xrow, "05_巡视")
    check("05_巡视/2026-11/X2_安全专项" in rx["public_path"], "X2 二级目录", rx["public_path"])

    bad = io.archive_one(upload, "/uploads/../../etc/passwd", row, "01_旁站")
    check(bad["status"] == "missing" and "越界" in bad["reason"], "路径穿越被拦", bad)
    bad2 = io.archive_one(upload, "http://evil/a.jpg", row, "01_旁站")
    check(bad2["status"] == "missing", "非 /uploads 路径被拦", bad2)

    cleaned = io.sanitize_component("../..\\危险:名*")
    check(all(ch not in cleaned for ch in ("/", "\\", ":", "*", "..")),
          "文件名清洗（无路径分隔符与 ..）", cleaned)

    # 台账：首次写 2 行，再以同路径的新行覆盖其中一行 → 仍 2 行且内容更新
    row1 = ledger_row(r1["public_path"])
    row2 = ledger_row(rx["public_path"], "X", "X2")
    for i, r in enumerate((row1, row2), start=1):
        r["序号"] = i
    led1 = io.write_ledger_csv(upload, "20261103", [row1, row2], LEDGER_FIELDS)
    check(led1["created"] and led1["rows"] == 2, "台账首次创建", led1)
    check(Path(led1["path"]).read_bytes().startswith(b"\xef\xbb\xbf"), "UTF-8 BOM 便于 Excel")

    updated = dict(row1)
    updated["类别码"] = "X"
    updated["子类"] = "X2"
    updated["关联资料编号"] = "巡视记录-1103-02"
    updated["备注"] = "人工确认口径"
    led2 = io.write_ledger_csv(upload, "20261103", [updated], LEDGER_FIELDS)
    check(not led2["created"] and led2["rows"] == 2, "合并去重不重复行", led2)
    check(led2["backup"] and Path(led2["backup"]).exists(), "覆盖前备份旧台账")
    with open(led2["path"], "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    hit = [r for r in rows if r["存储路径"] == r1["public_path"]]
    check(len(hit) == 1 and hit[0]["类别码"] == "X" and hit[0]["子类"] == "X2",
          "改口径后台账行联动更新", hit)
    check(list(rows[0].keys()) == LEDGER_FIELDS, "台账 13 列字段顺序")

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
