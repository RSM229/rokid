"""媒体文件内容校验，供统一媒体与历史上传接口复用。"""
from io import BytesIO

from fastapi import HTTPException
from PIL import Image


def assert_valid_image(data: bytes) -> None:
    """验证图片可被 Pillow 完整解析，拒绝伪造扩展名和损坏文件。"""
    try:
        image = Image.open(BytesIO(data))
        image.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="图片内容无效") from exc


def assert_valid_audio(data: bytes, ext: str) -> None:
    """校验常用 MP3/AAC/WAV/M4A/WebM 文件头。"""
    valid = (
        data.startswith(b"ID3")
        or data.startswith(b"RIFF")
        or data.startswith(b"\xff\xf1")
        or data.startswith(b"\xff\xf9")
        or (len(data) > 12 and data[4:8] == b"ftyp")
        or data.startswith(b"\x1a\x45\xdf\xa3")
    )
    valid = valid or (
        ext == ".mp3"
        and len(data) > 2
        and data[0] == 0xFF
        and data[1] & 0xE0 == 0xE0
    )
    if not valid:
        raise HTTPException(status_code=400, detail="音频文件头无效")
