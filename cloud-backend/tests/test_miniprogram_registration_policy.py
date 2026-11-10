"""小程序公开注册必须复用系统开关，且注册通道拿不到公司级角色。"""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.models.user import ORG_ROLES
from app.routers import miniprogram_auth as auth


class RegistrationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def request(self, role=None):
        # 现行注册请求：手机号 + 短信验证码（role 仅为主岗占位，可为 None）
        return auth.RegisterRequest(
            username="test-user",
            password="test-only-password",
            real_name="测试",
            role=role,
            phone="13800138000",
            sms_code="123456",
        )

    async def test_disabled_registration_rejected_before_database_access(self):
        with patch.object(auth.settings, "allow_public_registration", False):
            with self.assertRaises(HTTPException) as error:
                await auth.register_account(self.request(), db=object())
        self.assertEqual(error.exception.status_code, 403)

    async def test_company_level_roles_rejected_before_database_access(self):
        """公司级角色必须在碰库前被拒，注册不允许伪装成超管/管理员。

        旧口径按 public_registration_roles 白名单拦「总监理工程师」等项目级
        高权限角色；新版注册产出零权限裸用户（org_role / project_id 均为
        None），项目岗位由「加入项目」单独授予，因此只剩 ORG_ROLES 这条
        伪装入口需要守。db=object() 保证断言的确实是「碰库前」的分支。
        """
        with patch.object(auth.settings, "allow_public_registration", True):
            for role in sorted(ORG_ROLES):
                with self.subTest(role=role), self.assertRaises(HTTPException) as error:
                    await auth.register_account(self.request(role), db=object())
                self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
