import unittest
from unittest.mock import AsyncMock, patch

from app.db import close_db


class DatabaseRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_db_ignores_connection_lost_during_shutdown(self):
        engine = AsyncMock()
        engine.dispose.side_effect = ConnectionResetError("tunnel closed")

        with patch("app.db.engine", engine):
            await close_db()

        engine.dispose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
