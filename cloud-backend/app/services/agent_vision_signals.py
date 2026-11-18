"""从聊天素材 ``material`` 重建逐图结构化视觉信号（``image_signals``）。

前端已经对每张图跑了视觉（水印 OCR + 画面观察），并把结果拼进 ``material`` 的
「【图片 N】」逐图块里；但它从没填 ``image_signals`` 这个本该承载视觉信号的字段，
导致 ``multi_image_split`` 的分流能力一直沉睡。本模块在后端把这个缺失的环节补上：
解析每图的「水印字段 + 画面观察」文字，重建成 ``ImageSignal`` 风格字典，供
``group_images`` 使用。

纯文本解析，不下载图片、不跑额外模型，零额外延迟；失败的单图返回空字典（→ 待定）。

与「后端重新下载图片字节再跑视觉」相比，本方案直接用前端已经产出的真实观察结果，
更准确（观察是前端按监理场景定制的）、合规（不新增图片出网面）、更快。
"""
from __future__ import annotations

import re
from typing import Optional

from app.services.image_classification import (
    ImageSignal,
    _hazard_hit,
)
# 尾巴解析：切掉水印尾巴「摄时间/天气/监理」并把它们解析成字段回填（内容侧同时清洗）
from app.services.multi_image_split import split_hint

# 逐图块切分：material 形如「【图片 1】\n【水印】…\n【图片内容观察】…\n\n【图片 2】…」
_IMG_HEADER = re.compile(r"【图片\s*(\d+)\s*】")
_WM_FIELD = re.compile(r"【水印】([\s\S]*?)(?:\n|【水印识别原文】|【图片内容观察】|$)")
_WM_RAW = re.compile(r"【水印识别原文】([\s\S]*?)(?:\n|【图片内容观察】|$)")
_OBS = re.compile(r"【图片内容观察】([\s\S]*?)(?:\n【图片|$)")

# 行为判据关键词（在水印原文 + 画面观察里命中即置位）
_BEHAVIOR_FLAGS: list[tuple[tuple[str, ...], str]] = [
    (("举牌", "验收牌", "公示牌", "牌面", "持牌", "验收公示", "举牌验收"), "has_board"),
    (("封样", "试样编号", "封条", "试样", "送检", "封签"), "has_seal"),
    (("尺量", "量测", "实测", "读数", "资料核查", "铭牌", "合格证", "报验", "检测"), "has_measuring"),
    (("进场", "卸车", "堆放", "开箱", "材料验收", "原材", "钢筋原材", "待检", "管材堆放", "构配件"), "material_incoming"),
    (("会议", "交底", "培训", "会审", "签到"), "is_meeting"),
    (("整改前", "整改后", "复查"), "is_rectification"),
    (("进度形象", "固定机位", "全景", "整体效果", "航拍", "形象进度"), "progress_shot"),
]
_IN_PROGRESS_VERBS = (
    "浇筑", "回填", "夯实", "碾压", "摊铺", "安装", "绑扎", "砌筑", "顶进", "调试",
    "打拔", "成桩", "开挖", "转运", "施工", "进行中", "作业中", "正在", "焊接",
    "张拉", "注浆", "旋喷",
)
_NOT_IN_PROGRESS = ("已完成", "未施工", "仅堆放", "成品", "未开始", "停止施工", "尚未")

# 否定前缀：出现这些词时，紧跟的判据词应视为否定（如「未见举牌」「无封样」「未施工」）。
_NEG_PREFIX = ("未", "无", "没", "不", "没有", "未见", "未举", "未持", "未封")

_CONTENT_KW = re.compile(r"(?:内容|工序|对象|施工内容|检验批|隐蔽)[:：]\s*([^\n；;，,]{2,240})")
_SECTION_KW = re.compile(r"(?:单位工程|施工区域|部位|桩号|施工部位)[:：]\s*([^\n；;，,]{1,40})")
_TAKEN_AT_KW = re.compile(r"(?:拍摄时间|拍照时间|记录时间|时间|日期)[:：]\s*([^\n；;，,]{1,30})")


def _block_parts(block: str) -> tuple[str, str, str]:
    wm = _WM_FIELD.search(block)
    raw = _WM_RAW.search(block)
    obs = _OBS.search(block)
    return (
        wm.group(1) if wm else "",
        raw.group(1) if raw else "",
        obs.group(1) if obs else "",
    )


def _extract_content(block: str, raw_text: str) -> str:
    """从水印原文里抽「内容/工序/对象」作为归类 content 线索（含尾巴，未做身份剔除）。

    尾巴（拍摄时间/天气/人员）由 ``parse_material_signals`` 用 ``split_hint`` 切出来
    另存到信号字段 —— 不能在这一步直接丢掉（2026-11-13 用户要求「要共存」）。
    这里**不做任何清洗**：清洗会先删掉天气/拍摄时间/裸人员词，``split_hint``
    之后就再也切不出来（2026-11-18 真机 #200 天气未落库）。内容侧的清洗由
    ``split_hint`` 自己完成。
    """
    m = _CONTENT_KW.search(block)
    if m:
        return m.group(1).strip()
    # 退而求其次：用整段水印原文（截断）喂给规则引擎做子串匹配。
    # 🔴 上限必须留够一张图的整行水印（真机 2026-11-13：模板落款「调度电话…公司传真…
    # 时间 间：2026.10.2716:47」有 101 字，原来截到 80 字正好把拍摄时间砍掉，
    # 记录日期就默认成了建单当天）。
    if raw_text:
        return raw_text.strip()[:240]
    return ""


