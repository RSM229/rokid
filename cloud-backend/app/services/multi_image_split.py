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

import re

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
    """逐图分类 → 按（记录类型 + 施工部位）归并（类型内保持 _RECORD_ORDER 次序）。

    - 有信号 → 按信号分类；
    - 无信号（空 dict / 越界）→ 视为「无法确认」→ 待定；
    - 同类型**且同部位**才并成一组；同类型不同部位各成一组
      （2026-11-13 用户要求：4 条道路的照片不该塞进同一条记录）。
    - 部位取水印「单位工程 / 施工区域 / 部位 / 桩号」；取不到就按空部位归一组。
    """
    buckets: dict[tuple[str, str], ImageGroup] = {}
    order: list[tuple[str, str]] = []
    for idx, url in enumerate(images):
        u = str(url or "").strip()
        if not u:
            continue
        raw = signals[idx] if idx < len(signals) else None
        if not raw:
            rtype = "待定"
            hint = ""
            tail: dict[str, str] = {}
        else:
            # 内容里的水印尾巴（摄时间/天气/监理）会污染分类判据与工作内容：
            # 切出来单独留存，干净内容喂分类引擎，尾巴字段回填 wm（共存，不丢信息）。
            raw = dict(raw)
            hint, tail = split_hint(_safe_content(raw) or "")
            raw["content"] = hint
            # 尾巴里的时间/天气/人员/部位在没有其它来源时补回信号：
            # 内容干净了，但这些信息必须还在（共存，不丢）
            for key, value in tail.items():
                if not str(raw.get(key) or "").strip():
                    raw[key] = value
            rtype, _res = classify_to_record_type(raw)
        key = (rtype, _section_key(raw))
        grp = buckets.get(key)
        if grp is None:
            grp = ImageGroup(record_type=rtype, images=[], hints=[], wm=[])
            buckets[key] = grp
            order.append(key)
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
                # 人员（水印「监理」等）：记录表无独立栏，随 content_json 留档，不丢
                "person": str(raw.get("person") or raw.get("inspector") or "").strip(),
                # 水印模板的企业落款（调度电话/公司传真/地址）：从工作内容里切出来了，
                # 但必须原样带到 content_json，否则就是「改一样丢一样」
                "contact": str(raw.get("contact") or "").strip(),
                "location": str(raw.get("location") or "").strip(),
                "org": str(raw.get("org") or "").strip(),
            })
        else:
            grp.wm.append({})

    groups: list[ImageGroup] = []
    for rt in _RECORD_ORDER:
        for key in order:
            if key[0] == rt and buckets.get(key):
                groups.append(buckets.pop(key))
    # 理论不会进这里，但兜底保留未知类型
    for key in order:
        if buckets.get(key):
            groups.append(buckets.pop(key))
    return groups


def _section_key(raw: dict | None) -> str:
    """分组用的部位键：去掉空白与首尾标点，避免「沙盛四路 」与「沙盛四路」被拆成两组。"""
    if not raw:
        return ""
    text = str(raw.get("section") or "").strip()
    if not text:
        # 没填部位时退单位工程：水印只写了「单位工程：××路」的照片也应各自成组
        text = str(raw.get("unit") or "").strip()
    return re.sub(r"[\s，,、；;]+", "", text)


def _safe_content(raw: dict) -> str:
    c = raw.get("content")
    return str(c) if isinstance(c, str) else ""


# ---------------------------------------------------------------- 内容字段清洗
# 水印相机把「施工内容」和拍摄时间/天气/人员排在同一行、只用空格分隔，
# 而 OCR 原文拿不到换行，于是抽内容时会把后面几栏一起吞进来。真机样子：
#   『顶板钢筋模板验收 摄时间：2026.10.27 15:18 气：多云31°C 监理』
# 这串脏文本会被拼进记录的工作内容（2026-11-13 用户截图「这工作内容怎么这样啊」）。
# 做法：内容遇到「下一个字段名 + 冒号」就截断。
_HINT_TAIL_LONG = (
    "拍摄时间", "拍照时间", "记录时间", "摄时间", "拍摄日期", "拍照日期",
    "记录日期", "施工部位", "施工区域", "单位工程",
    "工程名称", "项目名称", "监理人员", "拍摄人员", "施工单位", "建设单位",
    "监理单位", "验收人员", "检查人员", "旁站人员",
    # 水印相机模板自带的企业落款（2026-11-13 真机：旁站内容被写成
    # 「基础混凝土浇筑 调度电话：0758-3371922 … 公司传真：0758-3371923 时间 间：…」）。
    # 这些不是施工内容，但也不能扔 —— 见 _TAIL_LABEL_KEY 的 contact / location / org。
    "调度电话", "公司传真", "联系电话", "电话号码", "公司电话", "办公电话",
    "公司地址", "项目地址", "办公地址", "公司名称", "单位名称",
    "纳税人识别号", "统一社会信用代码",
)
_HINT_TAIL_SHORT = (
    "时间", "日期", "地点", "位置", "地址", "部位", "桩号", "天气", "气温",
    "人员", "监理", "项目", "气",
    "电话", "手机", "短号", "传真", "邮箱", "网址", "邮编", "公司", "单位", "微信",
)


