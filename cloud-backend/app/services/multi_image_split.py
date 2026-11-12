"""多图逐张按视觉信号分类、按工作记录类型分组（B 方案）。

输入：本轮消息的 ``images``（URL 列表）+ ``image_signals``（与 ``images`` 等长，
逐图由视觉模型抽出的结构化行为判据，字段与 ``image_classification.ImageSignal`` 一致）。

职责边界（纯计算，不落库、不依赖 DB/ctx）：
- 逐张用 ``classify_image`` 判定 8 类影像类别；
- 映射到工作记录类型：X→巡视、P→旁站、Y/M→验收、W→见证、H→平行检验；
  其余（会议交底 C / 其他 O / 存疑）+ 无可用信号的图 → 待定；
- 同类多图标为一组（同组多图挂同一条记录的照片列表）；
- 产出「确认回执文案」与「分组方案」（供建记录时直接消费）。

无法确认（缺信号 / 命中待定口径）一律归「待定」，符合需求「无法确认的就归为待定记录」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.image_classification import (
    ClassificationResult,
    ImageSignal,
    classify_image,
)

# 8 类影像 → 工作记录类型
_CAT_TO_RECORD = {
    "X": "巡视",
    "P": "旁站",
    "Y": "验收",          # 举牌验收（工序/隐蔽/检验批）
    "M": "验收",          # 材料/构配件进场验收（见下方 classify_to_record_type 细分：无举牌时拆为见证）
    "W": "见证",          # 见证取样（封样编号）
    "H": "平行检验",
}
# 这两类归入「待定」（不是现场履职行为，无法落正式记录类型）
_CONFIRM_NEEDS = ("C", "O")

# 分组输出次序（待定永远排最后，回执更稳定）
_RECORD_ORDER = ["巡视", "旁站", "验收", "见证", "平行检验", "待定"]

# 记录类型校准（2026-11-12 真机：8 张照片被分成「巡视2 / 旁站5 / 验收1」）。
# 影像 8 类引擎的判据是「拍摄瞬间监理行为」，而工作记录类型看的是「这次留痕归哪张表」，
# 两者在两种水印写法上会分叉，这里只校准分组口径，不改影像归档类别。
_ACCEPTANCE_NEG = ("未验收", "待验收", "验收前", "准备验收", "尚未验收")
_SETTLEMENT_KW = ("收方", "计量", "工程量复核", "方量复核", "测量复核")


def _looks_like_acceptance_content(content: str) -> bool:
    """水印「内容」写了『××验收』但视觉没读到举牌牌面时，仍按验收记录留痕。"""
    return "验收" in content and not any(n in content for n in _ACCEPTANCE_NEG)


def _looks_like_settlement_content(content: str) -> bool:
    """『钢板桩收方』这类计量复核不是旁站清单里的打拔施工。"""
    return any(k in content for k in _SETTLEMENT_KW)


def _coerce_signal(raw: dict) -> dict:
    """过滤非法键 + 把字符串布尔规整为 bool（前端可能传 "true"/"false"）。"""
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if key not in ImageSignal.__dataclass_fields__:
            continue
        if isinstance(value, str) and value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        elif value in (None, ""):
            continue
        else:
            out[key] = value
    return out


def classify_to_record_type(raw_signal: dict) -> tuple[str, ClassificationResult]:
    """一张图 → (记录类型, 分类结果)。无法确认返回 (待定, result)。"""
    coerced = _coerce_signal(raw_signal)
    if not coerced:
        # 完全无可用视觉信号：无法判定类别，按「无法确认归待定」口径处理。
        return "待定", ClassificationResult("O", reason="无可用视觉信号，归待定", confidence=0.9)
    sig = ImageSignal(**coerced)
    try:
        res = classify_image(sig)
    except Exception:  # noqa: BLE001 - 规则引擎不应抛，但兜底防整批失败
        res = ClassificationResult("O", reason="分类异常，归待定", confidence=0.9)
    if res.needs_confirm or res.category_code in _CONFIRM_NEEDS or res.confidence < 1.0:
        return "待定", res

    content = str(coerced.get("content") or "").strip()
    # ① 无牌面的「底板/顶板钢筋模板验收」原先落到 X 巡视（施工中但不在旁站清单）；
    #    监理行为性质是验收，分组应归「验收」，否则验收照片被塞进巡视记录。
    if res.category_code == "X" and _looks_like_acceptance_content(content):
        return "验收", res
    # ② 「9m 钢板桩收方」被清单第 10 项的裸词「钢板桩」命中成旁站；
    #    收方是计量复核，不是打拔施工，按现场巡视留痕更贴切。
    if res.category_code == "P" and _looks_like_settlement_content(content):
        return "巡视", res

    # 材料验收（M）细分：举牌验收材料仍归「验收」；普通材料进场（无举牌）归「见证」，
    # 与系统别名「材料→见证」一致，使「材料单据」从「举牌验收」中单列出来。
    if res.category_code == "M":
        return "验收" if coerced.get("has_board") else "见证", res
    return _CAT_TO_RECORD.get(res.category_code, "待定"), res


@dataclass
class ImageGroup:
    """一个分组：同一记录类型下的若干张图。"""

    record_type: str
    images: list[str]
    # 逐图内容提示（来自信号 content / 水印），建记录时拼成 content 草稿
    hints: list[str]
    # 逐图水印元数据（部位 / 拍摄时间 / 项目 / 天气），建记录时回填到记录字段。
    # 与 images 等长对齐；分组确认可能跨轮，故需随分组方案序列化。
    wm: list = field(default_factory=list)


def group_images(images: list[str], signals: list[dict]) -> list[ImageGroup]:
    """逐图分类 → 按记录类型归并（保持 _RECORD_ORDER 次序）。

    - 有信号 → 按信号分类；
    - 无信号（空 dict / 越界）→ 视为「无法确认」→ 待定；
    - 同类多图标为同一组，保留出现次序。
    """
    buckets: dict[str, ImageGroup] = {}
    for idx, url in enumerate(images):
        u = str(url or "").strip()
        if not u:
            continue
        raw = signals[idx] if idx < len(signals) else None
        if not raw:
            rtype = "待定"
            hint = ""
        else:
            rtype, _res = classify_to_record_type(raw)
            hint = str((_safe_content(raw) or "")).strip()
        grp = buckets.get(rtype)
        if grp is None:
            grp = ImageGroup(record_type=rtype, images=[], hints=[], wm=[])
            buckets[rtype] = grp
        grp.images.append(u)
        if hint:
            grp.hints.append(hint)
        # 携带该图的水印元数据（部位 / 拍摄时间），供建单时回填，避免部位空、日期错成当天
        if raw:
            grp.wm.append({
                "section": str(raw.get("section") or "").strip(),
                "taken_at": str(raw.get("taken_at") or "").strip(),
                "project_name": str(raw.get("project_name") or "").strip(),
                "weather": str(raw.get("weather") or "").strip(),
            })
        else:
            grp.wm.append({})

    groups: list[ImageGroup] = []
    for rt in _RECORD_ORDER:
        if buckets.get(rt):
            groups.append(buckets.pop(rt))
    # 理论不会进这里，但兜底保留未知类型
    for grp in buckets.values():
        groups.append(grp)
    return groups


def _safe_content(raw: dict) -> str:
    c = raw.get("content")
    return str(c) if isinstance(c, str) else ""


def summarize(groups: list[ImageGroup]) -> str:
    """确认回执文案：『这批图我分了 巡视3 / 旁站3 / 待定3，确认后建。』"""
    parts = [f"{g.record_type}{len(g.images)}" for g in groups]
    if not parts:
        return "这批图我没分出可归类的记录，请告诉我每张图分别是什么类型。"
    return "这批图我分了 " + " / ".join(parts) + "，确认后建。"


# 肯定确认词（命中且无否定词即视为同意建记录）
_CONFIRM_TOKENS = (
    "确认", "建", "可以", "好的", "好", "行", "确定", "yes", "ok", "嗯", "对",
    "生成", "创建", "提交", "通过",
)
_NEG_TOKENS = ("不用", "不建", "不要", "取消", "否", "别", "暂不", "先不", "等会", "稍后")


def is_confirm(message: str) -> bool:
    """用户回复是否为『确认建』的肯定意图。"""
    t = (message or "").strip().lower()
    if not t:
        return False
    if any(n in t for n in _NEG_TOKENS):
        return False
    return any(c in t for c in _CONFIRM_TOKENS)
