"""Own the SSH database tunnel and local reload server in one terminal."""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time

from scripts.configure_local_services import (
    BACKEND_DIR,
    LOCAL_DIR,
    SSH_ARGS,
    SSH_HOST,
    sync_embedding_model,
)


def listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-config", action="store_true")
    args = parser.parse_args()
    os.chdir(BACKEND_DIR)
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if listening(8000) or listening(13308):
        raise RuntimeError("Port 8000 or 13308 is in use. Stop the previous local dev runtime first.")
    if args.refresh_config or not (LOCAL_DIR / "backend.env").exists():
        subprocess.run([sys.executable, "-m", "scripts.configure_local_services"], check=True)
    sync_embedding_model()
    from scripts.run_local import configure_local
    configure_local()
    if not os.environ["DATABASE_URL"].startswith("mysql+aiomysql:"):
        raise RuntimeError("Remote MySQL dev configuration missing; run with --refresh-config.")
    creation = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    tunnel = server = None
    try:
        tunnel = subprocess.Popen([
            "ssh", "-NT", *SSH_ARGS, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-L", "127.0.0.1:13308:127.0.0.1:3308", SSH_HOST,
        ], **creation)
        for _ in range(30):
            if tunnel.poll() is not None:
                raise RuntimeError("SSH database tunnel exited before becoming ready.")
            if listening(13308):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("SSH database tunnel did not become ready.")
        print("Local API: http://127.0.0.1:8000/docs (reload enabled)", flush=True)
        server = subprocess.Popen([sys.executable, "-m", "scripts.run_local"], **creation)
        while server.poll() is None:
            if tunnel.poll() is not None:
                raise RuntimeError("SSH tunnel disconnected. Restart this launcher to reconnect.")
            time.sleep(1)
        if server.returncode:
            raise RuntimeError("Local backend exited; inspect the startup log above.")
    except KeyboardInterrupt:
        pass
    finally:
        stop(server)
        stop(tunnel)


if __name__ == "__main__":
    main()
