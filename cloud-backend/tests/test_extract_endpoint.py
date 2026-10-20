# -*- coding: utf-8 -*-
"""测试 POST /work-session/extract - 小程序语音提取预览接口（仅提取不建记录）。

导入放在方法内部，避免 test_form_doc_flow.py 在模块级 del sys.modules
重新加载 app.* 时导致测试文件中旧引用失效。
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeUser:
    id = 1
    username = "testuser"
    real_name = "张三"
    project_id = 1
    is_active = True


class ExtractEndpointTests(unittest.IsolatedAsyncioTestCase):
    """直接调用路由函数，验证提取逻辑与响应契约（与现有测试同风格）。"""

    async def test_empty_voice_text_rejected(self):
        from app.routers.work_session import extract_work_session, ExtractRequest
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            await extract_work_session(
                ExtractRequest(voice_text="   "), user=FakeUser()
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_returns_fields_without_record_id(self):
        """正常调用应返回 work_type + fields，且不含 record_id（仅预览不建记录）"""
        llm_json = (
            '{"work_type": "巡视", "confidence": 0.9, "reasons": ["语音提到巡视"], '
            '"suggested_title": "3号楼二层巡视", "fields": {"section": "3号楼二层", '
            '"location": "东单元", "weather": "晴", "work_content": "钢筋绑扎", '
            '"inspection_remark": ""}}'
        )
        from app.routers.work_session import extract_work_session, ExtractRequest

        with patch(
            "app.services.model_gateway.chat_completion",
            new=AsyncMock(return_value=(llm_json, {})),
        ) as mock_llm:
            result = await extract_work_session(
                ExtractRequest(voice_text="3号楼二层巡视，钢筋绑扎，天气晴"),
                user=FakeUser(),
            )
        # 确认走了 mock（真实 LLM 不应被调用）
        mock_llm.assert_awaited_once()
        self.assertEqual(result["work_type"], "巡视")
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["fields"]["section"], "3号楼二层")
        self.assertEqual(result["fields"]["weather"], "晴")
        self.assertEqual(result["suggested_title"], "3号楼二层巡视")
        # 关键：仅提取，不应创建记录
        self.assertNotIn("record_id", result)

    async def test_force_type_skips_classification(self):
        """force_type 应跳过自动分类直接抽取指定类型字段"""
        llm_json = (
            '{"confidence": 0.85, "reasons": ["用户指定旁站"], "suggested_title": null, '
            '"fields": {"section": "1号楼", "station_project_name": "混凝土浇筑", '
            '"station_content": "正常", "station_conclusion": "合格"}}'
        )
        from app.routers.work_session import extract_work_session, ExtractRequest

        with patch(
            "app.services.model_gateway.chat_completion",
            new=AsyncMock(return_value=(llm_json, {})),
        ) as mock_llm:
            result = await extract_work_session(
                ExtractRequest(voice_text="1号楼混凝土浇筑正常", force_type="旁站"),
                user=FakeUser(),
            )
        self.assertEqual(result["work_type"], "旁站")
        self.assertEqual(result["fields"]["station_project_name"], "混凝土浇筑")
        # 确认走了 force_type 分支（prompt 含"已确定为：旁站"）
        system_prompt = mock_llm.call_args[0][0][0]["content"]
        self.assertIn("已确定为：旁站", system_prompt)

    async def test_llm_failure_returns_503(self):
        """LLM 服务异常应返回 503"""
        from app.routers.work_session import extract_work_session, ExtractRequest
        from app.services.model_gateway import ModelGatewayError
        from fastapi import HTTPException

        with patch(
            "app.services.model_gateway.chat_completion",
            new=AsyncMock(side_effect=ModelGatewayError("模型服务不可用")),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await extract_work_session(
                    ExtractRequest(voice_text="测试语音"), user=FakeUser()
                )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("模型服务不可用", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
