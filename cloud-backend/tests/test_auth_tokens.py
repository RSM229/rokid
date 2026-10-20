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
            {"sub": "42", "purpose": "access", "role": "监理员"},
            expires_delta=timedelta(minutes=5),
        )
        payload = decode_access_token(token)
        self.assertEqual(payload["sub"], "42")
        self.assertEqual(payload["purpose"], "access")
        self.assertEqual(payload["role"], "监理员")

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
    def setUp(self) -> None:
        self.original_enabled = settings.allow_public_registration
        self.original_roles = settings.public_registration_roles
        settings.allow_public_registration = True
        settings.public_registration_roles = "监理员"

    def tearDown(self) -> None:
        settings.allow_public_registration = self.original_enabled
        settings.public_registration_roles = self.original_roles

    def test_registration_request_validates_public_input(self) -> None:
        request = RegisterRequest(
            username="customer_001",
            password="safe-pass-123",
            real_name="测试客户",
            role="监理员",
            invite_code="PERSONAL001",
        )
        self.assertEqual(request.username, "customer_001")

        with self.assertRaises(ValidationError):
            RegisterRequest(
                username="ab",
                password="short",
                real_name="测试客户",
                role="监理员",
                invite_code="PERSONAL001",
                project_id=1,
            )

    def test_invalid_role_configuration_falls_back_to_inspector(self) -> None:
        settings.public_registration_roles = "总监理工程师,not-a-role"
        self.assertEqual(get_public_registration_roles(), ["总监理工程师"])
        settings.public_registration_roles = "not-a-role"
        self.assertEqual(get_public_registration_roles(), ["监理员"])

    async def test_roles_endpoint_exposes_separate_registration_roles(self) -> None:
        response = await get_available_roles()
        self.assertTrue(response.public_registration_enabled)
        self.assertEqual(
            [item["value"] for item in response.registration_roles],
            ["监理员"],
        )
        self.assertEqual(len(response.roles), 4)

    async def test_public_registration_rejects_when_disabled(self) -> None:
        settings.allow_public_registration = False
        request = RegisterRequest(
            username="customer_002",
            password="safe-pass-123",
            real_name="测试客户",
            role="监理员",
            invite_code="PERSONAL001",
        )
        with self.assertRaises(HTTPException) as error:
            await register(request, db=object())  # type: ignore[arg-type]
        self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
