import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_local


class LocalRuntimeTests(unittest.TestCase):
    def test_real_providers_with_isolated_local_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            local_dir = Path(directory)
            with patch.object(run_local, "LOCAL_DIR", local_dir), patch.dict(
                os.environ,
                {"DATABASE_URL": "mysql://production/db", "WECHAT_MOCK_LOGIN_ENABLED": "true", "SMS_PROVIDER": "mock"},
            ):
                run_local.configure_local()
                self.assertEqual(os.environ["WECHAT_MOCK_LOGIN_ENABLED"], "false")
                self.assertEqual(os.environ["SMS_PROVIDER"], "tencent")
                self.assertEqual(os.environ["GDJL_ENV_FILE"], str(local_dir / "backend.env"))
                self.assertEqual(os.environ["DATABASE_URL"], f"sqlite+aiosqlite:///{local_dir.as_posix()}/local.sqlite3")
                self.assertEqual(os.environ["UPLOAD_DIR"], str(local_dir / "uploads"))
                secret = os.environ["SECRET_KEY"]
                run_local.configure_local()
                self.assertEqual(os.environ["SECRET_KEY"], secret)


if __name__ == "__main__":
    unittest.main()
