"""Provision shared server DB access and copy service settings over SSH."""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
from pathlib import Path

from dotenv import set_key
from sqlalchemy.engine import URL

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_DIR = BACKEND_DIR / ".local"
MODEL_DIR = LOCAL_DIR / "models" / "bge-small-zh-v1.5"

# Values are captured directly into a private local file, never printed to logs.
REMOTE_SETTINGS = '''
import json
from app.config import settings
allowed = (
    "wechat_", "default_llm_", "default_vlm_", "default_stt_", "default_tts_",
    "huagong_", "ai_center_", "vlm_", "sms_tencent_", "trtc_", "weather_",
)
names = {"rtc_provider", "model_config_encryption_key", "allow_wechat_registration",
         "allow_public_registration", "public_registration_roles"}
print(json.dumps({key.upper(): value for key, value in settings.model_dump().items()
                  if key.startswith(allowed) or key in names}, default=str))
'''

REMOTE_PROVISION = '''
import json
import sys
import pymysql
from sqlalchemy.engine import make_url
from app.config import settings
password = json.load(sys.stdin)["password"]
url = make_url(settings.database_url)
connection = pymysql.connect(host=url.host, port=url.port or 3306,
    user=url.username, password=url.password, charset="utf8mb4", autocommit=True)
with connection.cursor() as cursor:
    cursor.execute("CREATE USER IF NOT EXISTS 'rokid_local_dev'@'%%' IDENTIFIED BY %s", (password,))
    cursor.execute("ALTER USER 'rokid_local_dev'@'%%' IDENTIFIED BY %s", (password,))
    cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON gdjl.* TO 'rokid_local_dev'@'%'")
connection.close()
print("Shared gdjl database access is ready through the R640 SSH tunnel.")
'''


def ssh_python(code: str, data: str | None = None) -> str:
    # The remote command is shell parsed; encode the fixed Python program safely.
    import base64
    encoded = base64.b64encode(code.encode()).decode()
    command = (
        "docker exec -i rokid-backend python -c "
        f'"import base64; exec(base64.b64decode(\'{encoded}\'))"'
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "-o", "ConnectTimeout=10", "rokid-r640", command],
        input=data, text=True, encoding="utf-8", capture_output=True, timeout=60,
    )
    if result.returncode:
        # Driver errors may contain credentials. Keep failure output private.
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        (LOCAL_DIR / "configure-error.log").write_text(result.stderr, encoding="utf-8")
        raise RuntimeError("Remote configuration failed; see .local/configure-error.log")
    return result.stdout


def sync_embedding_model() -> None:
    """Copy the same public CPU model used by the deployed application."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("model.onnx", "tokenizer.json", "config.json",
                 "tokenizer_config.json", "special_tokens_map.json", "vocab.txt"):
        target = MODEL_DIR / name
        if target.exists() and target.stat().st_size:
            continue
        temporary = target.with_suffix(target.suffix + ".part")
        print(f"Synchronizing embedding asset: {name}", flush=True)
        try:
            with temporary.open("wb") as output:
                subprocess.run(
                    ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                     "-o", "ConnectTimeout=10", "rokid-r640",
                     f"docker exec rokid-backend cat /app/models/bge-small-zh-v1.5/{name}"],
                    stdout=output, check=True, timeout=600,
                )
            if not temporary.stat().st_size:
                raise RuntimeError(f"Empty embedding asset: {name}")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    values = json.loads(ssh_python(REMOTE_SETTINGS))
    required = ("WECHAT_APP_ID", "WECHAT_APP_SECRET", "DEFAULT_LLM_API_KEY", "DEFAULT_VLM_API_KEY")
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise RuntimeError("Server service settings missing: " + ", ".join(missing))
    password_file = LOCAL_DIR / "db-password.key"
    if not password_file.exists():
        password_file.write_text(secrets.token_urlsafe(36), encoding="utf-8")
    password = password_file.read_text(encoding="utf-8").strip()
    ssh_python(REMOTE_PROVISION, json.dumps({"password": password}))
    values["LOCAL_DATABASE_URL"] = URL.create(
        "mysql+aiomysql", username="rokid_local_dev", password=password,
        host="127.0.0.1", port=13308, database="gdjl",
        query={"charset": "utf8mb4"},
    ).render_as_string(hide_password=False)
    # Preserve any other developer overrides; never import production DATABASE_URL/JWT keys.
    env_file = LOCAL_DIR / "backend.env"
    for name, value in values.items():
        if isinstance(value, (dict, list)):
            continue
        text_value = str(value).lower() if isinstance(value, bool) else str(value)
        set_key(str(env_file), name, text_value)
    print("Real WeChat/AI settings synchronized to ignored .local/backend.env (values hidden).")
    print("Database: shared server gdjl via the R640 SSH tunnel on localhost:13308.")
    sync_embedding_model()


if __name__ == "__main__":
    if "--assets-only" in sys.argv:
        sync_embedding_model()
    else:
        main()
