"""小程序公开注册必须复用系统开关与角色白名单。"""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.routers import miniprogram_auth as auth


class RegistrationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def request(self, role="监理员"):
        return auth.RegisterRequest(username="test-user", password="test-only-password",
                                    real_name="测试", role=role)

    async def test_disabled_registration_rejected_before_database_access(self):
        with patch.object(auth.settings, "allow_public_registration", False):
            with self.assertRaises(HTTPException) as error:
                await auth.register_account(self.request(), db=object())
        self.assertEqual(error.exception.status_code, 403)

    async def test_high_privilege_role_rejected_before_database_access(self):
        with patch.object(auth.settings, "allow_public_registration", True), \
                patch.object(auth.settings, "public_registration_roles", "监理员"):
            for role in ("总监理工程师", "总监代表", "专业监理工程师"):
                with self.subTest(role=role), self.assertRaises(HTTPException) as error:
                    await auth.register_account(self.request(role), db=object())
                self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
