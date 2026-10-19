"""水印服务路由 — 跨平台字体、GPS、天气、隐患标识"""
import io
import time
import hashlib
import platform
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import httpx
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from app.config import settings
from app.routers.auth import get_current_user
# 字体查找的唯一真源，避免与 watermark_service 的候选集漂移
from app.services.watermark_service import get_font_path

router = APIRouter()


async def fetch_weather(lat: Optional[float], lon: Optional[float], city: Optional[str]) -> str:
    """获取天气信息（和风天气 API）"""
    if not settings.weather_api_key:
        return ""
    try:
        location = f"{lon},{lat}" if lat and lon else city or ""
        if not location:
            return ""
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                settings.weather_api_url,
                params={"location": location, "key": settings.weather_api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            if "now" in data:
                now = data["now"]
                return f"{now.get('text', '')} {now.get('temp', '')}℃"
    except Exception:
        pass
    return ""


@router.post("/apply")
async def apply_watermark(
    image: UploadFile = File(...),
    project_name: str = Form(""),
    supervisor: str = Form(""),
    section: str = Form(""),
    location: str = Form(""),
    weather: str = Form(""),
    hazard_flag: str = Form(""),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None),
    user: dict = Depends(get_current_user),
):
    """为图片叠加水印

    水印字段：
    - 项目名称
    - 监理人员
    - 拍摄时间（服务端时间）
    - 地理位置（GPS 或手动输入）
    - 天气（自动获取或手动输入）
    - 楼栋/楼层/部位
    - 隐患标识
    """
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="图片为空")

    # 自动获取天气
    if not weather and (lat or lon):
        weather = await fetch_weather(lat, lon, None)

    # 叠加水印
    try:
        watermarked = _apply_watermark_to_image(
            image_bytes,
            project_name=project_name,
            supervisor=supervisor or user.real_name,
            section=section,
            location=location,
            weather=weather,
            hazard_flag=hazard_flag,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # 保存水印图
    img_name = f"wm_{int(time.time())}_{hashlib.md5(watermarked).hexdigest()[:8]}.jpg"
    img_path = settings.upload_dir / img_name
    img_path.write_bytes(watermarked)

    return {
        "watermarked_url": f"/uploads/{img_name}",
        "project_name": project_name,
        "supervisor": supervisor,
        "section": section,
        "location": location,
        "weather": weather,
        "hazard_flag": hazard_flag,
        "timestamp": datetime.now().isoformat(),
    }


@router.post("/apply-stream")
async def apply_watermark_stream(
    image: UploadFile = File(...),
    project_name: str = Form(""),
    supervisor: str = Form(""),
    section: str = Form(""),
    location: str = Form(""),
    weather: str = Form(""),
    hazard_flag: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """叠加水印并直接返回图片流（不保存）"""
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="图片为空")

    try:
        watermarked = _apply_watermark_to_image(
            image_bytes,
            project_name=project_name,
            supervisor=supervisor or user.real_name,
            section=section,
            location=location,
            weather=weather,
            hazard_flag=hazard_flag,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return StreamingResponse(
        io.BytesIO(watermarked),
        media_type="image/jpeg",
        headers={"Content-Disposition": "inline; filename=watermarked.jpg"},
    )


@router.post("/upload-photos")
async def upload_photos(
    photos: List[UploadFile] = File(...),
    module: str = Form("inspection"),  # inspection/acceptance/witness/hazard
    project_name: str = Form(""),
    section_name: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """批量上传现场识别照片（带水印）

    用于现场识别模块，自动为每张照片添加水印并保存。
    """
    if not photos:
        raise HTTPException(status_code=400, detail="没有上传任何照片")

    results = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for i, photo in enumerate(photos):
        image_bytes = await photo.read()
        if not image_bytes:
            continue

        # 自动添加水印（前端已添加，这里可以再次处理或直接保存）
        try:
            watermarked = _apply_watermark_to_image(
                image_bytes,
                project_name=project_name or "广东监理示范工程",
                supervisor=user.real_name,
                section=section_name,
                weather="晴",  # 可以从前端传入或自动获取
            )
        except RuntimeError as exc:
            results.append({
                "filename": photo.filename,
                "url": None,
                "module": module,
                "timestamp": now_str,
                "error": str(exc),
            })
            continue

        # 保存水印图
        img_name = f"camera_{module}_{int(time.time())}_{i}_{hashlib.md5(watermarked).hexdigest()[:8]}.jpg"
        img_path = settings.upload_dir / img_name
        img_path.write_bytes(watermarked)

        results.append({
            "filename": img_name,
            "url": f"/uploads/{img_name}",
            "module": module,
            "timestamp": now_str,
        })

    return {
        "uploaded_count": len(results),
        "photos": results,
        "module": module,
    }


def _apply_watermark_to_image(
    image_bytes: bytes,
    project_name: str = "",
    supervisor: str = "",
    section: str = "",
    location: str = "",
    weather: str = "",
    hazard_flag: str = "",
) -> bytes:
    """在图片上叠加水印"""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    draw = ImageDraw.Draw(img)
    width, height = img.size

    # 水印字体大小根据图片尺寸自适应
    font_size = max(16, min(width, height) // 25)
    font = get_font_path(font_size)

    # 水印内容
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"项目：{project_name}" if project_name else "",
        f"监理：{supervisor}" if supervisor else "",
        f"时间：{now}",
        f"位置：{location}" if location else "",
        f"部位：{section}" if section else "",
        f"天气：{weather}" if weather else "",
    ]
    if hazard_flag:
        lines.append(f"⚠ 隐患：{hazard_flag}")

    lines = [l for l in lines if l]

    # 水印背景区域
    line_height = font_size + 6
    padding = 10
    bg_height = len(lines) * line_height + padding * 2
    bg_width = max(draw.textlength(l, font=font) for l in lines) + padding * 2

    # 半透明背景
    overlay = Image.new("RGBA", (int(bg_width), int(bg_height)), (0, 0, 0, 128))
    img.paste(overlay, (padding, height - int(bg_height) - padding), overlay)

    # 绘制文字
    y = height - int(bg_height) - padding + padding
    for line in lines:
        color = (255, 100, 100) if line.startswith("⚠") else (255, 255, 255)
        draw.text((padding * 2, y), line, fill=color, font=font)
        y += line_height

    # 输出
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    return output.getvalue()
