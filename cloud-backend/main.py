"""AI眼镜工程管理应用 — 云端后端服务入口"""
import uvicorn
from app.config import settings


def main():
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
