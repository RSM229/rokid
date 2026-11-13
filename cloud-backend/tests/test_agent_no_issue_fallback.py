"""巡视/旁站「问题及处理」兜底逻辑边界测试。

核心约束（2026-11-13 多图口径）：
- 用户原话明确说「无问题/符合要求」→ 兜底回填该词；
- 用户静默（没说）、或只发了照片但没说无问题 → **不兜底**，交给模型从素材归结，
  服务端严禁盲填「符合要求」（否则造假的合规记录）。
"""
import unittest

from app.services.agent_tools import _backfill_no_issue


class TestBackfillNoIssue(unittest.TestCase):
    def test_xunshi_explicit_no_issue_returns_hint(self):
        # 巡视 + 用户原话说「符合要求」→ 回填
        self.assertEqual(_backfill_no_issue("现场巡视，符合要求"), "符合要求")

    def test_xunshi_explicit_wu_yichang_returns_hint(self):
        self.assertEqual(_backfill_no_issue("经检查无异常"), "无异常")

    def test_silent_no_message_returns_empty(self):
        # 静默（空消息）→ 不兜底，留空交给模型
        self.assertEqual(_backfill_no_issue(""), "")
        self.assertEqual(_backfill_no_issue(None), "")

    def test_photo_only_no_hint_returns_empty(self):
        # 带了照片但用户只说「记一条巡视」、没说无问题 → 不盲填
        self.assertEqual(_backfill_no_issue("帮我记一条巡视，照片在下面"), "")

    def test_photo_with_real_problem_no_false_fill(self):
        # 照片其实描述了问题，但没命中"无问题"词 → 不兜底（避免假合规）
        self.assertEqual(_backfill_no_issue("这张图看到钢筋间距偏大"), "")

    def test_multi_photo_silent_returns_empty(self):
        # 多张照片静默上传 → 不盲填（多图尤其危险）
        self.assertEqual(_backfill_no_issue("这几张图都看看，记巡视"), "")


if __name__ == "__main__":
    unittest.main()