def _extract_section(wm_text: str) -> str:
    m = _SECTION_KW.search(wm_text)
    return m.group(1).strip() if m else ""


def _extract_taken_at(wm_text: str) -> str:
    """从水印文本抽拍摄时间（分组记录据此回填业务日期，避免默认成建单当天）。"""
    m = _TAKEN_AT_KW.search(wm_text)
    return m.group(1).strip() if m else ""


# 天气：水印结构化行「天气：晴 28℃」；也有 OCR 只写「气：晴 28℃」的模板。
_WEATHER_KW = re.compile(r"(?:天气|气温|气)\s*[:：]\s*([^\n；;，,]{1,20})")
# 「晴 28℃ / 多云31°C」：天气词+气温整体，不依赖标签也能认出来。
_WEATHER_TEMP = re.compile(
    r"(?:晴|多云|阴|阵雨|雷阵雨|小雨|中雨|大雨|暴雨|大暴雨|特大暴雨|冻雨|"
    r"雨夹雪|小雪|中雪|大雪|雾|霾|沙尘暴|浮尘)"
    r"\s*[-+]?\d{1,2}\s*[℃°]\s*C?"
)


def _extract_weather(*texts: str) -> str:
    """从水印结构化行/识别原文抽天气。

    天气必须单独抽一次：``_extract_content`` 命中「施工内容：」时只返回内容值，
    尾巴里没有天气；走原文兜底时天气又会被 ``scrub_watermark_noise`` 当噪声删掉。
    两条路径都会让详情页「天气」永远为空（2026-11-18 真机 #200）。
    """
    for text in texts:
        if not text:
            continue
        m = _WEATHER_TEMP.search(text)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()[:16]
        m = _WEATHER_KW.search(text)
        if m:
            value = m.group(1).strip().split(" ")[0]
            if value:
                return value[:16]
    return ""


# 人员：结构化水印行「人员：张三」，不能只从尾巴取 —— 尾巴里的真实姓名
# 不在裸词表里会被当正文，切尾后要么留在内容里、要么被清洗删掉。
_PERSON_KW = re.compile(
    r"(?:^|[；;，,、|\s])\s*"
    r"(?:旁站人员|验收人员|检查人员|监理人员|拍摄人员|记录人员|拍摄人|记录人|人员|监理)"
    r"\s*[:：]\s*([^\n；;，,]{1,12})"
)


def _extract_person(wm_text: str) -> str:
    """从结构化水印行抽人员（姓名或岗位）。"""
    m = _PERSON_KW.search(str(wm_text or ""))
    if not m:
        return ""
    return re.split(r"\s+", m.group(1).strip())[0][:12]


def _negated_before(text: str, pos: int) -> bool:
    """判断 text[pos] 处的命中是否被其左侧 3 字内的否定前缀否定（如『未见举牌』）。"""
    pre = text[max(0, pos - 3):pos]
    return any(neg in pre for neg in _NEG_PREFIX)


def _has_kw(text: str, kws: tuple[str, ...]) -> bool:
    """任一关键词出现且不被否定前缀否定时返回 True。"""
    for kw in kws:
        if not kw:
            continue
        start = 0
        while True:
            idx = text.find(kw, start)
            if idx < 0:
                break
            if not _negated_before(text, idx):
                return True
            start = idx + 1
    return False


def _behavior_signals(text: str) -> dict:
    """从水印原文 + 画面观察文字里抽行为判据布尔信号。

    关键：所有关键词都经否定前缀过滤——『未见举牌 / 无封样 / 未见施工』等
    否定句式不应置位对应标志（否则会把「没做的事」误判成「做了」）。
    """
    sig: dict = {}
    for kws, flag in _BEHAVIOR_FLAGS:
        if _has_kw(text, kws):
            sig[flag] = True
    if _has_kw(text, _IN_PROGRESS_VERBS) and not _has_kw(text, _NOT_IN_PROGRESS):
        sig["in_progress"] = True
    if _hazard_hit(text):
        sig["is_hazard"] = True
    return sig


