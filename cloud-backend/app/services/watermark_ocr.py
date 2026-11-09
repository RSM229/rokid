"""照片水印识别（OCR）。

工作台上传的现场照片可能已带第三方水印（今日水印相机 / 工程相机等）。
不同相机的水印字段名、顺序、排版都不一样，所以这里不做「字段名白名单」：

1. 通用抽取所有「标签: 值」/「标签 + 下一行值」，原文保留在 ``labeled_items``；
2. 只对「类型」做正则识别：时间、天气、经纬度、地址；
3. 需要落到业务字段时，按省统表字段表（record_field_spec）做名称匹配，
   匹配不到也保留原文，交给上层或模型按上下文映射。

- 上传链路（/media/images）用识别结果回填记录字段（请求未提供时才回填）。
- 独立接口（/media/watermark/recognize）供工作台"先识别一下"预览。
- 未安装 OCR 时优雅降级（available=False），不阻塞上传。
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
from datetime import datetime
from typing import Optional

from PIL import Image

from app.services import record_field_spec

logger = logging.getLogger(__name__)

# OCR 前把大图等比压到该边长以内；小图适当放大，避免水印小字漏识别。
_MAX_SIDE = 1600
_MIN_SIDE = 900
_MIN_SCORE = 0.35

_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    """懒加载 RapidOCR 单例；未安装返回 None。"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                except ImportError:
                    logger.info("rapidocr-onnxruntime 未安装，水印识别降级为不可用")
                    return None
                _engine = RapidOCR()
    return _engine


def _preprocess(image_bytes: bytes):
    """解码并按需缩放，返回 BGR ndarray 供 OCR；解码失败返回 None。"""
    import cv2
    import numpy as np

    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    w, h = img.size
    long_side = max(w, h)
    if long_side > _MAX_SIDE:
        scale = _MAX_SIDE / long_side
    elif long_side < _MIN_SIDE:
        scale = _MIN_SIDE / long_side
    else:
        scale = 1.0
    if scale != 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    arr = np.asarray(img)[:, :, ::-1]  # RGB -> BGR
    return np.ascontiguousarray(arr)


# ---------------------------------------------------------------- 类型正则

_DT_RE = re.compile(
    r"(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?"
    # OCR 常把日期与时间粘连（"2026-10-1909:32:05"），分隔符允许为空
    r"(?:[\sT]*(\d{1,2})[:：](\d{2})(?:[:：](\d{2}))?)?"
)
_WEATHER_RE = re.compile(
    r"(晴|多云|阴|阵雨|雷阵雨|小雨|中雨|大雨|暴雨|大暴雨|特大暴雨|冻雨|雨夹雪|小雪|中雪|大雪|雾|霾|沙尘暴|浮尘)"
)
_TEMP_RE = re.compile(r"([-+]?\d{1,2})\s*[℃°]")
_GPS_RE = re.compile(r"([01]?\d{1,2}\.\d{3,})\s*[,，]\s*([01]?\d{1,2}\.\d{3,})")
_SEP_RE = re.compile(r"^\s*([^:：]{1,28})\s*[:：]\s*(.*)$")
# 地址行：含行政区划/道路关键词
_ADDR_RE = re.compile(
    r"(省|市|区|县|镇|乡|街道|路|大道|街|巷|村|大厦|广场|工地|项目|标段|栋|号楼|隧道|桥)"
)

# 「概念」别名：这些是跨表单的通用语义，不是某一家水印的字段名。
# 任意其它标签都不会丢，会原样进入 labeled_items。
_SEMANTIC_ALIASES = {
    "项目": "project_name", "工程": "project_name", "项目名称": "project_name",
    "工程名称": "project_name", "建设项目": "project_name", "工程名称简称": "project_name",
    # 🔴 2026-11-04 用户明确：「单位工程」「施工区域」是施工部位，不是项目名/独立区域
    "单位工程": "section", "单位工程名称": "section",
    "施工区域": "section", "施工区域名称": "section",
    "位置": "location", "地点": "location", "地址": "location",
    "施工地点": "location", "拍摄地点": "location",
    "拍摄时间": "taken_at", "拍照时间": "taken_at", "时间": "taken_at",
    "日期": "taken_at", "记录时间": "taken_at",
    "天气": "weather", "气温": "weather",
    "拍摄人": "inspector", "拍照人": "inspector", "记录人": "inspector",
    "人员": "inspector", "检查人员": "inspector", "检查人": "inspector",
    "监理单位": "supervision_unit", "监理单位名称": "supervision_unit",
    "监理单位名称（盖章）": "supervision_unit", "项目监理机构": "supervision_unit",
    "监理": "inspector", "监理人员": "inspector", "旁站人员": "inspector",
    "见证人": "inspector", "验收人员": "inspector",
    "建设单位": "owner_unit", "施工单位": "construction_unit", "监理单位": "supervision_unit",
    "建设单位名称": "owner_unit", "施工单位名称": "construction_unit",
    "部位": "section", "施工部位": "section",
    "危大名称": "hazard_name", "危大工程名称": "hazard_name",
    # 2026-11-09 工程影像分类：水印相机的「内容/工序」字段，供归类引擎消费
    "内容": "content", "施工内容": "content", "工序": "content", "工序对象": "content",
    "备注": "remark",
    "项目简称": "project_name",
}

