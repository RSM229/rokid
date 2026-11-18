"""幂等本地后端启动器（供计划任务/双击常驻调用）。

- 端口 8000 已被监听 -> 立即退出（exit 0），不重复起、不刷屏。
- 否则在本进程内直接 uvicorn.run（不派生子进程，避免计划任务实例结束时被带走）。
- 用 pythonw.exe 跑无控制台窗口；stdout/stderr 重定向到日志，出错有声。
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

TMP = Path(r"D:\rokid\rokid\tmp")
LOG = TMP / "local_backend.log"
PORT = 8000


def listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    # pythonw 无 stdout/stderr，先接管到日志文件，否则出错完全无声
    log = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log

    if listening(PORT):
        print(f"[launcher] port {PORT} already in use, exit.", flush=True)
        return

    # 复用 dev_local 的环境装配（SQLite 隔离库 + 微信凭据注入）
    sys.path.insert(0, str(TMP))
    import dev_local  # noqa: E402

    dev_local.prepare_env()
    print(f"[launcher] starting backend on 127.0.0.1:{PORT} ...", flush=True)

    import asyncio
    from app.db import init_db

    asyncio.run(init_db())

    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, reload=False, log_level="info")


if __name__ == "__main__":
    main()
