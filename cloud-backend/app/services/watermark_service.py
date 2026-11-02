"""水印服务 - 供工作记录等模块复用"""
import asyncio
import io
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
import platform
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def get_font_path(size: int = 24) -> ImageFont.FreeTypeFont:
    """跨平台获取中文字体（唯一真源，供 watermark router 复用）。

    优先级：文泉驿（wqy，体积最小、覆盖常用中文）→ Noto/思源 CJK（覆盖更全）。

    ❗ 若系统中没有任何可用的中文字体，直接抛异常，而不是静默回退到
    ``ImageFont.load_default()``——默认字体没有中文字形，会画出一堆方块，
    反而更难察觉配置缺失。
    """
    system = platform.system()
    # 🔴 项目自带字体优先：随代码目录发布（容器内即 bind mount 的 /app/app/fonts），
    #    不依赖系统包，`docker compose up -d --force-recreate` 重建容器也不会丢。
    #    教训（2026-10-13 生产 500）：重建容器后系统字体被清空、镜像又未重建，
    #    所有水印接口集体 RuntimeError；而 app/fonts/simhei.ttf 明明在，只是原先没被查找。
    _bundled = [
        Path(__file__).resolve().parents[1] / "fonts" / "simhei.ttf",
        Path(__file__).resolve().parents[1] / "fonts" / "wqy-zenhei.ttc",
    ]
    candidates = {
        "Windows": [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ],
        "Darwin": [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ],
        "Linux": [
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/SourceHanSansSC/SourceHanSansSC-Regular.otf",
        ],
    }
    for path in [*_bundled, *candidates.get(system, [])]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
    raise RuntimeError(
        "未找到可用的中文字体，无法为水印渲染中文。"
        "请把中文字体放到 backend 的 app/fonts/（如 simhei.ttf），"
        "或安装 fonts-wqy-zenhei（Debian: apt-get install fonts-wqy-zenhei）"
        "或 fonts-noto-cjk 后重试。"
    )


async def apply_watermark_to_image(
    image_data: bytes,
    project_name: str = "",
    supervisor: str = "",
    unit_project: str = "",
    section: str = "",
    location: str = "",
    work_content: str = "",
    weather: str = "",
    hazard_flag: str = "",
) -> bytes:
    """为图片添加监理水印（左下角）

    水印内容：工程名称、单位工程（简称）、施工区域、施工内容、拍摄时间、天气
    """
    img = Image.open(io.BytesIO(image_data))
    if img.mode != "RGB":
        img = img.convert("RGB")

    draw = ImageDraw.Draw(img)
    width, height = img.size

    font_size = max(16, min(width, height) // 25)
    font = get_font_path(font_size)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"工程名称：{project_name}" if project_name else "",
        f"监理人员：{supervisor}" if supervisor else "",
        f"单位工程：{unit_project}" if unit_project else "",
        f"施工区域：{section}" if section else "",
        f"位置：{location}" if location else "",
        f"施工内容：{work_content}" if work_content else "",
        f"拍摄时间：{now}",
        f"天气：{weather}" if weather else "",
    ]
    if hazard_flag:
        lines.append(f"隐患：{hazard_flag}")
    lines = [l for l in lines if l]

    line_height = font_size + 6
    padding = 10
    bg_width = max(draw.textlength(l, font=font) for l in lines) + padding * 2 if lines else 200
    bg_height = len(lines) * line_height + padding * 2

    # 左下角半透明背景
    overlay = Image.new("RGBA", (int(bg_width), int(bg_height)), (0, 0, 0, 128))
    img.paste(overlay, (padding, height - int(bg_height) - padding), overlay)

    # 绘制文字（白色）
    y = height - int(bg_height) - padding + padding
    for line in lines:
        draw.text((padding * 2, y), line, fill=(255, 255, 255), font=font)
        y += line_height

    output = io.BytesIO()
    img.save(output, format="JPEG", quality=90)
    return output.getvalue()


# ===== 视频水印 =====


