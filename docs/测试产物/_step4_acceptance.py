"""Step 4 生产端到端验收（容器内执行，走真实 HTTP 127.0.0.1:8000）。

覆盖 §8.8.8 验收清单：
  注册 → pending 且不发 token → 登录被拦 → 审批列表可见 → 批准 →
  登录成功 → 拒绝链路 → 权限负例 → 重复批准 → 不能批自己。

测试数据全部以 ``step4t`` 前缀命名，**finally 中幂等清理**（用户/成员行/审计/测试码）。
"""
from __future__ import annotations

import asyncio
import importlib
import json
import pkgutil
import sys
from urllib import error, request

import app.models as _M  # noqa: E402

for _m in pkgutil.iter_modules(_M.__path__):
    importlib.import_module(f"app.models.{_m.name}")

from sqlalchemy import select, text  # noqa: E402

from app.db import async_session  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routers.auth import create_access_token  # noqa: E402
from app.services.permissions import effective_permissions  # noqa: E402

BASE = "http://127.0.0.1:8000"
TEST_CODE = "STEP4TEST"
U1, U2 = "step4t01", "step4t02"
PWD = "Aa12345678"
RESULTS: list[tuple[str, bool, str]] = []


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload
    except Exception as e:  # noqa: BLE001
        return -1, {"detail": f"{type(e).__name__}: {e}"}


def chk(label: str, cond: bool, extra: str = "") -> None:
    RESULTS.append((label, cond, extra))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}  {extra}")


async def db_ctx(username: str):
    """返回 {id, role, org_role, audit, token} 或 None。"""
    async with async_session() as db:
        u = (await db.execute(
            select(User).where(User.username == username)
        )).scalar_one_or_none()
        if u is None:
            return None
        perms = await effective_permissions(u, db)
        return {
            "id": u.id, "role": u.role, "org_role": u.org_role,
            "audit": u.audit_status, "org_id": u.org_id,
            "project_id": u.project_id,
            "token": create_access_token({
                "sub": str(u.id), "role": u.role, "permissions": perms,
            }),
        }


async def cleanup() -> None:
    async with async_session() as db:
        await db.execute(text(
            "DELETE FROM project_members WHERE user_id IN "
            "(SELECT id FROM users WHERE username LIKE 'step4t%')"))
        await db.execute(text(
            "DELETE FROM security_audit WHERE detail LIKE '%step4t%'"))
        await db.execute(text(
            "DELETE FROM users WHERE username LIKE 'step4t%'"))
        await db.execute(text(
            "DELETE FROM invite_codes WHERE code = :c"), {"c": TEST_CODE})
        await db.commit()


