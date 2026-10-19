import unittest
from unittest.mock import AsyncMock, patch

from app.routers.local_ai import (
    VoiceCommandRequest,
    infer_voice_module,
    voice_command_match,
)


class VoiceRoutingTests(unittest.TestCase):
    def test_routes_all_integrated_work_types(self) -> None:
        cases = {
            "开始巡视三号楼施工现场": "inspection",
            "开始验收地下室防水": "acceptance",
            "开始旁站混凝土浇筑": "station",
            "记录钢筋见证取样": "witness",
            "发现一处质量问题": "quality",
            "记录今天的协调会议": "other",
            "开始安全巡检，检查危险源": "hazard",
            "复查昨天的整改问题并闭环": "rectify",
        }
        for utterance, expected in cases.items():
            with self.subTest(utterance=utterance):
                self.assertEqual(infer_voice_module(utterance), expected)

    def test_specific_rectification_intent_wins_over_generic_quality(self) -> None:
        self.assertEqual(
            infer_voice_module("复查质量问题整改完成情况"),
            "rectify",
        )

    def test_returns_none_without_business_clue(self) -> None:
        self.assertIsNone(infer_voice_module("帮我拍张照片"))


class VoiceCommandContractTests(unittest.IsolatedAsyncioTestCase):
    @patch(
        "app.routers.local_ai.model_gateway.chat_completion",
        new_callable=AsyncMock,
    )
    async def test_returns_resolved_module_and_workflow(self, chat: AsyncMock) -> None:
        chat.return_value = (
            '{"action":"start","params":{"target_module":"station"},'
            '"reply":"已开始旁站记录。"}',
            object(),
        )
        result = await voice_command_match(
            VoiceCommandRequest(text="开始旁站混凝土浇筑", module="auto"),
            user=object(),  # type: ignore[arg-type]
        )
        self.assertEqual(result["module"], "station")
        self.assertEqual(result["module_label"], "旁站记录")
        self.assertIn("旁站工作流程", result["workflow"])

    @patch(
        "app.routers.local_ai.model_gateway.chat_completion",
        new_callable=AsyncMock,
    )
    async def test_keyword_routing_corrects_invalid_model_target(
        self,
        chat: AsyncMock,
    ) -> None:
        chat.return_value = (
            '{"action":"end","params":{"target_module":"invalid"},'
            '"reply":"已完成。"}',
            object(),
        )
        result = await voice_command_match(
            VoiceCommandRequest(text="复查质量问题整改完成情况", module="auto"),
            user=object(),  # type: ignore[arg-type]
        )
        self.assertEqual(result["module"], "rectify")
        self.assertEqual(result["params"]["target_module"], "rectify")


if __name__ == "__main__":
    unittest.main()
