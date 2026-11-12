import unittest
from datetime import timedelta

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import settings
from app.routers.auth import (
    RegisterRequest,
    create_access_token,
    decode_access_token,
    get_available_roles,
    get_public_registration_roles,
    register,
)


class AuthTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_secret = settings.secret_key
        settings.secret_key = "unit-test-secret-that-is-longer-than-32-characters"

    def tearDown(self) -> None:
        settings.secret_key = self.original_secret

    def test_access_token_round_trip(self) -> None:
        token = create_access_token(
            {"sub": "42", "purpose": "access", "role": "inspector"},
            expires_delta=timedelta(minutes=5),
        )
        payload = decode_access_token(token)
        self.assertEqual(payload["sub"], "42")
        self.assertEqual(payload["purpose"], "access")
        self.assertEqual(payload["role"], "inspector")

    def test_tampered_token_is_rejected(self) -> None:
        token = create_access_token({"sub": "42"}, expires_delta=timedelta(minutes=5))
        head, body, signature = token.split(".")
        replacement = "A" if signature[-1] != "A" else "B"
        tampered = f"{head}.{body}.{signature[:-1]}{replacement}"
        with self.assertRaises(HTTPException) as error:
            decode_access_token(tampered)
        self.assertEqual(error.exception.status_code, 401)

    def test_expired_token_is_rejected(self) -> None:
        token = create_access_token({"sub": "42"}, expires_delta=timedelta(seconds=-1))
        with self.assertRaises(HTTPException) as error:
            decode_access_token(token)
        self.assertEqual(error.exception.status_code, 401)


class PublicRegistrationTests(unittest.IsolatedAsyncioTestCase):
    """公开注册回归。

    现行口径（设计说明 §8.8.3）：注册请求是「手机号 + 短信验证码」，无邀请码；
    角色一律用 UserRole 的英文 key，注册只产出零权限裸用户。旧断言里的中文
    角色名、invite_code 都是改造前的桩，留着只会让这条守门长期假红。
    """

    VALID_FORM = dict(
        username="customer_001",
        password="safe-pass-123",
        real_name="测试客户",
        phone="13800138000",
        sms_code="123456",
        role="inspector",
    )

    def setUp(self) -> None:
        self.original_enabled = settings.allow_public_registration
        self.original_roles = settings.public_registration_roles
        settings.allow_public_registration = True
        settings.public_registration_roles = "inspector"

    def tearDown(self) -> None:
        settings.allow_public_registration = self.original_enabled
        settings.public_registration_roles = self.original_roles

    def test_registration_request_validates_public_input(self) -> None:
        request = RegisterRequest(**self.VALID_FORM)
        self.assertEqual(request.username, "customer_001")

        with self.assertRaises(ValidationError):
            RegisterRequest(
                **{**self.VALID_FORM, "username": "ab", "password": "short"},
                project_id=1,  # extra="forbid"：注册请求不接受项目绑定字段
            )

    def test_invalid_role_configuration_falls_back_to_inspector(self) -> None:
        settings.public_registration_roles = "chief_supervisor,not-a-role"
        self.assertEqual(get_public_registration_roles(), ["chief_supervisor"])
        settings.public_registration_roles = "not-a-role"
        self.assertEqual(get_public_registration_roles(), ["inspector"])
        # 中文展示名、历史非法 key（旧默认值 "supervisor"）同样必须被过滤
        settings.public_registration_roles = "总监理工程师,supervisor"
        self.assertEqual(get_public_registration_roles(), ["inspector"])

    async def test_roles_endpoint_exposes_separate_registration_roles(self) -> None:
        from app.models.user import ORG_ROLES, UserRole

        response = await get_available_roles()
        self.assertTrue(response.public_registration_enabled)
        self.assertEqual(
            [item["value"] for item in response.registration_roles],
            ["inspector"],
        )
        # client=web 默认下发全部项目级岗位 + 公司级角色，数量随枚举变化
        self.assertEqual(len(response.roles), len(UserRole) + len(ORG_ROLES))

    async def test_public_registration_rejects_when_disabled(self) -> None:
        settings.allow_public_registration = False
        with self.assertRaises(HTTPException) as error:
            await register(RegisterRequest(**self.VALID_FORM), db=object())  # type: ignore[arg-type]
        self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
