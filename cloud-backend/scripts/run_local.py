"""Run with: .venv/Scripts/python -m scripts.run_local"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_DIR = BACKEND_DIR / ".local"


def configure_local() -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    secret_file = LOCAL_DIR / "secret.key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    # Local runs read only this file, never the deployment .env.
    os.environ.update({
        "GDJL_ENV_FILE": str(LOCAL_DIR / "backend.env"),
        "HOST": "127.0.0.1",
        "PORT": "8000",
        "DEBUG": "true",
        "DAILY_DOC_AUTO": "false",
        "WECHAT_MOCK_LOGIN_ENABLED": "false",
        "SMS_PROVIDER": "tencent",
        "SECRET_KEY": secret_file.read_text(encoding="utf-8").strip(),
        "DATABASE_URL": f"sqlite+aiosqlite:///{(LOCAL_DIR / 'local.sqlite3').as_posix()}",
        "UPLOAD_DIR": str(LOCAL_DIR / "uploads"),
        "EXPORT_DIR": str(LOCAL_DIR / "exports"),
        "PUBLIC_API_BASE_URL": "http://127.0.0.1:8000",
        "PUBLIC_WS_BASE_URL": "ws://127.0.0.1:8000",
    })


if __name__ == "__main__":
    configure_local()
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
