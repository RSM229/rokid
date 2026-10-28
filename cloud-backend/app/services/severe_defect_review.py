"""危大工程专项施工方案审查服务。

依据《危险性较大的分部分项工程专项施工方案严重缺陷清单（试行）》
（建办质〔2024〕63 号）对专项施工方案文本做逐条对照审查。

流程：
1. 判断工程类型（用户指定优先，否则按关键词从方案文本推断）；
2. 取适用条款子集 = 通用 17 条 + 命中的专业类条款；
3. 用 LLM 逐条判定 pass / fail（命中即严重缺陷）；
4. 汇总成结构化审查报告（通过 / 不通过 + 缺陷清单 + 整改方向）。

判定口径：清单里任一「严重缺陷情形」成立 → 该条 fail → 方案审查不予通过。
常见空话套话（「加强安全管理」「由专人负责」「按规范执行」等）也让 LLM
从严判定为「未落实」。

作者: cloud-backend
创建日期: 2026-10-28
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services import model_gateway, severe_defect_service as sds

logger = logging.getLogger(__name__)

# 方案文本超长时截断的安全上限（字符）。工程方案动辄几万字，
# 逐条判定只需覆盖到条文对应内容，超长会拖垮 token 与耗时。
_MAX_PLAN_CHARS = 12000

_REVIEW_SYSTEM = (
    "你是危大工程专项施工方案审查专家，依据住建部《危险性较大的分部分项工程"
    "专项施工方案严重缺陷清单（试行）》（建办质〔2024〕63 号）逐条对照审查。"
    "只输出 JSON，不要输出任何其它文字。判定原则：清单任一「严重缺陷情形」在方案里"
    "成立（即方案没有落实这条所要求的内容）→ passed=false；方案已经写明对应内容 → passed=true。"
    "从严把握：出现「加强安全管理」「由专人负责」「按规范执行」「视情况而定」「拨打120」"
    "这类空话套话，一律视为未落实（passed=false）。"
)


def _truncate(text: str, limit: int = _MAX_PLAN_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n……（方案过长，已截断）"


def _build_review_prompt(plan_text: str, items: list[dict[str, Any]]) -> str:
    clauses = []
    for item in items:
        clauses.append(
            f"- [{item.get('id')}]（{item.get('category')} / 第{item.get('global_no')}条）"
            f"{item.get('clause')}"
        )
        interp = (item.get("interpretation") or "").strip()
        if interp:
            clauses.append(f"    对方要求：{interp}")
    clauses_text = "\n".join(clauses)
    return (
        "请对以下专项施工方案，逐条对照下方清单条款，判断每一条是否已在方案中落实。\n\n"
        "=== 专项施工方案内容 ===\n"
        f"{_truncate(plan_text)}\n\n"
        "=== 需逐条对照的严重缺陷清单 ===\n"
        f"{clauses_text}\n\n"
        "输出 JSON 对象，格式：\n"
        '{"work_type":"工程类型","items":[{"id":"条款id","passed":true/false,'
        '"evidence":"方案中支持通过判定的原文片段；fail 时留空",'
        '"problem":"fail 时，这条具体缺失什么、怎么补"}]}\n'
        "evidence/problem 可以留空字符串，但 items 必须覆盖上面列出的每一条 id，无遗漏。"
    )


async def _llm_review(prompt: str) -> dict | None:
    try:
        raw, _cfg = await model_gateway.chat_completion(
            [{"role": "system", "content": _REVIEW_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
    except model_gateway.ModelGatewayError:
        logger.warning("方案审查 LLM 调用失败")
        return None
    try:
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1] if start >= 0 and end > start else raw)
    except (ValueError, TypeError):
        logger.warning("方案审查 JSON 解析失败")
        return None
    return data if isinstance(data, dict) else None


async def review_plan(
    plan_text: str,
    *,
    work_type: str = "",
    category_keys: list[str] | None = None,
) -> dict[str, Any]:
    """审查专项施工方案，返回结构化报告。

    Args:
        plan_text: 方案正文（粘贴文本 / 已解析的文档内容）。
        work_type: 用户显式指定的工程类型（可选，用于报告展示）。
        category_keys: 显式专业类 key（可选；缺省按文本推断）。

    Returns:
        {
          ok, work_type, total_check, failed, passed,
          applicable_category_count, items:[{id, category, clause, passed,
          evidence, problem}], conclusion, summary
        }
    """
    text = (plan_text or "").strip()
    if not text:
        return {"ok": False, "error": "方案内容为空，无法审查。请粘贴方案文本或上传方案文件。"}

    if not category_keys:
        category_keys = sds.infer_category_keys(text)
    items = sds.applicable_items(text=text, category_keys=category_keys)

    # 推断不出专业类时，只按通用 17 条 + 全部专业类会给模型过大负担且不精准。
    # 这里仍返回结果，但明确提示专业类未识别，审查范围可能不全。
    inferred_cats = [c for c in category_keys] if category_keys else sds.infer_category_keys(text)
    if not inferred_cats and not category_keys:
        # 完全没推断出专业类：仍审通用条款，报告里提示
        items = sds.items_by_category("general")

    if not items:
        return {"ok": False, "error": "未能确定适用条款，请补充方案工程类型或内容。"}

    prompt = _build_review_prompt(text, items)
    result = await _llm_review(prompt)

    cat_names = {c["key"]: c["name"] for c in sds.categories()}
    id_map = {i["id"]: i for i in items}

    reviewed: list[dict[str, Any]] = []
    if result and isinstance(result.get("items"), list):
        for row in result["items"]:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("id") or "").strip()
            source = id_map.get(item_id)
            if not source:
                continue
            reviewed.append({
                "id": item_id,
                "category": source.get("category"),
                "category_key": source.get("category_key"),
                "clause": source.get("clause"),
                "interpretation": source.get("interpretation"),
                "passed": bool(row.get("passed", False)),
                "evidence": (row.get("evidence") or "").strip(),
                "problem": (row.get("problem") or "").strip(),
            })
    else:
        reviewed = [
            {
                "id": i["id"], "category": i["category"], "category_key": i["category_key"],
                "clause": i["clause"], "interpretation": i["interpretation"],
                "passed": None, "evidence": "", "problem": "",
            }
            for i in items
        ]

    failed = [r for r in reviewed if r.get("passed") is False]
    unmet = [r for r in reviewed if r.get("passed") is not True]  # fail 或未判定
    checked = len(reviewed)
    conclusion = "审查不予通过" if failed else "未发现严重缺陷"

    applicable_names = [cat_names.get(k, k) for k in (category_keys or inferred_cats or [])]

    summary_parts = [
        f"本次对照 {checked} 条适用条款（通用 + {('、'.join(applicable_names)) if applicable_names else '通用'}）进行审查。",
        f"发现严重缺陷 {len(failed)} 条。",
    ]
    if failed:
        summary_parts.append("依据建办质〔2024〕63 号，存在任一条严重缺陷即审查不予通过。")
    if (category_keys is None) and not inferred_cats:
        summary_parts.append("⚠️ 未能从方案文本识别出工程专业类别，本次仅审查通用 17 条，请补充工程类型以便核对专业条款。")

    return {
        "ok": True,
        "work_type": work_type or (result or {}).get("work_type") or "未明确",
        "total_check": checked,
        "failed": len(failed),
        "passed": len(reviewed) - len(failed),
        "unjudged": len(unmet) - len(failed),
        "applicable_categories": applicable_names,
        "items": reviewed,
        "conclusion": conclusion,
        "summary": "".join(summary_parts),
    }