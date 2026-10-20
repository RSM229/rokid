import unittest
from unittest.mock import AsyncMock, patch

from app.models.knowledge_graph import KGEntity
from app.routers.knowledge import LLMQueryRequest, llm_query


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _ScalarResult(self._rows)


class KnowledgeScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_scope_combines_documents_and_knowledge_graph(self) -> None:
        entity = KGEntity(
            id=7,
            name="脚手架",
            entity_type="施工工序",
            description="连墙件与剪刀撑应按方案设置",
            source_doc="JGJ 130",
        )
        document = {
            "title": "脚手架检查表",
            "snippet": "检查立杆间距、连墙件和剪刀撑。",
            "category": "安全监理用表",
        }

        with (
            patch(
                "app.routers.knowledge.search_by_content",
                return_value=[document],
            ) as search,
            patch(
                "app.routers.knowledge.ai_client.generate_regulation_answer",
                new=AsyncMock(return_value="综合知识库回答"),
            ) as generate,
        ):
            response = await llm_query(
                LLMQueryRequest(
                    question="脚手架有什么规范要求",
                    knowledge_scope="all",
                ),
                user={},
                db=_FakeSession([entity]),
            )

        search.assert_called_once_with("脚手架有什么规范要求", None)
        self.assertEqual(response["answer"], "综合知识库回答")
        self.assertEqual(response["knowledge_scope"], "all")
        self.assertEqual(
            {item["source_type"] for item in response["context_results"]},
            {"document", "knowledge_graph"},
        )
        context = generate.await_args.args[1]
        self.assertIn("检查立杆间距", context)
        self.assertIn("连墙件与剪刀撑", context)

    async def test_document_category_does_not_query_graph(self) -> None:
        with (
            patch(
                "app.routers.knowledge.search_by_content",
                return_value=[],
            ) as search,
            patch(
                "app.routers.knowledge.ai_client.generate_regulation_answer",
                new=AsyncMock(return_value="分类回答"),
            ),
        ):
            response = await llm_query(
                LLMQueryRequest(
                    question="监理日志怎么填写",
                    knowledge_scope="省统表",
                ),
                user={},
                db=_FakeSession([]),
            )

        search.assert_called_once_with("监理日志怎么填写", "省统表")
        self.assertEqual(response["knowledge_scope"], "省统表")
        self.assertEqual(response["context_results"], [])


if __name__ == "__main__":
    unittest.main()
