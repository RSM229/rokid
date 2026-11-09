"""归档文档 + 项目口径 + 文档 API 预览自测（不启服务、不连数据库）。

覆盖：
- 两份文档正文（Markdown 章节、PDF 真实生成）；
- 项目级旁站排除口径（规则引擎 / 持久化 JSON / 请求级覆盖）；
- preview 与 commit(dry_run) 返回文档预览，口径改后版本号联动。

运行：python tests/test_image_archive_documents.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers import image_archive as r
from app.services import image_archive_document as d
from app.services import image_archive_service as io
from app.services.doc_generator import generate_pdf
from app.services.image_classification import (
    ImageSignal, archive_batch, classify_image, signal_from_watermark,
)

ok = True


def check(condition, label, detail=""):
    global ok
    if not condition:
        ok = False
        print(f"[FAIL] {label} {detail}")
    else:
        print(f"[ OK ] {label}")


def make_batch():
    sigs = [
        signal_from_watermark({
            "project_name": "沙盛四路等四条道路工程",
            "section": "w27 井", "content": "上下安全通道搭设",
            "taken_at": "2026-11-03T09:10:00", "inspector": "李四",
            "related_doc_no": "巡视记录-1103-01",
        }, unit="沙盛四路"),
        signal_from_watermark({
            "project_name": "沙盛四路等四条道路工程",
            "section": "dxy10-11 箱涵", "content": "沟槽开挖",
            "taken_at": "2026-11-03T09:20:00", "inspector": "李四",
        }, unit="大兴路"),
    ]
    return archive_batch(sigs, overrides=[{
        "序号": 2, "category_code": "X", "subcategory": "X2",
        "related_doc_no": "巡视记录-1103-02",
        "note": "沟槽开挖是危大工程巡视，不用旁站",
    }])


def test_work_record():
    batch = make_batch()
    rules = {
        "revision": 1, "pangzhan_excluded": ["开挖", "深基坑开挖", "沟槽开挖"],
        "note": "本项目旁站清单不含沟槽/深基坑开挖", "updated_at": "2026-11-03T10:00:00",
        "history": [{"revision": 1, "at": "2026-11-03T10:00:00", "actor": "李四",
                     "note": "确认开挖不旁站", "pangzhan_excluded": ["开挖"],
                     "override_count": 1}],
    }
    spec = d.build_work_record(
        batch, project_id=7, project_name="沙盛四路等四条道路工程",
        business_date="2026-11-03", actor_name="李四", revision=2,
        ledger_path="/uploads/现场影像/影像台账_20261103.csv", rules=rules,
        overrides=[{"序号": 2, "category_code": "X", "subcategory": "X2",
                    "related_doc_no": "巡视记录-1103-02", "note": "沟槽开挖是危大工程巡视，不用旁站"}],
    )
    md = spec["markdown"]
    print("  work record:", spec["title"], spec["doc_number"], len(md), "chars")
    check(spec["title"] == "工程影像归档工作记录_20261103", "工作记录标题按天")
    check(spec["doc_number"] == "YX-7-20261103", "编号不含版本，改口径不另起编号")
    for heading in ("## 一、归档结果", "## 二、分类统计", "## 三、影像台账",
                    "## 四、资料缺口", "## 五、待确认", "## 六、人工口径修正",
                    "## 七、项目分类标准口径"):
        check(heading in md, f"章节存在 {heading}")
    check("| 09:10 | 沙盛四路 | w27 井 |" in md, "归档结果表含首行")
    check("影像台账_20261103.csv" in md, "台账路径写入文档")
    check("沟槽开挖是危大工程巡视，不用旁站" in md, "人工口径修正记录")
    check("本项目旁站清单不含沟槽/深基坑开挖" in md, "项目口径章节")
    check("第 2 版" in md and spec["content"]["summary"].endswith("第 2 版。"),
          "正文版本号与 revision 一致")
    check("待补" in md or "暂无对应书面资料" in md or "日期" in md,
          "缺口/台账说明可读")
    return spec


def test_standard_document():
    rules = {
        "revision": 2, "pangzhan_excluded": ["钢板桩"],
        "note": "旁站清单第 10 项删除「深基坑开挖」，保留钢板桩支护打拔",
        "updated_at": "2026-11-03T10:05:00",
        "history": [{"revision": 2, "at": "2026-11-03T10:05:00", "actor": "李四",
                     "note": "确认开挖不旁站", "pangzhan_excluded": ["钢板桩"],
                     "override_count": 1}],
    }
    standard = r._standard(rules)
    spec = d.build_standard_document(
        standard, rules, project_id=7, project_name="沙盛四路等四条道路工程",
        business_date="2026-11-03", revision=2,
    )
    md = spec["markdown"]
    print("  standard doc:", spec["title"], len(md), "chars")
    check(spec["doc_number"] == "YX-BZ-7", "分类标准编号按项目固定")
    check(md.count("| 1 |") >= 1 and "| 13 |" in md, "旁站清单 13 项齐全")
    check("本项目不适用（排除项）" in md, "命中排除项的清单条目标注不适用")
    check("保留「钢板桩支护打拔」" in md, "保留标准原条目并说明项目口径")
    check("修订历史" in md and "确认开挖不旁站" in md, "修订历史写入")
    check("冲突优先级" in md and "影像台账字段" in md, "判定规则与台账字段")
    return spec, standard, rules


def test_real_pdf(specs):
    """用 doc_generator 真正生成 PDF，验证 content 结构不会让 reportlab 抛异常。"""
    with tempfile.TemporaryDirectory() as tmp:
        for idx, spec in enumerate(specs, start=1):
            out = Path(tmp) / f"doc{idx}.pdf"
            doc = SimpleNamespace(doc_type=spec["doc_type"],
                                  content=spec["content"], attached_images=[])
            try:
                generate_pdf(doc, str(out))
                size = out.stat().st_size if out.exists() else 0
                check(size > 1500, f"PDF{idx} 实际生成（{size} bytes）", size)
            except Exception as exc:
                check(False, f"PDF{idx} 生成", f"{type(exc).__name__}: {exc}")


def test_exclusion_rules():
    sig = ImageSignal(content="沟槽开挖回填压实", in_progress=True, is_hazard=True,
                      related_doc_no="巡视记录-1103-02")
    default = classify_image(sig)
    no_exclusion = classify_image(sig, excluded_keywords=[])
    print("  excluded default:", default.category_code, default.subcategory,
          "| no exclusion:", no_exclusion.category_code)
    check(default.category_code == "X" and default.subcategory == "X2",
          "默认项目口径：开挖归 X2 危大巡视")
    check(no_exclusion.category_code == "P",
          "清空排除项后同一工序命中旁站清单归 P")
    batch = archive_batch([sig], excluded_keywords=["钢板桩"])
    check(batch["project_rules"]["pangzhan_excluded"] == ["钢板桩"],
          "批次回传生效的排除口径")
    check(classify_image(sig, excluded_keywords=["钢板桩"]).category_code == "P",
          "自定义排除项不误伤回填工序")


def test_rules_io():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rules = io.load_project_rules(root)
        check(rules["revision"] == 0 and rules["pangzhan_excluded"] == [],
              "缺失口径文件回退默认值")
        merged = io.merge_project_rules(rules, exclusions=["开挖", "开挖", "深基坑开挖"],
                                        note="确认开挖不旁站", actor="李四",
                                        override_count=2)
        check(merged["revision"] == 1 and merged["pangzhan_excluded"] == ["开挖", "深基坑开挖"],
              "合并去重并递增版本号", merged["pangzhan_excluded"])
        check(merged["history"][0]["override_count"] == 2, "修订历史记录修正张数")
        saved = io.save_project_rules(root, merged)
        loaded = io.load_project_rules(root)
        check(loaded["revision"] == 1 and loaded["note"] == "确认开挖不旁站",
              "口径文件写后可读")
        check(saved["public_path"].endswith("/现场影像/分类标准_项目口径.json"),
              "口径文件公共路径", saved["public_path"])
        merged2 = io.merge_project_rules(loaded, exclusions=[], note="", actor="王五")
        io.save_project_rules(root, merged2)
        check(io.load_project_rules(root)["revision"] == 2, "再次确认出第 2 版")
        check(saved["backup"] is None, "首次写入无需备份")
        backups = list((root / io.ARCHIVE_ROOT).glob(f"{io.RULES_FILENAME}.bak_*"))
        check(len(backups) == 1, "覆盖前留 .bak 备份", len(backups))
        path = io.rules_path(root)
        path.write_text("{broken json", encoding="utf-8")
        check(io.load_project_rules(root)["revision"] == 0, "文件损坏时安全回退")


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
        id=mid, project_id=1, media_type="image",
        processed_path=f"/uploads/media/image/2026/11/03/camera_{mid}.jpg",
        original_path=f"/uploads/media/image/2026/11/03/camera_{mid}_original.jpg",
        file_hash=f"hash{mid}", section="w27 井", location="沙盛四路",
        metadata_json={
            "watermark": {"project_name": "沙盛四路等四条道路工程",
                          "section": "沙盛四路", "content": "上下安全通道搭设",
                          "taken_at": "2026-11-03T09:10:00", "inspector": "李四",
                          "labeled_items": [{"label": "单位工程", "value": "沙盛四路"},
                                            {"label": "部位", "value": "w27 井"}]},
            "capture_time": "2026-11-03T09:10:00",
        },
    )


USER = SimpleNamespace(real_name="李四", id=7)


class _FirstScalars(_Scalars):
    def __init__(self, first):
        super().__init__([])
        self._first = first

    def first(self):
        return self._first


class _FirstResult(_Result):
    def __init__(self, first):
        super().__init__([])
        self._first = first

    def scalars(self):
        return _FirstScalars(self._first)


class _DocDB:
    """只模拟 upsert_documents 用到的 session 能力，不执行任何 SQL。"""

    def __init__(self, existing):
        self.existing = existing
        self.added = []

    async def execute(self, stmt):
        return _FirstResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = 99

    async def commit(self):
        return None


async def test_upsert_revision():
    """改口径后复用同一 Document 出 R2（截图「四处联动」的核心机制）。"""
    from app.services import image_archive_sync as docsync

    batch = make_batch()
    rules = {"revision": 1, "pangzhan_excluded": ["开挖"],
             "note": "确认开挖不旁站", "updated_at": "2026-11-03T10:00:00",
             "history": []}
    specs = r._document_specs(
        r.ArchiveRequest(items=[r.ArchiveItem(media_id=1)]), batch,
        project_id=7, project_name="测试工程", rules=rules, actor_name="李四",
    )
    with tempfile.TemporaryDirectory() as tmp:
        original_settings = docsync.settings
        docsync.settings = SimpleNamespace(export_dir=Path(tmp))
        try:
            first = await docsync.upsert_documents(_DocDB(None), specs,
                                                   project_id=7, user=USER)
            check(len(first) == 2 and all(x["status"] == "generated" for x in first),
                  "首次生成两份可归档 PDF")
            docs = [x["document"] for x in first]
            check(all(x.content_revision == 1 for x in docs), "首次版本 R1")
            check("第 1 版" in docs[0].content["summary"], "render 注入真实版本号")
            check(all("_r1.pdf" in x["pdf_url"] for x in first), "R1 PDF 文件名")

            second = await docsync.upsert_documents(_DocDB(docs[0]), [specs[0]],
                                                    project_id=7, user=USER)
            item = second[0]
            check(item["revision"] == 2 and item["document"] is docs[0],
                  "改口径复用同一文档出 R2")
            check("第 2 版" in item["document"].content["summary"], "R2 正文联动更新")
            check("_r2.pdf" in item["pdf_url"], "R2 PDF 文件名")
        finally:
            docsync.settings = original_settings


async def test_api_preview():
    db = _FakeDB([make_media()])
    original = r.require_project_id
    original_loader = r.io.load_project_rules
    r.require_project_id = lambda user: 1
    r.io.load_project_rules = lambda _root: {
        "revision": 1, "pangzhan_excluded": ["开挖"], "note": "确认开挖不旁站",
        "updated_at": "2026-11-03T10:00:00", "history": [],
    }
    try:
        req = r.ArchiveRequest(items=[r.ArchiveItem(media_id=1)])
        resp = await r.preview(req, user=USER, db=db)
        docs = resp["documents"]
        check(resp["written"] is False and len(docs) == 2, "preview 返回两份文档预览")
        check([x["doc_type"] for x in docs] == [d.WORK_RECORD_DOC_TYPE, d.STANDARD_DOC_TYPE],
              "预览文档类型正确")
        check(all(x["next_revision"] >= 1 and x["markdown"] for x in docs),
              "预览含全文与下一版本号")
        check("确认开挖不旁站" in docs[0]["markdown"], "预览正文带项目口径")

        dry = await r.commit(req, user=USER, db=db)
        check(dry["written"] is False and len(dry["documents"]) == 2,
              "dry_run 不落盘但给出文档计划")
        check(all(x["status"] == "planned" for x in dry["documents"]),
              "文档计划状态为 planned")

        custom = r.ArchiveRequest(items=[r.ArchiveItem(media_id=1)],
                                  pangzhan_exclusions=["钢板桩"])
        batch, _ = await r._build_batch(custom, db, 1, USER)
        check(batch["project_rules"]["pangzhan_excluded"] == ["钢板桩"],
              "请求级排除口径覆盖项目已存口径")

        no_docs = r.ArchiveRequest(items=[r.ArchiveItem(media_id=1)],
                                   generate_documents=False)
        resp2 = await r.preview(no_docs, user=USER, db=db)
        check(resp2["documents"] == [], "可关闭文档生成")
    finally:
        r.require_project_id = original
        r.io.load_project_rules = original_loader


def main():
    wr = test_work_record()
    std, standard, rules = test_standard_document()
    test_real_pdf([wr, std])
    test_exclusion_rules()
    test_rules_io()
    asyncio.run(test_upsert_revision())
    asyncio.run(test_api_preview())


main()
print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
sys.exit(0 if ok else 1)
