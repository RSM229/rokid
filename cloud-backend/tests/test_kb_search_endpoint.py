"""GET /api/kb/search 的路由级测试：只验接线，不碰数据库。

为什么要单独测这一层：混排搜索的算法在 tests/test_kb_search.py 里已用假 session 覆盖，
这里要证明的是「路由、鉴权依赖、查询参数解析、响应透传」这套壳子是通的——
尤其是 kb_ids 的逗号分隔解析和 limit 的上下界，这两个是前端唯一依赖的契约。

用独立的 FastAPI 实例挂载 router，而不是启动 app.main：后者的启动钩子会连数据库，
在没有隧道/凭据的机器上根本起不来，那样的测试等于测环境。
注意挂载前缀必须与 app/main.py 一致（/api/kb）——router 里有空路径的路由，
不带 prefix 挂载会直接抛 FastAPIError。
"""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import knowledge_base

PREFIX = "/api/kb"
SEARCH_URL = f"{PREFIX}/search"


class _User:
    def __init__(self, user_id: int = 1):
        self.id = user_id
        self.real_name = "测试员"


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_base.router, prefix=PREFIX)
    app.dependency_overrides[knowledge_base.get_current_user] = lambda: _User()
    app.dependency_overrides[knowledge_base.get_db] = lambda: object()
    return TestClient(app)


class KbSearchEndpointTests(unittest.TestCase):
    def test_query_params_are_parsed_and_forwarded(self) -> None:
        captured: dict = {}

        async def fake_search(db, user_id, q, **kwargs):
            captured.update({"user_id": user_id, "q": q, **kwargs})
            return {"query": q, "kb_ids": kwargs.get("kb_ids") or [],
                    "folders": [], "documents": [], "has_more": False,
                    "total_folders": 0, "total_documents": 0}

        with patch.object(knowledge_base.kb_search, "search", side_effect=fake_search):
            response = _build_client().get(
                SEARCH_URL, params={"q": "钢筋", "kb_ids": "3,4", "limit": 10},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(captured["q"], "钢筋")
        self.assertEqual(captured["kb_ids"], [3, 4])
        self.assertEqual(captured["limit"], 10)
        self.assertEqual(captured["offset"], 0)
        self.assertEqual(captured["user_id"], 1)

    def test_kb_ids_is_optional_and_blank_values_are_ignored(self) -> None:
        captured: dict = {}

        async def fake_search(db, user_id, q, **kwargs):
            captured.update(kwargs)
            return {"query": q, "kb_ids": [], "folders": [], "documents": [],
                    "has_more": False, "total_folders": 0, "total_documents": 0}

        with patch.object(knowledge_base.kb_search, "search", side_effect=fake_search):
            # 全部非法片段 → 解析结果为空 → 退化为「搜索全部可访问库」
            response = _build_client().get(SEARCH_URL, params={"q": "x", "kb_ids": " , a, "})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(captured["kb_ids"])

    def test_limit_bounds_are_enforced(self) -> None:
        async def fake_search(db, user_id, q, **kwargs):  # pragma: no cover - 不应被调用
            raise AssertionError("参数非法时不应进入服务层")

        with patch.object(knowledge_base.kb_search, "search", side_effect=fake_search):
            client = _build_client()
            self.assertEqual(client.get(SEARCH_URL, params={"q": "x", "limit": 0}).status_code, 422)
            self.assertEqual(client.get(SEARCH_URL, params={"q": "x", "limit": 61}).status_code, 422)
            self.assertEqual(client.get(SEARCH_URL, params={"q": "x", "offset": -1}).status_code, 422)

    def test_payload_is_passed_through_unchanged(self) -> None:
        payload = {
            "query": "钢筋", "kb_ids": [3],
            "folders": [{"id": 11, "kb_id": 3, "name": "钢筋工程",
                         "path": "施工规范/钢筋工程", "count": 7, "score": 95}],
            "documents": [{"id": 88, "kb_id": 3, "name": "钢筋绑扎验收记录.docx",
                           "folder_id": 11, "folder_path": "施工规范/钢筋工程",
                           "size_bytes": 2048, "created_at": None, "score": 90}],
            "has_more": True, "total_folders": 1, "total_documents": 1,
        }

        async def fake_search(db, user_id, q, **kwargs):
            return payload

        with patch.object(knowledge_base.kb_search, "search", side_effect=fake_search):
            response = _build_client().get(SEARCH_URL, params={"q": "钢筋"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), payload)


if __name__ == "__main__":
    unittest.main()