def parse_material_signals(material: str, n_images: int) -> list[dict]:
    """把 ``material`` 解析成与 ``images`` 对齐的逐图信号字典列表。

    返回长度 == ``n_images``；索引 ``k`` 对应第 ``k+1`` 张图。解析不出的图返回
    空字典（→ 待定）。material 里若没有「【图片 N】」标记（旧格式/纯文字），
    返回空列表，调用方应回退到其它分组方式。
    """
    if not material or n_images <= 0:
        return []
    parts = _IMG_HEADER.split(material)
    # parts: [pre, '1', block1, '2', block2, ...]
    blocks: dict[int, str] = {}
    i = 1
    while i < len(parts):
        try:
            num = int(parts[i])
        except (TypeError, ValueError):
            i += 2
            continue
        block = parts[i + 1] if i + 1 < len(parts) else ""
        # 同一张图出现多次时保留首个有效块
        blocks.setdefault(num, block)
        i += 2

    if not blocks:
        return []

    out: list[dict] = []
    for idx in range(n_images):
        num = idx + 1
        block = blocks.get(num, "")
        if not block.strip():
            out.append({})
            continue
        wm_text, raw_text, obs_text = _block_parts(block)
        combined = f"{wm_text} {raw_text} {obs_text}"
        # 内容与尾巴拆开：content 进工作内容，尾巴里的时间/天气/人员/部位回填信号。
        # 尾巴必须从**未经内容抽取裁剪**的原文解析：命中「施工内容：」时
        # _extract_content 只返回内容值，单位/地址/电话等尾巴字段会全部丢失。
        content, _ = split_hint(_extract_content(block, raw_text))
        _, tail = split_hint(raw_text or _extract_content(block, raw_text))
        section = _extract_section(wm_text) or tail.get("section", "")
        taken_at = _extract_taken_at(wm_text) or tail.get("taken_at", "")
        weather = _extract_weather(wm_text, raw_text) or tail.get("weather", "")
        person = _extract_person(wm_text) or tail.get("person", "")
        sig = ImageSignal(
            content=content,
            section=section,
            taken_at=taken_at,
            inspector=person or None,
        )
        for key, val in _behavior_signals(combined).items():
            setattr(sig, key, val)
        d = {k: getattr(sig, k) for k in ImageSignal.__dataclass_fields__
             if getattr(sig, k) is not None}
        # 天气 / 联系方式 / 地址：ImageSignal 没有这些字段，但分组回填 wm 时要消费它，
        # 随 dict 带出（水印模板的企业落款不能因为清洗工作内容就被丢掉）
        if weather:
            d["weather"] = weather
        for extra in ("weather", "contact", "location", "org", "postcode"):
            if tail.get(extra):
                d.setdefault(extra, tail[extra])
        out.append(d)
    return out


# ---------------------------------------------------------------- 逐图水印复述
# 与模型版「图片N：时间，地点，部位，内容，天气。」保持同一格式，但由后端从
# material 确定性生成：分组确认分支会用工具回执覆盖模型 answer，模型写的复述
# 会被丢掉（2026-11-12 真机「怎么这样回答呢」）。
_WM_KV = re.compile(r"(拍摄时间|地点|项目|部位|天气|人员)[:：]\s*([^；;\n]*)")
_RAW_CONTENT = re.compile(r"(?:施工内容|内容|工序|对象)[:：]\s*([^；;\n]{2,60})")


def _split_blocks(material: str) -> dict[int, str]:
    """按【图片 N】切出逐图块（与 parse_material_signals 同一套切分规则）。"""
    parts = _IMG_HEADER.split(material)
    blocks: dict[int, str] = {}
    i = 1
    while i < len(parts):
        try:
            num = int(parts[i])
        except (TypeError, ValueError):
            i += 2
            continue
        blocks.setdefault(num, parts[i + 1] if i + 1 < len(parts) else "")
        i += 2
    return blocks


def watermark_lines(material: str, n_images: int) -> list[str]:
    """逐图水印复述，每张一行；返回长度 == n_images。读不到水印的图也占一行。

    只做文本解析，不调用模型、不下载图片。material 无【图片 N】标记时返回空列表，
    调用方据此退化为只展示分组汇总。
    """
    if not material or n_images <= 0:
        return []
    blocks = _split_blocks(material)
    if not blocks:
        return []
    lines: list[str] = []
    for idx in range(n_images):
        num = idx + 1
        block = blocks.get(num, "")
        if not block.strip():
            lines.append(f"图片{num}：未读到水印。")
            continue
        wm_text, raw_text, _obs = _block_parts(block)
        kv = {m.group(1).strip(): m.group(2).strip() for m in _WM_KV.finditer(wm_text)}
        # 施工内容/工序通常不在结构化 KV 里，从识别原文单独补抽
        cm = _RAW_CONTENT.search(raw_text) or _RAW_CONTENT.search(block)
        content = cm.group(1).strip() if cm else ""
        if content:
            content = re.split(r"\s+拍摄时间[:：]", content, maxsplit=1)[0].strip()
        if content and "施工内容" not in kv and "内容" not in kv:
            kv["施工内容"] = content
        if not kv:
            lines.append(f"图片{num}：水印字段未识别。")
            continue
        # 每张图：标题行 + 每个字段「字段名：值」单独成行（带名、易读）
        recap = [f"图片{num}："]
        for label, value in kv.items():
            recap.append(f"  {label}：{value}")
        lines.append("\n".join(recap))
    return lines
