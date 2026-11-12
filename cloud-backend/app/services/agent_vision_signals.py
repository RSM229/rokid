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

_CONTENT_KW = re.compile(r"(?:内容|工序|对象|施工内容|检验批|隐蔽)[:：]\s*([^\n；;，,]{2,40})")
_SECTION_KW = re.compile(r"(?:部位|桩号|施工部位)[:：]\s*([^\n；;，,]{1,40})")


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
    """从水印原文里抽「内容/工序/对象」作为归类 content 线索。"""
    m = _CONTENT_KW.search(block)
    if m:
        return m.group(1).strip()
    # 退而求其次：用整段水印原文（截断）喂给规则引擎做子串匹配
    if raw_text:
        return raw_text.strip()[:80]
    return ""


def _extract_section(wm_text: str) -> str:
    m = _SECTION_KW.search(wm_text)
    return m.group(1).strip() if m else ""


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
        content = _extract_content(block, raw_text)
        section = _extract_section(wm_text)
        sig = ImageSignal(content=content, section=section)
        for key, val in _behavior_signals(combined).items():
            setattr(sig, key, val)
        d = {k: getattr(sig, k) for k in ImageSignal.__dataclass_fields__
             if getattr(sig, k) is not None}
        out.append(d)
    return out
