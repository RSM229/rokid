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
    """陈述式 -> 嵌「是否」的是非问句。

    2026-11-06 用户明确「我不要这些选择，我要完全交互」：不再渲染复选框/填空控件，
    改成自然语言问句；2026-11-10 进一步要求：把「是否」嵌进句子、不要在句尾追问
    「这项符合吗」——要点原文多为「核查/检查…符合设计/合格/一致」的核查动作，
    句尾硬拼「这项符合吗」既啰嗦又别扭。这里按谓语锚点（符合/一致/合格/齐全…）
    把「是否」插到合适位置，①②③ 序号仍保留，aggregate_reply 靠序号逐条归集。
    """
    s = seg.strip().rstrip("；;。. ")
    if not s:
        return s
    # 已是问句：原样保留（补问号）
    if s.endswith(("吗", "呢", "？", "?")):
        return s if s.endswith(("？", "?")) else s + "？"
    # 拆出 ①②③… 序号，对正文做变换
    m = re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩]\s*", s)
    prefix = s[: m.end()] if m else ""
    body = s[m.end():] if m else s
    # 去掉句首祈使动词（核查/检查…），让主语成为话题，问句更自然
    for v in ("核查", "检查", "复核", "确认", "核对", "查验", "审查",
              "检验", "检测", "见证", "旁站", "量测", "测定"):
        if body.startswith(v):
            body = body[len(v):].lstrip("：: ")
            break
    new_body: str | None = None
    # 1) 与…一致 / 与…相同 / 与…相符
    mm = re.search(r"与[^，。；]{1,24}?(一致|相同|相符)", body)
    if mm:
        new_body = body[: mm.start()] + "是否" + body[mm.start():]
    else:
        # 2) 硬性谓语锚点：直接插入「是否」
        _HARD = ("符合", "满足", "合格", "无渗漏", "无裂缝", "无积水",
                 "无塌陷", "无松动", "无变形", "无缺陷")
        for p in _HARD:
            idx = body.find(p)
            if idx != -1:
                new_body = body[:idx] + "是否" + body[idx:]
                break
    if new_body is None:
        # 3) 状态形容词：可作名词（后接 性/度/率），需排除该情况再插「是否」
        _SOFT = ("齐全", "完整", "准确", "到位", "有效", "通畅", "稳定",
                 "牢固", "平顺", "均匀", "正确", "正常", "达标")
        for p in _SOFT:
            m2 = re.search(re.escape(p) + r"(?!性|度|率|量|数)", body)
            if m2:
                new_body = body[: m2.start()] + "是否" + body[m2.start():]
                break
    if new_body is None:
        # 4) 无明显谓语：补「是否符合要求」
        new_body = body + "是否符合要求"
    return prefix + new_body + "？"


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


# 验收类型 → 工序名筛选关键词（无法命中时返回空，由调用方只做文字引导）
_TYPE_KEYWORDS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("隐蔽",), ("隐蔽",)),
    (("进场", "材料"), ("进场", "材料", "设备")),
    (("危大",), ("危大",)),
]


