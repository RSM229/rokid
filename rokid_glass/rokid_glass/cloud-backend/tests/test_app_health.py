import unittest
from unittest.mock import AsyncMock, patch

import httpx

import app.main as app_main
from app.config import settings


class AppHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_endpoint_with_application_lifespan(self) -> None:
        original_debug = settings.debug
        settings.debug = True
        try:
            # 数据库连接由集成环境验证；此处保留完整 FastAPI lifespan，
            # 仅隔离外部 MySQL，以验证中间件、路由注册与响应契约。
            with patch.object(app_main, "init_db", new=AsyncMock()):
                async with app_main.lifespan(app_main.app):
                    transport = httpx.ASGITransport(app=app_main.app)
                    async with httpx.AsyncClient(
                        transport=transport,
                        base_url="https://testserver",
                    ) as client:
                        response = await client.get("/health")
        finally:
            settings.debug = original_debug

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "service": "gdjl-cloud-backend"},
        )


if __name__ == "__main__":
    unittest.main()
