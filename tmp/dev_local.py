"""本地开发后端：SQLite 全隔离，不连生产库。

用法：
  serve [--port N]  启动 127.0.0.1:N（默认 8000，供微信开发者工具联调）
  verify            进程内直接打接口，验证资料库分类树
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

BACKEND = Path(r"D:\rokid\rokid\cloud-backend")
TMP = Path(r"D:\rokid\rokid\tmp")
DB_PATH = TMP / "local_dev.db"


def prepare_env(port: int = 8000) -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    for sub in ("uploads", "exports"):
        (TMP / sub).mkdir(exist_ok=True)
    secret_file = TMP / "local_secret.key"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    secret = secret_file.read_text(encoding="utf-8").strip()

    # 微信登录凭据：模拟器走真实 wx.login（拿不到 ext.json 的 mock 开关），
    # 后端必须有真实 APP_ID/SECRET 才能 jscode2session。从仓库 .env 读取注入。
    wechat = {}
    env_file = BACKEND / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(("WECHAT_APP_ID=", "WECHAT_APP_SECRET=")):
                k, _, v = line.partition("=")
                wechat[k] = v.strip()

    os.environ.update({
        "DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH.as_posix()}",
        "DEBUG": "true",
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "SECRET_KEY": secret,
        "MODEL_CONFIG_ENCRYPTION_KEY": secret,
        "DAILY_DOC_AUTO": "false",
        "SMS_PROVIDER": "mock",
        "WECHAT_MOCK_LOGIN_ENABLED": "true",
        "ALLOW_WECHAT_REGISTRATION": "true",
        "UPLOAD_DIR": str(TMP / "uploads"),
        "EXPORT_DIR": str(TMP / "exports"),
        "PUBLIC_API_BASE_URL": f"http://127.0.0.1:{port}",
        "PUBLIC_WS_BASE_URL": f"ws://127.0.0.1:{port}",
        "GDJL_ENV_FILE": str(TMP / "empty.env"),
        "PYTHONUTF8": "1",
        **wechat,
    })
    (TMP / "empty.env").write_text("", encoding="utf-8")
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)


async def bootstrap():
    from app.db import init_db, async_session
    from app.models.user import User
    from app.routers.auth import create_access_token
    from sqlalchemy import select

    await init_db()
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.username == "dev"))).scalars().first()
        if user is None:
            user = User(
                username="dev",
                password_hash="x",
                real_name="本地测试",
                role="inspector",
                is_active=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        token = create_access_token({
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
        })
        return user.id, token


async def verify() -> None:
    import httpx
    from app.main import app

    uid, token = await bootstrap()
    headers = {"Authorization": f"Bearer {token}"}
    ok = True

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok
        print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
        if not cond:
            ok = False

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://local") as c:
        r = await c.get("/api/kb/mine", headers=headers)
        check("GET /kb/mine", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        home = r.json() if r.status_code == 200 else {}
        kb_id = home.get("id")

        r = await c.get(f"/api/kb/{kb_id}/folders", headers=headers)
        check("GET /{kb}/folders", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        tree = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        names = [n.get("name") for n in tree]
        check("预设目录已播种", "个人文件夹" in names and "项目文件夹" in names, f"{names}")

        def find(nodes, name):
            for n in nodes:
                if n["name"] == name:
                    return n
                got = find(n.get("children") or [], name)
                if got:
                    return got
            return None

        work = find(tree, "工作记录与表单")
        check("子分类层级存在", work is not None and len(work.get("children") or []) >= 5,
              f"工作记录与表单下 {len(work.get('children') or []) if work else 0} 项")

        # 上传一份文档到「工作记录与表单」
        folder_id = work["id"] if work else None
        r = await c.post(
            f"/api/kb/{kb_id}/documents",
            headers=headers,
            files={"file": ("本地验证.txt", "本地验证内容".encode("utf-8"), "text/plain")},
            data={"name": "本地验证", "folder_id": str(folder_id)},
        )
        check("POST 上传并归档到分类", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:160]}")
        doc = r.json() if r.status_code in (200, 201) else {}
        check("文档带 folder_id", doc.get("folder_id") == folder_id, f"folder_id={doc.get('folder_id')}")

        r = await c.get(f"/api/kb/{kb_id}/documents", params={"folder_id": folder_id}, headers=headers)
        items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("按分类过滤出该文档", any(d.get("id") == doc.get("id") for d in items), f"{len(items)} 条")

        r = await c.get(f"/api/kb/{kb_id}/folders", headers=headers)
        work2 = find((r.json() or {}).get("items", []), "工作记录与表单")
        parent = find((r.json() or {}).get("items", []), "项目文件夹")
        check("父级份数为递归合计", bool(parent and parent.get("count", 0) >= 1),
              f"项目文件夹 count={parent.get('count') if parent else None}, 子={work2.get('count') if work2 else None}")

        # 新建分类
        r = await c.post(f"/api/kb/{kb_id}/folders", headers=headers,
                         json={"name": "本地自建分类", "parent_id": None})
        check("POST 新建分类", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}")

        # 删除文件夹：父子两层 + 一份文档，验证递归删除与文档上移
        r = await c.post(f"/api/kb/{kb_id}/folders", headers=headers,
                         json={"name": "删除验证-父", "parent_id": work["id"]})
        check("POST 新建待删父分类", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}")
        del_parent = r.json() if r.status_code in (200, 201) else {}
        r = await c.post(f"/api/kb/{kb_id}/folders", headers=headers,
                         json={"name": "删除验证-子", "parent_id": del_parent.get("id")})
        del_child = r.json() if r.status_code in (200, 201) else {}
        r = await c.post(
            f"/api/kb/{kb_id}/documents",
            headers=headers,
            files={"file": ("删除验证.txt", "删除验证内容".encode("utf-8"), "text/plain")},
            data={"name": "删除验证文档", "folder_id": str(del_child.get("id"))},
        )
        del_doc = r.json() if r.status_code in (200, 201) else {}

        r = await c.delete(f"/api/kb/{kb_id}/folders/{del_parent.get('id')}", headers=headers)
        check("DELETE 删除文件夹", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        info = r.json() if r.status_code == 200 else {}
        check("连同子文件夹共删 2 个", info.get("deleted_folders") == 2, f"{info}")
        check("文档移至上层 1 份", info.get("moved_documents") == 1, f"{info}")

        r = await c.get(f"/api/kb/{kb_id}/folders", headers=headers)
        tree2 = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("被删节点已消失", find(tree2, "删除验证-父") is None and find(tree2, "删除验证-子") is None)

        r = await c.get(f"/api/kb/{kb_id}/documents", params={"folder_id": work["id"]}, headers=headers)
        items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("文档未丢失且归属上层文件夹", any(d.get("id") == del_doc.get("id") for d in items), f"{len(items)} 条")

        r = await c.delete(f"/api/kb/{kb_id}/folders/999999", headers=headers)
        check("删除不存在文件夹返回 404", r.status_code == 404, f"HTTP {r.status_code}")

        # 重命名文件夹：名称更新、层级与文档归属不变、空名称被拒
        r = await c.post(f"/api/kb/{kb_id}/folders", headers=headers,
                         json={"name": "重命名验证", "parent_id": None})
        ren = r.json() if r.status_code in (200, 201) else {}
        r = await c.put(f"/api/kb/{kb_id}/folders/{ren.get('id')}", headers=headers,
                        json={"name": "重命名验证-改"})
        check("PUT 重命名文件夹", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        check("名称已更新", r.status_code == 200 and r.json().get("name") == "重命名验证-改", f"{r.text[:80]}")

        r = await c.get(f"/api/kb/{kb_id}/folders", headers=headers)
        node = find((r.json() or {}).get("items", []), "重命名验证-改")
        check("重命名后节点仍在根级", node is not None and node.get("parent_id") is None, f"{node}")

        r = await c.put(f"/api/kb/{kb_id}/folders/{ren.get('id')}", headers=headers, json={"name": ""})
        check("空名称被拒(422)", r.status_code == 422, f"HTTP {r.status_code}")

        # 标签：新建幂等、挂到文档、按标签筛选、清空、删除
        r = await c.post(f"/api/kb/{kb_id}/tags", headers=headers, json={"name": "标签验证"})
        tag1 = r.json() if r.status_code in (200, 201) else {}
        check("POST 新建标签", r.status_code in (200, 201) and bool(tag1.get("id")), f"HTTP {r.status_code} {r.text[:120]}")
        check("新建标签非幂等标记", r.status_code in (200, 201) and tag1.get("existed") is False, f"{r.text[:80]}")

        r = await c.post(f"/api/kb/{kb_id}/tags", headers=headers, json={"name": "标签验证"})
        check("同名标签幂等返回", r.status_code in (200, 201) and r.json().get("id") == tag1.get("id") and r.json().get("existed") is True, f"{r.text[:80]}")

        r = await c.get(f"/api/kb/{kb_id}/tags", headers=headers)
        items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("标签列表含新建标签", any(t.get("name") == "标签验证" for t in items), f"{len(items)} 个")

        r = await c.put(f"/api/kb/{kb_id}/documents/{del_doc.get('id')}/tags", headers=headers,
                        json={"tag_ids": [tag1.get("id")]})
        check("PUT 设置文档标签", r.status_code == 200 and any(t.get("id") == tag1.get("id") for t in (r.json() or {}).get("tags", [])), f"HTTP {r.status_code} {r.text[:120]}")

        r = await c.get(f"/api/kb/{kb_id}/documents", params={"tag_id": tag1.get("id")}, headers=headers)
        items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("按标签筛选命中文档", any(d.get("id") == del_doc.get("id") for d in items), f"{len(items)} 条")
        check("筛选结果带标签信息", all(isinstance(d.get("tags"), list) for d in items), f"{items[:1]}")

        r = await c.put(f"/api/kb/{kb_id}/documents/{del_doc.get('id')}/tags", headers=headers, json={"tag_ids": []})
        check("清空文档标签", r.status_code == 200 and (r.json() or {}).get("tags") == [], f"{r.text[:80]}")

        r = await c.get(f"/api/kb/{kb_id}/documents", params={"tag_id": tag1.get("id")}, headers=headers)
        items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("清空后按标签筛不中", not any(d.get("id") == del_doc.get("id") for d in items), f"{len(items)} 条")

        r = await c.put(f"/api/kb/{kb_id}/documents/{del_doc.get('id')}/tags", headers=headers,
                        json={"tag_ids": [999999]})
        check("跨库标签被拒(422)", r.status_code == 422, f"HTTP {r.status_code}")

        r = await c.delete(f"/api/kb/{kb_id}/tags/{tag1.get('id')}", headers=headers)
        check("DELETE 删除标签", r.status_code == 200, f"HTTP {r.status_code}")
        r = await c.get(f"/api/kb/{kb_id}/tags", headers=headers)
        items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("删除后标签消失", not any(t.get("name") == "标签验证" for t in items), f"{len(items)} 个")
        r = await c.delete(f"/api/kb/{kb_id}/tags/999999", headers=headers)
        check("删除不存在标签返回 404", r.status_code == 404, f"HTTP {r.status_code}")

        # ---- 回收站：软删除 / 列表排除 / 恢复 / 彻底删除 ----
        r = await c.post(
            f"/api/kb/{kb_id}/documents",
            headers=headers,
            files={"file": ("回收站验证.txt", "回收站验证内容".encode("utf-8"), "text/plain")},
            data={"name": "回收站验证", "folder_id": str(folder_id)},
        )
        check("POST 上传回收站验证文档", r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}")
        trash_doc = r.json() if r.status_code in (200, 201) else {}
        tid = trash_doc.get("id")

        r = await c.delete(f"/api/kb/{kb_id}/documents/{tid}", headers=headers)
        check("DELETE 文档进回收站(200)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        check("返回文案为移入回收站", "回收站" in (r.json() or {}).get("detail", ""), f"{r.text[:80]}")

        r = await c.get(f"/api/kb/{kb_id}/trash", headers=headers)
        trash_items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("回收站列出该文档", any(d.get("id") == tid for d in trash_items), 
"{} 条".format(len(trash_items)))

        r = await c.get(f"/api/kb/{kb_id}/documents", params={"folder_id": folder_id}, headers=headers)
        normal_items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("常规列表已排除软删文档", not any(d.get("id") == tid for d in normal_items), 
"{} 条".format(len(normal_items)))

        r = await c.post(f"/api/kb/{kb_id}/documents/{tid}/restore", headers=headers)
        check("POST 恢复文档(200)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")

        r = await c.get(f"/api/kb/{kb_id}/documents", params={"folder_id": folder_id}, headers=headers)
        restored_items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("恢复后常规列表重新出现", any(d.get("id") == tid for d in restored_items), 
"{} 条".format(len(restored_items)))

        r = await c.delete(f"/api/kb/{kb_id}/documents/{tid}", headers=headers)
        check("恢复后再次软删(200)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")

        r = await c.delete(f"/api/kb/{kb_id}/trash/{tid}", headers=headers)
        check("DELETE 彻底删除文档(200)", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")

        r = await c.get(f"/api/kb/{kb_id}/trash", headers=headers)
        trash_items2 = (r.json() or {}).get("items", []) if r.status_code == 200 else []
        check("彻底删除后回收站不再含该文档", not any(d.get("id") == tid for d in trash_items2), 
"{} 条".format(len(trash_items2)))

    print("\nRESULT:", "ALL PASS" if ok else "HAS FAIL")
    if not ok:
        raise SystemExit(1)


def serve() -> None:
    port = 8000
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        if i + 1 >= len(sys.argv):
            raise SystemExit("--port 后要跟端口号，例如：python tmp/dev_local.py serve --port 8001")
        port = int(sys.argv[i + 1])
    prepare_env(port)
    from app.db import init_db
    asyncio.run(init_db())
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if mode == "serve":
        serve()
    else:
        prepare_env()
        asyncio.run(verify())
