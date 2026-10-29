import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routers.agent import (  # noqa: E402
    _missing_record_type_reply,
    _plan_evidence_count,
)


class MissingRecordTypeReplyTests(unittest.TestCase):
    """2026-10-27 回归：没收到素材时不能声称“已保留这些图片”。

    用户上传 9 张图后草稿保存失败，下一句「继续记录工作」其实既没有历史
    消息也没有图片，但旧文案仍说“我已收到并保留这些图片”，导致用户误以为
    上下文还在。
    """

    def test_without_evidence_does_not_claim_images_were_kept(self):
        reply = _missing_record_type_reply(False)
        self.assertNotIn("已收到并保留", reply)
        self.assertIn("还没收到现场照片", reply)

    def test_with_evidence_keeps_reassurance(self):
        self.assertIn("已收到并保留", _missing_record_type_reply(True))

    def test_plan_evidence_count_reads_items_and_unassigned(self):
        plan = {
            "item_plan": {
                "items": [{"evidence_indexes": [0, 1]}, {"evidence_indexes": []}, "bad-item"],
                "unassigned_evidence_indexes": [2],
            }
        }
        self.assertEqual(_plan_evidence_count(plan), 3)

    def test_plan_evidence_count_tolerates_missing_item_plan(self):
        self.assertEqual(_plan_evidence_count({}), 0)


if __name__ == "__main__":
    unittest.main()