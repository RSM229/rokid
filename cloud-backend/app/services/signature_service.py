"""电子签章服务 - 将签章图片嵌入 PDF 文档

作者: cloud-backend
创建日期: 2026-07-18

实现逻辑：
1. 使用 reportlab 在透明 PDF 页面上绘制签章图片，作为水印层
2. 使用 pypdf 将水印层叠加到原始 PDF 指定位置（默认右下角）
3. 输出新 PDF 文件，原始 PDF 保留
"""
import hashlib
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.config import settings

logger = logging.getLogger(__name__)


def _resolve_file_path(url_or_path: str) -> Optional[Path]:
    """将 /uploads/xxx 或 /exports/xxx 解析为文件系统绝对路径"""
    if not url_or_path:
        return None
    if url_or_path.startswith("/uploads/"):
        relative = Path(url_or_path.removeprefix("/uploads/"))
        candidate = (settings.upload_dir / relative).resolve()
        try:
            candidate.relative_to(settings.upload_dir.resolve())
        except ValueError:
            return None
        return candidate
    if url_or_path.startswith("/exports/"):
        relative = Path(url_or_path.removeprefix("/exports/"))
        candidate = (settings.export_dir / relative).resolve()
        try:
            candidate.relative_to(settings.export_dir.resolve())
        except ValueError:
            return None
        return candidate
    p = Path(url_or_path)
    return p if p.exists() else None


def _signature_hash(image_path: Path) -> str:
    """计算签章图片的 sha256 哈希，作为签署留痕"""
    h = hashlib.sha256()
    h.update(image_path.read_bytes())
    return h.hexdigest()


def embed_signature_to_pdf(
    original_pdf_url: str,
    signature_image_url: str,
    output_filename: str,
    position: str = "bottom-right",
    x_offset: int = 40,
    y_offset: int = 40,
    stamp_width: int = 120,
    stamp_height: int = 120,
) -> tuple[str, str]:
    """将签章图片嵌入 PDF，生成已签章 PDF

    Args:
        original_pdf_url: 原始 PDF 的 URL（/exports/xxx）
        signature_image_url: 签章图片 URL（/uploads/xxx）
        output_filename: 输出 PDF 文件名（不含路径）
        position: 签章位置，bottom-right / bottom-left / top-right / top-left
        x_offset: 水平偏移（pt）
        y_offset: 垂直偏移（pt）
        stamp_width: 签章宽度（pt）
        stamp_height: 签章高度（pt）

    Returns:
        (signed_pdf_url, signature_hash)
    """
    from pypdf import PdfReader, PdfWriter

    original_path = _resolve_file_path(original_pdf_url)
    if not original_path or not original_path.exists():
        raise FileNotFoundError(f"原始 PDF 不存在：{original_pdf_url}")

    signature_path = _resolve_file_path(signature_image_url)
    if not signature_path or not signature_path.exists():
        raise FileNotFoundError(f"签章图片不存在：{signature_image_url}")

    # 读取原始 PDF
    reader = PdfReader(str(original_path))
    page_count = len(reader.pages)
    if page_count == 0:
        raise ValueError("原始 PDF 没有页面")

    # 获取第一页尺寸作为水印页面尺寸
    first_page = reader.pages[-1]
    page_width = float(first_page.mediabox.width)
    page_height = float(first_page.mediabox.height)

    # 计算签章位置
    if position == "bottom-right":
        x = page_width - stamp_width - x_offset
        y = y_offset
    elif position == "bottom-left":
        x = x_offset
        y = y_offset
    elif position == "top-right":
        x = page_width - stamp_width - x_offset
        y = page_height - stamp_height - y_offset
    elif position == "top-left":
        x = x_offset
        y = page_height - stamp_height - y_offset
    else:
        x = page_width - stamp_width - x_offset
        y = y_offset

    # 使用 reportlab 生成透明背景的水印 PDF
    watermark_buf = io.BytesIO()
    c = canvas.Canvas(watermark_buf, pagesize=(page_width, page_height))
    try:
        img_reader = ImageReader(str(signature_path))
        c.drawImage(
            img_reader,
            x,
            y,
            width=stamp_width,
            height=stamp_height,
            mask="auto",
            preserveAspectRatio=True,
        )
    except Exception as exc:
        logger.warning(f"签章图片绘制失败，尝试预处理：{exc}")
        # 预处理图片为 RGBA PNG
        with Image.open(signature_path) as img:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            tmp_buf = io.BytesIO()
            img.save(tmp_buf, format="PNG")
            tmp_buf.seek(0)
            img_reader = ImageReader(tmp_buf)
            c.drawImage(
                img_reader,
                x,
                y,
                width=stamp_width,
                height=stamp_height,
                mask="auto",
                preserveAspectRatio=True,
            )
    c.showPage()
    c.save()
    watermark_buf.seek(0)

    # 仅在最后一页（通常是签名页）叠加签章
    watermark_reader = PdfReader(watermark_buf)
    watermark_page = watermark_reader.pages[0]

    writer = PdfWriter()
    for idx, page in enumerate(reader.pages):
        writer.add_page(page)
        if idx == page_count - 1:
            writer.pages[-1].merge_page(watermark_page)

    # 写入元数据
    writer.add_metadata({
        "/Title": "Signed Document",
        "/Producer": "gdjl-cloud-backend signature service",
        "/SignDate": datetime.now().isoformat(),
    })

    # 输出文件
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.export_dir / output_filename
    with open(output_path, "wb") as f:
        writer.write(f)

    signed_pdf_url = f"/exports/{output_filename}"
    sig_hash = _signature_hash(signature_path)
    return signed_pdf_url, sig_hash
