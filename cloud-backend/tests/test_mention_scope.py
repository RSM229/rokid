"""@ 引用作用域判定的单元测试。

`_mention_scope` 把 mentions 归纳成 rag 检索用的路径白名单。这三种情况必须严格区分——
判断错任何一格，用户都会遇到「选了东西反而搜不到」或「没选却偷偷搜全库」：

    只选模糊词           → None（不限定范围，关键词只进检索查询）
    选了文件夹/文档且命中 → 命中路径集合
    选了文件夹但是空的    → 空集合（刻意不外扩，让模型追问而不是拿全库冒充依据）
"""
import unittest

from app.routers.agent import _mention_scope


class MentionScopeTests(unittest.TestCase):
    def test_query_only_does_not_restrict_scope(self) -> None:
        self.assertIsNone(_mention_scope([{"type": "kb_query", "query": "钢筋 验收"}], set()))

    def test_folder_mention_uses_resolved_paths(self) -> None:
        scope = _mention_scope(
            [{"type": "kb_folder", "kb_id": 3, "folder_id": 11}],
            {"user_kb/3/a.docx", "user_kb/3/b.docx"},
        )
        self.assertEqual(scope, {"user_kb/3/a.docx", "user_kb/3/b.docx"})

    def test_empty_folder_keeps_empty_scope_instead_of_widening(self) -> None:
        scope = _mention_scope([{"type": "kb_folder", "kb_id": 3, "folder_id": 99}], set())
        # 关键区别：是「空集合」而不是 None —— 不能让检索退回全库
        self.assertIsNotNone(scope)
        self.assertEqual(scope, set())

    def test_doc_mention_counts_as_scope_item(self) -> None:
        scope = _mention_scope([{"type": "kb_doc", "kb_id": 3, "doc_id": 88}], {"user_kb/3/x.docx"})
        self.assertEqual(scope, {"user_kb/3/x.docx"})

    def test_query_mixed_with_folder_still_restricts_scope(self) -> None:
        scope = _mention_scope(
            [
                {"type": "kb_query", "query": "旁站"},
                {"type": "kb_folder", "kb_id": 3, "folder_id": 11},
            ],
            {"user_kb/3/a.docx"},
        )
        self.assertEqual(scope, {"user_kb/3/a.docx"})

    def test_malformed_mentions_are_ignored(self) -> None:
        self.assertIsNone(_mention_scope([{"type": ""}, "not-a-dict"], set()))  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
