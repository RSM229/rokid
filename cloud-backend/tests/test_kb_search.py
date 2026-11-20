"""资料库混排搜索与 @ 引用解析的单元测试。

覆盖三件事：
1. 打分档位可解释（精确 > 前缀 > 包含 > 分词命中 > 路径命中），
   这是前端排序和用户预期一致的前提；
2. 文件夹子树计数沿 parent 链上卷正确（@ 面板显示的「N 份资料」）；
3. mentions → 检索白名单的解析：文件夹展开整棵子树、单文件精确到路径、
   越权 kb_id 必须被忽略。
"""
import unittest
from unittest.mock import patch

from app.services import kb_search


class _Rows:
    """模拟 SQLAlchemy Result：同时支持 .all() 与 .scalars().all()。"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _QueuedSession:
    """按调用顺序返回预置结果，用于替身化数据库。"""

    def __init__(self, *results):
        self._queue = list(results)

    async def execute(self, _query):
        if not self._queue:
            raise AssertionError("execute 调用次数超出预期")
        return _Rows(self._queue.pop(0))


class ScorerTests(unittest.TestCase):
    def test_tokens_keep_whole_query_first(self) -> None:
        # 整串必须排在第一位：它决定「包含」档，拆开会把精确命中降档
        tokens = kb_search.tokenize("钢筋验收")
        self.assertEqual(tokens[0], "钢筋验收")
        self.assertIn("钢筋", tokens)

    def test_score_prefers_exact_then_prefix_then_contains(self) -> None:
        tokens = kb_search.tokenize("钢筋验收")
        exact = kb_search.score_text("钢筋验收", "钢筋验收", tokens)
        prefix = kb_search.score_text("钢筋验收记录表", "钢筋验收记录表", tokens)
        contains = kb_search.score_text("本工程钢筋验收单", "本工程钢筋验收单", tokens)
        self.assertGreater(exact, prefix)
        self.assertGreater(prefix, contains)
        self.assertGreater(contains, 0)

    def test_score_falls_back_to_path_and_subsequence(self) -> None:
        tokens = kb_search.tokenize("钢筋")
        # 名字里没有，但路径里有 → 路径命中档
        path_hit = kb_search.score_text("混凝土浇筑记录", "施工规范/钢筋工程/混凝土浇筑记录", tokens)
        self.assertEqual(path_hit, kb_search.S_PATH_CONTAINS)

        seq_tokens = kb_search.tokenize("钢筋验收")
        subseq = kb_search.score_text("钢_筋_绑扎_验收_记录", "钢_筋_绑扎_验收_记录", seq_tokens)
        self.assertGreater(subseq, 0)

    def test_unrelated_text_scores_zero(self) -> None:
        tokens = kb_search.tokenize("脚手架")
        self.assertEqual(kb_search.score_text("混凝土浇筑记录", "x/混凝土浇筑记录", tokens), 0)

    def test_empty_query_has_no_tokens(self) -> None:
        self.assertEqual(kb_search.tokenize("   "), [])


class SubtreeCountTests(unittest.TestCase):
    def test_counts_roll_up_parent_chain(self) -> None:
        nodes = {
            1: {"id": 1, "parent_id": None},
            2: {"id": 2, "parent_id": 1},
            3: {"id": 3, "parent_id": 2},
        }
        direct = {3: 4, 1: 1}
        totals = kb_search._subtree_counts(nodes, direct)
        self.assertEqual(totals[3], 4)
        self.assertEqual(totals[2], 4)
        self.assertEqual(totals[1], 5)


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_folders_rank_before_documents_and_carry_subtree_count(self) -> None:
        nodes = {
            10: {"id": 10, "kb_id": 3, "parent_id": None, "name": "施工规范", "path": "施工规范"},
            11: {"id": 11, "kb_id": 3, "parent_id": 10, "name": "钢筋工程", "path": "施工规范/钢筋工程"},
        }
        folder_rows = [(11, 3, "钢筋工程")]
        doc_rows = [
            (88, 3, "钢筋绑扎验收记录.docx", 11, 2048, None),
            (89, 3, "混凝土浇筑记录.docx", 11, 1024, None),
        ]
        session = _QueuedSession(folder_rows, doc_rows)
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]), \
             patch.object(kb_search, "_folder_path_map", return_value=nodes), \
             patch.object(kb_search, "_folder_doc_counts", return_value={11: 2}):
            result = await kb_search.search(session, 1, "钢筋")

        self.assertEqual([f["path"] for f in result["folders"]], ["施工规范/钢筋工程"])
        self.assertEqual(result["folders"][0]["count"], 2)
        # 文档按分数降序：名字命中（90）在前，仅路径命中（70）在后。
        # 路径命中保留是刻意的——用户打「钢筋」时，钢筋工程文件夹下的资料
        # 本就该出现；但不能压过名字直接命中的结果。
        names = [d["name"] for d in result["documents"]]
        self.assertEqual(names, ["钢筋绑扎验收记录.docx", "混凝土浇筑记录.docx"])
        scores = [d["score"] for d in result["documents"]]
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(result["documents"][0]["folder_path"], "施工规范/钢筋工程")
        self.assertFalse(result["has_more"])

    async def test_document_outside_matched_path_is_not_returned(self) -> None:
        nodes = {
            10: {"id": 10, "kb_id": 3, "parent_id": None, "name": "施工规范", "path": "施工规范"},
            11: {"id": 11, "kb_id": 3, "parent_id": 10, "name": "钢筋工程", "path": "施工规范/钢筋工程"},
            12: {"id": 12, "kb_id": 3, "parent_id": 10, "name": "混凝土工程", "path": "施工规范/混凝土工程"},
        }
        folder_rows = []
        doc_rows = [
            (90, 3, "混凝土浇筑记录.docx", 12, 1024, None),
        ]
        session = _QueuedSession(folder_rows, doc_rows)
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]), \
             patch.object(kb_search, "_folder_path_map", return_value=nodes), \
             patch.object(kb_search, "_folder_doc_counts", return_value={12: 1}):
            result = await kb_search.search(session, 1, "钢筋")
        self.assertEqual(result["documents"], [])

    async def test_empty_query_skips_database(self) -> None:
        session = _QueuedSession()
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]):
            result = await kb_search.search(session, 1, "   ")
        self.assertEqual(result["folders"], [])
        self.assertEqual(result["documents"], [])

    async def test_unauthorized_kb_is_filtered_out(self) -> None:
        session = _QueuedSession()
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]):
            result = await kb_search.search(session, 1, "钢筋", kb_ids=[999])
        self.assertEqual(result["kb_ids"], [])
        self.assertEqual(result["documents"], [])


class ResolveMentionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_folder_expands_whole_subtree(self) -> None:
        folder_rows = [(10, None), (11, 10), (12, 11), (99, None)]
        doc_paths = [("D:/kb/user_kb/3/a.docx",), ("D:/kb/user_kb/3/b.docx",)]
        session = _QueuedSession(folder_rows, doc_paths)
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]), \
             patch.object(kb_search, "rel_path", side_effect=lambda p: kb_search.norm_path(p).split("user_kb/")[-1]):
            paths, queries = await kb_search.resolve_mentions(
                session, 1, [{"type": "kb_folder", "kb_id": 3, "folder_id": 10}]
            )
        self.assertEqual(paths, {"3/a.docx", "3/b.docx"})
        self.assertEqual(queries, [])

    async def test_single_document_and_query_mention(self) -> None:
        doc_rows = [("D:/kb/user_kb/3/a.docx", 3)]
        session = _QueuedSession(doc_rows)
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]), \
             patch.object(kb_search, "rel_path", side_effect=lambda p: kb_search.norm_path(p).split("user_kb/")[-1]):
            paths, queries = await kb_search.resolve_mentions(
                session, 1,
                [
                    {"type": "kb_doc", "kb_id": 3, "doc_id": 88},
                    {"type": "kb_query", "query": "钢筋 验收"},
                ],
            )
        self.assertEqual(paths, {"3/a.docx"})
        self.assertEqual(queries, ["钢筋 验收"])

    async def test_foreign_kb_mention_is_ignored(self) -> None:
        session = _QueuedSession()
        with patch.object(kb_search, "accessible_kb_ids", return_value=[3]):
            paths, queries = await kb_search.resolve_mentions(
                session, 1, [{"type": "kb_folder", "kb_id": 999, "folder_id": 1}]
            )
        self.assertEqual(paths, set())
        self.assertEqual(queries, [])


if __name__ == "__main__":
    unittest.main()
