"""Step 3 生产接口验收（只读为主 + 一次可回滚的写入验证）。

用法：python _step3_acceptance.py
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://www.baize.gdces.cn:4432"
TMP = Path("D:/rokid/.workbuddy/tmp")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ADMIN = (TMP / "t_admin.txt").read_text(encoding="utf-8").strip()
CHIEF = (TMP / "t_chief.txt").read_text(encoding="utf-8").strip()

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, context=CTX, timeout=40) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {}), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw[:200]}
        return exc.code, payload, dict(exc.headers)


def main() -> int:
    # ---- 1. 超管读矩阵 ----
    status, matrix, headers = call("GET", "/api/permissions/matrix", ADMIN)
    check("超管可读矩阵 (200)", status == 200, f"实际 {status}")
    if status != 200:
        report()
        return 1

    roles = matrix.get("roles", [])
    perms = matrix.get("permissions", [])
    version = matrix.get("perm_version")
    mine = matrix.get("my_permissions", [])
    check("角色 9 个", len(roles) == 9, f"实际 {len(roles)}")
    check("权限目录 26 条", len(perms) == 26, f"实际 {len(perms)}")
    check("超管 my_permissions = 目录全量", len(mine) == 26, f"实际 {len(mine)}")
    check("super_admin 列标记为不可编辑",
          next((r for r in roles if r["key"] == "super_admin"), {}).get("editable") is False)
    check("版本号 > 0", isinstance(version, int) and version > 0, f"实际 {version}")
    check("命令级权限键带 org_level 标记",
          next((p for p in perms if p["key"] == "user_manage"), {}).get("org_level") is True)
    check("member_manage 不标 org_level（跨两层）",
          next((p for p in perms if p["key"] == "member_manage"), {}).get("org_level") is False)
    check("项目级角色列 editable=True",
          next((r for r in roles if r["key"] == "inspector"), {}).get("editable") is True)

    # ---- 2. 响应头带版本 ----
    hdr = {k.lower(): v for k, v in headers.items()}
    check("响应头含 X-Perm-Version", "x-perm-version" in hdr, f"头: {hdr.get('x-perm-version')}")

    # ---- 3. 非超管（总监）被拒 ----
    status403, _, _ = call("GET", "/api/permissions/matrix", CHIEF)
    check("总监读矩阵被拒 (403)", status403 == 403, f"实际 {status403}")
    status403b, _, _ = call(
        "PUT", "/api/permissions/matrix/inspector", CHIEF, {"permissions": ["dashboard"]}
    )
    check("总监写权限被拒 (403)", status403b == 403, f"实际 {status403b}")

    # ---- 4. 越权三连（约束 3/4/5）----
    st, body, _ = call("PUT", "/api/permissions/matrix/super_admin", ADMIN, {"permissions": []})
    check("禁止编辑 super_admin (400)", st == 400, f"实际 {st} {body.get('detail')}")

    st, body, _ = call(
        "PUT", "/api/permissions/matrix/inspector", ADMIN,
        {"permissions": ["dashboard", "not_a_real_permission"]},
    )
    check("目录外的权限键被拒 (400)", st == 400, f"实际 {st} {body.get('detail')}")

    st, body, _ = call(
        "PUT", "/api/permissions/matrix/inspector", ADMIN,
        {"permissions": ["dashboard", "user_manage"]},
    )
    check("项目级角色持公司级权限被拒 (400)", st == 400, f"实际 {st} {body.get('detail')}")

    # ---- 5. 正常写入（documenter 无真实用户，安全可回滚）----
    before = next(r for r in roles if r["key"] == "documenter")["permissions"]
    check("documenter 初始含 6 项", len(before) == 6, f"实际 {before}")
    target = sorted(set(before) | {"witness"})

    st, body, _ = call("PUT", "/api/permissions/matrix/documenter", ADMIN,
                       {"permissions": target, "remark": "Step3 验收（自动回滚）"})
    check("写入 documenter 成功 (200)", st == 200, f"实际 {st} {body}")
    check("返回新增项 = witness", body.get("added") == ["witness"], f"实际 {body.get('added')}")
    v2 = body.get("perm_version")
    check("版本号递增", isinstance(v2, int) and v2 > version, f"{version} → {v2}")

    # 版本头是否跟随
    _, _, h2 = call("GET", "/api/auth/roles", ADMIN)
    h2 = {k.lower(): v for k, v in h2.items()}
    check("新版本已反映到响应头", h2.get("x-perm-version") == str(v2),
          f"头 {h2.get('x-perm-version')} vs 期望 {v2}")

    # /auth/roles 是否同步（同口径）
    _, roles_pub, _ = call("GET", "/api/auth/roles", ADMIN)
    doc_entry = next((r for r in roles_pub.get("roles", []) if r["value"] == "documenter"), {})
    check("/auth/roles 与矩阵同口径（含新加的 witness）",
          "witness" in doc_entry.get("permissions", []), f"实际 {doc_entry.get('permissions')}")

    # 矩阵复读确认持久化
    _, m2, _ = call("GET", "/api/permissions/matrix", ADMIN)
    doc2 = next(r for r in m2["roles"] if r["key"] == "documenter")["permissions"]
    check("改动已持久化", "witness" in doc2, f"实际 {doc2}")

    # ---- 6. 回滚 ----
    st, body, _ = call("PUT", "/api/permissions/matrix/documenter", ADMIN,
                       {"permissions": before, "remark": "Step3 验收回滚"})
    check("回滚 documenter 成功 (200)", st == 200, f"实际 {st}")
    check("回滚后 removed = witness", body.get("removed") == ["witness"], f"实际 {body.get('removed')}")

    _, m3, _ = call("GET", "/api/permissions/matrix", ADMIN)
    doc3 = next(r for r in m3["roles"] if r["key"] == "documenter")["permissions"]
    check("回滚后与初始一致（零残留）", sorted(doc3) == sorted(before), f"实际 {doc3}")
    check("矩阵总映射数恢复 89",
          sum(len(r["permissions"]) for r in m3["roles"]) == 89,
          f"实际 {sum(len(r['permissions']) for r in m3['roles'])}")

    report()
    return 0 if all(ok for _, ok, _ in results) else 1


def report() -> None:
    print("=" * 70)
    print("Step 3 生产接口验收")
    print("=" * 70)
    for name, ok, detail in results:
        print(f"{'✅' if ok else '❌'} {name}" + (f"\n     └─ {detail}" if not ok and detail else ""))
    passed = sum(1 for _, ok, _ in results if ok)
    print("-" * 70)
    print(f"通过 {passed}/{len(results)}")


if __name__ == "__main__":
    raise SystemExit(main())
