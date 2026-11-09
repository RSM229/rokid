"""工程影像归档端点的真实 HTTP 层回归（ASGI transport + 隔离 SQLite + 临时磁盘）。

与 ``test_image_archive_api.py``（纯函数胶水）不同，本脚本走完整的
路由 → pydantic 校验 → 依赖注入(DB/鉴权) → 响应序列化链路，并真的复制
照片、写台账 CSV、生成 PDF、同步项目资料库 KB。**不连生产库、不写生产目录**。

运行：``python tests/test_image_archive_http.py``（需 cloud-backend/.venv）

覆盖：
- GET /standard 的 8 类 / 13 项清单 / 项目口径；
- POST /preview 的归档结果、台账行、缺口、待确认、两份文书全文；
- POST /commit dry_run=true 的落盘计划；
- POST /commit dry_run=false 的四处联动与真实文件产物；
- 同日重复归档复用 Document 出 R2；
- pydantic 422、跨项目媒体 404。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(tempfile.mkdtemp(prefix="image_archive_http_"))
UPLOADS = ROOT / "uploads"
EXPORTS = ROOT / "exports"
UPLOADS.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)
(ROOT / "empty.env").write_text("", encoding="utf-8")

# settings 在 import 时读取环境变量，必须在任何 app.* 导入之前设置。
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{(ROOT / 'probe.db').as_posix()}",
    "DEBUG": "true",
    "SECRET_KEY": "local-http-probe-secret",
    "MODEL_CONFIG_ENCRYPTION_KEY": "local-http-probe-secret",
    "DAILY_DOC_AUTO": "false",
    "SMS_PROVIDER": "mock",
    "UPLOAD_DIR": str(UPLOADS),
    "EXPORT_DIR": str(EXPORTS),
    # KB 同步会把 PDF 复制进 knowledge_base_path/user_kb；不隔离会污染仓库根目录。
    "KNOWLEDGE_BASE_PATH": str(ROOT / "knowledge"),
    "PUBLIC_API_BASE_URL": "http://127.0.0.1:8001",
    "PUBLIC_WS_BASE_URL": "ws://127.0.0.1:8001",
    "GDJL_ENV_FILE": str(ROOT / "empty.env"),
    "PYTHONUTF8": "1",
})
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    if not cond:
        FAILS.append(name)


def watermark(content: str, *, minute: str, section: str, doc_no: str = "") -> dict:
    fields = {
        "project_name": "沙盛四路等四条道路工程",
        "section": section,
        "content": content,
        "taken_at": f"2026-11-03 09:{minute}:00",
        "inspector": "李四",
        "labeled_items": [
            {"label": "单位工程", "value": "沙盛四路"},
            {"label": "部位", "value": section},
        ],
    }
    if doc_no:
        fields["related_doc_no"] = doc_no
    return fields


async def prepare_data():
    from app.db import init_db, async_session
    from app.models.miniprogram import MediaRecord
    from app.models.project_member import ProjectMember  # 确保建表 + 在岗成员
    from app.models.user import Project, User
    from app.routers.auth import create_access_token

    await init_db()
    async with async_session() as db:
        project = Project(name="本地验证项目", code="LOCAL-HTTP-001")
        other = Project(name="其他项目", code="LOCAL-HTTP-002")
        db.add_all([project, other])
        await db.flush()

        user = User(username="http_probe", password_hash="x", real_name="本地验证员",
                    role="inspector", project_id=project.id, is_active=True)
        db.add(user)
        await db.flush()
        # 真实项目必须有在岗成员，ensure_project_archive_library 才允许建项目资料库。
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role="inspector"))
        await db.flush()

        cases = [
            ("沟槽开挖，深基坑支护", "10", "W27 井", ""),
            ("水泥搅拌桩成桩施工", "19", "K0+320", "PZ-20261103-01"),
            ("上下安全通道搭设", "32", "W27 井", ""),
        ]
        media_ids: list[int] = []
        for idx, (content, minute, section, doc_no) in enumerate(cases, start=1):
            rel = f"media/image/20261103/http_probe_{idx}.jpg"
            path = UPLOADS / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xd8\xff\xe0" + f"probe-{idx}".encode() * 32 + b"\xff\xd9")
            record = MediaRecord(
                project_id=project.id, uploader_id=user.id,
                media_type="image", source="web",
                original_path=f"/uploads/{rel}", processed_path=f"/uploads/{rel}",
                mime_type="image/jpeg", size_bytes=path.stat().st_size,
                file_hash=f"probe-hash-{idx}", idempotency_key=f"probe-key-{idx}",
                section=section,
                metadata_json={"watermark": watermark(content, minute=minute,
                                                       section=section, doc_no=doc_no)},
            )
            db.add(record)
            await db.flush()
            media_ids.append(record.id)

        foreign = MediaRecord(
            project_id=other.id, uploader_id=user.id,
            media_type="image", source="web",
            original_path="/uploads/media/image/20261103/foreign.jpg",
            processed_path="/uploads/media/image/20261103/foreign.jpg",
            mime_type="image/jpeg", size_bytes=10,
            file_hash="probe-hash-foreign", idempotency_key="probe-key-foreign",
            metadata_json={},
        )
        db.add(foreign)
        await db.commit()
        token = create_access_token({"sub": str(user.id), "username": user.username,
                                     "role": user.role})
        return media_ids, foreign.id, token


async def main() -> None:
    import httpx
    from app.config import settings
    from app.main import app
    from app.services import image_archive_service as io

    media_ids, foreign_id, token = await prepare_data()
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"items": [{"media_id": mid} for mid in media_ids]}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://local", timeout=60) as c:
        r = await c.get("/api/image-archive/standard", headers=headers)
        std = r.json() if r.status_code == 200 else {}
        check("GET /standard 200", r.status_code == 200, f"HTTP {r.status_code}")
        check("标准 8 类 / 13 项旁站清单",
              len(std.get("categories") or []) == 8 and len(std.get("pangzhan_list") or []) == 13)
        check("项目口径含开挖排除", any("开挖" in x for x in
              (std.get("project_rules") or {}).get("effective_excluded") or []))

        r = await c.post("/api/image-archive/preview", headers=headers, json=payload)
        prev = r.json() if r.status_code == 200 else {}
        check("POST /preview 200", r.status_code == 200, f"HTTP {r.status_code} {r.text[:160]}")
        check("归档结果 3 行 / 台账 3 行 / 13 列",
              len(prev.get("records") or []) == 3
              and len(prev.get("ledger_rows") or []) == 3
              and len(prev.get("ledger_fields") or []) == 13)
        check("沟槽开挖按 X2 危大巡视（排除口径生效）",
              any("X2" in (x.get("归类") or "") for x in prev.get("records") or []),
              str([x.get("归类") for x in prev.get("records") or []]))
        check("资料缺口 / 待确认项有内容",
              bool((prev.get("gaps") or {}).get("missing")) and bool(prev.get("confirmations")))
        check("预览含两份文书全文 Markdown",
              len(prev.get("documents") or []) == 2
              and all(len(d.get("markdown") or "") > 300 for d in prev["documents"]))
        check("preview 不写盘", not io.rules_path(Path(settings.upload_dir)).exists())

        r = await c.post("/api/image-archive/commit", headers=headers,
                         json={**payload, "dry_run": True})
        planned = r.json() if r.status_code == 200 else {}
        check("POST /commit dry_run=true 200", r.status_code == 200, f"HTTP {r.status_code}")
        check("落盘计划 3 条且未写盘",
              len(planned.get("plan") or []) == 3 and planned.get("written") is False)

        r = await c.post("/api/image-archive/commit", headers=headers,
                         json={**payload, "dry_run": False,
                               "standard_note": "HTTP 回归：确认沟槽开挖不实施旁站",
                               "pangzhan_exclusions": ["沟槽开挖", "深基坑开挖"]})
        done = r.json() if r.status_code == 200 else {}
        check("POST /commit dry_run=false 200", r.status_code == 200,
              f"HTTP {r.status_code} {r.text[:200]}")
        check("照片复制 3 张", len(done.get("copy_results") or []) == 3)
        ledger_rel = (done.get("ledger") or {}).get("public_path", "").replace("/uploads/", "")
        check("台账文件真实存在", bool(ledger_rel) and (UPLOADS / ledger_rel).is_file())
        check("两份 PDF 已生成并同步进项目资料库 KB",
              len(done.get("documents") or []) == 2
              and all(d.get("status") == "generated"
                      and d.get("kb_sync_status") == "synced"
                      and d.get("kb_document_id") for d in done["documents"]),
              str([(d.get("title"), d.get("status"), d.get("kb_sync_status"),
                    d.get("kb_sync_error"), d.get("kb_document_id"))
                   for d in done.get("documents") or []]))
        pdfs = list(Path(settings.export_dir).glob("image_archive_*.pdf"))
        check("PDF 文件真实存在", len(pdfs) == 2, str([p.name for p in pdfs]))
        rules_path = io.rules_path(Path(settings.upload_dir))
        check("项目口径 JSON 持久化", rules_path.is_file()
              and (json.loads(rules_path.read_text(encoding="utf-8")).get("revision") or 0) >= 1)
        archived = list((UPLOADS / io.ARCHIVE_ROOT).rglob("*.jpg"))
        check("标准目录内有 3 张归档照片", len(archived) == 3, str([p.name for p in archived][:3]))
        first_docs = {d["title"]: d for d in done.get("documents") or []}

        # 同日重复归档：复用 Document，出 R2，不新建编号
        r = await c.post("/api/image-archive/commit", headers=headers,
                         json={**payload, "dry_run": False,
                               "standard_note": "HTTP 回归：二次确认口径"})
        again = r.json() if r.status_code == 200 else {}
        check("重复归档 200", r.status_code == 200, f"HTTP {r.status_code}")
        second_docs = {d["title"]: d for d in again.get("documents") or []}
        check("工作记录复用同一 Document 并出 R2",
              all(second_docs.get(t, {}).get("doc_id") == first_docs.get(t, {}).get("doc_id")
                  and second_docs.get(t, {}).get("revision") == 2
                  for t in first_docs),
              str([(t, second_docs.get(t, {}).get("doc_id"), second_docs.get(t, {}).get("revision"))
                   for t in first_docs]))

        # 真实 HTTP 层的参数校验与项目隔离
        r = await c.post("/api/image-archive/preview", headers=headers, json={"items": []})
        check("空 items 被 pydantic 拦为 422", r.status_code == 422, f"HTTP {r.status_code}")
        r = await c.post("/api/image-archive/preview", headers=headers,
                         json={"items": [{"media_id": foreign_id}]})
        check("跨项目 media_id 被隔离为 404", r.status_code == 404, f"HTTP {r.status_code}")
        r = await c.get("/api/image-archive/standard")
        check("未带 token 被拒 401", r.status_code in (401, 403), f"HTTP {r.status_code}")

    print("\nRESULT:", "ALL PASS" if not FAILS else f"HAS FAILURES ({len(FAILS)}): {FAILS}")
    print("TEMP ROOT:", ROOT)
    if FAILS:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
