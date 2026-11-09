"""Copy live service settings and tunnel the live database URL over SSH."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import set_key
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_DIR = BACKEND_DIR / ".local"
MODEL_DIR = LOCAL_DIR / "models" / "bge-small-zh-v1.5"
# SSH host used to reach the shared server. Override for environments where the
# R640 reverse tunnel is not set up (e.g. direct cloud access via `rokid-deploy`).
SSH_HOST = os.getenv("ROKID_SSH_HOST", "rokid-r640")
# Extra ssh options (space separated) for environments that cannot read ~/.ssh/config.
SSH_ARGS = [a for a in os.getenv("ROKID_SSH_ARGS", "").split() if a]


def forwarded_database_url(database_url: str) -> str:
    """Route the live application's MySQL credentials through the SSH tunnel."""
    production_url = make_url(database_url)
    if production_url.get_backend_name() != "mysql" or not production_url.database:
        raise RuntimeError("线上 DATABASE_URL 不是有效的 MySQL 数据库地址")
    return production_url.set(
        drivername="mysql+aiomysql", host="127.0.0.1", port=13308,
    ).render_as_string(hide_password=False)

# Values are captured directly into a private local file, never printed to logs.
REMOTE_SETTINGS = '''
import json
from app.config import settings
allowed = (
    "wechat_", "default_llm_", "default_vlm_", "default_stt_", "default_tts_",
    "huagong_", "ai_center_", "vlm_", "sms_tencent_", "weather_",
)
names = {"model_config_encryption_key", "allow_wechat_registration",
         "allow_public_registration", "public_registration_roles"}
values = {key.upper(): value for key, value in settings.model_dump().items()
          if key.startswith(allowed) or key in names}
values["PRODUCTION_DATABASE_URL"] = settings.database_url
print(json.dumps(values, default=str))
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
        ["ssh", *SSH_ARGS, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "-o", "ConnectTimeout=10", SSH_HOST, command],
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
                    ["ssh", "-T", *SSH_ARGS, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                     "-o", "ConnectTimeout=10", SSH_HOST,
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
    production_database_url = str(values.pop("PRODUCTION_DATABASE_URL", "")).strip()
    if not production_database_url:
        raise RuntimeError("线上服务未提供 DATABASE_URL，不能配置本地共享数据库")
    values["LOCAL_DATABASE_URL"] = forwarded_database_url(production_database_url)
    # Preserve any other developer overrides; never import production DATABASE_URL/JWT keys.
    env_file = LOCAL_DIR / "backend.env"
    for name, value in values.items():
        if isinstance(value, (dict, list)):
            continue
        text_value = str(value).lower() if isinstance(value, bool) else str(value)
        set_key(str(env_file), name, text_value)
    print("Real WeChat/AI settings synchronized to ignored .local/backend.env (values hidden).")
    print("Database: live application MySQL via the R640 SSH tunnel on localhost:13308 (credentials hidden).")
    sync_embedding_model()


if __name__ == "__main__":
    if "--assets-only" in sys.argv:
        sync_embedding_model()
    else:
        main()
