"""致命配置自检的回归测试。

覆盖背景：`check_production_security()` 只在 `debug=False` 时才执行，而 `debug`
默认 True。生产若漏配 DEBUG，JWT 会继续用仓库公开的占位密钥签发却全程无告警。
`check_critical_security()` 就是补这个洞——它必须与 DEBUG 无关地生效。
"""

import unittest

from app.config import STOCK_SECRET_KEY, check_critical_security, settings


class CriticalSecurityTests(unittest.TestCase):
    def setUp(self):
        self._orig_secret = settings.secret_key
        self._orig_model_key = settings.model_config_encryption_key
        self._orig_debug = settings.debug

    def tearDown(self):
        settings.secret_key = self._orig_secret
        settings.model_config_encryption_key = self._orig_model_key
        settings.debug = self._orig_debug

    def test_stock_secret_is_flagged_with_debug_on(self):
        """DEBUG 打开时也必须报出占位密钥（原实现此时整体短路，什么都不报）。"""
        settings.debug = True
        settings.secret_key = STOCK_SECRET_KEY
        settings.model_config_encryption_key = "m" * 40
        issues = check_critical_security()
        self.assertTrue(
            any("占位" in i for i in issues),
            f"占位密钥未被识别：{issues}",
        )

    def test_short_secret_is_flagged(self):
        settings.debug = True
        settings.secret_key = "short-key"
        settings.model_config_encryption_key = "m" * 40
        issues = check_critical_security()
        self.assertTrue(any("长度" in i for i in issues), issues)

    def test_model_key_shared_with_jwt_key_is_flagged(self):
        """模型密钥复用 JWT 密钥 → 一处泄露即两处失守，必须报出。"""
        settings.debug = True
        settings.secret_key = "j" * 40
        settings.model_config_encryption_key = "j" * 40
        issues = check_critical_security()
        self.assertTrue(any("MODEL_CONFIG_ENCRYPTION_KEY" in i for i in issues), issues)

    def test_clean_config_reports_nothing(self):
        settings.debug = False
        settings.secret_key = "a" * 40
        settings.model_config_encryption_key = "b" * 40
        self.assertEqual(check_critical_security(), [])


if __name__ == "__main__":
    unittest.main()
