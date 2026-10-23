import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_local
from scripts import configure_local_services


class LocalRuntimeTests(unittest.TestCase):
    def test_requires_server_config_and_uses_shared_server_database(self):
        with tempfile.TemporaryDirectory() as directory:
            local_dir = Path(directory)
            with patch.object(run_local, "LOCAL_DIR", local_dir), patch.dict(
                os.environ,
                {"DATABASE_URL": "mysql://production/db", "WECHAT_MOCK_LOGIN_ENABLED": "true", "SMS_PROVIDER": "mock", "LOCAL_DATABASE_URL": ""},
            ):
                with self.assertRaisesRegex(ValueError, "not configured"):
                    run_local.configure_local()
                local_env = local_dir / "backend.env"
                local_env.write_text(
                    "LOCAL_DATABASE_URL=mysql+aiomysql://local:secret@127.0.0.1:13308/gdjl?charset=utf8mb4\n",
                    encoding="utf-8",
                )
                run_local.configure_local()
                self.assertEqual(os.environ["WECHAT_MOCK_LOGIN_ENABLED"], "false")
                self.assertEqual(os.environ["SMS_PROVIDER"], "tencent")
                self.assertEqual(os.environ["GDJL_ENV_FILE"], str(local_dir / "backend.env"))
                self.assertEqual(os.environ["DATABASE_URL"], "mysql+aiomysql://local:secret@127.0.0.1:13308/gdjl?charset=utf8mb4")
                self.assertEqual(os.environ["GDJL_SKIP_DB_INIT"], "true")
                self.assertEqual(os.environ["UPLOAD_DIR"], str(local_dir / "uploads"))
                secret = os.environ["SECRET_KEY"]
                run_local.configure_local()
                self.assertEqual(os.environ["SECRET_KEY"], secret)

    def test_allows_shared_forwarded_mysql_database(self):
        with tempfile.TemporaryDirectory() as directory:
            local_dir = Path(directory)
            database_url = "mysql+aiomysql://local:secret@127.0.0.1:13308/gdjl?charset=utf8mb4"
            with patch.object(run_local, "LOCAL_DIR", local_dir), patch.dict(
                os.environ, {"LOCAL_DATABASE_URL": database_url}, clear=False
            ):
                run_local.configure_local()
                self.assertEqual(os.environ["DATABASE_URL"], database_url)

    def test_allows_live_database_name_and_refuses_non_forwarded_mysql(self):
        with tempfile.TemporaryDirectory() as directory:
            database_url = "mysql+aiomysql://production:secret@127.0.0.1:13308/live_database"
            with patch.object(run_local, "LOCAL_DIR", Path(directory)), patch.dict(
                os.environ, {"LOCAL_DATABASE_URL": database_url}, clear=False
            ):
                run_local.configure_local()
                self.assertEqual(os.environ["DATABASE_URL"], database_url)

        for database_url in (
            "mysql+aiomysql://local:secret@server.example:3306/gdjl",
            "sqlite+aiosqlite:///local.sqlite3",
        ):
            with self.subTest(database_url=database_url), tempfile.TemporaryDirectory() as directory:
                with patch.object(run_local, "LOCAL_DIR", Path(directory)), patch.dict(
                    os.environ, {"LOCAL_DATABASE_URL": database_url}, clear=False
                ):
                    with self.assertRaises(ValueError):
                        run_local.configure_local()

    def test_live_database_url_keeps_production_credentials_and_database(self):
        forwarded = configure_local_services.forwarded_database_url(
            "mysql://production_user:p%40ss@mysql.internal:3306/live_gdjl?charset=utf8mb4"
        )
        self.assertEqual(
            forwarded,
            "mysql+aiomysql://production_user:p%40ss@127.0.0.1:13308/live_gdjl?charset=utf8mb4",
        )

    def test_live_database_forwarding_rejects_non_mysql_url(self):
        with self.assertRaisesRegex(RuntimeError, "有效的 MySQL"):
            configure_local_services.forwarded_database_url("sqlite:///local.db")


if __name__ == "__main__":
    unittest.main()