def _label_pattern(label: str) -> str:
    """标签 → 容忍 OCR 错位的正则。

    OCR 常把同一个字重复识别（真机：「时间 间：2026.10.2716:47」），也会在字间
    插空格。允许字间空白、并容忍末字多出现一次，否则整条水印尾巴一个字都切不掉。
    """
    chars = list(label)
    body = r"\s*".join(re.escape(c) for c in chars)
    if len(chars) > 1:
        body += r"(?:\s*%s)?" % re.escape(chars[-1])
    return body


def _label_alternation(labels) -> str:
    """按长度倒序拼 alternation，保证「公司传真」优先于「公司」。"""
    return "|".join(_label_pattern(w) for w in sorted(labels, key=len, reverse=True))


# 长标签（≥3 字）几乎不会误伤，允许紧贴前文；短标签要求前面有分隔符，
# 否则「混凝土养护气温控制」这类正文会被从「气」处切断。
_HINT_CUT_LONG = re.compile(r"(?:%s)\s*[:：]" % _label_alternation(_HINT_TAIL_LONG))
_HINT_CUT_SHORT = re.compile(
    r"(?:^|[\s；;，,、|（(])\s*(?:%s)\s*[:：]" % _label_alternation(_HINT_TAIL_SHORT))
_HINT_TRIM = " \t\r\n；;，,、。|:：）)】]」』”"

# 被切下来的尾巴不能扔：拍摄时间/天气/人员/部位要能落回记录对应字段，
# 否则「工作内容干净了，日期天气全丢了」——2026-11-13 用户要求「要共存」。
_TAIL_LABEL_KEY = {
    "拍摄时间": "taken_at", "拍照时间": "taken_at", "记录时间": "taken_at",
    "摄时间": "taken_at", "拍摄日期": "taken_at", "拍照日期": "taken_at",
    "记录日期": "taken_at", "时间": "taken_at", "日期": "taken_at",
    "天气": "weather", "气温": "weather", "气": "weather",
    "施工部位": "section", "施工区域": "section", "单位工程": "section",
    "部位": "section", "桩号": "section",
    "位置": "location", "地点": "location", "地址": "location",
    "公司地址": "location", "项目地址": "location", "办公地址": "location",
    "人员": "person", "监理人员": "person", "拍摄人员": "person",
    "验收人员": "person", "检查人员": "person", "旁站人员": "person",
    "监理": "person", "拍摄人": "person",
    "工程名称": "project_name", "项目名称": "project_name", "项目": "project_name",
    # 水印模板落款里的联系方式：不进工作内容，但要留档（共存，不丢）
    "调度电话": "contact", "联系电话": "contact", "电话号码": "contact",
    "公司电话": "contact", "办公电话": "contact", "电话": "contact",
    "手机": "contact", "手机号": "contact", "手机号码": "contact",
    "短号": "contact", "传真": "contact", "公司传真": "contact",
    "邮箱": "contact", "电子邮箱": "contact", "网址": "contact", "微信": "contact",
    "邮编": "postcode",
    "公司名称": "org", "单位名称": "org", "公司": "org", "单位": "org",
    "纳税人识别号": "org", "统一社会信用代码": "org",
}
_TAIL_TOKEN = re.compile(
    r"(?:%s)\s*[:：]" % _label_alternation(_TAIL_LABEL_KEY))
# 水印落款里还常有简称/错字标签（真机「燃利：四会市大沙镇…」其实是「地址：」的
# OCR 错字）。没收录的「短词+冒号」也当段落边界，避免前一个字段把后一个字段的值吃掉。
_GENERIC_SEG = re.compile(r"[\u4e00-\u9fa5]{1,8}\s*[:：]")
# 值里出现这些地名字样，说明它其实是一段地址（归属 location 而不是 contact）
_ADDR_HINT = re.compile(r"(?:省|市|区|县|镇|乡|村|路|街|巷|工业区|开发区|园区|大厦|广场)")
# 水印常把人员写成尾巴上的裸词（「… 多云31°C 监理」，不带冒号），
# 直接剥出来归到 person，别让它挂在天气值后面。
_PERSON_BARE = ("监理员", "监理工程师", "监理", "施工员", "安全员", "质检员", "项目经理", "甲方", "业主")
# 确认串里真的是水印尾巴（而不是正文里出现「时间：15:00」）的锚点：
# 出现「摄时间/拍摄时间/天气/气温」这类水印专有字段才算；水印模板的企业落款
# （调度电话/公司传真/短号）同属水印特征，一并算作锚点。
_TAIL_ANCHOR = re.compile(
    r"(?:摄时间|拍摄时间|拍照时间|记录时间|天气|气温|调度电话|公司传真|"
    r"联系电话|公司地址|传真|短号)\s*[:：]")


def _classify_tail_value(key: str, value: str) -> str:
    """值归位纠偏：联系方式里夹着地址时，别把整段地址当电话留档。"""
    if key in ("contact", "org", "postcode") and len(value) >= 8 and _ADDR_HINT.search(value):
        return "location"
    return key


