"""验证：user_kb 孤儿文件不再被 RAG 索引收进默认库。

生产实测（2026-11-25）：知识库目录 rglob 无差别收录所有可解析文件，
user_kb 下三类「未登记」文件——
  1. 归档格式切换残留（document_213_r1.docx 旧版，登记行已指向 r2）；
  2. 软删除（is_deleted=1，登记行被查询过滤）；
  3. 事务失败遗留的孤儿文件
——查不到 registered 记录，全部兜底挂到默认库（kb=3，内置规范库）。
结果：用户业务文书混进公共规范库（18 个文件 / 124 个分块挂错库），
全局知识检索（source_types=[knowledge_doc] 不带 kb_id）可直接召回
用户文书，这是「规范检索返回整改通知单」类污染的第一层根因。

修法：user_kb/ 下未登记的文件不进索引（顶层内置目录维持默认库归属）。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.services import rag_index


class _Scalars:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._value

    def first(self):
        return self._value


class _FakeSession:
    """按查询类型返回假登记数据：文档行 / KG 实体 / 默认库 id。"""

    def __init__(self, docs, default_kb_id, entities=()):
        self._docs = docs
        self._default_kb_id = default_kb_id
        self._entities = entities

    def execute(self, query):
        # 用查询字符串内容区分三种 select（简单桩，够用）
        text = str(query)
        if "kg_entities" in text:
            return _Scalars(list(self._entities))
        if "knowledge_documents" in text:
            return _Scalars(list(self._docs))
        return _Scalars(self._default_kb_id)


def _make_doc(path, kb_id=12, name="测试文书"):
    return SimpleNamespace(path=path, kb_id=kb_id, name=name)


class UserKbOrphanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        # 场景文件
        # user_kb/12/doc.pdf：已登记（kb=12）→ 应收录
        (self.root / "user_kb" / "12").mkdir(parents=True)
        (self.root / "user_kb" / "12" / "doc.pdf").write_text("registered")
        # user_kb/12/orphan.docx：盘上有、登记表无（软删/旧版残留）→ 不应收录
        (self.root / "user_kb" / "12" / "orphan.docx").write_text("orphan")
        # 省统表word版/14旁站记录.docx：顶层内置目录，无登记 → 仍应收录（挂默认库）
        (self.root / "省统表word版").mkdir()
        (self.root / "省统表word版" / "14旁站记录.docx").write_text("builtin")

        # 需要让 SUPPORTED_EXTS 认这些后缀（拿模块常量，防测试与生产扩展集不一致）
        exts = rag_index.SUPPORTED_EXTS
        self.assertIn(".pdf", exts)
        self.assertIn(".docx", exts)

    def _collect(self, docs):
        session = _FakeSession(docs, default_kb_id=3)
        with unittest.mock.patch.object(rag_index.settings, "knowledge_base_path", str(self.root)):
            return rag_index._collect_knowledge_files(session)

    def test_registered_user_kb_file_is_indexed_with_own_kb(self):
        reg = str(self.root / "user_kb" / "12" / "doc.pdf")
        sources = self._collect([_make_doc(reg, kb_id=12)])
        by_sid = {s["source_id"]: s for s in sources}
        self.assertIn("user_kb/12/doc.pdf", by_sid)
        self.assertEqual(by_sid["user_kb/12/doc.pdf"]["kb_id"], 12)

    def test_orphan_user_kb_file_is_skipped(self):
        reg = str(self.root / "user_kb" / "12" / "doc.pdf")
        sources = self._collect([_make_doc(reg, kb_id=12)])
        sids = {s["source_id"] for s in sources}
        self.assertNotIn("user_kb/12/orphan.docx", sids)

    def test_builtin_top_level_file_keeps_default_kb(self):
        sources = self._collect([])
        by_sid = {s["source_id"]: s for s in sources}
        self.assertIn("省统表word版/14旁站记录.docx", by_sid)
        self.assertEqual(by_sid["省统表word版/14旁站记录.docx"]["kb_id"], 3)

    def test_soft_deleted_doc_row_absent_means_orphan(self):
        # 软删文档不会出现在查询结果里（is_deleted 过滤），与孤儿同路：不索引
        sources = self._collect([])  # 登记表全空
        sids = {s["source_id"] for s in sources}
        self.assertNotIn("user_kb/12/doc.pdf", sids)
        self.assertIn("省统表word版/14旁站记录.docx", sids)


import unittest.mock  # noqa: E402 — 放在类定义后避免循环里找不到

if __name__ == "__main__":
    unittest.main(verbosity=2)