# 无冒号时，靠「标签后缀」判断下一行是否为值。只是排版启发式，不是字段白名单。
_LABEL_SUFFIXES = (
    "名称", "单位", "人员", "时间", "日期", "地点", "位置", "地址", "部位",
    "区域", "天气", "气温", "编号", "内容", "类型", "数量", "规格", "厂家",
    "品牌", "工程", "项目", "备注", "结论", "结果", "说明", "情况", "原因",
)

_SCHEMA_INDEX: dict[str, str] | None = None
_PREFIX_KEYS: list[str] | None = None


def _clean_line(raw: str) -> str:
    text = str(raw or "").replace("：", ":").replace("，", ",")
    return re.sub(r"\s+", " ", text).strip()


def _norm_label(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    # OCR 经常在中文标签内部插入空格（如“监 理单位”）。字段匹配时
    # 空白不承载语义，应与冒号、分隔点一样统一去除；原始标签仍保存在证据中。
    text = re.sub(r"[\s:：·•,，.。;；/\\_\-—]+", "", text)
    return text.replace("(盖章)", "")


def _schema_index() -> dict[str, str]:
    """省统表字段表的「标签 → canonical」索引（数据驱动，不手写字段名）。"""
    global _SCHEMA_INDEX
    if _SCHEMA_INDEX is None:
        from app.services import form_contracts, form_registry

        index: dict[str, str] = {}
        for form in form_registry.FORMS:
            for field in form_contracts.fields_of(form.form_code):
                canonical = str(field.get("canonical") or "")
                if not canonical:
                    continue
                for key in (field.get("label"), field.get("raw_label"), canonical, field.get("key")):
                    norm = _norm_label(str(key or ""))
                    if norm:
                        index.setdefault(norm, canonical)
        _SCHEMA_INDEX = index
    return _SCHEMA_INDEX


def _prefix_keys() -> list[str]:
    """可用于「无分隔符标签」切分的已知标签，长标签优先。"""
    global _PREFIX_KEYS
    if _PREFIX_KEYS is None:
        keys = set(_SEMANTIC_ALIASES) | set(_schema_index())
        _PREFIX_KEYS = sorted((key for key in keys if len(key) >= 4), key=len, reverse=True)
    return _PREFIX_KEYS


def _split_known_prefix(text: str) -> tuple[str, str] | None:
    """无冒号时按已知标签前缀切分，如「危大名称脚手架工程」。"""
    norm = _norm_label(text)
    if not norm:
        return None
    for key in _prefix_keys():
        if norm.startswith(key) and len(norm) > len(key):
            remainder = norm[len(key):]
            if remainder in _LABEL_SUFFIXES:
                return None
            return key, remainder
    return None


def _best_containment(norm: str, index: dict[str, str]) -> str | None:
    best_value, best_score = None, 0.0
    for key, canonical in index.items():
        if len(key) < 2 or len(norm) < 2:
            continue
        if norm in key or key in norm:
            score = min(len(norm), len(key)) / max(len(norm), len(key))
            if score > best_score:
                best_value, best_score = canonical, score
    return best_value if best_score >= 0.5 else None


def match_form_field(label: str, form_code: str | None = None) -> str | None:
    """把任意水印标签匹配到业务字段 canonical。

    优先目标表单的字段表，其次全量字段表；匹配不到返回 None，原文仍会保留。
    """
    norm = _norm_label(label)
    if not norm:
        return None
    semantic = _SEMANTIC_ALIASES.get(norm)
    if semantic:
        return semantic
    if form_code:
        local: dict[str, str] = {}
        from app.services.form_contracts import fields_of

        for field in fields_of(form_code):
            canonical = str(field.get("canonical") or "")
            if not canonical:
                continue
            for key in (field.get("label"), field.get("raw_label"), canonical, field.get("key")):
                value = _norm_label(str(key or ""))
                if value:
                    local.setdefault(value, canonical)
        if norm in local:
            return local[norm]
        matched = _best_containment(norm, local)
        if matched:
            return matched
    index = _schema_index()
    if norm in index:
        return index[norm]
    return _best_containment(norm, index)


# ---------------------------------------------------------------- 字段抽取

def _valid_label(label: str) -> bool:
    text = label.strip(" \t-—·•")
    if not (1 <= len(text) <= 28):
        return False
    # OCR 可能在中文标签内部插入空格；后续 _norm_label 会统一归一化。
    if re.search(r"https?$", text, re.IGNORECASE):
        return False
    if _DT_RE.search(text) or _GPS_RE.search(text):
        return False
    if re.fullmatch(r"[\d./\-:]+", text):
        return False
    return True


def _looks_like_bare_label(line: str) -> bool:
    text = line.strip()
    if not (2 <= len(text) <= 16):
        return False
    if re.search(r"[:：]", text) or re.search(r"\d", text):
        return False
    if _DT_RE.search(text) or _WEATHER_RE.search(text):
        return False
    if match_form_field(text):
        return True
    return text.endswith(_LABEL_SUFFIXES)


def _looks_like_value(line: str) -> bool:
    text = str(line or "").strip()
    if not text or _SEP_RE.match(text):
        return False
    return not _looks_like_bare_label(text)


def _iso_time(match: "re.Match[str]") -> str | None:
    try:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        second = int(match.group(6) or 0)
        return datetime(year, month, day, hour, minute, second).isoformat()
    except (TypeError, ValueError):
        return None


def _weather_text(line: str) -> str:
    weather = _WEATHER_RE.search(line)
    if not weather:
        return ""
    temp = _TEMP_RE.search(line)
    return weather.group(1) + (f" {temp.group(1)}℃" if temp else "")


def _infer_split_project_name(clean: list[str]) -> tuple[str, set[int]]:
    """Recover a title that OCR split across two adjacent watermark lines.

    Some watermark cameras render the project title over two lines without a
    ``项目名称：`` label, for example::

        广东省中西医结合应急救治
        中心项目

    Treating the second line as a field label would silently attach the next
    line (often the supervising company) to it. This layout rule only proposes
    a title when a project/engineering suffix line and its preceding text form
    one plausible Chinese title; all other watermark fields remain generic.
    """
    for index in range(1, len(clean)):
        current = clean[index].strip()
        previous = clean[index - 1].strip()
        if not current.endswith(("项目", "工程")):
            continue
        if _SEP_RE.match(current) or _SEP_RE.match(previous):
            continue
        if not previous or _DT_RE.search(previous) or _WEATHER_RE.search(previous):
            continue
        if _looks_like_bare_label(previous):
            continue
        candidate = re.sub(r"\s+", "", previous + current)
        if not (6 <= len(candidate) <= 60):
            continue
        if not re.search(r"[\u4e00-\u9fff]", candidate):
            continue
        return candidate, {index - 1, index}
    return "", set()


def parse_watermark_fields(lines: list[str]) -> dict:
    """从 OCR 文本行抽取水印字段（纯函数，可单测）。

    任意 ``标签: 值`` 都会进入 ``labeled_items``，不会因为不在白名单里被丢掉。
    """
    fields: dict = {}
    clean: list[str] = []
    for raw in lines:
        if not raw or not str(raw).strip():
            continue
        clean.append(_clean_line(raw))

    labeled_items: list[dict[str, str]] = []
    pairs: list[tuple[str, str]] = []
    consumed: set[int] = set()

    inferred_project, project_lines = _infer_split_project_name(clean)
    if inferred_project:
        fields["project_name"] = inferred_project
        labeled_items.append({"label": "项目名称", "value": inferred_project})
        consumed.update(project_lines)

    for index, text in enumerate(clean):
        if index in consumed:
            continue
        matched = _SEP_RE.match(text)
        if matched:
            label = matched.group(1).strip(" \t-—·•")
            value = matched.group(2).strip()
            if not _valid_label(label):
                continue
            if not value:
                next_line = clean[index + 1] if index + 1 < len(clean) else ""
                if next_line and _looks_like_value(next_line):
                    value = next_line
                    consumed.add(index + 1)
            pairs.append((label, value))
            labeled_items.append({"label": label, "value": value})
        elif _looks_like_bare_label(text):
            next_line = clean[index + 1] if index + 1 < len(clean) else ""
            if next_line and _looks_like_value(next_line):
                pairs.append((text, next_line))
                labeled_items.append({"label": text, "value": next_line})
                consumed.add(index + 1)
            else:
                split = _split_known_prefix(text)
                if split:
                    pairs.append(split)
                    labeled_items.append({"label": split[0], "value": split[1]})
        else:
            split = _split_known_prefix(text)
            if split:
                pairs.append(split)
                labeled_items.append({"label": split[0], "value": split[1]})

    # 标签 → 业务字段：字段表匹配，不覆盖已有值
    for label, value in pairs:
        if not value:
            continue
        canonical = match_form_field(label)
        if canonical and canonical not in fields:
            fields[canonical] = value

    # 拍摄时间：优先标签值，其次任意一行的日期时间
    taken_raw = str(fields.get("taken_at") or "")
    taken_match = _DT_RE.search(taken_raw) if taken_raw else None
    if taken_match is None:
        for text in clean:
            taken_match = _DT_RE.search(text)
            if taken_match:
                break
    if taken_match:
        iso = _iso_time(taken_match)
        if iso:
            fields["taken_at"] = iso

    # 天气
    if not fields.get("weather"):
        for text in clean:
            weather = _weather_text(text)
            if weather:
                fields["weather"] = weather
                break
    else:
        weather = _weather_text(str(fields["weather"]))
        if weather:
            fields["weather"] = weather

    # 经纬度
    if "lat" not in fields:
        for text in clean:
            matched = _GPS_RE.search(text)
            if not matched:
                continue
            first, second = float(matched.group(1)), float(matched.group(2))
            lat, lon = (first, second) if first < second else (second, first)
            if 15 <= lat <= 60 and 70 <= lon <= 140:
                fields["lat"], fields["lon"] = lat, lon
                break

    # 无标签地址：只取没有被当作标签值的独立行
    if not fields.get("location"):
        candidates: list[str] = []
        for index, text in enumerate(clean):
            if index in consumed or _SEP_RE.match(text) or _DT_RE.search(text):
                continue
            if _WEATHER_RE.search(text):
                continue
            if _ADDR_RE.search(text):
                candidates.append(text)
        if candidates:
            fields["location"] = max(candidates, key=len)

    if labeled_items:
        fields["labeled_items"] = labeled_items

    return fields


async def recognize_watermark(image_bytes: bytes) -> dict:
    """识别图片上的水印文字并抽取字段。

    返回：
        {"available": bool, "recognized": bool, "fields": {...},
         "labeled_items": [...], "unmapped_labels": [...], "lines": [...], "reason": str?}
    """
    engine = _get_engine()
    if engine is None:
        return {"available": False, "recognized": False, "fields": {}, "labeled_items": [],
                "unmapped_labels": [], "lines": [], "reason": "ocr_not_installed"}
    try:
        arr = await asyncio.to_thread(_preprocess, image_bytes)
        if arr is None:
            return {"available": False, "recognized": False, "fields": {}, "labeled_items": [],
                    "unmapped_labels": [], "lines": [], "reason": "image_decode_failed"}
        result, _ = await asyncio.to_thread(engine, arr)
    except Exception as exc:
        logger.warning("水印 OCR 失败：%s", type(exc).__name__)
        return {"available": False, "recognized": False, "fields": {}, "labeled_items": [],
                "unmapped_labels": [], "lines": [], "reason": f"ocr_failed:{exc}"}

    rows: list[tuple[float, float, str]] = []
    if result:
        for item in result:
            try:
                box, text = item[0], item[1]
                score = float(item[2]) if len(item) > 2 else 0.0
                top = min(point[1] for point in box)
                left = min(point[0] for point in box)
            except (IndexError, TypeError, ValueError):
                continue
            if text and score >= _MIN_SCORE:
                rows.append((top, left, str(text)))
    # 按阅读顺序排；RapidOCR 默认顺序不保证从上到下，标签/值配对会错位
    rows.sort(key=lambda row: (round(row[0] / 12), row[1]))
    lines = [text for _top, _left, text in rows]

    fields = parse_watermark_fields(lines)
    labeled_items = fields.get("labeled_items") or []
    unmapped = [
        item for item in labeled_items
        if item.get("value") and not match_form_field(item.get("label") or "")
    ]
    return {
        "available": True,
        "recognized": bool([key for key in fields if key != "labeled_items"] or labeled_items),
        "fields": fields,
        "labeled_items": labeled_items,
        "unmapped_labels": unmapped,
        "lines": lines,
    }