def _normalize_tail_label(label: str) -> str:
    """把正则命中的标签还原成表里的键（去空白、折叠 OCR 重复末字）。"""
    s = re.sub(r"\s+", "", label or "")
    while len(s) > 2 and s[-1] == s[-2]:
        s = s[:-1]
    if s in _TAIL_LABEL_KEY:
        return s
    # OCR 多认一个字（「时间间」以外的情况）：按最长已知标签做前缀匹配
    for known in sorted(_TAIL_LABEL_KEY, key=len, reverse=True):
        if s.startswith(known):
            return known
    return ""


def _parse_tail_fields(tail: str) -> dict[str, str]:
    """把尾巴串按「标签：值」拆成结构化字段（取首个非空）。"""
    out: dict[str, str] = {}
    hits = list(_TAIL_TOKEN.finditer(tail))
    for i, m in enumerate(hits):
        key = _TAIL_LABEL_KEY.get(_normalize_tail_label(m.group(0)))
        if not key:
            continue
        end = hits[i + 1].start() if i + 1 < len(hits) else len(tail)
        raw_value = tail[m.end():end]
        # 值里若还有没收录的「短词：」，在那里断开：前半段是当前字段的值，
        # 后半段按内容归位（地址 → location，其余人/公司信息 → extra）。
        seg = _GENERIC_SEG.search(raw_value)
        if seg:
            rest = raw_value[seg.end():].strip(_HINT_TRIM)
            raw_value = raw_value[: seg.start()]
            unknown_key = _classify_tail_value("contact", rest) if rest else ""
            if rest and unknown_key and unknown_key not in out:
                out[unknown_key] = re.sub(r"\s+", " ", rest)
        value = re.sub(r"\s+", " ", raw_value).strip(_HINT_TRIM)
        # 尾部裸人员词（「… 晴27°C 监理」）：剥出来记人员，别留在天气/时间值里
        for person in _PERSON_BARE:
            if value.endswith(person):
                value = value[: -len(person)].strip(_HINT_TRIM)
                out.setdefault("person", person)
                break
        if value:
            key = _classify_tail_value(key, value)
            out.setdefault(key, value)
    return out


def split_hint(text: str) -> tuple[str, dict[str, str]]:
    """水印「内容」→（干净的施工内容, 尾巴里解析出的结构化字段）。

    例：``顶板钢筋模板验收 摄时间：2026.10.27 15:18 气：多云31°C 监理``
    → ``("顶板钢筋模板验收", {"taken_at": "2026.10.27 15:18", "weather": "多云31°C", "person": "监理"})``
    """
    s = str(text or "").strip()
    if not s:
        return "", {}
    cut = len(s)
    for pattern in (_HINT_CUT_LONG, _HINT_CUT_SHORT):
        m = pattern.search(s)
        if m and m.start() < cut:
            cut = m.start()
    if cut == 0:
        # 整条就是水印字段串（「拍摄时间：2026-10-27」），没有真正的施工内容
        return "", _parse_tail_fields(s)
    if cut < len(s):
        return s[:cut].strip(_HINT_TRIM), _parse_tail_fields(s[cut:])
    return s.strip(_HINT_TRIM), {}


def clean_hint(text: str) -> str:
    """把水印「内容」尾巴上的字段串切掉，只留真正的施工内容（见 split_hint）。"""
    return split_hint(text)[0]


def looks_like_watermark_tail(text: str) -> bool:
    """正文里是否真的混进了水印尾巴（用于单图建记录时决定要不要清洗）。"""
    return bool(_TAIL_ANCHOR.search(str(text or "")))


def clean_hints(hints) -> list[str]:
    """清洗 + 去重保序；清洗后为空的提示直接丢掉（否则「时间：…」会被当成工作内容）。"""
    out: list[str] = []
    seen: set[str] = set()
    for hint in hints or []:
        cleaned = clean_hint(hint)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def summarize(groups: list[ImageGroup]) -> str:
    """确认回执文案：『这批图我分了 巡视3 / 旁站3 / 待定3，确认后建。』

    🔴 2026-11-16 用户口径「识别的内容只保留到思考里，不需要写出来」：
    水印部位等识别值只用于分组（同类型不同部位仍会拆成多组），不写进回执；
    这里按类型合并计数，用户看到的是分类结果与张数。
    """
    counts: dict[str, int] = {}
    for g in groups or []:
        rtype = str(getattr(g, "record_type", "") or "待定").strip() or "待定"
        counts[rtype] = counts.get(rtype, 0) + len(getattr(g, "images", None) or [])
    if not counts:
        return "这批图我没分出可归类的记录，请告诉我每张图分别是什么类型。"
    parts = [f"{rtype}{n}" for rtype, n in counts.items()]
    return "这批图我分了 " + " / ".join(parts) + "，确认后建。"


def _group_section(group: ImageGroup) -> str:
    for wm in group.wm or []:
        if isinstance(wm, dict):
            section = str(wm.get("section") or "").strip()
            if section:
                return section
    return ""


def _short_section(section: str, limit: int = 12) -> str:
    section = str(section or "").strip()
    return section if len(section) <= limit else section[:limit] + "…"


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
