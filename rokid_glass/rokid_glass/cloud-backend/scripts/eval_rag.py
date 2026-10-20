"""RAG 检索质量评测 —— 三路对照

三路对照（同一批问题、同一批判定标准）：
    keyword   升级前的子串匹配（knowledge_service._search_by_content_keyword）
    terms     升级前的词频打分（knowledge_base._retrieve_by_terms，按库问答原实现）
    rag       升级后的 BM25 + 向量 + RRF 混合检索

指标：Recall@5（前 5 条里是否出现期望文档）、MRR@5（首个命中的倒数排名均值）。

用法（容器内）：
    python scripts/eval_rag.py
    python scripts/eval_rag.py --k 5 --verbose
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ------------------------------------------------------------------ 评测集
# expect 里的字符串只要能命中「文档展示名」或「文件名」即算正确。
GOLDEN_SET = [
    # ---- 口语化 / 同义表达（子串匹配基本不可能命中，最能体现语义检索价值）----
    {"id": "q01", "q": "总监理工程师要中途换人，需要办什么手续",
     "expect": ["总监变更申请书", "总监任命书"]},
    {"id": "q02", "q": "项目印章要用，得谁授权",
     "expect": ["项目印章使用授权书"]},
    {"id": "q03", "q": "现场驻场的人员有变动，要发什么通知",
     "expect": ["驻场人员调整通知书", "驻场人员通知书"]},
    {"id": "q04", "q": "监理记录里，现场站着盯着关键工序施工的那种表格",
     "expect": ["旁站记录"]},
    {"id": "q05", "q": "同一道工序做两次检测对比的那种记录表",
     "expect": ["平行检查记录"]},
    {"id": "q06", "q": "每天去工地转一圈看到的情况，记在哪张表",
     "expect": ["巡视记录", "监理日志"]},
    {"id": "q07", "q": "发现质量问题要书面通知施工单位，用什么单据",
     "expect": ["监理通知单", "安全隐患整改通知"]},
    {"id": "q08", "q": "危险性较大的分部分项工程要检查哪些内容",
     "expect": ["危险性较大的分部分项工程"]},
    {"id": "q09", "q": "塔吊装拆的时候监理要检查哪些项目",
     "expect": ["起重机械安装拆卸"]},
    {"id": "q10", "q": "施工单位安全整改完了，要回一个什么文件",
     "expect": ["安全隐患整改通知回复", "复工申请"]},
    {"id": "q11", "q": "要叫工地停下来不干了，发哪个通知",
     "expect": ["暂时停止施工通知"]},
    {"id": "q12", "q": "工地存在哪些危险因素要提前梳理",
     "expect": ["危险源控制表"]},
    {"id": "q13", "q": "每星期要往上报的安全材料是什么",
     "expect": ["施工安全监理周报", "安全监理日志"]},
    {"id": "q14", "q": "工程正式开始施工前要下达什么指令",
     "expect": ["工程开工令"]},
    {"id": "q15", "q": "出了质量事故要往上报什么报告",
     "expect": ["质量事故监理报告", "安全监理重大情况报告"]},
    {"id": "q16", "q": "监理资料最后归档时怎么清点交接",
     "expect": ["监理档案移交目录"]},
    {"id": "q17", "q": "钢筋进场要登记在哪个台账里",
     "expect": ["材料台账"]},
    {"id": "q18", "q": "设计或施工方案改动后怎么记录",
     "expect": ["工程变更台账"]},
    {"id": "q19", "q": "监理跟施工单位日常沟通用哪种联系单",
     "expect": ["监理工作联系单", "安全监理工作联系单"]},
    {"id": "q20", "q": "监理人员现场履职应该留哪些记录",
     "expect": ["履职", "监理日志"]},
    {"id": "q21", "q": "在穗建通平台上应该怎么操作",
     "expect": ["穗建通"]},
    {"id": "q22", "q": "总监代表可以代表总监行使哪些职责",
     # 「总监理工程师代表授权书」是 KG 层从 5总代授权书.docx 抽出的同一张表单的
     # 正式全称（知识库里的展示名是文件梗名「5总代授权书」）。复核时 top1 命中的正是
     # 这个实体，属于标注缺口而非检索失败，故补全别名。
     "expect": ["总代授权书", "总监理工程师代表授权书", "监理规划"]},
    {"id": "q23", "q": "监理规划里一般包含什么内容",
     "expect": ["监理规划"]},
    {"id": "q24", "q": "每个月要出的监理汇报文件叫什么",
     "expect": ["监理月报"]},
    {"id": "q25", "q": "监理人员签名要留档备查吗",
     "expect": ["签名笔迹备查表", "驻场人员"]},
    # ---- 直白问法（子串匹配也能命中，用于确认升级没有回退）----
    {"id": "q26", "q": "旁站记录", "expect": ["旁站记录"]},
    {"id": "q27", "q": "监理日志", "expect": ["监理日志"]},
    {"id": "q28", "q": "复工申请", "expect": ["复工申请"]},
    {"id": "q29", "q": "会议纪要", "expect": ["会议纪要"]},
    {"id": "q30", "q": "材料台账", "expect": ["材料台账"]},
]


def norm(text: str) -> str:
    """去空格与标点，用于宽松匹配。"""
    return re.sub(r"[\s　（）()、，,。.；;：:·\-—_/]+", "", text or "")


def arms_of(names: list[str], expects: list[str]) -> bool:
    haystack = norm(" ".join(names))
    return any(norm(e) in haystack for e in expects)


# ------------------------------------------------------------------ 三路检索

def arm_keyword(question: str, docs) -> list[str]:
    from app.services import knowledge_service

    return [d.get("name", "") for d in knowledge_service._search_by_content_keyword(question)]


def arm_terms(question: str, docs) -> list[str]:
    from app.routers.knowledge_base import _retrieve_by_terms

    return [d.get("name", "") for d in _retrieve_by_terms(docs, question, top_k=5)]


def arm_rag(question: str, docs) -> list[str]:
    from app.services import knowledge_service

    hits = knowledge_service._search_by_content_rag(question)
    return [d.get("name", "") for d in (hits or [])]


ARMS = {"keyword": arm_keyword, "terms": arm_terms, "rag": arm_rag}


def load_kb_docs():
    """取默认知识库的文档对象（arms 需要）。"""
    from app.models.knowledge_base import KnowledgeDocument
    from app.services import rag_index
    from sqlalchemy import select

    session = rag_index.get_sync_session()
    try:
        return session.execute(select(KnowledgeDocument)).scalars().all()
    finally:
        session.close()


def evaluate(k: int, verbose: bool) -> int:
    from app.services import rag_index

    stats = rag_index.stats()
    print(json.dumps({"index": stats}, ensure_ascii=False, indent=1, default=str))
    if not stats.get("available"):
        print("索引不可用，无法评测")
        return 1

    docs = load_kb_docs()
    summary = {
        name: {"recall_hits": 0, "rr_sum": 0.0, "total": 0}
        for name in ARMS
    }
    rows = []

    for item in GOLDEN_SET:
        record = {"id": item["id"], "q": item["q"], "expect": item["expect"], "arms": {}}
        for name, fn in ARMS.items():
            try:
                names = fn(item["q"], docs)[:k]
            except Exception as exc:  # noqa: BLE001
                names = [f"__ERR__ {exc}"]
            hit_rank = 0
            for position, doc_name in enumerate(names, start=1):
                if any(norm(e) in norm(doc_name) for e in item["expect"]):
                    hit_rank = position
                    break
            summary[name]["total"] += 1
            if hit_rank:
                summary[name]["recall_hits"] += 1
                summary[name]["rr_sum"] += 1.0 / hit_rank
            record["arms"][name] = {"top": names, "hit_rank": hit_rank}
        rows.append(record)

    print("\n===== 逐题明细 =====")
    for record in rows:
        marks = " ".join(
            f"{name}:{'PASS@%d' % record['arms'][name]['hit_rank'] if record['arms'][name]['hit_rank'] else 'MISS'}"
            for name in ARMS
        )
        print(f"[{record['id']}] {record['q']}")
        print(f"        期望：{record['expect']}   →  {marks}")
        if verbose:
            for name in ARMS:
                print(f"        {name:8} {record['arms'][name]['top'][:5]}")

    print("\n===== 汇总 =====")
    print(f"{'实现':10} {'Recall@%d' % k:>12} {'MRR@%d' % k:>10}")
    result = {}
    for name, agg in summary.items():
        total = agg["total"] or 1
        recall = agg["recall_hits"] / total
        mrr = agg["rr_sum"] / total
        result[name] = {"recall": round(recall, 4), "mrr": round(mrr, 4), "hits": agg["recall_hits"], "total": total}
        print(f"{name:10} {recall:>11.1%} {mrr:>10.3f}")
    print("\nJSON:", json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索质量评测")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return evaluate(args.k, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
