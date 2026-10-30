# -*- coding: utf-8 -*-
"""登录防爆破限流回归测试。

覆盖两层：
1) ``LoginRateLimiter`` 纯逻辑（注入时钟，无 DB）：达阈值锁、成功后清零、窗口过期、
   key 相互隔离、429 带 Retry-After。
2) ``POST /api/auth/login`` 端点：连错 5 次仍 401，第 6 次 429；换用户名不受影响。
   端点测试用依赖覆盖 + patch verify 返回 False 模拟"密码错误"，不触碰真实 DB。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services.login_limiter import LoginRateLimiter


class LoginRateLimiterTest(unittest.TestCase):
    def test_locks_after_max_attempts(self):
        lim = LoginRateLimiter(max_attempts=3, window_seconds=60, lockout_seconds=120)
        base = 1_000.0
        for i in range(3):
            lim.register_failure("k", now=base + i)
        with self.assertRaises(HTTPException) as ctx:
            lim.check("k", now=base + 3)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Retry-After", ctx.exception.headers)

    def test_below_threshold_passes(self):
        lim = LoginRateLimiter(max_attempts=3, window_seconds=60)
        lim.register_failure("k", now=1_000.0)
        lim.register_failure("k", now=1_001.0)
        lim.check("k", now=1_002.0)  # 2 < 3，不抛

    def test_reset_clears_counter(self):
        lim = LoginRateLimiter(max_attempts=2, window_seconds=60)
        lim.register_failure("k", now=1_000.0)
        lim.register_failure("k", now=1_001.0)
        lim.reset("k")
        lim.check("k", now=1_002.0)  # 已清零，不抛

    def test_old_failures_expire(self):
        lim = LoginRateLimiter(max_attempts=2, window_seconds=60)
        lim.register_failure("k", now=1_000.0)
        lim.register_failure("k", now=1_001.0)
        # 62s 后两次失败均已滑出 60s 窗口 → 不应再锁
        lim.check("k", now=1_062.0)

    def test_keys_isolated(self):
        lim = LoginRateLimiter(max_attempts=1, window_seconds=60)
        lim.register_failure("a", now=1_000.0)
        with self.assertRaises(HTTPException):
            lim.check("a", now=1_001.0)
        lim.check("b", now=1_001.0)  # 另一个 key 不受 a 影响


# ---- 端点级：真实走 /api/auth/login，用桩 DB + 恒失败的 verify ----

class _FakeResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeUser:
    # 仅需 password_hash（被 verify 读取）；login 在 verify 失败后立即 401，不触达其他属性
    password_hash = "not-a-real-hash"


class _FakeDb:
    async def execute(self, stmt):
        return _FakeResult(_FakeUser())

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    def add(self, obj):
        pass


class LoginEndpointRateLimitTest(unittest.TestCase):
    def setUp(self):
        from app.main import app
        from app.db import get_db
        import app.routers.auth as auth_mod

        self.app = app
        self.auth_mod = auth_mod
        self._saved_overrides = dict(app.dependency_overrides)

        async def _fake_get_db():
            yield _FakeDb()

        app.dependency_overrides[get_db] = _fake_get_db
        # 密码校验恒失败（模拟错误口令），避免依赖真实哈希 / bcrypt
        self._verify_patcher = patch.object(auth_mod.pwd_context, "verify", return_value=False)
        self._verify_patcher.start()

        auth_mod.default_limiter._fails.clear()
        self.client = TestClient(app)

    def tearDown(self):
        self._verify_patcher.stop()
        self.app.dependency_overrides = self._saved_overrides
        self.auth_mod.default_limiter._fails.clear()

    def test_five_failures_then_429(self):
        payload = {"username": "victim", "password": "wrong"}
        for i in range(5):
            r = self.client.post("/api/auth/login", json=payload)
            self.assertEqual(r.status_code, 401, f"第 {i + 1} 次应为 401")
        r = self.client.post("/api/auth/login", json=payload)
        self.assertEqual(r.status_code, 429)
        self.assertIn("retry-after", {k.lower() for k in r.headers})

    def test_other_username_not_locked(self):
        payload = {"username": "victim", "password": "wrong"}
        for _ in range(6):
            self.client.post("/api/auth/login", json=payload)
        # 另一个用户名不受 victim 的锁定影响
        r = self.client.post("/api/auth/login", json={"username": "other", "password": "wrong"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