def _escape_drawtext(text: str) -> str:
    """转义 ffmpeg drawtext 滤镜中的特殊字符"""
    if text is None:
        return ""
    # 转义冒号、单引号、反斜杠、逗号
    replacements = {
        "\\": "\\\\\\\\",
        ":": "\\:",
        "'": "\\\\u0027",
        ",": "\\,",
        "%": "\\%",
    }
    out = str(text)
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def apply_video_watermark(
    input_path: str | Path,
    output_path: str | Path,
    project_name: str = "",
    supervisor: str = "",
    location: str = "",
    section: str = "",
    weather: str = "",
    captured_at: str = "",
) -> bool:
    """为视频添加水印（右上角 + 左下角）

    使用 ffmpeg drawtext 滤镜叠加水印：
    - 右上角：项目名称 + 拍摄时间 + 拍摄人
    - 左下角：位置 + 施工区域 + 天气

    Args:
        input_path: 原始视频路径
        output_path: 水印视频输出路径
        project_name: 项目名称
        supervisor: 拍摄人
        location: 位置
        section: 施工区域
        weather: 天气
        captured_at: 拍摄时间字符串

    Returns:
        True 表示成功；False 表示失败（已记录日志，不阻断上传）
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        logger.warning(f"视频水印：原始视频不存在 {input_path}")
        return False

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("视频水印：服务器未安装 ffmpeg，跳过水印")
        return False

    now_str = captured_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    font_path = _pick_font_path_for_ffmpeg()

    # 右上角水印行
    top_lines = []
    if project_name:
        top_lines.append(f"工程：{project_name}")
    top_lines.append(f"时间：{now_str}")
    if supervisor:
        top_lines.append(f"拍摄人：{supervisor}")

    # 左下角水印行
    bottom_lines = []
    if location:
        bottom_lines.append(f"位置：{location}")
    if section:
        bottom_lines.append(f"区域：{section}")
    if weather:
        bottom_lines.append(f"天气：{weather}")

    # 构建 drawtext 滤镜链
    filters = []
    # 右上角
    y_pos = 10
    for line in top_lines:
        escaped = _escape_drawtext(line)
        filter_str = (
            f"drawtext=fontfile='{font_path}':text='{escaped}':"
            f"fontcolor=white@0.9:fontsize=22:x=w-tw-15:y={y_pos}:"
            f"box=1:boxcolor=black@0.5:boxborderw=4"
        )
        filters.append(filter_str)
        y_pos += 30
    # 左下角
    y_pos = "h-th-10"
    for idx, line in enumerate(bottom_lines):
        escaped = _escape_drawtext(line)
        if idx == 0:
            filter_str = (
                f"drawtext=fontfile='{font_path}':text='{escaped}':"
                f"fontcolor=white@0.9:fontsize=22:x=15:y={y_pos}:"
                f"box=1:boxcolor=black@0.5:boxborderw=4"
            )
        else:
            # 后续行基于上一行底部偏移（用表达式近似）
            filter_str = (
                f"drawtext=fontfile='{font_path}':text='{escaped}':"
                f"fontcolor=white@0.9:fontsize=22:x=15:"
                f"y=h-th-10-{idx * 30}:"
                f"box=1:boxcolor=black@0.5:boxborderw=4"
            )
        filters.append(filter_str)

    if not filters:
        logger.info("视频水印：无水印内容，跳过")
        return False

    filter_complex = ",".join(filters)

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(input_path),
        "-vf", filter_complex,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                f"视频水印 ffmpeg 失败（returncode={proc.returncode}）: "
                f"{proc.stderr.decode('utf-8', errors='ignore')[:500]}"
            )
            output_path.unlink(missing_ok=True)  # 清理半成品，防孤儿文件占磁盘
            return False
        if not output_path.exists():
            logger.warning("视频水印：输出文件未生成")
            return False
        logger.info(f"视频水印成功：{output_path}")
        return True
    except subprocess.TimeoutExpired:
        logger.warning("视频水印 ffmpeg 执行超时")
        output_path.unlink(missing_ok=True)
        return False
    except Exception as exc:
        logger.warning(f"视频水印异常：{exc}")
        output_path.unlink(missing_ok=True)
        return False


async def apply_video_watermark_async(
    input_path: str | Path,
    output_path: str | Path,
    project_name: str = "",
    supervisor: str = "",
    location: str = "",
    section: str = "",
    weather: str = "",
    captured_at: str = "",
) -> bool:
    """异步视频水印 - 使用 asyncio 子进程，不阻塞事件循环

    参数与 apply_video_watermark 相同，返回值语义一致。
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        logger.warning(f"视频水印：原始视频不存在 {input_path}")
        return False

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("视频水印：服务器未安装 ffmpeg，跳过水印")
        return False

    now_str = captured_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    font_path = _pick_font_path_for_ffmpeg()

    top_lines = []
    if project_name:
        top_lines.append(f"工程：{project_name}")
    top_lines.append(f"时间：{now_str}")
    if supervisor:
        top_lines.append(f"拍摄人：{supervisor}")

    bottom_lines = []
    if location:
        bottom_lines.append(f"位置：{location}")
    if section:
        bottom_lines.append(f"区域：{section}")
    if weather:
        bottom_lines.append(f"天气：{weather}")

    filters = []
    y_pos = 10
    for line in top_lines:
        escaped = _escape_drawtext(line)
        filter_str = (
            f"drawtext=fontfile='{font_path}':text='{escaped}':"
            f"fontcolor=white@0.9:fontsize=22:x=w-tw-15:y={y_pos}:"
            f"box=1:boxcolor=black@0.5:boxborderw=4"
        )
        filters.append(filter_str)
        y_pos += 30
    y_pos = "h-th-10"
    for idx, line in enumerate(bottom_lines):
        escaped = _escape_drawtext(line)
        if idx == 0:
            filter_str = (
                f"drawtext=fontfile='{font_path}':text='{escaped}':"
                f"fontcolor=white@0.9:fontsize=22:x=15:y={y_pos}:"
                f"box=1:boxcolor=black@0.5:boxborderw=4"
            )
        else:
            filter_str = (
                f"drawtext=fontfile='{font_path}':text='{escaped}':"
                f"fontcolor=white@0.9:fontsize=22:x=15:"
                f"y=h-th-10-{idx * 30}:"
                f"box=1:boxcolor=black@0.5:boxborderw=4"
            )
        filters.append(filter_str)

    if not filters:
        logger.info("视频水印：无水印内容，跳过")
        return False

    filter_complex = ",".join(filters)

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(input_path),
        "-vf", filter_complex,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("视频水印 ffmpeg 执行超时")
            output_path.unlink(missing_ok=True)  # 清理半成品
            return False

        if proc.returncode != 0:
            stderr = await proc.stderr.read() if proc.stderr else b""
            logger.warning(
                f"视频水印 ffmpeg 失败（returncode={proc.returncode}）: "
                f"{stderr.decode('utf-8', errors='ignore')[:500]}"
            )
            output_path.unlink(missing_ok=True)
            return False
        if not output_path.exists():
            logger.warning("视频水印：输出文件未生成")
            return False
        logger.info(f"视频水印成功：{output_path}")
        return True
    except Exception as exc:
        logger.warning(f"视频水印异常：{exc}")
        output_path.unlink(missing_ok=True)
        return False


