"""工程影像归档 IO（标准第七章：目录结构、命名、影像台账）。

职责边界：只做「复制文件 + 写台账 CSV」，不判类、不碰数据库、不删原图。
归类逻辑在 image_classification.py，落盘编排在 routers/image_archive.py。

安全约定（重要）：
- **复制而非移动**：原始上传图留在 /uploads/media/... 不动，MediaRecord 引用不失效；
- **绝不覆盖**：目标已存在且内容不同则自动加 _v2/_v3 后缀，内容相同则复用；
- **路径防穿越**：/uploads 公共路径解析后必须仍在 upload_dir 内；
- **台账可回退**：覆盖前备份 .bak_时间戳，写入走临时文件 + os.replace 原子替换；
- 文件名/目录名来自 OCR 文本，统一清洗控制字符与路径分隔符。
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


ARCHIVE_ROOT = "现场影像"

# 标准第七章 05_巡视 下的二级目录
X_SUBDIR_NAMES = {
    "X1": "X1_日常质量",
    "X2": "X2_安全专项",
    "X3": "X3_文明施工",
    "X4": "X4_整改复查",
    "X5": "X5_进度形象",
}

_UNSAFE_CHARS = re.compile(r"[\x00-\x1f/\\:*?\"<>|]")
_UPLOADS_PREFIX = "/uploads/"


def sanitize_component(text: str, max_len: int = 80) -> str:
    """清洗来自 OCR 的目录/文件名片段，防路径穿越与 Windows 非法字符。"""
    value = _UNSAFE_CHARS.sub("_", str(text or "")).strip()
    value = value.replace("..", "_").lstrip(".")  # 防隐藏文件 / 残留 ..
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return "未命名"
    return value[:max_len]


def sanitize_filename(filename: str) -> str:
    """保留扩展名，清洗主名；兜底给 .jpg。"""
    raw = str(filename or "")
    suffix = Path(raw).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    stem = sanitize_component(raw[: -len(Path(raw).suffix)] if Path(raw).suffix else raw, 160)
    return f"{stem}{suffix}"


def archive_root(upload_dir: Path) -> Path:
    root = Path(upload_dir) / ARCHIVE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def public_path(upload_dir: Path, path: Path) -> str:
    """把归档目录内的绝对路径转成 /uploads/... 公共路径。"""
    relative = Path(path).resolve().relative_to(Path(upload_dir).resolve())
    return f"/uploads/{relative.as_posix()}"


def resolve_source(upload_dir: Path, source: str) -> Path:
    """安全解析 /uploads/xxx 公共路径；越界或不存在直接抛 ValueError。"""
    text = str(source or "").strip()
    if not text.startswith(_UPLOADS_PREFIX):
        raise ValueError("源文件必须是 /uploads/ 公共路径")
    relative = text[len(_UPLOADS_PREFIX):]
    candidate = (Path(upload_dir) / relative).resolve()
    root = Path(upload_dir).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("源文件路径越界")
    if not candidate.is_file():
        raise ValueError("源文件不存在")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _non_conflicting(dest: Path, source_hash: str) -> tuple[Path, str]:
    """目标已存在时：内容相同复用，否则加 _vN 后缀，绝不覆盖。"""
    if not dest.exists():
        return dest, "copied"
    if _sha256(dest) == source_hash:
        return dest, "reused"
    stem, suffix = dest.stem, dest.suffix
    for number in range(2, 100):
        candidate = dest.with_name(f"{stem}_v{number}{suffix}")
        if not candidate.exists():
            return candidate, "copied"
        if _sha256(candidate) == source_hash:
            return candidate, "reused"
    raise RuntimeError("同名归档文件过多，拒绝继续写入")


def archive_one(upload_dir: Path, source: str, ledger_row: dict,
                archive_dir: str, actual_filename: Optional[str] = None) -> dict:
    """把一张已上传照片复制进标准目录并重命名。

    ``ledger_row`` 为标准台账行（需含 类别码/子类/拍摄日期/存储路径）。
    返回 status: copied / reused / missing（源缺失时只报告，不中断整批）。
    """
    upload_dir = Path(upload_dir)
    try:
        src = resolve_source(upload_dir, source)
    except ValueError as exc:
        return {"status": "missing", "reason": str(exc), "source": source}

    month = sanitize_component(str(ledger_row.get("拍摄日期") or "")[:7] or "未知日期", 16)
    category = str(ledger_row.get("类别码") or "")
    subcategory = str(ledger_row.get("子类") or "")
    parts = [archive_root(upload_dir), sanitize_component(archive_dir or "08_其他", 32), month]
    if category == "X" and subcategory in X_SUBDIR_NAMES:
        parts.append(X_SUBDIR_NAMES[subcategory])
    dest_dir = Path(*parts)
    dest_dir.mkdir(parents=True, exist_ok=True)

    filename = sanitize_filename(actual_filename or str(ledger_row.get("存储路径") or ""))
    source_hash = _sha256(src)
    dest, status = _non_conflicting(dest_dir / filename, source_hash)
    if status == "copied":
        temporary = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(src, temporary)
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "status": status,
        "source": source,
        "dest": str(dest),
        "public_path": public_path(upload_dir, dest),
        "bytes": dest.stat().st_size,
        "sha256": source_hash,
    }


def _csv_safe(value) -> str:
    """防 Excel 公式注入：以 = + - @ 开头的单元格前置单引号。"""
    text = "" if value is None else str(value)
    if text and text[0] in "=+-@":
        return "'" + text
    return text


def write_ledger_csv(upload_dir: Path, date: str, rows: list[dict],
                     fields: list[str], *, backup: bool = True) -> dict:
    """写「影像台账_YYYYMMDD.csv」，与已有台账按「存储路径」合并去重。

    改口径后重新归档同一张照片时，新行覆盖旧行（联动更新），其余历史行保留；
    覆盖前留 .bak_时间戳 备份，写入走临时文件原子替换。UTF-8 BOM 便于 Excel。
    """
    root = archive_root(upload_dir)
    safe_date = sanitize_component(str(date or "00000000"), 16)
    path = root / f"影像台账_{safe_date}.csv"
    created = not path.exists()
    backup_path: Optional[str] = None

    merged: dict[str, dict] = {}
    if not created:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for old in csv.DictReader(handle):
                    key = str(old.get("存储路径") or "")
                    if key:
                        merged[key] = {field: old.get(field, "") for field in fields}
        except (OSError, csv.Error):
            merged = {}  # 旧台账损坏时不阻断，下面会先备份再重建
        if backup:
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup_file = root / f"影像台账_{safe_date}.csv.bak_{stamp}"
            shutil.copy2(path, backup_file)
            backup_path = str(backup_file)

    for row in rows:
        key = str(row.get("存储路径") or "")
        if not key:
            continue
        merged[key] = {field: row.get(field, "") for field in fields}

    ordered = sorted(merged.values(), key=lambda r: (
        str(r.get("拍摄日期") or ""), str(r.get("时间") or ""), str(r.get("存储路径") or "")
    ))
    temporary = root / f".影像台账_{safe_date}.csv.{os.getpid()}.tmp"
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in ordered:
                writer.writerow({field: _csv_safe(row.get(field, "")) for field in fields})
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "path": str(path),
        "public_path": public_path(upload_dir, path),
        "rows": len(ordered),
        "created": created,
        "backup": backup_path,
    }
