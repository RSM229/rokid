"""归档 API 胶水自测：假 DB / 假用户直接调 _build_batch 与 commit(dry_run)。

不启服务、不连数据库、不写文件。运行：
    python tests/test_image_archive_api.py
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers import image_archive as r

ok = True


def check(condition, label, detail=""):
    global ok
    if not condition:
        ok = False
        print(f"[FAIL] {label} {detail}")
    else:
        print(f"[ OK ] {label}")


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, stmt):
        return _Result(self.rows)

    async def commit(self):
        return None


def make_media(mid=1):
    return SimpleNamespace(
        id=mid,
        project_id=1,
        media_type="image",
        processed_path=f"/uploads/media/image/2026/11/03/camera_{mid}.jpg",
        original_path=f"/uploads/media/image/2026/11/03/camera_{mid}_original.jpg",
        file_hash=f"hash{mid}",
        section="dxy10-11 箱涵",
        location="大兴路",
        metadata_json={
            "watermark": {
                "project_name": "沙盛四路等四条道路工程",
                "section": "大兴路",  # OCR 把「单位工程」折叠进 section
                "content": "沟槽开挖",
                "taken_at": "2026-11-03T09:20:00",
                "inspector": "李四",
                "labeled_items": [
                    {"label": "单位工程", "value": "大兴路"},
                    {"label": "部位", "value": "dxy10-11 箱涵"},
                ],
            },
            "capture_time": "2026-11-03T09:20:00",
        },
    )


USER = SimpleNamespace(real_name="李四", id=7)


async def main():
    global ok
    db = _FakeDB([make_media()])
    req = r.ArchiveRequest(items=[r.ArchiveItem(media_id=1)])
    batch, ctx = await r._build_batch(req, db, 1, USER)
    rec = batch["records"][0]
    print("  batch record:", rec["单位工程"], rec["部位"], rec["施工内容"], rec["归类"], rec["文件名"])
    check(len(ctx) == 1 and ctx[0]["media_id"] == 1, "定位到本项目的 MediaRecord")
    check(rec["单位工程"] == "大兴路" and rec["部位"] == "dxy10-11 箱涵",
          "OCR 桥接拆开单位工程/部位", rec)
    check("X2" in rec["归类"] and "不在本项目旁站清单" in rec["依据"],
          "沟槽开挖按项目口径归 X2")
    check(rec["文件名"].startswith("20261103_X201_"), "命名规范", rec["文件名"])
    check(batch["summary"]["needs_review"] == 1, "无关联资料编号 → 待确认")

    # 人工改口径：改为旁站并补记录编号，四处联动
    req2 = r.ArchiveRequest(
        items=[r.ArchiveItem(media_id=1)],
        overrides=[r.OverrideItem(序号=1, category_code="P",
                                  related_doc_no="旁站记录-1103-05",
                                  note="人工确认改为旁站")],
    )
    b2, _ = await r._build_batch(req2, db, 1, USER)
    r2, l2 = b2["records"][0], b2["ledger_rows"][0]
    print("  overridden:", r2["归类"], r2["文件名"], l2["关联资料编号"], r2["状态"])
    check(r2["人工确认"] is True and r2["归类"] == "P 旁站",
          "口径覆盖生效且旧二级类不残留", r2["归类"])
    check(b2["ledger_rows"][0]["子类"] == "", "台账子类同步清空")
    check(r2["文件名"].startswith("20261103_P01_"), "文件名联动重算")
    check(l2["关联资料编号"] == "旁站记录-1103-05" and r2["状态"] == "confirmed",
          "补齐编号后状态转正")
    check(b2["summary"]["by_category"] == {"P": 1}, "统计联动重算")

    # commit 默认 dry_run：只出计划，不写文件、不提交 DB
    original = r.require_project_id
    r.require_project_id = lambda user: 1
    try:
        resp = await r.commit(r.ArchiveRequest(items=[r.ArchiveItem(media_id=1)]),
                              user=USER, db=db)
    finally:
        r.require_project_id = original
    check(resp["written"] is False and len(resp["plan"]) == 1, "commit 默认 dry_run 不落盘")
    check("hint" in resp and "05_巡视" in resp["plan"][0]["目标目录"]
          and "X2_安全专项" in resp["plan"][0]["目标目录"],
          "落盘计划含标准目录与二级目录", resp["plan"][0]["目标目录"])
    check(resp["plan"][0]["源路径"].startswith("/uploads/"), "计划含源路径")

    # 缺失 media_id → 404
    try:
        await r._build_batch(r.ArchiveRequest(items=[r.ArchiveItem(media_id=999)]), db, 1, USER)
        check(False, "缺失影像应报 404")
    except Exception as exc:
        check(getattr(exc, "status_code", None) == 404, "缺失影像报 404", type(exc).__name__)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
