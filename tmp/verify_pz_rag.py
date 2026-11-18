"""本地验证：旁站范本汇编是否被 RAG 收录、检索能否命中（SQLite 隔离库，不碰生产）。

用 cloud-backend/.venv 跑：python D:/rokid/rokid/tmp/verify_pz_rag.py
"""
import asyncio
import os
import sys
import json

sys.path.insert(0, r"D:\rokid\rokid\cloud-backend")
sys.path.insert(0, r"D:\rokid\rokid\tmp")  # dev_local

os.environ["PYTHONUTF8"] = "1"

# 关键：模型在 .local/models，而不是默认的 models/
os.environ.setdefault(
    "RAG_MODEL_DIR",
    r"D:\rokid\rokid\cloud-backend\.local\models\bge-small-zh-v1.5",
)

from dev_local import prepare_env  # noqa: E402

prepare_env(port=8001)  # SQLite 隔离库 D:/rokid/tmp/local_dev.db


async def main():
    from app.db import init_db, async_session
    from app.models.user import User
    from sqlalchemy import select

    await init_db()
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.username == "dev"))).scalars().first()
        if user is None:
            from app.models.user import User as U
            u = U(username="dev", password_hash="x", real_name="本地测试", role="inspector", is_active=True)
            db.add(u)
            await db.commit()

    from app.services import rag_index

    # 1) 重建索引（knowledge 采集会扫 知识库/ 目录，含我们新放的 md）
    report = rag_index.build_all(force=True)
    print("\n=== 索引重建报告 ===")
    print(json.dumps({
        "indexed": report["indexed"],
        "skipped": report["skipped"],
        "empty": report["empty"],
        "failed": report["failed"],
        "chunks": report["chunks"],
        "embedding_available": report["embedding_available"],
        "errors": report["errors"][:5],
    }, ensure_ascii=False, indent=1))

    # 2) 确认我们的文档进了 kb_chunks
    from app.models.kb_chunk import KbChunk
    from sqlalchemy import func, select as sselect
    from app.services.rag_index import get_sync_session

    with get_sync_session() as s:
        row = s.execute(
            sselect(KbChunk.source_name, func.count(KbChunk.id))
            .where(KbChunk.source_name.like("%旁站记录范本%"))
            .group_by(KbChunk.source_name)
        ).all()
        print("\n=== 旁站范本在 kb_chunks 的块数 ===")
        for name, n in row:
            print(f"  {name}: {n} 块")
        if not row:
            print("  ⚠️ 未找到 —— 检查 knowledge_base_path 与 SUPPORTED_EXTS")

    # 3) 检索命中验证（语义：现场旁站要查点什么）
    print("\n=== 检索验证 ===")
    queries = [
        "混凝土浇筑旁站要检查哪些内容",
        "静压桩施工旁站监理要查什么",
        "防水卷材旁站记录",
        "钢结构吊装旁站要点",
        "电梯整机试运行旁站",
    ]
    for q in queries:
        hits = rag_index.search(q, top_k=3, source_types=None)
        print(f"\nQ: {q}")
        for h in hits[:3]:
            name = h.get("source_name") or h.get("source_id") or "?"
            score = h.get("score")
            print(f"   - {name}  (score={score})")
        if not hits:
            print("   (无命中)")


asyncio.run(main())