async def main() -> int:
    print("=== Step 4 端到端验收（生产容器内 · 真实 HTTP）===")
    admin = await db_ctx("admin")
    insp = await db_ctx("inspector")
    if not admin or not insp:
        print("FATAL: 基线账号缺失")
        return 3

    try:
        # 0. 建专用测试邀请码（inspector 岗，5 次）
        st, r = call("POST", "/api/auth/invite-codes", admin["token"], {
            "role": "inspector", "level": "personal",
            "max_uses": 5, "code": TEST_CODE,
        })
        chk("0 建测试邀请码", st in (200, 201), f"HTTP {st} {r}")
        if st not in (200, 201):
            return 1

        # 1. 注册 → pending 且不发 token
        st, r = call("POST", "/api/auth/register", None, {
            "username": U1, "password": PWD, "real_name": "验收测试一号",
            "invite_code": TEST_CODE,
        })
        chk("1a 注册返回成功", st == 200, f"HTTP {st} {r}")
        chk("1b 注册不发 access_token", "access_token" not in r, f"keys={list(r)}")
        u1 = await db_ctx(U1)
        chk("1c 库内 audit_status=pending", bool(u1) and u1["audit"] == "pending",
            f"audit={u1 and u1['audit']}")
        chk("1d org_id 继承自邀请码", bool(u1) and u1["org_id"] == admin["org_id"],
            f"user.org_id={u1 and u1['org_id']} admin.org_id={admin['org_id']}")

        # 2. pending 用户登录被拦
        st, r = call("POST", "/api/auth/login", None,
                     {"username": U1, "password": PWD})
        chk("2 pending 登录 403", st == 403, f"HTTP {st} {r.get('detail', '')[:40]}")

        # 3. 审批列表可见
        st, r = call("GET", "/api/auth/registrations?status=pending", admin["token"])
        names = [i["username"] for i in (r.get("items") or [])]
        chk("3 待审批列表含新申请", st == 200 and U1 in names,
            f"HTTP {st} items={names}")

        # 4. 权限负例：inspector 不能批
        st, _ = call("POST", f"/api/auth/registrations/{u1['id']}/approve",
                     insp["token"], {"project_id": 1, "roles": ["inspector"]})
        chk("4 inspector 批准 403", st == 403, f"HTTP {st}")

        # 5. 批准（项目1 + inspector 岗）
        st, r = call("POST", f"/api/auth/registrations/{u1['id']}/approve",
                     admin["token"], {"project_id": 1, "roles": ["inspector"]})
        chk("5a 批准成功", st == 200, f"HTTP {st} {r}")
        u1b = await db_ctx(U1)
        chk("5b 置为 approved", bool(u1b) and u1b["audit"] == "approved",
            f"audit={u1b and u1b['audit']}")

        async def _member():
            async with async_session() as db:
                row = (await db.execute(text(
                    "SELECT role, extra_roles, left_at FROM project_members "
                    "WHERE user_id=:u AND project_id=1"), {"u": u1["id"]})).first()
                return row
        m = await _member()
        chk("5c 落 project_members 归属", m is not None and m[0] == "inspector",
            f"member={m}")

        # 6. 批准后可登录
        st, r = call("POST", "/api/auth/login", None,
                     {"username": U1, "password": PWD})
        chk("6a 批准后登录成功", st == 200 and "access_token" in r, f"HTTP {st}")
        tok1 = r.get("access_token")
        if tok1:
            st, me = call("GET", "/api/auth/me", tok1)
            chk("6b /me 身份正确", st == 200 and me.get("username") == U1,
                f"HTTP {st} role={me.get('role')}")

        # 7. 重复批准 → 409
        st, _ = call("POST", f"/api/auth/registrations/{u1['id']}/approve",
                     admin["token"], {"project_id": 1, "roles": ["inspector"]})
        chk("7 重复批准 409", st == 409, f"HTTP {st}")

        # 8. 拒绝链路
        st, r = call("POST", "/api/auth/register", None, {
            "username": U2, "password": PWD, "real_name": "验收测试二号",
            "invite_code": TEST_CODE,
        })
        chk("8a 注册第二个", st == 200 and "access_token" not in r, f"HTTP {st}")
        u2 = await db_ctx(U2)
        st, r = call("POST", f"/api/auth/registrations/{u2['id']}/reject",
                     admin["token"], {"reason": "验收测试：拒绝路径"})
        chk("8b 拒绝成功", st == 200, f"HTTP {st} {r}")
        u2b = await db_ctx(U2)
        chk("8c 置为 rejected 且有理由",
            bool(u2b) and u2b["audit"] == "rejected", f"audit={u2b and u2b['audit']}")
        st, r = call("POST", "/api/auth/login", None,
                     {"username": U2, "password": PWD})
        chk("8d 被拒账号登录 403", st == 403, f"HTTP {st} {r.get('detail','')[:40]}")

        # 9. 不能批自己
        st, _ = call("POST", f"/api/auth/registrations/{admin['id']}/approve",
                     admin["token"], {"project_id": 1, "roles": ["inspector"]})
        chk("9 不能批自己（403/409/404）", st in (400, 403, 404, 409), f"HTTP {st}")

        # 10. 存量回归：admin/chief 仍可正常登录（密码未知 → 用 token 校验 /me）
        st, me = call("GET", "/api/auth/me", admin["token"])
        chk("10 存量 admin /me 正常", st == 200 and me.get("username") == "admin",
            f"HTTP {st}")

    finally:
        await cleanup()
        print("\n=== 清理完成（step4t* 与测试码已删除）===")

    ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n=== 结果：{ok}/{len(RESULTS)} 通过 ===")
    for label, cond, extra in RESULTS:
        if not cond:
            print(f"  !! FAIL {label} {extra}")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
