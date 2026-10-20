"""Private, retryable recording segments. No evidence records until normal upload.

Requires a shared local filesystem for workers. Staging expires after 24h;
deployments should periodically remove expired directories under ROOT.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from fastapi import HTTPException, UploadFile

from app.config import settings

ROOT = Path(tempfile.gettempdir()) / "rokid-video-segments"
MAX_SEGMENTS = 20
TTL = 24 * 3600


def session_path(project_id: int, user_id: int, recording_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", recording_id):
        raise HTTPException(400, "无效的录像编号")
    scope = hashlib.sha256(f"{project_id}:{user_id}:{recording_id}".encode()).hexdigest()
    return ROOT / scope


@contextmanager
def locked(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    lock = path / ".lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise HTTPException(409, "录像正在处理，请稍后重试")
    try:
        stamp = path / ".created"
        if stamp.exists() and time.time() - stamp.stat().st_mtime > TTL:
            raise HTTPException(410, "录像片段已过期，请重新拍摄")
        stamp.touch(exist_ok=True)
        yield
    finally:
        lock.rmdir()


async def run_tool(name: str, *args: str) -> bytes:
    executable = shutil.which(name)
    if not executable:
        raise HTTPException(503, f"服务器未安装 {name}")
    process = await asyncio.create_subprocess_exec(
        executable, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=110)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise HTTPException(400, "视频无法解析或合并")
    return stdout


async def probe(path: Path) -> dict:
    try:
        raw = await run_tool("ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path))
        data = json.loads(raw)
        video = next(s for s in data["streams"] if s["codec_type"] == "video")
        duration = float(data["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0 or video["width"] <= 0 or video["height"] <= 0:
            raise ValueError()
        return {"duration": duration, "audio": any(s["codec_type"] == "audio" for s in data["streams"])}
    except (ValueError, KeyError, StopIteration):
        raise HTTPException(400, "视频元数据无效")


async def store_segment(path: Path, index: int, file: UploadFile) -> dict:
    if not 0 <= index < MAX_SEGMENTS:
        raise HTTPException(400, "单次录像最多20段")
    with locked(path):
        target = path / f"{index}.mp4"
        temporary = path / f"{uuid.uuid4().hex}.part"
        try:
            size = 0
            with temporary.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_video_bytes:
                        raise HTTPException(413, "视频超过大小限制")
                    output.write(chunk)
            info = await probe(temporary)
            if target.exists():
                # An index is immutable: retries cannot change an already merged recording.
                if hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(temporary.read_bytes()).digest():
                    raise HTTPException(409, "录像片段与原上传不一致")
                return info
            if (path / "merged.mp4").exists():
                raise HTTPException(409, "录像已合并，不能添加片段")
            others = [json.loads(p.read_text()) for p in path.glob("[0-9]*.json")]
            if size + sum(item["size"] for item in others) > settings.max_video_bytes:
                raise HTTPException(413, "录像总大小超过限制")
            if info["duration"] + sum(item["duration"] for item in others) > settings.max_video_duration_seconds:
                raise HTTPException(413, "录像总时长超过限制")
            temporary.replace(target)
            info["size"] = size
            (path / f"{index}.json").write_text(json.dumps(info))
            return info
        finally:
            temporary.unlink(missing_ok=True)


async def merge_segments(path: Path, count: int) -> dict:
    if not 1 <= count <= MAX_SEGMENTS:
        raise HTTPException(400, "片段数量无效")
    with locked(path):
        if {p.stem for p in path.glob("[0-9]*.mp4")} != {str(i) for i in range(count)}:
            raise HTTPException(409, "录像片段不完整，请重新上传全部片段")
        output = path / "merged.mp4"
        if output.exists():
            return await probe(output)
        infos = [await probe(path / f"{i}.mp4") for i in range(count)]
        duration = sum(info["duration"] for info in infos)
        if duration > settings.max_video_duration_seconds:
            raise HTTPException(413, "录像总时长超过限制")
        normalized = []
        temporary = path / "merged.part.mp4"
        try:
            # Normalize dimensions, timestamps and audio so every segment is retained,
            # including clips with no microphone track or changed orientation.
            for index, info in enumerate(infos):
                clip = path / f"normalized-{index}.mp4"
                normalized.append(clip)
                args = ["-y", "-v", "error", "-i", str(path / f"{index}.mp4")]
                if not info["audio"]:
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                args += [
                    "-map", "0:v:0", "-map", "0:a:0" if info["audio"] else "1:a:0",
                    "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS",
                    "-af", "aresample=48000:async=1:first_pts=0,apad",
                    "-t", str(info["duration"]), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(clip),
                ]
                await run_tool("ffmpeg", *args)
            listing = path / "concat.txt"
            listing.write_text("\n".join(f"file '{p.name}'" for p in normalized), encoding="utf-8")
            await run_tool("ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(listing),
                           "-c", "copy", "-movflags", "+faststart", str(temporary))
            final = await probe(temporary)
            if abs(final["duration"] - duration) > max(0.5, count * 0.1):
                raise HTTPException(400, "合并视频时长不完整")
            if final["duration"] > settings.max_video_duration_seconds or temporary.stat().st_size > settings.max_video_bytes:
                raise HTTPException(413, "合并视频超过时长或大小限制")
            temporary.replace(output)
            return final
        finally:
            temporary.unlink(missing_ok=True)
            for clip in normalized:
                clip.unlink(missing_ok=True)
            (path / "concat.txt").unlink(missing_ok=True)
