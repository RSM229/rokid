"""照片水印识别（OCR）。

工作台上传的现场照片可能已带第三方水印（今日水印相机 / 工程相机等），
本模块用 RapidOCR 离线读出水印文字，并抽取结构化字段：
拍摄时间 / 位置 / 天气 / 人员 / 项目名称 / 部位 / 经纬度。

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

logger = logging.getLogger(__name__)

# OCR 前把大图等比压到该边长以内，兼顾速度与水印可读性
_MAX_SIDE = 1600

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
    """解码并按需缩图，返回 BGR ndarray 供 OCR；解码失败返回 None。"""
    import cv2
    import numpy as np

    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    w, h = img.size
    scale = min(1.0, _MAX_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    arr = np.asarray(img)[:, :, ::-1]  # RGB -> BGR
    return np.ascontiguousarray(arr)


# ---------------------------------------------------------------- 字段抽取

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
_LABEL_RE = re.compile(r"(项目|工程名称|工程|监理|拍摄人|拍照人|记录人|人员|位置|地点|地址|部位|天气)\s*[:：]\s*(.*)")
# 地址行：含行政区划/道路关键词
_ADDR_RE = re.compile(
    r"(省|市|区|县|镇|乡|街道|路|大道|街|巷|村|大厦|广场|工地|项目|标段|栋|号楼|隧道|桥)"
)


def parse_watermark_fields(lines: list[str]) -> dict:
    """从 OCR 文本行抽取水印结构化字段（纯函数，可单测）。"""
    fields: dict = {}
    clean: list[str] = []
    for raw in lines:
        if not raw:
            continue
        # 全角冒号/逗号归一，去多余空白
        text = raw.replace("：", ":").strip()
        text = re.sub(r"\s+", " ", text)
        clean.append(text)

    addr_candidates: list[str] = []
    for text in clean:
        label = _LABEL_RE.match(text)
        pairs = {}
        if label:
            pairs[label.group(1)] = label.group(2).strip()
        for key, value in pairs.items():
            if not value:
                continue
            if key in ("项目", "工程名称", "工程") and "project" not in fields:
                fields["project"] = value
            elif key == "监理" and "person" not in fields:
                fields["person"] = value
            elif key in ("拍摄人", "拍照人", "记录人", "人员") and "person" not in fields:
                fields["person"] = value
            elif key in ("位置", "地点", "地址") and "location" not in fields:
                fields["location"] = value
            elif key == "部位" and "section" not in fields:
                fields["section"] = value
            elif key == "天气" and "weather" not in fields:
                m = _WEATHER_RE.search(value)
                t = _TEMP_RE.search(value)
                fields["weather"] = "".join(
                    x for x in [m.group(1) if m else "", f" {t.group(1)}℃" if t else ""] if x
                )

        # 时间
        if "taken_at" not in fields:
            m = _DT_RE.search(text)
            if m:
                try:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    hh = int(m.group(4) or 0)
                    mi = int(m.group(5) or 0)
                    ss = int(m.group(6) or 0)
                    fields["taken_at"] = datetime(y, mo, d, hh, mi, ss).isoformat()
                except ValueError:
                    pass

        # 天气（无标签的 "晴 25℃" 形式）
        if "weather" not in fields:
            m = _WEATHER_RE.search(text)
            if m:
                t = _TEMP_RE.search(text)
                fields["weather"] = "".join(
                    x for x in [m.group(1), f" {t.group(1)}℃" if t else ""] if x
                )

        # 经纬度
        if "lat" not in fields:
            m = _GPS_RE.search(text)
            if m:
                a, b = float(m.group(1)), float(m.group(2))
                # 粗判：纬度小、经度大（中国大陆范围）
                lat, lon = (a, b) if a < b else (b, a)
                if 15 <= lat <= 60 and 70 <= lon <= 140:
                    fields["lat"], fields["lon"] = lat, lon

        # 地址候选（无标签）
        if _ADDR_RE.search(text) and not _DT_RE.search(text):
            addr_candidates.append(text)

    if "location" not in fields and addr_candidates:
        # 取最长的地址候选
        fields["location"] = max(addr_candidates, key=len)

    return fields


async def recognize_watermark(image_bytes: bytes) -> dict:
    """识别图片上的水印文字并抽取字段。

    返回：
        {"available": bool, "fields": {...}, "lines": [...], "reason": str?}
    """
    engine = _get_engine()
    if engine is None:
        return {"available": False, "fields": {}, "lines": [], "reason": "ocr_not_installed"}
    try:
        arr = await asyncio.to_thread(_preprocess, image_bytes)
        if arr is None:
            return {"available": False, "fields": {}, "lines": [], "reason": "image_decode_failed"}
        result, _ = await asyncio.to_thread(engine, arr)
    except Exception as exc:
        logger.warning("水印 OCR 失败：%s", type(exc).__name__)
        return {"available": False, "fields": {}, "lines": [], "reason": f"ocr_failed:{exc}"}

    lines: list[str] = []
    if result:
        for item in result:
            try:
                text = item[1]
                score = float(item[2]) if len(item) > 2 else 0.0
            except (IndexError, TypeError, ValueError):
                continue
            if text and score >= 0.5:
                lines.append(text)
    fields = parse_watermark_fields(lines)
    return {"available": True, "fields": fields, "lines": lines}