def suggest_procedures(acc_type: str = "", prefer_kind: str = "",
                       limit: int = 6) -> list[str]:
    """按验收类型推荐候选关键工序（用于用户没说工序时给 chips 引导）。

    返回格式与验收要点候选一致：'[房建]钢筋笼隐蔽前验收（地基与基础 · 桩基础）'。
    规则：
    - 类型能映射到关键词（隐蔽/进场材料/危大）→ 取工序名命中项；
      若用户项目类别内没有命中项（如市政工序名不叫「隐蔽」），改推该类别
      常见工序兜底（要点表顺序），并排在命中项之前。
    - 质量类（检验批/分项/分部等）覆盖面太大，返回空列表只做文字引导。
    - limit 默认 6（2026-11-05 用户真机反馈「要交互不要这么多选择」——
      12 个 chips 刷满一屏像表格；少而精，其它工序用户会自己说）。
    """
    kws: tuple[str, ...] | None = None
    for type_keys, proc_keys in _TYPE_KEYWORDS:
        if any(k in (acc_type or "") for k in type_keys):
            kws = proc_keys
            break
    if kws is None:
        if (acc_type or "").strip():
            return []      # 质量类（检验批/分项/分部等）覆盖面太大 → 只做文字引导
        # 类型未知：给全类型混合候选（隐蔽+进场+危大），用户点工序即可跳过验收类型步
        kws = tuple(dict.fromkeys(k for _, pks in _TYPE_KEYWORDS for k in pks))
    matched = [e for e in _INDEX if any(k in e["关键工序_关键部位"] for k in kws)]
    if prefer_kind:
        own_matched = [e for e in matched if e["kind"] == prefer_kind]
        own_all = [e for e in _INDEX if e["kind"] == prefer_kind]
        other_matched = [e for e in matched if e["kind"] != prefer_kind]
        if not (acc_type or "").strip():
            # 类型未知：本类别常见工序按要点表顺序打头，再补其它类别的关键词命中
            pool = own_all + other_matched
        else:
            # 类型已知但本类别无按名命中（如市政工序名不叫「隐蔽」）→ 该类别全部工序兜底
            pool = (own_matched if own_matched else own_all) + other_matched
    else:
        pool = matched
    picked: list[dict] = []
    seen: set[str] = set()
    for e in pool:
        name = e["关键工序_关键部位"]
        if name in seen:
            continue
        seen.add(name)
        picked.append(e)
        if len(picked) >= limit:
            break
    return [
        f"{'[房建]' if e['kind'] == '房建' else '[市政]'}{e['关键工序_关键部位']}"
        f"（{e['分部工程']}）"
        for e in picked
    ]


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
# ②-b 按现场识别内容自动带出验收要点（分组建档后的「请确认是否符合」用）
# ---------------------------------------------------------------------------
# 施工内容里出现这些词就去找对应的关键工序；只做确定性匹配，不调模型、不臆造要点。
_POINT_KEYWORDS = (
    "钢筋", "模板", "混凝土", "浇筑", "防水", "桩", "管道", "回填", "砌体",
    "脚手架", "基坑", "钢结构", "预应力", "门窗", "幕墙", "电气", "给排水",
    "沥青", "基层", "压实", "吊装", "支护", "降水", "抹灰", "保温", "试块",
    "坍落度", "地基", "路基", "路面", "顶管", "沉井", "照明", "沟槽",
)
# 同名工序散落在多个分部时，优先主体结构 / 道排这类与现场照片更贴近的分部，
# 避免「底板钢筋模板验收」被匹配到桩基础的「钢筋笼隐蔽前验收」。
_POINT_PREFER_DIVISION = ("主体结构", "道路", "给水", "排水", "混凝土")


def match_points_by_text(text: str, limit: int = 3) -> tuple[str, list[str]]:
    """按现场识别内容匹配最相关的关键工序，带出该工序的规范验收要点。

    返回 ``(工序名, 要点列表)``；匹配不到返回 ``("", [])``。
    底层仍是 acceptance_checklists.ITEMS 单一数据源（经 _INDEX 拆分好要点）。
    """
    t = str(text or "")
    kws = [k for k in _POINT_KEYWORDS if k in t]
    if not kws:
        return "", []
    best: Optional[dict] = None
    best_score = 0
    for e in _INDEX:
        name = e["关键工序_关键部位"]
        hits = sum(1 for k in kws if k in name)
        if not hits:
            continue
        score = hits * 10 + (3 if any(
            p in str(e["分部工程"]) for p in _POINT_PREFER_DIVISION) else 0)
        if score > best_score:
            best, best_score = e, score
    if not best:
        return "", []
    return (
        str(best["关键工序_关键部位"]),
        [str(p) for p in best["验收要点_原文"][:max(1, limit)]],
    )


# ---------------------------------------------------------------------------
# ③ 一起询问块拼装
# ---------------------------------------------------------------------------
def assemble_ask_block(entry: dict) -> str:
    """把该工序全部 验收要点_询问式 拼成一段纯文字（一起带出，不含任何选择控件）。

    2026-11-06：按用户「完全交互」要求，标题/收尾全部改自然口吻，
    不再出现「共 N 项，请一并答复」「可逐条逐项」这类问卷式措辞。
    """
    lines = [
        f"这次做的是「{entry['分部工程']} — {entry['关键工序_关键部位']}」，"
        "我按要点跟你过一下：",
        "",
    ]
    lines.extend(entry["验收要点_询问式"])
    lines.append("")
    lines.append("你照实际情况用一句话说给我就行（哪一项不符合或不适用，顺带说下原因），我来逐条整理成验收内容和结论。")
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
    """据逐条归集结果生成监理验收结论（与省统表 YS 栏位⑥示例一致）。"""
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


def generate_self_check_conclusion(aggregated: list[dict]) -> str:
    """据逐条归集结果生成施工单位自检结论（省统表 YS 栏位⑥-自检）。

    语义：施工单位对自己做的工程的自检确认。若监理验收发现不符合项，
    则自检结论需如实反映（不能一律写"合格"），供施工单位签字确认。
    """
    if any(a["conclusion"] == "不符合" for a in aggregated):
        bad_count = sum(1 for a in aggregated if a["conclusion"] == "不符合")
        return f"施工单位自检情况待确认（监理验收发现 {bad_count} 项不符合，需整改后复验）"
    if all(a["conclusion"] == "不适用" for a in aggregated):
        return "本工序相关验收项均不适用，无需自检"
    return "施工单位自检合格"


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
