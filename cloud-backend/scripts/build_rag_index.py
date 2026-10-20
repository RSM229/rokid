"""构建 / 刷新 RAG 检索索引

用法（容器内执行）：
    python scripts/build_rag_index.py                # 增量（内容未变的来源跳过）
    python scripts/build_rag_index.py --force        # 全量重建
    python scripts/build_rag_index.py --only knowledge_doc,work_record
    python scripts/build_rag_index.py --stats        # 只看索引概览

索引来源：
    knowledge_doc  知识库目录下全部可解析文档（docx/xlsx/pdf）
    work_record    工作记录（现场巡检/旁站/见证/验收等）
    document       已生成的省统表文书
    note           笔记
    kg_entity      知识图谱实体（名称+类型+说明）
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import rag_index  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 RAG 检索索引")
    parser.add_argument("--force", action="store_true", help="忽略 source_hash，全量重建")
    parser.add_argument("--only", default="", help="只处理指定来源类型，逗号分隔")
    parser.add_argument("--no-kg", action="store_true", help="不索引知识图谱实体")
    parser.add_argument("--stats", action="store_true", help="只打印索引概览")
    args = parser.parse_args()

    if args.stats:
        print(json.dumps(rag_index.stats(), ensure_ascii=False, indent=1, default=str))
        return 0

    only = [item.strip() for item in args.only.split(",") if item.strip()] or None
    started = time.time()
    report = rag_index.build_all(force=args.force, only=only, include_kg=not args.no_kg)
    report["elapsed_sec"] = round(time.time() - started, 2)
    report["index_stats"] = rag_index.stats()
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
