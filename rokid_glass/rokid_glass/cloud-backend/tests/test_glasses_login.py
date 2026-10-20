import time
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from app.routers.auth import (
    GlassesScanRequest,
    create_glasses_qr,
    glasses_scan_login,
)


class FakeScalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, user):
        self._user = user

    async def execute(self, query):
        return FakeScalar(self._user)

    async def get(self, model, pk):
        return None

    async def commit(self):
        return None


class FakeUser:
    id = 7
    username = "zhang_san"
    real_name = "张三"
    role = "监理员"
    avatar_url = None
    project_id = None
    is_active = True
    password_hash = "x"


TICKETS = {}


def patch_store():
    global TICKETS
    from app.routers import auth as auth_module

    auth_module._glasses_tickets = TICKETS


class QRCodeLoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        patch_store()
        self.tickets = TICKETS

    async def test_create_qr_returns_ticket(self):
        user = FakeUser()
        result = await create_glasses_qr(user)
        self.assertTrue(result["ticket"])
        self.assertEqual(result["expires_in"], 120)
        self.assertIn(result["ticket"], self.tickets)

    async def test_scan_exchanges_ticket_for_token_once(self):
        user = FakeUser()
        created = await create_glasses_qr(user)
        db = FakeDB(user)

        # 第一次扫码成功换令牌
        response = await glasses_scan_login(GlassesScanRequest(ticket=created["ticket"]), db=db)
        self.assertTrue(response.access_token)
        self.assertEqual(response.user_id, 7)
        self.assertEqual(response.username, "zhang_san")
        self.assertEqual(response.role, "监理员")
        import jwt as pyjwt

        from app.config import settings

        payload = pyjwt.decode(
            response.access_token, settings.secret_key, algorithms=[settings.algorithm]
        )
        self.assertEqual(payload["device"], "glasses")
        self.assertEqual(payload["purpose"], "access")

        # 一次性：再次使用同一码应失败
        with self.assertRaises(HTTPException) as error:
            await glasses_scan_login(GlassesScanRequest(ticket=created["ticket"]), db=db)
        self.assertEqual(error.exception.status_code, 404)

    async def test_scan_rejects_unknown_or_expired_ticket(self):
        with self.assertRaises(HTTPException) as error:
            await glasses_scan_login(GlassesScanRequest(ticket="unknown-ticket-value"), db=object())
        self.assertEqual(error.exception.status_code, 404)

        user = FakeUser()
        created = await create_glasses_qr(user)
        self.tickets[created["ticket"]] = (user.id, time.time() - 1)
        with self.assertRaises(HTTPException) as error:
            await glasses_scan_login(GlassesScanRequest(ticket=created["ticket"]), db=object())
        self.assertEqual(error.exception.status_code, 404)

    async def test_scan_request_validates_ticket_length(self):
        with self.assertRaises(ValidationError):
            GlassesScanRequest(ticket="short")


if __name__ == "__main__":
    unittest.main()