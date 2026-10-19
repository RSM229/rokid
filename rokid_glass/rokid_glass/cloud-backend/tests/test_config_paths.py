import unittest
from pathlib import Path

from app.config import BACKEND_DIR, Settings


class ConfigPathTests(unittest.TestCase):
    def test_backend_env_file_is_absolute_and_cwd_independent(self) -> None:
        env_file = Path(Settings.model_config["env_file"])
        self.assertTrue(env_file.is_absolute())
        self.assertEqual(env_file, BACKEND_DIR / ".env")


if __name__ == "__main__":
    unittest.main()