async def apply_video_watermark_background(
    user_id: int,
    input_path: str | Path,
    output_path: str | Path,
    project_name: str = "",
    supervisor: str = "",
    location: str = "",
    section: str = "",
    weather: str = "",
    captured_at: str = "",
) -> bool:
    """后台异步视频水印 + WebSocket 进度推送

    在视频上传后立即返回，水印在后台异步执行。
    处理开始、完成、失败时通过 WebSocket 推送进度通知。
    """
    from app.routers.ws import broadcast_to_user

    input_path = Path(input_path)
    output_path = Path(output_path)

    # 推送开始事件
    await broadcast_to_user(user_id, {
        "event": "video_watermark_start",
        "data": {
            "filename": input_path.name,
            "message": "视频水印处理开始",
        },
    })

    ok = await apply_video_watermark_async(
        input_path=input_path,
        output_path=output_path,
        project_name=project_name,
        supervisor=supervisor,
        location=location,
        section=section,
        weather=weather,
        captured_at=captured_at,
    )

    # 推送结果事件
    await broadcast_to_user(user_id, {
        "event": "video_watermark_done" if ok else "video_watermark_failed",
        "data": {
            "filename": input_path.name,
            "output": str(output_path) if ok else None,
            "message": "视频水印处理完成" if ok else "视频水印处理失败",
        },
    })

    return ok


def _pick_font_path_for_ffmpeg() -> str:
    """为 ffmpeg drawtext 滤镜挑一个中文字体路径（复用 get_font_path 的唯一真源）。

    ffmpeg 的 ``fontfile`` 参数需要字体文件的**绝对路径**，所以这里用
    ``get_font_path`` 找到字体后取其 ``.path``。找不到中文字体时同样抛
    ``RuntimeError``，而不是把 DejaVu 这种无中文字形的字体塞给 ffmpeg
    （那样视频水印的中文会变成豆腐块，且静默无感知）。
    """
    return get_font_path(48).path
