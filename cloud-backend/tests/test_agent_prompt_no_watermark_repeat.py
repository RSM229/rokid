"""回归：水印/图片识别内容只留在内部思考，不再写进 answer。

2026-11-16 用户要求「识别的内容只保留到思考里，不需要写出来，保留其他就行」。
这里只校验 _AGENT_SYSTEM 的文本口径（不调模型、不花钱）；模型行为由
tests/agent_eval/cases.py 的 K05 覆盖（识别值改为 answer_forbidden）。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _agent_system() -> str:
    tree = ast.parse((ROOT / "app/routers/agent.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id == "_AGENT_SYSTEM"
                        and isinstance(node.value, ast.Constant)):
                    return str(node.value.value)
    raise AssertionError("_AGENT_SYSTEM 未找到或不是字符串常量")


class WatermarkNotWrittenOutTest(unittest.TestCase):
    def test_prompt_keeps_recognition_internal(self):
        prompt = _agent_system()
        self.assertIn("识别到的内容只保留在你的内部思考里", prompt)
        self.assertIn("不引用、不复述水印原文", prompt)
        self.assertIn("同样不写出识别内容", prompt)
        self.assertIn("识别结果不写进 answer", prompt)

    def test_prompt_no_longer_requires_reciting_watermark(self):
        prompt = _agent_system()
        stale = [
            "必须先把水印内容逐项复述给用户",
            "先逐项复述水印",
            "先按上文把水印客观信息逐项复述",
            "逐项复述（每张照片单独一行",
            "先把水印逐张复述给用户",
            "先复述水印",
            "复述完即可",
        ]
        for phrase in stale:
            self.assertNotIn(phrase, prompt, phrase)

    def test_other_rules_are_kept(self):
        """「保留其他就行」：分流、防编造、自动回填说明都不能被一起删掉。"""
        prompt = _agent_system()
        self.assertIn("image_groups", prompt)
        self.assertIn("严禁凭空说", prompt)
        self.assertIn("识别≠强制建档", prompt)
        self.assertIn("已自动带入", prompt)
        self.assertIn("只问一个决定性问题", prompt)


if __name__ == "__main__":
    unittest.main()
