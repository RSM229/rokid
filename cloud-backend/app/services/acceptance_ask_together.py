"""验收记录 ·「一起询问」模式实现骨架（pull_points + aggregate_reply）

设计依据：知识库《验收记录·一起询问接口说明（开发）.md》

- 数据源：app.services.acceptance_checklists.ITEMS（与知识库 JSON 同源，320 条；
  房建 181 / 市政 139）。这里直接基于 ITEMS 建内存索引，保证单一数据源。
- LLM：app.services.model_gateway.chat_completion（统一网关，response_format=json_object）。
- 流程：① 信息确认（文字/水印）→ ② pull_points 带出该工序全部要点 →
  ③ assemble_ask_block 一起询问 → ④ 用户整体回复 →
  ⑤ aggregate_reply 按全部回复逐条归集 → ⑥ generate_conclusion 生成结论。

接入状态（2026-11-05）：已由 agent_tools._create_work_record 的验收分支接入
（find_entry / assemble_ask_block / aggregate_reply / generate_conclusion）；routers/agent.py
在 result 带 ask_block 时强制展示完整询问块。confirm_info 仍是预留占位
（抛 NotImplementedError），生产链路不调用它。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.services import acceptance_checklists as _ac
from app.services import model_gateway

logger = logging.getLogger(__name__)

# 验收类型枚举（与小程序交互层级图四级一致）
ACCEPTANCE_TYPES = ("材料进场验收", "隐蔽验收", "质量验收", "危大验收")
# 逐项结论枚举
CONCLUSION_VALUES = ("符合", "不符合", "不适用")


# ---------------------------------------------------------------------------
# 数据源索引（模块加载时构建）
# ---------------------------------------------------------------------------
_BY_DIVISION: dict[str, list[int]] = {}   # 分部工程(市政=单位工程/分部) -> [全局下标]
_INDEX: list[dict] = []                    # 与 ITEMS 平行


def _split_points(text: str) -> list[str]:
    """把监理验收要点全文拆成 ① ② ③ 分条。"""
    if not text:
        return []
    segs = re.split(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])", text.strip())
    return [s.strip().rstrip("；;。. ") for s in segs if s.strip()]


def _to_inquiry(seg: str) -> str:
    """陈述式 -> 询问式（与知识库 JSON / 模版「请确认：」格式一致）。"""
    s = seg.strip().rstrip("；;。. ")
    return f"请确认：{s}　□符合　□不符合　□不适用（说明：____）"


def _build_index() -> None:
    for i, (kind, seq, unit, division, keyproc, points) in enumerate(_ac.ITEMS):
        div_key = division if kind == "房建" else (unit + " / " + division)
        _INDEX.append(
            {
                "kind": kind,
                "序号": seq,
                "单位工程": unit,
                "分部工程": division,
                "关键工序_关键部位": keyproc,
                "验收要点_原文": _split_points(points),
                "验收要点_询问式": [_to_inquiry(p) for p in _split_points(points)],
            }
        )
        _BY_DIVISION.setdefault(div_key, []).append(i)


_build_index()


# ---------------------------------------------------------------------------
# ① 信息确认（预留接口，接文字 / 图片水印 OCR）
# ---------------------------------------------------------------------------
async def confirm_info(user_text: str, ocr_text: str = "") -> dict:
    """由用户文字 / 图片水印 OCR 文本识别确认 验收类型 / 分部工程 / 关键工序。

    返回：{"验收类型": str, "分部工程": str, "关键工序_关键部位": str}

    实现建议（不在此耦合）：
    - 验收类型、分部工程、关键工序可从既有 agent_tools._infer_acceptance_type
      及水印/OCR 解析结果取；验收类型必须在四个枚举内，不取索引的「建议验收类型」。
    - 也可走一次 LLM（温度 0）从 user_text+ocr_text 抽取上述三字段。
    本函数为占位，集成时替换实现。
    """
    raise NotImplementedError(
        "confirm_info 待接入 agent_tools._infer_acceptance_type 或 LLM 识别；"
        "输入 user_text / ocr_text，输出 {验收类型, 分部工程, 关键工序_关键部位}。"
    )


# ---------------------------------------------------------------------------
# ② 带出要点（pull_points）
# ---------------------------------------------------------------------------
def find_entry(division: str, keyproc: str) -> Optional[dict]:
    """按 分部工程 + 关键工序_关键部位 精确/模糊匹配一条 entry。"""
    # 1) 同分部下关键工序包含/被包含
    for idx in _BY_DIVISION.get(division, []):
        e = _INDEX[idx]
        if keyproc and (keyproc in e["关键工序_关键部位"] or e["关键工序_关键部位"] in keyproc):
            return e
    # 2) 退而求其次：跨分部按关键工序匹配
    for e in _INDEX:
        if keyproc and (keyproc in e["关键工序_关键部位"] or e["关键工序_关键部位"] in keyproc):
            return e
    return None


def pull_points(division: str, keyproc: str) -> dict:
    """带出某工序的全部验收要点（一起询问用）。

    返回：{"found": bool, "entry": dict|None, "询问式": [str], "原文": [str]}
    「询问式」已是按 ① ② ③ 拆分好的可展示列表，直接交给 assemble_ask_block。
    """
    e = find_entry(division, keyproc)
    if not e:
        return {"found": False, "entry": None, "询问式": [], "原文": []}
    return {
        "found": True,
        "entry": e,
        "询问式": e["验收要点_询问式"],
        "原文": e["验收要点_原文"],
    }


# ---------------------------------------------------------------------------
# ③ 一起询问块拼装
# ---------------------------------------------------------------------------
def assemble_ask_block(entry: dict) -> str:
    """把该工序全部 验收要点_询问式 拼成一段（一起带出，不渲染逐项复选框）。"""
    n = len(entry["验收要点_询问式"])
    lines = [
        f"【验收内容】（系统按「{entry['分部工程']} — {entry['关键工序_关键部位']}」"
        f"自动带出，共 {n} 项，请一并答复）",
        "",
    ]
    lines.extend(entry["验收要点_询问式"])
    lines.append("")
    lines.append("请就上述全部要点一并回复（可逐条逐项，也可整体描述；不符合/不适用须写明原因）：")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ⑤ LLM 按全部回复逐条归集（aggregate_reply）
# ---------------------------------------------------------------------------
def _validate_aggregated(arr, n_expected: int) -> Optional[list[dict]]:
    """校验 LLM 返回的逐条结论：长度 == 询问项数、conclusion 在枚举内、item_index 为整数。"""
    if not isinstance(arr, list) or len(arr) != n_expected:
        return None
    out = []
    for x in arr:
        if not isinstance(x, dict):
            return None
        c = x.get("conclusion")
        if c not in CONCLUSION_VALUES:
            return None
        idx = x.get("item_index")
        if not isinstance(idx, int):
            return None
        out.append({"item_index": idx, "conclusion": c, "note": (x.get("note") or "")})
    return out


async def aggregate_reply(
    items: list[str],
    user_reply: str,
    *,
    max_retries: int = 2,
) -> list[dict]:
    """输入：items（询问式列表，已标 ① ② ③…）+ 用户整体回复 user_reply。
    输出：[{item_index, conclusion, note}]，长度 == len(items)。

    conclusion ∈ {"符合", "不符合", "不适用"}。
    """
    system_prompt = (
        "你是监理验收助手。下面是一并带出的若干验收询问项（已标序号 ① ② ③…），"
        "以及监理人员就全部要点给出的整体回复。请逐条判定每条要点的结论。\n"
        '只返回 JSON 数组，每个元素：{"item_index": <序号整数>, '
        '"conclusion": "符合|不符合|不适用", "note": "<原因/说明，无则空字符串>"}。'
        "必须覆盖全部询问项、不要遗漏；conclusion 只能在三个枚举内。"
    )
    user_prompt = (
        "【询问项】\n" + "\n".join(items) + f"\n\n【监理人员整体回复】\n{user_reply}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            content, _ = await model_gateway.chat_completion(
                messages,
                temperature=0.2,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            data = json.loads(content)
            arr = data if isinstance(data, list) else (data.get("items") or data.get("result") or [])
            parsed = _validate_aggregated(arr, len(items))
            if parsed is not None:
                return parsed
            logger.warning("aggregate_reply 第 %s 次：结构校验未通过，重试", attempt + 1)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("aggregate_reply 第 %s 次调用失败: %s", attempt + 1, exc)
    raise RuntimeError(f"按全部回复归集验收结论失败：{last_err}")


# ---------------------------------------------------------------------------
# ⑥ 结论生成
# ---------------------------------------------------------------------------
def generate_conclusion(aggregated: list[dict]) -> str:
    """据逐条归集结果生成验收结论（与模版示例一致）。"""
    if any(a["conclusion"] == "不符合" for a in aggregated):
        bad = [
            f"第{a['item_index']}项{a['note']}".rstrip("：")
            for a in aggregated
            if a["conclusion"] == "不符合" and a["note"]
        ]
        base = "验收不合格，签发整改通知并限期复验。"
        return base + (f"（具体问题：{'；'.join(bad)}）" if bad else "")
    if all(a["conclusion"] == "不适用" for a in aggregated):
        return "本工序相关验收项经确认均不适用，无需验收。"
    n_na = sum(1 for a in aggregated if a["conclusion"] == "不适用")
    if n_na:
        return f"验收合格（{n_na} 项经确认不适用）。同意进入下道工序。"
    return "验收合格，同意进入下道工序。"


# ---------------------------------------------------------------------------
# 编排示例（开发自测用，不进生产链路）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 取「地基基底验收」演示纯函数链路（不含 LLM）
    e = find_entry("地基与基础 · 地基处理", "地基基底验收")
    assert e is not None, "未找到示例工序"
    block = assemble_ask_block(e)
    print(block)
    # 模拟用户整体回复后的归集结果（真实场景由 aggregate_reply 调 LLM 产出）
    fake = [
        {"item_index": 1, "conclusion": "符合", "note": ""},
        {"item_index": 2, "conclusion": "符合", "note": ""},
        {"item_index": 3, "conclusion": "符合", "note": ""},
        {"item_index": 4, "conclusion": "符合", "note": ""},
    ]
    print("\n→ 验收结论：", generate_conclusion(fake))
