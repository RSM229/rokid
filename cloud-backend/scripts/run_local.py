"""Run with: .venv/Scripts/python -m scripts.run_local"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_DIR = BACKEND_DIR / ".local"


def configure_local() -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    secret_file = LOCAL_DIR / "secret.key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    # Local runs read only this file, never the deployment .env.
    local_env = dotenv_values(LOCAL_DIR / "backend.env")
    database_url = (os.getenv("LOCAL_DATABASE_URL") or local_env.get("LOCAL_DATABASE_URL") or "").strip()
    if not database_url:
        raise ValueError("Local dev database is not configured; run start-local-dev.ps1 first")
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "mysql" or parsed.database != "gdjl":
        raise ValueError("LOCAL_DATABASE_URL must target the shared server MySQL database gdjl")
    if parsed.host not in {"127.0.0.1", "localhost"} or parsed.port != 13308:
        raise ValueError("Local MySQL must use the SSH-forwarded endpoint 127.0.0.1:13308")

    os.environ.update({
        "GDJL_ENV_FILE": str(LOCAL_DIR / "backend.env"),
        "HOST": "127.0.0.1",
        "PORT": "8000",
        "DEBUG": "true",
        "DAILY_DOC_AUTO": "false",
        "GDJL_SKIP_DB_INIT": "true",
        "WECHAT_MOCK_LOGIN_ENABLED": "false",
        "SMS_PROVIDER": "tencent",
        "SECRET_KEY": secret_file.read_text(encoding="utf-8").strip(),
        "DATABASE_URL": database_url,
        "UPLOAD_DIR": str(LOCAL_DIR / "uploads"),
        "EXPORT_DIR": str(LOCAL_DIR / "exports"),
        "RAG_MODEL_DIR": str(LOCAL_DIR / "models" / "bge-small-zh-v1.5"),
        "PUBLIC_API_BASE_URL": "http://127.0.0.1:8000",
        "PUBLIC_WS_BASE_URL": "ws://127.0.0.1:8000",
    })


if __name__ == "__main__":
    configure_local()
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(BACKEND_DIR / "app")],
    )
